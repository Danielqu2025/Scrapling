"""Sync Minhang district EIA static lists into local eia.db."""

from __future__ import annotations

import logging
from typing import Any

from db.store import EIAStore
from sources.district.minhang import CHANNELS, page_count, parse_list_page
from sources.district.minhang_adapter import minhang_rows_to_events
from sources.types import DISCLOSURE_TYPES, DISTRICT_MINHANG_TYPES, SOURCE_DISTRICT_MINHANG

logger = logging.getLogger(__name__)


class MinhangSyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def sync_type(
        self,
        disclosure_type: str,
        *,
        max_pages: int | None = None,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        if disclosure_type not in CHANNELS:
            raise ValueError(f"Unsupported Minhang type: {disclosure_type}")
        channel = CHANNELS[disclosure_type]
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        total_pages = page_count(channel)
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        pages_done = 0
        events_saved = 0
        files_saved = 0

        # Pages are 0-based: list_0.htm … list_{n-1}.htm
        for page_index in range(total_pages):
            page_no = page_index + 1
            logger.info("Minhang sync %s page %s/%s", disclosure_type, page_no, total_pages)
            if job_id is not None:
                progress = f"正在同步：闵行区 · {label} 第 {page_no}/{total_pages} 页"
                if stats is not None:
                    stats["current"] = {
                        "source": SOURCE_DISTRICT_MINHANG,
                        "disclosure_type": disclosure_type,
                        "page": page_no,
                        "total_pages": total_pages,
                    }
                    self.store.update_sync_progress(job_id, progress, stats)
                else:
                    self.store.update_sync_progress(job_id, progress)

            rows = parse_list_page(disclosure_type, channel, page_index)
            events = minhang_rows_to_events(rows)
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
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, int]]:
        types = [t for t in (disclosure_types or DISTRICT_MINHANG_TYPES) if t in CHANNELS]
        result: dict[str, dict[str, int]] = {}
        for disclosure_type in types:
            result[disclosure_type] = self.sync_type(
                disclosure_type,
                max_pages=max_pages,
                job_id=job_id,
                stats=stats,
            )
        return result
