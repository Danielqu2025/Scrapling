"""Sync Songjiang district EIA lists into local eia.db."""

from __future__ import annotations

import logging
import time
from typing import Any

from scrapling.fetchers import FetcherSession

from db.store import EIAStore
from sources.district.songjiang import (
    PAGE_URL,
    REQUEST_TIMEOUT,
    fetch_list_page,
    page_sleep_seconds,
)
from sources.district.songjiang_adapter import songjiang_rows_to_events
from sources.types import DISCLOSURE_TYPES, DISTRICT_SONGJIANG_TYPES, SOURCE_DISTRICT_SONGJIANG

logger = logging.getLogger(__name__)


class SongjiangSyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def sync_type(
        self,
        disclosure_type: str,
        *,
        max_pages: int | None = None,
        page_size: int = 20,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
        resume: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, int]:
        if disclosure_type not in DISTRICT_SONGJIANG_TYPES:
            raise ValueError(f"Unsupported Songjiang type: {disclosure_type}")
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        pages_done = 0
        events_saved = 0
        files_saved = 0
        pages_skipped: list[int] = []

        start_page = 0
        if resume and max_pages is None and not force_refresh:
            last_page = self.store.get_max_synced_page(SOURCE_DISTRICT_SONGJIANG, disclosure_type)
            if last_page > 0:
                start_page = last_page
                logger.info("Songjiang resume %s from page %s", disclosure_type, start_page + 1)

        with FetcherSession(impersonate="chrome", timeout=REQUEST_TIMEOUT) as session:
            session.get(PAGE_URL, stealthy_headers=True)
            page_number = start_page
            total = 0
            pages_fetched = 0

            while True:
                if max_pages is not None and pages_fetched >= max_pages:
                    break

                page_no = page_number + 1
                try:
                    data = fetch_list_page(session, page_number=page_number, page_size=page_size)
                except Exception:  # noqa: BLE001
                    logger.exception("Songjiang sync skip page %s after retries", page_no)
                    pages_skipped.append(page_no)
                    page_number += 1
                    if total and page_number * page_size >= total:
                        break
                    if page_number - start_page > 500 and not total:
                        raise RuntimeError(f"松江区同步连续失败，已跳过 {len(pages_skipped)} 页") from None
                    continue

                records = data["records"]
                if not total:
                    total = int(data.get("total") or 0)
                total_pages = max(1, (total + page_size - 1) // page_size) if total else page_no
                display_total = total_pages if max_pages is None else min(total_pages, start_page + max_pages)

                logger.info(
                    "Songjiang sync %s page %s/%s (%s records, remote_total=%s)",
                    disclosure_type,
                    page_no,
                    display_total,
                    len(records),
                    total,
                )
                if job_id is not None:
                    progress = f"正在同步：松江区 · {label} 第 {page_no}/{display_total} 页"
                    if pages_skipped:
                        progress += f"（已跳过 {len(pages_skipped)} 页）"
                    if stats is not None:
                        stats["current"] = {
                            "source": SOURCE_DISTRICT_SONGJIANG,
                            "disclosure_type": disclosure_type,
                            "page": page_no,
                            "total_pages": display_total,
                            "remote_total": total,
                            "skipped_pages": pages_skipped[-5:],
                        }
                        self.store.update_sync_progress(job_id, progress, stats)
                    else:
                        self.store.update_sync_progress(job_id, progress)

                events = songjiang_rows_to_events(records, page_no=page_no)
                event_count, file_count = self.store.upsert_events(events, page_no=page_no)
                events_saved += event_count
                files_saved += file_count
                pages_done += 1
                pages_fetched += 1
                page_number += 1

                if not records:
                    break
                if total and page_number * page_size >= total:
                    break
                time.sleep(page_sleep_seconds())

        result = {
            "pages": pages_done,
            "events": events_saved,
            "projects": events_saved,
            "files": files_saved,
            "skipped_pages": len(pages_skipped),
            "resumed_from_page": start_page + 1 if start_page else None,
        }
        if pages_skipped:
            logger.warning(
                "Songjiang sync %s finished with %s skipped pages: %s",
                disclosure_type,
                len(pages_skipped),
                pages_skipped[:20],
            )
        return result

    def sync_all(
        self,
        disclosure_types: list[str] | None = None,
        *,
        max_pages: int | None = None,
        page_size: int = 20,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
        resume: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, dict[str, int]]:
        types = [t for t in (disclosure_types or DISTRICT_SONGJIANG_TYPES) if t in DISTRICT_SONGJIANG_TYPES]
        result: dict[str, dict[str, int]] = {}
        for disclosure_type in types:
            result[disclosure_type] = self.sync_type(
                disclosure_type,
                max_pages=max_pages,
                page_size=page_size,
                job_id=job_id,
                stats=stats,
                resume=resume,
                force_refresh=force_refresh,
            )
        return result
