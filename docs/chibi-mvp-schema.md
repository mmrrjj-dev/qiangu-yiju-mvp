# 《赤壁之战》MVP 核心数据结构与配置字典

本文档将《千古一局：暗合推演》v2.0 概念拆解为可直接落库（PostgreSQL / JSONB）或映射为 TypeScript / Python Pydantic 模型的结构化数据表。

## 1. 角色档案与心理状态槽（`character_state`）

将角色拆分为**只读底色（Immutable Core）**与**状态机槽位（Mutable State Slots）**。AI 只能读取底色并输出事件标签，无权直接修改数值。

### 1.1 数据结构定义

| 字段名 | 类型 | 权限 | 说明 | 
| ----- | ----- | ----- | ----- | 
| `character_id` | `string` | 只读 | 唯一标识，如 `char_caocao`、`char_wulin_granary` | 
| `role_tier` | `enum` | 只读 | `COMMANDER`（主帅）/ `STRATEGIST`（谋士）/ `MINOR`（小人物） | 
| `butterfly_multiplier` | `float` | 只读 | 榜单冷门杠杆倍率（主帅 `1.0`，小人物 `3.0 - 8.0`） | 
| `active_window_days` | `int[]` | 只读 | 小人物高光活跃日，如 `[3, 4]` | 
| `locked_persona` | `jsonb` | 只读 | 锁定的性格、智力层级、价值观与人际关系网 | 
| `psych_slots` | `jsonb` | 代码读写 | 量化心理状态槽（`0-100`），如疑心、急躁度、水战焦虑 | 
| `ratchet_beliefs` | `string[]` | 单向棘轮 | 已锁定的认知锚点，无硬反证时只升不降 | 
| `location` | `jsonb` | 状态机读写 | 当前所在舞台与站位槽位 `{ "zone_id": "caoying_tent", "slot": 1 }` | 

### 1.2 实例对照：主帅 vs 小人物

```
{
  "character_id": "char_caocao",
  "role_tier": "COMMANDER",
  "butterfly_multiplier": 1.0,
  "locked_persona": {
    "traits": ["雄才大略", "极度多疑", "求胜心切"],
    "blind_spots": ["不谙水战", "轻视江东兵力"],
    "relationships": { "char_chengyu": "信任", "char_caimao": "戒备利用" }
  },
  "psych_slots": {
    "suspicion": 65,
    "impatience": 55,
    "naval_anxiety": 72
  },
  "ratchet_beliefs": ["北军不习水战，需稳固船阵"]
}

```

```
{
  "character_id": "char_wulin_granary",
  "role_tier": "MINOR",
  "butterfly_multiplier": 6.0,
  "active_window_days": [3, 4],
  "influence_cap": {
    "max_delay_hours": 12,
    "max_loss_ratio": 0.2,
    "max_intel_noise": 1
  },
  "location": { "zone_id": "wulin_depot", "slot": 2 }
}

```

### 1.3 不可逆阈值规则表（`state_threshold_rules`）

| 规则 ID | 目标角色 | 触发条件（代码判定） | 锁定结果（写入全局事实锚点表） | 
| ----- | ----- | ----- | ----- | 
| `RULE_KILL_CAIMAO` | `char_caocao` | `suspicion >= 80` 且收到反间事件 | `FACT_CAIMAO_EXECUTED`（曹营水军统帅空缺） | 
| `RULE_LOCK_SHIPS` | `char_caocao` | `naval_anxiety >= 75` 且 `ratchet_beliefs` 含“需稳固船阵” | `FACT_SHIPS_CHAINED`（战船铁索锁死） | 
| `RULE_MORALE_COLLAPSE` | `char_caocao` | `naval_anxiety >= 90` 且未锁船 | `FACT_NORTH_ARMY_ROUT`（北军晕船未战先溃） | 

## 2. 暗棋战术库与线索外泄表（`hidden_plot_catalog`）

限定每阶段可选暗棋，保证敌方可通过模糊线索进行有限空间推理。

