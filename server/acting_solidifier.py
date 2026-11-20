"""
《千古一局：暗合推演》悬疑演技排错与支线固化引擎 (acting_solidifier.py v1.0)

核心设计来源（对应 game-concept.md v3.0 第 4 章）：
1. 盲盒内鬼排错与原生烟雾弹 NPC（《我的玩家都是演技派》第64章）：
   - 区分「真正被篡改的敌方暗棋 (TRUE_TAMPERED_MOLE)」与「自带人性弱点的原生贪生怕死烟雾弹 (NATIVE_RED_HERRING)」。
   - 【记忆碎片】接触式解锁：开局仅持 1 点基层身份卡，同舞台近距离交互/跟踪/截获信物后解锁中高层角色卡。
2. OOC 违和度警觉条与三级惩罚阶梯（《我的玩家都是演技派》第64章）：
   - 校验玩家指令是否越出角色人设行为包络线（PersonaBehaviorEnvelope）。
   - 0-39 安全潜伏 | 40-69 轻度违和（视野收窄） | 70-89 重度违和（停职软禁棘轮） | >=90 致命暴露（校事府/影侍瞬杀灭口，点数作废）。
3. 跨轮次身份纠正与支线固化（《我的玩家都是演技派》第64章）：
   - 当 `persona_fit_score >= 85`（即 `ooc_suspicion <= 15`）且完成符合人设的纠偏动作后，写入 `solidified_branches`。
   - 下一轮无需再消耗身份点数附身该角色，AI 自动 100% 复现已固化支线，释放身份点数滚雪球抓深层内鬼。
   - 支持「固化 vs 反固化」：仅当敌方注入 `EVENT_HARD_COUNTER_PROOF` 或破坏物理前置条件时方可击穿旧固化支线。
4. 试炼幻境三维评分与 Top 1 晋级终局历史切片公演（《我的玩家都是演技派》第65、79章 & 《这游戏也太真实了》）：
   - 综合评分 = 历史/阵营目标还原度(40%) + 扮演契合度(35%) + 破局消耗效率(25%) + 排错固化奖励。
   - 选拔各角色 Top 1 晋级夜间正史合龙与 Day 5【终极试炼：赤壁火攻公演】，其余玩家化身为远征兵卒执行百人微扰共斗。
"""

from __future__ import annotations
import json
import os
import re
import urllib.request
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. 核心枚举与数据结构定义
# ---------------------------------------------------------------------------
class MoleTruthType(str, Enum):
    """角色在盲盒迷雾下的真实底色分类"""
    TRUE_TAMPERED_MOLE = "TRUE_TAMPERED_MOLE"        # 真正被敌方玩家/乱序黑手篡改的内鬼节点
    NATIVE_RED_HERRING = "NATIVE_RED_HERRING"        # 原生烟雾弹（真贪生怕死/贪财推诿，非敌方篡改）
    LOYAL_NORMAL = "LOYAL_NORMAL"                    # 正常履职角色


class OOCAlertTier(str, Enum):
    """OOC 违和度三级惩罚阶梯"""
    SAFE_IN_CHARACTER = "SAFE_IN_CHARACTER"          # 0 - 39：言行贴合人设，正常潜伏
    MILD_SUSPICION = "MILD_SUSPICION"                # 40 - 69：周围 AI 警觉，套话成功率减半，私有视野收窄
    HOUSE_ARREST_RATCHET = "HOUSE_ARREST_RATCHET"    # 70 - 89：触发单向警觉棘轮，被主帅/执法官停职软禁
    SHADOW_ASSASSINATED = "SHADOW_ASSASSINATED"      # >= 90：致命暴露，触发校事府/暗探瞬杀灭口，投入点数全额作废


class PersonaBehaviorEnvelope(BaseModel):
    """角色人设行为包络线与盲盒排错配置"""
    character_id: str
    character_name: str
    camp: str
    identity_point_cost: int = Field(..., ge=1, le=3, description="附身所需身份点数（基层1/中层2/主帅3）")
    unlocked_by_default: bool = Field(default=False, description="是否开局默认解锁")
    unlock_prerequisite_zone: str = Field(..., description="触发【记忆碎片】接触解锁所需舞台")
    unlock_clue_item: str = Field(..., description="触发【记忆碎片】所需接触事件或信物")
    truth_type: MoleTruthType = Field(..., description="盲盒真实底色（内鬼 / 原生烟雾弹 / 正常）")
    surface_anomaly_clue: str = Field(..., description="沙盘外显的可疑表象（供玩家侦察排雷）")
    hidden_truth_explanation: str = Field(..., description="排错揭晓后的真相说明")
    in_character_keywords: List[str] = Field(default_factory=list, description="符合人设的言行关键词（降违和/高契合）")
    ooc_taboo_keywords: Dict[str, int] = Field(
        default_factory=dict, description="违背人设的莽撞言行关键词及其增加的 ooc_suspicion 惩罚值"
    )
    solidification_action_key: str = Field(..., description="触发【支线固化】的标准纠偏动作标识")
    solidified_script_summary: str = Field(..., description="固化成功后 AI 下一轮自动执行的支线剧本")


