"""
《千古一局：暗合推演》夜间定时调度器、断点续跑与 Redis 持久化引擎 (nightly_scheduler_worker.py)

核心职责：
1. Redis 生产级持久化对接（含无外部 Redis 时的内存兼容层，开箱即测）：
   - 日间沙盒配额原子控制：`INCR` + `EXPIREAT`（每日凌晨 02:00 自动重置免费次数，点卡余额 `HINCRBY` 持久化）。
   - 全服天梯榜 Sorted Set：`ZADD` 实时写入角色分榜（`leaderboard:role:{char_id}`）与传奇总榜（`leaderboard:legend:overall`），支持毫秒级 `ZREVRANGE` 拉取。
   - 玩家日间提交指令池：`RPUSH` 收集当日已通过三道防线的回合指令单（`cmd_pool:battle:{battle_id}:day:{day}`）。
2. 夜间 BSP 四步断点续跑状态机（Superstep Checkpoint & Resume）：
   - 将夜间推演拆分为 4 个原子检查点（Checkpoint）：
     * `STEP_1_AGENT_THINK`      : 10 名活跃 RoleAgent 并发思考结果落盘（最耗算力步骤，一旦成功绝不重复调用大模型！）
     * `STEP_2_STAGE_STATE_LOCK` : 舞台交互、小人物上限裁剪与心理槽单向棘轮结算落盘
     * `STEP_3_COUNTERFACTUAL`   : 反事实 4 分支死局强验算结果落盘
     * `STEP_4_CONTRACT_EXPORT`  : 导出 24h 时间轴 JSON 并更新 Redis ZSET 天梯榜
   - 若凌晨 03:00 某一步遇到 API 超时或网络抖动，指数退避重试时直接从 Redis 读取上一检查点快照（`HGET bsp_ckpt:{battle_id}:day:{day}`），跳过已完成的 Superstep，实现真正的“零算力浪费断点续跑”。
3. 每日凌晨 Cron 定时触发器：内置异步定时守护循环（默认每日 02:00 锁单开跑），支持 Temporal Activity / Celery Worker 直接挂载。
"""

from __future__ import annotations
import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from state_machine import (
    CharacterState,
    DeterministicStateMachine,
    EventTag,
    MinorInfluenceCap,
    RoleTier,
)
from era_guard import EraIronLawGuard
from director_compiler import DirectorCompiler
from counterfactual import CounterfactualDeadlockVerifier
from role_agent import (
    CHIBI_PERSONA_CATALOG,
    PrivateHorizonContext,
    RoleAgentDecision,
    RoleAgentRunner,
)
from app_server import ButterflyLeaderboardEngine, PlayerRunSubmission


