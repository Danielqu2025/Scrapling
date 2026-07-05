"""Migrate legacy v1 projects/files schema to v2 master/event model."""

from __future__ import annotations

import json
import sqlite3

from db.resolver import MasterResolver, get_schema_version, pick_event_date
from db.schema import FTS_REBUILD_SQL, SCHEMA_V2_SQL, SCHEMA_VERSION


def migrate_if_needed(conn: sqlite3.Connection, now: str) -> bool:
    version = get_schema_version(conn)
    if version >= SCHEMA_VERSION:
        return False
    if version == 1:
        _migrate_v1_to_v2(conn, now)
        return True
    return False


def ensure_v2_files_table(conn: sqlite3.Connection) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()]
    if columns and "event_id" not in columns:
        conn.execute("ALTER TABLE files RENAME TO files_v1_backup")
        conn.execute(
            """
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                file_name TEXT DEFAULT '',
                file_url TEXT DEFAULT '',
                file_external_id TEXT DEFAULT '',
                download_status TEXT DEFAULT 'direct',
                UNIQUE(event_id, file_type, file_url),
                FOREIGN KEY(event_id) REFERENCES disclosure_events(id) ON DELETE CASCADE
            )
            """
        )


def _migrate_v1_to_v2(conn: sqlite3.Connection, now: str) -> None:
    conn.executescript(SCHEMA_V2_SQL)
    old_projects = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    old_files = conn.execute("SELECT * FROM files ORDER BY id").fetchall()
    ensure_v2_files_table(conn)
    resolver = MasterResolver(conn, now)
    files_by_project: dict[int, list[sqlite3.Row]] = {}
    for row in old_files:
        files_by_project.setdefault(int(row["project_id"]), []).append(row)

    event_id_map: dict[int, int] = {}

    for row in old_projects:
        record = {
            "source": "link_sthj",
            "disclosure_type": row["disclosure_type"],
            "external_id": "|".join(
                [
                    row["disclosure_type"],
                    row["project_name"],
                    row["approval_number"] or "",
                    row["pub_period"] or "",
                ]
            ),
            "project_name": row["project_name"],
            "company": row["company"] or "",
            "location": row["location"] or "",
            "approval_number": row["approval_number"] or "",
            "approval_date": row["approval_date"] or "",
            "pub_period": row["pub_period"] or "",
            "event_date": pick_event_date(
                {
                    "approval_date": row["approval_date"] or "",
                    "pub_period": row["pub_period"] or "",
                }
            ),
            "lifecycle_stage": "",
            "title": row["project_name"],
            "summary_json": json.dumps(
                {
                    "agency": row["agency"] or "",
                    "approval_title": row["approval_title"] or "",
                    "summary": row["summary"] or "",
                    "pub_period": row["pub_period"] or "",
                },
                ensure_ascii=False,
            ),
            "source_url": row["source_url"] or "",
            "page_no": row["page_no"] or 1,
        }
        master_id = resolver.resolve_master_id(record)
        cursor = conn.execute(
            """
            INSERT INTO disclosure_events (
                master_id, source, disclosure_type, external_id, lifecycle_stage,
                event_date, title, summary_json, source_url, page_no, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, disclosure_type, external_id) DO UPDATE SET
                master_id = excluded.master_id,
                event_date = excluded.event_date,
                title = excluded.title,
                summary_json = excluded.summary_json,
                source_url = excluded.source_url,
                page_no = excluded.page_no,
                synced_at = excluded.synced_at
            """,
            (
                master_id,
                record["source"],
                record["disclosure_type"],
                record["external_id"],
                record["lifecycle_stage"],
                record["event_date"],
                record["title"],
                record["summary_json"],
                record["source_url"],
                record["page_no"],
                row["synced_at"],
            ),
        )
        event_id = conn.execute(
            """
            SELECT id FROM disclosure_events
            WHERE source = ? AND disclosure_type = ? AND external_id = ?
            """,
            (record["source"], record["disclosure_type"], record["external_id"]),
        ).fetchone()[0]
        event_id_map[int(row["id"])] = int(event_id)

        for file_row in files_by_project.get(int(row["id"]), []):
            conn.execute(
                """
                INSERT INTO files(event_id, file_type, file_name, file_url, download_status)
                VALUES (?, ?, ?, ?, 'direct')
                ON CONFLICT(event_id, file_type, file_url) DO NOTHING
                """,
                (
                    event_id,
                    file_row["file_type"],
                    file_row["file_name"] or "",
                    file_row["file_url"] or "",
                ),
            )

    conn.execute("ALTER TABLE projects RENAME TO projects_v1_backup")
    if "files_v1_backup" not in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        try:
            conn.execute("ALTER TABLE files RENAME TO files_v1_backup")
        except sqlite3.OperationalError:
            pass
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS projects_ai;
        DROP TRIGGER IF EXISTS projects_ad;
        DROP TRIGGER IF EXISTS projects_au;
        """
    )
    try:
        conn.execute("DROP TABLE IF EXISTS projects_fts")
    except sqlite3.OperationalError:
        pass

    conn.executescript(FTS_REBUILD_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def _master_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(projects_master)").fetchall()}


def ensure_group_key_column(conn: sqlite3.Connection) -> bool:
    """Add projects_master.group_key if missing. Returns True when column was added."""
    if "group_key" in _master_columns(conn):
        return False
    conn.execute("ALTER TABLE projects_master ADD COLUMN group_key TEXT DEFAULT ''")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_master_group_key
        ON projects_master(group_key)
        WHERE group_key != ''
        """
    )
    return True


def backfill_group_keys(conn: sqlite3.Connection, batch_size: int = 5000) -> int:
    from db.resolver import project_group_key

    updated = 0
    while True:
        rows = conn.execute(
            """
            SELECT id, canonical_name FROM projects_master
            WHERE COALESCE(group_key, '') = ''
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            conn.execute(
                "UPDATE projects_master SET group_key = ? WHERE id = ?",
                (project_group_key(row["canonical_name"] or ""), int(row["id"])),
            )
            updated += 1
    return updated
