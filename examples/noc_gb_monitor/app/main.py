"""FastAPI web UI for standard notice monitoring."""

from __future__ import annotations

import os
import sys
import threading
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


def _app_root() -> Path:
    if root := os.environ.get("NOC_GB_MONITOR_APP_ROOT"):
        return Path(root)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitor import (  # noqa: E402
    DOMAIN_KEYWORDS,
    TRACKED_INDUSTRY_CODES,
    collect_all,
    export_excel,
    standard_item_to_dict,
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
OUTPUT_DIR = ROOT / "output"

app = FastAPI(title="标准公告查询", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class QueryRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    sources: list[str] = Field(default_factory=lambda: ["gb", "hb", "db"])


def _get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="查询任务不存在或已过期。")
    return job


def _run_query_job(job_id: str, year: int, month: int, sources: list[str]) -> None:
    valid_sources = {"gb", "hb", "db"}
    selected = [s for s in sources if s in valid_sources]
    if not selected:
        with _jobs_lock:
            _jobs[job_id].update(
                {
                    "status": "failed",
                    "message": "请至少选择一种标准类型。",
                    "error": "no_sources",
                }
            )
        return

    try:
        gb_items, hb_items, db_items = collect_all(
            year,
            month,
            run_gb="gb" in selected,
            run_hb="hb" in selected,
            run_db="db" in selected,
        )
        items = [standard_item_to_dict(item) for item in gb_items + hb_items + db_items]
        with _jobs_lock:
            _jobs[job_id].update(
                {
                    "status": "done",
                    "message": "查询完成",
                    "counts": {
                        "gb": len(gb_items),
                        "hb": len(hb_items),
                        "db": len(db_items),
                        "total": len(items),
                    },
                    "items": items,
                    "gb_items": gb_items,
                    "hb_items": hb_items,
                    "db_items": db_items,
                    "year": year,
                    "month": month,
                }
            )
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id].update(
                {
                    "status": "failed",
                    "message": f"查询失败：{exc}",
                    "error": str(exc),
                }
            )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(
        (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    return {
        "domains": list(DOMAIN_KEYWORDS.keys()),
        "industry_codes": list(TRACKED_INDUSTRY_CODES),
        "sources": [
            {"id": "gb", "label": "国家标准", "desc": "按领域关键词筛选"},
            {"id": "hb", "label": "行业标准", "desc": "AQ / GA / HG / HJ"},
            {"id": "db", "label": "上海地标", "desc": "DB31 上海市地方标准"},
        ],
    }


@app.post("/api/query")
def api_query(request: QueryRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "message": f"正在查询 {request.year}-{request.month:02d} 的标准公告...",
            "year": request.year,
            "month": request.month,
            "sources": request.sources,
        }
    background_tasks.add_task(_run_query_job, job_id, request.year, request.month, request.sources)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/query/{job_id}")
def api_query_status(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    payload = {
        "job_id": job_id,
        "status": job["status"],
        "message": job.get("message", ""),
        "year": job.get("year"),
        "month": job.get("month"),
    }
    if job["status"] == "done":
        payload["counts"] = job.get("counts", {})
        payload["items"] = job.get("items", [])
    if job["status"] == "failed":
        payload["error"] = job.get("error", "")
    return payload


@app.get("/api/export/{job_id}")
def api_export(job_id: str) -> StreamingResponse:
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=400, detail="查询尚未完成，无法导出。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"std_notice_{job['year']}{job['month']:02d}.xlsx"
    output_path = OUTPUT_DIR / filename
    export_excel(job["gb_items"], job["hb_items"], job["db_items"], output_path)

    data = output_path.read_bytes()
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/search")
def api_search(
    job_id: str = Query(...),
    q: str = Query(default=""),
    std_type: str = Query(default=""),
    domain: str = Query(default=""),
) -> dict[str, Any]:
    job = _get_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=400, detail="查询尚未完成。")

    items: list[dict[str, Any]] = job.get("items", [])
    keyword = q.strip().lower()
    filtered = items
    if std_type:
        filtered = [item for item in filtered if item.get("std_type") == std_type]
    if domain:
        filtered = [item for item in filtered if domain in (item.get("matched_domains") or "")]
    if keyword:
        filtered = [
            item
            for item in filtered
            if keyword in (item.get("std_code") or "").lower()
            or keyword in (item.get("std_name") or "").lower()
            or keyword in (item.get("notice_title") or "").lower()
        ]
    return {"count": len(filtered), "items": filtered}
