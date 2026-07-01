"""SQLite storage: projects_master + disclosure_events (schema v2)."""

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

from _paths import DB_PATH, MANIFEST_PATH
from db.migrate import migrate_if_needed
from db.resolver import MasterResolver, episode_group_key_from_event, episode_key_from_event, event_matches_episode, get_schema_version, pick_event_date
from db.schema import FTS_REBUILD_SQL, SCHEMA_V2_SQL, SCHEMA_VERSION
from db.timeline_view import build_progress_view
from sources.types import DISCLOSURE_TYPES, FILE_TYPE_LABELS, SORT_BY_OPTIONS, SORT_ORDER_OPTIONS, SOURCE_E2_QYGK, SOURCES, TYPE_SORT_ORDER

SUMMARY_YEAR_JSON_KEYS = (
    "approval_date",
    "planned_start_date",
    "actual_start_date",
    "completion_date",
    "debug_start_date",
    "acceptance_pub_start",
    "pre_pub_period",
    "pub_period",
    "approval_number",
)


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
            version = get_schema_version(conn)
            if version == 0:
                conn.executescript(SCHEMA_V2_SQL)
            elif version == 1:
                migrate_if_needed(conn, utc_now())
            else:
                conn.executescript(SCHEMA_V2_SQL)
                columns = [row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()]
                if columns and "event_id" not in columns:
                    from db.migrate import ensure_v2_files_table

                    ensure_v2_files_table(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def start_sync_job(self, trigger_mode: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sync_jobs(started_at, status, trigger_mode) VALUES (?, 'running', ?)",
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
            row = conn.execute("SELECT * FROM sync_jobs ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def upsert_events(self, records: list[dict], page_no: int = 1) -> tuple[int, int]:
        events_saved = 0
        files_saved = 0
        synced_at = utc_now()

        with self.connect() as conn:
            resolver = MasterResolver(conn, synced_at)
            for record in records:
                source = record["source"]
                disclosure_type = record["disclosure_type"]
                external_id = record.get("external_id") or self._default_external_id(record)
                event_date = pick_event_date(record)
                summary = record.get("summary_json")
                if isinstance(summary, dict):
                    summary_text = json.dumps(summary, ensure_ascii=False)
                else:
                    summary_text = summary or "{}"

                master_id = resolver.resolve_master_id({**record, "external_id": external_id})
                cursor = conn.execute(
                    """
                    INSERT INTO disclosure_events (
                        master_id, source, disclosure_type, external_id, lifecycle_stage,
                        event_date, title, summary_json, source_url, page_no, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, disclosure_type, external_id) DO UPDATE SET
                        master_id = excluded.master_id,
                        lifecycle_stage = excluded.lifecycle_stage,
                        event_date = excluded.event_date,
                        title = excluded.title,
                        summary_json = excluded.summary_json,
                        source_url = excluded.source_url,
                        page_no = excluded.page_no,
                        synced_at = excluded.synced_at
                    """,
                    (
                        master_id,
                        source,
                        disclosure_type,
                        external_id,
                        record.get("lifecycle_stage", ""),
                        event_date,
                        record.get("title") or record.get("project_name", ""),
                        summary_text,
                        record.get("source_url", ""),
                        record.get("page_no", page_no),
                        synced_at,
                    ),
                )
                if cursor.rowcount:
                    events_saved += 1

                event_id = conn.execute(
                    """
                    SELECT id FROM disclosure_events
                    WHERE source = ? AND disclosure_type = ? AND external_id = ?
                    """,
                    (source, disclosure_type, external_id),
                ).fetchone()[0]

                for file_info in record.get("files", []):
                    download_status = file_info.get("download_status", "direct")
                    file_external_id = file_info.get("file_external_id", "")
                    if file_external_id and file_info.get("file_type") != "attachment":
                        conn.execute(
                            """
                            DELETE FROM files
                            WHERE event_id = ? AND file_external_id = ? AND file_type = 'attachment'
                            """,
                            (event_id, file_external_id),
                        )
                    file_cursor = conn.execute(
                        """
                        INSERT INTO files(
                            event_id, file_type, file_name, file_url, file_external_id, download_status
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id, file_type, file_url) DO UPDATE SET
                            file_name = excluded.file_name,
                            file_external_id = excluded.file_external_id,
                            download_status = excluded.download_status
                        """,
                        (
                            event_id,
                            file_info["file_type"],
                            file_info.get("file_name", ""),
                            file_info.get("file_url", ""),
                            file_info.get("file_external_id", ""),
                            download_status,
                        ),
                    )
                    if file_cursor.rowcount:
                        files_saved += 1

        return events_saved, files_saved

    def upsert_records(self, records: list[dict], page_no: int = 1) -> tuple[int, int]:
        from sources.link_sthj.adapter import link_records_to_events

        return self.upsert_events(link_records_to_events(records), page_no=page_no)

    def filter_new_events(self, records: list[dict]) -> list[dict]:
        if not records:
            return []
        with self.connect() as conn:
            new_records: list[dict] = []
            for record in records:
                source = record["source"]
                disclosure_type = record["disclosure_type"]
                external_id = record.get("external_id") or self._default_external_id(record)
                exists = conn.execute(
                    """
                    SELECT 1 FROM disclosure_events
                    WHERE source = ? AND disclosure_type = ? AND external_id = ?
                    """,
                    (source, disclosure_type, external_id),
                ).fetchone()
                if not exists:
                    new_records.append(record)
            return new_records

    def filter_new_records(self, records: list[dict]) -> list[dict]:
        from sources.link_sthj.adapter import link_records_to_events

        events = link_records_to_events(records)
        return self.filter_new_events(events)

    def get_source_type_coverage(self, source: str, disclosure_type: str) -> dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS event_count, COALESCE(MAX(page_no), 0) AS max_page
                FROM disclosure_events
                WHERE source = ? AND disclosure_type = ?
                """,
                (source, disclosure_type),
            ).fetchone()
            return {"event_count": int(row["event_count"]), "max_page": int(row["max_page"])}

    def count_e2_incomplete_details(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM disclosure_events
                WHERE source = ? AND disclosure_type = 'post_construction'
                  AND (
                    COALESCE(json_extract(summary_json, '$.approval_number'), '') = ''
                    AND COALESCE(json_extract(summary_json, '$.approval_date'), '') = ''
                    AND COALESCE(json_extract(summary_json, '$.actual_start_date'), '') = ''
                    AND COALESCE(json_extract(summary_json, '$.debug_start_date'), '') = ''
                    AND COALESCE(json_extract(summary_json, '$.completion_date'), '') = ''
                  )
                """,
                (SOURCE_E2_QYGK,),
            ).fetchone()
            return int(row[0])

    def count_e2_without_list_year(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM disclosure_events
                WHERE source = ? AND disclosure_type = 'post_construction'
                  AND COALESCE(json_extract(summary_json, '$.list_year'), '') = ''
                """,
                (SOURCE_E2_QYGK,),
            ).fetchone()
            return int(row[0])

    def count_e2_by_list_year(self, year: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM disclosure_events
                WHERE source = ? AND disclosure_type = 'post_construction'
                  AND json_extract(summary_json, '$.list_year') = ?
                """,
                (SOURCE_E2_QYGK, year),
            ).fetchone()
            return int(row[0])

    def get_max_synced_page(self, source: str, disclosure_type: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(page_no) FROM disclosure_events
                WHERE source = ? AND disclosure_type = ?
                """,
                (source, disclosure_type),
            ).fetchone()
            return int(row[0] or 0)

    def existing_external_ids(self, source: str, disclosure_type: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT external_id FROM disclosure_events
                WHERE source = ? AND disclosure_type = ?
                """,
                (source, disclosure_type),
            ).fetchall()
            return {row[0] for row in rows if row[0]}

    def e2_detail_summary_cache(self, source: str, disclosure_type: str) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT external_id, summary_json FROM disclosure_events
                WHERE source = ? AND disclosure_type = ?
                """,
                (source, disclosure_type),
            ).fetchall()
            cache: dict[str, dict[str, Any]] = {}
            for row in rows:
                try:
                    summary = json.loads(row["summary_json"] or "{}")
                except json.JSONDecodeError:
                    summary = {}
                if isinstance(summary, dict):
                    cache[row["external_id"]] = summary
            return cache

    def get_timeline(self, master_id: int, episode_key: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            master = conn.execute("SELECT * FROM projects_master WHERE id = ?", (master_id,)).fetchone()
            if not master:
                return None
            events = conn.execute(
                """
                SELECT * FROM disclosure_events
                WHERE master_id = ?
                ORDER BY COALESCE(NULLIF(event_date, ''), synced_at) ASC, id ASC
                """,
                (master_id,),
            ).fetchall()
            timeline: list[dict[str, Any]] = []
            for event in events:
                item = dict(event)
                item["disclosure_label"] = DISCLOSURE_TYPES.get(item["disclosure_type"], {}).get(
                    "label", item["disclosure_type"]
                )
                item["source_label"] = item["source"]
                try:
                    item["summary"] = json.loads(item.get("summary_json") or "{}")
                except json.JSONDecodeError:
                    item["summary"] = {}
                item["files"] = [
                    self._enrich_file(dict(file_row))
                    for file_row in conn.execute(
                        "SELECT * FROM files WHERE event_id = ? ORDER BY file_type",
                        (item["id"],),
                    ).fetchall()
                ]
                timeline.append(item)
            if episode_key:
                timeline = [item for item in timeline if event_matches_episode(item, episode_key)]
            return {"master": dict(master), "events": timeline}

    def get_progress(self, master_id: int, episode_key: str | None = None) -> dict[str, Any] | None:
        timeline = self.get_timeline(master_id, episode_key=episode_key)
        if timeline is None:
            return None
        master = dict(timeline["master"])
        if episode_key and not episode_key.startswith("round_"):
            master["approval_number"] = episode_key
        elif episode_key and episode_key.startswith("round_"):
            for event in timeline["events"]:
                label = episode_key_from_event(event)
                if label and not label.startswith("round_"):
                    master["approval_number"] = label
                    break
        return build_progress_view(master, timeline["events"])

    def get_file_detail(self, file_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT f.*, m.canonical_name AS project_name, m.id AS master_id,
                       e.disclosure_type, e.source, e.external_id AS event_external_id,
                       e.source_url AS event_source_url
                FROM files f
                JOIN disclosure_events e ON e.id = f.event_id
                JOIN projects_master m ON m.id = e.master_id
                WHERE f.id = ?
                """,
                (file_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["file_type_label"] = FILE_TYPE_LABELS.get(item["file_type"], item["file_type"])
            return item

    def search(
        self,
        query: str,
        disclosure_types: list[str] | None = None,
        limit: int = 50,
        sort_by: str = "event_date",
        sort_order: str = "desc",
        year: str | None = None,
        district: str | None = None,
        lifecycle_stage: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        query = query.strip()
        types = disclosure_types or list(DISCLOSURE_TYPES)
        type_placeholders = ",".join("?" for _ in types)
        filter_sql, filter_params = self._search_filter_sql(year, district, lifecycle_stage, source)

        with self.connect() as conn:
            if query:
                master_ids = self._search_master_ids(conn, query, types, limit * 3, filter_sql, filter_params)
                if not master_ids:
                    rows = self._search_events_like(
                        conn, query, types, limit, sort_by, sort_order, filter_sql, filter_params
                    )
                else:
                    rows = self._fetch_events_for_masters(
                        conn,
                        master_ids,
                        types,
                        limit,
                        sort_by,
                        sort_order,
                        filter_sql,
                        filter_params,
                    )
            else:
                order_clause = self._build_order_clause(sort_by, sort_order, table_alias="e.", master_alias="m.")
                rows = conn.execute(
                    f"""
                    SELECT e.*, m.canonical_name AS project_name, m.company, m.approval_number,
                           m.location, m.district, m.st_eia_id, e.id AS event_id, m.id AS master_id
                    FROM disclosure_events e
                    JOIN projects_master m ON m.id = e.master_id
                    WHERE e.disclosure_type IN ({type_placeholders})
                      {filter_sql}
                    ORDER BY {order_clause}
                    LIMIT ?
                    """,
                    (*types, *filter_params, limit),
                ).fetchall()

            return [self._event_row_to_result(conn, row) for row in rows]

    def search_facets(self, disclosure_types: list[str] | None = None) -> dict[str, Any]:
        types = disclosure_types or list(DISCLOSURE_TYPES)
        type_placeholders = ",".join("?" for _ in types)
        with self.connect() as conn:
            years = conn.execute(
                f"""
                SELECT value, COUNT(*) AS count FROM (
                    SELECT substr(COALESCE(NULLIF(e.event_date, ''), e.synced_at), 1, 4) AS value
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND substr(COALESCE(NULLIF(e.event_date, ''), e.synced_at), 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
                    UNION ALL
                    SELECT substr(json_extract(e.summary_json, '$.approval_date'), 1, 4)
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND json_extract(e.summary_json, '$.approval_date') GLOB '[0-9][0-9][0-9][0-9]*'
                    UNION ALL
                    SELECT substr(json_extract(e.summary_json, '$.actual_start_date'), 1, 4)
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND json_extract(e.summary_json, '$.actual_start_date') GLOB '[0-9][0-9][0-9][0-9]*'
                    UNION ALL
                    SELECT substr(json_extract(e.summary_json, '$.completion_date'), 1, 4)
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND json_extract(e.summary_json, '$.completion_date') GLOB '[0-9][0-9][0-9][0-9]*'
                    UNION ALL
                    SELECT substr(json_extract(e.summary_json, '$.debug_start_date'), 1, 4)
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND json_extract(e.summary_json, '$.debug_start_date') GLOB '[0-9][0-9][0-9][0-9]*'
                    UNION ALL
                    SELECT substr(json_extract(e.summary_json, '$.acceptance_pub_start'), 1, 4)
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND json_extract(e.summary_json, '$.acceptance_pub_start') GLOB '[0-9][0-9][0-9][0-9]*'
                    UNION ALL
                    SELECT substr(json_extract(e.summary_json, '$.pub_period'), 1, 4)
                    FROM disclosure_events e
                    WHERE e.disclosure_type IN ({type_placeholders})
                      AND json_extract(e.summary_json, '$.pub_period') GLOB '[0-9][0-9][0-9][0-9]*'
                )
                WHERE value GLOB '[0-9][0-9][0-9][0-9]'
                GROUP BY value
                ORDER BY value DESC
                """,
                (*types, *types, *types, *types, *types, *types, *types),
            ).fetchall()
            districts = conn.execute(
                f"""
                SELECT m.district AS value, COUNT(DISTINCT m.id) AS count
                FROM disclosure_events e
                JOIN projects_master m ON m.id = e.master_id
                WHERE e.disclosure_type IN ({type_placeholders})
                  AND m.district != ''
                GROUP BY m.district
                ORDER BY count DESC, value ASC
                """,
                types,
            ).fetchall()
            lifecycle_stages = conn.execute(
                f"""
                SELECT e.lifecycle_stage AS value, COUNT(*) AS count
                FROM disclosure_events e
                WHERE e.disclosure_type IN ({type_placeholders})
                  AND e.lifecycle_stage != ''
                GROUP BY e.lifecycle_stage
                ORDER BY count DESC, value ASC
                """,
                types,
            ).fetchall()
            sources = conn.execute(
                f"""
                SELECT e.source AS value, COUNT(*) AS count
                FROM disclosure_events e
                WHERE e.disclosure_type IN ({type_placeholders})
                GROUP BY e.source
                ORDER BY count DESC
                """,
                types,
            ).fetchall()
            type_counts = {
                row["disclosure_type"]: row["count"]
                for row in conn.execute(
                    f"""
                    SELECT disclosure_type, COUNT(*) AS count
                    FROM disclosure_events
                    WHERE disclosure_type IN ({type_placeholders})
                    GROUP BY disclosure_type
                    """,
                    types,
                ).fetchall()
            }
        return {
            "years": [{"value": row["value"], "count": row["count"]} for row in years],
            "districts": [{"value": row["value"], "count": row["count"]} for row in districts],
            "lifecycle_stages": [{"value": row["value"], "count": row["count"]} for row in lifecycle_stages],
            "sources": [
                {
                    "value": row["value"],
                    "label": SOURCES.get(row["value"], {}).get("label", row["value"]),
                    "count": row["count"],
                }
                for row in sources
            ],
            "disclosure_types": [
                {
                    "value": key,
                    "label": DISCLOSURE_TYPES[key]["label"],
                    "count": type_counts.get(key, 0),
                }
                for key in types
            ],
        }

    def get_files(self, file_ids: list[int]) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        placeholders = ",".join("?" for _ in file_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT f.*, m.canonical_name AS project_name, e.disclosure_type, e.source
                FROM files f
                JOIN disclosure_events e ON e.id = f.event_id
                JOIN projects_master m ON m.id = e.master_id
                WHERE f.id IN ({placeholders})
                """,
                tuple(file_ids),
            ).fetchall()
            return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            total_masters = conn.execute("SELECT COUNT(*) FROM projects_master").fetchone()[0]
            total_events = conn.execute("SELECT COUNT(*) FROM disclosure_events").fetchone()[0]
            total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            by_type = {
                row["disclosure_type"]: row["count"]
                for row in conn.execute(
                    "SELECT disclosure_type, COUNT(*) AS count FROM disclosure_events GROUP BY disclosure_type"
                ).fetchall()
            }
            by_source = {
                row["source"]: row["count"]
                for row in conn.execute(
                    "SELECT source, COUNT(*) AS count FROM disclosure_events GROUP BY source"
                ).fetchall()
            }
            latest = self.latest_sync_job()
            return {
                "schema_version": SCHEMA_VERSION,
                "total_masters": total_masters,
                "total_events": total_events,
                "total_projects": total_masters,
                "total_files": total_files,
                "by_type": by_type,
                "by_source": by_source,
                "latest_sync": latest,
            }

    def build_manifest(self) -> dict[str, Any]:
        stats = self.stats()
        return {
            "exported_at": utc_now(),
            "stats": stats,
            "events_by_type": stats["by_type"],
            "events_by_source": stats["by_source"],
        }

    def write_manifest(self) -> dict[str, Any]:
        manifest = self.build_manifest()
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def read_manifest(self) -> dict[str, Any] | None:
        if not MANIFEST_PATH.exists():
            return None
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def export_database_zip(self) -> bytes:
        buffer = BytesIO()
        stats = self.stats()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("eia.db", self.db_path.read_bytes())
            archive.writestr(
                "export_info.json",
                json.dumps(
                    {
                        "exported_at": utc_now(),
                        "schema_version": SCHEMA_VERSION,
                        "total_masters": stats["total_masters"],
                        "total_events": stats["total_events"],
                        "total_files": stats["total_files"],
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

        before = self.stats() if self.db_path.exists() else {"total_masters": 0, "total_events": 0, "total_files": 0}
        if backup and self.db_path.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.db_path.with_name(f"eia.db.bak_{stamp}")
            shutil.copy2(self.db_path, backup_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_bytes(payload)
        self._init_db()
        after = self.stats()
        return {
            "restored_masters": after["total_masters"],
            "restored_events": after["total_events"],
            "restored_projects": after["total_masters"],
            "restored_files": after["total_files"],
            "previous_masters": before.get("total_masters", before.get("total_projects", 0)),
            "previous_events": before.get("total_events", 0),
            "previous_files": before["total_files"],
        }

    @staticmethod
    def _default_external_id(record: dict[str, Any]) -> str:
        return "|".join(
            [
                record.get("disclosure_type", ""),
                record.get("project_name", ""),
                record.get("approval_number", ""),
                record.get("pub_period", ""),
            ]
        )

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [token.strip() for token in query.split() if token.strip()]
        if not tokens:
            return ""
        return " ".join(f"{token}*" for token in tokens)

    @classmethod
    def _build_order_clause(
        cls, sort_by: str, sort_order: str, table_alias: str = "e.", master_alias: str = "m."
    ) -> str:
        if sort_by not in SORT_BY_OPTIONS:
            sort_by = "event_date"
        if sort_order not in SORT_ORDER_OPTIONS:
            sort_order = "desc"
        direction = "ASC" if sort_order == "asc" else "DESC"

        if sort_by == "type":
            cases = " ".join(f"WHEN '{key}' THEN {order}" for key, order in TYPE_SORT_ORDER.items())
            type_expr = f"CASE {table_alias}disclosure_type {cases} ELSE 99 END"
            tie = f"{table_alias}event_date DESC, {master_alias}canonical_name ASC, {table_alias}id ASC"
            if sort_order == "desc":
                return f"{type_expr} DESC, {tie}"
            return f"{type_expr} ASC, {tie}"

        if sort_by == "event_date":
            column = (
                f"COALESCE(NULLIF({table_alias}event_date, ''), {table_alias}synced_at)"
            )
        elif sort_by == "synced_at":
            column = f"{table_alias}synced_at"
        else:
            column = f"{master_alias}canonical_name"

        return f"{column} {direction}, {master_alias}canonical_name ASC, {table_alias}id ASC"

    @staticmethod
    def _search_filter_sql(
        year: str | None,
        district: str | None,
        lifecycle_stage: str | None,
        source: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if year:
            year_checks = [
                "substr(COALESCE(NULLIF(e.event_date, ''), e.synced_at), 1, 4) = ?",
            ]
            params.append(year)
            for key in SUMMARY_YEAR_JSON_KEYS:
                year_checks.append(f"json_extract(e.summary_json, '$.{key}') LIKE ?")
                params.append(f"{year}%")
            clauses.append(f"AND ({' OR '.join(year_checks)})")
        if district:
            clauses.append("AND m.district = ?")
            params.append(district)
        if lifecycle_stage:
            clauses.append("AND e.lifecycle_stage = ?")
            params.append(lifecycle_stage)
        if source:
            clauses.append("AND e.source = ?")
            params.append(source)
        return " ".join(clauses), params

    def _search_master_ids(
        self,
        conn: sqlite3.Connection,
        query: str,
        types: list[str],
        limit: int,
        filter_sql: str,
        filter_params: list[Any],
    ) -> list[int]:
        placeholders = ",".join("?" for _ in types)
        rows = conn.execute(
            f"""
            SELECT DISTINCT m.id
            FROM projects_master m
            JOIN masters_fts fts ON fts.rowid = m.id
            JOIN disclosure_events e ON e.master_id = m.id
            WHERE masters_fts MATCH ?
              AND e.disclosure_type IN ({placeholders})
              {filter_sql}
            LIMIT ?
            """,
            (self._fts_query(query), *types, *filter_params, limit),
        ).fetchall()
        return [int(row[0]) for row in rows]

    def _fetch_events_for_masters(
        self,
        conn: sqlite3.Connection,
        master_ids: list[int],
        types: list[str],
        limit: int,
        sort_by: str,
        sort_order: str,
        filter_sql: str,
        filter_params: list[Any],
    ):
        if not master_ids:
            return []
        id_placeholders = ",".join("?" for _ in master_ids)
        type_placeholders = ",".join("?" for _ in types)
        order_clause = self._build_order_clause(sort_by, sort_order, table_alias="e.", master_alias="m.")
        return conn.execute(
            f"""
            SELECT e.*, m.canonical_name AS project_name, m.company, m.approval_number,
                   m.location, m.district, m.st_eia_id, e.id AS event_id, m.id AS master_id
            FROM disclosure_events e
            JOIN projects_master m ON m.id = e.master_id
            WHERE m.id IN ({id_placeholders})
              AND e.disclosure_type IN ({type_placeholders})
              {filter_sql}
            ORDER BY {order_clause}
            LIMIT ?
            """,
            (*master_ids, *types, *filter_params, limit),
        ).fetchall()

    def _search_events_like(
        self,
        conn: sqlite3.Connection,
        query: str,
        types: list[str],
        limit: int,
        sort_by: str,
        sort_order: str,
        filter_sql: str,
        filter_params: list[Any],
    ):
        placeholders = ",".join("?" for _ in types)
        pattern = f"%{query}%"
        order_clause = self._build_order_clause(sort_by, sort_order, table_alias="e.", master_alias="m.")
        return conn.execute(
            f"""
            SELECT e.*, m.canonical_name AS project_name, m.company, m.approval_number,
                   m.location, m.district, m.st_eia_id, e.id AS event_id, m.id AS master_id
            FROM disclosure_events e
            JOIN projects_master m ON m.id = e.master_id
            WHERE e.disclosure_type IN ({placeholders})
              {filter_sql}
              AND (
                m.canonical_name LIKE ? OR m.company LIKE ? OR m.location LIKE ?
                OR m.approval_number LIKE ? OR e.title LIKE ? OR e.summary_json LIKE ?
                OR e.lifecycle_stage LIKE ?
              )
            ORDER BY {order_clause}
            LIMIT ?
            """,
            (*types, *filter_params, pattern, pattern, pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()

    def _event_row_to_result(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        event_id = item.get("event_id") or item["id"]
        item["id"] = event_id
        item["disclosure_label"] = DISCLOSURE_TYPES.get(item["disclosure_type"], {}).get(
            "label", item["disclosure_type"]
        )
        summary = {}
        try:
            summary = json.loads(item.get("summary_json") or "{}")
        except json.JSONDecodeError:
            pass
        item["approval_title"] = summary.get("approval_title", "")
        item["pub_period"] = summary.get("pub_period", "")
        item["summary"] = summary.get("summary", "")
        item["agency"] = summary.get("agency", "")
        event_approval = summary.get("approval_number", "")
        if not event_approval:
            parts = (item.get("external_id") or "").split("|")
            if len(parts) >= 3:
                event_approval = parts[2].strip()
        item["event_approval_number"] = event_approval or item.get("approval_number", "")
        episode_source = {**item, "approval_number": event_approval}
        item["episode_key"] = episode_group_key_from_event(episode_source)
        item["episode_label"] = event_approval or item["episode_key"]
        item["files"] = [
            self._enrich_file(dict(file_row))
            for file_row in conn.execute(
                "SELECT * FROM files WHERE event_id = ? ORDER BY file_type",
                (event_id,),
            ).fetchall()
        ]
        return item

    @staticmethod
    def _enrich_file(file_row: dict[str, Any]) -> dict[str, Any]:
        file_row["file_type_label"] = FILE_TYPE_LABELS.get(file_row["file_type"], file_row["file_type"])
        return file_row
