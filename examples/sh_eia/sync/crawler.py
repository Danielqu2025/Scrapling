"""Sync Shanghai EIA disclosure pages into the local database."""

from __future__ import annotations

import logging
from typing import Any

from _common import DISCLOSURE_TYPES, LIST_URLS, extract_total_pages, paginate_action, parse_list_records
from db.store import EIAStore

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def sync(
        self,
        disclosure_types: list[str] | None = None,
        max_pages: int | None = None,
        trigger_mode: str = "manual",
    ) -> dict[str, Any]:
        types = disclosure_types or list(DISCLOSURE_TYPES)
        job_id = self.store.start_sync_job(trigger_mode)
        stats: dict[str, Any] = {"types": {}, "errors": [], "full_sync": max_pages is None}

        try:
            for disclosure_type in types:
                label = DISCLOSURE_TYPES[disclosure_type]["label"]
                self.store.update_sync_progress(job_id, f"正在同步：{label}", stats)
                type_stats = self._sync_type(disclosure_type, max_pages=max_pages, job_id=job_id, stats=stats)
                stats["types"][disclosure_type] = type_stats

            if max_pages is None:
                pages_summary = ", ".join(
                    f"{DISCLOSURE_TYPES[key]['label']} {value['pages']} 页"
                    for key, value in stats["types"].items()
                )
                message = f"全量同步完成（{pages_summary}）"
            else:
                message = f"同步完成（每类 {max_pages} 页）"
            self.store.finish_sync_job(job_id, "success", message, stats)
            return {"job_id": job_id, "status": "success", "message": message, "stats": stats}
        except Exception as exc:
            logger.exception("Sync failed")
            stats["errors"].append(str(exc))
            message = f"同步失败: {exc}"
            self.store.finish_sync_job(job_id, "failed", message, stats)
            return {"job_id": job_id, "status": "failed", "message": message, "stats": stats}

    def _sync_type(
        self,
        disclosure_type: str,
        max_pages: int | None = None,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        entry_url = LIST_URLS[disclosure_type]
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        pages_done = 0
        projects_saved = 0
        files_saved = 0
        page_no = 1
        total_pages = max_pages if max_pages is not None else 1

        while page_no <= total_pages:
            logger.info("Sync %s page %s/%s", disclosure_type, page_no, total_pages)
            if job_id is not None:
                progress = f"正在同步：{label} 第 {page_no}/{total_pages} 页"
                if stats is not None:
                    self.store.update_sync_progress(job_id, progress, stats)
                else:
                    self.store.update_sync_progress(job_id, progress)

            from scrapling.fetchers import DynamicFetcher

            page = DynamicFetcher.fetch(
                entry_url,
                headless=True,
                google_search=False,
                network_idle=True,
                timeout=60_000,
                page_action=paginate_action(page_no),
                wait_selector="table.tab",
                wait_selector_state="visible",
                wait=1500,
            )

            records = parse_list_records(page, disclosure_type)
            project_count, file_count = self.store.upsert_records(records, page_no=page_no)
            projects_saved += project_count
            files_saved += file_count
            pages_done += 1

            if max_pages is None and page_no == 1:
                detected_total = extract_total_pages(page)
                if detected_total:
                    total_pages = detected_total
                    logger.info("Detected %s total pages for %s", detected_total, disclosure_type)
                else:
                    logger.warning("Could not detect total pages for %s, stopping after page 1", disclosure_type)

            page_no += 1

        return {
            "pages": pages_done,
            "projects": projects_saved,
            "files": files_saved,
        }