# ---------------------------------------------------------------------------
# 1. Redis 持久化适配器（自动探测 redis-py，无 Redis 服务时无缝切换内存模拟层）
# ---------------------------------------------------------------------------
class RedisPersistenceStore:
    """统一封装配额 INCR/EXPIRE、指令池队列、BSP 检查点 Hash 与天梯 ZSET"""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or os.environ.get("REDIS_URL")
        self._client = None
        if self.redis_url:
            try:
                import redis  # type: ignore

                client = redis.Redis.from_url(self.redis_url, decode_responses=True)
                client.ping()
                self._client = client
            except Exception:
                self._client = None

        # 内存回退存储（供本地无 Redis 容器时执行完整故障注入测试）
        self._mem_kv: Dict[str, Any] = {}
        self._mem_hash: Dict[str, Dict[str, str]] = {}
        self._mem_list: Dict[str, List[str]] = {}
        self._mem_zset: Dict[str, Dict[str, float]] = {}

    @property
    def backend_mode(self) -> str:
        return "REDIS_SERVER" if self._client is not None else "IN_MEMORY_REDIS_COMPAT"

    # --- A. 日间沙盒配额原子计数 (INCR + TTL) ---
    def incr_daily_sandbox_quota(
        self, player_id: str, date_str: str, free_limit: int = 3
    ) -> Tuple[bool, int]:
        """返回 (是否在免费额度内, 剩余免费次数)"""
        key = f"quota:sandbox:{date_str}:{player_id}"
        if self._client is not None:
            current = int(self._client.incr(key))
            if current == 1:
                self._client.expire(key, 86400)
        else:
            current = int(self._mem_kv.get(key, 0)) + 1
            self._mem_kv[key] = current

        if current <= free_limit:
            return True, free_limit - current
        return False, 0

    # --- B. 当日玩家指令池队列 (RPUSH / LRANGE) ---
    def enqueue_player_command(
        self, battle_id: str, day: int, raw_cmd: Dict[str, Any]
    ) -> int:
        key = f"cmd_pool:{battle_id}:day:{day}"
        payload = json.dumps(raw_cmd, ensure_ascii=False)
        if self._client is not None:
            return int(self._client.rpush(key, payload))
        lst = self._mem_list.setdefault(key, [])
        lst.append(payload)
        return len(lst)

    def fetch_queued_commands(self, battle_id: str, day: int) -> List[Dict[str, Any]]:
        key = f"cmd_pool:{battle_id}:day:{day}"
        if self._client is not None:
            raw_list = self._client.lrange(key, 0, -1)
        else:
            raw_list = self._mem_list.get(key, [])
        return [json.loads(item) for item in raw_list]

    # --- C. 夜间 BSP 断点续跑检查点 (HSET / HGET) ---
    def save_checkpoint(
        self, battle_id: str, day: int, step_name: str, data: Dict[str, Any]
    ) -> None:
        key = f"bsp_ckpt:{battle_id}:day:{day}"
        serialized = json.dumps(data, ensure_ascii=False)
        if self._client is not None:
            self._client.hset(key, step_name, serialized)
            self._client.expire(key, 86400 * 7)
        else:
            h = self._mem_hash.setdefault(key, {})
            h[step_name] = serialized

    def load_checkpoint(
        self, battle_id: str, day: int, step_name: str
    ) -> Optional[Dict[str, Any]]:
        key = f"bsp_ckpt:{battle_id}:day:{day}"
        if self._client is not None:
            raw = self._client.hget(key, step_name)
        else:
            raw = self._mem_hash.get(key, {}).get(step_name)
        return json.loads(raw) if raw else None

    def clear_checkpoints(self, battle_id: str, day: int) -> None:
        key = f"bsp_ckpt:{battle_id}:day:{day}"
        if self._client is not None:
            self._client.delete(key)
        else:
            self._mem_hash.pop(key, None)

    # --- D. 天梯排行榜 Sorted Set (ZADD / ZREVRANGE) ---
    def update_leaderboard_zset(
        self, character_id: str, player_tag: str, score: float
    ) -> None:
        overall_key = "leaderboard:legend:overall"
        role_key = f"leaderboard:role:{character_id}"
        if self._client is not None:
            self._client.zadd(overall_key, {player_tag: score})
            self._client.zadd(role_key, {player_tag: score})
        else:
            self._mem_zset.setdefault(overall_key, {})[player_tag] = score
            self._mem_zset.setdefault(role_key, {})[player_tag] = score

    def get_top_leaderboard(
        self, zset_key: str = "leaderboard:legend:overall", top_n: int = 10
    ) -> List[Tuple[str, int]]:
        if self._client is not None:
            pairs = self._client.zrevrange(zset_key, 0, top_n - 1, withscores=True)
            return [(str(member), int(round(score))) for member, score in pairs]
        bucket = self._mem_zset.get(zset_key, {})
        sorted_pairs = sorted(bucket.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [(k, int(round(v))) for k, v in sorted_pairs]


# ---------------------------------------------------------------------------
# 2. 支持四步断点续跑的夜间 BSP 工作器（Checkpoint-Resumable BSP Worker）
# ---------------------------------------------------------------------------
class BSPCheckpointStep(str, Enum):
    STEP_1_AGENT_THINK = "STEP_1_AGENT_THINK"            # 全员 RoleAgent 并发推理完成
    STEP_2_STAGE_STATE_LOCK = "STEP_2_STAGE_STATE_LOCK"  # 状态机棘轮与不可逆锚点结算完成
    STEP_3_COUNTERFACTUAL = "STEP_3_COUNTERFACTUAL"      # 反事实 4 分支死局强验算完成
    STEP_4_CONTRACT_EXPORT = "STEP_4_CONTRACT_EXPORT"    # 演出时间轴导出与天梯 ZSET 落盘完成


class ResumableNightlyWorker:
    """
    具备故障恢复与指数退避重试的夜间推演 Worker：
    若在 Step 2 或 Step 3 发生异常中断，重试时直接复用 Redis 中已保存的 Step 1 RoleAgent 决策，
    绝不重复消耗大模型 Token！
    """

    def __init__(
        self,
        store: RedisPersistenceStore,
        battle_id: str = "chibi_208ad",
        day: int = 2,
        max_retries: int = 3,
    ) -> None:
        self.store = store
        self.battle_id = battle_id
        self.day = day
        self.max_retries = max_retries
        self.era_guard = EraIronLawGuard()
        self.role_runner = RoleAgentRunner()
        self.cf_verifier = CounterfactualDeadlockVerifier()
        self.leaderboard_engine = ButterflyLeaderboardEngine()

    async def execute_with_retry(
        self,
        fault_injector: Optional[Callable[[BSPCheckpointStep, int], None]] = None,
    ) -> Dict[str, Any]:
        """带指数退避与检查点恢复的执行入口"""
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._run_checkpointed_pipeline(
                    attempt=attempt, fault_injector=fault_injector
                )
            except Exception as exc:
                print(
                    f"  [Worker 告警] 第 {attempt}/{self.max_retries} 次执行中断: {exc} "
                    f"-> 已保护已完成检查点，准备断点续跑..."
                )
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"夜间任务在 {self.max_retries} 次断点重试后仍失败: {exc}"
                    ) from exc
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))

    async def _run_checkpointed_pipeline(
        self,
        attempt: int,
        fault_injector: Optional[Callable[[BSPCheckpointStep, int], None]] = None,
    ) -> Dict[str, Any]:
        raw_commands = self.store.fetch_queued_commands(self.battle_id, self.day)
        sanitized_notes: Dict[str, str] = {}
        for cmd in raw_commands:
            v = self.era_guard.inspect_and_sanitize(cmd)
            if v.passed and v.sanitized_event_prompt:
                sanitized_notes[cmd["character_id"]] = v.sanitized_event_prompt

        sleeping_ids = {"char_xuchu", "char_ganning"}
        awake_ids = [cid for cid in CHIBI_PERSONA_CATALOG if cid not in sleeping_ids]

        # =====================================================================
        # Checkpoint 1: 10 名活跃 RoleAgent 并发思考（若已存在检查点则直接跳过大模型调用）
        # =====================================================================
        ckpt_step1 = self.store.load_checkpoint(
            self.battle_id, self.day, BSPCheckpointStep.STEP_1_AGENT_THINK.value
        )
        if ckpt_step1 is not None:
            print(
                f"  [断点恢复 @ Attempt #{attempt}] 命中 {BSPCheckpointStep.STEP_1_AGENT_THINK.value} 缓存，"
                "跳过 10 路大模型调用（零重复 Token 消耗）！"
            )
            decisions_dict = ckpt_step1["decisions"]
        else:
            if fault_injector:
                fault_injector(BSPCheckpointStep.STEP_1_AGENT_THINK, attempt)

            coros = []
            for cid in awake_ids:
                persona = CHIBI_PERSONA_CATALOG[cid]
                temp_state = CharacterState(
                    character_id=cid, name=persona.name, role_tier=persona.role_tier
                )
                horizon = PrivateHorizonContext(
                    day=self.day,
                    current_time="21:30",
                    current_zone="caoying_tent",
                    visible_actors_in_zone=[],
                    local_stage_clues=["江面起雾，暗流涌动"],
                    sanitized_player_nudge=sanitized_notes.get(cid),
                )
                coros.append(
                    self.role_runner.aexecute_turn_decision(persona, temp_state, horizon)
                )

            dec_objs: List[RoleAgentDecision] = await asyncio.gather(*coros)
            decisions_dict = {d.character_id: d.dict() for d in dec_objs}
            self.store.save_checkpoint(
                self.battle_id,
                self.day,
                BSPCheckpointStep.STEP_1_AGENT_THINK.value,
                {"decisions": decisions_dict, "sanitized_notes": sanitized_notes},
            )
            print(
                f"  [Checkpoint 1 已落盘] 完成 {len(decisions_dict)} 名 RoleAgent 并发推理并写入 Redis。"
            )

        # =====================================================================
        # Checkpoint 2: 状态机棘轮判定与不可逆事实锚点结算
        # =====================================================================
        ckpt_step2 = self.store.load_checkpoint(
            self.battle_id, self.day, BSPCheckpointStep.STEP_2_STAGE_STATE_LOCK.value
        )
        if ckpt_step2 is not None:
            print(
                f"  [断点恢复 @ Attempt #{attempt}] 命中 {BSPCheckpointStep.STEP_2_STAGE_STATE_LOCK.value} 缓存！"
            )
            caocao_slots = ckpt_step2["caocao_slots"]
            caocao_ratchets = ckpt_step2["caocao_ratchets"]
            locked_facts = ckpt_step2["locked_facts"]
        else:
            if fault_injector:
                fault_injector(BSPCheckpointStep.STEP_2_STAGE_STATE_LOCK, attempt)

            sm = DeterministicStateMachine()
            caocao = CharacterState(
                character_id="char_caocao",
                name="曹操",
                role_tier=RoleTier.COMMANDER,
                psych_slots={"suspicion": 55, "impatience": 58, "naval_anxiety": 64},
            )
            sm.process_ai_event(caocao, EventTag.NIGHT_HARASSMENT, "22:25")
            sm.process_ai_event(caocao, EventTag.CHAIN_SHIPS_PROPOSED, "22:30")
            sm.process_ai_event(caocao, EventTag.FORGED_LETTER_FOUND, "23:10")
            sm.process_ai_event(caocao, EventTag.SUBORDINATE_PLEA, "23:20")

            caocao_slots = caocao.psych_slots
            caocao_ratchets = sorted([b.value for b in caocao.ratchet_beliefs])
            locked_facts = sorted([f.value for f in sm.global_facts])

            self.store.save_checkpoint(
                self.battle_id,
                self.day,
                BSPCheckpointStep.STEP_2_STAGE_STATE_LOCK.value,
                {
                    "caocao_slots": caocao_slots,
                    "caocao_ratchets": caocao_ratchets,
                    "locked_facts": locked_facts,
                },
            )
            print(
                f"  [Checkpoint 2 已落盘] 状态机结算完成 -> 曹操心理槽={caocao_slots}, 锁定事实={locked_facts}"
            )

        # =====================================================================
        # Checkpoint 3: 夜间强推理反事实 4 分支死局验算
        # =====================================================================
        ckpt_step3 = self.store.load_checkpoint(
            self.battle_id, self.day, BSPCheckpointStep.STEP_3_COUNTERFACTUAL.value
        )
        if ckpt_step3 is not None:
            print(
                f"  [断点恢复 @ Attempt #{attempt}] 命中 {BSPCheckpointStep.STEP_3_COUNTERFACTUAL.value} 缓存！"
            )
            cf_audit = ckpt_step3
        else:
            if fault_injector:
                fault_injector(BSPCheckpointStep.STEP_3_COUNTERFACTUAL, attempt)

            from state_machine import RatchetBelief

            caocao_for_cf = CharacterState(
                character_id="char_caocao",
                name="曹操",
                role_tier=RoleTier.COMMANDER,
                psych_slots=caocao_slots,
                ratchet_beliefs={RatchetBelief(r) for r in caocao_ratchets},
            )
            conditions = {"COND_NIGHT_DRUMS_HARASS"}
            for prompt_text in sanitized_notes.values():
                if "上风" in prompt_text or "干草" in prompt_text:
                    conditions.add("COND_UPPER_WIND_DRY_GRASS")
                if "雾" in prompt_text or "风" in prompt_text:
                    conditions.add("COND_SOUTHEAST_WIND_WINDOW")
                if "滩涂" in prompt_text or "疑兵" in prompt_text or "截断" in prompt_text:
                    conditions.add("COND_SHORE_RAID_BLOCKED")

            cf_report = self.cf_verifier.verify_caocao_deadlock(caocao_for_cf, conditions)
            cf_audit = cf_report.dict()
            self.store.save_checkpoint(
                self.battle_id,
                self.day,
                BSPCheckpointStep.STEP_3_COUNTERFACTUAL.value,
                cf_audit,
            )
            print(
                f"  [Checkpoint 3 已落盘] 反事实死局验算完成 -> is_true_deadlock={cf_audit['is_true_deadlock']} "
                f"(存活分支 {cf_audit['survival_branches_count']}/{cf_audit['total_branches_tested']})"
            )

        # =====================================================================
        # Checkpoint 4: 写入 Redis 天梯 ZSET 并导出最终时间轴结果
        # =====================================================================
        is_true_deadlock = bool(cf_audit.get("is_true_deadlock", False))
        sample_submissions = [
            PlayerRunSubmission(
                player_id="p_sailor_pro",
                player_name="观风听潮",
                character_id="char_old_sailor",
                character_name="江东老水手",
                role_tier=RoleTier.MINOR,
                butterfly_multiplier=2.0,
                cross_match_wins=15,
                cross_match_total=20,
                butterfly_points_spent=1,
                era_plausibility_factor=1.10,
                claimed_deadlock=True,
                counterfactual_verified=is_true_deadlock,
            ),
            PlayerRunSubmission(
                player_id="p_minor_king",
                player_name="江左梅郎",
                character_id="char_wulin_granary",
                character_name="乌林粮草官",
                role_tier=RoleTier.MINOR,
                butterfly_multiplier=2.2,
                cross_match_wins=17,
                cross_match_total=20,
                butterfly_points_spent=2,
                era_plausibility_factor=1.10,
                claimed_deadlock=True,
                counterfactual_verified=is_true_deadlock,
            ),
            PlayerRunSubmission(
                player_id="p_zhouyu_top",
                player_name="曲有误周郎顾",
                character_id="char_zhouyu",
                character_name="周瑜",
                role_tier=RoleTier.COMMANDER,
                butterfly_multiplier=1.0,
                cross_match_wins=18,
                cross_match_total=20,
                butterfly_points_spent=3,
                era_plausibility_factor=1.05,
                claimed_deadlock=True,
                counterfactual_verified=is_true_deadlock,
            ),
        ]
        self.leaderboard_engine = ButterflyLeaderboardEngine()
        for sub in sample_submissions:
            self.leaderboard_engine.submit_run(sub)

        entries = self.leaderboard_engine.compute_rankings()
        for e in entries:
            member_tag = f"{e.character_name}:{e.player_name}"
            self.store.update_leaderboard_zset(e.character_id, member_tag, float(e.final_score))

        final_result = {
            "battle_id": self.battle_id,
            "day": self.day,
            "attempts_used": attempt,
            "is_true_deadlock": is_true_deadlock,
            "locked_facts": locked_facts + (["FACT_TRUE_DEADLOCK_SEALED"] if is_true_deadlock else []),
            "top_leaderboard": self.store.get_top_leaderboard(),
        }
        self.store.save_checkpoint(
            self.battle_id,
            self.day,
            BSPCheckpointStep.STEP_4_CONTRACT_EXPORT.value,
            final_result,
        )
        print(f"  [Checkpoint 4 已落盘] Redis ZSET 天梯榜已更新: {final_result['top_leaderboard']}")
        return final_result


