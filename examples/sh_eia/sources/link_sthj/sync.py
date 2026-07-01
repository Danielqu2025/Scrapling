"""Sync link.sthj.sh.gov.cn disclosure lists."""

from __future__ import annotations

import logging
from typing import Any

from _common import LIST_URLS, extract_total_pages, paginate_action, parse_list_records
from db.store import EIAStore
from sources.types import DISCLOSURE_TYPES

logger = logging.getLogger(__name__)


class LinkSthjSyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def sync_type(
        self,
        disclosure_type: str,
        max_pages: int | None = None,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        entry_url = LIST_URLS[disclosure_type]
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        pages_done = 0
        events_saved = 0
        files_saved = 0
        page_no = 1
        total_pages = max_pages if max_pages is not None else 1

        while page_no <= total_pages:
            logger.info("Link sync %s page %s/%s", disclosure_type, page_no, total_pages)
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
            event_count, file_count = self.store.upsert_records(records, page_no=page_no)
            events_saved += event_count
            files_saved += file_count
            pages_done += 1

            if max_pages is None and page_no == 1:
                detected_total = extract_total_pages(page)
                if detected_total:
                    total_pages = detected_total
                else:
                    logger.warning("Could not detect total pages for %s", disclosure_type)

            page_no += 1

        return {
            "pages": pages_done,
            "events": events_saved,
            "projects": events_saved,
            "files": files_saved,
        }
