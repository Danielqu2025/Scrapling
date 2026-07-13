"""Pudong EIA list API client for district sync."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from scrapling.fetchers import FetcherSession

logger = logging.getLogger(__name__)

BASE = "https://www.pudong.gov.cn"
INDEX_URL = f"{BASE}/zwgk/14464.gkml_ywl_wsgs_hbspgs/index.html"
API_URL = f"{BASE}/zwgk-search-front/api/data/search"
CHANNEL_ID = "16783"
DEFAULT_PAGE_SIZE = 20
REQUEST_TIMEOUT = 60
POST_MAX_ATTEMPTS = 5
POST_RETRY_BACKOFF = (2, 4, 8, 12, 16)
PAGE_SLEEP_SECONDS = 0.8


def page_sleep_seconds() -> float:
    return PAGE_SLEEP_SECONDS


def _body_json(resp: Any) -> dict[str, Any]:
    raw = resp.body
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw or "")
    return json.loads(text)


def _post_page(
    session: FetcherSession,
    *,
    page_number: int,
    page_size: int,
) -> dict[str, Any]:
    payload = {
        "pageNo": page_number,
        "pageSize": page_size,
        "channelList": [CHANNEL_ID],
    }
    last_error: Exception | None = None
    for attempt in range(POST_MAX_ATTEMPTS):
        try:
            resp = session.post(
                API_URL,
                json=payload,
                stealthy_headers=True,
                headers={
                    "Referer": INDEX_URL,
                    "Origin": BASE,
                    "Content-Type": "application/json;charset=UTF-8",
                },
            )
            data = _body_json(resp)
            if int(data.get("code") or 0) != 0:
                raise RuntimeError(f"pudong api error: {data.get('message') or data}")
            return data.get("data") or {}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "pudong page %s attempt %s/%s failed: %s",
                page_number,
                attempt + 1,
                POST_MAX_ATTEMPTS,
                exc,
            )
            if attempt + 1 < POST_MAX_ATTEMPTS:
                time.sleep(POST_RETRY_BACKOFF[min(attempt, len(POST_RETRY_BACKOFF) - 1)])
    raise last_error or RuntimeError("pudong request failed")


def fetch_list_page(
    session: FetcherSession,
    *,
    page_number: int,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    data = _post_page(session, page_number=page_number, page_size=page_size)
    return {
        "records": data.get("list") or [],
        "total": int(data.get("totalCount") or 0),
        "total_page": int(data.get("totalPage") or 0),
    }


def iter_pages(
    session: FetcherSession,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int | None = None,
    start_page: int = 1,
) -> Iterator[tuple[int, list[dict[str, Any]], int]]:
    """Yield (page_number_1based, records, total)."""
    page_number = start_page
    total = 0
    pages_fetched = 0
    while True:
        if max_pages is not None and pages_fetched >= max_pages:
            break
        data = fetch_list_page(session, page_number=page_number, page_size=page_size)
        records = data["records"]
        if not total:
            total = int(data.get("total") or 0)
        yield page_number, records, total
        pages_fetched += 1
        if not records:
            break
        if total and page_number * page_size >= total:
            break
        page_number += 1
        time.sleep(PAGE_SLEEP_SECONDS)


def display_date_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def remote_total(*, page_size: int = 1) -> int:
    with FetcherSession(impersonate="chrome", timeout=REQUEST_TIMEOUT) as session:
        session.get(INDEX_URL, stealthy_headers=True)
        data = fetch_list_page(session, page_number=1, page_size=page_size)
        return int(data.get("total") or 0)
