"""
《千古一局：暗合推演》服务端核心状态机与因果锚点引擎 (state_machine.py - v1.1 QA 平衡修正版)

QA 审查与平衡性修正要点：
1. 修复“曹操初始疑心 65 + 反间信 +20 = 85 单步秒杀蔡瑁”的阈值过脆漏洞：
   - 将曹操初始 suspicion 下调至 55，FORGED_LETTER_FOUND 单次冲击调整为 +18（单步仅达 73，激活单向棘轮但未达 80 斩杀线）。
   - 必须配合第二重暗棋（如 NIGHT_HARASSMENT 带来的疑心微澜 +8 或小人物流言 NOISE_RUMOR_SPREAD +8）形成“双暗棋连环配合”，才能跨过 >=80 的必斩蔡瑁不可逆阈值。
2. 消除硬编码魔法数字：将棘轮激活阈值（RATCHET_LOCK_THRESHOLD = 70）与不可逆锚点阈值提取为显式配置常量。
"""

from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 显式状态机阈值常量（消除硬编码魔法数字）
# ---------------------------------------------------------------------------
RATCHET_LOCK_THRESHOLD: int = 70          # 激活单向棘轮执念的心理槽门槛
THRESHOLD_KILL_CAIMAO_SUSPICION: int = 80 # 触发立斩蔡瑁的最低疑心值
THRESHOLD_LOCK_SHIPS_ANXIETY: int = 75    # 触发铁索连舟的最低水战焦虑值


class RoleTier(str, Enum):
    COMMANDER = "COMMANDER"      # 主帅（如曹操、周瑜）
    STRATEGIST = "STRATEGIST"    # 谋士（如程昱、鲁肃、庞统）
    MINOR = "MINOR"              # 小人物（如乌林粮草官、蒋干书童）


class EventTag(str, Enum):
    """AI 对话博弈后允许输出的标准化事件标签（禁止自由文本直接改状态）"""
    NIGHT_HARASSMENT = "EVENT_NIGHT_HARASSMENT"          # 江东夜袭扰营（焦虑+12，疑心+8）
    CHAIN_SHIPS_PROPOSED = "EVENT_CHAIN_SHIPS_PROPOSED"  # 庞统献连环计（焦虑+8）
    SUBORDINATE_PLEA = "EVENT_SUBORDINATE_PLEA"          # 部下求情/劝阻（受单向棘轮拦截）
    FORGED_LETTER_FOUND = "EVENT_FORGED_LETTER_FOUND"    # 发现反间密信（疑心+18，需配合前序铺垫方能破80）
    HARD_COUNTER_PROOF = "EVENT_HARD_COUNTER_PROOF"      # 抓获活口/确凿反证（唯一可破棘轮）
    GRAIN_DAMP_DELAY = "EVENT_GRAIN_DAMP_DELAY"          # 粮草受潮/船位微调


class FactAnchor(str, Enum):
    """全局只读事实锚点枚举（一旦写入不可撤销）"""
    CAIMAO_EXECUTED = "FACT_CAIMAO_EXECUTED"      # 蔡瑁张允已被斩，曹军水师统帅空缺
    SHIPS_CHAINED = "FACT_SHIPS_CHAINED"          # 曹营战船已用铁索连舟
    NORTH_ARMY_ROUT = "FACT_NORTH_ARMY_ROUT"      # 北军晕船崩溃，未战先乱


class RatchetBelief(str, Enum):
    """单向棘轮执念枚举"""
    NEED_STABLE_SHIPS = "BELIEF_NEED_STABLE_SHIPS"  # 执念：北军不习水战，唯有连舟可解
    DISTRUST_JINGZHOU = "BELIEF_DISTRUST_JINGZHOU"  # 执念：荆州降将必有异心


class MinorInfluenceCap(BaseModel):
    """小人物三大物理影响力天花板"""
    max_delay_hours: float = Field(default=12.0, description="最大允许时间延迟（小时）")
    max_loss_ratio: float = Field(default=0.20, description="最大局部损耗比例（0.0-0.20）")
    max_intel_noise: int = Field(default=1, description="单回合允许添加的信息噪点数")


