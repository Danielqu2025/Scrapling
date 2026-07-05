"""E2 attachment download with captcha (semi-automated, user provides code)."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Any

from scrapling.fetchers import FetcherSession

from _common import is_html_response
from db.store import EIAStore
from sources.e2_qygk.client import detail_url_for, response_text

logger = logging.getLogger(__name__)

CAPTCHA_URL = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/qygk/getValidateFjCode.do"
FILEDOWN_URL = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/file/filedown.do"
SESSION_TTL_SECONDS = 1800


def _captcha_url() -> str:
    return f"{CAPTCHA_URL}?&time={int(time.time() * 1000)}"


def _captcha_headers(referer: str) -> dict[str, str]:
    return {
        "Referer": referer,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _open_http_session(cookies_json: str = "") -> tuple[FetcherSession, Any]:
    fetcher = FetcherSession(impersonate="chrome", retries=5, retry_delay=2)
    session = fetcher.__enter__()
    if cookies_json:
        try:
            cookies = json.loads(cookies_json)
        except json.JSONDecodeError:
            cookies = {}
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                session._curl_session.cookies.set(name, value)
    return fetcher, session


def _close_http_session(fetcher: FetcherSession) -> None:
    try:
        fetcher.__exit__(None, None, None)
    except Exception:
        pass


def _export_cookies(session) -> str:
    return json.dumps(session._curl_session.cookies.get_dict(), ensure_ascii=False)


def _fetch_captcha_image(session, referer: str) -> tuple[bytes, str]:
    response = session.get(_captcha_url(), stealthy_headers=True, headers=_captcha_headers(referer))
    image_bytes = response.body if isinstance(response.body, bytes) else response.body.encode()
    if len(image_bytes) < 64:
        raise ValueError("无法获取验证码图片，请稍后重试。")
    headers = response.headers or {}
    content_type = headers.get("content-type", "image/jpeg")
    return image_bytes, content_type


def _is_download_error(body: bytes, text_head: str) -> bool:
    if is_html_response(body):
        return True
    lowered = text_head.lower()
    return "验证码错误" in text_head or "alert(" in lowered or "window.close" in lowered


def _store() -> EIAStore:
    return EIAStore()


def _load_session_row(session_id: str) -> dict[str, Any]:
    store = _store()
    store.purge_expired_e2_captcha_sessions(SESSION_TTL_SECONDS)
    row = store.get_e2_captcha_session(session_id)
    if row is None:
        raise ValueError("验证码会话已过期，请关闭弹窗后重新点击「验证码下载」。")
    return row


def _persist_cookies(session_id: str, session) -> None:
    _store().update_e2_captcha_session_cookies(session_id, _export_cookies(session))


def start_captcha_session(
    file_external_id: str,
    event_external_id: str,
    nm_type: str | int | None = None,
    detail_url: str | None = None,
) -> dict[str, str]:
    """Open HTTP session, load detail page + captcha image, persist cookies in SQLite."""
    referer = detail_url or detail_url_for(event_external_id, nm_type)
    fetcher, session = _open_http_session()
    try:
        session.get(referer, stealthy_headers=True)
        image_bytes, content_type = _fetch_captcha_image(session, referer)
        cookies_json = _export_cookies(session)
    except Exception:
        _close_http_session(fetcher)
        raise
    finally:
        _close_http_session(fetcher)

    session_id = uuid.uuid4().hex
    _store().save_e2_captcha_session(
        session_id,
        file_external_id=file_external_id,
        event_external_id=event_external_id,
        referer=referer,
        cookies_json=cookies_json,
    )
    logger.info("Started e2 captcha session %s for file %s", session_id[:8], file_external_id[:8])
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

    row = _load_session_row(session_id)
    fetcher, session = _open_http_session(row["cookies_json"])
    try:
        response = session.post(
            FILEDOWN_URL,
            data={"fileId": row["file_external_id"], "fileYzm": captcha},
            stealthy_headers=True,
            headers={
                "Referer": row["referer"],
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        _persist_cookies(session_id, session)
        body = response.body if isinstance(response.body, bytes) else response.body.encode()
        text_head = response_text(response)[:500]
        if _is_download_error(body, text_head):
            raise ValueError("验证码错误或已过期，请点击「换一张」后重新输入。")
        if len(body) < 128:
            raise ValueError("下载失败，服务器未返回有效文件，请换一张验证码后重试。")
        headers = response.headers or {}
        content_type = headers.get("content-type", "application/octet-stream")
    finally:
        _close_http_session(fetcher)

    _store().delete_e2_captcha_session(session_id)
    return body, content_type


def refresh_captcha(session_id: str) -> dict[str, str]:
    row = _load_session_row(session_id)
    fetcher, session = _open_http_session(row["cookies_json"])
    try:
        session.get(row["referer"], stealthy_headers=True)
        image_bytes, content_type = _fetch_captcha_image(session, row["referer"])
        _persist_cookies(session_id, session)
    finally:
        _close_http_session(fetcher)

    return {
        "session_id": session_id,
        "captcha_base64": base64.b64encode(image_bytes).decode("ascii"),
        "content_type": content_type,
    }
