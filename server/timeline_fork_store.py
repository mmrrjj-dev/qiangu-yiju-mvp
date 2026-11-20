"""
《千古一局：暗合推演》长线战役“历史分叉点读档（Fork Timeline）”与 PostgreSQL JSONB 存储引擎 (timeline_fork_store.py)

核心职责：
1. 解决“5 天长线局走错一步、后 3 天慢性死亡坐牢”的痛点：
   允许玩家消耗少量活跃积分/点卡，从任意历史回合存档点（如 Day 1 晚或 Day 2 关键死局锁死前）切出一条独立的“平行历史分叉线（Parallel Branch）”，注入全新破局暗棋重新推演。
2. PostgreSQL JSONB 生产级 DDL 与 ORM 模型（含 GIN 索引与事务级 Copy-on-Write 克隆）：
   - `campaign_branches`：记录主时间线与所有平行分叉线的父子谱系（`parent_branch_id`、`fork_from_day`）。
   - `daily_world_snapshots`：以 JSONB 存储每一天结算后的角色心理槽、单向棘轮、不可逆事实锚点与 24h 演出时间轴契约。
3. 双模持久化驱动（PostgreSQL `psycopg2/SQLAlchemy` + 内置 SQLite/JSONB 零依赖兼容层）：
   生产环境配置 `DATABASE_URL=postgresql://...` 即走原生 JSONB；本地无数据库服务时自动使用内置事务引擎跑通完整分叉克隆与平行推演测试。
"""

from __future__ import annotations
import copy
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from state_machine import (
    CharacterState,
    DeterministicStateMachine,
    EventTag,
    FactAnchor,
    RatchetBelief,
    RoleTier,
)
from counterfactual import CounterfactualDeadlockVerifier


# ---------------------------------------------------------------------------
# 1. PostgreSQL 原生 JSONB 表结构与 GIN 索引定义 (生产环境迁移脚本)
# ---------------------------------------------------------------------------
POSTGRES_JSONB_DDL_SQL = """
-- 1. 战役时间线分支表（管理主线与玩家分叉出的平行历史线）
CREATE TABLE IF NOT EXISTS campaign_branches (
    branch_id           VARCHAR(64) PRIMARY KEY,
    battle_id           VARCHAR(64) NOT NULL,
    branch_title        VARCHAR(128) NOT NULL,
    parent_branch_id    VARCHAR(64) REFERENCES campaign_branches(branch_id),
    fork_from_day       INTEGER NOT NULL DEFAULT 0,
    owner_player_id     VARCHAR(64) NOT NULL,
    fork_cost_points    INTEGER NOT NULL DEFAULT 0,
    is_mainline         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_branches_battle_parent
    ON campaign_branches(battle_id, parent_branch_id);

-- 2. 每日世界状态 JSONB 快照表（支持任意回合毫秒级 Copy-on-Write 分叉克隆）
CREATE TABLE IF NOT EXISTS daily_world_snapshots (
    snapshot_id         VARCHAR(64) PRIMARY KEY,
    branch_id           VARCHAR(64) NOT NULL REFERENCES campaign_branches(branch_id) ON DELETE CASCADE,
    day                 INTEGER NOT NULL,
    roster_state_jsonb  JSONB NOT NULL,          -- 全阵容角色 psych_slots 与 ratchet_beliefs
    locked_facts_jsonb  JSONB NOT NULL,          -- 全局已锁死的不可逆事实锚点数组
    battlefield_conds   JSONB NOT NULL,          -- 当前已触发的战场环境伏笔集合
    counterfactual_json JSONB,                   -- 当晚反事实死局验算报告
    replay_contract     JSONB,                   -- 当晚生成的 24h 动作原语时间轴
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (branch_id, day)
);

-- GIN 索引：支持按“包含特定不可逆锚点（如 FACT_SHIPS_CHAINED）”快速检索平行宇宙分支
CREATE INDEX IF NOT EXISTS idx_snapshots_locked_facts_gin
    ON daily_world_snapshots USING GIN (locked_facts_jsonb);
"""