class OOCInspectionResult(BaseModel):
    """OOC 违和度校验与身份纠正结果"""
    character_id: str
    character_name: str
    action_text: str
    base_ooc: int
    delta_ooc: int
    final_ooc_suspicion: int = Field(..., ge=0, le=100)
    persona_fit_score: int = Field(..., ge=0, le=100, description="扮演契合度 = 100 - final_ooc_suspicion")
    alert_tier: OOCAlertTier
    triggered_taboos: List[str] = Field(default_factory=list)
    is_points_wiped_by_assassin: bool = Field(default=False)
    is_house_arrested: bool = Field(default=False)
    mole_investigation_verdict: str = Field(..., description="内鬼排错结论（排除错误答案 / 揪出真内鬼）")
    solidified_success: bool = Field(default=False, description="是否成功触发【身份纠正与支线固化】")
    solidified_branch_note: str = Field(default="")
    scoring_mode: str = Field(default="DETERMINISTIC_FALLBACK", description="LLM_SEMANTIC 或 DETERMINISTIC_FALLBACK")
    llm_judge_reason: str = Field(default="", description="大模型语义裁判或规则引擎的判定依据")
    feedback_message: str


class SolidifiedBranchRecord(BaseModel):
    """跨轮次已固化支线记录（下一轮免消耗身份点数自动运行）"""
    branch_record_id: str
    character_id: str
    character_name: str
    solidified_by_player: str
    solidified_on_round: int
    persona_fit_score: int
    identity_points_refunded_next_round: int
    auto_script_summary: str
    is_broken_by_counter_proof: bool = Field(default=False)
    broken_reason: Optional[str] = None


class TrialIllusionRunScore(BaseModel):
    """日间【试炼幻境】三维评分与终局切片选拔条目"""
    run_id: str
    player_id: str
    player_name: str
    character_id: str
    character_name: str
    identity_points_spent: int
    butterfly_points_spent: int
    restoration_rate: float = Field(..., ge=0.0, le=100.0, description="历史/阵营目标还原度 (0-100, 权重40%)")
    persona_fit_score: float = Field(..., ge=0.0, le=100.0, description="角色扮演契合度 (0-100, 权重35%)")
    clear_efficiency_score: float = Field(..., ge=0.0, le=100.0, description="破局消耗与时间效率 (0-100, 权重25%)")
    red_herrings_ruled_out: int = Field(default=0, ge=0, description="成功排除的原生烟雾弹 NPC 数量")
    branches_solidified: int = Field(default=0, ge=0, description="成功固化的支线数量")
    composite_trial_score: float = Field(..., description="试炼幻境综合总分")
    qualifies_for_true_slice: bool = Field(default=False, description="是否夺得单角色 Top 1 晋级夜间正史/终局切片")
    finale_role_assignment: str = Field(default="EXPEDITION_VANGUARD", description="TOP1_SLICE_ACTOR 或 EXPEDITION_VANGUARD")


