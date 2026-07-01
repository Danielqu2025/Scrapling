"""FastAPI application for team search, sync, and downloads."""

from __future__ import annotations

import os
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from _common import is_html_response, normalize_response_body, resolve_download_url
from _paths import APP_DIR, DATA_DIR
from db.store import EIAStore, SORT_BY_OPTIONS, SORT_ORDER_OPTIONS, utc_now
from sources.e2_qygk.download import refresh_captcha, start_captcha_session, submit_captcha_download
from sources.types import DISCLOSURE_TYPES, SOURCE_E2_QYGK, SOURCE_LINK_STHJ
from sync.checker import UpdateChecker
from sync.crawler import SyncService

STATIC_DIR = APP_DIR / "static"
DOWNLOAD_DIR = DATA_DIR / "downloads"

store = EIAStore()
sync_service = SyncService(store)
update_checker = UpdateChecker(store)
sync_lock = threading.Lock()
update_check_lock = threading.Lock()
sync_running = False
update_check_state: dict[str, Any] = {
    "checking": False,
    "has_updates": False,
    "checked_at": None,
    "message": "",
    "new_count": 0,
    "details": {},
    "auto_sync_started": False,
    "error": "",
}


def _startup_check_enabled() -> bool:
    return os.getenv("SH_EIA_STARTUP_CHECK", "1").lower() not in {"0", "false", "off", "no"}


def _startup_check_mode() -> str:
    return os.getenv("SH_EIA_STARTUP_CHECK_MODE", "remind").lower()


def _set_update_check_state(**kwargs: Any) -> None:
    with update_check_lock:
        update_check_state.update(kwargs)


def _get_update_check_state() -> dict[str, Any]:
    with update_check_lock:
        return dict(update_check_state)


def _run_update_check(trigger_mode: str = "startup") -> dict[str, Any]:
    with update_check_lock:
        if update_check_state["checking"]:
            return dict(update_check_state)
        update_check_state["checking"] = True
        update_check_state["error"] = ""

    try:
        result = update_checker.check()
        _set_update_check_state(
            checking=False,
            has_updates=result["has_updates"],
            checked_at=utc_now(),
            message=result["message"],
            new_count=result["new_count"],
            details=result["details"],
            auto_sync_started=False,
        )
        if result["has_updates"] and trigger_mode == "auto":
            if not sync_running:
                _run_sync_job(
                    [SOURCE_LINK_STHJ],
                    list(DISCLOSURE_TYPES),
                    1,
                    False,
                    "auto",
                )
                _set_update_check_state(auto_sync_started=True)
        return result
    except Exception as exc:
        _set_update_check_state(
            checking=False,
            has_updates=False,
            checked_at=utc_now(),
            message=f"检查更新失败: {exc}",
            new_count=0,
            details={},
            error=str(exc),
        )
        return {"has_updates": False, "message": str(exc), "new_count": 0, "details": {}}


def _run_startup_check() -> None:
    mode = _startup_check_mode()
    if mode in {"0", "off", "false", "no"}:
        return
    trigger = "auto" if mode == "auto" else "startup"
    _run_update_check(trigger_mode=trigger)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _startup_check_enabled():
        threading.Thread(target=_run_startup_check, daemon=True, name="sh_eia_startup_check").start()
    yield


app = FastAPI(title="上海环评资料检索", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SyncRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: [SOURCE_LINK_STHJ, SOURCE_E2_QYGK])
    disclosure_types: list[str] = Field(default_factory=lambda: list(DISCLOSURE_TYPES))
    full_sync: bool = False
    max_pages: int = Field(default=1, ge=1, description="Used when full_sync is false.")
    fetch_e2_details: bool = True


class DownloadRequest(BaseModel):
    file_ids: list[int]


class E2CaptchaRequest(BaseModel):
    file_id: int


class E2CaptchaSubmitRequest(BaseModel):
    file_id: int
    session_id: str
    captcha: str = Field(min_length=1, max_length=12)


def _reload_store() -> None:
    global store, sync_service, update_checker
    store = EIAStore()
    sync_service = SyncService(store)
    update_checker = UpdateChecker(store)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return cleaned or "download.bin"


