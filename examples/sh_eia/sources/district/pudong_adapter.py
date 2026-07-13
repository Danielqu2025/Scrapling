"""Convert Pudong list API rows to disclosure events."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from sources.district import (
    DISTRICT_LABELS,
    DISTRICT_PUDONG,
    STAGE_ACCEPTANCE,
    STAGE_DECISION,
    STAGE_PROPOSED,
)
from sources.district.pudong import BASE, display_date_text
from sources.types import SOURCE_DISTRICT_PUDONG


def infer_disclosure_type(title: str) -> str:
    text = (title or "").strip()
    if any(token in text for token in ("审批决定", "批复", "审批意见")):
        return STAGE_DECISION
    if any(token in text for token in ("拟审批", "告知承诺")):
        return STAGE_PROPOSED
    if "受理" in text:
        return STAGE_ACCEPTANCE
    if text.endswith("的公示"):
        return STAGE_ACCEPTANCE
    return STAGE_PROPOSED


def _project_name(title: str) -> str:
    text = (title or "").strip()
    text = re.sub(r"环境影响报告(?:书|表)?的?(?:拟审批公示|拟审批的公示|告知承诺公示|公示|审批决定公告).*$", "", text)
    text = re.sub(r"^关于[“\"]?|[”\"]?的审批意见$", "", text).strip()
    return text or title.strip()


def _file_name(url: str, fallback: str = "") -> str:
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    return name or fallback or "attachment.pdf"


def pudong_row_to_event(row: dict[str, Any], *, page_no: int = 1) -> dict[str, Any]:
    title = str(row.get("title") or "").strip()
    disclosure_type = infer_disclosure_type(title)
    external_id = str(row.get("id") or "").strip() or f"{title}|{display_date_text(row.get('display_date'))}"
    source_url = str(row.get("url") or "").strip()
    if source_url.startswith("/"):
        source_url = urljoin(BASE, source_url)
    event_date = display_date_text(row.get("display_date"))
    pdf_url = str(row.get("pdf_file_url") or "").strip()
    if pdf_url.startswith("/"):
        pdf_url = urljoin(BASE, pdf_url)
    pdf_name = str(row.get("pdf_file_name") or "").strip()
    files = []
    if pdf_url:
        files.append(
            {
                "file_type": "attachment",
                "file_name": pdf_name or _file_name(pdf_url),
                "file_url": pdf_url,
                "file_external_id": "",
                "download_status": "direct",
            }
        )
    return {
        "source": SOURCE_DISTRICT_PUDONG,
        "disclosure_type": disclosure_type,
        "external_id": external_id,
        "project_name": _project_name(title),
        "company": "",
        "location": "",
        "district": DISTRICT_LABELS.get(DISTRICT_PUDONG, "浦东新区"),
        "approval_number": str(row.get("index_no") or "").strip(),
        "approval_date": event_date,
        "pub_period": event_date,
        "event_date": event_date,
        "lifecycle_stage": "",
        "title": title,
        "summary_json": {
            "index_no": str(row.get("index_no") or "").strip(),
            "dispatch_agency": str(row.get("dispatch_agency") or "").strip(),
            "channel_name": str(row.get("channel_name") or "").strip(),
            "channel_id": str(row.get("channel_id") or "").strip(),
            "inferred_type": disclosure_type,
            "district_code": DISTRICT_PUDONG,
        },
        "source_url": source_url,
        "page_no": page_no,
        "files": files,
    }


def pudong_rows_to_events(
    rows: list[dict[str, Any]],
    *,
    page_no: int = 1,
    disclosure_type: str | None = None,
) -> list[dict[str, Any]]:
    events = [pudong_row_to_event(row, page_no=page_no) for row in rows]
    if disclosure_type:
        events = [event for event in events if event["disclosure_type"] == disclosure_type]
    return events
