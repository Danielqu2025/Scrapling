"""Build unified project progress view (link pre-approval + e2 post-approval stages)."""

from __future__ import annotations

import json
import re
from typing import Any

from sources.types import E2_PHASE_FILE_TYPES, FILE_TYPE_LABELS

# Fixed stage rail: pre-approval (link) then post-approval (e2-style tabs)
PROGRESS_STAGES = [
    {"key": "acceptance", "label": "受理信息", "match_types": ["acceptance"]},
    {"key": "proposed_approval", "label": "拟审批公示", "match_types": ["proposed_approval"]},
    {"key": "approval_decision", "label": "审批决定公告", "match_types": ["approval_decision"]},
    {"key": "pre_publicity", "label": "报批前公示", "match_types": ["post_construction"], "e2_phase": "pre"},
    {"key": "basic_info", "label": "项目基本信息", "match_types": ["post_construction"], "e2_phase": "basic"},
    {"key": "construction", "label": "建设期", "match_types": ["post_construction"], "e2_phase": "construction"},
    {"key": "debug", "label": "竣工及调试期", "match_types": ["post_construction"], "e2_phase": "debug"},
    {"key": "acceptance_phase", "label": "竣工环保验收", "match_types": ["post_construction"], "e2_phase": "acceptance"},
]

LEGACY_ATTACHMENT_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "pre": [(r"拟报批|公众参与|报批前", "pre_approval_notice")],
    "construction": [(r"施工期", "construction_measures")],
    "debug": [(r"调试|非重大调整", "debug_measures")],
    "acceptance": [(r"验收监测|验收意见|其他需要说明", "acceptance_report")],
}