# ---------------------------------------------------------------------------
# 2. 赤壁 MVP 角色盲盒底色与人设包络库（含真内鬼 vs 原生怂货烟雾弹）
# ---------------------------------------------------------------------------
CHIBI_ACTING_ENVELOPES: Dict[str, PersonaBehaviorEnvelope] = {
    "char_junxu_clerk": PersonaBehaviorEnvelope(
        character_id="char_junxu_clerk",
        character_name="曹营怯战书佐（汪生）",
        camp="WEI",
        identity_point_cost=1,
        unlocked_by_default=True,
        unlock_prerequisite_zone="caoying_port",
        unlock_clue_item="初始基础身份卡（1点）",
        truth_type=MoleTruthType.NATIVE_RED_HERRING,
        surface_anomaly_clue="【可疑表象】连续两夜在北岸水寨鬼祟徘徊、私藏金帛行囊，且刻意少报水军染疫人数。",
        hidden_truth_explanation=(
            "【排错真相：排除错误答案！】此人并非江东暗探或被篡改内鬼，而是天生贪生怕死（致敬《演技派》第64章汪景辉），"
            "因惧怕大江水战与瘟疫，正欲趁夜色从乌林小道逃出军营！"
        ),
        in_character_keywords=["称病告假", "暗中打点行囊", "借送文书去乌林坞", "顺从上官"],
        ooc_taboo_keywords={
            "拔剑死战": 55,
            "当众质问蔡瑁": 60,
            "独闯中军大帐献策": 65,
            "逼问机密": 45,
        },
        solidification_action_key="借送文书去乌林坞顺路盯梢粮仓",
        solidified_script_summary="【已固化支线·顺水推舟】利用书佐怯战想调离前线的本性，合规调往乌林粮坞抄录账目，顺带为我方盯死乌林上风口船位异动（下轮省 1 点身份点）。",
    ),
    "char_wulin_granary": PersonaBehaviorEnvelope(
        character_id="char_wulin_granary",
        character_name="乌林粮草官",
        camp="WEI",
        identity_point_cost=1,
        unlocked_by_default=True,
        unlock_prerequisite_zone="wulin_depot",
        unlock_clue_item="初始基础身份卡（1点）",
        truth_type=MoleTruthType.TRUE_TAMPERED_MOLE,
        surface_anomaly_clue="【可疑表象】借口乌林河道拥堵，将十余艘满载干草与猛火油的轻舟改泊于北军主战船上风口。",
        hidden_truth_explanation=(
            "【排错真相：揪出真内鬼！】该角色已被敌方暗棋篡改短期念头，企图配合铁索连舟制造上风口20%火势暴击！"
        ),
        in_character_keywords=["双印复核船籍", "按军需规制分仓隔离", "向程昱秘呈油草账册", "清点受潮草料"],
        ooc_taboo_keywords={
            "直接带兵处决水手": 75,
            "擅自火烧船队": 85,
            "越级闯帐咆哮曹操": 70,
            "承认自己是玩家": 95,
        },
        solidification_action_key="按军需规制分仓隔离并向程昱秘呈油草账册",
        solidified_script_summary="【已固化支线·釜底抽薪】乌林粮草官已被身份纠正并固化：每晚自动按曹营双印规制将干草船隔离至下风口浅滩，并向程昱报备（下轮省 1 点身份点）。",
    ),
    "char_old_sailor": PersonaBehaviorEnvelope(
        character_id="char_old_sailor",
        character_name="江东老水手",
        camp="ALLIANCE",
        identity_point_cost=1,
        unlocked_by_default=True,
        unlock_prerequisite_zone="jiangdong_pier",
        unlock_clue_item="初始基础身份卡（1点）",
        truth_type=MoleTruthType.LOYAL_NORMAL,
        surface_anomaly_clue="【可疑表象】深夜独自在芦苇荡测风观潮，与鲁肃账下亲随私语。",
        hidden_truth_explanation="【排错真相：江东关键暗棋】熟悉九江风向与暗流，可在不触发曹军哨船警觉下引导草船受箭。",
        in_character_keywords=["观云测出子时大雾", "加固草船青布", "顺东南暗流操舟"],
        ooc_taboo_keywords={
            "引经据典空谈天下大势": 50,
            "命令周瑜改换阵型": 75,
            "孤身潜入曹营刺杀曹操": 92,
        },
        solidification_action_key="观云测出子时大雾并顺东南暗流操舟",
        solidified_script_summary="【已固化支线·暗流引航】老水手已固化：子时自动向鲁肃报信并校准草船受箭吃水角度（下轮省 1 点身份点）。",
    ),
    "char_jianggan": PersonaBehaviorEnvelope(
        character_id="char_jianggan",
        character_name="蒋干",
        camp="WEI",
        identity_point_cost=2,
        unlocked_by_default=False,
        unlock_prerequisite_zone="caoying_tent",
        unlock_clue_item="【记忆碎片：同窗书简】在曹营大帐或江东船坞与蒋干同槽交互1次解锁",
        truth_type=MoleTruthType.TRUE_TAMPERED_MOLE,
        surface_anomaly_clue="【可疑表象】自江东归来后神色慌张，袖中暗藏一封涉及蔡瑁张允的密信。",
        hidden_truth_explanation="【排错真相：中计棋子】蒋干并非主动叛曹，但已被周瑜「群英会反间计」植入假信，即将触发曹操疑心棘轮！",
        in_character_keywords=["以同窗旧情试探", "向程昱先呈密信核验笔迹", "自陈江东饮宴细节"],
        ooc_taboo_keywords={
            "当众怒斥周瑜诈伪并拔剑": 82,
            "当场撕毁密信隐瞒曹操": 55,
            "自称知晓未来赤壁火攻": 95,
        },
        solidification_action_key="向程昱先呈密信核验笔迹再禀曹操",
        solidified_script_summary="【已固化支线·反间拆解】蒋干已被纠正固化：归营后先交由程昱与校事府比对蔡瑁笔迹印信，阻断曹操疑心值暴涨（下轮省 2 点身份点）。",
    ),
    "char_caimao": PersonaBehaviorEnvelope(
        character_id="char_caimao",
        character_name="蔡瑁",
        camp="WEI",
        identity_point_cost=2,
        unlocked_by_default=False,
        unlock_prerequisite_zone="caoying_port",
        unlock_clue_item="【记忆碎片：水师虎符】在水寨截获伪信线索或排除怯战书佐后解锁",
        truth_type=MoleTruthType.NATIVE_RED_HERRING,
        surface_anomaly_clue="【可疑表象】对北军将领傲慢敷衍、夜间关闭水寨内门禁止北军杂役出入。",
        hidden_truth_explanation=(
            "【排错真相：排除错误答案！】蔡瑁虽性格倨傲、爱惜荆州水师私兵，但本局并未私通周瑜；"
            "其封锁内门实为防止北军疫病传染荆州水手，属于典型的原生性格烟雾弹！"
        ),
        in_character_keywords=["严整水寨鹿角", "呈交荆州水师花名册自证", "分批轮换染疫北军上岸"],
        ooc_taboo_keywords={
            "卑躬屈膝向小卒认错": 48,
            "无故斩杀北军督邮": 85,
            "率船队直接投奔江东": 92,
        },
        solidification_action_key="呈交荆州水师花名册自证并严整水寨鹿角",
        solidified_script_summary="【已固化支线·水师自证】蔡瑁已固化：主动向曹操与程昱交出每日巡江海图与印信底册，提供硬性反证解除疑心棘轮（下轮省 2 点身份点）。",
    ),
}


