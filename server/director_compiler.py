"""
《千古一局：暗合推演》阶段二 T2.2：2.5D 沙盘动作原语编译器 (director_compiler.py - v1.2 含反事实裁决契约)

核心职责：
1. 严格执行“防穿帮协议”：将多轮 AI 博弈输出与状态机 ANCHOR_LOCK 因果锚点，编译为客户端 PixiJS 唯一支持的 5 类标准动作原语（MOVE / INTERACT / EMOTE / VFX / ANCHOR_LOCK）。
2. 枚举白名单强校验与非法指令自动降级（Fallback）：若大模型输出了未注册的地标、越界的站位槽（Slot）或不存在的 Spine 动作/立绘表情，编译器自动修正为安全的默认枚举，保证前端 100% 零穿帮、零崩溃。
3. 自动按 24 小时时间戳排序，并挂载夜间反事实死局验算报告（counterfactual_audit），生成符合 CDN 分发契约的 ReplayTimeline JSON。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 客户端 2.5D 沙盘烘焙资源字典（唯一合法枚举源）
# ---------------------------------------------------------------------------
VALID_ZONES: Dict[str, int] = {
    "caoying_tent": 4,      # 曹营中军大帐（4个站位槽：0主位, 1-2谋士位, 3传令位）
    "caoying_port": 4,      # 曹营北岸水寨（4个站位槽）
    "wulin_depot": 3,       # 乌林粮草坞（3个站位槽）
    "jiangdong_tent": 4,    # 江东都督府（4个站位槽）
    "jiangdong_pier": 4,    # 江东南岸船坞（4个站位槽）
    "yangtze_river": 4,     # 赤壁江面中央（4个水战船位槽）
}

VALID_TRAVEL_MODES: Set[str] = {"WALK", "HORSE", "BOAT"}
VALID_SPINE_ANIMS: Set[str] = {"WHISPER", "DEBATE", "HANDOVER_LETTER", "DRAW_SWORD", "EXECUTE"}
VALID_PORTRAIT_MOODS: Set[str] = {"CALM", "SUSPICIOUS", "FURIOUS", "SMIRK", "ANXIOUS"}
VALID_VFX_EFFECTS: Set[str] = {"HEAVY_FOG", "FIRE_SPREAD", "WAR_DRUMS", "RAIN"}


class TimelineEventItem(BaseModel):
    """单条客户端可渲染的时间轴动作原语"""
    t: str = Field(..., description="24小时制时间戳，如 '21:30'")
    cmd: str = Field(..., description="MOVE | INTERACT | EMOTE | VFX | ANCHOR_LOCK")
    zone_id: Optional[str] = None
    actor_id: Optional[str] = None
    actor_a: Optional[str] = None
    actor_b: Optional[str] = None
    to_zone: Optional[str] = None
    slot_idx: Optional[int] = None
    travel_mode: Optional[str] = None
    anim: Optional[str] = None
    portrait_mood: Optional[str] = None
    line_text: Optional[str] = None
    effect: Optional[str] = None
    fact_id: Optional[str] = None
    summary: Optional[str] = None


class ReplayTimelineContract(BaseModel):
    """前后端联调契约：24h 演出时间轴完整数据包（含反事实死局验算报告）"""
    schema_version: str = "1.2.0"
    battle_id: str = "chibi_208ad"
    day: int
    active_zones: List[str]
    locked_facts: List[str]
    counterfactual_audit: Optional[Dict[str, Any]] = None
    character_snapshots: Dict[str, Dict[str, Any]]
    timeline_events: List[TimelineEventItem]


class DirectorCompiler:
    """导演原语编译器：清洗 AI 原始动作并注入确定性状态机锚点"""

    def __init__(self, day: int = 2) -> None:
        self.day = day
        self._events: List[TimelineEventItem] = []

    def add_vfx(self, t: str, zone_id: str, effect: str) -> None:
        safe_zone = zone_id if zone_id in VALID_ZONES else "yangtze_river"
        safe_effect = effect if effect in VALID_VFX_EFFECTS else "HEAVY_FOG"
        self._events.append(
            TimelineEventItem(t=t, cmd="VFX", zone_id=safe_zone, effect=safe_effect)
        )

    def add_move(
        self, t: str, actor_id: str, to_zone: str, slot_idx: int, travel_mode: str = "WALK"
    ) -> None:
        safe_zone = to_zone if to_zone in VALID_ZONES else "caoying_tent"
        max_slots = VALID_ZONES[safe_zone]
        # 强制将槽位索引裁剪在 [0, max_slots - 1] 内，杜绝角色站到地图墙外
        safe_slot = max(0, min(slot_idx, max_slots - 1))
        safe_mode = travel_mode if travel_mode in VALID_TRAVEL_MODES else "WALK"

        self._events.append(
            TimelineEventItem(
                t=t,
                cmd="MOVE",
                actor_id=actor_id,
                to_zone=safe_zone,
                slot_idx=safe_slot,
                travel_mode=safe_mode,
            )
        )

    def add_interact(
        self, t: str, zone_id: str, actor_a: str, actor_b: str, anim: str
    ) -> None:
        safe_zone = zone_id if zone_id in VALID_ZONES else "caoying_tent"
        safe_anim = anim if anim in VALID_SPINE_ANIMS else "DEBATE"
        self._events.append(
            TimelineEventItem(
                t=t,
                cmd="INTERACT",
                zone_id=safe_zone,
                actor_a=actor_a,
                actor_b=actor_b,
                anim=safe_anim,
            )
        )

    def add_emote(
        self, t: str, zone_id: str, actor_id: str, portrait_mood: str, line_text: str
    ) -> None:
        safe_zone = zone_id if zone_id in VALID_ZONES else "caoying_tent"
        safe_mood = portrait_mood if portrait_mood in VALID_PORTRAIT_MOODS else "CALM"
        # 台词气泡长度安全裁剪，避免撑爆前端 UI
        safe_line = line_text.strip()[:60]
        self._events.append(
            TimelineEventItem(
                t=t,
                cmd="EMOTE",
                zone_id=safe_zone,
                actor_id=actor_id,
                portrait_mood=safe_mood,
                line_text=safe_line,
            )
        )

    def add_anchor_lock(
        self, t: str, zone_id: str, fact_id: str, summary: str
    ) -> None:
        safe_zone = zone_id if zone_id in VALID_ZONES else "caoying_tent"
        self._events.append(
            TimelineEventItem(
                t=t,
                cmd="ANCHOR_LOCK",
                zone_id=safe_zone,
                fact_id=fact_id,
                summary=summary,
            )
        )

    def compile_contract(
        self,
        active_zones: List[str],
        locked_facts: List[str],
        character_snapshots: Dict[str, Dict[str, Any]],
        counterfactual_audit: Optional[Dict[str, Any]] = None,
    ) -> ReplayTimelineContract:
        """按时间戳排序并输出最终前端契约数据包"""
        sorted_events = sorted(self._events, key=lambda item: item.t)
        return ReplayTimelineContract(
            day=self.day,
            active_zones=active_zones,
            locked_facts=locked_facts,
            counterfactual_audit=counterfactual_audit,
            character_snapshots=character_snapshots,
            timeline_events=sorted_events,
        )
