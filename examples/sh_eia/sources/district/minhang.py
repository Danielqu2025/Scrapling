"""Minhang static XXGK list client for district sync."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

from lxml import html as lhtml
from scrapling.fetchers import Fetcher

from sources.district import STAGE_ACCEPTANCE, STAGE_DECISION, STAGE_PROPOSED

logger = logging.getLogger(__name__)

BASE = "https://zwgk.shmh.gov.cn/mh-xxgk-cms/website/mh_xxgk"
CHANNELS = {
    STAGE_ACCEPTANCE: "zdly_scjg_hjbh_jsxmhpslxx",
    STAGE_PROPOSED: "zdly_scjg_hjbh_jsxmhpnspxx",
    STAGE_DECISION: "zdly_scjg_hjbh_jsxmhpjdspxx",
}


def _fetch_text(url: str) -> str:
    resp = Fetcher.get(url, stealthy_headers=True, timeout=25)
    raw = resp.body
    return raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw or "")


def channel_meta(channel: str) -> dict[str, int]:
    text = _fetch_text(f"{BASE}/{channel}/List/config.js")
    page_match = re.search(r"PageCount\s*=\s*(\d+)", text)
    record_match = re.search(r"RecordCount\s*=\s*(\d+)", text)
    return {
        "page_count": int(page_match.group(1)) if page_match else 1,
        "record_count": int(record_match.group(1)) if record_match else 0,
    }


def page_count(channel: str) -> int:
    return channel_meta(channel)["page_count"]


def remote_totals() -> dict[str, dict[str, int]]:
    return {stage: channel_meta(channel) for stage, channel in CHANNELS.items()}


def _clean_cell(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    value = re.sub(r"if\s*\([\s\S]*$", "", value).strip()
    return value


def parse_list_page(stage: str, channel: str, page_index: int) -> list[dict[str, Any]]:
    url = f"{BASE}/{channel}/List/list_{page_index}.htm"
    try:
        raw = Fetcher.get(url, stealthy_headers=True, timeout=25).body
        doc = lhtml.fromstring(raw)
    except Exception:  # noqa: BLE001
        logger.exception("minhang fetch failed %s", url)
        return []
    rows_out: list[dict[str, Any]] = []
    rows = doc.xpath('//table[@id="table"]//tr')
    for row_idx, tr in enumerate(rows[1:]):
        cells = [_clean_cell(c.text_content() or "") for c in tr.xpath("./td")]
        if len(cells) < 2:
            continue
        project_name = cells[0]
        if not project_name or project_name in {"项目名称", "项目"}:
            continue
        links = [urljoin(url, href) for href in tr.xpath(".//a/@href") if href]
        date_text = ""
        for cell in cells:
            if re.search(r"\d{4}-\d{2}-\d{2}", cell):
                date_text = cell
                break
        rows_out.append(
            {
                "stage": stage,
                "channel": channel,
                "page_index": page_index,
                "row_index": row_idx,
                "project_name": project_name,
                "eia_form": cells[1] if len(cells) > 1 else "",
                "date_text": date_text,
                "cells": cells[:8],
                "links": links,
                "source_url": url,
            }
        )
    return rows_out