# ---------------------------------------------------------------------------
# 3. 悬疑演技排错、记忆碎片解锁与支线固化核心引擎
# ---------------------------------------------------------------------------
class ActingSolidifierEngine:
    """管理记忆碎片解锁、盲盒内鬼排错、OOC 违和度校验、支线固化与试炼幻境 Top 1 选拔"""

    SOLIDIFY_MIN_FIT_SCORE: int = 85  # 扮演契合度 >= 85 (即 ooc_suspicion <= 15) 方可触发支线固化

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        self._load_dotenv_if_present()
        self.api_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or ""
        ).strip() or None
        self.base_url = base_url or os.environ.get(
            "LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions"
        )
        self.model_name = model_name or os.environ.get("LLM_MODEL_NAME", "deepseek-chat")
        self.envelopes: Dict[str, PersonaBehaviorEnvelope] = CHIBI_ACTING_ENVELOPES
        self.unlocked_cards_by_player: Dict[str, Set[str]] = {}
        self.ruled_out_red_herrings: Dict[str, Set[str]] = {}
        self.solidified_branches: Dict[str, SolidifiedBranchRecord] = {}
        self.trial_leaderboard: List[TrialIllusionRunScore] = []
        self._seed_default_state()

    @staticmethod
    def _load_dotenv_if_present() -> None:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if not os.path.exists(env_path):
            return
        try:
            with open(env_path, "r", encoding="utf-8") as ef:
                for raw_line in ef:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and v and not os.environ.get(k):
                        os.environ[k] = v
        except Exception:
            pass

    def _call_llm_semantic_ooc_judge(
        self, env: PersonaBehaviorEnvelope, action_text: str
    ) -> Optional[Dict[str, Any]]:
        """第2道语义裁判：当配置了真实 API Key 时，由大模型评估自由短语的 OOC 违和度与纠偏意图"""
        if not self.api_key:
            return None
        sys_prompt = (
            "你是《千古一局：暗合推演》的东汉历史人设 OOC 裁判。请严格对照角色人设包络线，判断玩家输入动作是否违背角色性格、身份地位与时代背景。\n"
            "必须只输出合法 JSON：{\"delta_ooc\": 整数(-8到+85), \"is_corrective_intent\": 布尔值, \"reason\": \"<=40字简短评语\"}"
        )
        user_prompt = (
            f"角色：{env.character_name}（阵营：{env.camp}）\n"
            f"符合人设参考动作：{env.in_character_keywords}\n"
            f"人设禁忌越界动作：{list(env.ooc_taboo_keywords.keys())}\n"
            f"标准纠偏固化目标：{env.solidification_action_key}\n"
            f"玩家实际输入动作：「{action_text}」\n"
            "请评定 delta_ooc（贴合人设给 -6 到 0；轻度试探给 +25 到 +45；严重越级或背离人设给 +60 到 +85）及是否达成合规纠偏意图。"
        )
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 180,
            "response_format": {"type": "json_object"},
        }
        try:
            req = urllib.request.Request(
                self.base_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                raw = body["choices"][0]["message"]["content"]
                cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
                if not m:
                    return None
                parsed = json.loads(m.group(0))
                return {
                    "delta_ooc": max(-10, min(90, int(parsed.get("delta_ooc", 0)))),
                    "is_corrective_intent": bool(parsed.get("is_corrective_intent", False)),
                    "reason": str(parsed.get("reason", ""))[:60],
                }
        except Exception:
            return None

    def _seed_default_state(self) -> None:
        """初始化默认玩家解锁卡池、1 条已固化示范支线与试炼幻境榜单"""
        default_unlocked = {
            cid for cid, env in self.envelopes.items() if env.unlocked_by_default
        }
        self.unlocked_cards_by_player["player_main"] = set(default_unlocked)
        self.ruled_out_red_herrings["player_main"] = {"char_junxu_clerk"}

        # 预置日间试炼幻境排行榜样本（展示不同玩家如何靠排错+演技+固化冲榜争夺夜间正史 Top 1）
        self.trial_leaderboard = [
            TrialIllusionRunScore(
                run_id="trial_run_01",
                player_id="p_chuge",
                player_name="楚歌（记忆碎片流）",
                character_id="char_wulin_granary",
                character_name="乌林粮草官",
                identity_points_spent=1,
                butterfly_points_spent=1,
                restoration_rate=94.0,
                persona_fit_score=92.0,
                clear_efficiency_score=90.0,
                red_herrings_ruled_out=2,
                branches_solidified=1,
                composite_trial_score=104.3,
                qualifies_for_true_slice=True,
                finale_role_assignment="TOP1_SLICE_ACTOR（晋级夜间正史主控）",
            ),
            TrialIllusionRunScore(
                run_id="trial_run_02",
                player_id="p_xiaruoling",
                player_name="夏若凌（蹲守截信流）",
                character_id="char_jianggan",
                character_name="蒋干",
                identity_points_spent=2,
                butterfly_points_spent=2,
                restoration_rate=91.0,
                persona_fit_score=88.0,
                clear_efficiency_score=84.0,
                red_herrings_ruled_out=1,
                branches_solidified=1,
                composite_trial_score=97.2,
                qualifies_for_true_slice=True,
                finale_role_assignment="TOP1_SLICE_ACTOR（晋级夜间正史主控）",
            ),
            TrialIllusionRunScore(
                run_id="trial_run_03",
                player_id="p_zhaohaiping",
                player_name="赵海平（陷阵武卒）",
                character_id="char_caimao",
                character_name="蔡瑁",
                identity_points_spent=2,
                butterfly_points_spent=2,
                restoration_rate=89.0,
                persona_fit_score=86.0,
                clear_efficiency_score=82.0,
                red_herrings_ruled_out=1,
                branches_solidified=1,
                composite_trial_score=94.7,
                qualifies_for_true_slice=True,
                finale_role_assignment="TOP1_SLICE_ACTOR（晋级夜间正史主控）",
            ),
            TrialIllusionRunScore(
                run_id="trial_run_04",
                player_id="p_reckless_09",
                player_name="莽夫探子（OOC被杀）",
                character_id="char_wulin_granary",
                character_name="乌林粮草官",
                identity_points_spent=1,
                butterfly_points_spent=3,
                restoration_rate=20.0,
                persona_fit_score=8.0,
                clear_efficiency_score=15.0,
                red_herrings_ruled_out=0,
                branches_solidified=0,
                composite_trial_score=14.6,
                qualifies_for_true_slice=False,
                finale_role_assignment="EXPEDITION_VANGUARD（未晋级主控·转入终局百人远征兵卒）",
            ),
        ]

    def get_player_unlocked_cards(self, player_id: str = "player_main") -> List[str]:
        if player_id not in self.unlocked_cards_by_player:
            self.unlocked_cards_by_player[player_id] = {
                cid for cid, env in self.envelopes.items() if env.unlocked_by_default
            }
        return sorted(self.unlocked_cards_by_player[player_id])

    def trigger_memory_fragment_unlock(
        self,
        player_id: str,
        actor_character_id: str,
        target_character_id: str,
        interaction_zone: str,
    ) -> Dict[str, Any]:
        """
        机制 1B：【记忆碎片】接触式解锁
        当玩家操控基础角色在目标角色所在舞台发生近距离交互/跟踪时，解锁目标中高层身份卡
        """
        unlocked = set(self.get_player_unlocked_cards(player_id))
        target_env = self.envelopes.get(target_character_id)
        if not target_env:
            return {"unlocked": False, "reason": f"未知目标角色: {target_character_id}"}

        if target_character_id in unlocked:
            return {
                "unlocked": True,
                "already_unlocked": True,
                "target_character_id": target_character_id,
                "target_name": target_env.character_name,
                "message": f"【记忆碎片】角色卡「{target_env.character_name}」此前已解锁。",
            }

        if interaction_zone != target_env.unlock_prerequisite_zone:
            return {
                "unlocked": False,
                "already_unlocked": False,
                "target_character_id": target_character_id,
                "message": (
                    f"【记忆碎片未触发】需操控「{actor_character_id}」前往舞台 `{target_env.unlock_prerequisite_zone}` "
                    f"接触「{target_env.character_name}」，当前舞台为 `{interaction_zone}`。"
                ),
            }

        unlocked.add(target_character_id)
        self.unlocked_cards_by_player[player_id] = unlocked
        return {
            "unlocked": True,
            "already_unlocked": False,
            "target_character_id": target_character_id,
            "target_name": target_env.character_name,
            "identity_point_cost": target_env.identity_point_cost,
            "fragment_clue": target_env.unlock_clue_item,
            "message": (
                f"✨【记忆碎片触发！】你在 `{interaction_zone}` 近距离接触了「{target_env.character_name}」，"
                f"成功获取{target_env.unlock_clue_item}，解锁该角色附身权限（需消耗 {target_env.identity_point_cost} 点身份点）！"
            ),
        }

    def evaluate_acting_and_solidify(
        self,
        player_id: str,
        character_id: str,
        action_text: str,
        current_round: int = 2,
        base_ooc: int = 10,
    ) -> OOCInspectionResult:
        """
        机制 1A + 机制 2 + 机制 3：
        执行盲盒内鬼排错、OOC 违和度三级惩罚校验，以及高契合度下的【身份纠正与支线固化】
        """
        if character_id not in self.envelopes:
            raise ValueError(f"Unknown character_id for acting check: {character_id}")

        env = self.envelopes[character_id]
        delta_ooc = 0
        triggered_taboos: List[str] = []

        # 1. 检测是否命中违背人设的莽夫言行禁忌词
        for taboo_kw, penalty in env.ooc_taboo_keywords.items():
            if taboo_kw in action_text:
                delta_ooc += penalty
                triggered_taboos.append(f"{taboo_kw}(+{penalty} OOC)")

        # 2. 检测是否贴合角色人设关键词（降低违和度）
        matched_in_char = False
        for good_kw in env.in_character_keywords:
            if good_kw in action_text or action_text in good_kw:
                delta_ooc -= 6
                matched_in_char = True

        scoring_mode = "DETERMINISTIC_FALLBACK"
        judge_reason = "关键词前置快筛与规则引擎判定"

        # 2B. 若未命中显性硬禁忌且配置了真实 API Key，则调用 LLM 语义裁判评估自由短语的 OOC 与纠偏意图
        llm_verdict = None
        if not triggered_taboos and self.api_key:
            llm_verdict = self._call_llm_semantic_ooc_judge(env, action_text)
            if llm_verdict is not None:
                scoring_mode = "LLM_SEMANTIC"
                delta_ooc = llm_verdict["delta_ooc"]
                matched_in_char = matched_in_char or llm_verdict["is_corrective_intent"]
                judge_reason = llm_verdict["reason"] or "大模型语义裁判通过"
                if delta_ooc >= 25:
                    triggered_taboos.append(f"LLM语义判定违和(+{delta_ooc} OOC)")

        # 2C. 无 Key 或 LLM 超时兜底：规则短语检测
        if llm_verdict is None and not triggered_taboos and not matched_in_char:
            if any(w in action_text for w in ["盘问", "搜身", "跟踪主帅", "逼供", "直呼其名", "拔剑"]):
                delta_ooc += 38
                triggered_taboos.append("越级盘问试探(+38 OOC)")

        final_ooc = max(0, min(100, base_ooc + delta_ooc))
        fit_score = 100 - final_ooc

        # 3. 判定 OOC 违和度三级惩罚阶梯
        if final_ooc >= 90:
            tier = OOCAlertTier.SHADOW_ASSASSINATED
            wiped = True
            arrested = True
        elif final_ooc >= 70:
            tier = OOCAlertTier.HOUSE_ARREST_RATCHET
            wiped = False
            arrested = True
        elif final_ooc >= 40:
            tier = OOCAlertTier.MILD_SUSPICION
            wiped = False
            arrested = False
        else:
            tier = OOCAlertTier.SAFE_IN_CHARACTER
            wiped = False
            arrested = False

        # 4. 生成盲盒内鬼排错结论
        if env.truth_type == MoleTruthType.NATIVE_RED_HERRING:
            self.ruled_out_red_herrings.setdefault(player_id, set()).add(character_id)
            mole_verdict = env.hidden_truth_explanation
        elif env.truth_type == MoleTruthType.TRUE_TAMPERED_MOLE:
            mole_verdict = env.hidden_truth_explanation
        else:
            mole_verdict = env.hidden_truth_explanation

        # 5. 判定是否达成【身份纠正与支线固化】
        solidified_ok = False
        solidified_note = ""
        is_corrective_match = (
            matched_in_char
            or env.solidification_action_key in action_text
            or any(k in action_text for k in env.in_character_keywords)
        )
        if fit_score >= self.SOLIDIFY_MIN_FIT_SCORE and is_corrective_match and not arrested:
            solidified_ok = True
            solidified_note = env.solidified_script_summary
            self.solidified_branches[character_id] = SolidifiedBranchRecord(
                branch_record_id=f"solid_{character_id}_r{current_round}",
                character_id=character_id,
                character_name=env.character_name,
                solidified_by_player=player_id,
                solidified_on_round=current_round,
                persona_fit_score=fit_score,
                identity_points_refunded_next_round=env.identity_point_cost,
                auto_script_summary=env.solidified_script_summary,
            )

        # 6. 组装反馈提示
        if tier == OOCAlertTier.SHADOW_ASSASSINATED:
            msg = (
                f"💀【致命 OOC 暴露 (违和度 {final_ooc}/100)】「{env.character_name}」做出严重背离人设之举"
                f"（{', '.join(triggered_taboos)}），当夜被校事府暗探/东夷影侍瞬杀灭口！本轮投入的 {env.identity_point_cost} 点身份点全额作废！"
            )
        elif tier == OOCAlertTier.HOUSE_ARREST_RATCHET:
            msg = (
                f"🚨【重度违和·单向棘轮锁死 (违和度 {final_ooc}/100)】「{env.character_name}」言行越界"
                f"（{', '.join(triggered_taboos)}），触发 `EVENT_ABNORMAL_SUBORDINATE` 被主帅停职软禁，丧失后续行动权！"
            )
        elif tier == OOCAlertTier.MILD_SUSPICION:
            msg = (
                f"⚠️【轻度违和警觉 (违和度 {final_ooc}/100，契合度 {fit_score})】同舞台 AI 对「{env.character_name}」产生戒心，"
                f"套话成功率减半且无法达到支线固化门槛（需契合度 >= 85）。"
            )
        elif solidified_ok:
            msg = (
                f"🔒【身份纠正与支线固化达成！（违和度仅 {final_ooc}/100，扮演契合度 {fit_score}）】"
                f"{mole_verdict} {solidified_note}"
            )
        else:
            msg = (
                f"✓【潜伏飙戏通过（违和度 {final_ooc}/100，契合度 {fit_score}）】"
                f"{mole_verdict}"
            )

        return OOCInspectionResult(
            character_id=character_id,
            character_name=env.character_name,
            action_text=action_text,
            base_ooc=base_ooc,
            delta_ooc=delta_ooc,
            final_ooc_suspicion=final_ooc,
            persona_fit_score=fit_score,
            alert_tier=tier,
            triggered_taboos=triggered_taboos,
            is_points_wiped_by_assassin=wiped,
            is_house_arrested=arrested,
            mole_investigation_verdict=mole_verdict,
            solidified_success=solidified_ok,
            solidified_branch_note=solidified_note,
            scoring_mode=scoring_mode,
            llm_judge_reason=judge_reason,
            feedback_message=msg,
        )

    def attempt_break_solidified_branch(
        self,
        character_id: str,
        enemy_has_hard_counter_proof: bool,
        counter_proof_desc: str,
    ) -> Dict[str, Any]:
        """
        机制 3B：固化 vs 反固化对抗
        普通软性流言无法击穿已固化支线；唯有敌方投入 EVENT_HARD_COUNTER_PROOF（硬性物证或物理截断）方可击穿
        """
        rec = self.solidified_branches.get(character_id)
        if not rec:
            return {"broken": False, "reason": f"角色 {character_id} 当前尚无已固化支线。"}

        if not enemy_has_hard_counter_proof:
            return {
                "broken": False,
                "character_id": character_id,
                "reason": (
                    f"【固化防线稳固】敌方仅投喂软性流言（{counter_proof_desc}），"
                    f"无法动摇「{rec.character_name}」已固化的合规支线！"
                ),
            }

        rec.is_broken_by_counter_proof = True
        rec.broken_reason = counter_proof_desc
        return {
            "broken": True,
            "character_id": character_id,
            "reason": (
                f"⚡【反固化击穿！】敌方针对「{rec.character_name}」出示了硬性物证/物理改变（{counter_proof_desc}），"
                f"原固化支线被强制击穿，需在下一轮重新排错！"
            ),
        }

    def compute_trial_illusion_score_and_rank(
        self,
        player_id: str,
        player_name: str,
        character_id: str,
        identity_points_spent: int,
        butterfly_points_spent: int,
        restoration_rate: float,
        persona_fit_score: float,
        clear_efficiency_score: float,
        red_herrings_ruled_out: int = 1,
        branches_solidified: int = 1,
    ) -> TrialIllusionRunScore:
        """
        机制 4：计算日间【试炼幻境】三维综合得分，并实时刷新单角色 Top 1 晋级夜间正史/终局切片资格
        公式：0.40 * 还原度 + 0.35 * 扮演契合度 + 0.25 * 破局效率 + 排错奖励(每个+3分) + 固化奖励(每个+6分)
        """
        env = self.envelopes.get(character_id)
        cname = env.character_name if env else character_id

        base_weighted = (
            0.40 * restoration_rate
            + 0.35 * persona_fit_score
            + 0.25 * clear_efficiency_score
        )
        bonus = (red_herrings_ruled_out * 3.0) + (branches_solidified * 6.0)
        composite = round(base_weighted + bonus, 1)

        new_entry = TrialIllusionRunScore(
            run_id=f"trial_{player_id}_{character_id}",
            player_id=player_id,
            player_name=player_name,
            character_id=character_id,
            character_name=cname,
            identity_points_spent=identity_points_spent,
            butterfly_points_spent=butterfly_points_spent,
            restoration_rate=restoration_rate,
            persona_fit_score=persona_fit_score,
            clear_efficiency_score=clear_efficiency_score,
            red_herrings_ruled_out=red_herrings_ruled_out,
            branches_solidified=branches_solidified,
            composite_trial_score=composite,
        )
        self.trial_leaderboard.append(new_entry)
        self._recompute_top1_slice_qualifiers()
        return new_entry

    def _recompute_top1_slice_qualifiers(self) -> None:
        """按单角色分组排序，唯有各角色第 1 名获得 TOP1_SLICE_ACTOR（晋级夜间正史与终局切片），其余转入百人远征军"""
        self.trial_leaderboard.sort(key=lambda x: x.composite_trial_score, reverse=True)
        seen_top_roles: Set[str] = set()
        for item in self.trial_leaderboard:
            if item.character_id not in seen_top_roles and item.persona_fit_score >= 60.0:
                seen_top_roles.add(item.character_id)
                item.qualifies_for_true_slice = True
                item.finale_role_assignment = "TOP1_SLICE_ACTOR（晋级夜间正史主控 & 终局切片公演）"
            else:
                item.qualifies_for_true_slice = False
                item.finale_role_assignment = "EXPEDITION_VANGUARD（转入终局百人远征兵卒微扰共斗）"

    def export_acting_state_summary(self, player_id: str = "player_main") -> Dict[str, Any]:
        """导出完整盲盒排错、支线固化与试炼幻境选拔状态（供 FastAPI 与前端沙盘同步）"""
        self._recompute_top1_slice_qualifiers()
        return {
            "identity_points_max": 3,
            "unlocked_cards": self.get_player_unlocked_cards(player_id),
            "ruled_out_red_herrings": sorted(self.ruled_out_red_herrings.get(player_id, set())),
            "envelopes": {k: v.dict() for k, v in self.envelopes.items()},
            "solidified_branches": {k: v.dict() for k, v in self.solidified_branches.items()},
            "trial_leaderboard": [t.dict() for t in self.trial_leaderboard],
        }


if __name__ == "__main__":
    print("=========================================================================")
    print("  《千古一局：暗合推演》悬疑演技排错与支线固化引擎自检 (acting_solidifier.py)")
    print("=========================================================================\n")

    engine = ActingSolidifierEngine()

    # 测试 1：盲盒排错——调查鬼祟的「曹营怯战书佐（汪生）」，排除原生贪生怕死烟雾弹并顺水推舟固化
    print("--- [测试 1] 附身鬼祟的「曹营怯战书佐」，排查是否为敌方内鬼 ---")
    res_coward = engine.evaluate_acting_and_solidify(
        player_id="player_main",
        character_id="char_junxu_clerk",
        action_text="借送文书去乌林坞顺路盯梢粮仓",
        current_round=1,
    )
    print(f"  [排错与固化结果]: {res_coward.feedback_message}")
    assert res_coward.solidified_success is True
    assert "char_junxu_clerk" in engine.ruled_out_red_herrings["player_main"]

    # 测试 2：OOC 违和度三级惩罚——附身乌林粮草官却莽撞越级「直接带兵处决水手」或「擅自火烧船队」
    print("\n--- [测试 2] OOC 违和度拦截：附身基层粮草官却做出莽夫越级言行 ---")
    res_ooc_kill = engine.evaluate_acting_and_solidify(
        player_id="player_main",
        character_id="char_wulin_granary",
        action_text="擅自火烧船队并越级闯帐咆哮曹操",
        current_round=2,
    )
    print(f"  [OOC 致命暴露结果]: {res_ooc_kill.feedback_message}")
    assert res_ooc_kill.alert_tier == OOCAlertTier.SHADOW_ASSASSINATED
    assert res_ooc_kill.is_points_wiped_by_assassin is True

    # 测试 3：合规飙戏与支线固化——乌林粮草官按曹营双印规制隔离火船并秘报程昱
    print("\n--- [测试 3] 合规飙戏与支线固化：乌林粮草官按双印规制隔离干草船 ---")
    res_solid = engine.evaluate_acting_and_solidify(
        player_id="player_main",
        character_id="char_wulin_granary",
        action_text="按军需规制分仓隔离并向程昱秘呈油草账册",
        current_round=2,
    )
    print(f"  [支线固化结果]: {res_solid.feedback_message}")
    assert res_solid.solidified_success is True
    assert "char_wulin_granary" in engine.solidified_branches

    # 测试 4：【记忆碎片】近距离接触解锁中高层身份卡「蒋干 (2点)」
    print("\n--- [测试 4] 【记忆碎片】接触式解锁：在曹营大帐接触蒋干解锁 2 点角色卡 ---")
    frag_res = engine.trigger_memory_fragment_unlock(
        player_id="player_main",
        actor_character_id="char_junxu_clerk",
        target_character_id="char_jianggan",
        interaction_zone="caoying_tent",
    )
    print(f"  [解锁反馈]: {frag_res['message']}")
    assert frag_res["unlocked"] is True

    # 测试 5：试炼幻境三维评分与 Top 1 晋级夜间正史/终局历史切片选拔
    print("\n--- [测试 5] 日间试炼幻境刷榜：选拔各角色 Top 1 晋级夜间正史与终局切片 ---")
    new_score = engine.compute_trial_illusion_score_and_rank(
        player_id="player_main",
        player_name="归序者·你",
        character_id="char_wulin_granary",
        identity_points_spent=1,
        butterfly_points_spent=1,
        restoration_rate=98.0,
        persona_fit_score=96.0,
        clear_efficiency_score=95.0,
        red_herrings_ruled_out=2,
        branches_solidified=2,
    )
    for idx, entry in enumerate(engine.trial_leaderboard[:4], start=1):
        print(
            f"  Rank {idx} | {entry.character_name:<6} | {entry.player_name:<14} "
            f"| 试炼总分: {entry.composite_trial_score:>5.1f} | {entry.finale_role_assignment}"
        )
    assert new_score.qualifies_for_true_slice is True
    print("\n[ALL ACTING SOLIDIFIER CHECKS PASSED]")