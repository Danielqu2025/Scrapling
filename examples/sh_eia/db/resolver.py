"""Resolve disclosure events to projects_master records."""

from __future__ import annotations

import json
import re
import sqlite3
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
    return text


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

    def resolve_master_id(self, record: dict[str, Any]) -> int:
        approval_number = (record.get("approval_number") or "").strip()
        st_eia_id = (record.get("st_eia_id") or "").strip()
        project_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        company = (record.get("company") or "").strip()

        if approval_number:
            row = self.conn.execute(
                "SELECT id FROM projects_master WHERE approval_number = ?",
                (approval_number,),
            ).fetchone()
            if row:
                return self._touch_master(int(row[0]), record)

        if st_eia_id:
            row = self.conn.execute(
                "SELECT master_id FROM master_aliases WHERE alias_type = 'st_eia_id' AND alias_value = ?",
                (st_eia_id,),
            ).fetchone()
            if row:
                return self._touch_master(int(row[0]), record)

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
                if not self._approval_conflicts_with_master(master_id, approval_number):
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
                if not self._approval_conflicts_with_master(master_id, approval_number):
                    return self._touch_master(master_id, record)

        return self._create_master(record)

    def _create_master(self, record: dict[str, Any]) -> int:
        project_name = (record.get("project_name") or record.get("canonical_name") or "未命名项目").strip()
        company = (record.get("company") or "").strip()
        cursor = self.conn.execute(
            """
            INSERT INTO projects_master (
                canonical_name, company, approval_number, location, district, st_eia_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_name,
                company,
                (record.get("approval_number") or "").strip(),
                (record.get("location") or "").strip(),
                (record.get("district") or "").strip(),
                (record.get("st_eia_id") or "").strip(),
                self.now,
                self.now,
            ),
        )
        master_id = int(cursor.lastrowid)
        self._write_aliases(master_id, record)
        return master_id

    def _touch_master(self, master_id: int, record: dict[str, Any]) -> int:
        existing = self.conn.execute(
            "SELECT * FROM projects_master WHERE id = ?",
            (master_id,),
        ).fetchone()
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

        self.conn.execute(
            """
            UPDATE projects_master SET
                canonical_name = CASE WHEN ? != '' THEN ? ELSE canonical_name END,
                company = CASE WHEN ? != '' THEN ? ELSE company END,
                approval_number = CASE WHEN ? != '' THEN ? ELSE approval_number END,
                location = CASE WHEN ? != '' THEN ? ELSE location END,
                district = CASE WHEN ? != '' THEN ? ELSE district END,
                st_eia_id = CASE WHEN ? != '' THEN ? ELSE st_eia_id END,
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
                self.now,
                master_id,
            ),
        )
        self._write_aliases(master_id, record)
        return master_id

    def _write_aliases(self, master_id: int, record: dict[str, Any]) -> None:
        project_name = (record.get("project_name") or record.get("canonical_name") or "").strip()
        approval_number = (record.get("approval_number") or "").strip()
        if project_name:
            alias_value = normalize_text(project_name)
            existing = self.conn.execute(
                """
                SELECT master_id FROM master_aliases
                WHERE alias_type = 'project_name' AND alias_value = ?
                """,
                (alias_value,),
            ).fetchone()
            if not existing or int(existing[0]) == master_id:
                self._upsert_alias(master_id, "project_name", alias_value)
            elif not self._approval_conflicts_with_master(int(existing[0]), approval_number):
                self._upsert_alias(master_id, "project_name", alias_value)
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
