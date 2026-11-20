"""
《千古一局：暗合推演》多人混合房间匹配、AI 自动补位与中途空降接管引擎 (room_matchmaker.py)

核心职责：
1. 解决“热门角色扎堆排队、冷门角色鬼服凑不齐人”的开局死锁问题：
   - 混合托管开局（Hybrid Matchmaking）：单局 12 名赤壁角色只需 2–6 名真人玩家认领核心主帅或关键小人物即可立即开局；其余未认领角色 100% 由 `RoleAgentRunner` 按 `CHIBI_PERSONA_CATALOG` 锁定底色自动托管（`AI_HOSTED`）。
   - 冷门高倍与高光时间窗引导：选角池显式标注每个角色的 `butterfly_multiplier`（1.00x ~ 2.20x）与 `active_window_days`（如乌林粮草官高光期为 Day 2–4），吸引冲榜玩家主动分流至小人物。
2. 中途空降接管（Mid-Game Drop-In Takeover）：
   - 战役进行中（如已推进至 Day 2），新玩家可随时检索处于 `AI_HOSTED` 状态且正值高光时间窗的角色（如乌林粮草官、庞统）一键空降接管。
   - 状态无缝继承与防作弊边界：空降玩家完整继承该角色当前的 `psych_slots`、`ratchet_beliefs` 与舞台坐标，按战役剩余天数动态折算可用蝴蝶点数预算，且严禁回滚已写入全局的不可逆事实锚点（`locked_facts`）。
3. 真人超时未交卷无损兜底（AFK Auto-Fallback）：
   - 夜间 02:00 锁单时，若某名已认领角色的真人玩家未提交当日微调指令，系统自动将该角色当晚路由给 `RoleAgentRunner` 按锁定底色代管推演，绝不阻塞全房间夜间结算；连续 2 回合未操作则自动释放回可空降池。
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from state_machine import CharacterState, MinorInfluenceCap, RoleTier
from role_agent import CHIBI_PERSONA_CATALOG, LockedPersonaProfile


class RoomPhase(str, Enum):
    LOBBY_CLAIMING = "LOBBY_CLAIMING"      # 选角认领阶段（满 2 名真人且双阵营有人即可开局）
    IN_PROGRESS_DAY = "IN_PROGRESS_DAY"    # 战役推进中（白天提交暗棋 & 开放中途空降接管）
    NIGHTLY_LOCKED = "NIGHTLY_LOCKED"      # 夜间 BSP 结算锁单中
    COMPLETED = "COMPLETED"                # 战役已完结


class ControlMode(str, Enum):
    AI_HOSTED = "AI_HOSTED"                # 纯 AI 按锁定底色自动托管（开放真人认领或中途空降）
    HUMAN_CLAIMED = "HUMAN_CLAIMED"        # 真人玩家开局认领控制
    HUMAN_DROP_IN = "HUMAN_DROP_IN"        # 真人玩家中途空降接管控制


class RoleSlotStatus(BaseModel):
    """房间内单个角色槽位的认领与托管状态"""
    character_id: str
    name: str
    camp: str
    role_tier: RoleTier
    butterfly_multiplier: float = Field(description="冷门杠杆倍率（1.00x ~ 2.20x）")
    active_window_days: List[int] = Field(description="角色高光活跃时间窗（Day 1 ~ Day 5）")
    control_mode: ControlMode = ControlMode.AI_HOSTED
    owner_player_id: Optional[str] = None
    owner_player_name: Optional[str] = None
    joined_at_day: int = 1
    remaining_butterfly_budget: int = Field(default=6, description="本局剩余可用蝴蝶点数预算")
    submitted_today: bool = False
    consecutive_afk_days: int = 0
    is_recommended_drop_in: bool = Field(
        default=False, description="当前回合是否处于该小人物/谋士的高光空降时间窗"
    )


class BattleRoomState(BaseModel):
    """多人混合战局房间完整状态"""
    room_id: str
    battle_id: str = "chibi_208ad"
    current_day: int = 1
    max_days: int = 5
    phase: RoomPhase = RoomPhase.LOBBY_CLAIMING
    min_human_to_start: int = 2
    max_human_capacity: int = 8
    human_count: int = 0
    ai_hosted_count: int = 12
    locked_facts: List[str] = Field(default_factory=list)
    role_slots: Dict[str, RoleSlotStatus] = Field(default_factory=dict)
    created_at: str


class ClaimRoleRequest(BaseModel):
    """开局选角认领或中途空降接管请求体"""
    room_id: str
    player_id: str
    player_name: str
    character_id: str


class ClaimRoleResponse(BaseModel):
    """选角认领 / 空降接管结果"""
    succeeded: bool
    message: str
    control_mode: ControlMode
    character_id: str
    character_name: str
    butterfly_multiplier: float
    allocated_butterfly_budget: int
    inherited_psych_slots: Dict[str, int] = Field(default_factory=dict)
    active_locked_facts: List[str] = Field(default_factory=list)


class NightlyDispatchPlan(BaseModel):
    """夜间锁单时的真人指令与 AI 代管混合派发单"""
    room_id: str
    day: int
    human_active_actors: List[str] = Field(description="今晚由真人指令驱动的角色")
    ai_hosted_actors: List[str] = Field(description="今晚由纯 AI 自动托管的角色")
    afk_fallback_actors: List[str] = Field(description="今晚真人超时未交卷、临时转由 AI 代管的角色")
    released_afk_slots: List[str] = Field(description="因连续 2 天旷工被释放回空降池的角色")


# ---------------------------------------------------------------------------
# 预设赤壁 12 角色冷门倍率与高光活跃时间窗配置表
# ---------------------------------------------------------------------------
CHIBI_SLOT_METADATA: Dict[str, Tuple[float, List[int], int]] = {
    # character_id: (收敛后冷门倍率, 高光活跃天数列表, 5天完整局初始蝴蝶点数预算)
    "char_caocao": (1.00, [1, 2, 3, 4, 5], 8),
    "char_zhouyu": (1.00, [1, 2, 3, 4, 5], 8),
    "char_chengyu": (1.25, [1, 2, 3, 4, 5], 6),
    "char_lusu": (1.25, [1, 2, 3, 4], 6),
    "char_pangtong": (1.35, [2, 3], 5),
    "char_huangai": (1.35, [3, 4], 5),
    "char_jianggan": (1.40, [1, 2], 5),
    "char_caimao": (1.30, [1, 2], 5),
    "char_wulin_granary": (2.20, [2, 3, 4], 4),
    "char_old_sailor": (2.00, [1, 2, 3], 4),
    "char_xuchu": (1.25, [4, 5], 4),
    "char_ganning": (1.30, [3, 4, 5], 4),
}


class HybridRoomMatchmaker:
    """多人混合认领、AI 自动补位与中途空降接管调度器"""

    MAX_CONSECUTIVE_AFK_DAYS = 2  # 连续 2 回合未交卷则自动释放角色槽位供其他玩家空降

    def __init__(self) -> None:
        self._rooms: Dict[str, BattleRoomState] = {}
        self._runtime_char_states: Dict[str, Dict[str, CharacterState]] = {}
        self._seed_default_room()

    def _seed_default_room(self) -> None:
        """创建一个默认演示房间 room_chibi_01"""
        self.create_room(room_id="room_chibi_01", min_human_to_start=2)

    def create_room(
        self, room_id: Optional[str] = None, min_human_to_start: int = 2
    ) -> BattleRoomState:
        rid = room_id or f"room_{uuid.uuid4().hex[:6]}"
        now_str = datetime.now(timezone.utc).isoformat()

        slots: Dict[str, RoleSlotStatus] = {}
        char_states: Dict[str, CharacterState] = {}

        for cid, persona in CHIBI_PERSONA_CATALOG.items():
            mult, active_days, base_budget = CHIBI_SLOT_METADATA.get(
                cid, (1.20, [1, 2, 3], 5)
            )
            slots[cid] = RoleSlotStatus(
                character_id=cid,
                name=persona.name,
                camp=persona.camp,
                role_tier=persona.role_tier,
                butterfly_multiplier=mult,
                active_window_days=active_days,
                control_mode=ControlMode.AI_HOSTED,
                remaining_butterfly_budget=base_budget,
                is_recommended_drop_in=(1 in active_days),
            )
            char_states[cid] = CharacterState(
                character_id=cid,
                name=persona.name,
                role_tier=persona.role_tier,
                butterfly_multiplier=mult,
                minor_cap=MinorInfluenceCap() if persona.role_tier == RoleTier.MINOR else None,
            )

        room = BattleRoomState(
            room_id=rid,
            current_day=1,
            phase=RoomPhase.LOBBY_CLAIMING,
            min_human_to_start=min_human_to_start,
            human_count=0,
            ai_hosted_count=len(slots),
            role_slots=slots,
            created_at=now_str,
        )
        self._rooms[rid] = room
        self._runtime_char_states[rid] = char_states
        return room

    def get_room(self, room_id: str) -> BattleRoomState:
        if room_id not in self._rooms:
            raise ValueError(f"房间 {room_id} 不存在。")
        return self._rooms[room_id]

    def claim_role_in_lobby(self, req: ClaimRoleRequest) -> ClaimRoleResponse:
        """阶段 1：开局前在大厅认领角色（主帅/谋士/小人物均可）"""
        room = self.get_room(req.room_id)
        if room.phase != RoomPhase.LOBBY_CLAIMING:
            return ClaimRoleResponse(
                succeeded=False,
                message="房间已开局，请改用「中途空降接管 (drop_in_takeover)」接口加入战局。",
                control_mode=ControlMode.AI_HOSTED,
                character_id=req.character_id,
                character_name="",
                butterfly_multiplier=1.0,
                allocated_butterfly_budget=0,
            )

        slot = room.role_slots.get(req.character_id)
        if not slot:
            raise ValueError(f"角色 {req.character_id} 不存在。")

        if slot.control_mode != ControlMode.AI_HOSTED:
            return ClaimRoleResponse(
                succeeded=False,
                message=f"角色「{slot.name}」已被玩家 {slot.owner_player_name} 认领，请选择其他角色或高倍小人物。",
                control_mode=slot.control_mode,
                character_id=slot.character_id,
                character_name=slot.name,
                butterfly_multiplier=slot.butterfly_multiplier,
                allocated_butterfly_budget=0,
            )

        slot.control_mode = ControlMode.HUMAN_CLAIMED
        slot.owner_player_id = req.player_id
        slot.owner_player_name = req.player_name
        slot.joined_at_day = 1
        self._recount_room_occupancy(room)

        cstate = self._runtime_char_states[req.room_id][req.character_id]
        return ClaimRoleResponse(
            succeeded=True,
            message=f"成功认领「{slot.name}」（{slot.butterfly_multiplier:.2f}x 冷门倍率，高光期 Day {slot.active_window_days}）！",
            control_mode=slot.control_mode,
            character_id=slot.character_id,
            character_name=slot.name,
            butterfly_multiplier=slot.butterfly_multiplier,
            allocated_butterfly_budget=slot.remaining_butterfly_budget,
            inherited_psych_slots=cstate.psych_slots,
            active_locked_facts=room.locked_facts,
        )

    def start_room_with_ai_autofill(self, room_id: str) -> Tuple[bool, str, BattleRoomState]:
        """
        阶段 2：混合开局校验（无需凑满 12 人，只要达到最小真人数且双阵营均有真人或小人物即可开局，其余全由 AI 托管）
        """
        room = self.get_room(room_id)
        if room.human_count < room.min_human_to_start:
            return (
                False,
                f"当前真人玩家数 ({room.human_count}) 未达最低开局门槛 ({room.min_human_to_start})。",
                room,
            )

        room.phase = RoomPhase.IN_PROGRESS_DAY
        self._refresh_drop_in_recommendations(room)
        msg = (
            f"房间 {room_id} 已正式开局（Day {room.current_day}）："
            f"{room.human_count} 名角色由真人操控，其余 {room.ai_hosted_count} 名角色已由 RoleAgentRunner 自动托管补位！"
        )
        return True, msg, room

    def drop_in_takeover(self, req: ClaimRoleRequest) -> ClaimRoleResponse:
        """
        阶段 3：中途空降接管（Mid-Game Drop-In Takeover）
        允许新玩家在 Day 2 ~ Day 5 中途接管当前仍由 AI 托管且存活的角色，按剩余天数折算蝴蝶点数。
        """
        room = self.get_room(req.room_id)
        if room.phase != RoomPhase.IN_PROGRESS_DAY:
            return ClaimRoleResponse(
                succeeded=False,
                message=f"当前房间阶段为 {room.phase.value}，仅在日间操作期开放中途空降接管。",
                control_mode=ControlMode.AI_HOSTED,
                character_id=req.character_id,
                character_name="",
                butterfly_multiplier=1.0,
                allocated_butterfly_budget=0,
            )

        slot = room.role_slots.get(req.character_id)
        if not slot:
            raise ValueError(f"角色 {req.character_id} 不存在。")

        if slot.control_mode != ControlMode.AI_HOSTED:
            return ClaimRoleResponse(
                succeeded=False,
                message=f"角色「{slot.name}」当前正由真人玩家 {slot.owner_player_name} 操控，不可强行夺取。",
                control_mode=slot.control_mode,
                character_id=slot.character_id,
                character_name=slot.name,
                butterfly_multiplier=slot.butterfly_multiplier,
                allocated_butterfly_budget=0,
            )

        # 防作弊与状态继承检查：若该角色在全局不可逆事实中已阵亡（如蔡瑁已被斩），禁止空降接管死人
        if (
            req.character_id == "char_caimao"
            and "FACT_CAIMAO_EXECUTED" in room.locked_facts
        ):
            return ClaimRoleResponse(
                succeeded=False,
                message="因果锚点拦截：蔡瑁已在主线中被立斩（FACT_CAIMAO_EXECUTED），无法空降接管已阵亡角色！",
                control_mode=ControlMode.AI_HOSTED,
                character_id=slot.character_id,
                character_name=slot.name,
                butterfly_multiplier=slot.butterfly_multiplier,
                allocated_butterfly_budget=0,
                active_locked_facts=room.locked_facts,
            )

        # 按剩余天数比例折算蝴蝶点数预算（保底 2 点）
        _, _, full_budget = CHIBI_SLOT_METADATA.get(req.character_id, (1.2, [1, 2], 5))
        remaining_days_ratio = (room.max_days - room.current_day + 1) / float(room.max_days)
        prorated_budget = max(2, int(round(full_budget * remaining_days_ratio)))

        slot.control_mode = ControlMode.HUMAN_DROP_IN
        slot.owner_player_id = req.player_id
        slot.owner_player_name = req.player_name
        slot.joined_at_day = room.current_day
        slot.remaining_butterfly_budget = prorated_budget
        slot.consecutive_afk_days = 0
        self._recount_room_occupancy(room)

        cstate = self._runtime_char_states[req.room_id][req.character_id]
        window_tip = (
            "（正值该角色高光时间窗！）"
            if room.current_day in slot.active_window_days
            else ""
        )
        return ClaimRoleResponse(
            succeeded=True,
            message=(
                f"【中途空降成功】玩家 {req.player_name} 在 Day {room.current_day} 接管原 AI 托管角色「{slot.name}」"
                f"{window_tip}，无缝继承当前心理状态槽，获批剩余蝴蝶点数预算 {prorated_budget} 点！"
            ),
            control_mode=slot.control_mode,
            character_id=slot.character_id,
            character_name=slot.name,
            butterfly_multiplier=slot.butterfly_multiplier,
            allocated_butterfly_budget=prorated_budget,
            inherited_psych_slots=cstate.psych_slots,
            active_locked_facts=room.locked_facts,
        )

    def mark_player_submitted_today(
        self, room_id: str, character_id: str, butterfly_cost: int = 1
    ) -> None:
        """记录真人玩家已完成今日暗棋交卷"""
        room = self.get_room(room_id)
        slot = room.role_slots[character_id]
        slot.submitted_today = True
        slot.consecutive_afk_days = 0
        slot.remaining_butterfly_budget = max(
            0, slot.remaining_butterfly_budget - butterfly_cost
        )

    def lock_nightly_and_resolve_afk(self, room_id: str) -> NightlyDispatchPlan:
        """
        阶段 4：夜间锁单与真人超时 AFK 自动代管结算
        - 已交卷真人角色 -> 走玩家微扰指令
        - 未交卷真人角色 -> 当晚自动降级由 AI 按底色代管（不卡房间），且累加 `consecutive_afk_days`；
          若连续 2 天未交卷，自动释放回 `AI_HOSTED` 供其他活跃玩家空降接管！
        """
        room = self.get_room(room_id)
        human_active: List[str] = []
        ai_hosted: List[str] = []
        afk_fallback: List[str] = []
        released_slots: List[str] = []

        for cid, slot in room.role_slots.items():
            if slot.control_mode == ControlMode.AI_HOSTED:
                ai_hosted.append(cid)
            else:
                if slot.submitted_today:
                    human_active.append(cid)
                    slot.submitted_today = False
                else:
                    # 真人超时未交卷：当晚无缝转 AI 代管，不阻塞夜间队列
                    afk_fallback.append(cid)
                    slot.consecutive_afk_days += 1
                    if slot.consecutive_afk_days >= self.MAX_CONSECUTIVE_AFK_DAYS:
                        slot.control_mode = ControlMode.AI_HOSTED
                        slot.owner_player_id = None
                        slot.owner_player_name = None
                        released_slots.append(cid)

        # 推进至下一天并刷新高光时间窗推荐标记
        room.current_day = min(room.max_days, room.current_day + 1)
        self._recount_room_occupancy(room)
        self._refresh_drop_in_recommendations(room)

        return NightlyDispatchPlan(
            room_id=room.room_id,
            day=room.current_day - 1,
            human_active_actors=human_active,
            ai_hosted_actors=ai_hosted,
            afk_fallback_actors=afk_fallback,
            released_afk_slots=released_slots,
        )

    def _recount_room_occupancy(self, room: BattleRoomState) -> None:
        humans = sum(
            1
            for s in room.role_slots.values()
            if s.control_mode in (ControlMode.HUMAN_CLAIMED, ControlMode.HUMAN_DROP_IN)
        )
        room.human_count = humans
        room.ai_hosted_count = len(room.role_slots) - humans

    def _refresh_drop_in_recommendations(self, room: BattleRoomState) -> None:
        for slot in room.role_slots.values():
            slot.is_recommended_drop_in = (
                slot.control_mode == ControlMode.AI_HOSTED
                and (room.current_day in slot.active_window_days)
            )


# ---------------------------------------------------------------------------
# 自包含验证演示（直接运行 python3 room_matchmaker.py 测试混合开局、空降接管与 AFK 释放）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mm = HybridRoomMatchmaker()
    room_id = "room_chibi_01"

    print("=========================================================================")
    print("  《千古一局：暗合推演》多人认领 + AI 自动托管 + 中途空降接管验证")
    print("=========================================================================\n")

    print("--- [阶段 1] 开局大厅：仅 2 名真人认领主帅（曹操 & 周瑜），其余 10 角由 AI 托管 ---")
    r1 = mm.claim_role_in_lobby(
        ClaimRoleRequest(
            room_id=room_id,
            player_id="p_wei_01",
            player_name="孟德新书",
            character_id="char_caocao",
        )
    )
    r2 = mm.claim_role_in_lobby(
        ClaimRoleRequest(
            room_id=room_id,
            player_id="p_wu_01",
            player_name="曲有误周郎顾",
            character_id="char_zhouyu",
        )
    )
    print(f"  * {r1.message}")
    print(f"  * {r2.message}")
    ok, start_msg, room_state = mm.start_room_with_ai_autofill(room_id)
    print(f"  * [混合开局状态]: {start_msg}\n")

    print("--- [阶段 2] Day 1 夜间结算：周瑜玩家交卷，曹操玩家超时未交卷 (AFK) -> AI 自动代管不卡房 ---")
    mm.mark_player_submitted_today(room_id, "char_zhouyu", butterfly_cost=2)
    plan_d1 = mm.lock_nightly_and_resolve_afk(room_id)
    print(f"  * Day 1 真人交卷驱动: {plan_d1.human_active_actors}")
    print(f"  * Day 1 超时转 AI 临时代管: {plan_d1.afk_fallback_actors} (房间零阻塞，推进至 Day {room_state.current_day})\n")

    print("--- [阶段 3] Day 2 中途空降接管：新玩家检索正值高光时间窗的小人物「乌林粮草官」空降入局 ---")
    recommended = [
        f"{s.name}({s.butterfly_multiplier:.2f}x)"
        for s in room_state.role_slots.values()
        if s.is_recommended_drop_in
    ]
    print(f"  * Day 2 系统推荐高光可空降角色池: {recommended}")
    drop_res = mm.drop_in_takeover(
        ClaimRoleRequest(
            room_id=room_id,
            player_id="p_minor_dropin",
            player_name="江左梅郎",
            character_id="char_wulin_granary",
        )
    )
    print(f"  * {drop_res.message}")
    print(
        f"  * 当前房间人员构成: 真人操控 {room_state.human_count} 人 / AI 托管 {room_state.ai_hosted_count} 人\n"
    )

    print("--- [阶段 4] Day 2 夜间结算：曹操玩家连续第 2 天 AFK 旷工 -> 自动释放回可空降池 ---")
    mm.mark_player_submitted_today(room_id, "char_zhouyu", butterfly_cost=2)
    mm.mark_player_submitted_today(room_id, "char_wulin_granary", butterfly_cost=1)
    plan_d2 = mm.lock_nightly_and_resolve_afk(room_id)
    print(f"  * Day 2 真人交卷驱动: {plan_d2.human_active_actors}")
    print(f"  * 因连续 2 天旷工被释放回 AI 托管/可空降池的角色: {plan_d2.released_afk_slots}")