def _run_sync_job(
    sources: list[str],
    disclosure_types: list[str],
    max_pages: int | None,
    fetch_e2_details: bool,
    trigger_mode: str,
) -> None:
    global sync_running
    with sync_lock:
        sync_running = True
    status = "failed"
    try:
        result = sync_service.sync(
            sources=sources,
            disclosure_types=disclosure_types,
            max_pages=max_pages,
            fetch_e2_details=fetch_e2_details,
            trigger_mode=trigger_mode,
        )
        status = result.get("status", "failed")
        if status == "success":
            store.write_manifest()
    finally:
        with sync_lock:
            sync_running = False
        if status == "success":
            threading.Thread(
                target=lambda: _run_update_check("startup"),
                daemon=True,
                name="sh_eia_post_sync_check",
            ).start()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    return store.stats()


@app.get("/api/manifest")
def api_manifest(refresh: bool = Query(default=False)) -> dict[str, Any]:
    if refresh or store.read_manifest() is None:
        return store.write_manifest()
    manifest = store.read_manifest()
    assert manifest is not None
    return manifest


@app.get("/api/master/{master_id}/progress")
def api_master_progress(master_id: int) -> dict[str, Any]:
    progress = store.get_progress(master_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="未找到该项目。")
    return progress


@app.get("/api/master/{master_id}/timeline")
def api_master_timeline(master_id: int) -> dict[str, Any]:
    timeline = store.get_timeline(master_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="未找到该项目。")
    return timeline


