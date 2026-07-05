"""Build unified project progress view (link pre-approval + e2 post-approval stages)."""

from __future__ import annotations

import json
import re
from typing import Any

from _common import dedupe_files
from sources.types import E2_PHASE_FILE_TYPES, FILE_TYPE_LABELS

# 项目基本信息置顶作总览；其后为投用前三步与中后期四步
PROGRESS_STAGES = [
    {"key": "basic_info", "label": "项目基本信息", "match_types": ["post_construction"], "e2_phase": "basic"},
    {"key": "acceptance", "label": "受理信息", "match_types": ["acceptance"]},
    {"key": "proposed_approval", "label": "拟审批公示", "match_types": ["proposed_approval"]},
    {"key": "approval_decision", "label": "审批决定公告", "match_types": ["approval_decision"]},
    {"key": "construction", "label": "建设期", "match_types": ["post_construction"], "e2_phase": "construction"},
    {"key": "debug", "label": "竣工及调试期", "match_types": ["post_construction"], "e2_phase": "debug"},
    {"key": "acceptance_phase", "label": "竣工环保验收", "match_types": ["post_construction"], "e2_phase": "acceptance"},
]

LINK_STAGE_KEYS = frozenset({"acceptance", "proposed_approval", "approval_decision"})
BASIC_INFO_FIELD_LABELS = frozenset(
    {
        "项目名称",
        "建设单位",
        "建设地点",
        "所属区域",
        "所属行业",
        "环评批文文号",
        "环评批文日期",
        "计划开工日期",
        "设计单位",
        "项目基本信息",
        "联系人",
        "联系电话",
        "联系邮箱",
    }
)

E2_STAGE_FIELD_ORDER = {
    "pre": ["公示日期", "联系人", "联系电话", "联系邮箱"],
    "basic": [
        "项目名称",
        "建设单位",
        "所属行业",
        "建设地点",
        "所属区域",
        "项目基本信息",
        "设计单位",
        "计划开工日期",
        "环评批文文号",
        "环评批文日期",
        "联系人",
        "联系电话",
        "联系邮箱",
    ],
    "construction": ["实际开工日期", "联系人", "联系电话"],
    "debug": ["竣工日期", "开始调试日期", "联系人", "联系电话"],
    "acceptance": ["公示起始日期", "公示截止日期", "联系人", "联系电话"],
}

LINK_STAGE_E2_FALLBACK_LABELS: dict[str, frozenset[str]] = {
    "acceptance": frozenset({"项目名称", "建设单位", "建设地点", "所属区域", "公示期", "联系人", "联系电话", "联系邮箱"}),
    "proposed_approval": frozenset(
        {
            "项目名称",
            "建设单位",
            "建设地点",
            "所属区域",
            "环评批文文号",
            "公示期",
            "项目概况",
            "审批机关",
            "联系人",
            "联系电话",
            "联系邮箱",
        }
    ),
    "approval_decision": frozenset(
        {"项目名称", "建设单位", "建设地点", "所属区域", "环评批文文号", "环评批文日期", "审批机关", "批文标题"}
    ),
}

E2_PRE_FILES_BY_LINK_STAGE: dict[str, set[str]] = {
    "acceptance": {"pre_approval_entrust"},
    "proposed_approval": {"pre_approval_notice"},
}

LEGACY_ATTACHMENT_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "construction": [(r"施工期", "construction_measures")],
    "debug": [(r"调试|非重大变", "debug_measures")],
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


def _event_approval_number(event: dict[str, Any]) -> str:
    summary = _parse_summary(event.get("summary_json") or event.get("summary"))
    approval = (summary.get("approval_number") or "").strip()
    if approval:
        return approval
    parts = (event.get("external_id") or "").split("|")
    if len(parts) >= 3:
        return parts[2].strip()
    return ""


def _episode_target_approval(events: list[dict[str, Any]]) -> str:
    for disclosure_type in ("approval_decision", "post_construction", "proposed_approval", "acceptance"):
        for event in events:
            if event.get("disclosure_type") != disclosure_type:
                continue
            approval = _event_approval_number(event)
            if approval:
                return approval
    return ""


def _fields_from_e2_stage(summary: dict[str, Any], stage_key: str) -> list[dict[str, str]]:
    stage_fields = summary.get("stage_fields") or {}
    block = stage_fields.get(stage_key) or {}
    if not block:
        return []
    order = E2_STAGE_FIELD_ORDER.get(stage_key, [])
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for label in order:
        value = block.get(label)
        if value:
            rows.append({"label": label, "value": value})
            seen.add(label)
    for label, value in block.items():
        if label in seen or not value:
            continue
        rows.append({"label": label, "value": value})
    return rows