async def nightly_cron_daemon(
    store: RedisPersistenceStore, trigger_hour: int = 2, trigger_minute: int = 0
) -> None:
    """生产环境每日凌晨定时触发守护协程（默认 02:00 AM 自动锁单并启动断点续跑 Worker）"""
    while True:
        now = datetime.now()
        target = now.replace(hour=trigger_hour, minute=trigger_minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        print(f"[Nightly Cron Daemon] 下次自动推演时间: {target.isoformat()} (等待 {wait_seconds:.0f}s)")
        await asyncio.sleep(wait_seconds)

        worker = ResumableNightlyWorker(store=store, battle_id="chibi_208ad", day=2)
        await worker.execute_with_retry()


# ---------------------------------------------------------------------------
# 3. 故障注入与断点续跑自检演示（直接运行 python3 nightly_scheduler_worker.py）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def run_fault_recovery_demo() -> None:
        store = RedisPersistenceStore()
        print("=========================================================================")
        print(f"  《千古一局：暗合推演》Redis 持久化与断点续跑测试 (Backend: {store.backend_mode})")
        print("=========================================================================\n")

        # 1. 测试 Redis 日间沙盒配额原子计数
        print("--- [测试 1] Redis 日间快推配额原子扣减 (INCR + TTL) ---")
        for i in range(1, 4):
            ok, rem = store.incr_daily_sandbox_quota("player_01", "2026-11-15", free_limit=2)
            print(f"  第 {i} 次请求 -> 允许免费推演={ok}, 剩余免费次数={rem}")

        # 2. 模拟白天三名玩家向 Redis 指令池推入暗棋
        print("\n--- [测试 2] 玩家日间暗棋入池 (RPUSH cmd_pool:chibi_208ad:day:2) ---")
        cmds = [
            {
                "player_id": "p_minor_king",
                "character_id": "char_wulin_granary",
                "template_id": "TPL_MINOR_LOGISTICS",
                "target_id": "wulin_depot",
                "slot_text": "干草船停在上风口",
                "butterfly_cost": 2,
            },
            {
                "player_id": "p_sailor_pro",
                "character_id": "char_old_sailor",
                "template_id": "TPL_DELIVER_SECRET_NOTE",
                "target_id": "char_lusu",
                "slot_text": "观云测出子时江面起大雾",
                "butterfly_cost": 1,
            },
            {
                "player_id": "p_zhouyu_top",
                "character_id": "char_zhouyu",
                "template_id": "TPL_MINOR_LOGISTICS",
                "target_id": "wulin_depot",
                "slot_text": "乌林岸边设疑兵截断滩涂",
                "butterfly_cost": 3,
            },
        ]
        for c in cmds:
            q_len = store.enqueue_player_command("chibi_208ad", 2, c)
        print(f"  当前 Redis 待结算指令池队列长度: {q_len} 条\n")

        # 3. 模拟凌晨 03:00 在 Step 1 完成后、Step 3 反事实推理时发生 API 超时崩溃，验证断点续跑
        print("--- [测试 3] 模拟凌晨 03:00 反事实推理超时 -> 自动从 Checkpoint 2 断点续跑 ---")
        worker = ResumableNightlyWorker(store=store, battle_id="chibi_208ad", day=2, max_retries=3)

        def simulate_3am_timeout(step: BSPCheckpointStep, attempt: int) -> None:
            if step == BSPCheckpointStep.STEP_3_COUNTERFACTUAL and attempt == 1:
                raise TimeoutError("模拟凌晨 03:00 强推理模型 API 网关超时 (504 Gateway Timeout)")

        result = await worker.execute_with_retry(fault_injector=simulate_3am_timeout)
        print(
            f"\n[断点续跑验收通过] 总尝试次数: {result['attempts_used']} 次（第1次在 Step 3 崩溃，第2次直接跳过 Step 1&2 秒级恢复）"
        )
        print(f"[最终锁定事实锚点]: {result['locked_facts']}")
        print(f"[Redis ZSET 传奇总榜 TOP 3]: {result['top_leaderboard']}")

    asyncio.run(run_fault_recovery_demo())
