"""SQLite storage and full-text search for Shanghai EIA disclosures."""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

from _common import DATA_DIR, DB_PATH, DISCLOSURE_TYPES

FILE_TYPE_LABELS = {
    "report": "环评报告",
    "approval": "批复文件",
    "notice": "受理公示",
    "public_participation": "公众参与说明",
    "measures": "环保措施",
}

SORT_BY_OPTIONS = ("event_date", "synced_at", "type", "project_name")
SORT_ORDER_OPTIONS = ("asc", "desc")

# Natural disclosure workflow: acceptance → proposed → decision
TYPE_SORT_ORDER = {
    "acceptance": 1,
    "proposed_approval": 2,
    "approval_decision": 3,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EIAStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    disclosure_type TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    company TEXT DEFAULT '',
                    location TEXT DEFAULT '',
                    agency TEXT DEFAULT '',
                    approval_number TEXT DEFAULT '',
                    approval_title TEXT DEFAULT '',
                    approval_date TEXT DEFAULT '',
                    pub_period TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    source_url TEXT DEFAULT '',
                    page_no INTEGER DEFAULT 1,
                    synced_at TEXT NOT NULL,
                    UNIQUE(disclosure_type, project_name, approval_number, pub_period)
                );

                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    file_type TEXT NOT NULL,
                    file_name TEXT DEFAULT '',
                    file_url TEXT NOT NULL,
                    UNIQUE(project_id, file_type, file_url),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sync_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    trigger_mode TEXT NOT NULL,
                    message TEXT DEFAULT '',
                    stats_json TEXT DEFAULT '{}'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
                    project_name,
                    company,
                    location,
                    approval_number,
                    approval_title,
                    summary,
                    content='projects',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS projects_ai AFTER INSERT ON projects BEGIN
                    INSERT INTO projects_fts(rowid, project_name, company, location, approval_number, approval_title, summary)
                    VALUES (new.id, new.project_name, new.company, new.location, new.approval_number, new.approval_title, new.summary);
                END;

                CREATE TRIGGER IF NOT EXISTS projects_ad AFTER DELETE ON projects BEGIN
                    INSERT INTO projects_fts(projects_fts, rowid, project_name, company, location, approval_number, approval_title, summary)
                    VALUES ('delete', old.id, old.project_name, old.company, old.location, old.approval_number, old.approval_title, old.summary);
                END;

                CREATE TRIGGER IF NOT EXISTS projects_au AFTER UPDATE ON projects BEGIN
                    INSERT INTO projects_fts(projects_fts, rowid, project_name, company, location, approval_number, approval_title, summary)
                    VALUES ('delete', old.id, old.project_name, old.company, old.location, old.approval_number, old.approval_title, old.summary);
                    INSERT INTO projects_fts(rowid, project_name, company, location, approval_number, approval_title, summary)
                    VALUES (new.id, new.project_name, new.company, new.location, new.approval_number, new.approval_title, new.summary);
                END;
                """
            )

    def start_sync_job(self, trigger_mode: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_jobs(started_at, status, trigger_mode)
                VALUES (?, 'running', ?)
                """,
                (utc_now(), trigger_mode),
            )
            return int(cursor.lastrowid)

    def finish_sync_job(self, job_id: int, status: str, message: str, stats: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_jobs
                SET finished_at = ?, status = ?, message = ?, stats_json = ?
                WHERE id = ?
                """,
                (utc_now(), status, message, json.dumps(stats, ensure_ascii=False), job_id),
            )

    def update_sync_progress(self, job_id: int, message: str, stats: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            if stats is None:
                conn.execute("UPDATE sync_jobs SET message = ? WHERE id = ?", (message, job_id))
            else:
                conn.execute(
                    "UPDATE sync_jobs SET message = ?, stats_json = ? WHERE id = ?",
                    (message, json.dumps(stats, ensure_ascii=False), job_id),
                )

    def latest_sync_job(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_jobs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def upsert_records(self, records: list[dict], page_no: int = 1) -> tuple[int, int]:
        inserted_projects = 0
        inserted_files = 0
        synced_at = utc_now()

        with self.connect() as conn:
            for record in records:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        disclosure_type, project_name, company, location, agency,
                        approval_number, approval_title, approval_date, pub_period,
                        summary, source_url, page_no, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(disclosure_type, project_name, approval_number, pub_period)
                    DO UPDATE SET
                        company = excluded.company,
                        location = excluded.location,
                        agency = excluded.agency,
                        approval_title = excluded.approval_title,
                        approval_date = excluded.approval_date,
                        summary = excluded.summary,
                        source_url = excluded.source_url,
                        page_no = excluded.page_no,
                        synced_at = excluded.synced_at
                    """,
                    (
                        record["disclosure_type"],
                        record["project_name"],
                        record.get("company", ""),
                        record.get("location", ""),
                        record.get("agency", ""),
                        record.get("approval_number", ""),
                        record.get("approval_title", ""),
                        record.get("approval_date", ""),
                        record.get("pub_period", ""),
                        record.get("summary", ""),
                        record.get("source_url", ""),
                        page_no,
                        synced_at,
                    ),
                )
                if cursor.rowcount:
                    inserted_projects += 1

                project_id = conn.execute(
                    """
                    SELECT id FROM projects
                    WHERE disclosure_type = ? AND project_name = ? AND approval_number = ? AND pub_period = ?
                    """,
                    (
                        record["disclosure_type"],
                        record["project_name"],
                        record.get("approval_number", ""),
                        record.get("pub_period", ""),
                    ),
                ).fetchone()[0]

                for file_info in record.get("files", []):
                    file_cursor = conn.execute(
                        """
                        INSERT INTO files(project_id, file_type, file_name, file_url)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(project_id, file_type, file_url) DO NOTHING
                        """,
                        (project_id, file_info["file_type"], file_info["file_name"], file_info["file_url"]),
                    )
                    if file_cursor.rowcount:
                        inserted_files += 1

        return inserted_projects, inserted_files

    def filter_new_records(self, records: list[dict]) -> list[dict]:
        if not records:
            return []
        with self.connect() as conn:
            new_records: list[dict] = []
            for record in records:
                exists = conn.execute(
                    """
                    SELECT 1 FROM projects
                    WHERE disclosure_type = ? AND project_name = ? AND approval_number = ? AND pub_period = ?
                    """,
                    (
                        record["disclosure_type"],
                        record["project_name"],
                        record.get("approval_number", ""),
                        record.get("pub_period", ""),
                    ),
                ).fetchone()
                if not exists:
                    new_records.append(record)
            return new_records

    def export_database_zip(self) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("eia.db", self.db_path.read_bytes())
            archive.writestr(
                "export_info.json",
                json.dumps(
                    {
                        "exported_at": utc_now(),
                        "total_projects": self.stats()["total_projects"],
                        "total_files": self.stats()["total_files"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return buffer.getvalue()

    def import_database_bytes(self, data: bytes, backup: bool = True) -> dict[str, Any]:
        if data[:2] == b"PK":
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = archive.namelist()
                db_name = next((name for name in names if name.endswith("eia.db")), None)
                if db_name is None:
                    raise ValueError("压缩包中未找到 eia.db")
                payload = archive.read(db_name)
        else:
            payload = data

        if payload[:16] != b"SQLite format 3\x00":
            raise ValueError("不是有效的 SQLite 数据库文件")

        before = self.stats() if self.db_path.exists() else {"total_projects": 0, "total_files": 0}
        if backup and self.db_path.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_name(f"eia.db.bak_{stamp}")
            shutil.copy2(self.db_path, backup_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_bytes(payload)
        self._init_db()
        after = self.stats()
        return {
            "restored_projects": after["total_projects"],
            "restored_files": after["total_files"],
            "previous_projects": before["total_projects"],
            "previous_files": before["total_files"],
        }

    def search(
        self,
        query: str,
        disclosure_types: list[str] | None = None,
        limit: int = 50,
        sort_by: str = "event_date",
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        query = query.strip()
        types = disclosure_types or list(DISCLOSURE_TYPES)
        placeholders = ",".join("?" for _ in types)
        order_clause = self._build_order_clause(sort_by, sort_order, table_alias="")

        with self.connect() as conn:
            if query:
                rows = self._search_with_fts(conn, query, types, limit, sort_by, sort_order)
                if not rows:
                    rows = self._search_with_like(conn, query, types, limit, sort_by, sort_order)
            else:
                rows = conn.execute(
                    f"""
                    SELECT * FROM projects
                    WHERE disclosure_type IN ({placeholders})
                    ORDER BY {order_clause}
                    LIMIT ?
                    """,
                    (*types, limit),
                ).fetchall()

            results: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["disclosure_label"] = DISCLOSURE_TYPES[item["disclosure_type"]]["label"]
                item["files"] = [
                    dict(file_row)
                    for file_row in conn.execute(
                        "SELECT * FROM files WHERE project_id = ? ORDER BY file_type",
                        (item["id"],),
                    ).fetchall()
                ]
                for file_item in item["files"]:
                    file_item["file_type_label"] = FILE_TYPE_LABELS.get(file_item["file_type"], file_item["file_type"])
                results.append(item)
            return results

    def get_files(self, file_ids: list[int]) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        placeholders = ",".join("?" for _ in file_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT f.*, p.project_name, p.disclosure_type
                FROM files f
                JOIN projects p ON p.id = f.project_id
                WHERE f.id IN ({placeholders})
                """,
                tuple(file_ids),
            ).fetchall()
            return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            total_projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            by_type = {
                row["disclosure_type"]: row["count"]
                for row in conn.execute(
                    "SELECT disclosure_type, COUNT(*) AS count FROM projects GROUP BY disclosure_type"
                ).fetchall()
            }
            latest = self.latest_sync_job()
            return {
                "total_projects": total_projects,
                "total_files": total_files,
                "by_type": by_type,
                "latest_sync": latest,
            }

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [token.strip() for token in query.split() if token.strip()]
        if not tokens:
            return ""
        return " ".join(f"{token}*" for token in tokens)

    @classmethod
    def _build_order_clause(cls, sort_by: str, sort_order: str, table_alias: str = "p.") -> str:
        if sort_by not in SORT_BY_OPTIONS:
            sort_by = "event_date"
        if sort_order not in SORT_ORDER_OPTIONS:
            sort_order = "desc"
        direction = "ASC" if sort_order == "asc" else "DESC"
        prefix = table_alias

        if sort_by == "type":
            cases = " ".join(
                f"WHEN '{key}' THEN {order}" for key, order in TYPE_SORT_ORDER.items()
            )
            type_expr = f"CASE {prefix}disclosure_type {cases} ELSE 99 END"
            tie_breaker = f"{prefix}synced_at DESC, {prefix}id DESC"
            if sort_order == "desc":
                return f"{type_expr} DESC, {tie_breaker}"
            return f"{type_expr} ASC, {tie_breaker}"

        if sort_by == "event_date":
            column = cls._event_date_sql(prefix)
        elif sort_by == "synced_at":
            column = f"{prefix}synced_at"
        else:
            column = f"{prefix}project_name"

        id_col = f"{prefix}id" if prefix else "id"
        name_col = f"{prefix}project_name"
        return f"{column} {direction}, {name_col} ASC, {id_col} ASC"

    @staticmethod
    def _event_date_sql(prefix: str) -> str:
        return (
            f"COALESCE("
            f"NULLIF({prefix}approval_date, ''), "
            f"CASE WHEN {prefix}pub_period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*' "
            f"THEN substr({prefix}pub_period, 1, 10) ELSE NULL END, "
            f"{prefix}synced_at)"
        )

    def _search_with_fts(
        self, conn, query: str, types: list[str], limit: int, sort_by: str, sort_order: str
    ):
        placeholders = ",".join("?" for _ in types)
        order_clause = self._build_order_clause(sort_by, sort_order, table_alias="p.")
        return conn.execute(
            f"""
            SELECT p.* FROM projects p
            JOIN projects_fts fts ON fts.rowid = p.id
            WHERE projects_fts MATCH ?
              AND p.disclosure_type IN ({placeholders})
            ORDER BY {order_clause}
            LIMIT ?
            """,
            (self._fts_query(query), *types, limit),
        ).fetchall()

    def _search_with_like(
        self, conn, query: str, types: list[str], limit: int, sort_by: str, sort_order: str
    ):
        placeholders = ",".join("?" for _ in types)
        pattern = f"%{query}%"
        order_clause = self._build_order_clause(sort_by, sort_order, table_alias="")
        return conn.execute(
            f"""
            SELECT * FROM projects
            WHERE disclosure_type IN ({placeholders})
              AND (
                project_name LIKE ? OR company LIKE ? OR location LIKE ?
                OR approval_number LIKE ? OR approval_title LIKE ? OR summary LIKE ?
              )
            ORDER BY {order_clause}
            LIMIT ?
            """,
            (*types, pattern, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
