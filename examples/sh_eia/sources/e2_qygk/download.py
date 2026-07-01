"""E2 attachment download with captcha (semi-automated, user provides code)."""

from __future__ import annotations

import base64
import threading
import time
import uuid
from typing import Any

from scrapling.fetchers import FetcherSession

from sources.e2_qygk.client import response_text

CAPTCHA_URL = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/qygk/getValidateFjCode.do"
FILEDOWN_URL = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/file/filedown.do"
DETAIL_URL_TEMPLATE = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/jsxmInfo_edit.jsp?id={external_id}"

SESSION_TTL_SECONDS = 600

_store_lock = threading.Lock()
_sessions: dict[str, dict[str, Any]] = {}


def _purge_expired() -> None:
    now = time.time()
    expired = [key for key, item in _sessions.items() if now - item["created_at"] > SESSION_TTL_SECONDS]
    for key in expired:
        session = _sessions.pop(key, None)
        if session and session.get("fetcher") is not None:
            try:
                session["fetcher"].__exit__(None, None, None)
            except Exception:
                pass


def _detail_referer(external_id: str) -> str:
    return DETAIL_URL_TEMPLATE.format(external_id=external_id)


def start_captcha_session(file_external_id: str, event_external_id: str) -> dict[str, str]:
    """Open HTTP session, load detail page + captcha image."""
    _purge_expired()
    fetcher = FetcherSession(impersonate="chrome")
    session = fetcher.__enter__()
    referer = _detail_referer(event_external_id)
    session.get(referer, stealthy_headers=True)
    response = session.get(CAPTCHA_URL, stealthy_headers=True, headers={"Referer": referer})
    image_bytes = response.body if isinstance(response.body, bytes) else response.body.encode()
    if len(image_bytes) < 64:
        fetcher.__exit__(None, None, None)
        raise ValueError("无法获取验证码图片，请稍后重试。")

    session_id = uuid.uuid4().hex
    content_type = response.headers.get("content-type", "image/jpeg")
    with _store_lock:
        _sessions[session_id] = {
            "created_at": time.time(),
            "fetcher": fetcher,
            "session": session,
            "file_external_id": file_external_id,
            "event_external_id": event_external_id,
            "referer": referer,
        }
    return {
        "session_id": session_id,
        "captcha_base64": base64.b64encode(image_bytes).decode("ascii"),
        "content_type": content_type,
    }


def submit_captcha_download(session_id: str, captcha: str) -> tuple[bytes, str]:
    """POST filedown.do with user-entered captcha; returns (body, content_type)."""
    captcha = captcha.strip()
    if not captcha:
        raise ValueError("请输入验证码。")

    with _store_lock:
        entry = _sessions.pop(session_id, None)
    if entry is None:
        raise ValueError("验证码会话已过期，请重新获取。")

    fetcher = entry["fetcher"]
    session = entry["session"]
    referer = entry["referer"]
    file_id = entry["file_external_id"]

    try:
        response = session.post(
            FILEDOWN_URL,
            data={"fileId": file_id, "fileYzm": captcha},
            stealthy_headers=True,
            headers={"Referer": referer, "Content-Type": "application/x-www-form-urlencoded"},
        )
        body = response.body if isinstance(response.body, bytes) else response.body.encode()
        text_head = response_text(response)[:200]
        if "验证码错误" in text_head or "alert" in text_head.lower():
            raise ValueError("验证码错误，请重试。")
        if len(body) < 128:
            raise ValueError("下载失败，服务器未返回有效文件。")
        content_type = response.headers.get("content-type", "application/octet-stream")
        return body, content_type
    finally:
        fetcher.__exit__(None, None, None)


def refresh_captcha(session_id: str) -> dict[str, str]:
    with _store_lock:
        entry = _sessions.get(session_id)
    if entry is None:
        raise ValueError("验证码会话已过期，请重新获取。")

    session = entry["session"]
    referer = entry["referer"]
    response = session.get(CAPTCHA_URL, stealthy_headers=True, headers={"Referer": referer})
    image_bytes = response.body if isinstance(response.body, bytes) else response.body.encode()
    content_type = response.headers.get("content-type", "image/jpeg")
    return {
        "session_id": session_id,
        "captcha_base64": base64.b64encode(image_bytes).decode("ascii"),
        "content_type": content_type,
    }
