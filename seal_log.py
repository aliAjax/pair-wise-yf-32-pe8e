"""封签记录层：事件落库、表结构迁移与协调台差异查询。

判定逻辑见 seal_policy.py，页面交互见 static/index.html，本模块只管记录。
"""
from __future__ import annotations

import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS seal_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    allocation_id INTEGER NOT NULL REFERENCES allocations(id),
    handoff_id INTEGER,
    stage TEXT NOT NULL,
    actor TEXT NOT NULL,
    hospital TEXT NOT NULL DEFAULT '',
    expected_seal TEXT,
    reported_seal TEXT,
    result TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

EXTRA_COLUMNS = {
    "allocations": {"seal_code": "TEXT", "seal_issued_at": "TEXT"},
    "handoffs": {"initiator_seal": "TEXT", "receiver_seal": "TEXT", "receiver_reported_at": "TEXT"},
}

ACTIVE_SEAL_QUERY = "SELECT id, seal_code FROM allocations WHERE seal_code IS NOT NULL AND status NOT IN ('withdrawn','expired','implanted')"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建封签事件表，并为既有数据库补齐封签列。"""
    conn.executescript(SCHEMA)
    for table, columns in EXTRA_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


class SealLedger:
    """封签留痕：生成、双方上报、拒绝与补发都写入 seal_events。"""

    @staticmethod
    def record(conn: sqlite3.Connection, *, allocation_id: int, handoff_id: int | None, stage: str, actor: str,
               hospital: str, expected_seal: str | None, reported_seal: str | None, result: str, detail: str, created_at: str) -> None:
        conn.execute(
            """INSERT INTO seal_events(allocation_id,handoff_id,stage,actor,hospital,expected_seal,reported_seal,result,detail,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (allocation_id, handoff_id, stage, actor, hospital, expected_seal, reported_seal, result, detail, created_at))

    @staticmethod
    def events(conn: sqlite3.Connection, allocation_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute("SELECT * FROM seal_events WHERE allocation_id=? ORDER BY id", (allocation_id,))]

    @staticmethod
    def active_seals(conn: sqlite3.Connection, exclude_allocation_id: int | None = None) -> set[str]:
        """仍在流转的封签集合，用于生成去重和识别"撞他单封签"。"""
        return {r["seal_code"] for r in conn.execute(ACTIVE_SEAL_QUERY) if r["id"] != exclude_allocation_id}

    @staticmethod
    def discrepancies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        """协调台差异视图：在途缺封签（待补）+ 最近一次核验被拒的分配。"""
        issues: list[dict[str, Any]] = []
        for r in conn.execute("SELECT id, status, updated_at FROM allocations WHERE status='in_transit' AND seal_code IS NULL ORDER BY id"):
            issues.append({"allocation_id": r["id"], "issue": "seal_pending", "status": r["status"], "stage": None,
                           "expected_seal": None, "reported_seal": None, "reported_by": None, "hospital": None, "reported_at": r["updated_at"]})
        latest = conn.execute("""
            SELECT e.* FROM seal_events e
            JOIN (SELECT allocation_id, MAX(id) AS max_id FROM seal_events
                  WHERE stage IN ('initiate','confirm') GROUP BY allocation_id) t ON t.max_id=e.id
            WHERE e.result IN ('mismatch','duplicate','expired') ORDER BY e.id DESC""")
        for e in latest:
            alloc = conn.execute("SELECT status FROM allocations WHERE id=?", (e["allocation_id"],)).fetchone()
            if not alloc:
                continue
            issues.append({"allocation_id": e["allocation_id"], "issue": e["result"], "status": alloc["status"], "stage": e["stage"],
                           "expected_seal": e["expected_seal"], "reported_seal": e["reported_seal"],
                           "reported_by": e["actor"], "hospital": e["hospital"], "reported_at": e["created_at"]})
        return issues
