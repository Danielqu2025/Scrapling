"""Sync post-construction disclosures from e2.sthj.sh.gov.cn."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from db.store import EIAStore
from sources.e2_qygk.client import LIST_URL, fetch_detail_page, fetch_list_page
from sources.e2_qygk.parsers import extract_total_pages, parse_detail_page, parse_list_records
from sources.types import DISCLOSURE_TYPES, SOURCE_E2_QYGK

logger = logging.getLogger(__name__)

DEFAULT_SYNC_YEARS_BACK = 12


def _detail_is_complete(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("approval_number")
        or summary.get("approval_date")
        or summary.get("actual_start_date")
        or summary.get("debug_start_date")
        or summary.get("completion_date")
    )


def _sync_years() -> list[str]:
    current = datetime.now().year
    return [str(year) for year in range(current, current - DEFAULT_SYNC_YEARS_BACK, -1)]


class E2SyncService:
    def __init__(
        self,
        store: EIAStore | None = None,
        detail_delay: float = 0.5,
        page_delay: float = 1.0,
    ) -> None:
        self.store = store or EIAStore()
        self.detail_delay = detail_delay
        self.page_delay = page_delay

    def sync_type(
        self,
        disclosure_type: str = "post_construction",
        max_pages: int | None = None,
        fetch_details: bool = True,
        job_id: int | None = None,
        stats: dict[str, Any] | None = None,
        start_page: int | None = None,
        resume: bool = True,
        years: list[str] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, int]:
        label = DISCLOSURE_TYPES[disclosure_type]["label"]
        totals = {
            "pages": 0,
            "events": 0,
            "files": 0,
            "details": 0,
            "details_skipped": 0,
            "page_errors": 0,
            "years": {},
        }
        year_list = years if years is not None else ([""] if max_pages is not None else _sync_years())
        for year in year_list:
            year_label = year or "全部"
            logger.info("E2 sync %s year=%s", disclosure_type, year_label)
            year_stats = self._sync_year(
                disclosure_type=disclosure_type,
                label=label,
                year=year,
                max_pages=max_pages,
                fetch_details=fetch_details,
                job_id=job_id,
                stats=stats,
                start_page=start_page,
                resume=resume and not force_refresh,
                force_refresh=force_refresh,
            )
            totals["years"][year_label] = year_stats
            for key in ("pages", "events", "files", "details", "details_skipped", "page_errors"):
                totals[key] += year_stats.get(key, 0)
        return totals

    def _sync_year(
        self,
        *,
        disclosure_type: str,
        label: str,
        year: str,
        max_pages: int | None,
        fetch_details: bool,
        job_id: int | None,
        stats: dict[str, Any] | None,
        start_page: int | None,
        resume: bool,
        force_refresh: bool,
    ) -> dict[str, int]:
        pages_done = 0
        events_saved = 0
        files_saved = 0
        details_fetched = 0
        details_skipped = 0
        page_no = start_page or 1
        if resume and max_pages is None and start_page is None and year and not force_refresh:
            last_page = self.store.get_max_synced_page(SOURCE_E2_QYGK, disclosure_type)
            if last_page > 1:
                page_no = last_page
                logger.info("Resuming e2 sync year=%s from page %s", year or "default", page_no)

        total_pages = max_pages if max_pages is not None else max(page_no, 1)
        existing_ids = self.store.existing_external_ids(SOURCE_E2_QYGK, disclosure_type)
        detail_cache = self.store.e2_detail_summary_cache(SOURCE_E2_QYGK, disclosure_type)

        page_errors = 0

        while page_no <= total_pages:
            logger.info("E2 sync %s page %s/%s", disclosure_type, page_no, total_pages)
            if job_id is not None:
                progress = f"正在同步：{label} 第 {page_no}/{total_pages} 页"
                if stats is not None:
                    self.store.update_sync_progress(job_id, progress, stats)
                else:
                    self.store.update_sync_progress(job_id, progress)

            try:
                html = fetch_list_page(page_no, year=year)
            except Exception as exc:
                page_errors += 1
                logger.error("E2 list page %s failed after retries: %s", page_no, exc)
                if stats is not None:
                    stats.setdefault("page_errors", []).append({"page": page_no, "error": str(exc)})
                page_no += 1
                if page_no <= total_pages and self.page_delay > 0:
                    time.sleep(self.page_delay)
                continue

            records = parse_list_records(html, source_url=LIST_URL)
            if year:
                for record in records:
                    summary = dict(record.get("summary_json") or {})
                    summary["list_year"] = year
                    record["summary_json"] = summary

            if fetch_details:
                enriched: list[dict] = []
                for record in records:
                    external_id = record["external_id"]
                    cached_summary = detail_cache.get(external_id, {})
                    if (
                        not force_refresh
                        and external_id in existing_ids
                        and _detail_is_complete(cached_summary)
                    ):
                        details_skipped += 1
                        if cached_summary:
                            record["summary_json"] = {**record.get("summary_json", {}), **cached_summary}
                        enriched.append(record)
                        continue

                    detail_html = fetch_detail_page(external_id)
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
            for record in records:
                existing_ids.add(record["external_id"])
                summary = record.get("summary_json")
                if isinstance(summary, dict):
                    detail_cache[record["external_id"]] = summary

            if max_pages is None:
                detected_total = extract_total_pages(html)
                if detected_total and detected_total > total_pages:
                    total_pages = detected_total
                    logger.info("Detected %s total pages for %s", detected_total, disclosure_type)
                elif page_no == 1 and not detected_total:
                    logger.warning("Could not detect total pages for %s", disclosure_type)

            page_no += 1
            if page_no <= total_pages and self.page_delay > 0:
                time.sleep(self.page_delay)

        return {
            "pages": pages_done,
            "events": events_saved,
            "files": files_saved,
            "details": details_fetched,
            "details_skipped": details_skipped,
            "page_errors": page_errors,
        }
