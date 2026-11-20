"""
《千古一局：暗合推演》统一 FastAPI 服务端入口与 v3.0 演技排错/反事实死局联动天梯引擎 (app_server.py v1.3)

核心职责与 v1.3 升级记录：
1. 统一整合六大核心模块：
   - state_machine.py（量化心理槽、双步配合不可逆阈值、单向棘轮、小人物物理上限裁剪）
   - era_guard.py v1.2（15字结构化槽位、NFKC反混淆与谐音拆字拦截、化学配方共现拦截、史官转译器）
   - acting_solidifier.py v1.0（盲盒内鬼排错与原生烟雾弹NPC、记忆碎片接触解锁、OOC违和度三级惩罚、跨轮次支线固化、试炼幻境Top 1晋级终局切片）
   - daytime_sandbox.py（单场景 <=3 人极速试算、假想敌分支对比、每日免费配额与点卡抵扣）
   - counterfactual.py（夜间强推理“反事实死局验算器”：站在敌方主帅视角穷举 4 条自救路线）
   - nightly_bsp_job.py & director_compiler.py（夜间舞台按需唤醒、BSP 并发推演、Superstep 2.5 反事实裁决、动作原语编译）
2. v3.0 阻尼天梯结算公式（game-concept.md v3.0 第 7 章）：
   - 综合得分 = 基础胜率分 × 时代贴合度系数 × 扮演契合度系数(1 - OOC惩罚) × 点数阻尼效率 × 角色冷门杠杆倍率 × 死局闭环加成(1.20x) + 排错固化奖励分
"""

from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from state_machine import CharacterState, RatchetBelief, RoleTier
from era_guard import EraIronLawGuard, GuardVerdict
from acting_solidifier import (
    ActingSolidifierEngine,
    OOCInspectionResult,
    TrialIllusionRunScore,
)
from daytime_sandbox import (
    BranchComparisonResult,
    DailyQuotaLimiter,
    DaytimeSandboxEngine,
    SandboxPreviewRequest,
)
from counterfactual import CounterfactualDeadlockVerifier, DeadlockVerificationReport
from nightly_bsp_job import NightlyBSPPipeline
from timeline_fork_store import (
    TimelineForkRepository,
    TimelineForkRequest,
    TimelineForkResponse,
    CampaignBranchNode,
)
from room_matchmaker import (
    HybridRoomMatchmaker,
    BattleRoomState,
    ClaimRoleRequest,
    ClaimRoleResponse,
)


# ---------------------------------------------------------------------------
# 1. v3.0 反事实联动 + 演技固化版蝴蝶倍率天梯结算引擎
# ---------------------------------------------------------------------------
class PlayerRunSubmission(BaseModel):
    """玩家单局/单回合人设微调与盲盒排错参赛记录"""
    player_id: str
    player_name: str
    character_id: str
    character_name: str
    role_tier: RoleTier
    butterfly_multiplier: float = Field(
        ..., ge=1.0, le=3.0, description="收敛后的角色冷门杠杆倍率（主帅 1.0x，谋士 1.25-1.45x，小人物 1.8-2.4x）"
    )
    cross_match_wins: int = Field(..., ge=0, description="平行沙盒交叉对战胜利场次")
    cross_match_total: int = Field(..., ge=1, description="平行沙盒交叉对战总场次")
    butterfly_points_spent: int = Field(..., ge=1, le=10, description="消耗的蝴蝶点数（越少效率越高）")
    era_plausibility_factor: float = Field(default=1.0, ge=0.5, le=1.2, description="时代贴合度系数")
    ooc_suspicion: int = Field(default=5, ge=0, le=100, description="OOC 违和度警觉值 (0-100)")
    solidified_branches_count: int = Field(default=0, ge=0, description="成功完成身份纠正与支线固化的数量")
    ruled_out_red_herrings_count: int = Field(default=0, ge=0, description="成功排除的原生烟雾弹 NPC 数量")
    claimed_deadlock: bool = Field(default=False, description="是否触发了表面锚点并申请死局加成")
    counterfactual_verified: bool = Field(
        default=False, description="是否通过夜间 counterfactual.py 0生路强验算（唯一决定 1.20x 加成）"
    )