def _parse_summary(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _fields_from_event(event: dict[str, Any], master: dict[str, Any]) -> list[dict[str, str]]:
    summary = _parse_summary(event.get("summary_json") or event.get("summary"))
    rows: list[dict[str, str]] = []

    def add(label: str, value: str) -> None:
        if value:
            rows.append({"label": label, "value": value})

    add("项目名称", master.get("canonical_name") or event.get("title", ""))
    add("建设单位", master.get("company") or summary.get("company", ""))
    add("建设地点", master.get("location") or summary.get("location", ""))
    add("所属区域", master.get("district") or summary.get("district", ""))
    add("环评批文文号", master.get("approval_number") or summary.get("approval_number", ""))
    add("环评批文日期", summary.get("approval_date") or event.get("event_date", ""))
    add("审批机关", summary.get("agency", ""))
    add("批文标题", summary.get("approval_title", ""))
    add("公示期", summary.get("pub_period", "") or summary.get("pre_pub_period", ""))
    add("项目概况", summary.get("summary", ""))
    add("计划开工日期", summary.get("planned_start_date", ""))
    add("实际开工日期", summary.get("actual_start_date", ""))
    add("竣工日期", summary.get("completion_date", ""))
    add("开始调试日期", summary.get("debug_start_date", ""))
    add("验收公示起始", summary.get("acceptance_pub_start", ""))
    add("当前状态", event.get("lifecycle_stage", ""))
    return rows


def _enrich_file(file_row: dict[str, Any]) -> dict[str, Any]:
    item = dict(file_row)
    item["file_type_label"] = FILE_TYPE_LABELS.get(item.get("file_type", ""), item.get("file_type", ""))
    return item


def _pick_event(events: list[dict[str, Any]], disclosure_type: str) -> dict[str, Any] | None:
    matched = [event for event in events if event.get("disclosure_type") == disclosure_type]
    if not matched:
        return None
    return matched[-1]


def _e2_post_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    matched = [event for event in events if event.get("disclosure_type") == "post_construction"]
    return matched[-1] if matched else None


def _stage_date(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    return event.get("event_date") or event.get("synced_at") or ""


def _legacy_phase_for_file(phase: str, file_item: dict[str, Any]) -> bool:
    if file_item.get("file_type") != "attachment":
        return False
    text = f"{file_item.get('file_name', '')} {file_item.get('file_type_label', '')}"
    for pattern, _ in LEGACY_ATTACHMENT_PATTERNS.get(phase, []):
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _files_for_e2_phase(post_files: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    allowed = E2_PHASE_FILE_TYPES.get(phase, set())
    matched = [file_item for file_item in post_files if file_item.get("file_type") in allowed]
    if matched:
        return matched
    return [file_item for file_item in post_files if _legacy_phase_for_file(phase, file_item)]


def build_progress_view(master: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge link.sthj pre-approval steps with e2-style post-approval tabs."""
    events = sorted(
        events,
        key=lambda item: item.get("event_date") or item.get("synced_at") or "",
    )
    post_event = _e2_post_event(events)
    post_summary = _parse_summary(post_event.get("summary_json") if post_event else {})
    post_files = [_enrich_file(file_item) for file_item in (post_event or {}).get("files", [])]

    stages: list[dict[str, Any]] = []
    for stage_def in PROGRESS_STAGES:
        stage: dict[str, Any] = {
            "key": stage_def["key"],
            "label": stage_def["label"],
            "status": "empty",
            "date": "",
            "source": "",
            "event_id": None,
            "source_url": "",
            "fields": [],
            "files": [],
        }

        if stage_def["key"] in {"acceptance", "proposed_approval", "approval_decision"}:
            event = _pick_event(events, stage_def["key"])
            if event:
                stage["status"] = "completed"
                stage["date"] = _stage_date(event)
                stage["source"] = event.get("source", "")
                stage["event_id"] = event.get("id")
                stage["source_url"] = event.get("source_url", "")
                stage["fields"] = _fields_from_event(event, master)
                stage["files"] = [_enrich_file(file_item) for file_item in event.get("files", [])]
        elif post_event:
            phase = stage_def.get("e2_phase")
            stage["source"] = post_event.get("source", "")
            stage["event_id"] = post_event.get("id")
            stage["source_url"] = post_event.get("source_url", "")

            if phase == "pre":
                stage["files"] = _files_for_e2_phase(post_files, "pre")
                stage["status"] = "completed" if stage["files"] or post_summary.get("pre_pub_period") or post_summary.get("st_eia_id") else "partial"
                stage["date"] = post_summary.get("pre_pub_period") or _stage_date(post_event)
                stage["fields"] = _fields_from_event(post_event, master)
            elif phase == "basic":
                stage["status"] = "completed" if post_summary.get("approval_number") else "partial"
                stage["date"] = post_summary.get("approval_date") or _stage_date(post_event)
                stage["fields"] = [
                    row
                    for row in _fields_from_event(post_event, master)
                    if row["label"]
                    in {"项目名称", "建设单位", "建设地点", "所属区域", "环评批文文号", "环评批文日期", "计划开工日期"}
                ]
            elif phase == "construction":
                stage["files"] = _files_for_e2_phase(post_files, "construction")
                stage["status"] = "completed" if post_summary.get("actual_start_date") or stage["files"] else "partial"
                stage["date"] = post_summary.get("actual_start_date") or post_summary.get("planned_start_date") or ""
                stage["fields"] = [
                    row
                    for row in _fields_from_event(post_event, master)
                    if row["label"] in {"实际开工日期", "计划开工日期", "当前状态"}
                ]
            elif phase == "debug":
                stage["files"] = _files_for_e2_phase(post_files, "debug")
                stage["status"] = "completed" if post_summary.get("completion_date") or stage["files"] else "partial"
                stage["date"] = post_summary.get("debug_start_date") or post_summary.get("completion_date") or ""
                stage["fields"] = [
                    row
                    for row in _fields_from_event(post_event, master)
                    if row["label"] in {"竣工日期", "开始调试日期", "当前状态"}
                ]
            elif phase == "acceptance":
                stage["files"] = _files_for_e2_phase(post_files, "acceptance")
                stage["status"] = "completed" if post_summary.get("acceptance_pub_start") or stage["files"] else "partial"
                stage["date"] = post_summary.get("acceptance_pub_start") or _stage_date(post_event)
                stage["fields"] = [
                    row for row in _fields_from_event(post_event, master) if row["label"] in {"验收公示起始", "当前状态"}
                ]

            if stage["status"] == "empty" and (stage["fields"] or stage["files"]):
                stage["status"] = "completed"

        if stage["status"] == "empty":
            stage["fields"] = [{"label": "说明", "value": "暂无该阶段同步数据"}]

        stages.append(stage)

    completed = sum(1 for stage in stages if stage["status"] in {"completed", "partial"})
    return {
        "master": master,
        "stages": stages,
        "summary": {
            "total_stages": len(stages),
            "completed_stages": completed,
            "has_pre_approval": any(stage["status"] != "empty" for stage in stages[:3]),
            "has_post_approval": post_event is not None,
        },
    }
