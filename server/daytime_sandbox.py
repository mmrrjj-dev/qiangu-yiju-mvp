"""
《千古一局：暗合推演》阶段二 T2.3：日间局部快推沙盒与配额控制 (daytime_sandbox.py - v1.3 正式接入 role_agent.py)

核心职责：
1. 局部唤醒降本提速：日间沙盒不跑全图 25+ 角色，仅针对玩家选定的 1 个目标舞台（Zone）及关联的 2-3 名核心角色调用 `RoleAgentRunner.aexecute_turn_decision` 并发推演（端到端耗时 < 4s）。
2. Context Caching 复用：日间沙盒与夜间 BSP 共享同一套 `CHIBI_PERSONA_CATALOG` 静态系统前缀，100% 命中前缀缓存，并返回每个唤醒角色的 `cache_prefix_hash`。
3. “假想敌反应”分支对比（What-If Diff）：支持玩家预设敌方今日可能采取的应对假设，将 `RoleAgentDecision` 产出的 `EventTag` 送入深拷贝隔离状态机，直观对比心理状态槽变化与是否触发不可逆死局锚点。
4. 每日配额与防刷控制（Quota Limiter）：违规输入前置秒拒不扣配额；免费次数耗尽后支持推演点卡抵扣。
"""

from __future__ import annotations
import asyncio
import copy
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from state_machine import (
    CharacterState,
    DeterministicStateMachine,
    EventTag,
    MinorInfluenceCap,
    RoleTier,
)
from era_guard import EraIronLawGuard
from role_agent import (
    CHIBI_PERSONA_CATALOG,
    PrivateHorizonContext,
    RoleAgentDecision,
    RoleAgentRunner,
)


class EnemyHypothesis(str, Enum):
    """玩家在日间沙盒中预设的敌方应对假设"""
    CAOCAO_NORMAL_VOLLEY = "HYP_CAOCAO_NORMAL_VOLLEY"    # 假设曹营：谨守水寨、常规万箭齐发
    CAOCAO_FIRE_ARROWS = "HYP_CAOCAO_FIRE_ARROWS"        # 假设曹营：识破草船、换装火箭齐射
    CAOCAO_TRUST_CHENGYU = "HYP_CAOCAO_TRUST_CHENGYU"    # 假设曹营：听信程昱、彻查反间密信真伪


class SandboxPreviewRequest(BaseModel):
    """日间局部快推请求体（对应 POST /api/sandbox/preview）"""
    player_id: str
    zone_id: str = Field(..., description="仅唤醒的目标舞台，如 'caoying_tent' 或 'yangtze_river'")
    focus_actors: List[str] = Field(..., description="局部试算聚焦的 2-3 名角色 ID（上限 3 人）")
    player_command: Dict[str, Any] = Field(..., description="待测试的玩家结构化微调指令")
    enemy_hypothesis: EnemyHypothesis = Field(..., description="玩家预设的敌方应对分支")
    use_paid_ticket: bool = Field(default=False, description="免费次数耗尽时是否消耗点卡")


class BranchComparisonResult(BaseModel):
    """单次日间沙盒试算返回给前端的对比快照"""
    passed_era_guard: bool
    guard_message: str
    quota_remaining: int
    used_paid_ticket: bool
    elapsed_ms: float
    awoken_actors: List[str]
    cache_prefix_hashes: Dict[str, str] = Field(default_factory=dict)
    sanitized_prompt: Optional[str] = None
    psych_slots_before: Dict[str, int] = Field(default_factory=dict)
    psych_slots_after: Dict[str, int] = Field(default_factory=dict)
    newly_locked_facts: List[str] = Field(default_factory=list)
    simulated_dialogue: List[Dict[str, str]] = Field(default_factory=list)
    tactical_verdict: str = ""


