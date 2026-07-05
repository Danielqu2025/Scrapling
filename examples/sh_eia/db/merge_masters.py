"""Fast SQL-oriented merge for fragmented projects_master records."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from _paths import DB_PATH
from db.migrate import backfill_group_keys, ensure_group_key_column
from db.resolver import episode_group_key_from_event, project_group_key
from db.store import EIAStore, utc_now

_DB_LOCKED_HINT = (
    "数据库正被其他进程占用（通常是 04_run_server.py）。"
    "请先在运行服务的终端按 Ctrl+C 停止服务，再执行：python -m db.merge_masters"
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


def _pick_target_master(conn: sqlite3.Connection, master_ids: list[int]) -> int:
    placeholders = ",".join("?" for _ in master_ids)
    row = conn.execute(
        f"""
        SELECT m.id FROM projects_master m
        WHERE m.id IN ({placeholders})
        ORDER BY
            CASE WHEN COALESCE(m.approval_number, '') != '' THEN 0 ELSE 1 END,
            (SELECT CASE WHEN EXISTS(
                SELECT 1 FROM disclosure_events e
                WHERE e.master_id = m.id AND e.disclosure_type = 'approval_decision'
            ) THEN 0 ELSE 1 END),
            length(COALESCE(m.canonical_name, '')) DESC,
            m.id ASC
        LIMIT 1
        """,
        master_ids,
    ).fetchone()
    return int(row[0])


def _consolidate_master_fields(conn: sqlite3.Connection, target_id: int, source_ids: list[int]) -> None:
    for master_id in source_ids:
        if master_id != target_id:
            conn.execute(
                "UPDATE projects_master SET approval_number = '' WHERE id = ?",
                (master_id,),
            )
    placeholders = ",".join("?" for _ in source_ids)
    row = conn.execute(
        f"""
        SELECT
            MAX(length(COALESCE(canonical_name, ''))) AS max_len,
            MAX(CASE WHEN COALESCE(approval_number, '') != '' THEN approval_number END) AS approval_number,
            MAX(CASE WHEN COALESCE(company, '') != '' THEN company END) AS company,
            MAX(CASE WHEN COALESCE(location, '') != '' THEN location END) AS location,
            MAX(CASE WHEN COALESCE(district, '') != '' THEN district END) AS district
        FROM projects_master
        WHERE id IN ({placeholders})
        """,
        source_ids,
    ).fetchone()
    best_name = conn.execute(
        f"""
        SELECT canonical_name FROM projects_master
        WHERE id IN ({placeholders})
        ORDER BY length(COALESCE(canonical_name, '')) DESC, id ASC
        LIMIT 1
        """,
        source_ids,
    ).fetchone()
    approval_number = row["approval_number"] or ""
    if approval_number:
        conflict = conn.execute(
            "SELECT id FROM projects_master WHERE approval_number = ? AND id != ?",
            (approval_number, target_id),
        ).fetchone()
        if conflict:
            approval_number = ""
    conn.execute(
        """
        UPDATE projects_master SET
            canonical_name = CASE WHEN ? != '' THEN ? ELSE canonical_name END,
            approval_number = CASE WHEN ? != '' THEN ? ELSE approval_number END,
            company = CASE WHEN ? != '' THEN ? ELSE company END,
            location = CASE WHEN ? != '' THEN ? ELSE location END,
            district = CASE WHEN ? != '' THEN ? ELSE district END,
            group_key = CASE WHEN ? != '' THEN ? ELSE group_key END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            best_name[0] if best_name else "",
            best_name[0] if best_name else "",
            row["approval_number"] or "",
            approval_number,
            row["company"] or "",
            row["company"] or "",
            row["location"] or "",
            row["location"] or "",
            row["district"] or "",
            row["district"] or "",
            project_group_key(best_name[0] if best_name else ""),
            project_group_key(best_name[0] if best_name else ""),
            utc_now(),
            target_id,
        ),
    )


