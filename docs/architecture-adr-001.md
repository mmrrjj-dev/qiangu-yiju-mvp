# 《千古一局：暗合推演》架构决策记录（ADR-001）与前后端工程目录规范

* **状态**：已确认（Accepted）

* **日期**：2026-11-14

* **技术栈概览**：

  * **客户端**：TypeScript + React 19 + Zustand（UI 与状态层）+ PixiJS v8 + `@esotericsoftware/spine-pixi-v8`（2.5D 沙盘与骨骼动画层）

  * **服务端**：Python 3.12 + FastAPI + Pydantic v2（结构化校验与状态机）+ Temporal / Redis（夜间 BSP 异步推演队列）+ PostgreSQL（JSONB 事实锚点存储）

## 1. 架构决策记录（ADR-001：客户端与服务端技术选型）

### 1.1 背景与问题（Context）

本项目需要同时承载两类截然不同的交互负载：

1. **高密度信息推理 UI（占交互时长 80%）**：24 小时可拖拽战局时间轴、模糊线索拼图白板、结构化防越狱指令槽位、全服角色天梯排行榜。

2. **2.5D 动态战略沙盘演出（占视觉焦点 20%）**：12–15 个战役地标、25–30+ 个 Spine 骨骼角色同步走位、环境粒子（江雾、火光）与高精半身立绘差分弹出。

3. **云端夜间 BSP 异步推演**：服务端需在夜间窗口调度 25–30+ 智能体按舞台分区并发思考、短串行博弈，并通过确定性状态机锁死“羊毛局”因果链。

### 1.2 备选方案对比（Options Considered）

| 评估维度 | 方案 A：Web 栈（React + PixiJS + FastAPI） | 方案 B：Godot 4（GDScript + FastAPI） | 方案 C：Unity（URP + FastAPI） | 
| ----- | ----- | ----- | ----- | 
| **复杂 UI 开发效率** | **极高**（React 生态实现拖拽白板、时间轴、排行榜极快） | **中**（Control 节点构建复杂富文本与拖拽表较繁琐） | **中低**（UGUI/UI Toolkit 迭代周期长） | 
| **2.5D 沙盘渲染能力** | **中上**（PixiJS v8 WebGL/WebGPU 稳跑 60fps + Spine） | **高**（原生 2D/2.5D 渲染管线轻量高效） | **极高**（URP 光影上限最高，但包体与工程极重） | 
| **跨端分发与获客** | **极高**（URL 点开即玩，可套 Tauri 发 Steam 或转小游戏） | **高**（支持多端导出，Web 导出包体略大） | **中**（WebGL 包体沉重，主要依赖客户端安装） | 
| **前后端协议对齐** | **极高**（Pydantic 直接生成 OpenAPI + TypeScript 类型） | **中**（需额外维护 GDScript 序列化映射） | **中**（需维护 C# DTO 与 JSON 解析） | 

### 1.3 最终决策（Decision）

选择 **方案 A：React + PixiJS 双层解耦前端 + Python FastAPI 异步后端**。

* **核心架构原则**：前端严格执行“DOM UI 层（React）与 Canvas 渲染层（PixiJS）状态单向解耦”——24 小时演出时间轴按固定时间步长（Fixed Timestep `dt`）推进逻辑状态，PixiJS 在 `requestAnimationFrame` 中做坐标插值渲染，绝不在渲染帧循环中触发 React 组件重渲染。

### 1.4 决策影响与应对（Consequences）

* **正向收益**：推理白板、时间轴与天梯榜开发速度提升 3 倍以上；前后端共用同一套 JSON Schema（基于 `chibi-mvp-schema.md`），零协议偏差。

* **潜在风险与缓解**：

  * *风险*：浏览器端一次性加载 30 个 Spine 骨骼与高清立绘可能导致首屏卡顿。

  * *缓解*：采用纹理图集（Texture Atlas）合并 + 按舞台（Zone）懒加载立绘差分；夜间生成的 `演出时间轴 JSON` 静态托管至 CDN。

## 2. 工程目录规范（Monorepo 结构）

严格遵循工作室物理隔离红线（`core` 零内存泄漏与纯逻辑、`gameplay` 纯数据驱动且不引用具体 UI、`ui` 只订阅状态并派发意图、`networking` 强版本校验）：