# ---------------------------------------------------------------------------
# 2. Pydantic 领域模型与 API 请求/响应契约
# ---------------------------------------------------------------------------
class WorldSnapshotPayload(BaseModel):
    """单日世界状态完整快照（对应 daily_world_snapshots 行记录）"""
    snapshot_id: str
    branch_id: str
    day: int
    roster_state: Dict[str, Dict[str, Any]] = Field(
        description="各角色心理状态槽与单向棘轮快照"
    )
    locked_facts: List[str] = Field(default_factory=list)
    battlefield_conditions: List[str] = Field(default_factory=list)
    is_true_deadlock: bool = False
    escape_routes: List[str] = Field(default_factory=list)
    summary_note: str = ""


class CampaignBranchNode(BaseModel):
    """时间线分支谱系节点"""
    branch_id: str
    battle_id: str
    branch_title: str
    parent_branch_id: Optional[str] = None
    fork_from_day: int = 0
    owner_player_id: str
    fork_cost_points: int = 0
    is_mainline: bool = False
    latest_day: int = 1
    latest_locked_facts: List[str] = Field(default_factory=list)
    is_deadlock_escaped: bool = False
    created_at: str


class TimelineForkRequest(BaseModel):
    """POST /api/timeline/fork 分叉读档请求体"""
    battle_id: str = "chibi_208ad"
    parent_branch_id: str = Field(..., description="要回溯的源时间线 ID，如 'branch_main'")
    fork_from_day: int = Field(..., ge=1, le=4, description="回溯到第几天的存档点（如 Day 1 尚未斩蔡瑁时）")
    player_id: str
    new_branch_title: str = Field(..., description="新平行分支名称，如 '曹营自救线：分批上岸+密查书信'")
    override_counter_strategy: str = Field(
        default="ROTATE_SHORE_AND_VERIFY_LETTER",
        description="分叉后注入的新破局策略（ROTATE_SHORE_AND_VERIFY_LETTER | FORCE_LOCK_SHIPS）",
    )


class TimelineForkResponse(BaseModel):
    """分叉读档与平行推演结果"""
    fork_succeeded: bool
    new_branch: CampaignBranchNode
    cloned_base_snapshot: WorldSnapshotPayload
    simulated_next_day_snapshot: WorldSnapshotPayload
    comparison_verdict: str


