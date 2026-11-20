"""
《千古一局：暗合推演》夜间强推理“反事实死局验算器” (counterfactual.py)

核心职责：
1. 防“伪羊毛局”（杜绝靠 AI 临场降智白嫖死局加成）：
   当一方玩家声称已将敌方逼入“羊毛死局（无论怎么选都必败）”时，系统在夜间异步队列中启动本验算器，站在敌方主帅视角穷举所有合规应对分支。
2. 严格视野隔离（Information-Horizon Isolation）：
   反事实推演绝不开“上帝视角”作弊——敌方主帅只能基于其【当前已知线索】与【当前已激活的单向棘轮/心理状态槽】做极限最优自救。
3. 判定准则（2 回合生存树搜索）：
   - 真羊毛死局（True Deadlock）：敌方所有合规应对分支在 2 回合内均触发致命不可逆锚点（如“锁船必遭上风口干草船火攻连环焚毁” vs “不锁船则北军晕船超 90 未战先溃”，且岸上轮休路线已被截断）。此时授予 1.20x 死局闭环加成并触发提前结算。
   - 伪死局（False Deadlock / 存在破局生路）：只要存在至少 1 条合规自救分支可避免崩盘（例如玩家未提前封堵“分批上岸扎营轮休”或未激活单向棘轮），则驳回死局锁定，输出具体破局生路。
"""

from __future__ import annotations
import copy
from enum import Enum
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field

from state_machine import (
    CharacterState,
    DeterministicStateMachine,
    EventTag,
    FactAnchor,
    RatchetBelief,
    RoleTier,
)


class CommanderCounterAction(str, Enum):
    """敌方主帅（曹操）在当前时代认知下可采取的合规自救动作枚举"""
    CHAIN_SHIPS = "ACT_CHAIN_SHIPS"                  # 采纳连环计：铁索连舟稳住颠簸
    REFUSE_CHAIN_STAY_AFLOAT = "ACT_REFUSE_CHAIN"    # 拒绝连舟：全军继续在江面独立船只颠簸
    ROTATE_SHORE_CAMPS = "ACT_ROTATE_SHORE_CAMPS"    # 分批上岸扎营轮休（需陆路营地未被骚扰封锁）
    VERIFY_JINGZHOU_LETTER = "ACT_VERIFY_LETTER"     # 暂缓行刑：派亲信核查蔡瑁反间密信真伪
    EXECUTE_CAIMAO_NOW = "ACT_EXECUTE_CAIMAO"        # 宁错杀毋放过：立斩蔡瑁张允


class CounterfactualBranchOutcome(BaseModel):
    """单条反事实自救分支的推演结果"""
    action: CommanderCounterAction
    action_title: str
    permitted_by_ratchet: bool = Field(description="该自救动作是否被角色当前单向棘轮执念所允许")
    blocked_reason: Optional[str] = None
    final_psych_slots: Dict[str, int] = Field(default_factory=dict)
    triggered_fatal_anchors: List[str] = Field(default_factory=list)
    survived_two_turns: bool = Field(description="2回合后是否成功避免崩盘")
    branch_summary: str


class DeadlockVerificationReport(BaseModel):
    """反事实死局验算最终裁决报告"""
    target_commander: str
    is_true_deadlock: bool = Field(description="是否确认为100%无解的真羊毛死局")
    deadlock_bonus_granted: float = Field(description="授予的死局闭环天梯乘区（真死局 1.20x，伪死局 1.00x）")
    total_branches_tested: int
    survival_branches_count: int
    escape_actions: List[str] = Field(default_factory=list)
    branch_details: List[CounterfactualBranchOutcome]
    referee_verdict: str