```
sanguo-deduction-platform/
├── shared-schemas/                 # 前后端共享协议（Single Source of Truth）
│   ├── action_primitives.schema.json
│   ├── character_state.schema.json
│   └── era_whitelist.json
│
├── client/                         # 前端工程（Vite + React + TypeScript + PixiJS v8）
│   └── src/
│       ├── core/                   # [红线] 热路径零分配、对象池、固定时间步长调度器
│       │   ├── timeline-clock.ts   # 24h 时间轴驱动器（Fixed Timestep dt 推进与跳转）
│       │   ├── object-pool.ts      # 精灵与粒子对象池（禁止每帧 new 对象）
│       │   └── event-bus.ts        # 跨层轻量事件总线（解耦 React 与 PixiJS）
│       │
│       ├── gameplay/               # [红线] 纯数据驱动逻辑，严禁 import React 或 DOM
│       │   ├── stage-director.ts   # 解析 MOVE/INTERACT/EMOTE/VFX 原语并计算插值坐标
│       │   ├── pathfinding.ts      # 2.5D 地标槽位（Slot）烘焙路径寻路计算
│       │   └── sandbox-preview.ts  # 日间快推沙盒本地状态快照对比器
│       │
│       ├── renderer/               # PixiJS 2.5D 沙盘渲染层（只读订阅 gameplay 状态）
│       │   ├── map-canvas.ts       # 2.5D 等距地图底图与视口缩放/拖拽控制
│       │   ├── spine-actor.ts      # Spine 骨骼角色封装（步行/密谈/拔剑等 8 动作切换）
│       │   └── vfx-layer.ts        # 江雾、火势蔓延、战鼓波纹等环境粒子层
│       │
│       ├── ui/                     # [红线] React 视图层，只读订阅 Store 并派发玩家意图
│       │   ├── ClueBoard/          # 模糊线索拼图与敌方暗棋推理白板
│       │   ├── CommandSlot/        # 三道防线第1层：结构化下拉模板 + 15字短语输入槽
│       │   ├── PortraitStage/      # 关键碰撞弹出的高精半身立绘 + 表情差分气泡
│       │   ├── TimelineScrubber/   # 底部 24h 战局进度条（高亮 ANCHOR_LOCK 转折点）
│       │   └── Leaderboard/        # 角色独立天梯榜与蝴蝶点数效率复盘面板
│       │
│       └── networking/             # [红线] 显式携带 schema_version，对接后端与 CDN
│           ├── api-client.ts       # 提交暗棋、调用日间快推沙盒接口
│           └── replay-loader.ts    # 从 CDN 拉取夜间预生成的演出时间轴 JSON
│
└── server/                         # 后端工程（Python 3.12 + FastAPI + Temporal）
    └── app/
        ├── core/                   # 状态机与三道防越狱铁律（100% 确定性代码）
        │   ├── state_machine.py    # 心理槽加减、单向棘轮锁定与不可逆阈值（如疑心>=80斩蔡瑁）
        │   ├── minor_cap.py        # 小人物三大物理边界校验（延迟<=12h、损耗<=20%）
        │   └── era_guard.py        # 时代白名单向量检索与违规词秒拒
        │
        ├── agents/                 # 多智能体编排与 Prompt 路由
        │   ├── historian_sanitizer.py # 史官转译器（隔离玩家输入，转译为东汉动机）
        │   ├── role_agent.py       # 角色智能体封装（私密视野 + 锁定底色 Context Caching）
        │   ├── counterfactual.py   # 夜间强推理模型“反事实死局验算器”
        │   └── director_compiler.py# 将多轮博弈结果编译为标准动作原语时间轴 JSON
        │
        ├── pipelines/              # 异步任务流（双速架构）
        │   ├── nightly_bsp_job.py  # 夜间慢队列：舞台分区唤醒 → 并发思考 → 局部短串行 → 结算
        │   └── daytime_sandbox.py  # 日间快通道：单场景 2-3 角色 3-5 秒极速试算
        │
        └── api/                    # FastAPI 路由与数据库模型
            ├── routes_command.py   # 接收玩家回合指令单并执行前置时代校验
            ├── routes_sandbox.py   # 日间沙盒试算接口（含频次与配额控制）
            └── models_db.py        # PostgreSQL JSONB 表定义（对接 chibi-mvp-schema）

```

## 3. 架构红线与关键数据流约束

1. **React 与 PixiJS 通信铁律**：

   * 当玩家拖动底部 React `TimelineScrubber` 进度条时，只更新 `core/timeline-clock.ts` 的时间戳 `t`。

   * `gameplay/stage-director.ts` 根据 `t` 计算出当前时刻 25 个角色所在的 `(zone_id, slot_idx)` 与动作状态，由 PixiJS 渲染帧直接读取绘制。

   * 只有当时间轴触发 `EMOTE` 或 `ANCHOR_LOCK` 事件时，才通过 `event-bus.ts` 通知 React 弹出半身立绘或高亮因果卡片，彻底避免每帧触发 React 重渲染。

2. **服务端“逻辑归代码，演技归 AI”铁律**：

   * `agents/role_agent.py` 的大模型输出被 Pydantic 严格限制为 `{ "thought": str, "event_tag": Enum, "chosen_plot": PlotID, "dialogue": str }`。

   * 任何心理数值变动（`psych_slots`）与生死结局判定，必须且只能由 `core/state_machine.py` 的纯 Python 确定性函数执行，大模型绝无直接修改数据库状态的权限。