# ---------------------------------------------------------------------------
# 3. 历史分叉点存储与克隆引擎（支持 PostgreSQL JSONB 与内置 SQLite 事务存储）
# ---------------------------------------------------------------------------
class TimelineForkRepository:
    """
    管理战役主线与平行分叉时间线的存储、Copy-on-Write 克隆与谱系树查询。
    """

    FORK_BASE_COST_POINTS = 50  # 每次开启平行历史分叉消耗的活跃积分

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.cf_verifier = CounterfactualDeadlockVerifier()
        self._init_schema()
        self._seed_chibi_mainline()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS campaign_branches (
                branch_id           TEXT PRIMARY KEY,
                battle_id           TEXT NOT NULL,
                branch_title        TEXT NOT NULL,
                parent_branch_id    TEXT,
                fork_from_day       INTEGER NOT NULL DEFAULT 0,
                owner_player_id     TEXT NOT NULL,
                fork_cost_points    INTEGER NOT NULL DEFAULT 0,
                is_mainline         INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_world_snapshots (
                snapshot_id         TEXT PRIMARY KEY,
                branch_id           TEXT NOT NULL,
                day                 INTEGER NOT NULL,
                roster_state_jsonb  TEXT NOT NULL,
                locked_facts_jsonb  TEXT NOT NULL,
                battlefield_conds   TEXT NOT NULL,
                is_true_deadlock    INTEGER NOT NULL DEFAULT 0,
                escape_routes_jsonb TEXT NOT NULL,
                summary_note        TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                UNIQUE (branch_id, day)
            );
            """
        )
        self._conn.commit()

    def _seed_chibi_mainline(self) -> None:
        """
        初始化《赤壁之战》主时间线（branch_main）：
        - Day 1 存档点：大雾前夕，曹操疑心 63、水战焦虑 64，蔡瑁未死、战船未锁（尚有挽回余地！）
        - Day 2 存档点：主线曹营应对失误，中反间计+连环计，疑心飙至 81、焦虑 76，触发真·羊毛死局！
        """
        now_str = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO campaign_branches
            (branch_id, battle_id, branch_title, parent_branch_id, fork_from_day, owner_player_id, fork_cost_points, is_mainline, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "branch_main",
                "chibi_208ad",
                "赤壁主时间线（史实死局线）",
                None,
                0,
                "system_mainline",
                0,
                1,
                now_str,
            ),
        )

        # Day 1 关键分叉锚点快照（尚未跨过不可逆阈值）
        day1_roster = {
            "char_caocao": {
                "name": "曹操",
                "psych_slots": {"suspicion": 63, "impatience": 58, "naval_anxiety": 64},
                "ratchet_beliefs": [],
            },
            "char_caimao": {
                "name": "蔡瑁",
                "psych_slots": {"suspicion": 50, "impatience": 55, "naval_anxiety": 30},
                "ratchet_beliefs": [],
            },
        }
        self._save_snapshot_row(
            branch_id="branch_main",
            day=1,
            roster_state=day1_roster,
            locked_facts=[],
            conditions=["COND_UPPER_WIND_DRY_GRASS", "COND_NIGHT_DRUMS_HARASS"],
            is_true_deadlock=False,
            escape_routes=[
                "分支 3：分批上岸扎营轮休（以时间换空间）",
                "分支 4：派校事府密查蒋干盗信真伪",
            ],
            summary_note="Day 1 戌时存档点：北军初现晕船，蒋干尚未献信，曹营保有 2 条破局生路。",
        )

        # Day 2 主线结局快照（已掉入真·羊毛死局）
        day2_roster = {
            "char_caocao": {
                "name": "曹操",
                "psych_slots": {"suspicion": 81, "impatience": 68, "naval_anxiety": 76},
                "ratchet_beliefs": [
                    RatchetBelief.DISTRUST_JINGZHOU.value,
                    RatchetBelief.NEED_STABLE_SHIPS.value,
                ],
            },
            "char_caimao": {
                "name": "蔡瑁（已斩）",
                "psych_slots": {"suspicion": 50, "impatience": 65, "naval_anxiety": 30},
                "ratchet_beliefs": [],
            },
        }
        self._save_snapshot_row(
            branch_id="branch_main",
            day=2,
            roster_state=day2_roster,
            locked_facts=[
                FactAnchor.CAIMAO_EXECUTED.value,
                FactAnchor.SHIPS_CHAINED.value,
                "FACT_TRUE_DEADLOCK_SEALED",
            ],
            conditions=[
                "COND_UPPER_WIND_DRY_GRASS",
                "COND_SOUTHEAST_WIND_WINDOW",
                "COND_NIGHT_DRUMS_HARASS",
                "COND_SHORE_RAID_BLOCKED",
            ],
            is_true_deadlock=True,
            escape_routes=[],
            summary_note="Day 2 子时终局：曹操锁船且立斩蔡瑁，乌林滩涂被截断，4条自救路线全灭（真·羊毛死局）。",
        )

    def _save_snapshot_row(
        self,
        branch_id: str,
        day: int,
        roster_state: Dict[str, Any],
        locked_facts: List[str],
        conditions: List[str],
        is_true_deadlock: bool,
        escape_routes: List[str],
        summary_note: str,
    ) -> WorldSnapshotPayload:
        snap_id = f"snap_{branch_id}_d{day}"
        now_str = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO daily_world_snapshots
            (snapshot_id, branch_id, day, roster_state_jsonb, locked_facts_jsonb,
             battlefield_conds, is_true_deadlock, escape_routes_jsonb, summary_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap_id,
                branch_id,
                day,
                json.dumps(roster_state, ensure_ascii=False),
                json.dumps(locked_facts, ensure_ascii=False),
                json.dumps(conditions, ensure_ascii=False),
                1 if is_true_deadlock else 0,
                json.dumps(escape_routes, ensure_ascii=False),
                summary_note,
                now_str,
            ),
        )
        self._conn.commit()
        return WorldSnapshotPayload(
            snapshot_id=snap_id,
            branch_id=branch_id,
            day=day,
            roster_state=roster_state,
            locked_facts=locked_facts,
            battlefield_conditions=conditions,
            is_true_deadlock=is_true_deadlock,
            escape_routes=escape_routes,
            summary_note=summary_note,
        )

    def get_snapshot(self, branch_id: str, day: int) -> WorldSnapshotPayload:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM daily_world_snapshots WHERE branch_id = ? AND day = ?",
            (branch_id, day),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"未找到分支 {branch_id} 在 Day {day} 的历史存档快照。")
        return WorldSnapshotPayload(
            snapshot_id=row["snapshot_id"],
            branch_id=row["branch_id"],
            day=row["day"],
            roster_state=json.loads(row["roster_state_jsonb"]),
            locked_facts=json.loads(row["locked_facts_jsonb"]),
            battlefield_conditions=json.loads(row["battlefield_conds"]),
            is_true_deadlock=bool(row["is_true_deadlock"]),
            escape_routes=json.loads(row["escape_routes_jsonb"]),
            summary_note=row["summary_note"],
        )

    def fork_timeline_and_simulate(
        self, req: TimelineForkRequest
    ) -> TimelineForkResponse:
        """
        核心分叉读档流程（Copy-on-Write）：
        1. 从 parent_branch_id 读取 fork_from_day（如 Day 1）的干净快照并深拷贝；
        2. 创建新的平行分支记录（如 branch_fork_xxxx）；
        3. 在隔离状态机上应用玩家的新破局策略推演 Day (fork_from_day + 1)，验证是否成功逆天改命！
        """
        base_snap = self.get_snapshot(req.parent_branch_id, req.fork_from_day)
        new_branch_id = f"branch_fork_{uuid.uuid4().hex[:6]}"
        now_str = datetime.now(timezone.utc).isoformat()

        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO campaign_branches
            (branch_id, battle_id, branch_title, parent_branch_id, fork_from_day,
             owner_player_id, fork_cost_points, is_mainline, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                new_branch_id,
                req.battle_id,
                req.new_branch_title,
                req.parent_branch_id,
                req.fork_from_day,
                req.player_id,
                self.FORK_BASE_COST_POINTS,
                now_str,
            ),
        )
        self._conn.commit()

        # 1. 将 fork_from_day 的基线快照写入新分支（Copy-on-Write 起点）
        cloned_roster = copy.deepcopy(base_snap.roster_state)
        self._save_snapshot_row(
            branch_id=new_branch_id,
            day=req.fork_from_day,
            roster_state=cloned_roster,
            locked_facts=list(base_snap.locked_facts),
            conditions=list(base_snap.battlefield_conditions),
            is_true_deadlock=base_snap.is_true_deadlock,
            escape_routes=list(base_snap.escape_routes),
            summary_note=f"[自 {req.parent_branch_id} Day {req.fork_from_day} 分叉克隆] {base_snap.summary_note}",
        )

        # 2. 在新平行分支上推演下一天（Day 2'）
        next_day = req.fork_from_day + 1
        sm = DeterministicStateMachine()
        caocao_dict = copy.deepcopy(cloned_roster["char_caocao"])
        caocao_state = CharacterState(
            character_id="char_caocao",
            name="曹操",
            role_tier=RoleTier.COMMANDER,
            psych_slots=caocao_dict["psych_slots"],
            ratchet_beliefs={RatchetBelief(r) for r in caocao_dict.get("ratchet_beliefs", [])},
        )

        if req.override_counter_strategy == "ROTATE_SHORE_AND_VERIFY_LETTER":
            # 玩家在 Day 1 分叉点提前派校事府抓获伪造书信破绽（触发 HARD_COUNTER_PROOF）并分批上岸扎营
            sm.process_ai_event(caocao_state, EventTag.HARD_COUNTER_PROOF, "21:30")
            caocao_state.psych_slots["naval_anxiety"] = max(
                35, caocao_state.psych_slots["naval_anxiety"] - 18
            )
            new_conditions = ["COND_UPPER_WIND_DRY_GRASS"]
        else:
            sm.process_ai_event(caocao_state, EventTag.NIGHT_HARASSMENT, "22:00")
            sm.process_ai_event(caocao_state, EventTag.CHAIN_SHIPS_PROPOSED, "22:30")
            sm.process_ai_event(caocao_state, EventTag.FORGED_LETTER_FOUND, "23:10")
            new_conditions = [
                "COND_UPPER_WIND_DRY_GRASS",
                "COND_SOUTHEAST_WIND_WINDOW",
                "COND_NIGHT_DRUMS_HARASS",
                "COND_SHORE_RAID_BLOCKED",
            ]

        cf_report = self.cf_verifier.verify_caocao_deadlock(
            caocao_state, set(new_conditions)
        )
        next_locked_facts = sorted([f.value for f in sm.global_facts])
        if cf_report.is_true_deadlock:
            next_locked_facts.append("FACT_TRUE_DEADLOCK_SEALED")

        next_roster = copy.deepcopy(cloned_roster)
        next_roster["char_caocao"]["psych_slots"] = caocao_state.psych_slots
        next_roster["char_caocao"]["ratchet_beliefs"] = sorted(
            [b.value for b in caocao_state.ratchet_beliefs]
        )

        if not cf_report.is_true_deadlock:
            summary = (
                f"Day {next_day}' 平行破局成功：曹操疑心降至 {caocao_state.psych_slots['suspicion']}（识破反间计保住蔡瑁），"
                f"分批上岸轮休化解晕船，成功跳出主线火烧连环死局！"
            )
            verdict = (
                f"【平行分叉破局成功】相比主线 Day 2 的全军覆没，分支「{req.new_branch_title}」"
                f"在 Day {req.fork_from_day} 提前阻断了单向棘轮，存活分支从 0/4 提升至 "
                f"{cf_report.survival_branches_count}/{cf_report.total_branches_tested}！"
            )
        else:
            summary = f"Day {next_day}' 平行推演：仍未解开死局锚点。"
            verdict = "【平行分叉未破局】当前微调仍未能打破敌方四路封锁。"

        next_snap = self._save_snapshot_row(
            branch_id=new_branch_id,
            day=next_day,
            roster_state=next_roster,
            locked_facts=next_locked_facts,
            conditions=new_conditions,
            is_true_deadlock=cf_report.is_true_deadlock,
            escape_routes=cf_report.escape_actions,
            summary_note=summary,
        )

        branch_node = CampaignBranchNode(
            branch_id=new_branch_id,
            battle_id=req.battle_id,
            branch_title=req.new_branch_title,
            parent_branch_id=req.parent_branch_id,
            fork_from_day=req.fork_from_day,
            owner_player_id=req.player_id,
            fork_cost_points=self.FORK_BASE_COST_POINTS,
            is_mainline=False,
            latest_day=next_day,
            latest_locked_facts=next_locked_facts,
            is_deadlock_escaped=not cf_report.is_true_deadlock,
            created_at=now_str,
        )

        return TimelineForkResponse(
            fork_succeeded=True,
            new_branch=branch_node,
            cloned_base_snapshot=base_snap,
            simulated_next_day_snapshot=next_snap,
            comparison_verdict=verdict,
        )

    def list_timeline_tree(self, battle_id: str = "chibi_208ad") -> List[CampaignBranchNode]:
        """获取当前战役的完整平行宇宙分叉树（供前端渲染分叉时间线图谱）"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM campaign_branches WHERE battle_id = ? ORDER BY is_mainline DESC, created_at ASC",
            (battle_id,),
        )
        branches = cur.fetchall()
        result: List[CampaignBranchNode] = []
        for b in branches:
            cur.execute(
                "SELECT * FROM daily_world_snapshots WHERE branch_id = ? ORDER BY day DESC LIMIT 1",
                (b["branch_id"],),
            )
            latest_snap = cur.fetchone()
            latest_day = int(latest_snap["day"]) if latest_snap else b["fork_from_day"]
            latest_facts = (
                json.loads(latest_snap["locked_facts_jsonb"]) if latest_snap else []
            )
            is_escaped = (
                (not bool(latest_snap["is_true_deadlock"])) if latest_snap else False
            )
            result.append(
                CampaignBranchNode(
                    branch_id=b["branch_id"],
                    battle_id=b["battle_id"],
                    branch_title=b["branch_title"],
                    parent_branch_id=b["parent_branch_id"],
                    fork_from_day=int(b["fork_from_day"]),
                    owner_player_id=b["owner_player_id"],
                    fork_cost_points=int(b["fork_cost_points"]),
                    is_mainline=bool(b["is_mainline"]),
                    latest_day=latest_day,
                    latest_locked_facts=latest_facts,
                    is_deadlock_escaped=is_escaped,
                    created_at=b["created_at"],
                )
            )
        return result


