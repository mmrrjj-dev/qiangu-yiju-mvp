# 《千古一局：暗合推演》

> **一句话定位**：以历史与经典战役为舞台的**异步多智能体悬疑博弈与暗合推演平台**。融合“盲盒内鬼排错 × OOC 违和度潜伏 × 跨轮次支线固化 × 反事实死局强验算”，支持 2.5D 国风战略沙盘 24 小时时间轴复盘与百人终局远征共斗。

## 核心特性（v3.0.0）

* **逻辑归代码，演技归 AI**：确定性状态机（`state_machine.py`）锁死心理阈值与单向棘轮（`>=70`），彻底杜绝大模型临场心软妥协。
* **三道时代铁律防线**：`15` 字结构化槽位 + Unicode NFKC 反混淆/化学配方共现拦截 + 史官转译器（`era_guard.py` v1.2）。
* **盲盒内鬼排错与防抄作业洗牌**：区分真内鬼与原生烟雾弹 NPC（`acting_solidifier.py`），通过 `SEED_BATCH` 动态轮换与物证前置锁防论坛剧透（`mole_seed_rotator.py`）。
* **夜间反事实 4 分支死局验算**：穷举敌方主帅全部自救路线，仅 `0/4` 存活时授予 `1.20x` 死局加成（`counterfactual.py`）。
* **双战役热切换与 2.5D 沙盘**：内置《赤壁之战（208 AD）》与《官渡之战（200 AD）》水墨重彩底图、17 名角色头像与百人流星远征特效（`client/chibi_sandtable_mvp.html`）。

## 快速开始

1. **直接体验前端**：浏览器打开 `client/chibi_sandtable_mvp.html`。
2. **启动后端服务**：
   ```bash
   cd server
   cp .env.example .env  # 可选填入 DEEPSEEK_API_KEY
   pip install -r requirements.txt
   uvicorn app_server:app --reload --port 8000
   ```
3. **Docker 一键部署**：
   ```bash
   docker-compose up --build
   ```

详细架构与交接事项请参阅 [`HANDOFF.md`](./HANDOFF.md)，版本演进请参阅 [`CHANGELOG.md`](./CHANGELOG.md)。
