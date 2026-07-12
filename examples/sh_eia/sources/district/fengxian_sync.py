"""Sync Fengxian district EIA lists into local eia.db."""

from __future__ import annotations

import logging
from typing import Any

from scrapling.fetchers import FetcherSession

from db.store import EIAStore
from sources.district.fengxian import LIST_PATHS, iter_stage_pages, login
from sources.district.fengxian_adapter import fengxian_rows_to_events
from sources.types import DISCLOSURE_TYPES, DISTRICT_FENGXIAN_TYPES, SOURCE_DISTRICT_FENGXIAN

logger = logging.getLogger(__name__)


class FengxianSyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def sync_type(
        self,
        disclosure_type: str,
        *,
        max_pages: int | None = None,
        page_size: int = 50,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        if disclosure_type not in LIST_PATHS:
            raise ValueError(f"Unsupported Fengxian type: {disclosure_type}")
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        pages_done = 0
        events_saved = 0
        files_saved = 0

        with FetcherSession(impersonate="chrome", timeout=30) as session:
            token = login(session)
            for page_no, records, total in iter_stage_pages(
                session,
                token,
                disclosure_type,
                page_size=page_size,
                max_pages=max_pages,
            ):
                total_pages = max(1, (total + page_size - 1) // page_size) if total else page_no
                logger.info(
                    "Fengxian sync %s page %s/%s (%s records)",
                    disclosure_type,
                    page_no,
                    total_pages if max_pages is None else min(total_pages, max_pages),
                    len(records),
                )
                if job_id is not None:
                    progress = f"正在同步：奉贤区 · {label} 第 {page_no}/{total_pages} 页"
                    if stats is not None:
                        stats["current"] = {
                            "source": SOURCE_DISTRICT_FENGXIAN,
                            "disclosure_type": disclosure_type,
                            "page": page_no,
                            "total_pages": total_pages,
                            "remote_total": total,
                        }
                        self.store.update_sync_progress(job_id, progress, stats)
                    else:
                        self.store.update_sync_progress(job_id, progress)

                events = fengxian_rows_to_events(disclosure_type, records, page_no=page_no)
                event_count, file_count = self.store.upsert_events(events, page_no=page_no)
                events_saved += event_count
                files_saved += file_count
                pages_done += 1

        return {
            "pages": pages_done,
            "events": events_saved,
            "projects": events_saved,
            "files": files_saved,
        }

    def sync_all(
        self,
        disclosure_types: list[str] | None = None,
        *,
        max_pages: int | None = None,
        page_size: int = 50,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, int]]:
        types = [t for t in (disclosure_types or DISTRICT_FENGXIAN_TYPES) if t in LIST_PATHS]
        result: dict[str, dict[str, int]] = {}
        for disclosure_type in types:
            result[disclosure_type] = self.sync_type(
                disclosure_type,
                max_pages=max_pages,
                page_size=page_size,
                job_id=job_id,
                stats=stats,
            )
        return result
