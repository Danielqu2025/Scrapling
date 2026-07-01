"""Convert link.sthj.sh.gov.cn list records to disclosure events."""

from __future__ import annotations

import json
from typing import Any

from sources.types import SOURCE_LINK_STHJ


def link_record_to_event(record: dict[str, Any]) -> dict[str, Any]:
    external_id = "|".join(
        [
            record["disclosure_type"],
            record["project_name"],
            record.get("approval_number", ""),
            record.get("pub_period", ""),
        ]
    )
    return {
        "source": SOURCE_LINK_STHJ,
        "disclosure_type": record["disclosure_type"],
        "external_id": external_id,
        "project_name": record["project_name"],
        "company": record.get("company", ""),
        "location": record.get("location", ""),
        "approval_number": record.get("approval_number", ""),
        "approval_date": record.get("approval_date", ""),
        "pub_period": record.get("pub_period", ""),
        "event_date": record.get("approval_date", ""),
        "lifecycle_stage": "",
        "title": record["project_name"],
        "summary_json": {
            "agency": record.get("agency", ""),
            "approval_title": record.get("approval_title", ""),
            "summary": record.get("summary", ""),
            "pub_period": record.get("pub_period", ""),
        },
        "source_url": record.get("source_url", ""),
        "files": record.get("files", []),
    }


def link_records_to_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [link_record_to_event(record) for record in records]
