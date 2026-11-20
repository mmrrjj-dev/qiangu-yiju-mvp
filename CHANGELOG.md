# Changelog (`CHANGELOG.md`)

本项目遵循语义化版本规范（Semantic Versioning），记录《千古一局：暗合推演》从赤壁 MVP v1.0 到融合悬疑演技排错、防抄作业洗牌与百人终局远征的 v3.0.0 全周期演进。

## \[v3.0.0\] - 2026-11-19 — 悬疑演技排错、四层重彩视觉实装与 Sprint 4 终局远征合龙

### Added（新增）

* **悬疑演技排错与支线固化引擎（`acting_solidifier.py` v1.0）**：
  * 融合[《我的玩家都是演技派》](https://www.qidian.com/book/1033210790/)（第 43、64、65、79 章）与[《这游戏也太真实了》](https://www.qidian.com/book/1029391348/)四大机制。
  * 实现**盲盒内鬼排错与原生烟雾弹 NPC**（区分 `TRUE_TAMPERED_MOLE` 与 `NATIVE_RED_HERRING`）、**【记忆碎片】接触式解锁**、**OOC 违和度三级惩罚**（`40–69` 视野收窄、`70–89` 软禁棘轮、`>=90` 影侍瞬杀）及**跨轮次身份纠正与支线固化**（契合度 `>=85` 自动锁入下轮免身份点复现）。
  * 实现日间**试炼幻境三维评分与 Top 1 晋级夜间正史/终局切片选拔**。
* **防抄作业动态洗牌与支线互斥仲裁（`mole_seed_rotator.py` v1.0）**：
  * 实现 `DynamicMoleSeedRotator`（`SEED_BATCH_ALPHA / BETA / GAMMA` 动态轮换内鬼落点）与**物证前置锁**（未采集现场物证跳步抄攻略直接触发 `+65 OOC` 惩罚，跨批次抓错人触发 `+52 OOC`）。
  * 实现 `StageEntityMutexArbitrator`，解决跨轮次固化支线在夜间争夺同一舞台实体（如乌林干草船）时的逻辑冲突。
* **Day 5 终局切片回溯、百人流星远征与多剧本热加载（`finale_expedition_engine.py` v1.0）**：
  * 实现五日因果回溯合成器 `TrueHistoryRewindSynthesizer`（【事定犹须待阖棺】）。
  * 实现百人归序者远征微扰聚合器 `ExpeditionSwarmAggregator`（采用 `tanh(net/35)` 将百人合力严格收敛在 `±25%` 火势、`±8h` 延迟、`±10` 心理槽内）。
  * 实现零代码多战役剧本包注册表 `SCENARIO_PACK_REGISTRY`，内置《官渡之战：乌巢劫粮（200 AD）》完整剧本包。
* **美术资产发包规范与全周期路线图（`chibi-art-asset-spec.md` v1.0 & `chibi-mvp-sprint-plan.md` v3.0）**：
  * 拆解 Sprint 2–4 开发排期，制定 `4096×2048` 三层烘焙底图、`2048×2048` 沙盘特效图集及 `1024×1024` 半身立绘 + 5 表情差分贴图规格。

### Changed（变更）

* **游戏概念文档与服务端统一入口升级（`game-concept.md` v3.0 & `app_server.py` v1.3）**：
  * 升级 v3.0 阻尼天梯结算公式（纳入 OOC 扮演契合度衰减系数与排错固化奖励分），新增 `/api/acting/state`、`/api/acting/evaluate`、`/api/acting/unlock_fragment`、`/api/acting/trial_submit` 接口。
* **2.5D 战略沙盘前端视觉与机制全量实装（`chibi_sandtable_mvp.html`）**：
  * 实装重彩山峦与乌林火把粒子、北寨铁索连环战船、朱砂方印与机械棘轮齿轮锁、角色脚下旋转金色固化法阵及「图1·全局沙盘 / 图2·大帐斩蔡瑁 / 图3·乌林夜探固化」镜头切换。
  * 新增动态内鬼洗牌与物证前置锁交互、百人流星坠入特效及赤壁（208 AD）⇄ 官渡乌巢（200 AD）剧本热切换。

---

## \[v2.0.0\] - 2026-11-16 — 持久化、平行分叉读档、混合房间空降与 Docker 联调

### Added（新增）

* **Redis 夜间调度与 4 步断点续跑（`nightly_scheduler_worker.py`）**：支持日间配额 `INCR`、指令池 `RPUSH`、排行榜 `ZADD` 及 `STEP_1`–`STEP_4` 故障恢复。
* **PostgreSQL JSONB 平行历史分叉树（`timeline_fork_store.py`）**：支持 Copy-on-Write 从 Day 1 存档点克隆平行分支并与主线死局隔离对比。
* **多人混合托管与高光空降接管（`room_matchmaker.py`）**：支持 2–5 名真人开局、AI 自动补位、Day 2 高光窗口中途空降与 2 天 AFK 自动释放。
* **容器化一键部署（`Dockerfile`、`docker-compose.yml`、`nginx.conf`）**：实现前端自动探测真实 FastAPI `/api/*` 或本地离线契约无缝切换。

---

## \[v1.3.0\] - 2026-11-15 — 全阵容 RoleAgent 合龙与 Context Caching 贯通

* 补齐 12 名角色锁定底色档案、SHA-256 静态前缀缓存、`PrivateHorizonContext` 舞台隔离与三级 JSON 容错。

## \[v1.2.0\] - 2026-11-15 — 夜间反事实死局强验算与 AUDIT-03 反混淆加固

* 新增 `counterfactual.py` 4 分支自救穷举验算与 `era_guard.py` v1.2 NFKC 反混淆/化学配方共现拦截。

## \[v1.1.0\] - 2026-11-14 — QA 数值平衡热修与双步死局阈值校准

* 升级平滑阻尼天梯公式并校准曹操疑心两步触发阈值（`55 + 18 + 8 = 81`）。

## \[v1.0.0\] - 2026-11-14 — 赤壁 MVP 概念定稿、ADR-001 架构与首版前后端闭环

* 完成概念文档、数据字典、ADR-001 架构决策及首版前后端沙盘原型。