class LeaderboardEntry(BaseModel):
    """天梯榜排名条目（v3.0 校准版）"""
    rank: int
    role_rank: int = Field(default=1, description="同角色流派内排名")
    player_id: str
    player_name: str
    character_id: str
    character_name: str
    role_tier: str
    win_rate: float
    butterfly_points_spent: int
    cost_efficiency: float
    persona_fit_factor: float
    solidification_bonus_pts: int
    butterfly_multiplier: float
    counterfactual_verified: bool
    deadlock_bonus: float
    final_score: int = Field(description="v3.0 反事实+演技固化平衡天梯分")
    highlight_tag: str


class ButterflyLeaderboardEngine:
    """全服角色独立榜与传奇总榜结算器（含阻尼曲线 + OOC契合度 + 排错固化奖励 + 反事实校验）"""

    DEADLOCK_CLOSURE_BONUS: float = 1.20
    COST_DAMPING_SLOPE: float = 0.35
    COST_DAMPING_EXP: float = 0.75
    SOLIDIFY_BONUS_PER_BRANCH: int = 8
    RED_HERRING_BONUS_PER_NPC: int = 4

    def __init__(self) -> None:
        self._submissions: List[PlayerRunSubmission] = []

    def submit_run(self, sub: PlayerRunSubmission) -> None:
        self._submissions.append(sub)

    def update_deadlock_verification_for_all(self, is_verified_deadlock: bool) -> None:
        for sub in self._submissions:
            if sub.claimed_deadlock:
                sub.counterfactual_verified = is_verified_deadlock

    @classmethod
    def compute_damped_cost_efficiency(cls, points_spent: int) -> float:
        safe_spent = max(1, points_spent)
        return 1.0 / ((1.0 + cls.COST_DAMPING_SLOPE * (safe_spent - 1)) ** cls.COST_DAMPING_EXP)

    @classmethod
    def compute_persona_fit_factor(cls, ooc_suspicion: int) -> float:
        """
        扮演契合度系数 (1 - OOC 惩罚)：
        - OOC 0-15（高契合飙戏）：1.00
        - OOC 16-69（轻度违和）：线性衰减至 0.65
        - OOC 70-89（停职软禁棘轮）：衰减至 0.30
        - OOC >= 90（影侍瞬杀灭口）：0.00（本轮积分作废）
        """
        if ooc_suspicion >= 90:
            return 0.0
        if ooc_suspicion >= 70:
            return 0.30
        if ooc_suspicion <= 15:
            return 1.00
        return round(1.00 - ((ooc_suspicion - 15) / 54.0) * 0.35, 3)

    def compute_rankings(
        self, character_filter: Optional[str] = None
    ) -> List[LeaderboardEntry]:
        candidates = [
            s
            for s in self._submissions
            if character_filter is None or s.character_id == character_filter
        ]

        scored_items: List[LeaderboardEntry] = []
        for s in candidates:
            win_rate = s.cross_match_wins / float(s.cross_match_total)
            cost_eff = self.compute_damped_cost_efficiency(s.butterfly_points_spent)
            fit_factor = self.compute_persona_fit_factor(s.ooc_suspicion)

            granted_deadlock = s.claimed_deadlock and s.counterfactual_verified
            deadlock_bonus = self.DEADLOCK_CLOSURE_BONUS if granted_deadlock else 1.00

            solid_bonus = (
                s.solidified_branches_count * self.SOLIDIFY_BONUS_PER_BRANCH
                + s.ruled_out_red_herrings_count * self.RED_HERRING_BONUS_PER_NPC
            )
            if fit_factor == 0.0:
                solid_bonus = 0

            balanced_raw = (
                (win_rate * 100.0)
                * s.era_plausibility_factor
                * fit_factor
                * cost_eff
                * s.butterfly_multiplier
                * deadlock_bonus
                + solid_bonus
            )
            final_score = int(round(balanced_raw))

            if fit_factor == 0.0:
                tag = "💀 OOC>=90 影侍灭口 (点数作废)"
            elif granted_deadlock and s.role_tier == RoleTier.MINOR:
                tag = f"★ 真死局·小人物奇袭 ({s.butterfly_multiplier:.1f}x | 固化+{solid_bonus})"
            elif granted_deadlock:
                tag = f"⚓ 真死局·0生路认证 (1.2x | 固化+{solid_bonus})"
            elif s.claimed_deadlock and not s.counterfactual_verified:
                tag = "⚠ 伪死局·敌方存破局生路 (1.0x)"
            else:
                tag = "稳健潜伏推演流"

            scored_items.append(
                LeaderboardEntry(
                    rank=0,
                    role_rank=1,
                    player_id=s.player_id,
                    player_name=s.player_name,
                    character_id=s.character_id,
                    character_name=s.character_name,
                    role_tier=s.role_tier.value,
                    win_rate=round(win_rate, 3),
                    butterfly_points_spent=s.butterfly_points_spent,
                    cost_efficiency=round(cost_eff, 3),
                    persona_fit_factor=fit_factor,
                    solidification_bonus_pts=solid_bonus,
                    butterfly_multiplier=s.butterfly_multiplier,
                    counterfactual_verified=granted_deadlock,
                    deadlock_bonus=deadlock_bonus,
                    final_score=final_score,
                    highlight_tag=tag,
                )
            )

        scored_items.sort(key=lambda x: x.final_score, reverse=True)
        role_counters: Dict[str, int] = {}
        for idx, item in enumerate(scored_items, start=1):
            item.rank = idx
            rc = role_counters.get(item.character_id, 0) + 1
            role_counters[item.character_id] = rc
            item.role_rank = rc

        return scored_items


