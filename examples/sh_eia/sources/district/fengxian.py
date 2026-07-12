"""Fengxian EIA list API client for district sync."""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Iterator

from scrapling.fetchers import FetcherSession

from sources.district import STAGE_ACCEPTANCE, STAGE_DECISION, STAGE_PROPOSED

BASE = "http://211.136.184.90:8090"
_JS_URL = f"{BASE}/js/app.698d0adb.js"
LIST_PATHS = {
    STAGE_ACCEPTANCE: "/hp/huanbao/AuditAccept/List",
    STAGE_PROPOSED: "/hp/huanbao/AuditResult/List",
    STAGE_DECISION: "/hp/huanbao/AuditDecision/List",
}

_token_lock = threading.Lock()
_cached_creds: tuple[str, str] | None = None


def _extract_creds(js_text: str) -> tuple[str, str]:
    match = re.search(r'username:"([^"]+)",password:"([^"]+)"', js_text)
    if not match:
        raise RuntimeError("奉贤公示系统前端未找到查询账号配置")
    return match.group(1), match.group(2)


def _parse_body(resp: Any) -> dict[str, Any]:
    if hasattr(resp, "json"):
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            pass
    raw = resp.body
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw or "")
    return json.loads(text)


def login(session: FetcherSession) -> str:
    global _cached_creds
    with _token_lock:
        if _cached_creds is None:
            js = session.get(_JS_URL, stealthy_headers=True)
            body = js.body.decode("utf-8", "ignore") if isinstance(js.body, bytes) else str(js.body)
            _cached_creds = _extract_creds(body)
        username, password = _cached_creds
    session.get(f"{BASE}/acceptance", stealthy_headers=True)
    resp = session.post(
        f"{BASE}/hp/auth/login",
        json={"username": username, "password": password},
        stealthy_headers=True,
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": f"{BASE}/acceptance",
        },
    )
    payload = _parse_body(resp)
    token = (payload.get("data") or {}).get("accessToken")
    if not token:
        raise RuntimeError(f"奉贤登录失败: {payload}")
    return token


def fetch_list_page(
    session: FetcherSession,
    token: str,
    stage: str,
    *,
    page_no: int,
    page_size: int = 50,
) -> dict[str, Any]:
    path = LIST_PATHS[stage]
    resp = session.get(
        f"{BASE}{path}",
        params={"pageNo": page_no, "pageSize": page_size},
        stealthy_headers=True,
        headers={"hp-token": token, "Referer": f"{BASE}/acceptance"},
    )
    if getattr(resp, "status", 0) != 200:
        raise RuntimeError(f"奉贤列表失败 status={getattr(resp, 'status', None)} stage={stage} page={page_no}")
    return _parse_body(resp).get("data") or {}


def iter_stage_pages(
    session: FetcherSession,
    token: str,
    stage: str,
    *,
    page_size: int = 50,
    max_pages: int | None = None,
) -> Iterator[tuple[int, list[dict[str, Any]], int]]:
    """Yield (page_no, records, total)."""
    page_no = 1
    total = 0
    while True:
        if max_pages is not None and page_no > max_pages:
            break
        data = fetch_list_page(session, token, stage, page_no=page_no, page_size=page_size)
        records = data.get("records") or []
        total = int(data.get("total") or 0)
        yield page_no, records, total
        if not records:
            break
        if page_no * page_size >= total:
            break
        page_no += 1


def remote_totals(page_size: int = 1) -> dict[str, int]:
    """Return {stage: total_records} from page-1 probes."""
    totals: dict[str, int] = {}
    with FetcherSession(impersonate="chrome", timeout=25) as session:
        token = login(session)
        for stage in LIST_PATHS:
            data = fetch_list_page(session, token, stage, page_no=1, page_size=page_size)
            totals[stage] = int(data.get("total") or 0)
    return totals

