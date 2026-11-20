# 《千古一局：暗合推演》全周期项目交接文档（HANDOFF.md）

* **当前版本**：`v3.0.0-chibi-guandu-ea`
* **最后更新**：2026-11-20
* **绑定 GitHub 项目看板**：[mmrrjj-dev / Project #1](https://github.com/users/mmrrjj-dev/projects/1) | **私有代码仓库**：[`mmrrjj-dev/qiangu-yiju-mvp`](https://github.com/mmrrjj-dev/qiangu-yiju-mvp)
* **交接范围**：完整设计文档（v3.0）、Python FastAPI 异步服务端（14 个核心模块）、Web 2.5D 战略沙盘前端（`chibi_sandtable_mvp.html`）、双战役（赤壁 + 官渡）18 份 WebP 美术切图、Docker 一键部署编排及全量自检脚本。

---

## 1. 项目一句话定位与核心设计铁律

《千古一局：暗合推演》是一款以历史/经典战役为舞台的**异步多智能体悬疑博弈与暗合推演平台**。任何后续接手开发者都必须严守以下 **5 条架构铁律**（禁止为了图省事而破坏）：

1. **逻辑归代码，演技归 AI（`state_machine.py`）**：大模型无权直接修改角色生死或战役胜负，仅允许输出枚举内的 `EventTag`；心理槽（`0–100`）、单向棘轮（`>= 70` 锁死，仅 `EVENT_HARD_COUNTER_PROOF` 可解）及不可逆事实锚点（`FactAnchor`）100% 由纯 Python 确定性代码裁决。
2. **三道物理隔离防越狱（`era_guard.py` v1.2）**：玩家输入严格限制为「结构化模板 + `<= 15` 字短语槽位」，经 Unicode NFKC 归一化剥离标点空格、同音拆字与化学成分共现拦截后，由史官转译器重写为东汉第三人称事件，永不直达 `RoleAgent`。
3. **小人物不坐牢且不越权**：基层小人物拥有情报分流权、高冷门杠杆（`2.00x–2.20x`）与显式高光时间窗，但延迟硬性裁剪在 `<= 12h`、局部损耗 `<= 20%`、噪点 `<= 1` 条。
4. **盲盒防抄作业前置锁（`mole_seed_rotator.py`）**：同一角色底色锁死不变，但内鬼篡改落点按 `SEED_BATCH_ALPHA / BETA / GAMMA` 动态轮换；未在本局现场采集前置物证 Token 直接跳步排错的，强制判定 `Meta-Gaming` 暴涨 `+65 OOC`。
5. **百人远征渐近阻尼上限（`finale_expedition_engine.py`）**：终局百人流星微扰采用 `tanh(net / 35.0)` 收敛在火势 `±25%`、延迟 `±8h`、心理槽 `±10`，永远不能推翻已锁死的硬性因果锚点。

---

## 2. 仓库目录与全量文件清单

```text
.
├── README.md                           # GitHub 仓库首页说明与快速启动
├── HANDOFF.md                          # 本文档：全周期项目交接与架构铁律
├── CHANGELOG.md                        # v1.0.0 -> v3.0.0 完整版本演进日志
├── RELEASE_CHECKLIST.md                # v3.0.0 发布 28 项门禁核查表（28/28 通过）
├── .gitignore                          # 强制忽略 .env（防 API Key 泄露）、缓存与归档包
├── sync_to_github.sh                   # 一键创建 GitHub 私有仓库并推送同步脚本
├── Dockerfile                          # Python 3.12 + FastAPI 服务端容器镜像
├── docker-compose.yml                  # Nginx (:8080) + FastAPI (:8000) + Redis 7 (:6379)
├── nginx.conf                          # 静态前端托管与 /api/* 反向代理配置
├── docs/
│   ├── game-concept.md                 # 游戏概念文档 v3.0（融合《演技派》与《太真实》四大机制）
│   ├── chibi-mvp-schema.md             # 赤壁 MVP 数据字典与 5 类零穿帮动作原语枚举
│   ├── architecture-adr-001.md         # Web (React+PixiJS+Spine) + FastAPI 架构决策记录
│   ├── chibi-mvp-sprint-plan.md        # Sprint 1-4 全周期路线图与生产三大雷区拆解
│   ├── chibi-art-asset-spec.md         # 2.5D 国风重彩美术资产发包规范（4096×2048 分层图与立绘差分）
│   └── chibi-qa-balance-audit.md       # QA 审查与阻尼天梯数值平衡报告
├── shared-schemas/
│   └── mock_chibi_day2_replay.json     # 夜间 BSP + RoleAgent 导出的 24h 时间轴契约样本
├── client/
│   ├── chibi_sandtable_mvp.html        # 可双击独立运行/联调后端的 2.5D 沙盘与时间轴工作台
│   └── assets/                         # 18 份 WebP 资产（赤壁/官渡双底图 + 17 名角色 128×128 头像）
└── server/
    ├── .env.example                    # API Key 环境变量模板（复制为 .env 后填入密钥）
    ├── requirements.txt                # Python 依赖（pydantic / fastapi / uvicorn）
    ├── state_machine.py                # v1.1 确定性心理状态槽、单向棘轮与小人物上限裁剪
    ├── era_guard.py                    # v1.2 三道防越狱防线（NFKC/同音拆字/配方共现/史官转译）
    ├── role_agent.py                   # v1.2 12 角色底色库、SHA-256 前缀缓存与三级容错
    ├── counterfactual.py               # 夜间反事实 4 分支死局强验算器（0/4 存活才授 1.20x）
    ├── acting_solidifier.py            # v1.1 盲盒排错、记忆碎片解锁、OOC 混合语义裁判与支线固化
    ├── mole_seed_rotator.py            # v1.0 防抄作业动态洗牌、物证前置锁与支线实体互斥仲裁
    ├── finale_expedition_engine.py     # v1.0 Day 5 五日回溯合成器、百人远征聚合与官渡剧本热加载
    ├── director_compiler.py            # v1.2 零穿帮动作原语编译器
    ├── daytime_sandbox.py              # v1.3 日间局部快推沙盒（<=3 人唤醒 + 配额点卡控制）
    ├── nightly_bsp_job.py              # v1.3 夜间舞台分区按需唤醒 + BSP 并发流水线
    ├── nightly_scheduler_worker.py     # Redis 配额/排行榜持久化 + 4 步断点续跑 Worker
    ├── timeline_fork_store.py          # PostgreSQL JSONB DDL + Copy-on-Write 平行历史分叉树
    ├── room_matchmaker.py              # 2-5 人混合开局、AI 自动托管与高光窗口中途空降接管
    └── app_server.py                   # v1.3 统一 FastAPI 入口与 v3.0 阻尼天梯结算引擎
```

---

## 3. 版本演进脉络（v1.0 → v3.0.0）

| 版本 | 核心里程碑 | 解决的关键痛点 |
| :--- | :--- | :--- |
| **v1.0 – v1.1** | 概念架构定稿 + 阻尼天梯平衡热修 | 修复小人物倍率过高（分差从 `18.8x` 收敛至健康的 `2.6x`）与曹操疑心单步瞬秒蔡瑁问题。 |
| **v1.2 – v1.3** | 反事实死局强验算 + AUDIT-03 反混淆 + 全阵容 RoleAgent | 防止玩家靠假死局骗分；拦截 `一·流·二·消` 等插空谐音越狱；打通 SHA-256 Context Caching。 |
| **v2.0.0** | Redis 断点续跑 + PostgreSQL JSONB 分叉树 + 混合空降 + Docker | 支持长线战役分叉读档、超时从 Checkpoint 恢复省 Token、2 人即可开局不卡房。 |
| **v3.0.0** | 融合《我的玩家都是演技派》&《这游戏也太真实了》四大机制 + 双战役美术实装 | 落地盲盒内鬼排错、OOC 语义裁判、跨轮次支线固化、防抄作业动态洗牌、百人远征与赤壁/官渡双图热切换。 |

---

## 4. 本地运行与全量验收命令

### 4.1 体验前端 2.5D 沙盘（零依赖）
直接用浏览器打开 `client/chibi_sandtable_mvp.html`（内置赤壁/官渡重彩底图与 17 名角色头像切图）。

### 4.2 配置真实大模型 API Key 并启动服务端
```bash
cd server
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 或 LLM_API_KEY（不填则自动以 Tier 3 规则引擎运行）
pip install -r requirements.txt
uvicorn app_server:app --reload --port 8000
```

### 4.3 容器化一键部署（Nginx + FastAPI + Redis 7）
```bash
# 确保 server/.env 或当前环境变量已设置 API Key
docker-compose up --build
# 访问 http://localhost:8080 即可自动探测并连接 /api/* 真实后端
```

### 4.4 一键全量回归自检（12 个服务端模块）
```bash
cd server
for f in state_machine.py era_guard.py role_agent.py counterfactual.py acting_solidifier.py mole_seed_rotator.py finale_expedition_engine.py daytime_sandbox.py nightly_scheduler_worker.py timeline_fork_store.py room_matchmaker.py app_server.py; do
  python3 "$f" || exit 1
done
```

---

## 5. 后续接手待办（Next Engineering Priorities）

1. **PixiJS v8 + Spine 2D 独立工程拆分（Sprint 2）**：将 `chibi_sandtable_mvp.html` 中的 Canvas 渲染层拆入 `React 18 + PixiJS` 工程，由美术按 `docs/chibi-art-asset-spec.md` 交付 `4096×2048` 透明分层建筑与 5 表情五官差分图集。
2. **官渡战役专属时间轴与角色快照补齐**：目前官渡已支持底图、6 舞台坐标与 4 名核心角色头像热切换，后续需在 `mock_guandu_day2_replay.json` 中补齐官渡 20 条独立动作原语（许攸夜奔、火烧乌巢等）。
3. **PostgreSQL 真实连接池替换**：`timeline_fork_store.py` 已内置标准 PostgreSQL JSONB DDL 与事务逻辑，上线前将本地 SQLite 兼容驱动切换为 `asyncpg` 连接真实 RDS 实例即可。