class CharacterState(BaseModel):
    """角色运行时状态模型"""
    character_id: str
    name: str
    role_tier: RoleTier
    butterfly_multiplier: float = 1.0
    psych_slots: Dict[str, int] = Field(
        default_factory=lambda: {"suspicion": 55, "impatience": 50, "naval_anxiety": 58}
    )
    ratchet_beliefs: Set[RatchetBelief] = Field(default_factory=set)
    minor_cap: Optional[MinorInfluenceCap] = None


class ActionPrimitive(BaseModel):
    """输出给客户端 PixiJS 2.5D 沙盘的标准动作原语"""
    timestamp: str
    cmd: str  # MOVE | INTERACT | EMOTE | VFX | ANCHOR_LOCK
    payload: Dict[str, str]


# ---------------------------------------------------------------------------
# 确定性事件冲击矩阵：(目标心理槽, 变动值, 关联单向棘轮执念)
# ---------------------------------------------------------------------------
EVENT_DELTAS: Dict[EventTag, List[Tuple[str, int, Optional[RatchetBelief]]]] = {
    EventTag.NIGHT_HARASSMENT: [
        ("naval_anxiety", +12, RatchetBelief.NEED_STABLE_SHIPS),
        ("suspicion", +8, RatchetBelief.DISTRUST_JINGZHOU),
        ("impatience", +10, None),
    ],
    EventTag.CHAIN_SHIPS_PROPOSED: [
        ("naval_anxiety", +8, RatchetBelief.NEED_STABLE_SHIPS),
    ],
    EventTag.FORGED_LETTER_FOUND: [
        ("suspicion", +18, RatchetBelief.DISTRUST_JINGZHOU),
    ],
    EventTag.SUBORDINATE_PLEA: [
        # 普通情况下部下求情会降低疑心与焦虑，但若对应的单向棘轮已锁定，则此负向衰减会被拦截！
        ("suspicion", -12, RatchetBelief.DISTRUST_JINGZHOU),
        ("naval_anxiety", -10, RatchetBelief.NEED_STABLE_SHIPS),
    ],
    EventTag.HARD_COUNTER_PROOF: [
        # 只有硬性反证才能击穿棘轮并大幅降低疑心
        ("suspicion", -30, None),
    ],
}


