"""
《千古一局：暗合推演》Sprint 3 核心防崩坏模块：
动态内鬼洗牌器、物证前置锁（防抄作业开天眼）与固化支线舞台实体互斥仲裁器 (mole_seed_rotator.py v1.0)

解决《开发计划 v3.0》第 2 章诊断的两大网游落地死穴：
1. 雷区一（论坛剧透抄作业杀死盲盒排错）：
   - DynamicMoleSeedRotator：保持历史人物核心性格底色 100% 锁死不变，但按试炼批次/房间加密种子（SEED_ALPHA / SEED_BETA / SEED_GAMMA）动态轮换「篡改落点（TRUE_TAMPERED_MOLE）」与「原生烟雾弹（NATIVE_RED_HERRING）」。
   - ProofChainMetaGamingGuard（因果物证前置锁）：即便玩家从外部论坛看攻略得知当前批次谁是内鬼，若本局未亲自操控前置角色抵达对应舞台采集到【记忆碎片/当场物证 (proof_token)】，直接跳步下达纠偏固化指令将触发 `META_GAMING_PENALTY`（暴涨 +65 OOC 违和度），直接触发软禁棘轮或校事府灭口！
2. 雷区三（跨轮次支线固化叠加后的夜间 BSP 实体抢占死锁）：
   - StageEntityMutex（舞台实体互斥仲裁器）：当双方阵营已固化的自动支线在夜间争夺同一舞台实体（如 `entity_wulin_grass_boats` 乌林干草船或 `entity_caimao_seal_ledger` 水师印信底册）时，严格按「硬性物证反制 (EVENT_HARD_COUNTER_PROOF) > 舞台主场防御优先权 > 固化轮次时间戳」确定性裁决，败方支线优雅降级为 `BLOCKED_BY_ENTITY_MUTEX` 并生成次日战报预警，彻底杜绝状态机死锁。
"""

from __future__ import annotations
import hashlib
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from acting_solidifier import MoleTruthType


# ---------------------------------------------------------------------------
# 1. 动态内鬼批次种子定义与洗牌模型（Dynamic Mole Seeding）
# ---------------------------------------------------------------------------
class SeedBatchVariant(str, Enum):
    """平行试炼幻境/房间动态内鬼种子批次"""
    SEED_BATCH_ALPHA = "SEED_BATCH_ALPHA"  # 批次A：乌林粮草官+蒋干为真内鬼；怯战书佐汪生+蔡瑁为原生烟雾弹
    SEED_BATCH_BETA = "SEED_BATCH_BETA"    # 批次B（反抄作业变体）：怯战书佐汪生被敌方收买夹带伪印（真内鬼），乌林粮草官只是抱怨草料受潮（原生烟雾弹）
    SEED_BATCH_GAMMA = "SEED_BATCH_GAMMA"  # 批次C（深层潜伏变体）：蔡瑁亲随水军督邮盗用虎符（真内鬼），蒋干归营主动交出书信（非内鬼）


class RoleSeedAssignment(BaseModel):
    """单个角色在特定批次种子下的盲盒真相与前置必拿物证"""
    character_id: str
    character_name: str
    core_personality_locked: str = Field(..., description="永恒锁死的历史人物性格底色（任何批次均不可违背）")
    batch_truth_type: MoleTruthType = Field(..., description="当前批次种子下的真实盲盒身份")
    required_proof_token: str = Field(..., description="执行合规排错/固化前必须在本局亲自采集到的前置物证 ID")
    required_proof_zone: str = Field(..., description="采集该前置物证所在的舞台 ID")
    batch_specific_anomaly: str = Field(..., description="当前批次沙盘外泄的微差物理线索")
    batch_truth_reveal: str = Field(..., description="采集物证后揭晓的本批次真相说明")


