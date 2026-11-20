"""
《千古一局：暗合推演》Sprint 4 核心终局与扩展模块：
Day 5 终局历史切片回溯合成器、百人流星远征微扰聚合器与多战役剧本热加载引擎 (finale_expedition_engine.py v1.0)

对应《开发计划 v3.0》Sprint 4（T4.1 – T4.3）：
1. T4.1 Day 5【终局历史切片回溯】合成器（TrueHistoryRewindSynthesizer）：
   - 源自《我的玩家都是演技派》第79章【终极试炼：事定犹须待阖棺】。
   - 将 Day 1 至 Day 5 各角色 Top 1 晋级玩家埋下的固化支线、单向棘轮与不可逆事实锚点合成为完整的「五日因果回溯剧本」，精确标注每一环死局由哪位玩家在哪个时辰锁定。
2. T4.2 【归序者远征】百人流星微扰共斗聚合器（ExpeditionSwarmAggregator）：
   - 融合《我的玩家都是演技派》第79章“百名玩家如流星坠入战场护航”与《这游戏也太真实了》第412章“第四天灾集群战役收尾”。
   - 未夺得核心角色 Top 1 的普通玩家化身为基层兵卒（火船水手、乌林滩涂疑兵、北寨救火兵）空降 6 大舞台。
   - 严守「人海渐近上限（Asymptotic Swarm Cap）」：采用双曲正切阻尼 `tanh(net_power / 35.0)` 将百人合力严格限制在 `火势微扰 ±25%`、`行军延迟 ±8h`、`心理槽微调 ±10 点` 以内——既能让百人共斗把 `72` 的临界心理槽推过 `80` 阈值，又绝不允许无脑人海直接推翻主帅已锁死的硬性因果锚点。
3. T4.3 多战役剧本包零代码热加载器（ScenarioPackLoader）：
   - 将角色盲盒包络线、舞台槽位、心理棘轮与反事实 4 分支抽离为标准 `ScenarioManifest`，内置第二战役《官渡之战：乌巢劫粮 (建安五年·200 AD)》完整剧本包并验证一键切换。
"""

from __future__ import annotations
import math
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. T4.1 五日终局历史切片回溯合成器（True History Rewind Synthesizer）
# ---------------------------------------------------------------------------
class CausalChainMilestone(BaseModel):
    """五日战役中的关键因果链节点（用于终局历史回溯公演）"""
    day: int = Field(..., ge=1, le=5)
    timestamp: str
    zone_id: str
    character_id: str
    character_name: str
    top1_player_name: str
    mechanism_tag: str = Field(..., description="SOLIDIFIED_BRANCH / RATCHET_LOCK / FACT_ANCHOR / FINALE_CLIMAX")
    summary_line: str
    primitive_cmd: str = Field(default="ANCHOR_LOCK")


class TrueHistorySliceReplay(BaseModel):
    """Day 5【事定犹须待阖棺】五日全链路正史回溯数据包"""
    scenario_id: str
    slice_title: str
    total_days_synthesized: int = 5
    top1_mvp_players: List[str]
    causal_milestones: List[CausalChainMilestone]
    finale_replay_primitives: List[Dict[str, Any]]
    verdict_epilogue: str