@app.get("/api/search")
def api_search(
    response: Response,
    q: str = Query(default=""),
    types: list[str] = Query(default=[]),
    limit: int = Query(default=50, ge=1, le=200),
    sort_by: str = Query(default="event_date", description="event_date | synced_at | type | project_name"),
    sort_order: str = Query(default="desc", description="asc | desc"),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    selected_types = types or list(DISCLOSURE_TYPES)
    for disclosure_type in selected_types:
        if disclosure_type not in DISCLOSURE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown disclosure type: {disclosure_type}")
    if sort_by not in SORT_BY_OPTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown sort_by: {sort_by}")
    if sort_order not in SORT_ORDER_OPTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown sort_order: {sort_order}")
    results = store.search(
        q,
        disclosure_types=selected_types,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return {
        "query": q,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "count": len(results),
        "results": results,
    }


@app.get("/api/sync/status")
def api_sync_status() -> dict[str, Any]:
    latest = store.latest_sync_job()
    return {"running": sync_running, "latest": latest}


@app.get("/api/updates/status")
def api_updates_status() -> dict[str, Any]:
    return _get_update_check_state()


@app.post("/api/updates/check")
def api_updates_check(background_tasks: BackgroundTasks) -> dict[str, Any]:
    state = _get_update_check_state()
    if state["checking"]:
        return {"status": "running", **state}
    background_tasks.add_task(_run_update_check, "startup")
    return {"status": "accepted", "message": "正在检查官网是否有新公示..."}


@app.get("/api/database/export")
def api_database_export() -> StreamingResponse:
    if not store.db_path.exists():
        raise HTTPException(status_code=404, detail="当前没有可导出的数据库。")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = store.export_database_zip()
    return StreamingResponse(
        BytesIO(payload),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sh_eia_backup_{stamp}.zip"'},
    )


@app.post("/api/database/import")
async def api_database_import(file: UploadFile = File(...)) -> dict[str, Any]:
    if sync_running:
        raise HTTPException(status_code=409, detail="同步进行中，请稍后再导入数据库。")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空。")
    try:
        result = store.import_database_bytes(data, backup=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_store()
    _set_update_check_state(
        checking=False,
        has_updates=False,
        checked_at=utc_now(),
        message="已导入数据库备份。",
        new_count=0,
        details={},
        auto_sync_started=False,
        error="",
    )
    return {"status": "success", "message": "数据库导入成功。", **result}


@app.post("/api/sync")
def api_sync(request: SyncRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if sync_running:
        raise HTTPException(status_code=409, detail="已有同步任务正在运行，请稍后再试。")

    for source in request.sources:
        if source not in {SOURCE_LINK_STHJ, SOURCE_E2_QYGK}:
            raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

    for disclosure_type in request.disclosure_types:
        if disclosure_type not in DISCLOSURE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown disclosure type: {disclosure_type}")

    max_pages = None if request.full_sync else request.max_pages
    mode_label = "全量同步" if request.full_sync else f"每类 {max_pages} 页"
    background_tasks.add_task(
        _run_sync_job,
        request.sources,
        request.disclosure_types,
        max_pages,
        request.fetch_e2_details,
        "manual",
    )
    return {
        "status": "accepted",
        "message": f"同步任务已开始（{mode_label}），可在同步状态中查看进度。",
        "full_sync": request.full_sync,
        "max_pages": max_pages,
        "disclosure_types": request.disclosure_types,
    }


@app.post("/api/download/e2/captcha")
def api_download_e2_captcha(request: E2CaptchaRequest) -> dict[str, Any]:
    file_item = store.get_file_detail(request.file_id)
    if file_item is None:
        raise HTTPException(status_code=404, detail="未找到文件。")
    if file_item.get("source") != SOURCE_E2_QYGK:
        raise HTTPException(status_code=400, detail="该文件不属于中后期平台附件。")
    if file_item.get("download_status") != "captcha_required":
        raise HTTPException(status_code=400, detail="该文件无需验证码。")
    external_id = file_item.get("file_external_id") or ""
    event_external_id = file_item.get("event_external_id") or ""
    if not external_id or not event_external_id:
        raise HTTPException(status_code=400, detail="缺少文件标识，请重新同步该项目。")
    try:
        payload = start_captcha_session(external_id, event_external_id)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "file_id": request.file_id,
        "file_name": file_item.get("file_name", ""),
        "project_name": file_item.get("project_name", ""),
        **payload,
    }


@app.post("/api/download/e2/refresh")
def api_download_e2_refresh(session_id: str = Query(...)) -> dict[str, Any]:
    try:
        return refresh_captcha(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/download/e2/file")
def api_download_e2_file(request: E2CaptchaSubmitRequest) -> StreamingResponse:
    file_item = store.get_file_detail(request.file_id)
    if file_item is None:
        raise HTTPException(status_code=404, detail="未找到文件。")
    try:
        body, content_type = submit_captcha_download(request.session_id, request.captcha)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filename = _safe_filename(file_item.get("file_name") or f"e2_{request.file_id}.pdf")
    return StreamingResponse(
        BytesIO(body),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/download/zip")
def api_download_zip(request: DownloadRequest) -> StreamingResponse:
    files = store.get_files(request.file_ids)
    if not files:
        raise HTTPException(status_code=404, detail="未找到可下载文件。")

    from scrapling.fetchers import FetcherSession

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with FetcherSession(impersonate="chrome") as session:
            errors: list[str] = []
            for index, file_item in enumerate(files, 1):
                if file_item.get("download_status") == "captcha_required":
                    filename = _safe_filename(file_item.get("file_name") or f"attachment_{index}")
                    errors.append(f"{filename}: 需在官网验证码页面下载（中后期附件）")
                    continue
                url = resolve_download_url(file_item["file_url"], file_item.get("file_name", ""))
                response = session.get(url, stealthy_headers=True)
                body = normalize_response_body(response.body)
                filename = _safe_filename(
                    file_item.get("file_name") or f"{file_item['project_name']}_{file_item['file_type']}_{index}.pdf"
                )
                if is_html_response(body):
                    errors.append(f"{filename}: 服务器未返回有效文件（可能已过期或不存在）")
                    continue
                archive.writestr(filename, body)

            if errors and not any(name for name in archive.namelist() if not name.startswith("_")):
                raise HTTPException(status_code=502, detail="所选文件均无法下载：" + "；".join(errors))
            if errors:
                archive.writestr(
                    "_download_errors.txt",
                    "以下文件下载失败：\n" + "\n".join(errors),
                )

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sh_eia_files.zip"'},
    )