class MetaGamingCheckResult(BaseModel):
    """防抄作业开天眼（物证前置锁）校验结果"""
    player_id: str
    batch_seed: SeedBatchVariant
    target_character_id: str
    attempted_action: str
    has_required_proof: bool
    required_proof_token: str
    is_wrong_batch_copycat: bool = Field(
        default=False, description="是否照抄了其他批次的攻略抓错了本批次的原生烟雾弹"
    )
    meta_gaming_ooc_penalty: int = Field(default=0, ge=0, le=100)
    final_ooc_suspicion: int = Field(default=0, ge=0, le=100)
    verdict_code: str
    verdict_message: str


# ---------------------------------------------------------------------------
# 2. 固化支线舞台实体互斥仲裁模型（Stage Entity Mutex Arbitrator）
# ---------------------------------------------------------------------------
class StageEntityContention(BaseModel):
    """夜间 BSP 推演中针对同一舞台物理实体的固化支线争夺请求"""
    branch_id: str
    camp: str
    character_id: str
    character_name: str
    target_entity_id: str = Field(..., description="争夺的舞台实体 ID，如 entity_wulin_grass_boats")
    target_zone_id: str = Field(..., description="实体所在舞台 ID，如 wulin_depot")
    is_home_zone_owner: bool = Field(..., description="该角色是否为该舞台的常驻主场负责人")
    has_hard_counter_proof: bool = Field(
        default=False, description="是否携带 EVENT_HARD_COUNTER_PROOF 级别的硬性反制物证/物理奇袭"
    )
    solidified_on_round: int = Field(..., ge=1, le=5, description="该支线完成固化的轮次（越早优先级越高）")
    intended_effect_summary: str


class EntityMutexArbitrationReport(BaseModel):
    """舞台实体互斥裁决报告"""
    target_entity_id: str
    target_zone_id: str
    winning_branch_id: str
    winning_character_name: str
    winning_camp: str
    losing_branch_id: str
    losing_character_name: str
    losing_status_code: str = "BLOCKED_BY_ENTITY_MUTEX"
    arbitration_rule_triggered: str
    morning_report_alert: str