class CounterfactualDeadlockVerifier:
    """夜间反事实死局验算引擎（基于深拷贝状态机与视野约束分支搜索）"""

    DEADLOCK_BONUS = 1.20

    def verify_caocao_deadlock(
        self,
        baseline_caocao: CharacterState,
        active_battlefield_conditions: Set[str],
    ) -> DeadlockVerificationReport:
        """
        针对曹营主帅执行反事实极限自救验算：
        active_battlefield_conditions 可包含：
          - "COND_UPPER_WIND_DRY_GRASS" : 乌林粮草官已将干草船泊于主船上风口
          - "COND_SOUTHEAST_WIND_WINDOW": 江东老水手已测准子时东南风窗
          - "COND_SHORE_RAID_BLOCKED"   : 联军轻骑已袭扰乌林沿岸，封堵分批上岸轮休路线
          - "COND_NIGHT_DRUMS_HARASS"   : 江东连夜擂鼓袭扰，北军严重失眠晕船
        """
        candidate_actions = [
            (CommanderCounterAction.CHAIN_SHIPS, "分支 1：采纳铁索连舟（优先止住北军晕船）"),
            (CommanderCounterAction.REFUSE_CHAIN_STAY_AFLOAT, "分支 2：强令解开铁索（硬抗江面风浪颠簸）"),
            (CommanderCounterAction.ROTATE_SHORE_CAMPS, "分支 3：分批上岸扎营轮休（以时间换空间）"),
            (CommanderCounterAction.VERIFY_JINGZHOU_LETTER, "分支 4：按兵不动，派校事府密查蔡瑁书信真伪"),
        ]

        branch_outcomes: List[CounterfactualBranchOutcome] = []
        escape_actions: List[str] = []

        for action, title in candidate_actions:
            outcome = self._simulate_single_counter_branch(
                baseline_caocao, action, title, active_battlefield_conditions
            )
            branch_outcomes.append(outcome)
            if outcome.survived_two_turns:
                escape_actions.append(f"{title} -> {outcome.branch_summary}")

        is_deadlock = len(escape_actions) == 0
        bonus = self.DEADLOCK_BONUS if is_deadlock else 1.00

        if is_deadlock:
            verdict = (
                "【反事实裁决：真·羊毛死局成立 (1.20x 加成)】经穷举曹营主帅全部 4 条合规自救路线，"
                "在当前单向棘轮与战场伏笔闭环下，所有分支均在 2 回合内导向不可逆崩盘，触发「大局已定」提前结算！"
            )
        else:
            verdict = (
                f"【反事实裁决：伪死局（驳回提前结算）】检测到曹营仍存 {len(escape_actions)} 条合理破局生路"
                f"（{escape_actions[0]}），设局方尚未封死全部退路，战局继续推进。"
            )

        return DeadlockVerificationReport(
            target_commander=baseline_caocao.name,
            is_true_deadlock=is_deadlock,
            deadlock_bonus_granted=bonus,
            total_branches_tested=len(branch_outcomes),
            survival_branches_count=len(escape_actions),
            escape_actions=escape_actions,
            branch_details=branch_outcomes,
            referee_verdict=verdict,
        )

    def _simulate_single_counter_branch(
        self,
        baseline_caocao: CharacterState,
        action: CommanderCounterAction,
        title: str,
        conditions: Set[str],
    ) -> CounterfactualBranchOutcome:
        """在独立深拷贝状态机上推演单条反事实自救路线"""
        sm = DeterministicStateMachine()
        sim_char = copy.deepcopy(baseline_caocao)
        fatal_anchors: List[str] = []

        # 分支 1：采纳铁索连舟
        if action == CommanderCounterAction.CHAIN_SHIPS:
            sm.process_ai_event(sim_char, EventTag.CHAIN_SHIPS_PROPOSED, "22:30")
            # 若上风口已被乌林粮草官停满干草船且东南风起，锁船直接触发连环火海必败
            if (
                "COND_UPPER_WIND_DRY_GRASS" in conditions
                and "COND_SOUTHEAST_WIND_WINDOW" in conditions
            ):
                fatal_anchors.append("FACT_SHIPS_CHAINED_FIRE_DOOM")
                return CounterfactualBranchOutcome(
                    action=action,
                    action_title=title,
                    permitted_by_ratchet=True,
                    final_psych_slots=sim_char.psych_slots,
                    triggered_fatal_anchors=fatal_anchors,
                    survived_two_turns=False,
                    branch_summary="虽止住北军晕船，但铁索锁死后撞上「乌林上风口干草船 + 子时东南风」，水寨连环焚毁必败。",
                )
            return CounterfactualBranchOutcome(
                action=action,
                action_title=title,
                permitted_by_ratchet=True,
                final_psych_slots=sim_char.psych_slots,
                triggered_fatal_anchors=[],
                survived_two_turns=True,
                branch_summary="成功稳住船阵且敌方缺少上风口火攻伏笔，曹军水寨安然度过今夜。",
            )

        # 分支 2：拒绝锁船、硬抗风浪
        if action == CommanderCounterAction.REFUSE_CHAIN_STAY_AFLOAT:
            # 若曹操已激活“唯有连舟可解晕船”单向棘轮，且遭受夜袭鼓噪，强行不锁船会导致焦虑破90全军自溃
            if "COND_NIGHT_DRUMS_HARASS" in conditions:
                sim_char.psych_slots["naval_anxiety"] = min(
                    100, sim_char.psych_slots.get("naval_anxiety", 60) + 22
                )
            if sim_char.psych_slots.get("naval_anxiety", 0) >= 88:
                fatal_anchors.append(FactAnchor.NORTH_ARMY_ROUT.value)
                return CounterfactualBranchOutcome(
                    action=action,
                    action_title=title,
                    permitted_by_ratchet=True,
                    final_psych_slots=sim_char.psych_slots,
                    triggered_fatal_anchors=fatal_anchors,
                    survived_two_turns=False,
                    branch_summary="虽避开火攻，但北军在江东夜袭鼓噪下水战焦虑突破 88，士卒呕吐不止、未战先溃。",
                )
            return CounterfactualBranchOutcome(
                action=action,
                action_title=title,
                permitted_by_ratchet=True,
                final_psych_slots=sim_char.psych_slots,
                triggered_fatal_anchors=[],
                survived_two_turns=True,
                branch_summary="江面风平浪静，北军虽有不适但未达溃散阈值，成功规避火攻。",
            )

        # 分支 3：分批上岸扎营轮休（高玩破局首选路线）
        if action == CommanderCounterAction.ROTATE_SHORE_CAMPS:
            if "COND_SHORE_RAID_BLOCKED" in conditions:
                fatal_anchors.append("FACT_SHORE_CAMP_AMBUSHED")
                return CounterfactualBranchOutcome(
                    action=action,
                    action_title=title,
                    permitted_by_ratchet=True,
                    final_psych_slots=sim_char.psych_slots,
                    triggered_fatal_anchors=fatal_anchors,
                    survived_two_turns=False,
                    branch_summary="联军已提前布置岸上疑兵截断乌林滩涂，半渡上岸时遭伏击大乱。",
                )
            return CounterfactualBranchOutcome(
                action=action,
                action_title=title,
                permitted_by_ratchet=True,
                final_psych_slots=sim_char.psych_slots,
                triggered_fatal_anchors=[],
                survived_two_turns=True,
                branch_summary="【发现破局生路】曹军分批上岸轮休，既不锁船受火攻、亦缓解北军晕船，仅延缓2日进攻即可破局！",
            )

        # 分支 4：派亲信核查蔡瑁反间信真伪
        if RatchetBelief.DISTRUST_JINGZHOU in sim_char.ratchet_beliefs and sim_char.psych_slots.get("suspicion", 0) >= 80:
            return CounterfactualBranchOutcome(
                action=action,
                action_title=title,
                permitted_by_ratchet=False,
                blocked_reason="曹操疑心已达 >=80 且激活「猜忌荆州」单向棘轮，性格底色拒绝任何拖延核查，强制立斩蔡瑁。",
                final_psych_slots=sim_char.psych_slots,
                triggered_fatal_anchors=[FactAnchor.CAIMAO_EXECUTED.value],
                survived_two_turns=False,
                branch_summary="受单向棘轮锁死，曹操拒绝复核直接斩杀水军都督蔡瑁，水师指挥链断裂。",
            )

        return CounterfactualBranchOutcome(
            action=action,
            action_title=title,
            permitted_by_ratchet=True,
            final_psych_slots=sim_char.psych_slots,
            triggered_fatal_anchors=[],
            survived_two_turns=True,
            branch_summary="【发现破局生路】曹操疑心尚未跨过 80 斩杀线，派校事府密查后识破伪造书信，保住蔡瑁水军统帅。",
        )