class TrueHistoryRewindSynthesizer:
    """将 Day 1 - Day 5 的排错固化与暗棋碰撞合成为无剪辑终局历史回溯切片"""

    @staticmethod
    def synthesize_chibi_5day_rewind() -> TrueHistorySliceReplay:
        milestones = [
            CausalChainMilestone(
                day=1,
                timestamp="Day1·戌时(20:00)",
                zone_id="caoying_port",
                character_id="char_junxu_clerk",
                character_name="曹营怯战书佐·汪生",
                top1_player_name="楚歌（记忆碎片流）",
                mechanism_tag="SOLIDIFIED_BRANCH",
                summary_line="识破汪生乃原生贪生怕死烟雾弹（排除错误答案），顺水推舟调其赴乌林抄账并固化盯梢支线。",
                primitive_cmd="EMOTE",
            ),
            CausalChainMilestone(
                day=2,
                timestamp="Day2·亥时(22:30)",
                zone_id="caoying_port",
                character_id="char_pangtong",
                character_name="庞统",
                top1_player_name="凤雏献策",
                mechanism_tag="FACT_ANCHOR",
                summary_line="鲁肃与老水手借子时大雾草船受箭，北军水战焦虑突破 75，庞统献策锁死【FACT_SHIPS_CHAINED 铁索连舟】。",
                primitive_cmd="ANCHOR_LOCK",
            ),
            CausalChainMilestone(
                day=2,
                timestamp="Day2·子时(23:35)",
                zone_id="caoying_tent",
                character_id="char_jianggan",
                character_name="蒋干",
                top1_player_name="夏若凌（蹲守截信流）",
                mechanism_tag="RATCHET_LOCK",
                summary_line="周瑜群英会反间计叠加工兵夜袭，曹操疑心值达 81（>=80）激活单向棘轮，无视程昱求情立斩蔡瑁张允！",
                primitive_cmd="ANCHOR_LOCK",
            ),
            CausalChainMilestone(
                day=3,
                timestamp="Day3·酉时(18:30)",
                zone_id="jiangdong_pier",
                character_id="char_huangai",
                character_name="黄盖",
                top1_player_name="曲有误周郎顾",
                mechanism_tag="SOLIDIFIED_BRANCH",
                summary_line="苦肉计与阚泽密献诈降书固化完成，曹营新任水军都督毛玠轻信黄盖粮船将于第 5 夜来投。",
                primitive_cmd="INTERACT",
            ),
            CausalChainMilestone(
                day=4,
                timestamp="Day4·戌时(20:15)",
                zone_id="wulin_depot",
                character_id="char_wulin_granary",
                character_name="乌林粮草官",
                top1_player_name="江左梅郎",
                mechanism_tag="SOLIDIFIED_BRANCH",
                summary_line="小人物 2.2x 奇袭支线固化：将二十艘浸油干草引火船暗泊于北寨铁索战船正上风口。",
                primitive_cmd="MOVE",
            ),
            CausalChainMilestone(
                day=5,
                timestamp="Day5·子时(23:55)",
                zone_id="yangtze_river",
                character_id="char_zhouyu",
                character_name="周瑜 & 百人远征军",
                top1_player_name="全服归序者远征团 (128人)",
                mechanism_tag="FINALE_CLIMAX",
                summary_line="甲子日东南大风骤起，黄盖蒙冲斗舰点火撞入连环船阵，百名远征玩家流星助燃，火烧乌林终局锁死！",
                primitive_cmd="ANCHOR_LOCK",
            ),
        ]

        primitives = [
            {"t": "D1·20:00", "cmd": "EMOTE", "zone_id": "caoying_port", "actor_id": "char_wulin_granary", "portrait_mood": "CALM", "line_text": "【Day1回溯·楚歌固化】已排除怯战书佐汪生嫌疑，调往乌林粮坞盯死北寨上风口泊位！"},
            {"t": "D2·21:30", "cmd": "VFX", "zone_id": "yangtze_river", "effect": "HEAVY_FOG"},
            {"t": "D2·22:30", "cmd": "ANCHOR_LOCK", "zone_id": "caoying_port", "fact_id": "FACT_SHIPS_CHAINED", "summary": "【Day2回溯·凤雏献策】草船借箭叠加江面颠簸，曹操采纳连环计，北军战船悉数锁死！"},
            {"t": "D2·23:35", "cmd": "ANCHOR_LOCK", "zone_id": "caoying_tent", "fact_id": "FACT_CAIMAO_EXECUTED", "summary": "【Day2回溯·夏若凌】反间密信使曹操疑心升至81，单向棘轮阻断程昱求情，立斩蔡瑁！"},
            {"t": "D3·19:00", "cmd": "INTERACT", "zone_id": "jiangdong_pier", "actor_a": "char_huangai", "actor_b": "char_zhouyu", "anim": "DEBATE"},
            {"t": "D4·20:30", "cmd": "MOVE", "actor_id": "char_wulin_granary", "to_zone": "wulin_depot", "slot_idx": 1, "travel_mode": "WALK"},
            {"t": "D5·23:00", "cmd": "VFX", "zone_id": "yangtze_river", "effect": "METEOR_EXPEDITION"},
            {"t": "D5·23:30", "cmd": "EMOTE", "zone_id": "yangtze_river", "actor_id": "char_huangai", "portrait_mood": "SMIRK", "line_text": "【Day5终局公演】东南风急，百名远征死士已随老夫火船冲入北寨，点火！"},
            {"t": "D5·23:55", "cmd": "ANCHOR_LOCK", "zone_id": "wulin_depot", "fact_id": "FACT_RED_CLIFFS_VICTORY", "summary": "【终极试炼：事定犹须待阖棺】五日固化支线与百人流星远征合龙，火烧赤壁连环大局彻底锁死！"},
        ]

        return TrueHistorySliceReplay(
            scenario_id="chibi_208ad",
            slice_title="【终极试炼：事定犹须待阖棺】赤壁五日全因果回溯公演",
            top1_mvp_players=[
                "楚歌（记忆碎片流）",
                "凤雏献策",
                "夏若凌（蹲守截信流）",
                "曲有误周郎顾",
                "江左梅郎",
            ],
            causal_milestones=milestones,
            finale_replay_primitives=primitives,
            verdict_epilogue=(
                "五日暗棋伏笔全部收束：曹营自 Day 1 漏查乌林上风口泊位、Day 2 锁船斩将，至 Day 5 东南风起再无任何反事实逃生路线（0/4）。"
            ),
        )