# ---------------------------------------------------------------------------
# 3. 动态内鬼种子洗牌与防开天眼校验引擎实现
# ---------------------------------------------------------------------------
BATCH_SEED_MATRIX: Dict[SeedBatchVariant, Dict[str, RoleSeedAssignment]] = {
    SeedBatchVariant.SEED_BATCH_ALPHA: {
        "char_junxu_clerk": RoleSeedAssignment(
            character_id="char_junxu_clerk",
            character_name="曹营怯战书佐·汪生",
            core_personality_locked="胆小惜命、畏惧水战疾疫、面对上官唯唯诺诺",
            batch_truth_type=MoleTruthType.NATIVE_RED_HERRING,
            required_proof_token="PROOF_WANG_BAGGAGE_PASS",
            required_proof_zone="caoying_port",
            batch_specific_anomaly="夜行私藏盘缠行囊，行囊中仅有返乡路引，无任何江东书信。",
            batch_truth_reveal="【批次A真相：原生怂货烟雾弹】汪生仅因惧怕瘟疫想私逃回乡，并非江东内鬼。",
        ),
        "char_wulin_granary": RoleSeedAssignment(
            character_id="char_wulin_granary",
            character_name="乌林粮草官",
            core_personality_locked="谨小慎微、看重账目规制、怕担延误军粮之罪",
            batch_truth_type=MoleTruthType.TRUE_TAMPERED_MOLE,
            required_proof_token="PROOF_WULIN_OIL_MANIFEST",
            required_proof_zone="wulin_depot",
            batch_specific_anomaly="乌林油草账册底部多出一道伪造的将作监调船手令，引导干草船泊往北寨上风口。",
            batch_truth_reveal="【批次A真相：真篡改内鬼！】粮草官被伪造手令误导，正将引火干草船移向北军主战船上风口！",
        ),
    },
    SeedBatchVariant.SEED_BATCH_BETA: {
        "char_junxu_clerk": RoleSeedAssignment(
            character_id="char_junxu_clerk",
            character_name="曹营怯战书佐·汪生",
            core_personality_locked="胆小惜命、畏惧水战疾疫、面对上官唯唯诺诺",
            batch_truth_type=MoleTruthType.TRUE_TAMPERED_MOLE,
            required_proof_token="PROOF_WANG_STOLEN_SEAL",
            required_proof_zone="caoying_port",
            batch_specific_anomaly="汪生逃跑行囊夹层中藏有被江东细作以免死金帛诱骗盗出的「乌林船坞副印」。",
            batch_truth_reveal=(
                "【批次B真相：怂货被利用成真内鬼！】敌方利用汪生贪生怕死想逃跑的弱点，"
                "许诺送其出境并诱使其顺走「乌林船坞副印」！（照抄批次A攻略放过汪生者将满盘皆输）"
            ),
        ),
        "char_wulin_granary": RoleSeedAssignment(
            character_id="char_wulin_granary",
            character_name="乌林粮草官",
            core_personality_locked="谨小慎微、看重账目规制、怕担延误军粮之罪",
            batch_truth_type=MoleTruthType.NATIVE_RED_HERRING,
            required_proof_token="PROOF_WULIN_DAMP_STRAW",
            required_proof_zone="wulin_depot",
            batch_specific_anomaly="粮草官在码头连夜晾晒受潮干草，船只均用铁锚死死固定在浅滩避风处。",
            batch_truth_reveal=(
                "【批次B真相：原生尽职烟雾弹！】本批次粮草官完全未被篡改，夜调草船仅是为晾晒受潮草料防程昱责罚！"
                "（照抄批次A攻略强行去抓粮草官者将触发诬告同僚 OOC 惩罚！）"
            ),
        ),
    },
    SeedBatchVariant.SEED_BATCH_GAMMA: {
        "char_junxu_clerk": RoleSeedAssignment(
            character_id="char_junxu_clerk",
            character_name="曹营怯战书佐·汪生",
            core_personality_locked="胆小惜命、畏惧水战疾疫、面对上官唯唯诺诺",
            batch_truth_type=MoleTruthType.NATIVE_RED_HERRING,
            required_proof_token="PROOF_WANG_BAGGAGE_PASS",
            required_proof_zone="caoying_port",
            batch_specific_anomaly="汪生染风寒高热卧床，行囊原封未动。",
            batch_truth_reveal="【批次C真相：原生病患烟雾弹】汪生真染风寒卧床不起，完全无作案能力。",
        ),
        "char_wulin_granary": RoleSeedAssignment(
            character_id="char_wulin_granary",
            character_name="乌林粮草官",
            core_personality_locked="谨小慎微、看重账目规制、怕担延误军粮之罪",
            batch_truth_type=MoleTruthType.TRUE_TAMPERED_MOLE,
            required_proof_token="PROOF_WULIN_OIL_MANIFEST",
            required_proof_zone="wulin_depot",
            batch_specific_anomaly="粮草官受伪造军令驱使，将十艘火油船混入北寨巡逻队。",
            batch_truth_reveal="【批次C真相：真篡改内鬼！】粮草官正按伪令调拨火油船混入北寨。",
        ),
    },
}