# ---------------------------------------------------------------------------
# 自包含验证演示（直接运行 python3 counterfactual.py 对比“伪死局”与“真羊毛死局”）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    verifier = CounterfactualDeadlockVerifier()

    # 场景 A：设局存在漏洞（玩家只塞了反间信和干草船，但没封堵“分批上岸轮休”，且疑心仅73未达80）
    caocao_flawed = CharacterState(
        character_id="char_caocao",
        name="曹操",
        role_tier=RoleTier.COMMANDER,
        psych_slots={"suspicion": 73, "impatience": 60, "naval_anxiety": 72},
        ratchet_beliefs={RatchetBelief.DISTRUST_JINGZHOU, RatchetBelief.NEED_STABLE_SHIPS},
    )
    flawed_conditions = {
        "COND_UPPER_WIND_DRY_GRASS",
        "COND_SOUTHEAST_WIND_WINDOW",
        "COND_NIGHT_DRUMS_HARASS",
    }

    print("=======================================================================")
    print("  测试 1：半吊子设局（未封堵岸上轮休 & 疑心仅73） -> 预期：检出破局生路")
    print("=======================================================================")
    rep_flawed = verifier.verify_caocao_deadlock(caocao_flawed, flawed_conditions)
    print(f"  是否真死局: {rep_flawed.is_true_deadlock} | 授予加成: {rep_flawed.deadlock_bonus_granted:.2f}x")
    print(f"  存活分支数: {rep_flawed.survival_branches_count}/{rep_flawed.total_branches_tested}")
    for esc in rep_flawed.escape_actions:
        print(f"  -> 敌方可用生路: {esc}")
    print(f"  {rep_flawed.referee_verdict}\n")

    # 场景 B：严丝合缝的真羊毛死局（疑心81破阈值 + 乌林干草船上风口 + 夜袭鼓噪 + 岸上疑兵封路）
    caocao_trapped = CharacterState(
        character_id="char_caocao",
        name="曹操",
        role_tier=RoleTier.COMMANDER,
        psych_slots={"suspicion": 81, "impatience": 68, "naval_anxiety": 76},
        ratchet_beliefs={RatchetBelief.DISTRUST_JINGZHOU, RatchetBelief.NEED_STABLE_SHIPS},
    )
    airtight_conditions = {
        "COND_UPPER_WIND_DRY_GRASS",
        "COND_SOUTHEAST_WIND_WINDOW",
        "COND_NIGHT_DRUMS_HARASS",
        "COND_SHORE_RAID_BLOCKED",
    }

    print("=======================================================================")
    print("  测试 2：严密羊毛死局（4条退路全封死） -> 预期：确认真死局并授予 1.20x")
    print("=======================================================================")
    rep_trapped = verifier.verify_caocao_deadlock(caocao_trapped, airtight_conditions)
    print(f"  是否真死局: {rep_trapped.is_true_deadlock} | 授予加成: {rep_trapped.deadlock_bonus_granted:.2f}x")
    print(f"  存活分支数: {rep_trapped.survival_branches_count}/{rep_trapped.total_branches_tested}")
    for b in rep_trapped.branch_details:
        print(f"  - {b.action_title}: 存活={b.survived_two_turns} | {b.branch_summary}")
    print(f"  {rep_trapped.referee_verdict}")