class DailyQuotaLimiter:
    """每日沙盒快推配额控制器（生产环境对接 Redis INCR + EXPIRE）"""

    def __init__(self, free_daily_limit: int = 5) -> None:
        self.free_daily_limit = free_daily_limit
        self._usage_counter: Dict[str, int] = {}
        self._paid_tickets: Dict[str, int] = {}

    def grant_paid_tickets(self, player_id: str, count: int) -> None:
        self._paid_tickets[player_id] = self._paid_tickets.get(player_id, 0) + count

    def check_and_consume(
        self, player_id: str, use_paid_ticket: bool = False
    ) -> Tuple[bool, int, bool, str]:
        """返回: (是否放行, 剩余免费次数, 是否扣除了付费点卡, 提示信息)"""
        used = self._usage_counter.get(player_id, 0)
        if used < self.free_daily_limit:
            self._usage_counter[player_id] = used + 1
            remaining = self.free_daily_limit - (used + 1)
            return True, remaining, False, f"消耗 1 次今日免费推演（剩余 {remaining}/{self.free_daily_limit} 次）"

        tickets = self._paid_tickets.get(player_id, 0)
        if use_paid_ticket and tickets > 0:
            self._paid_tickets[player_id] = tickets - 1
            return True, 0, True, f"免费配额已满，已消耗 1 张推演点卡（剩余点卡 {tickets - 1} 张）"

        return (
            False,
            0,
            False,
            f"今日免费沙盒推演次数已用尽（{self.free_daily_limit}/{self.free_daily_limit}），请开启点卡推演或等待明日重置。",
        )