class DynamicMoleSeedRotator:
    """动态内鬼洗牌与因果物证前置锁（防论坛抄作业开天眼）"""

    META_GAMING_OOC_PENALTY: int = 65  # 未采集前置物证直接跳步排错的 OOC 暴涨惩罚
    WRONG_BATCH_COPYCAT_OOC_PENALTY: int = 52  # 照抄其他批次攻略抓错烟雾弹的 OOC 惩罚

    def __init__(self) -> None:
        self.active_batch: SeedBatchVariant = SeedBatchVariant.SEED_BATCH_ALPHA
        self.player_collected_proofs: Dict[str, Set[str]] = {
            "player_main": {"PROOF_WANG_BAGGAGE_PASS", "PROOF_WULIN_OIL_MANIFEST"}
        }

    @staticmethod
    def derive_deterministic_batch(room_id: str, day: int) -> SeedBatchVariant:
        """根据房间号与天数生成不可预测但可复现的批次种子哈希"""
        digest = hashlib.sha256(f"{room_id}::day_{day}::chibi_salt".encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % 3
        return [
            SeedBatchVariant.SEED_BATCH_ALPHA,
            SeedBatchVariant.SEED_BATCH_BETA,
            SeedBatchVariant.SEED_BATCH_GAMMA,
        ][idx]

    def rotate_batch_seed(
        self, target_batch: Optional[SeedBatchVariant] = None, clear_player_proofs: bool = True
    ) -> Dict[str, Any]:
        """切换当前试炼幻境的内鬼种子批次（切换后旧批次物证自动失效，模拟新开一局防抄作业）"""
        order = [
            SeedBatchVariant.SEED_BATCH_ALPHA,
            SeedBatchVariant.SEED_BATCH_BETA,
            SeedBatchVariant.SEED_BATCH_GAMMA,
        ]
        if target_batch is not None:
            self.active_batch = target_batch
        else:
            cur_idx = order.index(self.active_batch)
            self.active_batch = order[(cur_idx + 1) % len(order)]

        if clear_player_proofs:
            self.player_collected_proofs["player_main"] = set()

        assignments = BATCH_SEED_MATRIX[self.active_batch]
        return {
            "active_batch": self.active_batch.value,
            "collected_proofs": sorted(self.player_collected_proofs.get("player_main", set())),
            "roles": {cid: a.dict() for cid, a in assignments.items()},
        }

    def collect_stage_proof(
        self, player_id: str, character_id: str, current_zone: str
    ) -> Dict[str, Any]:
        """玩家亲自控制小人物抵达目标舞台侦察，采集当前批次的真实物证 Token"""
        assignments = BATCH_SEED_MATRIX[self.active_batch]
        role_cfg = assignments.get(character_id)
        if not role_cfg:
            return {"collected": False, "message": f"未知排查目标: {character_id}"}

        if current_zone != role_cfg.required_proof_zone:
            return {
                "collected": False,
                "message": (
                    f"【现场侦察失败】需前往 `{role_cfg.required_proof_zone}` 才能勘验「{role_cfg.character_name}」的物证！"
                ),
            }

        proofs = self.player_collected_proofs.setdefault(player_id, set())
        proofs.add(role_cfg.required_proof_token)
        return {
            "collected": True,
            "active_batch": self.active_batch.value,
            "character_id": character_id,
            "proof_token": role_cfg.required_proof_token,
            "anomaly_observed": role_cfg.batch_specific_anomaly,
            "truth_reveal": role_cfg.batch_truth_reveal,
            "message": (
                f"🔍【已采集当场物证 `{role_cfg.required_proof_token}`】"
                f"{role_cfg.batch_specific_anomaly} → {role_cfg.batch_truth_reveal}"
            ),
        }

    def verify_action_against_meta_gaming(
        self,
        player_id: str,
        target_character_id: str,
        attempted_action: str,
        accused_as_mole: bool,
        base_ooc: int = 8,
    ) -> MetaGamingCheckResult:
        """
        防抄作业核心拦截器：
        1. 检查玩家是否持有当前批次该角色的 `required_proof_token`；若未勘验现场直接按论坛攻略跳步操作，判为「开天眼 Meta-Gaming」，OOC +65！
        2. 检查玩家是否照抄了批次 A 的攻略，在批次 B 中把真内鬼放走或把真尽职 NPC 当内鬼抓捕，判为「跨批次照抄翻车」，OOC +52！
        """
        assignments = BATCH_SEED_MATRIX[self.active_batch]
        role_cfg = assignments[target_character_id]
        held_proofs = self.player_collected_proofs.get(player_id, set())
        has_proof = role_cfg.required_proof_token in held_proofs

        # 情况 1：未采集前置物证，直接跳步执行终极纠偏/抓捕（无凭据开天眼）
        if not has_proof:
            final_ooc = min(100, base_ooc + self.META_GAMING_OOC_PENALTY)
            return MetaGamingCheckResult(
                player_id=player_id,
                batch_seed=self.active_batch,
                target_character_id=target_character_id,
                attempted_action=attempted_action,
                has_required_proof=False,
                required_proof_token=role_cfg.required_proof_token,
                is_wrong_batch_copycat=False,
                meta_gaming_ooc_penalty=self.META_GAMING_OOC_PENALTY,
                final_ooc_suspicion=final_ooc,
                verdict_code="REJECTED_META_GAMING_NO_PROOF",
                verdict_message=(
                    f"🚨【防抄作业前置锁拦截！(OOC 暴涨 +{self.META_GAMING_OOC_PENALTY} → {final_ooc}/100)】"
                    f"你本局尚未前往 `{role_cfg.required_proof_zone}` 勘验获取物证 `{role_cfg.required_proof_token}`，"
                    f"却直接针对「{role_cfg.character_name}」下达终极排错指令，被周围 AI 判定为“无端臆测/未卜先知”，当场触发停职软禁棘轮！"
                ),
            )

        # 情况 2：虽然拿了线索，但死板照抄其他批次攻略（把当前批次的原生烟雾弹当真内鬼抓，或放任真内鬼）
        is_actually_mole = role_cfg.batch_truth_type == MoleTruthType.TRUE_TAMPERED_MOLE
        if accused_as_mole != is_actually_mole:
            final_ooc = min(100, base_ooc + self.WRONG_BATCH_COPYCAT_OOC_PENALTY)
            return MetaGamingCheckResult(
                player_id=player_id,
                batch_seed=self.active_batch,
                target_character_id=target_character_id,
                attempted_action=attempted_action,
                has_required_proof=True,
                required_proof_token=role_cfg.required_proof_token,
                is_wrong_batch_copycat=True,
                meta_gaming_ooc_penalty=self.WRONG_BATCH_COPYCAT_OOC_PENALTY,
                final_ooc_suspicion=final_ooc,
                verdict_code="REJECTED_WRONG_BATCH_COPYCAT",
                verdict_message=(
                    f"💥【照抄攻略翻车！(当前为 {self.active_batch.value}，OOC +{self.WRONG_BATCH_COPYCAT_OOC_PENALTY} → {final_ooc}/100)】"
                    f"{role_cfg.batch_truth_reveal} 你照搬旧批次套路做出错误指控，打草惊蛇并引发主帅强烈戒心！"
                ),
            )

        # 情况 3：物证齐全且判断精准，允许固化支线！
        return MetaGamingCheckResult(
            player_id=player_id,
            batch_seed=self.active_batch,
            target_character_id=target_character_id,
            attempted_action=attempted_action,
            has_required_proof=True,
            required_proof_token=role_cfg.required_proof_token,
            is_wrong_batch_copycat=False,
            meta_gaming_ooc_penalty=0,
            final_ooc_suspicion=base_ooc,
            verdict_code="VERIFIED_CLEAN_SOLIDIFICATION",
            verdict_message=(
                f"✅【物证链闭环 + 排错精准 ({self.active_batch.value})】已凭 `{role_cfg.required_proof_token}` "
                f"完成「{role_cfg.character_name}」身份纠正与支线固化（违和度仅 {base_ooc}/100）！"
            ),
        )


# ---------------------------------------------------------------------------
# 4. 固化支线舞台实体互斥仲裁器（StageEntityMutex）
# ---------------------------------------------------------------------------
class StageEntityMutexArbitrator:
    """
    解决雷区三：当多方跨轮次固化支线在夜间 BSP 推演中同时操作同一舞台实体（如乌林干草船）时，
    按确定性优先级裁决，防止状态机冲突死锁。
    优先级顺序：
      1. 是否携带硬性物证/物理奇袭 (has_hard_counter_proof == True)
      2. 是否拥有该舞台的主场防御管辖权 (is_home_zone_owner == True)
      3. 支线固化达成轮次先后 (solidified_on_round 越小越优先)
    """

    @staticmethod
    def arbitrate_contention(
        branch_a: StageEntityContention, branch_b: StageEntityContention
    ) -> EntityMutexArbitrationReport:
        assert branch_a.target_entity_id == branch_b.target_entity_id, "Must contend for same entity"

        def priority_key(b: StageEntityContention) -> tuple:
            return (
                1 if b.has_hard_counter_proof else 0,
                1 if b.is_home_zone_owner else 0,
                -b.solidified_on_round,
            )

        key_a = priority_key(branch_a)
        key_b = priority_key(branch_b)

        if key_a >= key_b:
            winner, loser = branch_a, branch_b
        else:
            winner, loser = branch_b, branch_a

        if winner.has_hard_counter_proof and not loser.has_hard_counter_proof:
            rule_reason = "法则1：硬性物证/物理奇袭（EVENT_HARD_COUNTER_PROOF）击穿常规固化防线"
        elif winner.is_home_zone_owner and not loser.is_home_zone_owner:
            rule_reason = f"法则2：舞台主场管辖权优先（「{winner.character_name}」在 `{winner.target_zone_id}` 拥有主场防御优先权）"
        else:
            rule_reason = f"法则3：先发固化时间戳优先（Round {winner.solidified_on_round} 先于 Round {loser.solidified_on_round}）"

        alert_msg = (
            f"⚔️【夜间 BSP 实体互斥裁决 (`{winner.target_entity_id}`)】"
            f"「{winner.character_name}({winner.camp})」的固化支线胜出（触发{rule_reason}）；"
            f"「{loser.character_name}({loser.camp})」的原固化支线已降级为 `BLOCKED_BY_ENTITY_MUTEX`，请于今日调整暗棋！"
        )

        return EntityMutexArbitrationReport(
            target_entity_id=winner.target_entity_id,
            target_zone_id=winner.target_zone_id,
            winning_branch_id=winner.branch_id,
            winning_character_name=winner.character_name,
            winning_camp=winner.camp,
            losing_branch_id=loser.branch_id,
            losing_character_name=loser.character_name,
            arbitration_rule_triggered=rule_reason,
            morning_report_alert=alert_msg,
        )


if __name__ == "__main__":
    print("=========================================================================")
    print("  《千古一局：暗合推演》防抄作业动态洗牌与支线互斥仲裁自检 (mole_seed_rotator.py)")
    print("=========================================================================\n")

    rotator = DynamicMoleSeedRotator()

    # 测试 1：切换到批次 B（SEED_BATCH_BETA），此时怯战书佐汪生才是被敌方利用的真内鬼，乌林粮草官是烟雾弹
    rotator.rotate_batch_seed(SeedBatchVariant.SEED_BATCH_BETA, clear_player_proofs=True)
    print(f"--- [测试 1] 当前试炼批次已洗牌为: {rotator.active_batch.value} ---")

    # 测试 2：玩家不采集物证，直接照抄论坛攻略去抓乌林粮草官 -> 触发开天眼无物证拦截 (+65 OOC)
    res_no_proof = rotator.verify_action_against_meta_gaming(
        player_id="player_main",
        target_character_id="char_wulin_granary",
        attempted_action="直接指控乌林粮草官并隔离船只",
        accused_as_mole=True,
        base_ooc=10,
    )
    print(f"  [未勘验现场直接抄作业结果]: {res_no_proof.verdict_message}")
    assert res_no_proof.verdict_code == "REJECTED_META_GAMING_NO_PROOF"
    assert res_no_proof.final_ooc_suspicion == 75

    # 测试 3：玩家去乌林采集了物证，但仍然死板按批次 A 套路指控粮草官是内鬼 -> 触发跨批次照抄翻车 (+52 OOC)
    rotator.collect_stage_proof("player_main", "char_wulin_granary", "wulin_depot")
    res_wrong_batch = rotator.verify_action_against_meta_gaming(
        player_id="player_main",
        target_character_id="char_wulin_granary",
        attempted_action="强行按批次A套路抓捕乌林粮草官",
        accused_as_mole=True,
        base_ooc=10,
    )
    print(f"\n  [死板照搬旧批次攻略结果]: {res_wrong_batch.verdict_message}")
    assert res_wrong_batch.verdict_code == "REJECTED_WRONG_BATCH_COPYCAT"

    # 测试 4：玩家在批次 B 勘验北岸水寨汪生行囊，揪出汪生夹带的乌林船坞副印 -> 物证闭环通过！
    proof_wang = rotator.collect_stage_proof("player_main", "char_junxu_clerk", "caoying_port")
    print(f"\n  [现场勘验汪生行囊]: {proof_wang['message']}")
    res_clean = rotator.verify_action_against_meta_gaming(
        player_id="player_main",
        target_character_id="char_junxu_clerk",
        attempted_action="截获汪生行囊中的乌林副印并顺藤摸瓜",
        accused_as_mole=True,
        base_ooc=6,
    )
    print(f"  [批次B精准排错结果]: {res_clean.verdict_message}")
    assert res_clean.verdict_code == "VERIFIED_CLEAN_SOLIDIFICATION"

    # 测试 5：夜间 BSP 固化支线舞台实体互斥仲裁（乌林粮草官主场防御 vs 江东甘宁潜入奇袭）
    print("\n--- [测试 5] 夜间 BSP 固化支线互斥仲裁 (StageEntityMutex) ---")
    wei_branch = StageEntityContention(
        branch_id="solid_wulin_r1",
        camp="WEI",
        character_id="char_wulin_granary",
        character_name="乌林粮草官",
        target_entity_id="entity_wulin_grass_boats",
        target_zone_id="wulin_depot",
        is_home_zone_owner=True,
        has_hard_counter_proof=False,
        solidified_on_round=1,
        intended_effect_summary="按双印规制将干草船隔离至下风口浅滩",
    )
    wu_branch_soft = StageEntityContention(
        branch_id="solid_ganning_r2",
        camp="ALLIANCE",
        character_id="char_ganning",
        character_name="甘宁",
        target_entity_id="entity_wulin_grass_boats",
        target_zone_id="wulin_depot",
        is_home_zone_owner=False,
        has_hard_counter_proof=False,
        solidified_on_round=2,
        intended_effect_summary="夜探乌林试图移走干草船",
    )
    rep_home_win = StageEntityMutexArbitrator.arbitrate_contention(wei_branch, wu_branch_soft)
    print(f"  [无硬物证对抗裁决]: {rep_home_win.morning_report_alert}")
    assert rep_home_win.winning_character_name == "乌林粮草官"

    wu_branch_hard = StageEntityContention(
        branch_id="solid_ganning_r3_hard",
        camp="ALLIANCE",
        character_id="char_ganning",
        character_name="甘宁",
        target_entity_id="entity_wulin_grass_boats",
        target_zone_id="wulin_depot",
        is_home_zone_owner=False,
        has_hard_counter_proof=True,
        solidified_on_round=3,
        intended_effect_summary="凭截获的【乌林船坞副印】硬物证伪造调令，强行接管干草船",
    )
    rep_hard_win = StageEntityMutexArbitrator.arbitrate_contention(wei_branch, wu_branch_hard)
    print(f"  [持硬物证反固化裁决]: {rep_hard_win.morning_report_alert}")
    assert rep_hard_win.winning_character_name == "甘宁"
    print("\n[ALL MOLE SEED ROTATOR & ENTITY MUTEX CHECKS PASSED]")