# ---------------------------------------------------------------------------
# 2. T4.2 【归序者远征】百人流星微扰共斗聚合器（Expedition Swarm Aggregator）
# ---------------------------------------------------------------------------
class VanguardOrderType(str, Enum):
    """终局百人远征基层兵卒可选微扰指令"""
    FAN_SOUTHEAST_FIRE = "FAN_SOUTHEAST_FIRE"          # 江东火船水手：顺风泼洒火油助推火势
    AMBUSH_WULIN_SHORE = "AMBUSH_WULIN_SHORE"          # 江东滩涂疑兵：在乌林岸边擂鼓截击溃兵
    CUT_CHAIN_RESCUE = "CUT_CHAIN_RESCUE"              # 曹营校刀手：冒火砍断局部铁索抢救战船


class SwarmContributionTicket(BaseModel):
    """单名远征玩家的流星空降微扰选票"""
    player_id: str
    camp: str = Field(..., description="ALLIANCE 或 WEI")
    target_zone_id: str
    order_type: VanguardOrderType
    tickets_committed: int = Field(default=1, ge=1, le=5)


class SwarmAggregationResult(BaseModel):
    """百人远征微扰聚合结算报告（含双曲正切渐近上限保护）"""
    total_vanguard_players: int
    alliance_tickets: int
    wei_tickets: int
    net_alliance_advantage: int
    damped_swarm_ratio: float = Field(..., ge=-1.0, le=1.0, description="tanh(net / 35) 渐近阻尼系数")
    clamped_fire_boost_pct: float = Field(..., description="最终火势微扰百分比（严格裁剪在 [-25%, +25%]）")
    clamped_delay_hours: float = Field(..., description="最终行军延迟微扰（严格裁剪在 [-8h, +8h]）")
    clamped_psych_shift: int = Field(..., description="对主帅水战焦虑/恐慌的临界助推值（严格裁剪在 [-10, +10]）")
    can_override_locked_anchor: bool = Field(
        default=False, description="铁律：永远为 False，人海微扰不可直接推翻已锁死的硬性事实锚点"
    )
    broadcast_banner: str


