"""Songjiang EIA API client for district sync."""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any, Iterator

from scrapling.fetchers import FetcherSession

logger = logging.getLogger(__name__)

BASE = "https://www.songjiang.gov.cn"
API_URL = f"{BASE}/shsj-application-front/rest/hjbhapiaction/getjsfj"
PAGE_URL = f"{BASE}/Template/govinfo/hpspdetail.html"
GONGSHI_TYPE = "环评项目公告"
DEFAULT_BEGIN = "2018-01-01"
REQUEST_TIMEOUT = 120
POST_MAX_ATTEMPTS = 5
POST_RETRY_BACKOFF = (3, 6, 10, 15, 20)
PAGE_SLEEP_SECONDS = 1.5


def page_sleep_seconds() -> float:
    return PAGE_SLEEP_SECONDS


def _body_json(resp: Any) -> dict[str, Any]:
    raw = resp.body
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw or "")
    return json.loads(text)


def _post_page(session: FetcherSession, *, page_number: int, page_size: int, begin: str, end: str) -> dict[str, Any]:
    params = {
        "pageNumber": page_number,
        "pageSize": page_size,
        "gongshitype": GONGSHI_TYPE,
        "begindate": begin,
        "enddate": end,
    }
    last_error: Exception | None = None
    for attempt in range(POST_MAX_ATTEMPTS):
        try:
            resp = session.post(
                API_URL,
                data={"params": json.dumps(params, ensure_ascii=False)},
                stealthy_headers=True,
                headers={
                    "Referer": PAGE_URL,
                    "Origin": BASE,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            return _body_json(resp)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("songjiang page %s attempt %s/%s failed: %s", page_number, attempt + 1, POST_MAX_ATTEMPTS, exc)
            if attempt + 1 < POST_MAX_ATTEMPTS:
                time.sleep(POST_RETRY_BACKOFF[min(attempt, len(POST_RETRY_BACKOFF) - 1)])
    raise last_error or RuntimeError("songjiang request failed")


def fetch_list_page(
    session: FetcherSession,
    *,
    page_number: int,
    page_size: int = 50,
    begin: str = DEFAULT_BEGIN,
    end: str | None = None,
) -> dict[str, Any]:
    end = end or date.today().isoformat()
    payload = _post_page(session, page_number=page_number, page_size=page_size, begin=begin, end=end)
    custom = payload.get("custom") or {}
    return {
        "records": custom.get("data") or [],
        "total": int(custom.get("total") or 0),
    }


def iter_pages(
    session: FetcherSession,
    *,
    page_size: int = 50,
    max_pages: int | None = None,
    start_page: int = 0,
    begin: str = DEFAULT_BEGIN,
    end: str | None = None,
) -> Iterator[tuple[int, list[dict[str, Any]], int]]:
    """Yield (page_number_0based, records, total)."""
    page_number = start_page
    while True:
        if max_pages is not None and page_number >= start_page + max_pages:
            break
        data = fetch_list_page(session, page_number=page_number, page_size=page_size, begin=begin, end=end)
        records = data["records"]
        total = data["total"]
        yield page_number, records, total
        if not records:
            break
        if (page_number + 1) * page_size >= total:
            break
        page_number += 1
        time.sleep(PAGE_SLEEP_SECONDS)


def remote_total(*, page_size: int = 1, begin: str = DEFAULT_BEGIN) -> int:
    with FetcherSession(impersonate="chrome", timeout=REQUEST_TIMEOUT) as session:
        session.get(PAGE_URL, stealthy_headers=True)
        data = fetch_list_page(session, page_number=0, page_size=page_size, begin=begin)
        return int(data.get("total") or 0)