def _e2_basic_block(summary: dict[str, Any]) -> dict[str, str]:
    return (summary.get("stage_fields") or {}).get("basic") or {}


def _master_identity_fallback(master: dict[str, Any], field: str) -> str:
    value = (master.get(field) or "").strip()
    district = (master.get("district") or "").strip()
    if field in {"company", "location"} and value and district and value == district:
        return ""
    return value


def _enrich_display_master(
    master: dict[str, Any],
    post_event: dict[str, Any] | None,
    post_summary: dict[str, Any],
) -> dict[str, Any]:
    result = dict(master)
    basic = _e2_basic_block(post_summary)
    mapping = {
        "company": "建设单位",
        "location": "建设地点",
        "district": "所属区域",
    }
    for field, label in mapping.items():
        value = (basic.get(label) or post_summary.get(field) or "").strip()
        if not value:
            value = _master_identity_fallback(result, field)
        if value:
            result[field] = value
    if post_event and post_event.get("title"):
        result["canonical_name"] = post_event["title"]
    return result


def _e2_project_overview(post_summary: dict[str, Any]) -> str:
    direct = (post_summary.get("project_description") or "").strip()
    if direct:
        return direct
    stage_fields = post_summary.get("stage_fields") or {}
    for stage_key in ("pre", "basic"):
        block = stage_fields.get(stage_key) or {}
        for label in ("项目概况", "项目基本信息"):
            value = (block.get(label) or "").strip()
            if value:
                return value
    return ""


def _prefer_longer_field(rows: list[dict[str, str]], label: str, candidate: str) -> list[dict[str, str]]:
    candidate = candidate.strip()
    if not candidate:
        return rows
    updated: list[dict[str, str]] = []
    found = False
    for row in rows:
        if row["label"] != label:
            updated.append(row)
            continue
        found = True
        value = row["value"]
        updated.append({"label": label, "value": candidate if len(candidate) > len(value) else value})
    if not found:
        updated.append({"label": label, "value": candidate})
    return updated


def _fields_for_link_e2_fallback(
    post_event: dict[str, Any] | None,
    master: dict[str, Any],
    summary: dict[str, Any],
    link_stage_key: str,
) -> list[dict[str, str]]:
    if not post_event:
        return []
    allowed = LINK_STAGE_E2_FALLBACK_LABELS.get(link_stage_key, BASIC_INFO_FIELD_LABELS)
    return [row for row in _fields_from_event(post_event, master) if row["label"] in allowed]


def _fields_from_event(event: dict[str, Any], master: dict[str, Any]) -> list[dict[str, str]]:
    summary = _parse_summary(event.get("summary_json") or event.get("summary"))
    basic = _e2_basic_block(summary)
    rows: list[dict[str, str]] = []

    def add(label: str, value: str) -> None:
        if value:
            rows.append({"label": label, "value": value})

    display_name = (event.get("title") or "").strip()
    if not display_name:
        display_name = master.get("canonical_name") or ""
    add("项目名称", display_name)
    add(
        "建设单位",
        basic.get("建设单位") or summary.get("company") or _master_identity_fallback(master, "company"),
    )
    add(
        "建设地点",
        basic.get("建设地点") or summary.get("location") or _master_identity_fallback(master, "location"),
    )
    add(
        "所属区域",
        basic.get("所属区域") or summary.get("district") or _master_identity_fallback(master, "district"),
    )
    add("所属行业", summary.get("industry", ""))
    add("环评批文文号", _event_approval_number(event) or master.get("approval_number") or summary.get("approval_number", ""))
    add("环评批文日期", summary.get("approval_date") or event.get("event_date", ""))
    add("审批机关", summary.get("agency", ""))
    add("批文标题", summary.get("approval_title", ""))
    add("公示期", summary.get("pub_period", "") or summary.get("pre_pub_period", ""))
    add("项目概况", summary.get("summary", "") or summary.get("project_description", ""))
    add("项目基本信息", summary.get("project_description", ""))
    add("设计单位", summary.get("design_unit", ""))
    add("计划开工日期", summary.get("planned_start_date", ""))
    add("实际开工日期", summary.get("actual_start_date", ""))
    add("竣工日期", summary.get("completion_date", ""))
    add("开始调试日期", summary.get("debug_start_date", ""))
    add("验收公示起始", summary.get("acceptance_pub_start", ""))
    add("联系人", summary.get("contact_name", ""))
    add("联系电话", summary.get("contact_phone", ""))
    add("联系邮箱", summary.get("contact_email", ""))
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
    if len(matched) == 1:
        return matched[0]
    target_approval = _episode_target_approval(events)
    if target_approval:
        for event in matched:
            if _event_approval_number(event) == target_approval:
                return event
            parts = (event.get("external_id") or "").split("|")
            if len(parts) >= 3 and parts[2].strip() == target_approval:
                return event
    return matched[-1]


