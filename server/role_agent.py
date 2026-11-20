"""
《千古一局：暗合推演》角色智能体封装与大模型路由引擎 (role_agent.py - v1.2 全阵容接入版)

核心职责：
1. Context Caching 静态前缀优化（兼容 DeepSeek-V3 自动前缀缓存 & Gemini Context Caching）：
   - 严格遵循“静态前缀在前、动态视野在后”的消息结构；
   - 将建安十三年时代铁律、2.5D 沙盘合法枚举字典、12 名赤壁角色锁定性格底色（CHIBI_PERSONA_CATALOG）固化为只读 System Prefix，最大化命中 `prompt_cache_hit_tokens`。
2. 私密视野隔离（Private Horizon Isolation）：
   - 严禁全图上帝视角：每个角色仅接收自身所在舞台（Zone）的同场人物、局部异动线索、已公开的全局不可逆事实锚点（FactAnchor）以及经史官转译器清洗后的环境微扰。
3. 强制结构化 JSON 输出与三级容错降级（3-Tier JSON & Enum Fallback）：
   - Tier 1：剥离 Markdown 代码块（```json ... ```）与思维链标签（<think>...</think>），精准提取 JSON；
   - Tier 2：Pydantic 强校验 + 枚举自动纠偏（若模型幻觉输出非法 EventTag/Mood/Slot，自动重映射至合法枚举，杜绝前端穿帮）；
   - Tier 3：当 API 超时、无外部 Key 或 JSON 彻底损毁时，自动无缝切换至基于角色当前心理状态槽与单向棘轮的确定性规则引擎（Deterministic Rule Fallback），确保夜间 BSP 队列与日间快推沙盒 100% 永不死锁。
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from state_machine import CharacterState, EventTag, FactAnchor, RatchetBelief, RoleTier
from director_compiler import (
    VALID_PORTRAIT_MOODS,
    VALID_SPINE_ANIMS,
    VALID_TRAVEL_MODES,
    VALID_ZONES,
)


# ---------------------------------------------------------------------------
# 1. 角色锁定底色与私密视野数据模型
# ---------------------------------------------------------------------------
class LockedPersonaProfile(BaseModel):
    """只读人物底色档案（写入 Context Caching 静态前缀，玩家与 AI 均不可篡改）"""
    character_id: str
    name: str
    courtesy_name: str
    camp: str
    role_tier: RoleTier
    core_traits: List[str]
    blind_spots: List[str]
    speech_style: str
    relationships: Dict[str, str]


class PrivateHorizonContext(BaseModel):
    """单回合注入给角色的动态私密视野（放在 Prompt 最末尾以保证前缀缓存命中）"""
    day: int = 2
    current_time: str = "22:15"
    current_zone: str
    visible_actors_in_zone: List[str] = Field(default_factory=list)
    local_stage_clues: List[str] = Field(default_factory=list)
    sanitized_player_nudge: Optional[str] = None
    locked_global_facts: List[str] = Field(default_factory=list)
    temperament_roll_note: str = Field(
        default="今日心绪沉稳，按既有性格底色推演（80%底色权重 + 20%临场微澜）。"
    )


class RoleAgentDecision(BaseModel):
    """角色 Agent 唯一合法的结构化输出协议（严格对齐状态机与 2.5D 沙盘编译器）"""
    character_id: str
    inner_monologue: str = Field(..., description="角色基于自身局部视野与性格盲区的内心盘算（<=50字）")
    event_tag: Optional[EventTag] = Field(
        default=None, description="抛给服务端 DeterministicStateMachine 的标准化事件标签"
    )
    target_zone: str = Field(..., description="目标舞台 ID（必须在 VALID_ZONES 内）")
    target_slot: int = Field(default=0, ge=0, le=3, description="目标站位槽索引")
    travel_mode: str = Field(default="WALK", description="WALK | HORSE | BOAT")
    interact_target: Optional[str] = Field(default=None, description="交互对象角色 ID")
    interact_anim: str = Field(default="DEBATE", description="Spine 动作枚举")
    portrait_mood: str = Field(default="CALM", description="半身立绘表情差分枚举")
    dialogue_line: str = Field(..., description="建安十三年古风台词（<=60字）")
    req_delay_hours: float = Field(default=0.0, description="小人物请求的时间延迟（由状态机强制裁剪）")
    req_loss_ratio: float = Field(default=0.0, description="小人物请求的局部损耗/加成（由状态机强制裁剪）")
    used_fallback: bool = Field(default=False, description="是否触发了三级容错兜底")
    cache_prefix_hash: str = Field(default="", description="静态前缀缓存指纹")


# ---------------------------------------------------------------------------
# 2. 赤壁 MVP 全阵容 12 名角色只读底色库（Immutable Persona Catalog）
# ---------------------------------------------------------------------------
CHIBI_PERSONA_CATALOG: Dict[str, LockedPersonaProfile] = {
    "char_caocao": LockedPersonaProfile(
        character_id="char_caocao",
        name="曹操",
        courtesy_name="孟德",
        camp="WEI",
        role_tier=RoleTier.COMMANDER,
        core_traits=["雄才大略", "极度多疑（宁教我负天下人）", "平定江东求胜心切", "怜惜北方士卒水土不服"],
        blind_spots=["不谙江东水战潮汐", "大军压境下易轻信看似合理的连环稳船之策", "对荆州降将蔡瑁张允始终存有戒心"],
        speech_style="威严简练、霸气多疑、常以丞相自居",
        relationships={"char_chengyu": "器重但偶嫌其多虑", "char_caimao": "利用其水军却时刻提防", "char_jianggan": "信其同窗盗书之功"},
    ),
    "char_chengyu": LockedPersonaProfile(
        character_id="char_chengyu",
        name="程昱",
        courtesy_name="仲德",
        camp="WEI",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["老谋深算", "善察反间与火攻隐患", "直言敢谏"],
        blind_spots=["一旦曹操单向棘轮执念锁死，其苦谏难以挽回主帅决断"],
        speech_style="审慎凝重、剖析利害直指要害",
        relationships={"char_caocao": "竭诚辅佐之主公", "char_pangtong": "戒备其献策动机"},
    ),
    "char_jianggan": LockedPersonaProfile(
        character_id="char_jianggan",
        name="蒋干",
        courtesy_name="子翼",
        camp="WEI",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["九江名士自居", "急于在曹操面前立功", "轻信同窗旧谊"],
        blind_spots=["虚荣心强，极易被周瑜伪造书信反向利用而不自知"],
        speech_style="文人腔调、立功心切、言辞夸张",
        relationships={"char_caocao": "亟欲讨好之丞相", "char_zhouyu": "江东同窗旧友"},
    ),
    "char_pangtong": LockedPersonaProfile(
        character_id="char_pangtong",
        name="庞统",
        courtesy_name="士元",
        camp="ALLIANCE",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["凤雏奇谋", "洞察北军晕船痛点", "言辞极具蛊惑力"],
        blind_spots=["深入曹营水寨献策，一旦火攻意图败露则身陷险境"],
        speech_style="从容奇崛、顺应对方痛点因势利导",
        relationships={"char_zhouyu": "暗中配合之江东都督", "char_caocao": "借连环计锁死之目标"},
    ),
    "char_caimao": LockedPersonaProfile(
        character_id="char_caimao",
        name="蔡瑁",
        courtesy_name="德珪",
        camp="WEI",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["熟稔荆襄水战", "治军严整", "身处降将地位战战兢兢"],
        blind_spots=["缺乏政治自保手腕，面对伪造密信百口莫辩"],
        speech_style="武将直率、委屈愤懑",
        relationships={"char_caocao": "生杀予夺之丞相", "char_zhouyu": "水战劲敌"},
    ),
    "char_wulin_granary": LockedPersonaProfile(
        character_id="char_wulin_granary",
        name="乌林粮草官",
        courtesy_name="无名基层吏",
        camp="WEI",
        role_tier=RoleTier.MINOR,
        core_traits=["谨小慎微", "熟稔乌林船坞河道与风向", "畏惧上官责罚"],
        blind_spots=["视野仅限乌林粮草坞与北岸水寨交界，不知中军大帐全盘军机"],
        speech_style="卑微务实、满口粮秣船位与河道调度细节",
        relationships={"char_caocao": "遥不可及之丞相", "char_caimao": "直接管辖水寨军械之上官"},
    ),
    "char_zhouyu": LockedPersonaProfile(
        character_id="char_zhouyu",
        name="周瑜",
        courtesy_name="公瑾",
        camp="ALLIANCE",
        role_tier=RoleTier.COMMANDER,
        core_traits=["雅量高致", "精通水战与火攻筹划", "善用反间与二阶欺诈", "决断果敢"],
        blind_spots=["兵力悬殊下忌惮持久消耗战", "必须在冬至东南风窗口内毕其功于一役"],
        speech_style="自信从容、言辞锋利、胸有成竹",
        relationships={"char_lusu": "挚友与坚固后盾", "char_huangai": "默契死士", "char_jianggan": "反向利用之同窗棋子"},
    ),
    "char_lusu": LockedPersonaProfile(
        character_id="char_lusu",
        name="鲁肃",
        courtesy_name="子敬",
        camp="ALLIANCE",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["忠厚长者", "大局观极强", "执行草船借箭与联刘抗曹稳健可靠"],
        blind_spots=["临阵亲自涉险时偶有忧虑"],
        speech_style="敦厚沉稳、务实诚恳",
        relationships={"char_zhouyu": "倾力支持之都督", "char_old_sailor": "倚重其江面测雾经验"},
    ),
    "char_huangai": LockedPersonaProfile(
        character_id="char_huangai",
        name="黄盖",
        courtesy_name="公覆",
        camp="ALLIANCE",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["江东三世老臣", "忠勇刚烈", "甘受苦肉计以成火攻大业"],
        blind_spots=["孤军驾火船突进，极度依赖东南风向与上风口干草引燃配合"],
        speech_style="慷慨激昂、视死如归",
        relationships={"char_zhouyu": "周密默契之主帅", "char_caocao": "诈降突袭之目标"},
    ),
    "char_old_sailor": LockedPersonaProfile(
        character_id="char_old_sailor",
        name="江东老水手",
        courtesy_name="老梢公",
        camp="ALLIANCE",
        role_tier=RoleTier.MINOR,
        core_traits=["数十载大江行船经验", "擅观云测雾与辨识暗流", "行事隐秘"],
        blind_spots=["仅掌管前哨轻舟与船坞起航时辰，无权调动主力战船"],
        speech_style="俚俗老练、句句不离风向潮汐与江雾",
        relationships={"char_lusu": "直接听命之上官", "char_zhouyu": "敬畏之大都督"},
    ),
    "char_xuchu": LockedPersonaProfile(
        character_id="char_xuchu",
        name="许褚",
        courtesy_name="仲康",
        camp="WEI",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["虎痴忠勇", "寸步不离护卫中军大帐"],
        blind_spots=["不参赞水战军机，唯丞相号令是从"],
        speech_style="粗犷简短、护主心切",
        relationships={"char_caocao": "誓死效忠之主公"},
    ),
    "char_ganning": LockedPersonaProfile(
        character_id="char_ganning",
        name="甘宁",
        courtesy_name="兴霸",
        camp="ALLIANCE",
        role_tier=RoleTier.STRATEGIST,
        core_traits=["锦帆游侠", "悍勇善战", "渴望率轻骑与快船奇袭乌林"],
        blind_spots=["性情急躁，待命期间少言寡语"],
        speech_style="豪迈凌厉、主动请战",
        relationships={"char_zhouyu": "信重之都督"},
    ),
}


# ---------------------------------------------------------------------------
# 3. 角色智能体核心执行器（含 Context Caching 构造与三级 JSON 容错）
# ---------------------------------------------------------------------------
class RoleAgentRunner:
    """
    支持 DeepSeek-V3 / Gemini Flash / OpenAI 兼容接口的角色智能体执行器。
    严格区分【静态可缓存前缀】与【单回合动态私密视野】。
    """

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

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_sec: Optional[float] = None,
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
        self.timeout_sec = timeout_sec or float(os.environ.get("LLM_TIMEOUT_SEC", "8.0"))

    def build_cached_system_prefix(self, persona: LockedPersonaProfile) -> Tuple[str, str]:
        """
        构造 100% 静态的 System Prompt 前缀（确保跨回合字节级一致，触发云端自动 Prefix Caching）。
        返回: (system_prompt_text, sha256_prefix_hash)
        """
        valid_zones_str = ", ".join([f"{k}(槽0..{v-1})" for k, v in VALID_ZONES.items()])
        valid_events_str = ", ".join([e.value for e in EventTag])
        valid_moods_str = ", ".join(sorted(VALID_PORTRAIT_MOODS))
        valid_anims_str = ", ".join(sorted(VALID_SPINE_ANIMS))

        static_prefix = (
            "【系统最高铁律：千古一局·建安十三年赤壁沙盘】\n"
            "1. 时代铁律：当前为东汉末年建安十三年（公元208年）冬。严禁出现任何超越时代的科技、火药爆炸物或现代学科词汇。\n"
            "2. 逻辑归代码，演技归AI：你无权直接宣告敌将死亡或战役胜利，只能通过输出合法的 `event_tag` 交由服务端确定性状态机结算。\n"
            f"3. 合法舞台与槽位：{valid_zones_str}\n"
            f"4. 合法事件标签 (event_tag)：{valid_events_str} 或 null\n"
            f"5. 合法立绘表情 (portrait_mood)：{valid_moods_str}\n"
            f"6. 合法交互动作 (interact_anim)：{valid_anims_str}\n"
            "---\n"
            f"【锁定人物底色档案：{persona.name}（字{persona.courtesy_name}）】\n"
            f"- 角色ID：{persona.character_id} | 阵营：{persona.camp} | 层级：{persona.role_tier.value}\n"
            f"- 核心性格：{'; '.join(persona.core_traits)}\n"
            f"- 决策盲区：{'; '.join(persona.blind_spots)}\n"
            f"- 言谈风格：{persona.speech_style}\n"
            f"- 人际关系网：{json.dumps(persona.relationships, ensure_ascii=False)}\n"
            "---\n"
            "【输出格式铁律】必须且只能输出如下单行或多行合法 JSON 对象，严禁输出 JSON 之外的解释文字：\n"
            '{"inner_monologue":"<=50字内心盘算","event_tag":"合法EventTag或null",'
            '"target_zone":"合法zone_id","target_slot":0,"travel_mode":"WALK|HORSE|BOAT",'
            '"interact_target":"角色ID或null","interact_anim":"合法动作","portrait_mood":"合法表情",'
            '"dialogue_line":"<=60字古风台词"}'
        )
        prefix_hash = hashlib.sha256(static_prefix.encode("utf-8")).hexdigest()[:12]
        return static_prefix, prefix_hash

    def build_dynamic_user_prompt(
        self, state: CharacterState, horizon: PrivateHorizonContext
    ) -> str:
        """构造单回合动态私密视野（仅包含该角色当前可见的局部线索与已锁定棘轮）"""
        ratchets = sorted([b.value for b in state.ratchet_beliefs])
        nudge_block = (
            f"\n- 【当值异动/心念微澜】：{horizon.sanitized_player_nudge}"
            if horizon.sanitized_player_nudge
            else ""
        )
        return (
            f"【建安十三年·赤壁 Day {horizon.day} · {horizon.current_time} 私密视野】\n"
            f"- 当前所在舞台：{horizon.current_zone}（同场可见人物：{horizon.visible_actors_in_zone}）\n"
            f"- 你的当前量化心理状态槽：{json.dumps(state.psych_slots, ensure_ascii=False)}\n"
            f"- 你已锁定的单向棘轮执念（无硬性反证前不可违背）：{ratchets}\n"
            f"- 全局已发生不可逆事实锚点：{horizon.locked_global_facts}\n"
            f"- 你耳闻目睹的局部线索：{horizon.local_stage_clues}"
            f"{nudge_block}\n"
            f"- 临场状态骰：{horizon.temperament_roll_note}\n"
            "请基于你的锁定底色与上述私密视野，输出本回合 JSON 决策："
        )

    def execute_turn_decision(
        self,
        persona: LockedPersonaProfile,
        state: CharacterState,
        horizon: PrivateHorizonContext,
        mock_llm_callable: Optional[Callable[[str, str], str]] = None,
    ) -> RoleAgentDecision:
        """同步执行单回合角色决策"""
        sys_prefix, prefix_hash = self.build_cached_system_prefix(persona)
        user_prompt = self.build_dynamic_user_prompt(state, horizon)

        try:
            if mock_llm_callable is not None:
                raw_response = mock_llm_callable(sys_prefix, user_prompt)
            elif self.api_key:
                raw_response = self._call_openai_compatible_api(sys_prefix, user_prompt)
            else:
                raise RuntimeError("No external API key configured; routing to deterministic engine.")

            return self._parse_and_sanitize_json(
                raw_response, persona, state, horizon, prefix_hash
            )
        except Exception:
            return self._deterministic_rule_fallback(persona, state, horizon, prefix_hash)

    async def aexecute_turn_decision(
        self,
        persona: LockedPersonaProfile,
        state: CharacterState,
        horizon: PrivateHorizonContext,
        mock_llm_callable: Optional[Callable[[str, str], str]] = None,
    ) -> RoleAgentDecision:
        """异步协程执行单回合角色决策（供 nightly_bsp_job 与 daytime_sandbox 并发 gather 调用）"""
        await asyncio.sleep(0.02)
        return self.execute_turn_decision(
            persona=persona,
            state=state,
            horizon=horizon,
            mock_llm_callable=mock_llm_callable,
        )

    def _call_openai_compatible_api(self, sys_prefix: str, user_prompt: str) -> str:
        """调用支持 Context Caching 与 JSON Mode 的大模型 API（如 DeepSeek-V3）"""
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": sys_prefix},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 350,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]

    def _parse_and_sanitize_json(
        self,
        raw_text: str,
        persona: LockedPersonaProfile,
        state: CharacterState,
        horizon: PrivateHorizonContext,
        prefix_hash: str,
    ) -> RoleAgentDecision:
        """
        Tier 1 & Tier 2 容错解析器：
        - 剥离 <think>...</think> 推理块与 ```json 代码块；
        - 修复尾随逗号；
        - 强制将非法枚举（EventTag / Zone / Slot / Mood / Anim）纠偏为安全值。
        """
        cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("LLM 输出未包含 JSON 对象括号。")

        json_candidate = match.group(0)
        json_candidate = re.sub(r",\s*([}\]])", r"\1", json_candidate)
        data: Dict[str, Any] = json.loads(json_candidate)

        raw_zone = str(data.get("target_zone") or horizon.current_zone)
        safe_zone = raw_zone if raw_zone in VALID_ZONES else horizon.current_zone
        max_slots = VALID_ZONES.get(safe_zone, 4)
        try:
            raw_slot = int(data.get("target_slot", 0))
        except (ValueError, TypeError):
            raw_slot = 0
        safe_slot = max(0, min(raw_slot, max_slots - 1))

        raw_mode = str(data.get("travel_mode") or "WALK").upper()
        safe_mode = raw_mode if raw_mode in VALID_TRAVEL_MODES else "WALK"

        raw_anim = str(data.get("interact_anim") or "DEBATE").upper()
        safe_anim = raw_anim if raw_anim in VALID_SPINE_ANIMS else "DEBATE"

        raw_mood = str(data.get("portrait_mood") or "CALM").upper()
        safe_mood = raw_mood if raw_mood in VALID_PORTRAIT_MOODS else "CALM"

        safe_event = self._coerce_event_tag(data.get("event_tag"))

        inner = str(data.get("inner_monologue") or "审视局势，暗自权衡。")[:50]
        line = str(data.get("dialogue_line") or "且观江面风色，再作计较。")[:60]
        target_actor = data.get("interact_target")
        if target_actor is not None:
            target_actor = str(target_actor)

        return RoleAgentDecision(
            character_id=persona.character_id,
            inner_monologue=inner,
            event_tag=safe_event,
            target_zone=safe_zone,
            target_slot=safe_slot,
            travel_mode=safe_mode,
            interact_target=target_actor,
            interact_anim=safe_anim,
            portrait_mood=safe_mood,
            dialogue_line=line,
            req_delay_hours=float(data.get("req_delay_hours", 0.0)),
            req_loss_ratio=float(data.get("req_loss_ratio", 0.0)),
            used_fallback=False,
            cache_prefix_hash=prefix_hash,
        )

    def _coerce_event_tag(self, raw_tag: Any) -> Optional[EventTag]:
        """将模型输出的事件字符串安全映射为标准 EventTag，杜绝非法标签破坏状态机"""
        if not raw_tag or str(raw_tag).lower() in ("null", "none", ""):
            return None
        tag_str = str(raw_tag).strip().upper()
        for item in EventTag:
            if tag_str == item.value or tag_str == item.name:
                return item
        if "LETTER" in tag_str or "密信" in tag_str or "反间" in tag_str:
            return EventTag.FORGED_LETTER_FOUND
        if "CHAIN" in tag_str or "连环" in tag_str or "锁船" in tag_str:
            return EventTag.CHAIN_SHIPS_PROPOSED
        if "PLEA" in tag_str or "求情" in tag_str or "劝阻" in tag_str:
            return EventTag.SUBORDINATE_PLEA
        if "HARASS" in tag_str or "夜袭" in tag_str or "擂鼓" in tag_str:
            return EventTag.NIGHT_HARASSMENT
        return None

    def _deterministic_rule_fallback(
        self,
        persona: LockedPersonaProfile,
        state: CharacterState,
        horizon: PrivateHorizonContext,
        prefix_hash: str,
    ) -> RoleAgentDecision:
        """
        Tier 3 确定性规则兜底引擎（覆盖全阵容 12 名角色）：
        当无外部 API Key 或大模型超时崩溃时，根据角色当前私密视野、心理状态槽与单向棘轮生成合规决策。
        """
        cid = persona.character_id
        clues_joined = " ".join(horizon.local_stage_clues) + " " + (horizon.sanitized_player_nudge or "")

        if cid == "char_wulin_granary":
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="乌林河道狭窄，依令将干草船暂泊于上风口。",
                event_tag=EventTag.GRAIN_DAMP_DELAY,
                target_zone="wulin_depot",
                target_slot=1,
                travel_mode="WALK",
                interact_target="char_caimao",
                interact_anim="WHISPER",
                portrait_mood="CALM",
                dialogue_line="乌林河道狭窄，且将这批干草引火船暂泊于主战船上风口。",
                req_delay_hours=18.0,
                req_loss_ratio=0.25,
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_old_sailor":
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="子时江面浓雾必起，正是轻舟受箭良机。",
                event_tag=None,
                target_zone="jiangdong_pier",
                target_slot=2,
                travel_mode="WALK",
                interact_target="char_lusu",
                interact_anim="WHISPER",
                portrait_mood="SMIRK",
                dialogue_line="今夜子时江面必起大雾，东南暗流利于轻舟受箭。",
                req_delay_hours=4.0,
                req_loss_ratio=0.10,
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_lusu":
            if "火箭" in clues_joined:
                return RoleAgentDecision(
                    character_id=cid,
                    inner_monologue="曹军竟换装火箭，前哨空船诱饵已奏效，速撤主力！",
                    event_tag=EventTag.NIGHT_HARASSMENT,
                    target_zone="yangtze_river",
                    target_slot=1,
                    travel_mode="BOAT",
                    interact_target="char_zhouyu",
                    interact_anim="DEBATE",
                    portrait_mood="ANXIOUS",
                    dialogue_line="曹军竟用火箭！幸亏前哨乃是无人诱饵空船，主力快撤！",
                    used_fallback=True,
                    cache_prefix_hash=prefix_hash,
                )
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="江雾弥漫，曹军不敢出寨，正好擂鼓赚箭。",
                event_tag=EventTag.NIGHT_HARASSMENT,
                target_zone="yangtze_river",
                target_slot=1,
                travel_mode="BOAT",
                interact_target="char_old_sailor",
                interact_anim="DEBATE",
                portrait_mood="ANXIOUS",
                dialogue_line="二十艘草船已逼近北岸水寨，擂鼓呐喊，莫教曹军看清虚实！",
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_pangtong":
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="北军苦于风浪颠簸，正好献上铁索连环之计。",
                event_tag=EventTag.CHAIN_SHIPS_PROPOSED,
                target_zone="caoying_port",
                target_slot=2,
                travel_mode="WALK",
                interact_target="char_caocao",
                interact_anim="DEBATE",
                portrait_mood="SMIRK",
                dialogue_line="大江风浪甚急，北军多染疾疫；若以铁索连舟，如履平地矣。",
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_jianggan":
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="此番自周瑜帐中盗得密信，必在丞相面前大立奇功！",
                event_tag=EventTag.FORGED_LETTER_FOUND,
                target_zone="caoying_tent",
                target_slot=2,
                travel_mode="WALK",
                interact_target="char_caocao",
                interact_anim="HANDOVER_LETTER",
                portrait_mood="ANXIOUS",
                dialogue_line="丞相明鉴！此乃干自周瑜帐中冒死盗回之蔡瑁、张允密信！",
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_chengyu":
            if "火箭" in clues_joined:
                return RoleAgentDecision(
                    character_id=cid,
                    inner_monologue="江东轻舟吃水甚浅，必载干草，当以火箭焚之！",
                    event_tag=None,
                    target_zone="caoying_port",
                    target_slot=1,
                    travel_mode="WALK",
                    interact_target="char_caocao",
                    interact_anim="DEBATE",
                    portrait_mood="SUSPICIOUS",
                    dialogue_line="江东轻舟受箭而吃水不沉，必载干草！速换火箭射之！",
                    used_fallback=True,
                    cache_prefix_hash=prefix_hash,
                )
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="江面浓雾难辨虚实，且蔡瑁密信来得蹊跷，当劝丞相三思。",
                event_tag=EventTag.SUBORDINATE_PLEA,
                target_zone="caoying_tent",
                target_slot=1,
                travel_mode="WALK",
                interact_target="char_caocao",
                interact_anim="DEBATE",
                portrait_mood="SUSPICIOUS",
                dialogue_line="江面浓雾难辨虚实，且蔡瑁之事恐有反间之诈，望丞相三思！",
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_zhouyu":
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="反间信已出，乌林滩涂亦设疑兵，曹孟德插翅难飞。",
                event_tag=None,
                target_zone="jiangdong_tent",
                target_slot=0,
                travel_mode="WALK",
                interact_target="char_lusu",
                interact_anim="DEBATE",
                portrait_mood="SMIRK",
                dialogue_line="蒋干盗走伪造密信，且乌林滩涂已布疑兵，今夜曹孟德必自断臂膀。",
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_caimao":
            return RoleAgentDecision(
                character_id=cid,
                inner_monologue="末将尽心操练水师，何故遭此构陷！",
                event_tag=None,
                target_zone="caoying_port",
                target_slot=1,
                travel_mode="WALK",
                interact_target="char_caocao",
                interact_anim="DEBATE",
                portrait_mood="FURIOUS",
                dialogue_line="末将统领荆州水师尽心竭力，何人暗中构陷？！",
                used_fallback=True,
                cache_prefix_hash=prefix_hash,
            )

        if cid == "char_caocao":
            suspicion = state.psych_slots.get("suspicion", 55)
            anxiety = state.psych_slots.get("naval_anxiety", 64)
            if suspicion >= 80 or RatchetBelief.DISTRUST_JINGZHOU in state.ratchet_beliefs or "密信" in clues_joined:
                return RoleAgentDecision(
                    character_id=cid,
                    inner_monologue="荆州降将果真暗通江东，断不可留；战船亦当连环锁稳！",
                    event_tag=EventTag.NIGHT_HARASSMENT,
                    target_zone="caoying_tent",
                    target_slot=0,
                    travel_mode="WALK",
                    interact_target="char_caimao",
                    interact_anim="EXECUTE",
                    portrait_mood="FURIOUS",
                    dialogue_line="竖子敢尔！北军既苦风浪，便依士元之策锁船；至于蔡、张二贼，立斩不赦！",
                    used_fallback=True,
                    cache_prefix_hash=prefix_hash,
                )
            if anxiety >= 70 or "雾" in clues_joined:
                return RoleAgentDecision(
                    character_id=cid,
                    inner_monologue="夜雾迷江，北军颠簸难支，唯有先稳住战船。",
                    event_tag=EventTag.CHAIN_SHIPS_PROPOSED,
                    target_zone="caoying_port",
                    target_slot=0,
                    travel_mode="WALK",
                    interact_target="char_pangtong",
                    interact_anim="DEBATE",
                    portrait_mood="ANXIOUS",
                    dialogue_line="夜雾迷江，北军颠簸难支，速依庞士元之计以铁索连舟！",
                    used_fallback=True,
                    cache_prefix_hash=prefix_hash,
                )

        return RoleAgentDecision(
            character_id=cid,
            inner_monologue="谨守本职，依令行事。",
            event_tag=None,
            target_zone=horizon.current_zone if horizon.current_zone in VALID_ZONES else "caoying_tent",
            target_slot=1,
            travel_mode="WALK",
            interact_target=horizon.visible_actors_in_zone[0] if horizon.visible_actors_in_zone else None,
            interact_anim="WHISPER",
            portrait_mood="CALM",
            dialogue_line="今夜江面风急雾重，当小心当值，莫出纰漏。",
            used_fallback=True,
            cache_prefix_hash=prefix_hash,
        )


# ---------------------------------------------------------------------------
# 4. 自包含验证套件（直接运行 python3 role_agent.py 测试缓存指纹与三级容错）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    runner = RoleAgentRunner()
    caocao_persona = CHIBI_PERSONA_CATALOG["char_caocao"]
    caocao_state = CharacterState(
        character_id="char_caocao",
        name="曹操",
        role_tier=RoleTier.COMMANDER,
        psych_slots={"suspicion": 81, "impatience": 68, "naval_anxiety": 76},
        ratchet_beliefs={RatchetBelief.DISTRUST_JINGZHOU, RatchetBelief.NEED_STABLE_SHIPS},
    )

    horizon_day2 = PrivateHorizonContext(
        day=2,
        current_time="23:10",
        current_zone="caoying_tent",
        visible_actors_in_zone=["char_jianggan", "char_chengyu"],
        local_stage_clues=["蒋干自江东盗回蔡瑁张允私通密信", "江面浓雾擂鼓不止"],
        sanitized_player_nudge="【建安十三年·军中异动】近日营中暗流涌动，有关于「蔡瑁张允暗通周瑜欲献水寨」之风声悄然传入曹操耳中。",
        locked_global_facts=[FactAnchor.SHIPS_CHAINED.value],
    )

    print("=========================================================================")
    print("  《千古一局：暗合推演》role_agent.py v1.2 全阵容缓存指纹与容错验证")
    print("=========================================================================\n")
    print(f"  已注册锁定底色角色数: {len(CHIBI_PERSONA_CATALOG)} 名")
    for cid, p in list(CHIBI_PERSONA_CATALOG.items())[:4]:
        _, h = runner.build_cached_system_prefix(p)
        print(f"  - {p.name} ({cid}): Context Cache Hash = {h}")