def merge_by_approval_number(conn: sqlite3.Connection) -> int:
    reassigned = 0
    groups = conn.execute(
        """
        SELECT approval_number, MIN(id) AS keep_id, GROUP_CONCAT(id) AS all_ids
        FROM projects_master
        WHERE COALESCE(approval_number, '') != ''
        GROUP BY approval_number
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for group in groups:
        keep_id = int(group["keep_id"])
        all_ids = [int(part) for part in (group["all_ids"] or "").split(",") if part]
        for master_id in all_ids:
            if master_id == keep_id:
                continue
            cursor = conn.execute(
                "UPDATE disclosure_events SET master_id = ? WHERE master_id = ?",
                (keep_id, master_id),
            )
            reassigned += cursor.rowcount
        _consolidate_master_fields(conn, keep_id, all_ids)
    return reassigned


def merge_by_group_key_episode(conn: sqlite3.Connection) -> int:
    reassigned = 0
    masters = conn.execute(
        """
        SELECT id, group_key, canonical_name
        FROM projects_master
        WHERE COALESCE(group_key, '') != ''
        """
    ).fetchall()
    by_group: dict[str, list[int]] = defaultdict(list)
    for row in masters:
        key = row["group_key"] or project_group_key(row["canonical_name"] or "")
        prefix = key[:18] if len(key) >= 18 else key
        by_group[prefix].append(int(row["id"]))

    for master_ids in by_group.values():
        if len(master_ids) < 2:
            continue
        placeholders = ",".join("?" for _ in master_ids)
        events = conn.execute(
            f"""
            SELECT id, master_id, summary_json, external_id, event_date, disclosure_type
            FROM disclosure_events
            WHERE master_id IN ({placeholders})
            """,
            master_ids,
        ).fetchall()
        by_episode: dict[str, set[int]] = defaultdict(set)
        event_episode: dict[int, str] = {}
        for event in events:
            episode = episode_group_key_from_event(dict(event))
            by_episode[episode].add(int(event["master_id"]))
            event_episode[int(event["id"])] = episode

        for episode, episode_master_ids in by_episode.items():
            ids = sorted(episode_master_ids)
            if len(ids) < 2:
                continue
            target_id = _pick_target_master(conn, ids)
            for master_id in ids:
                if master_id == target_id:
                    continue
                for event in events:
                    if int(event["master_id"]) != master_id:
                        continue
                    if event_episode[int(event["id"])] != episode:
                        continue
                    conn.execute(
                        "UPDATE disclosure_events SET master_id = ? WHERE id = ?",
                        (target_id, int(event["id"])),
                    )
                    reassigned += 1
            _consolidate_master_fields(conn, target_id, ids)
    return reassigned


def remove_orphan_masters(conn: sqlite3.Connection) -> int:
    orphan_ids = [
        int(row[0])
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
    return len(orphan_ids)


def bulk_merge_masters(store: EIAStore | None = None, *, verbose: bool = True) -> dict[str, Any]:
    store = store or EIAStore()
    with store.connect() as conn:
        conn.execute("PRAGMA synchronous=NORMAL")
        if verbose:
            print("补全 group_key…", flush=True)
        ensure_group_key_column(conn)
        filled = backfill_group_keys(conn)
        if verbose and filled:
            print(f"  已补全 {filled} 条 master 的 group_key", flush=True)

        if verbose:
            print("按批文号合并…", flush=True)
        by_approval = merge_by_approval_number(conn)

        if verbose:
            print("按 group_key + 环评轮次合并…", flush=True)
        by_group = merge_by_group_key_episode(conn)

        if verbose:
            print("清理孤立 master…", flush=True)
        orphans = remove_orphan_masters(conn)

        masters_after = conn.execute("SELECT COUNT(*) FROM projects_master").fetchone()[0]

    return {
        "group_keys_backfilled": filled,
        "events_reassigned_by_approval": by_approval,
        "events_reassigned_by_group": by_group,
        "orphan_masters_removed": orphans,
        "masters_remaining": masters_after,
    }


if __name__ == "__main__":
    _ensure_db_writable()
    try:
        result = bulk_merge_masters()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise SystemExit(_DB_LOCKED_HINT) from exc
        raise
    print(result)
