"""Compare local database coverage against official list totals before full sync."""

from __future__ import annotations

import logging
from typing import Any

from _common import LIST_URLS, extract_total_pages, paginate_action, parse_list_records
from db.store import EIAStore
from sources.types import (
    DISCLOSURE_TYPES,
    DISTRICT_FENGXIAN_TYPES,
    DISTRICT_MINHANG_TYPES,
    DISTRICT_PUDONG_TYPES,
    DISTRICT_SONGJIANG_TYPES,
    E2_QYGK_TYPES,
    LINK_STHJ_TYPES,
    SOURCE_DISTRICT_FENGXIAN,
    SOURCE_DISTRICT_MINHANG,
    SOURCE_DISTRICT_PUDONG,
    SOURCE_DISTRICT_SONGJIANG,
    SOURCE_E2_QYGK,
    SOURCE_LINK_STHJ,
)

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
        details: dict[str, Any] = {"link": {}, "e2": {}, "fengxian": {}, "minhang": {}, "songjiang": {}, "pudong": {}}

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

        if SOURCE_DISTRICT_FENGXIAN in selected_sources:
            try:
                from sources.district.fengxian import remote_totals

                remote_map = remote_totals(page_size=1)
            except Exception as exc:  # noqa: BLE001
                logger.exception("fengxian remote totals failed")
                issues.append(f"奉贤区：无法读取官网总量（{exc}）")
                remote_map = {}
            for disclosure_type in [t for t in types if t in DISTRICT_FENGXIAN_TYPES]:
                label = f"奉贤区 · {DISCLOSURE_TYPES[disclosure_type]['label']}"
                local = self.store.get_source_type_coverage(SOURCE_DISTRICT_FENGXIAN, disclosure_type)
                remote_total = int(remote_map.get(disclosure_type) or 0)
                fx_issues: list[str] = []
                if remote_total and local["event_count"] + 5 < remote_total:
                    fx_issues.append(
                        f"{label}：本地 {local['event_count']} 条，官网约 {remote_total} 条"
                    )
                if remote_total == 0 and local["event_count"] == 0:
                    fx_issues.append(f"{label}：尚未同步")
                issues.extend(fx_issues)
                details["fengxian"][disclosure_type] = {
                    "label": label,
                    "remote_total": remote_total,
                    "local_event_count": local["event_count"],
                    "local_max_page": local["max_page"],
                    "issues": fx_issues,
                    "complete": not fx_issues,
                }

        if SOURCE_DISTRICT_MINHANG in selected_sources:
            try:
                from sources.district.minhang import remote_totals

                remote_map = remote_totals()
            except Exception as exc:  # noqa: BLE001
                logger.exception("minhang remote totals failed")
                issues.append(f"闵行区：无法读取官网总量（{exc}）")
                remote_map = {}
            for disclosure_type in [t for t in types if t in DISTRICT_MINHANG_TYPES]:
                label = f"闵行区 · {DISCLOSURE_TYPES[disclosure_type]['label']}"
                local = self.store.get_source_type_coverage(SOURCE_DISTRICT_MINHANG, disclosure_type)
                remote_info = remote_map.get(disclosure_type) or {}
                remote_total = int(remote_info.get("record_count") or 0)
                remote_pages = int(remote_info.get("page_count") or 0)
                mh_issues: list[str] = []
                if remote_total and local["event_count"] + 10 < remote_total:
                    mh_issues.append(
                        f"{label}：本地 {local['event_count']} 条，官网约 {remote_total} 条"
                    )
                if remote_pages and local["max_page"] < remote_pages:
                    mh_issues.append(
                        f"{label}：本地仅同步到第 {local['max_page']} 页，官网共 {remote_pages} 页"
                    )
                if remote_total == 0 and local["event_count"] == 0:
                    mh_issues.append(f"{label}：尚未同步")
                issues.extend(mh_issues)
                details["minhang"][disclosure_type] = {
                    "label": label,
                    "remote_total": remote_total,
                    "remote_pages": remote_pages,
                    "local_event_count": local["event_count"],
                    "local_max_page": local["max_page"],
                    "issues": mh_issues,
                    "complete": not mh_issues,
                }

        if SOURCE_DISTRICT_SONGJIANG in selected_sources:
            try:
                from sources.district.songjiang import remote_total as songjiang_remote_total

                remote_total_sj = songjiang_remote_total(page_size=1)
            except Exception as exc:  # noqa: BLE001
                logger.exception("songjiang remote total failed")
                issues.append(f"松江区：无法读取官网总量（{exc}）")
                remote_total_sj = 0
            for disclosure_type in [t for t in types if t in DISTRICT_SONGJIANG_TYPES]:
                label = f"松江区 · {DISCLOSURE_TYPES[disclosure_type]['label']}"
                local = self.store.get_source_type_coverage(SOURCE_DISTRICT_SONGJIANG, disclosure_type)
                sj_issues: list[str] = []
                if remote_total_sj and local["event_count"] + 5 < remote_total_sj:
                    sj_issues.append(
                        f"{label}：本地 {local['event_count']} 条，官网约 {remote_total_sj} 条"
                    )
                if remote_total_sj == 0 and local["event_count"] == 0:
                    sj_issues.append(f"{label}：尚未同步")
                issues.extend(sj_issues)
                details["songjiang"][disclosure_type] = {
                    "label": label,
                    "remote_total": remote_total_sj,
                    "local_event_count": local["event_count"],
                    "local_max_page": local["max_page"],
                    "issues": sj_issues,
                    "complete": not sj_issues,
                }

        if SOURCE_DISTRICT_PUDONG in selected_sources:
            try:
                from sources.district.pudong import remote_total as pudong_remote_total

                remote_total_pd = pudong_remote_total(page_size=1)
            except Exception as exc:  # noqa: BLE001
                logger.exception("pudong remote total failed")
                issues.append(f"浦东新区：无法读取官网总量（{exc}）")
                remote_total_pd = 0
            local_total = 0
            local_max_page = 0
            pd_issues: list[str] = []
            for disclosure_type in [t for t in types if t in DISTRICT_PUDONG_TYPES]:
                label = f"浦东新区 · {DISCLOSURE_TYPES[disclosure_type]['label']}"
                local = self.store.get_source_type_coverage(SOURCE_DISTRICT_PUDONG, disclosure_type)
                local_total += int(local["event_count"] or 0)
                local_max_page = max(local_max_page, int(local["max_page"] or 0))
                details["pudong"][disclosure_type] = {
                    "label": label,
                    "local_event_count": local["event_count"],
                    "local_max_page": local["max_page"],
                    "issues": [],
                    "complete": True,
                }
            if remote_total_pd and local_total + 10 < remote_total_pd:
                pd_issues.append(
                    f"浦东新区 · 环保审批公示：本地 {local_total} 条，官网约 {remote_total_pd} 条"
                )
            remote_pages = max(1, (remote_total_pd + 19) // 20) if remote_total_pd else 0
            if remote_pages and local_max_page < remote_pages:
                pd_issues.append(
                    f"浦东新区 · 环保审批公示：本地仅同步到第 {local_max_page} 页，官网共 {remote_pages} 页"
                )
            if remote_total_pd == 0 and local_total == 0:
                pd_issues.append("浦东新区 · 环保审批公示：尚未同步")
            issues.extend(pd_issues)
            for item in details["pudong"].values():
                item["issues"] = pd_issues
                item["complete"] = not pd_issues
                item["remote_total"] = remote_total_pd
                item["remote_pages"] = remote_pages

        complete = not issues
        link_complete = all(item.get("complete") for item in details["link"].values()) if details["link"] else True
        e2_complete = all(item.get("complete") for item in details["e2"].values()) if details["e2"] else True
        fengxian_complete = (
            all(item.get("complete") for item in details["fengxian"].values()) if details["fengxian"] else True
        )
        minhang_complete = (
            all(item.get("complete") for item in details["minhang"].values()) if details["minhang"] else True
        )
        songjiang_complete = (
            all(item.get("complete") for item in details["songjiang"].values()) if details["songjiang"] else True
        )
        pudong_complete = (
            all(item.get("complete") for item in details["pudong"].values()) if details["pudong"] else True
        )
        if complete:
            message = "本地数据与官网统计一致，无需全量下载。"
        else:
            message = f"发现 {len(issues)} 项差异，将执行全量重新下载。"
        return {
            "complete": complete,
            "link_complete": link_complete,
            "e2_complete": e2_complete,
            "fengxian_complete": fengxian_complete,
            "minhang_complete": minhang_complete,
            "songjiang_complete": songjiang_complete,
            "pudong_complete": pudong_complete,
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
