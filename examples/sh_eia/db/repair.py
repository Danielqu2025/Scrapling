"""Repair master assignments and aliases after resolver rule changes."""

from __future__ import annotations

import sqlite3
import sys
from typing import Any

from db.resolver import MasterResolver, _parse_summary  # noqa: PLC2701
from db.store import EIAStore, utc_now
from _paths import DB_PATH

_DB_LOCKED_HINT = (
    "数据库正被其他进程占用（通常是 04_run_server.py）。"
    "请先在运行服务的终端按 Ctrl+C 停止服务，再执行：python -m db.repair"
)


def _ensure_db_writable() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库：{DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=3.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise SystemExit(_DB_LOCKED_HINT) from exc
        raise
    finally:
        conn.close()

def _record_from_event(row: dict[str, Any]) -> dict[str, Any]:
    summary = _parse_summary(row.get("summary_json"))
    basic = (summary.get("stage_fields") or {}).get("basic") or {}
    company = (basic.get("建设单位") or summary.get("company") or "").strip()
    location = (basic.get("建设地点") or summary.get("location") or "").strip()
    district = (basic.get("所属区域") or summary.get("district") or "").strip()
    if company and district and company == district:
        company = ""
    return {
        "source": row.get("source", ""),
        "disclosure_type": row.get("disclosure_type", ""),
        "external_id": row.get("external_id", ""),
        "project_name": row.get("title") or summary.get("project_name", ""),
        "title": row.get("title", ""),
        "company": company,
        "location": location,
        "district": district,
        "approval_number": summary.get("approval_number", ""),
        "st_eia_id": summary.get("st_eia_id", ""),
        "summary_json": summary,
    }


def repair_master_assignments(store: EIAStore | None = None, *, verbose: bool = True) -> dict[str, int]:
    store = store or EIAStore()
    now = utc_now()
    reassigned = 0
    aliases_rebuilt = 0

    with store.connect() as conn:
        conn.execute("DELETE FROM master_aliases")
        events = conn.execute(
            """
            SELECT * FROM disclosure_events
            ORDER BY
                CASE disclosure_type
                    WHEN 'approval_decision' THEN 0
                    WHEN 'post_construction' THEN 1
                    WHEN 'proposed_approval' THEN 2
                    WHEN 'acceptance' THEN 3
                    ELSE 4
                END,
                CASE source WHEN 'link_sthj' THEN 0 ELSE 1 END,
                COALESCE(NULLIF(event_date, ''), synced_at),
                id
            """
        ).fetchall()
        total = len(events)
        if verbose:
            print(f"共 {total} 条事件，正在加载索引…", flush=True)
        resolver = MasterResolver(conn, now)
        resolver.preload_caches()
        if verbose:
            print("开始重分配 master…", flush=True)
        for index, row in enumerate(events, start=1):
            event = dict(row)
            record = _record_from_event(event)
            master_id = resolver.resolve_master_id(record)
            if master_id != event["master_id"]:
                old_master_id = int(event["master_id"])
                conn.execute(
                    "UPDATE disclosure_events SET master_id = ? WHERE id = ?",
                    (master_id, event["id"]),
                )
                reassigned += 1
                resolver.refresh_master_episode_keys(old_master_id)
                resolver.refresh_master_episode_keys(master_id)
            if verbose and index % 5000 == 0:
                print(
                    f"进度 {index}/{total} ({index * 100 // total}%)，已重分配 {reassigned} 条",
                    flush=True,
                )

        if verbose:
            print("清理孤立 master…", flush=True)

        orphan_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT m.id FROM projects_master m
                LEFT JOIN disclosure_events e ON e.master_id = m.id
                WHERE e.id IS NULL
                """
            ).fetchall()
        ]
        for master_id in orphan_ids:
            conn.execute("DELETE FROM projects_master WHERE id = ?", (master_id,))

        aliases_rebuilt = conn.execute("SELECT COUNT(*) FROM master_aliases").fetchone()[0]

    if verbose:
        print("完成。", flush=True)
    return {
        "events_checked": len(events),
        "events_reassigned": reassigned,
        "aliases_rebuilt": aliases_rebuilt,
        "orphan_masters_removed": len(orphan_ids),
    }


if __name__ == "__main__":
    _ensure_db_writable()
    try:
        result = repair_master_assignments()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise SystemExit(_DB_LOCKED_HINT) from exc
        raise
    print(result)