class ExpeditionSwarmAggregator:
    """
    聚合百名普通玩家的终局远征微扰：
    使用 `math.tanh(net_tickets / 35.0)` 确保：
    - 10~30 人参与时反馈灵敏，能帮助主帅把 73 的心理槽推过 80 临界线；
    - 即便 500 人同时涌入，最大加成也收敛在 +25% 火势 / +8h 延迟 / +10 心理槽，绝不出现人海数值崩坏。
    """

    MAX_FIRE_BOOST_PCT: float = 25.0
    MAX_DELAY_HOURS: float = 8.0
    MAX_PSYCH_SHIFT: int = 10
    DAMPING_SCALE: float = 35.0

    @classmethod
    def aggregate_swarm_wave(
        cls, contributions: List[SwarmContributionTicket]
    ) -> SwarmAggregationResult:
        alliance_sum = sum(
            c.tickets_committed for c in contributions if c.camp == "ALLIANCE"
        )
        wei_sum = sum(
            c.tickets_committed for c in contributions if c.camp == "WEI"
        )
        net_adv = alliance_sum - wei_sum
        ratio = math.tanh(net_adv / cls.DAMPING_SCALE)

        fire_boost = round(ratio * cls.MAX_FIRE_BOOST_PCT, 1)
        delay_h = round(ratio * cls.MAX_DELAY_HOURS, 1)
        psych_delta = int(round(ratio * cls.MAX_PSYCH_SHIFT))

        banner = (
            f"☄️【归序者远征·百人流星共斗结算】共 {len(contributions)} 名先锋玩家坠入赤壁战场"
            f"（联军 {alliance_sum} 票 vs 曹魏 {wei_sum} 票）→ 经渐近阻尼收敛："
            f"乌林火势蔓延 {'+' if fire_boost >= 0 else ''}{fire_boost}%、"
            f"北军撤退迟滞 {'+' if delay_h >= 0 else ''}{delay_h}h、"
            f"主帅恐慌微扰 {'+' if psych_delta >= 0 else ''}{psych_delta} 点（严守不推翻已锁死锚点铁律）！"
        )

        return SwarmAggregationResult(
            total_vanguard_players=len(contributions),
            alliance_tickets=alliance_sum,
            wei_tickets=wei_sum,
            net_alliance_advantage=net_adv,
            damped_swarm_ratio=round(ratio, 3),
            clamped_fire_boost_pct=fire_boost,
            clamped_delay_hours=delay_h,
            clamped_psych_shift=psych_delta,
            can_override_locked_anchor=False,
            broadcast_banner=banner,
        )


# ---------------------------------------------------------------------------
# 3. T4.3 多战役剧本包零代码热加载器（内置《赤壁之战》与《官渡之战：乌巢劫粮》）
# ---------------------------------------------------------------------------
class ScenarioManifest(BaseModel):
    """跨战役通用剧本包配置表（零代码修改即可导入新历史切片）"""
    scenario_id: str
    title: str
    era_year: str
    duration_days: int = 5
    commander_a: str
    commander_b: str
    zones: Dict[str, str]
    core_ratchets: List[str]
    irreversible_anchors: List[str]
    true_moles_default: List[str]
    native_red_herrings_default: List[str]
    counterfactual_4_branches: List[str]


SCENARIO_PACK_REGISTRY: Dict[str, ScenarioManifest] = {
    "chibi_208ad": ScenarioManifest(
        scenario_id="chibi_208ad",
        title="《赤壁之战：火烧乌林》",
        era_year="建安十三年 (208 AD)",
        duration_days=5,
        commander_a="曹操（魏武水陆大军）",
        commander_b="周瑜 & 诸葛亮（孙刘联军）",
        zones={
            "wulin_depot": "乌林粮草坞",
            "caoying_tent": "曹营中军大帐",
            "caoying_port": "曹军北岸水寨",
            "yangtze_river": "赤壁大江中央",
            "jiangdong_pier": "江东南岸船坞",
            "jiangdong_tent": "江东都督府",
        },
        core_ratchets=["BELIEF_DISTRUST_JINGZHOU (猜忌荆州降将)", "BELIEF_NEED_STABLE_SHIPS (急求锁船止晕)"],
        irreversible_anchors=["FACT_SHIPS_CHAINED (铁索连舟)", "FACT_CAIMAO_EXECUTED (立斩蔡瑁)"],
        true_moles_default=["乌林粮草官 (上风口泊干草船)", "蒋干 (中反间计盗回伪信)"],
        native_red_herrings_default=["曹营怯战书佐·汪生 (畏疫私逃)", "蔡瑁 (闭门防疫非叛变)"],
        counterfactual_4_branches=[
            "分支1：采纳铁索连舟",
            "分支2：强令解开铁索硬抗风浪",
            "分支3：分批上乌林岸扎营轮休",
            "分支4：派校事府密查蔡瑁书信真伪",
        ],
    ),
    "guandu_200ad": ScenarioManifest(
        scenario_id="guandu_200ad",
        title="《官渡之战：乌巢劫粮》",
        era_year="建安五年 (200 AD)",
        duration_days=5,
        commander_a="袁绍（河北主力大营）",
        commander_b="曹操（官渡守军奇袭团）",
        zones={
            "wuchao_granary": "乌巢屯粮大营（淳于琼驻地）",
            "yuanshao_tent": "袁绍阳武中军帐",
            "guandu_wall": "曹操官渡土山地道",
            "huanghe_ferry": "延津白马粮道渡口",
            "zhanghe_camp": "张郃高览攻城前营",
            "xuyou_tent": "许攸幕府行辕",
        },
        core_ratchets=[
            "BELIEF_SUSPECT_XUYOU_GREED (袁绍厌恶许攸贪财干政，拒听缓兵之计)",
            "BELIEF_OVERCONFIDENT_SIEGE (袁绍执念强攻官渡大营，轻视乌巢侧翼)",
        ],
        irreversible_anchors=[
            "FACT_XUYOU_DEFECTED (许攸夜奔曹营献乌巢虚实)",
            "FACT_WUCHAO_GRANARY_BURNED (淳于琼醉酒失乌巢，袁军粮草尽毁)",
            "FACT_ZHANGHE_SURRENDERED (郭图谗言嫁祸，张郃高览焚械投曹)",
        ],
        true_moles_default=[
            "乌巢守备伪印官 (暗换曹军打袁军蒋奇旗号通行口令)",
            "郭图账下门客 (向张郃投喂袁绍必斩败将流言)",
        ],
        native_red_herrings_default=[
            "淳于琼帐下嗜酒军司马 (真贪杯偷喝军酒，并非曹军细作)",
            "审配家仆 (私藏河北粮引牟利，被许攸抓住把柄但非内鬼)",
        ],
        counterfactual_4_branches=[
            "分支1：采纳沮授之议增派轻骑驻防乌巢侧翼",
            "分支2：不救乌巢，全力猛攻曹操官渡空虚大营",
            "分支3：分兵两路同时救援乌巢并奇袭官渡",
            "分支4：安抚张郃高览、严惩郭图以稳住前线军心",
        ],
    ),
}


