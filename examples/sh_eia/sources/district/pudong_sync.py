"""Sync Pudong district EIA lists into local eia.db."""

from __future__ import annotations

import logging
from typing import Any

from scrapling.fetchers import FetcherSession

from db.store import EIAStore
from sources.district.pudong import DEFAULT_PAGE_SIZE, INDEX_URL, REQUEST_TIMEOUT, iter_pages, page_sleep_seconds
from sources.district.pudong_adapter import pudong_rows_to_events
from sources.types import DISCLOSURE_TYPES, DISTRICT_PUDONG_TYPES, SOURCE_DISTRICT_PUDONG

logger = logging.getLogger(__name__)


class PudongSyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def _sync_pages(
        self,
        *,
        disclosure_types: list[str] | None,
        max_pages: int | None,
        page_size: int,
        job_id: int | None,
        stats: dict[str, Any] | None,
        resume: bool,
        force_refresh: bool,
    ) -> dict[str, Any]:
        allowed_types = set(disclosure_types or DISTRICT_PUDONG_TYPES)
        type_stats: dict[str, dict[str, int]] = {key: {"pages": 0, "events": 0, "projects": 0, "files": 0} for key in allowed_types}
        pages_done = 0
        events_saved = 0
        files_saved = 0
        pages_skipped: list[int] = []

        start_page = 1
        if resume and max_pages is None and not force_refresh:
            last_pages = [
                self.store.get_max_synced_page(SOURCE_DISTRICT_PUDONG, disclosure_type)
                for disclosure_type in allowed_types
            ]
            last_page = max(last_pages) if last_pages else 0
            if last_page > 0:
                start_page = last_page
                logger.info("Pudong resume from page %s", start_page)

        with FetcherSession(impersonate="chrome", timeout=REQUEST_TIMEOUT) as session:
            session.get(INDEX_URL, stealthy_headers=True)
            total = 0
            for page_no, records, remote_total in iter_pages(
                session,
                page_size=page_size,
                max_pages=max_pages,
                start_page=start_page,
            ):
                if not total:
                    total = remote_total
                total_pages = max(1, (total + page_size - 1) // page_size) if total else page_no
                display_total = total_pages if max_pages is None else min(total_pages, start_page + max_pages - 1)

                try:
                    events = pudong_rows_to_events(records, page_no=page_no)
                    events = [event for event in events if event["disclosure_type"] in allowed_types]
                except Exception:  # noqa: BLE001
                    logger.exception("Pudong sync skip page %s", page_no)
                    pages_skipped.append(page_no)
                    continue

                logger.info(
                    "Pudong sync page %s/%s (%s records, remote_total=%s, saved=%s)",
                    page_no,
                    display_total,
                    len(records),
                    total,
                    len(events),
                )
                if job_id is not None:
                    progress = f"正在同步：浦东新区 · 环保审批公示 第 {page_no}/{display_total} 页"
                    if pages_skipped:
                        progress += f"（已跳过 {len(pages_skipped)} 页）"
                    if stats is not None:
                        stats["current"] = {
                            "source": SOURCE_DISTRICT_PUDONG,
                            "disclosure_type": "mixed",
                            "page": page_no,
                            "total_pages": display_total,
                            "remote_total": total,
                            "skipped_pages": pages_skipped[-5:],
                        }
                        self.store.update_sync_progress(job_id, progress, stats)
                    else:
                        self.store.update_sync_progress(job_id, progress)

                event_count, file_count = self.store.upsert_events(events, page_no=page_no)
                events_saved += event_count
                files_saved += file_count
                pages_done += 1
                for event in events:
                    bucket = type_stats.setdefault(
                        event["disclosure_type"],
                        {"pages": 0, "events": 0, "projects": 0, "files": 0},
                    )
                    bucket["events"] += 1
                    bucket["projects"] += 1
                    bucket["files"] += len(event.get("files") or [])
                for bucket in type_stats.values():
                    bucket["pages"] = pages_done

        result = {
            "pages": pages_done,
            "events": events_saved,
            "projects": events_saved,
            "files": files_saved,
            "skipped_pages": len(pages_skipped),
            "resumed_from_page": start_page if start_page > 1 else None,
            "by_type": type_stats,
        }
        if pages_skipped:
            logger.warning("Pudong sync finished with %s skipped pages: %s", len(pages_skipped), pages_skipped[:20])
        return result

    def sync_type(
        self,
        disclosure_type: str,
        *,
        max_pages: int | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
        resume: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, int]:
        if disclosure_type not in DISTRICT_PUDONG_TYPES:
            raise ValueError(f"Unsupported Pudong type: {disclosure_type}")
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        result = self._sync_pages(
            disclosure_types=[disclosure_type],
            max_pages=max_pages,
            page_size=page_size,
            job_id=job_id,
            stats=stats,
            resume=resume,
            force_refresh=force_refresh,
        )
        by_type = result.get("by_type") or {}
        type_result = by_type.get(
            disclosure_type,
            {"pages": result["pages"], "events": 0, "projects": 0, "files": 0},
        )
        logger.info("Pudong sync %s (%s) done: %s events", disclosure_type, label, type_result.get("events", 0))
        return {
            "pages": int(result["pages"]),
            "events": int(type_result.get("events") or 0),
            "projects": int(type_result.get("projects") or 0),
            "files": int(type_result.get("files") or 0),
            "skipped_pages": int(result.get("skipped_pages") or 0),
            "resumed_from_page": result.get("resumed_from_page"),
        }

    def sync_all(
        self,
        disclosure_types: list[str] | None = None,
        *,
        max_pages: int | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
        resume: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, dict[str, int]]:
        types = [t for t in (disclosure_types or DISTRICT_PUDONG_TYPES) if t in DISTRICT_PUDONG_TYPES]
        result = self._sync_pages(
            disclosure_types=types,
            max_pages=max_pages,
            page_size=page_size,
            job_id=job_id,
            stats=stats,
            resume=resume,
            force_refresh=force_refresh,
        )
        by_type = dict(result.get("by_type") or {})
        for disclosure_type in types:
            by_type.setdefault(disclosure_type, {"pages": result["pages"], "events": 0, "projects": 0, "files": 0})
        return by_type
