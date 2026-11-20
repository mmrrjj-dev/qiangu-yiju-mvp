"""
《千古一局：暗合推演》夜间 BSP 舞台分区并发推演引擎 (nightly_bsp_job.py - v1.3 正式接入 role_agent.py)

核心职责：
1. 舞台分区按需唤醒（Zone-Based Wakeup）：未受玩家微调或异常事件波及的角色保持休眠（零算力消耗），仅唤醒处于活跃风暴眼的舞台。
2. 正式接入 RoleAgentRunner（替换原内置模拟函数）：
   - Superstep 1（全员并发思考）：为每名唤醒角色构造隔离的 `PrivateHorizonContext`（仅含所在舞台同场人物、局部线索、全局已锁死事实与史官转译后的微扰），通过 `asyncio.gather` 并发调用 `role_agent_runner.aexecute_turn_decision`，并记录每个角色的 `cache_prefix_hash`。
   - Superstep 2（分舞台短串行交互 + 状态机棘轮判定）：由角色 Agent 产出的 `RoleAgentDecision` 驱动动作原语与状态机 `EventTag` 结算。
   - Superstep 2.5（反事实死局强验算）：调用 `CounterfactualDeadlockVerifier` 验证是否 0 生路。
   - Superstep 3（导演编译与契约导出）：导出含 `cache_prefix_hash` 与 `counterfactual_audit` 的 `mock_chibi_day2_replay.json`。
"""

from __future__ import annotations
import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Set

from state_machine import (
    CharacterState,
    DeterministicStateMachine,
    EventTag,
    MinorInfluenceCap,
    RoleTier,
)
from era_guard import EraIronLawGuard
from director_compiler import DirectorCompiler
from counterfactual import CounterfactualDeadlockVerifier, DeadlockVerificationReport
from role_agent import (
    CHIBI_PERSONA_CATALOG,
    PrivateHorizonContext,
    RoleAgentDecision,
    RoleAgentRunner,
)