# ---------------------------------------------------------------------------
# 2. 统一应用门面（Application Facade）与 FastAPI 路由注册
# ---------------------------------------------------------------------------
class CounterfactualVerifyRequest(BaseModel):
    suspicion: int = Field(default=81, ge=0, le=100)
    impatience: int = Field(default=68, ge=0, le=100)
    naval_anxiety: int = Field(default=76, ge=0, le=100)
    active_conditions: List[str] = Field(
        default_factory=lambda: [
            "COND_UPPER_WIND_DRY_GRASS",
            "COND_SOUTHEAST_WIND_WINDOW",
            "COND_NIGHT_DRUMS_HARASS",
            "COND_SHORE_RAID_BLOCKED",
        ]
    )


class ActingEvaluateRequest(BaseModel):
    player_id: str = "player_main"
    character_id: str = "char_wulin_granary"
    action_text: str = "按军需规制分仓隔离并向程昱秘呈油草账册"
    current_round: int = 2
    base_ooc: int = 8


class UnlockFragmentRequest(BaseModel):
    player_id: str = "player_main"
    actor_character_id: str = "char_junxu_clerk"
    target_character_id: str = "char_jianggan"
    interaction_zone: str = "caoying_tent"


class TrialSubmitRequest(BaseModel):
    player_id: str = "player_main"
    player_name: str = "归序者·你"
    character_id: str = "char_wulin_granary"
    identity_points_spent: int = 1
    butterfly_points_spent: int = 1
    restoration_rate: float = 95.0
    persona_fit_score: float = 94.0
    clear_efficiency_score: float = 92.0
    red_herrings_ruled_out: int = 2
    branches_solidified: int = 1


