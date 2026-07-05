"""Resolve disclosure events to projects_master records."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from typing import Any
from db.schema import SCHEMA_VERSION

SUMMARY_DATE_KEYS = (
    "approval_date",
    "planned_start_date",
    "actual_start_date",
    "completion_date",
    "debug_start_date",
    "acceptance_pub_start",
    "pre_pub_period",
    "pub_period",
)


def normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\.{2,}|…+$", "", text)
    return text


def project_group_key(name: str) -> str:
    """Stable key for clustering truncated variants of the same project name."""
    text = normalize_text(name)
    if not text:
        return ""
    for marker in ("中试服务平台", "中试服务", "中试服", "合成生物学中心"):
        idx = text.find(marker)
        if idx >= 0:
            return text[: idx + len(marker)]
    return text[:24] if len(text) > 24 else text


def search_card_cluster_key(
    display_name: str,
    *,
    master_id: int,
    master_group_key: str = "",
) -> str:
    """Stable search card cluster: same master / same project round → one card."""
    key = (master_group_key or "").strip()
    if len(key) >= 12:
        return key[:18]
    key = project_group_key(display_name)
    if len(key) >= 12:
        return key[:18]
    return f"m{master_id}"


def names_compatible(left: str, right: str) -> bool:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return True
    if left_norm == right_norm:
        return True
    if left_norm in right_norm or right_norm in left_norm:
        return True
    prefix_len = min(len(left_norm), len(right_norm), 24)
    if prefix_len >= 12 and left_norm[:prefix_len] == right_norm[:prefix_len]:
        return True
    return False


def pick_event_date(record: dict[str, Any]) -> str:
    if record.get("event_date"):
        return record["event_date"]
    if record.get("approval_date"):
        return record["approval_date"]
    pub = record.get("pub_period") or ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", pub)
    return match.group(0) if match else ""


def _parse_summary(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def episode_key_from_event(event: dict[str, Any]) -> str:
    """Return the most specific identifier for an event round (often approval number)."""
    summary = _parse_summary(event.get("summary_json") or event.get("summary"))
    approval = (summary.get("approval_number") or "").strip()
    external_id = (event.get("external_id") or "").strip()
    parts = external_id.split("|")
    if not approval and len(parts) >= 3 and parts[2].strip():
        approval = parts[2].strip()
    if not approval:
        approval = (event.get("approval_number") or "").strip()
    if approval:
        return approval

    for key in ("pub_period", "pre_pub_period", *SUMMARY_DATE_KEYS):
        value = summary.get(key) or event.get(key) or ""
        match = re.search(r"(\d{4})", str(value))
        if match:
            return f"round_{match.group(1)}"
    if len(parts) >= 4:
        match = re.search(r"(\d{4})", parts[3])
        if match:
            return f"round_{match.group(1)}"
    event_date = event.get("event_date") or ""
    match = re.search(r"(\d{4})", event_date)
    if match:
        return f"round_{match.group(1)}"
    return "default"


def episode_group_key_from_event(event: dict[str, Any]) -> str:
    """Normalize events from the same approval round to one search/progress group."""
    episode = episode_key_from_event(event)
    year_match = re.search(r"\[(\d{4})\]", episode)
    if year_match:
        return f"round_{year_match.group(1)}"
    if episode.startswith("round_"):
        return episode
    summary = _parse_summary(event.get("summary_json") or event.get("summary"))
    external_id = (event.get("external_id") or "").strip()
    parts = external_id.split("|")
    for key in ("pub_period", "pre_pub_period", *SUMMARY_DATE_KEYS):
        value = summary.get(key) or event.get(key) or ""
        match = re.search(r"(\d{4})", str(value))
        if match:
            return f"round_{match.group(1)}"
    if len(parts) >= 4:
        match = re.search(r"(\d{4})", parts[3])
        if match:
            return f"round_{match.group(1)}"
    event_date = event.get("event_date") or ""
    match = re.search(r"(\d{4})", event_date)
    if match:
        return f"round_{match.group(1)}"
    return episode


def event_matches_episode(event: dict[str, Any], episode_key: str | None) -> bool:
    if not episode_key:
        return True
    if episode_key_from_event(event) == episode_key:
        return True
    return episode_group_key_from_event(event) == episode_key


def _approval_conflicts(existing_approval: str, incoming_approval: str) -> bool:
    existing = (existing_approval or "").strip()
    incoming = (incoming_approval or "").strip()
    if not existing or not incoming:
        return False
    return existing != incoming


class MasterResolver:
    def __init__(self, conn: sqlite3.Connection, now: str) -> None:
        self.conn = conn
        self.now = now
        self._episode_keys_cache: dict[int, set[str]] | None = None
        self._master_cache: dict[int, sqlite3.Row] | None = None
        self._link_masters_by_group: dict[str, list[tuple[int, str]]] | None = None
        self._masters_by_company: dict[str, list[tuple[int, str]]] | None = None

    def preload_caches(self) -> None:
        if self._episode_keys_cache is not None:
            return
        self._episode_keys_cache = defaultdict(set)
        for row in self.conn.execute(
            "SELECT master_id, summary_json, external_id, event_date FROM disclosure_events"
        ).fetchall():
            master_id = int(row["master_id"])
            self._episode_keys_cache[master_id].add(episode_group_key_from_event(dict(row)))

        self._master_cache = {}
        self._link_masters_by_group = defaultdict(list)
        self._masters_by_company = defaultdict(list)
        link_master_ids = {
            int(row[0])
            for row in self.conn.execute(
                "SELECT DISTINCT master_id FROM disclosure_events WHERE source = 'link_sthj'"
            ).fetchall()
        }
        for row in self.conn.execute("SELECT * FROM projects_master").fetchall():
            master_id = int(row["id"])
            self._master_cache[master_id] = row
            name = (row["canonical_name"] or "").strip()
            company = (row["company"] or "").strip()
            if company:
                self._masters_by_company[company].append((master_id, name))
            if master_id in link_master_ids:
                group_key = (row["group_key"] or "").strip() if "group_key" in row.keys() else ""
                if not group_key:
                    group_key = project_group_key(name)
                if group_key:
                    self._link_masters_by_group[group_key].append((master_id, name))

    def refresh_master_episode_keys(self, master_id: int) -> None:
        if self._episode_keys_cache is None:
            return
        rows = self.conn.execute(
            "SELECT summary_json, external_id, event_date FROM disclosure_events WHERE master_id = ?",
            (master_id,),
        ).fetchall()
        self._episode_keys_cache[master_id] = {
            episode_group_key_from_event(dict(row)) for row in rows
        }

    def _get_master_row(self, master_id: int) -> sqlite3.Row | None:
        if self._master_cache is not None:
            return self._master_cache.get(master_id)
        return self.conn.execute(
            "SELECT * FROM projects_master WHERE id = ?",
            (master_id,),
        ).fetchone()

    def _register_master_in_cache(self, master_id: int, *, is_link: bool = False) -> None:
        if self._master_cache is None:
            return
        row = self.conn.execute(
            "SELECT * FROM projects_master WHERE id = ?",
            (master_id,),
        ).fetchone()
        if not row:
            return
        self._master_cache[master_id] = row
        name = (row["canonical_name"] or "").strip()
        company = (row["company"] or "").strip()
        if company:
            bucket = self._masters_by_company.setdefault(company, [])
            if not any(mid == master_id for mid, _ in bucket):
                bucket.append((master_id, name))
        if is_link or self._link_masters_by_group is not None:
            group_key = (row["group_key"] or "").strip() if "group_key" in row.keys() else ""
            if not group_key:
                group_key = project_group_key(name)
            if group_key and self._link_masters_by_group is not None:
                bucket = self._link_masters_by_group.setdefault(group_key, [])
                if not any(mid == master_id for mid, _ in bucket):
                    bucket.append((master_id, name))
        if self._episode_keys_cache is not None and master_id not in self._episode_keys_cache:
            self._episode_keys_cache[master_id] = set()

    def resolve_master_id(self, record: dict[str, Any]) -> int:
        source = (record.get("source") or "").strip()
        external_id = (record.get("external_id") or "").strip()
        approval_number = (record.get("approval_number") or "").strip()
        st_eia_id = (record.get("st_eia_id") or "").strip()
        project_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        company = (record.get("company") or "").strip()

        if external_id and source:
            row = self.conn.execute(
                "SELECT master_id FROM master_aliases WHERE alias_type = ? AND alias_value = ?",
                (f"external:{source}", external_id),
            ).fetchone()
            if row:
                master_id = int(row[0])
                if self._record_compatible_with_master(master_id, record):
                    return self._touch_master(master_id, record)

        if approval_number:
            row = self.conn.execute(
                "SELECT id FROM projects_master WHERE approval_number = ?",
                (approval_number,),
            ).fetchone()
            if row:
                master_id = int(row[0])
                if self._record_compatible_with_master(master_id, record):
                    return self._touch_master(master_id, record)
            row = self.conn.execute(
                """
                SELECT DISTINCT e.master_id
                FROM disclosure_events e
                WHERE json_extract(e.summary_json, '$.approval_number') = ?
                LIMIT 1
                """,
                (approval_number,),
            ).fetchone()
            if row:
                master_id = int(row[0])
                if self._record_compatible_with_master(master_id, record):
                    return self._touch_master(master_id, record)

        if st_eia_id:
            row = self.conn.execute(
                "SELECT master_id FROM master_aliases WHERE alias_type = 'st_eia_id' AND alias_value = ?",
                (st_eia_id,),
            ).fetchone()
            if row:
                master_id = int(row[0])
                if self._record_compatible_with_master(master_id, record):
                    return self._touch_master(master_id, record)

        episode_master_id = self._find_master_by_episode(record)
        if episode_master_id is not None:
            return self._touch_master(episode_master_id, record)

        group_key = project_group_key(project_name)
        if group_key:
            group_master_id = self._find_master_by_group_and_episode(group_key, record)
            if group_master_id is not None:
                return self._touch_master(group_master_id, record)

        if project_name:
            alias = self.conn.execute(
                """
                SELECT master_id FROM master_aliases
                WHERE alias_type = 'project_name' AND alias_value = ?
                """,
                (normalize_text(project_name),),
            ).fetchone()
            if alias:
                master_id = int(alias[0])
                if self._record_compatible_with_master(master_id, record):
                    return self._touch_master(master_id, record)

        if project_name and company:
            row = self.conn.execute(
                """
                SELECT id FROM projects_master
                WHERE canonical_name = ? AND company = ?
                """,
                (project_name, company),
            ).fetchone()
            if row:
                master_id = int(row[0])
                if self._record_compatible_with_master(master_id, record):
                    return self._touch_master(master_id, record)

        return self._create_master(record)

    def _find_master_by_group_and_episode(self, group_key: str, record: dict[str, Any]) -> int | None:
        incoming_key = episode_group_key_from_event(record)
        if incoming_key == "default":
            return None
        rows = self.conn.execute(
            "SELECT id, canonical_name FROM projects_master WHERE group_key = ?",
            (group_key,),
        ).fetchall()
        return self._pick_episode_master(rows, record, incoming_key)

    def _pick_episode_master(
        self,
        rows: list[sqlite3.Row] | list[dict[str, Any]],
        record: dict[str, Any],
        incoming_key: str,
    ) -> int | None:
        project_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        candidates: list[int] = []
        for row in rows:
            master_id = int(row["id"] if isinstance(row, dict) else row[0])
            canonical_name = row["canonical_name"] if isinstance(row, dict) else row["canonical_name"]
            if not names_compatible(project_name, canonical_name or ""):
                continue
            episode_keys = self._master_episode_keys(master_id)
            if incoming_key in episode_keys:
                candidates.append(master_id)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        for master_id in candidates:
            has_approval = self.conn.execute(
                """
                SELECT 1 FROM disclosure_events
                WHERE master_id = ? AND disclosure_type = 'approval_decision'
                LIMIT 1
                """,
                (master_id,),
            ).fetchone()
            if has_approval:
                return master_id
        return min(candidates)

    def _find_master_by_episode(self, record: dict[str, Any]) -> int | None:
        project_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        company = (record.get("company") or "").strip()
        incoming_key = episode_group_key_from_event(record)
        if incoming_key == "default":
            return None

        if company:
            if self._masters_by_company is not None:
                rows = [
                    {"id": master_id, "canonical_name": name}
                    for master_id, name in self._masters_by_company.get(company, [])
                ]
            else:
                rows = self.conn.execute(
                    """
                    SELECT DISTINCT m.id, m.canonical_name
                    FROM projects_master m
                    JOIN disclosure_events e ON e.master_id = m.id
                    WHERE m.company = ?
                    """,
                    (company,),
                ).fetchall()
        else:
            group_key = project_group_key(project_name)
            if self._link_masters_by_group is not None and group_key:
                rows = [
                    {"id": master_id, "canonical_name": name}
                    for master_id, name in self._link_masters_by_group.get(group_key, [])
                ]
            elif group_key:
                rows = self.conn.execute(
                    "SELECT id, canonical_name FROM projects_master WHERE group_key = ?",
                    (group_key,),
                ).fetchall()
            elif self._link_masters_by_group is not None:
                rows = []
            else:
                rows = self.conn.execute(
                    """
                    SELECT DISTINCT m.id, m.canonical_name
                    FROM projects_master m
                    JOIN disclosure_events e ON e.master_id = m.id
                    WHERE e.source = 'link_sthj'
                    """
                ).fetchall()
        return self._pick_episode_master(rows, record, incoming_key)

    def _master_episode_keys(self, master_id: int) -> set[str]:
        if self._episode_keys_cache is not None:
            return self._episode_keys_cache.get(master_id, set())
        keys: set[str] = set()
        rows = self.conn.execute(
            "SELECT summary_json, external_id, event_date FROM disclosure_events WHERE master_id = ?",
            (master_id,),
        ).fetchall()
        for row in rows:
            keys.add(episode_group_key_from_event(dict(row)))
        return keys

    def _record_compatible_with_master(self, master_id: int, record: dict[str, Any]) -> bool:
        incoming_approval = (record.get("approval_number") or "").strip()
        if self._approval_conflicts_with_master(master_id, incoming_approval):
            return False

        master = self._get_master_row(master_id)
        if not master:
            return False

        incoming_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        master_name = (master["canonical_name"] or "").strip()
        source = (record.get("source") or "").strip()
        incoming_approval = (record.get("approval_number") or "").strip()
        master_approval = (master["approval_number"] or "").strip()
        if (
            source == "e2_qygk"
            and incoming_approval
            and master_approval
            and incoming_approval == master_approval
        ):
            if incoming_name and master_name and not names_compatible(incoming_name, master_name):
                return False
            return not self._episode_conflicts_with_master(master_id, record)
        if incoming_name and master_name and not names_compatible(incoming_name, master_name):
            return False

        if self._episode_conflicts_with_master(master_id, record):
            return False
        return True

    def _episode_conflicts_with_master(self, master_id: int, record: dict[str, Any]) -> bool:
        incoming_key = episode_group_key_from_event(record)
        if incoming_key == "default":
            return False

        master_keys = self._master_episode_keys(master_id)
        if not master_keys or incoming_key in master_keys:
            return False
        return True

    def _approval_taken_by_other(self, approval_number: str, master_id: int | None = None) -> bool:
        incoming = (approval_number or "").strip()
        if not incoming:
            return False
        row = self.conn.execute(
            "SELECT id FROM projects_master WHERE approval_number = ? AND id != ?",
            (incoming, master_id or -1),
        ).fetchone()
        return row is not None

    def _create_master(self, record: dict[str, Any]) -> int:
        project_name = (record.get("project_name") or record.get("canonical_name") or "未命名项目").strip()
        company = (record.get("company") or "").strip()
        approval_number = (record.get("approval_number") or "").strip()
        if self._approval_taken_by_other(approval_number):
            approval_number = ""
        group_key = project_group_key(project_name)
        cursor = self.conn.execute(
            """
            INSERT INTO projects_master (
                canonical_name, company, approval_number, location, district, st_eia_id,
                group_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_name,
                company,
                approval_number,
                (record.get("location") or "").strip(),
                (record.get("district") or "").strip(),
                (record.get("st_eia_id") or "").strip(),
                group_key,
                self.now,
                self.now,
            ),
        )
        master_id = int(cursor.lastrowid)
        self._write_aliases(master_id, record)
        self._register_master_in_cache(
            master_id,
            is_link=(record.get("source") or "").strip() == "link_sthj",
        )
        return master_id

    def _touch_master(self, master_id: int, record: dict[str, Any]) -> int:
        existing = self._get_master_row(master_id)
        if not existing:
            return self._create_master(record)

        approval_number = (record.get("approval_number") or "").strip()
        location = (record.get("location") or "").strip()
        district = (record.get("district") or "").strip()
        st_eia_id = (record.get("st_eia_id") or "").strip()
        company = (record.get("company") or "").strip()
        name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        existing_approval = (existing["approval_number"] or "").strip()
        if _approval_conflicts(existing_approval, approval_number):
            approval_number = ""
        if approval_number and self._approval_taken_by_other(approval_number, master_id):
            approval_number = ""

        existing_name = (existing["canonical_name"] or "").strip()
        if name and existing_name and not names_compatible(name, existing_name):
            name = ""
        if name and not existing_name:
            pass
        elif name and existing_name and len(name) > len(existing_name):
            pass
        elif name and existing_name:
            name = existing_name

        display_name = name or existing_name
        group_key = project_group_key(display_name)

        self.conn.execute(
            """
            UPDATE projects_master SET
                canonical_name = CASE WHEN ? != '' THEN ? ELSE canonical_name END,
                company = CASE WHEN ? != '' THEN ? ELSE company END,
                approval_number = CASE WHEN ? != '' THEN ? ELSE approval_number END,
                location = CASE WHEN ? != '' THEN ? ELSE location END,
                district = CASE WHEN ? != '' THEN ? ELSE district END,
                st_eia_id = CASE WHEN ? != '' THEN ? ELSE st_eia_id END,
                group_key = CASE WHEN ? != '' THEN ? ELSE group_key END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                name,
                company,
                company,
                approval_number,
                approval_number,
                location,
                location,
                district,
                district,
                st_eia_id,
                st_eia_id,
                group_key,
                group_key,
                self.now,
                master_id,
            ),
        )
        self._write_aliases(master_id, record)
        self._register_master_in_cache(
            master_id,
            is_link=(record.get("source") or "").strip() == "link_sthj",
        )
        return master_id

    def _write_aliases(self, master_id: int, record: dict[str, Any]) -> None:
        project_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        approval_number = (record.get("approval_number") or "").strip()
        if project_name:
            self._upsert_alias(master_id, "project_name", normalize_text(project_name))
        if approval_number:
            self._upsert_alias(master_id, "approval_number", approval_number)
        st_eia_id = (record.get("st_eia_id") or "").strip()
        if st_eia_id:
            self._upsert_alias(master_id, "st_eia_id", st_eia_id)
        external_id = (record.get("external_id") or "").strip()
        source = (record.get("source") or "").strip()
        if external_id and source:
            self._upsert_alias(master_id, f"external:{source}", external_id)

    def _approval_conflicts_with_master(self, master_id: int, approval_number: str) -> bool:
        incoming = (approval_number or "").strip()
        if not incoming:
            return False
        row = self.conn.execute(
            "SELECT approval_number FROM projects_master WHERE id = ?",
            (master_id,),
        ).fetchone()
        if not row:
            return False
        return _approval_conflicts(row[0] or "", incoming)

    def _upsert_alias(self, master_id: int, alias_type: str, alias_value: str) -> None:
        if not alias_value:
            return
        existing = self.conn.execute(
            "SELECT master_id FROM master_aliases WHERE alias_type = ? AND alias_value = ?",
            (alias_type, alias_value),
        ).fetchone()
        if existing and int(existing[0]) != master_id:
            return
        self.conn.execute(
            """
            INSERT INTO master_aliases(master_id, alias_type, alias_value)
            VALUES (?, ?, ?)
            ON CONFLICT(alias_type, alias_value) DO UPDATE SET master_id = excluded.master_id
            """,
            (master_id, alias_type, alias_value),
        )


def get_schema_version(conn: sqlite3.Connection) -> int:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if "projects" in tables:
        return 1
    if "projects_v1_backup" in tables:
        return SCHEMA_VERSION
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        if row:
            return int(row[0])
    except sqlite3.OperationalError:
        pass
    if "projects_master" in tables:
        return SCHEMA_VERSION
    return 0