class DeterministicStateMachine:
    """确定性状态机：管理全局事实锚点、棘轮保护与阈值触发"""

    def __init__(self) -> None:
        self.global_facts: Set[FactAnchor] = set()
        self.timeline_log: List[ActionPrimitive] = []

    def apply_minor_action_clamp(
        self, char: CharacterState, req_delay_hours: float, req_loss_ratio: float
    ) -> Tuple[float, float]:
        """强制裁剪小人物的影响力，防止小人物一票否决主帅布局"""
        if char.role_tier != RoleTier.MINOR or not char.minor_cap:
            return req_delay_hours, req_loss_ratio

        clamped_delay = min(max(0.0, req_delay_hours), char.minor_cap.max_delay_hours)
        clamped_loss = min(max(0.0, req_loss_ratio), char.minor_cap.max_loss_ratio)
        return clamped_delay, clamped_loss

    def process_ai_event(
        self, char: CharacterState, event: EventTag, timestamp: str = "23:00"
    ) -> List[FactAnchor]:
        """
        处理 AI 博弈产出的事件标签：
        1. 若是硬性反证，先解除相关单向棘轮；
        2. 计算心理槽变动（若触发单向棘轮保护，拦截一切试图降低该执念的软性妥协）；
        3. 检查是否跨过不可逆阈值，自动锁定全局事实锚点。
        """
        if event == EventTag.HARD_COUNTER_PROOF:
            char.ratchet_beliefs.clear()

        for slot_name, delta, linked_belief in EVENT_DELTAS.get(event, []):
            current_val = char.psych_slots.get(slot_name, 50)

            # 核心防妥协机制：单向棘轮激活时，拒绝软性事件的负向衰减（只升不降）
            if delta < 0 and linked_belief and (linked_belief in char.ratchet_beliefs):
                continue

            new_val = max(0, min(100, current_val + delta))
            char.psych_slots[slot_name] = new_val

            # 当某项心理指标突破 RATCHET_LOCK_THRESHOLD (70) 时，自动激活对应的单向棘轮执念
            if new_val >= RATCHET_LOCK_THRESHOLD and linked_belief:
                char.ratchet_beliefs.add(linked_belief)

        return self._evaluate_thresholds(char, timestamp)

    def _evaluate_thresholds(
        self, char: CharacterState, timestamp: str
    ) -> List[FactAnchor]:
        """不可逆阈值判定：跨过门槛立即锁死事实锚点，生成客户端高光原语"""
        newly_locked: List[FactAnchor] = []
        if char.character_id != "char_caocao":
            return newly_locked

        suspicion = char.psych_slots.get("suspicion", 0)
        anxiety = char.psych_slots.get("naval_anxiety", 0)

        # 规则 1：疑心 >= 80 且持有荆州怀疑棘轮 -> 必斩蔡瑁（不可逆）
        if (
            suspicion >= THRESHOLD_KILL_CAIMAO_SUSPICION
            and RatchetBelief.DISTRUST_JINGZHOU in char.ratchet_beliefs
            and FactAnchor.CAIMAO_EXECUTED not in self.global_facts
        ):
            self._lock_fact(
                FactAnchor.CAIMAO_EXECUTED,
                "曹操疑心突破阈值(>=80)，挥剑立斩蔡瑁、张允，水军统帅易主！",
                timestamp,
            )
            newly_locked.append(FactAnchor.CAIMAO_EXECUTED)

        # 规则 2：水战焦虑 >= 75 且持有连舟棘轮 -> 必锁战船（不可逆）
        if (
            anxiety >= THRESHOLD_LOCK_SHIPS_ANXIETY
            and RatchetBelief.NEED_STABLE_SHIPS in char.ratchet_beliefs
            and FactAnchor.SHIPS_CHAINED not in self.global_facts
        ):
            self._lock_fact(
                FactAnchor.SHIPS_CHAINED,
                "北军晕船焦虑突破阈值(>=75)，曹操采纳连环计，大船悉数以铁索锁死！",
                timestamp,
            )
            newly_locked.append(FactAnchor.SHIPS_CHAINED)

        return newly_locked

    def _lock_fact(self, fact: FactAnchor, summary: str, timestamp: str) -> None:
        self.global_facts.add(fact)
        self.timeline_log.append(
            ActionPrimitive(
                timestamp=timestamp,
                cmd="ANCHOR_LOCK",
                payload={"fact_id": fact.value, "summary": summary},
            )
        )


if __name__ == "__main__":
    sm = DeterministicStateMachine()
    caocao = CharacterState(
        character_id="char_caocao",
        name="曹操",
        role_tier=RoleTier.COMMANDER,
        psych_slots={"suspicion": 55, "impatience": 55, "naval_anxiety": 58},
    )

    print("=== QA 平衡修正测试：单步反间信仅激活棘轮(73)，需配合夜袭扰营(+8 -> 81)方能斩蔡瑁 ===")
    locked_step1 = sm.process_ai_event(caocao, EventTag.FORGED_LETTER_FOUND, timestamp="21:00")
    print(f"Step 1 [单发反间信] -> 疑心: {caocao.psych_slots['suspicion']} (激活棘轮={len(caocao.ratchet_beliefs)>0}), 锁定事实: {locked_step1}")
    locked_step2 = sm.process_ai_event(caocao, EventTag.NIGHT_HARASSMENT, timestamp="22:00")
    print(f"Step 2 [叠加夜袭扰营] -> 疑心: {caocao.psych_slots['suspicion']}, 锁定事实: {[f.value for f in locked_step2]}")
    sm.process_ai_event(caocao, EventTag.SUBORDINATE_PLEA, timestamp="22:30")
    print(f"Step 3 [部下求情被棘轮拦截] -> 疑心维持: {caocao.psych_slots['suspicion']}")