class NightlyBSPPipeline:
    """夜间 BSP 舞台分区并发推演调度器（正式接入 RoleAgentRunner 与 CounterfactualDeadlockVerifier）"""

    def __init__(
        self, day: int = 2, role_agent_runner: Optional[RoleAgentRunner] = None
    ) -> None:
        self.day = day
        self.state_machine = DeterministicStateMachine()
        self.era_guard = EraIronLawGuard()
        self.compiler = DirectorCompiler(day=day)
        self.counterfactual_verifier = CounterfactualDeadlockVerifier()
        self.role_agent_runner = role_agent_runner or RoleAgentRunner()
        self.roster: Dict[str, CharacterState] = self._init_chibi_roster()
        self.zone_assignments: Dict[str, str] = {
            "char_caocao": "caoying_tent",
            "char_chengyu": "caoying_tent",
            "char_jianggan": "caoying_tent",
            "char_pangtong": "caoying_port",
            "char_caimao": "caoying_port",
            "char_wulin_granary": "wulin_depot",
            "char_zhouyu": "jiangdong_tent",
            "char_lusu": "jiangdong_pier",
            "char_huangai": "jiangdong_pier",
            "char_old_sailor": "jiangdong_pier",
            # 常态休眠角色（本回合无玩家指令波及，演示按需休眠省算力）
            "char_xuchu": "caoying_tent",
            "char_ganning": "jiangdong_pier",
        }

    def _init_chibi_roster(self) -> Dict[str, CharacterState]:
        """初始化角色状态（同步 v1.1 平衡后的收敛冷门倍率 1.0x - 2.2x 与曹操 55 初始疑心）"""
        return {
            "char_caocao": CharacterState(
                character_id="char_caocao",
                name="曹操",
                role_tier=RoleTier.COMMANDER,
                butterfly_multiplier=1.00,
                psych_slots={"suspicion": 55, "impatience": 58, "naval_anxiety": 64},
            ),
            "char_chengyu": CharacterState(
                character_id="char_chengyu",
                name="程昱",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.25,
            ),
            "char_jianggan": CharacterState(
                character_id="char_jianggan",
                name="蒋干",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.40,
            ),
            "char_pangtong": CharacterState(
                character_id="char_pangtong",
                name="庞统",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.35,
            ),
            "char_caimao": CharacterState(
                character_id="char_caimao",
                name="蔡瑁",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.30,
            ),
            "char_wulin_granary": CharacterState(
                character_id="char_wulin_granary",
                name="乌林粮草官",
                role_tier=RoleTier.MINOR,
                butterfly_multiplier=2.20,
                minor_cap=MinorInfluenceCap(max_delay_hours=12.0, max_loss_ratio=0.20),
            ),
            "char_zhouyu": CharacterState(
                character_id="char_zhouyu",
                name="周瑜",
                role_tier=RoleTier.COMMANDER,
                butterfly_multiplier=1.00,
            ),
            "char_lusu": CharacterState(
                character_id="char_lusu",
                name="鲁肃",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.25,
            ),
            "char_huangai": CharacterState(
                character_id="char_huangai",
                name="黄盖",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.35,
            ),
            "char_old_sailor": CharacterState(
                character_id="char_old_sailor",
                name="江东老水手",
                role_tier=RoleTier.MINOR,
                butterfly_multiplier=2.00,
                minor_cap=MinorInfluenceCap(max_delay_hours=6.0, max_loss_ratio=0.15),
            ),
            "char_xuchu": CharacterState(
                character_id="char_xuchu",
                name="许褚",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.25,
            ),
            "char_ganning": CharacterState(
                character_id="char_ganning",
                name="甘宁",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.30,
            ),
        }

    def _build_private_horizon_for_actor(
        self,
        char_id: str,
        awake_ids: List[str],
        sanitized_notes: Dict[str, str],
    ) -> PrivateHorizonContext:
        """为单个角色构造严格隔离的舞台私密视野（绝不泄露其他舞台未公开的暗棋）"""
        my_zone = self.zone_assignments.get(char_id, "caoying_tent")
        same_zone_actors = [
            cid
            for cid in awake_ids
            if cid != char_id and self.zone_assignments.get(cid) == my_zone
        ]

        zone_clues_map: Dict[str, List[str]] = {
            "wulin_depot": ["乌林河道狭窄，多艘运粮船与干草船正在调度"],
            "jiangdong_pier": ["江东连日筹集干草青布，二十艘轻舟整装待发"],
            "jiangdong_tent": ["周瑜与鲁肃密议破曹之策，乌林对岸已派疑兵"],
            "caoying_port": ["北军士卒不习水战，江面颠簸致呕吐腹疾频发"],
            "caoying_tent": ["蒋干自江东归来求见丞相，携回密信一封", "江面浓雾弥漫，隐闻江东擂鼓之声"],
        }
        return PrivateHorizonContext(
            day=self.day,
            current_time="21:30",
            current_zone=my_zone,
            visible_actors_in_zone=same_zone_actors,
            local_stage_clues=zone_clues_map.get(my_zone, []),
            sanitized_player_nudge=sanitized_notes.get(char_id),
            locked_global_facts=sorted([f.value for f in self.state_machine.global_facts]),
        )

    def _extract_battlefield_conditions(
        self, sanitized_notes: Dict[str, str], extra_conditions: Optional[Set[str]] = None
    ) -> Set[str]:
        """根据当晚玩家通过三道防线注入的微扰与舞台事件，提取反事实死局验算条件集合"""
        conditions: Set[str] = set(extra_conditions or set())
        conditions.add("COND_NIGHT_DRUMS_HARASS")

        for cid, prompt_text in sanitized_notes.items():
            if "上风" in prompt_text or "干草" in prompt_text:
                conditions.add("COND_UPPER_WIND_DRY_GRASS")
            if "雾" in prompt_text or "风" in prompt_text or cid == "char_old_sailor":
                conditions.add("COND_SOUTHEAST_WIND_WINDOW")
            if "滩涂" in prompt_text or "疑兵" in prompt_text or "截断" in prompt_text or "岸" in prompt_text:
                conditions.add("COND_SHORE_RAID_BLOCKED")

        return conditions

    async def run_turn(
        self,
        player_raw_commands: List[Dict[str, Any]],
        extra_conditions: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()

        # 每次运行重置状态机与编译器，保证幂等可重入
        self.state_machine = DeterministicStateMachine()
        self.compiler = DirectorCompiler(day=self.day)
        self.roster = self._init_chibi_roster()

        # 0. 三道防线清洗玩家指令
        sanitized_notes: Dict[str, str] = {}
        for cmd in player_raw_commands:
            verdict = self.era_guard.inspect_and_sanitize(cmd)
            if verdict.passed and verdict.sanitized_event_prompt:
                sanitized_notes[cmd["character_id"]] = verdict.sanitized_event_prompt

        # 1. 舞台分区按需唤醒（过滤掉无事件波及的休眠角色许褚、甘宁）
        sleeping_ids: Set[str] = {"char_xuchu", "char_ganning"}
        awake_ids = [cid for cid in self.roster if cid not in sleeping_ids]

        # 2. Superstep 1：通过 RoleAgentRunner 执行全员并发思考 (asyncio.gather + Context Caching)
        think_coroutines = []
        for cid in awake_ids:
            persona = CHIBI_PERSONA_CATALOG[cid]
            horizon = self._build_private_horizon_for_actor(cid, awake_ids, sanitized_notes)
            think_coroutines.append(
                self.role_agent_runner.aexecute_turn_decision(
                    persona=persona,
                    state=self.roster[cid],
                    horizon=horizon,
                )
            )

        agent_decisions: List[RoleAgentDecision] = await asyncio.gather(*think_coroutines)
        decision_map: Dict[str, RoleAgentDecision] = {
            dec.character_id: dec for dec in agent_decisions
        }

        # 3. Superstep 2：由 RoleAgentDecision 驱动舞台交互、小人物边界裁剪与状态机棘轮判定
        granary_dec = decision_map["char_wulin_granary"]
        clamped_delay, clamped_loss = self.state_machine.apply_minor_action_clamp(
            self.roster["char_wulin_granary"],
            req_delay_hours=granary_dec.req_delay_hours,
            req_loss_ratio=granary_dec.req_loss_ratio,
        )
        self.compiler.add_move(
            "20:00",
            "char_wulin_granary",
            granary_dec.target_zone,
            granary_dec.target_slot,
            granary_dec.travel_mode,
        )
        self.compiler.add_emote(
            "20:15",
            granary_dec.target_zone,
            "char_wulin_granary",
            granary_dec.portrait_mood,
            f"{granary_dec.dialogue_line}（状态机已裁剪：延迟{clamped_delay:.0f}h/火势加成{clamped_loss:.0%}）",
        )

        # [21:00] 江东老水手观云测雾 -> 鲁肃草船出坞
        sailor_dec = decision_map["char_old_sailor"]
        lusu_dec = decision_map["char_lusu"]
        self.compiler.add_interact(
            "21:00",
            sailor_dec.target_zone,
            "char_old_sailor",
            sailor_dec.interact_target or "char_lusu",
            sailor_dec.interact_anim,
        )
        self.compiler.add_emote(
            "21:05",
            sailor_dec.target_zone,
            "char_old_sailor",
            sailor_dec.portrait_mood,
            sailor_dec.dialogue_line,
        )
        self.compiler.add_vfx("21:30", "yangtze_river", "HEAVY_FOG")
        self.compiler.add_move(
            "21:45", "char_lusu", lusu_dec.target_zone, lusu_dec.target_slot, lusu_dec.travel_mode
        )
        self.compiler.add_vfx("22:00", "yangtze_river", "WAR_DRUMS")
        self.compiler.add_emote(
            "22:05",
            lusu_dec.target_zone,
            "char_lusu",
            lusu_dec.portrait_mood,
            lusu_dec.dialogue_line,
        )

        # [22:15] 江东夜袭鼓声传至曹营水寨 + 庞统献连环计 -> 触发曹操锁船棘轮与不可逆锚点
        caocao = self.roster["char_caocao"]
        pangtong_dec = decision_map["char_pangtong"]
        self.compiler.add_interact(
            "22:15",
            pangtong_dec.target_zone,
            "char_pangtong",
            pangtong_dec.interact_target or "char_caocao",
            pangtong_dec.interact_anim,
        )
        self.compiler.add_emote(
            "22:20",
            pangtong_dec.target_zone,
            "char_pangtong",
            pangtong_dec.portrait_mood,
            pangtong_dec.dialogue_line,
        )
        if lusu_dec.event_tag:
            self.state_machine.process_ai_event(caocao, lusu_dec.event_tag, "22:25")
        locked_ships = self.state_machine.process_ai_event(
            caocao, pangtong_dec.event_tag or EventTag.CHAIN_SHIPS_PROPOSED, "22:30"
        )
        for fact in locked_ships:
            self.compiler.add_anchor_lock(
                "22:30",
                "caoying_port",
                fact.value,
                "北军晕船焦虑突破阈值(>=75)，曹操采纳连环计，战船悉数以铁索锁死！",
            )

        # [23:00] 蒋干献反间密信 -> 曹操疑心 63+18=81 破 80 -> 程昱求情被单向棘轮拦截 -> 立斩蔡瑁
        jianggan_dec = decision_map["char_jianggan"]
        chengyu_dec = decision_map["char_chengyu"]
        caocao_dec = decision_map["char_caocao"]

        self.compiler.add_move(
            "22:50",
            "char_jianggan",
            jianggan_dec.target_zone,
            jianggan_dec.target_slot,
            jianggan_dec.travel_mode,
        )
        self.compiler.add_interact(
            "23:00",
            jianggan_dec.target_zone,
            "char_jianggan",
            jianggan_dec.interact_target or "char_caocao",
            jianggan_dec.interact_anim,
        )
        self.compiler.add_emote(
            "23:05",
            jianggan_dec.target_zone,
            "char_jianggan",
            jianggan_dec.portrait_mood,
            jianggan_dec.dialogue_line,
        )
        locked_kill = self.state_machine.process_ai_event(
            caocao, jianggan_dec.event_tag or EventTag.FORGED_LETTER_FOUND, "23:10"
        )

        # 程昱求情（测试单向棘轮阻止 AI 心软妥协）
        self.compiler.add_interact(
            "23:15",
            chengyu_dec.target_zone,
            "char_chengyu",
            chengyu_dec.interact_target or "char_caocao",
            chengyu_dec.interact_anim,
        )
        self.compiler.add_emote(
            "23:18",
            chengyu_dec.target_zone,
            "char_chengyu",
            chengyu_dec.portrait_mood,
            chengyu_dec.dialogue_line,
        )
        if chengyu_dec.event_tag:
            self.state_machine.process_ai_event(caocao, chengyu_dec.event_tag, "23:20")

        self.compiler.add_emote(
            "23:25",
            caocao_dec.target_zone,
            "char_caocao",
            caocao_dec.portrait_mood,
            caocao_dec.dialogue_line,
        )
        self.compiler.add_interact(
            "23:30", "caoying_port", "char_caocao", "char_caimao", "EXECUTE"
        )
        for fact in locked_kill:
            self.compiler.add_anchor_lock(
                "23:35",
                "caoying_tent",
                fact.value,
                "曹操疑心突破阈值(81>=80)且受单向棘轮锁定，无视程昱劝阻立斩蔡瑁张允！",
            )

        # 4. Superstep 2.5：启动夜间反事实死局验算器（验证曹营是否真的 0 生路）
        active_conditions = self._extract_battlefield_conditions(
            sanitized_notes, extra_conditions=extra_conditions
        )
        cf_report: DeadlockVerificationReport = (
            self.counterfactual_verifier.verify_caocao_deadlock(caocao, active_conditions)
        )
        if cf_report.is_true_deadlock:
            self.compiler.add_anchor_lock(
                "23:55",
                "caoying_tent",
                "FACT_TRUE_DEADLOCK_SEALED",
                f"反事实死局验算通过（存活分支 0/{cf_report.total_branches_tested}）：授予设局方 1.20x 死局闭环加成！",
            )

        # 5. Superstep 3：汇总角色快照（含 Context Caching 指纹）并导出前端联调契约 JSON
        snapshots: Dict[str, Dict[str, Any]] = {}
        for cid, char in self.roster.items():
            dec_obj = decision_map.get(cid)
            snapshots[cid] = {
                "name": char.name,
                "role_tier": char.role_tier.value,
                "awake": cid not in sleeping_ids,
                "zone_id": self.zone_assignments[cid],
                "butterfly_multiplier": char.butterfly_multiplier,
                "psych_slots": char.psych_slots,
                "ratchet_beliefs": sorted([b.value for b in char.ratchet_beliefs]),
                "cache_prefix_hash": dec_obj.cache_prefix_hash if dec_obj else "SLEEPING_NO_CALL",
            }

        locked_list = sorted([f.value for f in self.state_machine.global_facts])
        if cf_report.is_true_deadlock:
            locked_list.append("FACT_TRUE_DEADLOCK_SEALED")

        contract = self.compiler.compile_contract(
            active_zones=["wulin_depot", "jiangdong_pier", "yangtze_river", "caoying_port", "caoying_tent"],
            locked_facts=locked_list,
            character_snapshots=snapshots,
            counterfactual_audit=cf_report.dict(),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        print(
            f"[Nightly BSP Job v1.3] 完成 Day {self.day} RoleAgent 并发推演："
            f"唤醒 {len(awake_ids)}/{len(self.roster)} 名角色 | "
            f"耗时 {elapsed_ms:.1f} ms | "
            f"反事实死局验算: is_true_deadlock={cf_report.is_true_deadlock} "
            f"(存活分支 {cf_report.survival_branches_count}/{cf_report.total_branches_tested}, "
            f"加成 {cf_report.deadlock_bonus_granted:.2f}x)"
        )
        return contract.dict()


if __name__ == "__main__":
    pipeline = NightlyBSPPipeline(day=2)
    airtight_commands = [
        {
            "player_id": "player_minor_01",
            "character_id": "char_wulin_granary",
            "template_id": "TPL_MINOR_LOGISTICS",
            "target_id": "wulin_depot",
            "slot_text": "干草船停在上风口",
            "butterfly_cost": 2,
        },
        {
            "player_id": "player_east_02",
            "character_id": "char_old_sailor",
            "template_id": "TPL_DELIVER_SECRET_NOTE",
            "target_id": "char_lusu",
            "slot_text": "观云测出子时江面起大雾",
            "butterfly_cost": 1,
        },
        {
            "player_id": "player_zhouyu_03",
            "character_id": "char_zhouyu",
            "template_id": "TPL_MINOR_LOGISTICS",
            "target_id": "wulin_depot",
            "slot_text": "乌林岸边设疑兵截断滩涂",
            "butterfly_cost": 3,
        },
    ]

    replay_data = asyncio.run(pipeline.run_turn(airtight_commands))
    out_path = os.path.join(os.path.dirname(__file__), "mock_chibi_day2_replay.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(replay_data, f, ensure_ascii=False, indent=2)
    print(f"[Contract Exported] 已导出 RoleAgent 驱动的前端契约文件: {out_path}")