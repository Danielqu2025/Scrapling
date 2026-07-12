"""Convert Songjiang list API rows to disclosure events."""

from __future__ import annotations

import re
from typing import Any

from sources.district import DISTRICT_LABELS, DISTRICT_SONGJIANG, STAGE_DECISION
from sources.district.songjiang import PAGE_URL
from sources.types import SOURCE_DISTRICT_SONGJIANG


def _date_text(row: dict[str, Any]) -> str:
    begin = str(row.get("GONGSHIBEGINDATE") or row.get("PIWENDATE") or row.get("OPERDATE") or "").strip()
    end = str(row.get("GONGSHIENDDATE") or "").strip()
    if begin and end:
        return f"{begin}~{end}"
    return begin or end


def _event_date(row: dict[str, Any]) -> str:
    for key in ("GONGSHIBEGINDATE", "PIWENDATE", "OPERDATE", "COMPOSEDATE", "UPDATETIME"):
        value = str(row.get(key) or "")
        match = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)
    return ""


def songjiang_row_to_event(row: dict[str, Any], *, page_no: int = 1) -> dict[str, Any]:
    project_name = str(row.get("SUBJECT") or row.get("PRJCN") or row.get("PIWENNAME") or "").strip()
    company = str(row.get("BUILDERUNIT") or "").strip()
    address = str(row.get("ADDRESS") or "").strip()
    approval_number = str(row.get("PIWNENO") or row.get("PIWENNO") or "").strip()
    item_no = str(row.get("ITEMNO") or "").strip()
    row_id = str(row.get("ROW_ID") or "").strip()
    external_id = item_no or row_id or f"{project_name}|{approval_number}|{_event_date(row)}"
    attachments = []
    for key in (f"ATTACH_{i}" for i in range(1, 9)):
        value = str(row.get(key) or "").strip()
        if value:
            attachments.append(value)
    pub_period = _date_text(row)
    return {
        "source": SOURCE_DISTRICT_SONGJIANG,
        "disclosure_type": STAGE_DECISION,
        "external_id": external_id,
        "project_name": project_name,
        "company": company,
        "location": address,
        "district": DISTRICT_LABELS.get(DISTRICT_SONGJIANG, "松江区"),
        "approval_number": approval_number,
        "approval_date": _event_date(row),
        "pub_period": pub_period,
        "event_date": _event_date(row),
        "lifecycle_stage": "",
        "title": project_name,
        "summary_json": {
            "agency": str(row.get("APPROVEUNIT") or row.get("HPUNIT") or "").strip(),
            "approval_title": str(row.get("PIWENNAME") or "").strip(),
            "approval_number": approval_number,
            "pub_period": pub_period,
            "item_no": item_no,
            "gongshi_type": str(row.get("GONGSHITYPE") or "").strip(),
            "attachments": attachments,
            "district_code": DISTRICT_SONGJIANG,
        },
        "source_url": PAGE_URL,
        "page_no": page_no,
        "files": [],
    }


def songjiang_rows_to_events(rows: list[dict[str, Any]], *, page_no: int = 1) -> list[dict[str, Any]]:
    return [songjiang_row_to_event(row, page_no=page_no) for row in rows]