| `plot_id` | 阵营 / 适用层级 | 蝴蝶点数 | 向敌方外泄的模糊线索模板 | 克制关系 / 碰撞效果 | 
| ----- | ----- | ----- | ----- | ----- | 
| `PLOT_CAOCHUAN_ARROWS` | 孙刘 / 谋士 | `3` | “江东连日收购干草青布，夜调二十艘空船” | 若敌方出 `常规万箭齐发` → 孙刘+10万箭；若敌方出 `火箭齐射` → 草船全毁 | 
| `PLOT_DECOY_GRASS_BOAT` | 孙刘 / 谋士 | `4` | “江东连日收购干草青布，夜调二十艘空船”（同上诱饵） | 若敌方出 `火箭齐射` → 诱骗敌方消耗全部火油储备，真精锐侧翼突袭成功 | 
| `PLOT_DELAY_GRAIN_POSITION` | 曹营或内应 / 小人物 | `2` | 极隐蔽（仅当敌方投入高额侦察点才暴露“乌林河道船位微调”） | 干草船停靠主战船上风口，火攻蔓延速度 `+20%` | 
| `PLOT_SPLIT_CAMP_ROTATION` | 曹营 / 主帅 | `3` | “曹营水寨近岸搭建大量临时营帐” | 绕开“铁索连舟”二选一死局，但进攻节奏延后 `2` 回合 | 

## 3. 2.5D 沙盘动作原语枚举（`action_primitives_enum`）

夜间推演引擎最终只允许输出以下 5 类标准原语，驱动客户端 Spine 骨骼动画与立绘演出，彻底杜绝穿帮：

| 原语类型 | 枚举参数 | 客户端表现 | 
| ----- | ----- | ----- | 
| `MOVE` | `actor_id`, `from_zone`, `to_zone`, `slot_idx`, `travel_mode`: `WALK | HORSE | BOAT` | 角色按烘焙寻路网移动至目标地标指定站位槽 | 
| `INTERACT` | `actor_a`, `actor_b`, `anim`: `WHISPER | DEBATE | HANDOVER_LETTER | DRAW_SWORD | EXECUTE` | 两人面对面播放对应的 Spine 通用动作动画 | 
| `EMOTE` | `actor_id`, `portrait_mood`: `CALM | SUSPICIOUS | FURIOUS | SMIRK | ANXIOUS`, `line_text` | 弹出高精度半身立绘差分与简短台词气泡 | 
| `VFX` | `zone_id`, `effect`: `HEAVY_FOG | FIRE_SPREAD | WAR_DRUMS | RAIN` | 在指定沙盘区域叠加环境粒子特效 | 
| `ANCHOR_LOCK` | `fact_id`, `summary` | 时间轴上打下红色高光因果标记，写入全局只读事实表 | 

### 演出时间轴数据包示例（Day 2 夜间片段）

```
{
  "day": 2,
  "timeline_events": [
    { "t": "22:00", "cmd": "VFX", "zone_id": "yangtze_river", "effect": "HEAVY_FOG" },
    { "t": "22:30", "cmd": "MOVE", "actor_id": "char_lusu", "to_zone": "jiangdong_pier", "slot_idx": 1, "travel_mode": "BOAT" },
    { "t": "23:15", "cmd": "INTERACT", "actor_a": "char_caocao", "actor_b": "char_chengyu", "anim": "DEBATE" },
    { "t": "23:20", "cmd": "EMOTE", "actor_id": "char_caocao", "portrait_mood": "SUSPICIOUS", "line_text": "大雾弥江，莫非周郎欲趁夜劫寨？" }
  ]
}

```

## 4. 时代铁律白名单词库与防越狱规则（`era_whitelist_config`）

玩家只能通过“下拉模板 + 15 字内短语槽位”输入，且必须经过白名单检索与史官转译器清洗。

| 类别 | 允许白名单示例（东汉末年合规） | 拦截黑名单概念（秒拒） | 擦边球清洗示例（史官转译器） | 
| ----- | ----- | ----- | ----- | 
| **军械与火攻物** | 猛火油、干草、苇席、硫磺引火物、火箭、铁索、蒙冲斗舰 | 黑火药配比（一硫二硝三木炭）、炸药包、热气球、望远镜 | 玩家填“用配比火药炸船” → **直接拦截打回** | 
| **气象与侦察** | 观云测风、听水声、派斥候哨船、探查灶烟、拦截驿卒 | 气象卫星、概率统计、摩尔斯电码、信号灯密码 | 玩家填“用科学方法测东南风” → 转译为“请江边老水手观云象测风势” | 
| **心理与谋略** | 反间计、苦肉计、借刀杀人、虚张声势、示弱诱敌 | 博弈论、纳什均衡、PUA、沉没成本 | 玩家填“利用对手沉没成本逼他锁船” → 转译为“传言北军久耗江面，不连舟则士气尽散” | 