def _e2_post_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    matched = [event for event in events if event.get("disclosure_type") == "post_construction"]
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]
    target_approval = _episode_target_approval(events)
    if target_approval:
        for event in matched:
            if _event_approval_number(event) == target_approval:
                return event
    return matched[-1]


def _stage_date(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    return event.get("event_date") or event.get("synced_at") or ""


def _file_belongs_to_e2_phase(file_item: dict[str, Any], phase: str) -> bool:
    allowed = E2_PHASE_FILE_TYPES.get(phase, set())
    file_type = file_item.get("file_type", "")
    if file_type in allowed or file_type == f"e2_{phase}":
        return True
    text = f"{file_item.get('file_name', '')} {file_item.get('file_type_label', '')} {FILE_TYPE_LABELS.get(file_type, file_type)}"
    for pattern, _ in LEGACY_ATTACHMENT_PATTERNS.get(phase, []):
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _files_for_e2_phase(post_files: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file_item in post_files:
        if not _file_belongs_to_e2_phase(file_item, phase):
            continue
        key = (
            file_item.get("file_external_id")
            or file_item.get("file_url")
            or str(file_item.get("id"))
        )
        if not key or key in seen:
            continue
        seen.add(key)
        matched.append(file_item)
    return matched


def _e2_pre_files_for_link_stage(
    post_files: list[dict[str, Any]], link_stage_key: str
) -> list[dict[str, Any]]:
    allowed = E2_PRE_FILES_BY_LINK_STAGE.get(link_stage_key, set())
    return [_enrich_file(file_item) for file_item in post_files if file_item.get("file_type") in allowed]


def _merge_files(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for group in groups:
        combined.extend(group)
    return dedupe_files(combined)


def _merge_field_rows(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for row in group:
            label = row.get("label", "")
            if not label or label in seen:
                continue
            seen.add(label)
            merged.append(row)
    return merged


def _overview_fields(
    display_master: dict[str, Any], events: list[dict[str, Any]], post_event: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Build project overview from link + e2 basic info for the current episode."""
    rows: list[dict[str, str]] = []
    post_summary = _parse_summary(post_event.get("summary_json") if post_event else {})
    e2_basic = _fields_from_e2_stage(post_summary, "basic") if post_summary else []

    for disclosure_type in ("approval_decision", "proposed_approval", "acceptance", "post_construction"):
        event = _pick_event(events, disclosure_type)
        if event:
            rows = _merge_field_rows(_fields_from_event(event, display_master), e2_basic)
            rows = [row for row in rows if row["label"] in BASIC_INFO_FIELD_LABELS]
            if rows:
                return rows

    if e2_basic:
        return [row for row in e2_basic if row["label"] in BASIC_INFO_FIELD_LABELS]

    rows = []
    def add(label: str, value: str) -> None:
        if value:
            rows.append({"label": label, "value": value})

    add("项目名称", display_master.get("canonical_name", ""))
    add("建设单位", _master_identity_fallback(display_master, "company"))
    add("建设地点", _master_identity_fallback(display_master, "location"))
    add("所属区域", display_master.get("district", ""))
    add("环评批文文号", display_master.get("approval_number", ""))
    return rows


def build_progress_view(master: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge link.sthj pre-approval steps with e2-style post-approval tabs."""
    events = sorted(
        events,
        key=lambda item: item.get("event_date") or item.get("synced_at") or "",
    )
    post_event = _e2_post_event(events)
    post_summary = _parse_summary(post_event.get("summary_json") if post_event else {})
    post_files = [_enrich_file(file_item) for file_item in (post_event or {}).get("files", [])]
    display_master = _enrich_display_master(master, post_event, post_summary)
    if not post_event or not post_event.get("title"):
        for disclosure_type in ("approval_decision", "proposed_approval", "acceptance"):
            event = _pick_event(events, disclosure_type)
            if event and event.get("title"):
                display_master["canonical_name"] = event["title"]
                break

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

        if stage_def["key"] == "basic_info":
            stage["fields"] = _overview_fields(display_master, events, post_event)
            if post_event:
                stage["source"] = post_event.get("source", "")
                stage["event_id"] = post_event.get("id")
                stage["source_url"] = post_event.get("source_url", "")
                stage["date"] = post_summary.get("approval_date") or _stage_date(post_event)
                stage["status"] = "completed" if post_summary.get("approval_number") or stage["fields"] else "partial"
            elif stage["fields"]:
                stage["status"] = "completed"
                for link_type in ("approval_decision", "proposed_approval", "acceptance"):
                    link_event = _pick_event(events, link_type)
                    if link_event:
                        stage["source"] = link_event.get("source", "")
                        stage["event_id"] = link_event.get("id")
                        stage["source_url"] = link_event.get("source_url", "")
                        stage["date"] = _stage_date(link_event)
                        break
        elif stage_def["key"] in LINK_STAGE_KEYS:
            event = _pick_event(events, stage_def["key"])
            link_files = [_enrich_file(file_item) for file_item in (event or {}).get("files", [])]
            e2_pre_files = _e2_pre_files_for_link_stage(post_files, stage_def["key"]) if post_event else []
            stage["files"] = _merge_files(link_files, e2_pre_files)

            if event:
                stage["status"] = "completed"
                stage["date"] = _stage_date(event)
                stage["source"] = event.get("source", "")
                stage["event_id"] = event.get("id")
                stage["source_url"] = event.get("source_url", "")
                stage["fields"] = _fields_from_event(event, display_master)
                if stage_def["key"] == "proposed_approval" and post_summary:
                    stage["fields"] = _prefer_longer_field(
                        stage["fields"],
                        "项目概况",
                        _e2_project_overview(post_summary),
                    )
            elif e2_pre_files or (
                stage_def["key"] == "proposed_approval" and post_summary.get("pre_pub_period")
            ):
                stage["status"] = "partial"
                stage["source"] = post_event.get("source", "") if post_event else ""
                stage["event_id"] = post_event.get("id") if post_event else None
                stage["source_url"] = post_event.get("source_url", "") if post_event else ""
                stage["date"] = post_summary.get("pre_pub_period") or _stage_date(post_event)
                pre_fields = _fields_from_e2_stage(post_summary, "pre") if post_summary else []
                stage["fields"] = _merge_field_rows(
                    _fields_for_link_e2_fallback(post_event, display_master, post_summary, stage_def["key"]),
                    pre_fields,
                )
        elif post_event:
            phase = stage_def.get("e2_phase")
            stage["source"] = post_event.get("source", "")
            stage["event_id"] = post_event.get("id")
            stage["source_url"] = post_event.get("source_url", "")

            if phase == "construction":
                stage["files"] = _files_for_e2_phase(post_files, "construction")
                stage["status"] = "completed" if post_summary.get("actual_start_date") or stage["files"] else "partial"
                stage["date"] = post_summary.get("actual_start_date") or post_summary.get("planned_start_date") or ""
                stage["fields"] = _merge_field_rows(
                    _fields_from_e2_stage(post_summary, "construction"),
                    [
                        row
                        for row in _fields_from_event(post_event, display_master)
                        if row["label"] in {"实际开工日期", "计划开工日期", "当前状态"}
                    ],
                )
            elif phase == "debug":
                stage["files"] = _files_for_e2_phase(post_files, "debug")
                stage["status"] = "completed" if post_summary.get("completion_date") or stage["files"] else "partial"
                stage["date"] = post_summary.get("debug_start_date") or post_summary.get("completion_date") or ""
                stage["fields"] = _merge_field_rows(
                    _fields_from_e2_stage(post_summary, "debug"),
                    [
                        row
                        for row in _fields_from_event(post_event, display_master)
                        if row["label"] in {"竣工日期", "开始调试日期", "当前状态"}
                    ],
                )
            elif phase == "acceptance":
                stage["files"] = _files_for_e2_phase(post_files, "acceptance")
                stage["status"] = "completed" if post_summary.get("acceptance_pub_start") or stage["files"] else "partial"
                stage["date"] = post_summary.get("acceptance_pub_start") or _stage_date(post_event)
                stage["fields"] = _merge_field_rows(
                    _fields_from_e2_stage(post_summary, "acceptance"),
                    [
                        row
                        for row in _fields_from_event(post_event, display_master)
                        if row["label"] in {"验收公示起始", "当前状态"}
                    ],
                )
            elif phase == "basic":
                stage["fields"] = _fields_from_e2_stage(post_summary, "basic")

            if stage["status"] == "empty" and (stage["fields"] or stage["files"]):
                stage["status"] = "completed"

        if stage["status"] == "empty":
            stage["fields"] = [{"label": "说明", "value": "暂无该阶段同步数据"}]

        stages.append(stage)

    pre_approval_keys = LINK_STAGE_KEYS
    completed = sum(1 for stage in stages if stage["status"] in {"completed", "partial"})
    return {
        "master": display_master,
        "stages": stages,
        "summary": {
            "total_stages": len(stages),
            "completed_stages": completed,
            "has_pre_approval": any(
                stage["status"] != "empty" for stage in stages if stage["key"] in pre_approval_keys
            ),
            "has_post_approval": post_event is not None,
        },
    }