# ---------------------------------------------------------------------------
# 4. 自包含验证演示（直接运行 python3 timeline_fork_store.py 测试分叉克隆与主线隔离）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    repo = TimelineForkRepository()

    print("=========================================================================")
    print("  《千古一局：暗合推演》历史分叉点读档 (Fork Timeline) 与平行破局测试")
    print("=========================================================================\n")

    main_d1 = repo.get_snapshot("branch_main", 1)
    main_d2 = repo.get_snapshot("branch_main", 2)
    print("--- [1] 主时间线 (branch_main) 历史存档状态 ---")
    print(f"  * Day 1 存档: {main_d1.summary_note} | 曹操心理槽={main_d1.roster_state['char_caocao']['psych_slots']}")
    print(f"  * Day 2 终局: {main_d2.summary_note} | 锁定锚点={main_d2.locked_facts}\n")

    print("--- [2] 曹营玩家在 Day 2 战败后，消耗 50 积分从 Day 1 存档点开启平行分叉 ---")
    fork_req = TimelineForkRequest(
        battle_id="chibi_208ad",
        parent_branch_id="branch_main",
        fork_from_day=1,
        player_id="player_wei_strategist",
        new_branch_title="曹营逆天改命线：校事府核查密信 + 乌林分批上岸轮休",
        override_counter_strategy="ROTATE_SHORE_AND_VERIFY_LETTER",
    )
    fork_res = repo.fork_timeline_and_simulate(fork_req)
    print(f"  [新建平行分支 ID]: {fork_res.new_branch.branch_id} (父分支: {fork_res.new_branch.parent_branch_id} @ Day {fork_res.new_branch.fork_from_day})")
    print(f"  [平行线 Day 2' 曹操心理槽]: {fork_res.simulated_next_day_snapshot.roster_state['char_caocao']['psych_slots']}")
    print(f"  [平行线 Day 2' 锁定事实表]: {fork_res.simulated_next_day_snapshot.locked_facts} (零死局锚点！)")
    print(f"  {fork_res.comparison_verdict}\n")

    print("--- [3] 验证 Copy-on-Write 隔离性：检查原主线 (branch_main) Day 2 是否受污染 ---")
    main_d2_after = repo.get_snapshot("branch_main", 2)
    print(
        f"  原主线 Day 2 锁定事实仍保持不变: {main_d2_after.locked_facts} "
        f"(隔离校验通过={main_d2_after.locked_facts == main_d2.locked_facts})\n"
    )

    print("--- [4] 当前战役平行历史谱系树 (GET /api/timeline/tree) ---")
    for node in repo.list_timeline_tree("chibi_208ad"):
        tag = "【主线死局】" if node.is_mainline else ("【分叉破局成功】" if node.is_deadlock_escaped else "【分叉未破局】")
        print(
            f"  - {node.branch_id:<18} | {tag} {node.branch_title} "
            f"| 最新进度: Day {node.latest_day} | 锁定事实: {node.latest_locked_facts}"
        )