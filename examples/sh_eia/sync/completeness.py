"""Compare local database coverage against official list totals before full sync."""

from __future__ import annotations

import logging
from typing import Any

from _common import LIST_URLS, extract_total_pages, paginate_action, parse_list_records
from db.store import EIAStore
from sources.types import DISCLOSURE_TYPES, E2_QYGK_TYPES, LINK_STHJ_TYPES, SOURCE_E2_QYGK, SOURCE_LINK_STHJ

logger = logging.getLogger(__name__)

RECORDS_PER_PAGE = 10


class CompletenessChecker:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def audit(
        self,
        sources: list[str] | None = None,
        disclosure_types: list[str] | None = None,
    ) -> dict[str, Any]:
        selected_sources = sources or [SOURCE_LINK_STHJ, SOURCE_E2_QYGK]
        types = disclosure_types or list(DISCLOSURE_TYPES)
        issues: list[str] = []
        details: dict[str, Any] = {"link": {}, "e2": {}}

        if SOURCE_LINK_STHJ in selected_sources:
            for disclosure_type in [t for t in types if t in LINK_STHJ_TYPES]:
                label = DISCLOSURE_TYPES[disclosure_type]["label"]
                remote = self._fetch_link_remote_meta(disclosure_type)
                local = self.store.get_source_type_coverage(SOURCE_LINK_STHJ, disclosure_type)
                item = {
                    "label": label,
                    "remote_total_pages": remote["total_pages"],
                    "remote_min_records": remote["min_records"],
                    "local_event_count": local["event_count"],
                    "local_max_page": local["max_page"],
                    "complete": False,
                }
                type_issues = self._link_issues(label, remote, local)
                item["complete"] = not type_issues
                item["issues"] = type_issues
                issues.extend(type_issues)
                details["link"][disclosure_type] = item

        if SOURCE_E2_QYGK in selected_sources:
            e2_types = [t for t in types if t in E2_QYGK_TYPES]
            for disclosure_type in e2_types:
                label = DISCLOSURE_TYPES[disclosure_type]["label"]
                local = self.store.get_source_type_coverage(SOURCE_E2_QYGK, disclosure_type)
                incomplete_details = self.store.count_e2_incomplete_details()
                legacy_without_year = self.store.count_e2_without_list_year()

                e2_issues: list[str] = []
                if incomplete_details:
                    e2_issues.append(f"{label}：{incomplete_details} 条缺少详情页字段")
                if legacy_without_year:
                    e2_issues.append(f"{label}：{legacy_without_year} 条缺少 list_year 标记（旧同步数据）")
                if local["max_page"] <= 1 and local["event_count"] <= 20:
                    e2_issues.append(f"{label}：仅同步了首页样例数据（{local['event_count']} 条）")
                issues.extend(e2_issues)

                details["e2"][disclosure_type] = {
                    "label": label,
                    "local_event_count": local["event_count"],
                    "local_max_page": local["max_page"],
                    "incomplete_details": incomplete_details,
                    "legacy_without_list_year": legacy_without_year,
                    "issues": e2_issues,
                    "complete": not e2_issues,
                }

        complete = not issues
        link_complete = all(item.get("complete") for item in details["link"].values()) if details["link"] else True
        e2_complete = all(item.get("complete") for item in details["e2"].values()) if details["e2"] else True
        if complete:
            message = "本地数据与官网统计一致，无需全量下载。"
        else:
            message = f"发现 {len(issues)} 项差异，将执行全量重新下载。"
        return {
            "complete": complete,
            "link_complete": link_complete,
            "e2_complete": e2_complete,
            "issue_count": len(issues),
            "issues": issues,
            "message": message,
            "details": details,
        }

    @staticmethod
    def _link_issues(label: str, remote: dict[str, Any], local: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        remote_pages = remote["total_pages"]
        if remote_pages and local["max_page"] < remote_pages:
            issues.append(f"{label}：本地仅同步到第 {local['max_page']} 页，官网共 {remote_pages} 页")
        return issues

    def _fetch_link_remote_meta(self, disclosure_type: str) -> dict[str, Any]:
        from scrapling.fetchers import DynamicFetcher

        entry_url = LIST_URLS[disclosure_type]
        page = DynamicFetcher.fetch(
            entry_url,
            headless=True,
            google_search=False,
            network_idle=True,
            timeout=60_000,
            page_action=paginate_action(1),
            wait_selector="table.tab",
            wait_selector_state="visible",
            wait=1500,
        )
        total_pages = extract_total_pages(page) or 0
        first_page_count = len(parse_list_records(page, disclosure_type))
        min_records = 0
        if total_pages > 0:
            min_records = max(0, (total_pages - 1) * RECORDS_PER_PAGE + min(first_page_count, 1))
        return {
            "total_pages": total_pages,
            "first_page_count": first_page_count,
            "min_records": min_records,
        }
