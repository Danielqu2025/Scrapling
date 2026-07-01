"""Sync post-construction disclosures from e2.sthj.sh.gov.cn."""

from __future__ import annotations

import logging
import time
from typing import Any

from db.store import EIAStore
from sources.e2_qygk.client import LIST_URL, fetch_detail_page, fetch_list_page
from sources.e2_qygk.parsers import extract_total_pages, parse_detail_page, parse_list_records
from sources.types import DISCLOSURE_TYPES

logger = logging.getLogger(__name__)


class E2SyncService:
    def __init__(self, store: EIAStore | None = None, detail_delay: float = 0.5) -> None:
        self.store = store or EIAStore()
        self.detail_delay = detail_delay

    def sync_type(
        self,
        disclosure_type: str = "post_construction",
        max_pages: int | None = None,
        fetch_details: bool = True,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        pages_done = 0
        events_saved = 0
        files_saved = 0
        details_fetched = 0
        page_no = 1
        total_pages = max_pages if max_pages is not None else 1

        while page_no <= total_pages:
            logger.info("E2 sync %s page %s/%s", disclosure_type, page_no, total_pages)
            if job_id is not None:
                progress = f"正在同步：{label} 第 {page_no}/{total_pages} 页"
                if stats is not None:
                    self.store.update_sync_progress(job_id, progress, stats)
                else:
                    self.store.update_sync_progress(job_id, progress)

            html = fetch_list_page(page_no)
            records = parse_list_records(html, source_url=LIST_URL)

            if fetch_details:
                enriched: list[dict] = []
                for record in records:
                    detail_html = fetch_detail_page(record["external_id"])
                    if detail_html:
                        record = parse_detail_page(detail_html, record)
                        details_fetched += 1
                    enriched.append(record)
                    time.sleep(self.detail_delay)
                records = enriched

            event_count, file_count = self.store.upsert_events(records, page_no=page_no)
            events_saved += event_count
            files_saved += file_count
            pages_done += 1

            if max_pages is None and page_no == 1:
                detected_total = extract_total_pages(html)
                if detected_total:
                    total_pages = detected_total
                    logger.info("Detected %s total pages for %s", detected_total, disclosure_type)
                else:
                    logger.warning("Could not detect total pages for %s", disclosure_type)

            page_no += 1

        return {
            "pages": pages_done,
            "events": events_saved,
            "files": files_saved,
            "details": details_fetched,
        }
