"""FastAPI application for team search, sync, and downloads."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from _common import is_html_response, normalize_response_body, resolve_download_url
from _paths import APP_DIR, DATA_DIR
from app.admin import router as admin_router
from app.auth import auth_enabled, ensure_auth_ready, router as auth_router, user_from_request
from app.security import setup_security
from db.store import EIAStore, SORT_BY_OPTIONS, SORT_ORDER_OPTIONS, utc_now
from sources.e2_qygk.download import refresh_captcha, start_captcha_session, submit_captcha_download
from sources.types import (
    DISCLOSURE_TYPES,
    FILE_TYPE_LABELS,
    SOURCE_DISTRICT_FENGXIAN,
    SOURCE_DISTRICT_MINHANG,
    SOURCE_DISTRICT_PUDONG,
    SOURCE_DISTRICT_SONGJIANG,
    SOURCE_E2_QYGK,
    SOURCE_LINK_STHJ,
)
from sync.checker import UpdateChecker
from sync.crawler import SyncService

logger = logging.getLogger(__name__)

STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
DOWNLOAD_DIR = DATA_DIR / "downloads"

_AUTH_PUBLIC_EXACT = {"/login", "/health", "/favicon.ico"}
_AUTH_PUBLIC_PREFIXES = (
    "/static/",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/config",
)

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


def _is_public_path(path: str) -> bool:
    if path in _AUTH_PUBLIC_EXACT:
        return True
    return any(path == p.rstrip("/") or path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES)


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not auth_enabled() or _is_public_path(request.url.path):
            return await call_next(request)

        user = user_from_request(request)
        path = request.url.path
        if user is None:
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "未登录"})
            return RedirectResponse(url="/login", status_code=302)

        if path.startswith("/admin") or path.startswith("/api/admin"):
            if user.get("role") != "admin":
                if path.startswith("/api/"):
                    return JSONResponse(status_code=403, content={"detail": "需要管理员权限"})
                return RedirectResponse(url="/", status_code=302)

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_auth_ready()
    if _startup_check_enabled():
        threading.Thread(target=_run_startup_check, daemon=True, name="sh_eia_startup_check").start()
    yield


app = FastAPI(title="上海环评资料检索", version="0.1.0", lifespan=lifespan)
setup_security(app)
app.add_middleware(AuthGateMiddleware)
app.include_router(auth_router)
app.include_router(admin_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


class SyncRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: [SOURCE_LINK_STHJ, SOURCE_E2_QYGK])
    disclosure_types: list[str] = Field(default_factory=lambda: list(DISCLOSURE_TYPES))
    full_sync: bool = False
    max_pages: int = Field(default=2, ge=1, description="Used when full_sync is false.")
    fetch_e2_details: bool = True
    force: bool = Field(default=False, description="Skip completeness check and force full download.")
    skip_completeness_check: bool = Field(default=False, description="Run full sync without pre-check.")


class DownloadRequest(BaseModel):
    file_ids: list[int]


class E2CaptchaRequest(BaseModel):
    file_id: int


class SingleFileDownloadRequest(BaseModel):
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


_MAX_PROJECT_IN_FILENAME = 40
_MAX_DOWNLOAD_FILENAME = 120


def _download_filename(file_item: dict[str, Any]) -> str:
    """Build a browse-friendly download name: {项目短名}_{用途标签}.ext"""
    original = (file_item.get("file_name") or "").strip()
    suffix = Path(original).suffix
    if not suffix or len(suffix) > 8:
        suffix = ".pdf"

    project = re.sub(r"\s+", " ", (file_item.get("project_name") or "").strip())
    if len(project) > _MAX_PROJECT_IN_FILENAME:
        project = project[:_MAX_PROJECT_IN_FILENAME].rstrip()

    file_type = (file_item.get("file_type") or "").strip()
    label = (
        (file_item.get("file_type_label") or "").strip()
        or FILE_TYPE_LABELS.get(file_type, "")
        or file_type
        or "附件"
    )

    parts = [part for part in (project, label) if part]
    stem = "_".join(parts) if parts else (Path(original).stem if original else f"attachment_{file_item.get('id') or 'x'}")
    name = f"{stem}{suffix}"
    if len(name) > _MAX_DOWNLOAD_FILENAME:
        stem = stem[: _MAX_DOWNLOAD_FILENAME - len(suffix)].rstrip("._ ")
        name = f"{stem}{suffix}"
    return _safe_filename(name)


def _unique_zip_name(filename: str, file_item: dict[str, Any], used: set[str]) -> str:
    if filename not in used:
        used.add(filename)
        return filename
    path = Path(filename)
    candidate = f"{path.stem}_{file_item.get('id') or 'x'}{path.suffix}"
    used.add(candidate)
    return candidate


def _content_disposition(filename: str, fallback: str = "download.bin") -> str:
    safe = _safe_filename(filename) or fallback
    if safe.isascii():
        return f'attachment; filename="{safe}"'
    ascii_name = re.sub(r"[^\x20-\x7E]", "_", safe).strip("._") or fallback
    if "." not in ascii_name and "." in fallback:
        ascii_name = fallback
    encoded = quote(safe, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def _cache_path_for_file(file_item: dict[str, Any], filename: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_id = file_item.get("id") or "x"
    return DOWNLOAD_DIR / f"{file_id}_{filename}"


def _ensure_cached_link_file(file_item: dict[str, Any]) -> tuple[Path, str]:
    """Fetch link.sthj attachment to local cache (avoids re-downloading large PDFs)."""
    if file_item.get("download_status") == "captcha_required":
        raise HTTPException(status_code=400, detail="该文件需在官网输入验证码下载。")

    cache_name = _safe_filename(file_item.get("file_name") or "download.pdf")
    cache_path = _cache_path_for_file(file_item, cache_name)
    filename = _download_filename(file_item)
    if cache_path.exists() and cache_path.stat().st_size > 64:
        return cache_path, filename

    from scrapling.fetchers import FetcherSession

    url = resolve_download_url(file_item["file_url"], file_item.get("file_name", ""))
    try:
        with FetcherSession(impersonate="chrome", timeout=180, retries=2) as session:
            response = session.get(url, stealthy_headers=True, timeout=180, retries=2)
            body = normalize_response_body(response.body)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Official download failed for file_id=%s url=%s", file_item.get("id"), url)
        raise HTTPException(
            status_code=502,
            detail=f"{filename}：官网下载失败或超时，请稍后重试（{type(exc).__name__}）",
        ) from exc

    if is_html_response(body):
        raise HTTPException(
            status_code=502,
            detail=f"{filename}：服务器未返回有效文件（可能已过期或不存在）",
        )
    if not body:
        raise HTTPException(status_code=502, detail=f"{filename}：官网返回空文件")

    tmp_path = cache_path.with_suffix(cache_path.suffix + ".part")
    try:
        tmp_path.write_bytes(body)
        tmp_path.replace(cache_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return cache_path, filename


def _fetch_link_file_body(file_item: dict[str, Any]) -> tuple[bytes, str]:
    path, filename = _ensure_cached_link_file(file_item)
    return path.read_bytes(), filename


def _file_download_response(file_item: dict[str, Any], *, fallback: str) -> FileResponse:
    path, filename = _ensure_cached_link_file(file_item)
    return FileResponse(
        path=path,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename, fallback=fallback)},
    )


def _zip_download_response(file_ids: list[int]) -> StreamingResponse:
    files = store.get_files(file_ids)
    if not files:
        raise HTTPException(status_code=404, detail="未找到可下载文件。")

    buffer = BytesIO()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        errors: list[str] = []
        used_names: set[str] = set()
        for index, file_item in enumerate(files, 1):
            try:
                path, filename = _ensure_cached_link_file(file_item)
                archive.write(path, arcname=_unique_zip_name(filename, file_item, used_names))
            except HTTPException as exc:
                filename = _download_filename(file_item) or _safe_filename(
                    file_item.get("file_name") or f"attachment_{index}"
                )
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                errors.append(f"{filename}: {detail}")
                continue

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


def _run_sync_job(
    sources: list[str],
    disclosure_types: list[str],
    max_pages: int | None,
    fetch_e2_details: bool,
    trigger_mode: str,
    force: bool = False,
    skip_completeness_check: bool = False,
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
            force=force,
            skip_completeness_check=skip_completeness_check,
        )
        status = result.get("status", "failed")
        if status == "success" and not result.get("skipped"):
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
    return HTMLResponse(
        (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(
        _read_template("login.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return HTMLResponse(
        _read_template("admin.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> HTMLResponse:
    return HTMLResponse(
        (STATIC_DIR / "settings.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "auth_enabled": auth_enabled()}


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
def api_master_progress(master_id: int, episode: str = Query(default="")) -> dict[str, Any]:
    progress = store.get_progress(master_id, episode_key=episode or None)
    if progress is None:
        raise HTTPException(status_code=404, detail="未找到该项目。")
    return progress


@app.get("/api/master/{master_id}/timeline")
def api_master_timeline(master_id: int, episode: str = Query(default="")) -> dict[str, Any]:
    timeline = store.get_timeline(master_id, episode_key=episode or None)
    if timeline is None:
        raise HTTPException(status_code=404, detail="未找到该项目。")
    return timeline


@app.get("/api/search/facets")
def api_search_facets(types: list[str] = Query(default=[])) -> dict[str, Any]:
    selected_types = types or list(DISCLOSURE_TYPES)
    for disclosure_type in selected_types:
        if disclosure_type not in DISCLOSURE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown disclosure type: {disclosure_type}")
    return store.search_facets(disclosure_types=selected_types)


@app.get("/api/search")
def api_search(
    response: Response,
    q: str = Query(default=""),
    types: list[str] = Query(default=[]),
    limit: int = Query(default=80, ge=1, le=200),
    sort_by: str = Query(default="event_date", description="event_date | synced_at | type | project_name"),
    sort_order: str = Query(default="desc", description="asc | desc"),
    year: str = Query(default=""),
    district: str = Query(default=""),
    lifecycle_stage: str = Query(default=""),
    source: str = Query(default=""),
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
    if source and source not in {
        SOURCE_LINK_STHJ,
        SOURCE_E2_QYGK,
        SOURCE_DISTRICT_FENGXIAN,
        SOURCE_DISTRICT_MINHANG,
        SOURCE_DISTRICT_PUDONG,
        SOURCE_DISTRICT_SONGJIANG,
    }:
        raise HTTPException(status_code=400, detail=f"Unknown source: {source}")
    results = store.search(
        q,
        disclosure_types=selected_types,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        year=year or None,
        district=district or None,
        lifecycle_stage=lifecycle_stage or None,
        source=source or None,
    )
    return {
        "query": q,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "filters": {
            "year": year or None,
            "district": district or None,
            "lifecycle_stage": lifecycle_stage or None,
            "source": source or None,
        },
        "count": len(results),
        "results": results,
    }


def _parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _serialize_sync_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    stats: dict[str, Any] = {}
    raw_stats = item.pop("stats_json", None)
    if raw_stats:
        try:
            stats = json.loads(raw_stats) if isinstance(raw_stats, str) else dict(raw_stats)
        except (json.JSONDecodeError, TypeError):
            stats = {}
    item["stats"] = stats
    started = _parse_utc_timestamp(item.get("started_at"))
    finished = _parse_utc_timestamp(item.get("finished_at"))
    if started:
        end = finished or datetime.now(timezone.utc)
        item["elapsed_seconds"] = max(0, int((end - started).total_seconds()))
    return item


@app.get("/api/sync/status")
def api_sync_status() -> dict[str, Any]:
    latest = _serialize_sync_job(store.latest_sync_job())
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
def api_database_export(background_tasks: BackgroundTasks) -> FileResponse:
    if not store.db_path.exists():
        raise HTTPException(status_code=404, detail="当前没有可导出的数据库。")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fd, tmp_name = tempfile.mkstemp(prefix="sh_eia_export_", suffix=".zip")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        store.export_database_zip_to(tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(lambda p=tmp_path: p.unlink(missing_ok=True))
    return FileResponse(
        path=tmp_path,
        media_type="application/zip",
        filename=f"sh_eia_backup_{stamp}.zip",
    )


@app.post("/api/database/import")
async def api_database_import(
    file: UploadFile = File(...),
    mode: str = Form(default="merge"),
) -> dict[str, Any]:
    if sync_running:
        raise HTTPException(status_code=409, detail="同步进行中，请稍后再导入数据库。")
    mode_norm = (mode or "merge").strip().lower()
    if mode_norm not in {"merge", "replace"}:
        raise HTTPException(status_code=400, detail="mode 仅支持 merge 或 replace")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空。")
    try:
        if mode_norm == "replace":
            result = store.import_database_bytes(data, backup=True)
            message = "数据库已整库替换导入。"
        else:
            result = store.merge_database_bytes(data, backup=True)
            message = (
                f"增量合并完成：扫描 {result.get('scanned', 0)} 条，"
                f"新增 {result.get('inserted', 0)}，更新 {result.get('updated', 0)}，"
                f"跳过较旧 {result.get('skipped_older', 0)}。"
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_store()
    _set_update_check_state(
        checking=False,
        has_updates=False,
        checked_at=utc_now(),
        message=message,
        new_count=int(result.get("inserted", 0) or 0),
        details=result if mode_norm == "merge" else {},
        auto_sync_started=False,
        error="",
    )
    return {"status": "success", "message": message, **result}


@app.post("/api/sync")
def api_sync(request: SyncRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if sync_running:
        raise HTTPException(status_code=409, detail="已有同步任务正在运行，请稍后再试。")

    for source in request.sources:
        if source not in {
            SOURCE_LINK_STHJ,
            SOURCE_E2_QYGK,
            SOURCE_DISTRICT_FENGXIAN,
            SOURCE_DISTRICT_MINHANG,
            SOURCE_DISTRICT_PUDONG,
            SOURCE_DISTRICT_SONGJIANG,
        }:
            raise HTTPException(status_code=400, detail=f"Unknown source: {source}")

    for disclosure_type in request.disclosure_types:
        if disclosure_type not in DISCLOSURE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown disclosure type: {disclosure_type}")

    max_pages = None if request.full_sync else request.max_pages
    mode_label = "全量同步" if request.full_sync else f"每类 {max_pages} 页"
    source_labels: list[str] = []
    if SOURCE_LINK_STHJ in request.sources:
        source_labels.append("link 投用前")
    if SOURCE_E2_QYGK in request.sources:
        source_labels.append("e2 投用后")
    if SOURCE_DISTRICT_FENGXIAN in request.sources:
        source_labels.append("奉贤区")
    if SOURCE_DISTRICT_MINHANG in request.sources:
        source_labels.append("闵行区")
    if SOURCE_DISTRICT_SONGJIANG in request.sources:
        source_labels.append("松江区")
    if SOURCE_DISTRICT_PUDONG in request.sources:
        source_labels.append("浦东新区")
    scope = "、".join(source_labels) if source_labels else "全部来源"
    force_hint = "，强制重下" if request.force else ""
    background_tasks.add_task(
        _run_sync_job,
        request.sources,
        request.disclosure_types,
        max_pages,
        request.fetch_e2_details,
        "manual",
        request.force,
        request.skip_completeness_check,
    )
    check_hint = ""
    if request.full_sync and not request.force and not request.skip_completeness_check:
        check_hint = "（将先检查本地数据完整性，已完整则跳过下载）"
    return {
        "status": "accepted",
        "message": f"同步任务已开始（{mode_label}：{scope}{force_hint}）{check_hint}，可在下方状态面板查看进度。",
        "full_sync": request.full_sync,
        "max_pages": max_pages,
        "disclosure_types": request.disclosure_types,
        "sources": request.sources,
        "force": request.force,
    }


@app.get("/api/sync/completeness")
def api_sync_completeness(
    sources: list[str] = Query(default=[]),
    types: list[str] = Query(default=[]),
) -> dict[str, Any]:
    selected_sources = sources or [SOURCE_LINK_STHJ, SOURCE_E2_QYGK]
    selected_types = types or list(DISCLOSURE_TYPES)
    for source in selected_sources:
        if source not in {
            SOURCE_LINK_STHJ,
            SOURCE_E2_QYGK,
            SOURCE_DISTRICT_FENGXIAN,
            SOURCE_DISTRICT_MINHANG,
            SOURCE_DISTRICT_PUDONG,
            SOURCE_DISTRICT_SONGJIANG,
        }:
            raise HTTPException(status_code=400, detail=f"Unknown source: {source}")
    for disclosure_type in selected_types:
        if disclosure_type not in DISCLOSURE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown disclosure type: {disclosure_type}")
    return sync_service.check_completeness(sources=selected_sources, disclosure_types=selected_types)


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
        payload = start_captcha_session(
            external_id,
            event_external_id,
            detail_url=file_item.get("event_source_url"),
        )
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
    except Exception as exc:
        logger.exception("E2 captcha download failed")
        raise HTTPException(status_code=502, detail=f"下载失败：{exc}") from exc

    filename = _download_filename(file_item)
    fallback = f"e2_{request.file_id}.pdf"
    return StreamingResponse(
        BytesIO(body),
        media_type=content_type,
        headers={"Content-Disposition": _content_disposition(filename, fallback=fallback)},
    )


@app.get("/api/download/file/{file_id}")
def api_download_file_get(file_id: int) -> StreamingResponse:
    file_item = store.get_file_detail(file_id)
    if not file_item:
        raise HTTPException(status_code=404, detail="未找到文件。")
    return _file_download_response(file_item, fallback=f"attachment_{file_id}.pdf")


@app.post("/api/download/file")
def api_download_file(request: SingleFileDownloadRequest) -> StreamingResponse:
    file_item = store.get_file_detail(request.file_id)
    if not file_item:
        raise HTTPException(status_code=404, detail="未找到文件。")
    return _file_download_response(file_item, fallback=f"attachment_{request.file_id}.pdf")


@app.get("/api/download/zip")
def api_download_zip_get(ids: str = Query(..., description="Comma-separated file ids")) -> StreamingResponse:
    file_ids = [int(part.strip()) for part in ids.split(",") if part.strip()]
    if not file_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个文件。")
    return _zip_download_response(file_ids)


@app.post("/api/download/zip")
def api_download_zip(request: DownloadRequest) -> StreamingResponse:
    return _zip_download_response(request.file_ids)
