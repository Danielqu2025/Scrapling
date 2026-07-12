"""Convert Minhang list rows to disclosure events."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from sources.district import DISTRICT_LABELS, DISTRICT_MINHANG
from sources.types import SOURCE_DISTRICT_MINHANG


def _event_date(date_text: str) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", date_text or "")
    return match.group(0) if match else ""


def _file_name(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1]
    return name or "attachment.pdf"


def _external_id(stage: str, row: dict[str, Any]) -> str:
    for link in row.get("links") or []:
        match = re.search(r"/([A-Fa-f0-9]{32})_", link)
        if match:
            return match.group(1).upper()
    project = (row.get("project_name") or "").strip()
    date_text = (row.get("date_text") or "").strip()
    page = row.get("page_index", 0)
    row_index = row.get("row_index", 0)
    return f"{stage}|{project}|{date_text}|{page}|{row_index}"


def minhang_row_to_event(row: dict[str, Any]) -> dict[str, Any]:
    stage = row["stage"]
    project_name = (row.get("project_name") or "").strip()
    date_text = (row.get("date_text") or "").strip()
    links = [u for u in (row.get("links") or []) if u]
    files = [
        {
            "file_type": "attachment",
            "file_name": _file_name(url),
            "file_url": url,
            "file_external_id": "",
            "download_status": "direct",
        }
        for url in links
        if re.search(r"\.(pdf|doc|docx|zip|rar)(?:\?|$)", url, re.I) or "/UploadPath/" in url
    ]
    return {
        "source": SOURCE_DISTRICT_MINHANG,
        "disclosure_type": stage,
        "external_id": _external_id(stage, row),
        "project_name": project_name,
        "company": "",
        "location": "",
        "district": DISTRICT_LABELS.get(DISTRICT_MINHANG, "闵行区"),
        "approval_number": "",
        "approval_date": _event_date(date_text),
        "pub_period": date_text,
        "event_date": _event_date(date_text),
        "lifecycle_stage": "",
        "title": project_name,
        "summary_json": {
            "eia_form": row.get("eia_form") or "",
            "pub_period": date_text,
            "channel": row.get("channel") or "",
            "page_index": row.get("page_index"),
            "district_code": DISTRICT_MINHANG,
        },
        "source_url": row.get("source_url") or "",
        "page_no": int(row.get("page_index") or 0) + 1,
        "files": files,
    }


def minhang_rows_to_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [minhang_row_to_event(row) for row in rows]