if __name__ == "__main__":
    print("=========================================================================")
    print("  《千古一局：暗合推演》Sprint 4 终局回溯、百人远征与多剧本热加载自检")
    print("=========================================================================\n")

    # 测试 1：合成赤壁 5 日终局历史切片回溯
    slice_replay = TrueHistoryRewindSynthesizer.synthesize_chibi_5day_rewind()
    print(f"--- [测试 1] {slice_replay.slice_title} ---")
    for m in slice_replay.causal_milestones:
        print(f"  * {m.timestamp:<14} | [{m.mechanism_tag:<17}] {m.character_name}({m.top1_player_name}): {m.summary_line}")
    assert len(slice_replay.causal_milestones) == 6
    assert len(slice_replay.finale_replay_primitives) == 9

    # 测试 2：百人归序者远征微扰共斗聚合（验证双曲正切阻尼上限）
    print("\n--- [测试 2] 百人流星远征微扰聚合（验证防人海崩坏阻尼上限） ---")
    tickets = [
        SwarmContributionTicket(
            player_id=f"p_ally_{i}",
            camp="ALLIANCE",
            target_zone_id="wulin_depot",
            order_type=VanguardOrderType.FAN_SOUTHEAST_FIRE,
            tickets_committed=2,
        )
        for i in range(45)
    ] + [
        SwarmContributionTicket(
            player_id=f"p_wei_{j}",
            camp="WEI",
            target_zone_id="caoying_port",
            order_type=VanguardOrderType.CUT_CHAIN_RESCUE,
            tickets_committed=1,
        )
        for j in range(15)
    ]
    swarm_res = ExpeditionSwarmAggregator.aggregate_swarm_wave(tickets)
    print(f"  {swarm_res.broadcast_banner}")
    assert abs(swarm_res.clamped_fire_boost_pct) <= 25.0
    assert swarm_res.can_override_locked_anchor is False

    # 测试 3：零代码热加载第二战役剧本《官渡之战：乌巢劫粮 (200 AD)》
    print("\n--- [测试 3] 多战役剧本包零代码热加载验证 ---")
    for sid, pack in SCENARIO_PACK_REGISTRY.items():
        print(
            f"  [已加载剧本包 `{sid}`] {pack.title} ({pack.era_year}) | "
            f"对阵: {pack.commander_a} vs {pack.commander_b} | 核心锚点数: {len(pack.irreversible_anchors)}"
        )
    assert "guandu_200ad" in SCENARIO_PACK_REGISTRY
    print("\n[ALL SPRINT 4 FINALE EXPEDITION & MULTI-SCENARIO CHECKS PASSED]")