class ChibiServerFacade:
    """统一管理三道防线、演技排错固化、日间试炼沙盒、夜间 BSP、反事实死局验算与天梯结算的服务端门面"""

    def __init__(self) -> None:
        self.era_guard = EraIronLawGuard()
        self.acting_engine = ActingSolidifierEngine()
        self.quota_limiter = DailyQuotaLimiter(free_daily_limit=3)
        self.sandbox_engine = DaytimeSandboxEngine(quota_limiter=self.quota_limiter)
        self.counterfactual_verifier = CounterfactualDeadlockVerifier()
        self.nightly_pipeline = NightlyBSPPipeline(day=2)
        self.leaderboard = ButterflyLeaderboardEngine()
        self.fork_repo = TimelineForkRepository()
        self.matchmaker = HybridRoomMatchmaker()
        self._latest_replay_cache: Optional[Dict[str, Any]] = None
        self._seed_initial_leaderboard()

    def _seed_initial_leaderboard(self) -> None:
        seed_runs = [
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
                ooc_suspicion=6,
                solidified_branches_count=1,
                ruled_out_red_herrings_count=1,
                claimed_deadlock=True,
                counterfactual_verified=True,
            ),
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
                ooc_suspicion=8,
                solidified_branches_count=1,
                ruled_out_red_herrings_count=1,
                claimed_deadlock=True,
                counterfactual_verified=True,
            ),
            PlayerRunSubmission(
                player_id="p_pangtong_01",
                player_name="凤雏献策",
                character_id="char_pangtong",
                character_name="庞统",
                role_tier=RoleTier.STRATEGIST,
                butterfly_multiplier=1.35,
                cross_match_wins=16,
                cross_match_total=20,
                butterfly_points_spent=2,
                era_plausibility_factor=1.10,
                ooc_suspicion=10,
                solidified_branches_count=1,
                ruled_out_red_herrings_count=0,
                claimed_deadlock=True,
                counterfactual_verified=True,
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
                ooc_suspicion=4,
                solidified_branches_count=1,
                ruled_out_red_herrings_count=1,
                claimed_deadlock=True,
                counterfactual_verified=True,
            ),
            PlayerRunSubmission(
                player_id="p_caocao_def",
                player_name="孟德新书",
                character_id="char_caocao",
                character_name="曹操",
                role_tier=RoleTier.COMMANDER,
                butterfly_multiplier=1.0,
                cross_match_wins=15,
                cross_match_total=20,
                butterfly_points_spent=2,
                era_plausibility_factor=1.05,
                ooc_suspicion=12,
                solidified_branches_count=0,
                ruled_out_red_herrings_count=0,
                claimed_deadlock=False,
                counterfactual_verified=False,
            ),
        ]
        for r in seed_runs:
            self.leaderboard.submit_run(r)

    def validate_command(self, raw_payload: Dict[str, Any]) -> GuardVerdict:
        return self.era_guard.inspect_and_sanitize(raw_payload)

    async def preview_sandbox(
        self, req: SandboxPreviewRequest
    ) -> BranchComparisonResult:
        return await self.sandbox_engine.preview_single_scene(req)

    def verify_counterfactual(
        self, req: CounterfactualVerifyRequest
    ) -> DeadlockVerificationReport:
        ratchets = set()
        if req.suspicion >= 70:
            ratchets.add(RatchetBelief.DISTRUST_JINGZHOU)
        if req.naval_anxiety >= 70:
            ratchets.add(RatchetBelief.NEED_STABLE_SHIPS)

        caocao = CharacterState(
            character_id="char_caocao",
            name="曹操",
            role_tier=RoleTier.COMMANDER,
            psych_slots={
                "suspicion": req.suspicion,
                "impatience": req.impatience,
                "naval_anxiety": req.naval_anxiety,
            },
            ratchet_beliefs=ratchets,
        )
        return self.counterfactual_verifier.verify_caocao_deadlock(
            caocao, set(req.active_conditions)
        )

    async def trigger_nightly_bsp(
        self,
        commands: List[Dict[str, Any]],
        extra_conditions: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        replay = await self.nightly_pipeline.run_turn(
            commands, extra_conditions=extra_conditions
        )
        self._latest_replay_cache = replay

        cf_audit = replay.get("counterfactual_audit") or {}
        is_true_deadlock = bool(cf_audit.get("is_true_deadlock", False))
        self.leaderboard.update_deadlock_verification_for_all(is_true_deadlock)
        return replay

    def get_leaderboard(
        self, character_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        entries = self.leaderboard.compute_rankings(character_filter=character_id)
        return [e.dict() for e in entries]


server_facade = ChibiServerFacade()

try:
    from fastapi import FastAPI, Query  # type: ignore
    from fastapi.middleware.cors import CORSMiddleware  # type: ignore

    app = FastAPI(
        title="千古一局：暗合推演 API Server",
        version="1.3.0",
        description="赤壁 MVP v3.0：盲盒内鬼排错 + OOC违和度 + 支线固化 + 试炼幻境选拔 + 夜间BSP与反事实死局验算",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/command/validate", response_model=GuardVerdict)
    async def api_validate_command(payload: Dict[str, Any]) -> GuardVerdict:
        return server_facade.validate_command(payload)

    @app.post("/api/sandbox/preview", response_model=BranchComparisonResult)
    async def api_sandbox_preview(
        req: SandboxPreviewRequest,
    ) -> BranchComparisonResult:
        return await server_facade.preview_sandbox(req)

    @app.post("/api/counterfactual/verify", response_model=DeadlockVerificationReport)
    async def api_verify_counterfactual(
        req: CounterfactualVerifyRequest,
    ) -> DeadlockVerificationReport:
        return server_facade.verify_counterfactual(req)

    @app.post("/api/nightly/run")
    async def api_run_nightly(commands: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await server_facade.trigger_nightly_bsp(commands)

    @app.get("/api/timeline/tree", response_model=List[CampaignBranchNode])
    async def api_get_timeline_tree(battle_id: str = "chibi_208ad") -> List[CampaignBranchNode]:
        return server_facade.fork_repo.list_timeline_tree(battle_id=battle_id)

    @app.post("/api/timeline/fork", response_model=TimelineForkResponse)
    async def api_fork_timeline(req: TimelineForkRequest) -> TimelineForkResponse:
        return server_facade.fork_repo.fork_timeline_and_simulate(req)

    @app.get("/api/room/status", response_model=BattleRoomState)
    async def api_get_room_status(room_id: str = "room_chibi_01") -> BattleRoomState:
        return server_facade.matchmaker.get_room(room_id=room_id)

    @app.post("/api/room/claim", response_model=ClaimRoleResponse)
    async def api_claim_role(req: ClaimRoleRequest) -> ClaimRoleResponse:
        return server_facade.matchmaker.claim_role_in_lobby(req)

    @app.post("/api/room/drop_in", response_model=ClaimRoleResponse)
    async def api_drop_in_takeover(req: ClaimRoleRequest) -> ClaimRoleResponse:
        return server_facade.matchmaker.drop_in_takeover(req)

    @app.get("/api/acting/state")
    async def api_get_acting_state(player_id: str = "player_main") -> Dict[str, Any]:
        return server_facade.acting_engine.export_acting_state_summary(player_id=player_id)

    @app.post("/api/acting/evaluate", response_model=OOCInspectionResult)
    async def api_evaluate_acting(req: ActingEvaluateRequest) -> OOCInspectionResult:
        return server_facade.acting_engine.evaluate_acting_and_solidify(
            player_id=req.player_id,
            character_id=req.character_id,
            action_text=req.action_text,
            current_round=req.current_round,
            base_ooc=req.base_ooc,
        )

    @app.post("/api/acting/unlock_fragment")
    async def api_unlock_fragment(req: UnlockFragmentRequest) -> Dict[str, Any]:
        return server_facade.acting_engine.trigger_memory_fragment_unlock(
            player_id=req.player_id,
            actor_character_id=req.actor_character_id,
            target_character_id=req.target_character_id,
            interaction_zone=req.interaction_zone,
        )

    @app.post("/api/acting/trial_submit", response_model=TrialIllusionRunScore)
    async def api_submit_trial_run(req: TrialSubmitRequest) -> TrialIllusionRunScore:
        return server_facade.acting_engine.compute_trial_illusion_score_and_rank(
            player_id=req.player_id,
            player_name=req.player_name,
            character_id=req.character_id,
            identity_points_spent=req.identity_points_spent,
            butterfly_points_spent=req.butterfly_points_spent,
            restoration_rate=req.restoration_rate,
            persona_fit_score=req.persona_fit_score,
            clear_efficiency_score=req.clear_efficiency_score,
            red_herrings_ruled_out=req.red_herrings_ruled_out,
            branches_solidified=req.branches_solidified,
        )

    @app.get("/api/leaderboard")
    async def api_get_leaderboard(
        character_id: Optional[str] = Query(default=None),
    ) -> List[Dict[str, Any]]:
        return server_facade.get_leaderboard(character_id=character_id)

except ImportError:
    app = None


if __name__ == "__main__":
    async def run_counterfactual_pipeline_demo() -> None:
        print("=========================================================================")
        print("  《千古一局：暗合推演》v1.3 夜间 BSP + 演技排错固化 + 反事实天梯测试")
        print("=========================================================================\n")

        incomplete_cmds = [
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
        ]
        airtight_cmds = incomplete_cmds + [
            {
                "player_id": "p_zhouyu_top",
                "character_id": "char_zhouyu",
                "template_id": "TPL_MINOR_LOGISTICS",
                "target_id": "wulin_depot",
                "slot_text": "乌林岸边设疑兵截断滩涂",
                "butterfly_cost": 3,
            }
        ]
        replay_b = await server_facade.trigger_nightly_bsp(airtight_cmds)
        audit_b = replay_b["counterfactual_audit"]
        print(f"  [反事实裁决]: {audit_b['referee_verdict']}")
        rankings_b = server_facade.get_leaderboard()
        print("  [v3.0 校准天梯结算（含 OOC 契合度系数 + 排错固化奖励 + 1.20x 死局加成）]:")
        for r in rankings_b:
            print(
                f"    TOP {r['rank']} (同角#{r['role_rank']}) | {r['character_name']:<6} "
                f"| 契合度:{r['persona_fit_factor']:.2f} | 固化奖:+{r['solidification_bonus_pts']:<2} "
                f"| 天梯分: {r['final_score']:>3} | {r['highlight_tag']}"
            )

    asyncio.run(run_counterfactual_pipeline_demo())