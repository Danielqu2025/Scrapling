"""HTTP helpers for e2.sthj.sh.gov.cn."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Callable

from scrapling.fetchers import FetcherSession

logger = logging.getLogger(__name__)

LIST_URL = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/hjxxgk/jsxmzhq_list.jsp"
DETAIL_URL_TEMPLATE = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/jsxmInfo_edit.jsp?id={external_id}"
FILE_BASE = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/file/filedown.do"

DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAY = 2.0

HIDDEN_FIELD_NAMES = (
    "currentPage",
    "nd",
    "xm",
    "districts",
    "status",
    "type",
    "zhqTab",
    "districtAll",
)


def response_text(response) -> str:
    body = response.body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="ignore")
    return body or ""


def response_status(response) -> int:
    for attr in ("status", "status_code"):
        value = getattr(response, attr, None)
        if value is not None:
            return int(value)
    return 200


def _looks_like_error_page(text: str) -> bool:
    if not text or len(text) < 200:
        return True
    lowered = text.lower()
    return (
        "internal server error" in lowered
        or "http status 500" in lowered
        or "服务器错误" in text
        or "系统异常" in text
    )


def _extract_hidden_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in HIDDEN_FIELD_NAMES:
        match = re.search(rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"', html, re.IGNORECASE)
        if match:
            fields[name] = match.group(1)
    active_status = re.search(
        r'dqzt[^>]*active[^>]*>.*?classtype[^>]*>([^<]+)<',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if active_status:
        fields.setdefault("status", _clean_text(active_status.group(1)))
    active_type = re.search(
        r'yslx[^>]*active[^>]*>.*?classtype[^>]*>([^<]+)<',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if active_type:
        fields.setdefault("type", _clean_text(active_type.group(1)))
    return fields


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def fetch_with_retry(
    fetch_fn: Callable[[], tuple[str, int]],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_RETRY_DELAY,
    label: str = "request",
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            text, status = fetch_fn()
            if status >= 500 or _looks_like_error_page(text):
                raise RuntimeError(f"{label} failed with HTTP {status}")
            if status >= 400:
                raise RuntimeError(f"{label} rejected with HTTP {status}")
            return text
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning("%s attempt %s/%s failed: %s; retry in %.1fs", label, attempt, max_retries, exc, delay)
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def fetch_list_page(page_no: int = 1, year: str = "", max_retries: int = DEFAULT_MAX_RETRIES) -> str:
    headers = {"Referer": LIST_URL, "Content-Type": "application/x-www-form-urlencoded"}

    def _do_fetch() -> tuple[str, int]:
        with FetcherSession(impersonate="chrome") as session:
            warmup = session.get(LIST_URL, stealthy_headers=True)
            warmup_html = response_text(warmup)
            form_fields = _extract_hidden_fields(warmup_html)
            form_fields["currentPage"] = str(page_no)
            if year:
                form_fields["nd"] = year
            elif not form_fields.get("nd"):
                form_fields["nd"] = str(datetime.now().year)
            form_fields.setdefault("xm", "")
            form_fields.setdefault("districts", "")
            form_fields.setdefault("districtAll", "")
            form_fields.setdefault("status", "")
            form_fields.setdefault("type", "")
            form_fields.setdefault("zhqTab", "0")

            if page_no <= 1 and not year:
                return warmup_html, response_status(warmup)

            response = session.post(LIST_URL, data=form_fields, stealthy_headers=True, headers=headers)
            return response_text(response), response_status(response)

    return fetch_with_retry(_do_fetch, max_retries=max_retries, label=f"e2 list page {page_no}")


def fetch_detail_page(external_id: str, max_retries: int = DEFAULT_MAX_RETRIES) -> str:
    url = DETAIL_URL_TEMPLATE.format(external_id=external_id)

    def _do_fetch() -> tuple[str, int]:
        with FetcherSession(impersonate="chrome") as session:
            response = session.get(url, stealthy_headers=True)
            return response_text(response), response_status(response)

    return fetch_with_retry(_do_fetch, max_retries=max_retries, label=f"e2 detail {external_id[:8]}")