class DaytimeSandboxEngine:
    """日间局部快推沙盒引擎（正式接入 RoleAgentRunner，最多唤醒 3 名角色，隔离内存快照）"""

    MAX_SANDBOX_ACTORS = 3

    def __init__(
        self,
        quota_limiter: Optional[DailyQuotaLimiter] = None,
        role_agent_runner: Optional[RoleAgentRunner] = None,
    ) -> None:
        self.era_guard = EraIronLawGuard()
        self.quota_limiter = quota_limiter or DailyQuotaLimiter(free_daily_limit=3)
        self.role_agent_runner = role_agent_runner or RoleAgentRunner()
        self._baseline_roster: Dict[str, CharacterState] = {
            "char_caocao": CharacterState(
                character_id="char_caocao",
                name="曹操",
                role_tier=RoleTier.COMMANDER,
                psych_slots={"suspicion": 63, "impatience": 58, "naval_anxiety": 68},
            ),
            "char_chengyu": CharacterState(
                character_id="char_chengyu",
                name="程昱",
                role_tier=RoleTier.STRATEGIST,
            ),
            "char_jianggan": CharacterState(
                character_id="char_jianggan",
                name="蒋干",
                role_tier=RoleTier.STRATEGIST,
            ),
            "char_lusu": CharacterState(
                character_id="char_lusu",
                name="鲁肃",
                role_tier=RoleTier.STRATEGIST,
            ),
            "char_pangtong": CharacterState(
                character_id="char_pangtong",
                name="庞统",
                role_tier=RoleTier.STRATEGIST,
            ),
            "char_wulin_granary": CharacterState(
                character_id="char_wulin_granary",
                name="乌林粮草官",
                role_tier=RoleTier.MINOR,
                butterfly_multiplier=2.2,
                minor_cap=MinorInfluenceCap(max_delay_hours=12.0, max_loss_ratio=0.20),
            ),
        }

    async def preview_single_scene(
        self, req: SandboxPreviewRequest
    ) -> BranchComparisonResult:
        t0 = time.perf_counter()

        # 1. 强制裁剪聚焦角色人数上限（最多 3 人，保障 <4s 极速响应与低成本）
        focus_actors = [
            cid for cid in req.focus_actors if cid in CHIBI_PERSONA_CATALOG
        ][: self.MAX_SANDBOX_ACTORS]

        # 2. 先过三道时代防线（违规输入不扣减玩家免费配额！）
        verdict = self.era_guard.inspect_and_sanitize(req.player_command)
        if not verdict.passed:
            used = self.quota_limiter._usage_counter.get(req.player_id, 0)
            rem = max(0, self.quota_limiter.free_daily_limit - used)
            return BranchComparisonResult(
                passed_era_guard=False,
                guard_message=f"[{verdict.blocked_stage}] {verdict.reason}",
                quota_remaining=rem,
                used_paid_ticket=False,
                elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                awoken_actors=[],
            )

        # 3. 校验并扣减每日沙盒配额
        allowed, rem_quota, used_ticket, quota_msg = (
            self.quota_limiter.check_and_consume(req.player_id, req.use_paid_ticket)
        )
        if not allowed:
            return BranchComparisonResult(
                passed_era_guard=True,
                guard_message=quota_msg,
                quota_remaining=0,
                used_paid_ticket=False,
                elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                awoken_actors=[],
            )

        # 4. 深拷贝基线状态（确保沙盒试算 100% 隔离，绝不污染主线）
        sandbox_sm = DeterministicStateMachine()
        isolated_roster = copy.deepcopy(self._baseline_roster)
        isolated_caocao = isolated_roster["char_caocao"]
        before_slots = copy.deepcopy(isolated_caocao.psych_slots)

        # 5. 通过 RoleAgentRunner 并发唤醒目标舞台的 2-3 名角色做单轮快速推演
        dialogue, locked_facts, verdict_note, cache_hashes = (
            await self._run_role_agents_for_hypothesis(
                sandbox_sm,
                isolated_roster,
                req.zone_id,
                focus_actors,
                req.enemy_hypothesis,
                verdict.sanitized_event_prompt or "",
            )
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return BranchComparisonResult(
            passed_era_guard=True,
            guard_message=quota_msg,
            quota_remaining=rem_quota,
            used_paid_ticket=used_ticket,
            elapsed_ms=round(elapsed_ms, 2),
            awoken_actors=focus_actors,
            cache_prefix_hashes=cache_hashes,
            sanitized_prompt=verdict.sanitized_event_prompt,
            psych_slots_before=before_slots,
            psych_slots_after=isolated_caocao.psych_slots,
            newly_locked_facts=locked_facts,
            simulated_dialogue=dialogue,
            tactical_verdict=verdict_note,
        )

    async def _run_role_agents_for_hypothesis(
        self,
        sm: DeterministicStateMachine,
        isolated_roster: Dict[str, CharacterState],
        zone_id: str,
        focus_actors: List[str],
        hypothesis: EnemyHypothesis,
        sanitized_prompt: str,
    ) -> Tuple[List[Dict[str, str]], List[str], str, Dict[str, str]]:
        """调用 RoleAgentRunner 并发生成局部舞台角色决策，并在隔离状态机上结算因果差异"""
        caocao = isolated_roster["char_caocao"]
        locked: List[str] = []

        # 根据玩家预设的敌方应对假设构造局部舞台线索
        if hypothesis == EnemyHypothesis.CAOCAO_NORMAL_VOLLEY:
            stage_clues = ["子时江面大雾弥漫，江东草船擂鼓逼近，曹军谨守水寨乱箭齐发"]
        elif hypothesis == EnemyHypothesis.CAOCAO_FIRE_ARROWS:
            stage_clues = ["江东轻舟吃水甚浅似载干草，曹军水寨果断换装火箭齐射"]
        else:
            stage_clues = ["蒋干自江东盗回蔡瑁私通密信，程昱入帐极力进言劝阻"]

        # 并发调用被唤醒的 2-3 名 RoleAgent
        tasks = []
        for cid in focus_actors:
            persona = CHIBI_PERSONA_CATALOG[cid]
            char_state = isolated_roster.get(
                cid,
                CharacterState(character_id=cid, name=persona.name, role_tier=persona.role_tier),
            )
            horizon = PrivateHorizonContext(
                day=2,
                current_time="22:30",
                current_zone=zone_id,
                visible_actors_in_zone=[a for a in focus_actors if a != cid],
                local_stage_clues=stage_clues,
                sanitized_player_nudge=sanitized_prompt,
                locked_global_facts=[],
            )
            tasks.append(
                self.role_agent_runner.aexecute_turn_decision(
                    persona=persona, state=char_state, horizon=horizon
                )
            )

        decisions: List[RoleAgentDecision] = await asyncio.gather(*tasks)
        cache_hashes = {d.character_id: d.cache_prefix_hash for d in decisions}
        dialogue = [
            {
                "actor": CHIBI_PERSONA_CATALOG[d.character_id].name,
                "mood": d.portrait_mood,
                "line": d.dialogue_line,
            }
            for d in decisions
        ]

        # 在隔离状态机上结算事件标签
        if hypothesis == EnemyHypothesis.CAOCAO_NORMAL_VOLLEY:
            sm.process_ai_event(caocao, EventTag.NIGHT_HARASSMENT, "22:00")
            new_facts = sm.process_ai_event(caocao, EventTag.CHAIN_SHIPS_PROPOSED, "22:30")
            locked.extend([f.value for f in new_facts])
            verdict_note = (
                "【推演结论：大吉】若曹营选常规万箭齐发，不仅白送十万支箭，且曹操水战焦虑将突破 75 阈值，"
                "自动锁死「铁索连舟」因果锚点！"
            )
        elif hypothesis == EnemyHypothesis.CAOCAO_FIRE_ARROWS:
            sm.process_ai_event(caocao, EventTag.NIGHT_HARASSMENT, "22:00")
            verdict_note = (
                "【推演结论：平局偏险】若曹营算准并换装火箭，常规借箭将受损；"
                "建议搭配「空船诱饵」暗棋消耗曹军猛火油储备。"
            )
        else:
            for d in decisions:
                if d.event_tag:
                    new_f = sm.process_ai_event(caocao, d.event_tag, "23:00")
                    for f in new_f:
                        if f.value not in locked:
                            locked.append(f.value)
            verdict_note = (
                "【推演结论：死局闭环】即便程昱进言劝阻（EVENT_SUBORDINATE_PLEA），曹操疑心已达 81 "
                "并激活「猜忌荆州」单向棘轮，100% 触发「斩杀蔡瑁」不可逆锚点！"
            )

        return dialogue, locked, verdict_note, cache_hashes


# ---------------------------------------------------------------------------
# 自包含验证演示（直接运行 python3 daytime_sandbox.py 测试 RoleAgent 接入与配额拦截）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def run_demo() -> None:
        limiter = DailyQuotaLimiter(free_daily_limit=2)
        limiter.grant_paid_tickets("player_strategist_01", count=1)
        engine = DaytimeSandboxEngine(quota_limiter=limiter)

        base_cmd = {
            "player_id": "player_strategist_01",
            "character_id": "char_jianggan",
            "template_id": "TPL_DELIVER_SECRET_NOTE",
            "target_id": "char_caocao",
            "slot_text": "蔡瑁张允暗通周瑜欲献水寨",
            "butterfly_cost": 2,
        }

        scenarios = [
            ("第 1 次试算（假设曹军常规放箭）", EnemyHypothesis.CAOCAO_NORMAL_VOLLEY, False),
            ("第 2 次试算（假设程昱极力劝阻反间计）", EnemyHypothesis.CAOCAO_TRUST_CHENGYU, False),
            ("第 3 次试算（免费次数耗尽，未开启点卡 -> 预期拦截）", EnemyHypothesis.CAOCAO_FIRE_ARROWS, False),
            ("第 4 次试算（免费耗尽，开启点卡抵扣 -> 预期放行）", EnemyHypothesis.CAOCAO_FIRE_ARROWS, True),
        ]

        for title, hyp, use_ticket in scenarios:
            print(f"=== {title} ===")
            req = SandboxPreviewRequest(
                player_id="player_strategist_01",
                zone_id="caoying_tent",
                focus_actors=["char_jianggan", "char_chengyu", "char_caocao", "char_xuchu"],
                player_command=base_cmd,
                enemy_hypothesis=hyp,
                use_paid_ticket=use_ticket,
            )
            res = await engine.preview_single_scene(req)
            print(f"  [配额状态]: {res.guard_message} | 耗时: {res.elapsed_ms} ms")
            if res.awoken_actors:
                print(f"  [局部唤醒 RoleAgent(上限3人)]: {res.awoken_actors}")
                print(f"  [Context Cache 指纹]: {res.cache_prefix_hashes}")
                print(f"  [曹操心理槽对比]: {res.psych_slots_before} -> {res.psych_slots_after}")
                print(f"  [新锁定事实锚点]: {res.newly_locked_facts}")
                print(f"  {res.tactical_verdict}\n")
            else:
                print("  [已阻断大模型调用，零算力消耗]\n")

    asyncio.run(run_demo())
