"""Check official sources for disclosures not yet in the local database."""

from __future__ import annotations

import logging
from typing import Any

from _common import LIST_URLS, paginate_action, parse_list_records
from db.store import EIAStore
from sources.e2_qygk.client import fetch_list_page
from sources.e2_qygk.parsers import parse_list_records as parse_e2_list_records
from sources.types import DISCLOSURE_TYPES, E2_QYGK_TYPES, LINK_STHJ_TYPES, SOURCE_E2_QYGK, SOURCE_LINK_STHJ

logger = logging.getLogger(__name__)


class UpdateChecker:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()

    def check(
        self,
        sources: list[str] | None = None,
        disclosure_types: list[str] | None = None,
    ) -> dict[str, Any]:
        selected_sources = sources or [SOURCE_LINK_STHJ, SOURCE_E2_QYGK]
        types = disclosure_types or list(DISCLOSURE_TYPES)
        details: dict[str, Any] = {}
        all_new: list[dict] = []

        if SOURCE_LINK_STHJ in selected_sources:
            for disclosure_type in [t for t in types if t in LINK_STHJ_TYPES]:
                label = DISCLOSURE_TYPES[disclosure_type]["label"]
                records = self._fetch_link_first_page(disclosure_type)
                new_records = self.store.filter_new_records(records)
                details[disclosure_type] = {
                    "label": label,
                    "source": SOURCE_LINK_STHJ,
                    "checked": len(records),
                    "new": len(new_records),
                    "samples": [item["project_name"] for item in new_records[:3]],
                }
                all_new.extend(new_records)

        if SOURCE_E2_QYGK in selected_sources:
            for disclosure_type in [t for t in types if t in E2_QYGK_TYPES]:
                label = DISCLOSURE_TYPES[disclosure_type]["label"]
                records = self._fetch_e2_first_page()
                new_records = self.store.filter_new_events(records)
                details[disclosure_type] = {
                    "label": label,
                    "source": SOURCE_E2_QYGK,
                    "checked": len(records),
                    "new": len(new_records),
                    "samples": [item["project_name"] for item in new_records[:3]],
                }
                all_new.extend(new_records)

        new_count = len(all_new)
        has_updates = new_count > 0
        if not has_updates:
            message = "官网暂无新公示，本地库已是最新。"
        else:
            parts = [f"{info['label']} {info['new']} 条" for info in details.values() if info["new"] > 0]
            message = f"发现 {new_count} 条新公示（{', '.join(parts)}）"

        return {
            "has_updates": has_updates,
            "new_count": new_count,
            "message": message,
            "details": details,
        }

    def _fetch_link_first_page(self, disclosure_type: str) -> list[dict]:
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
        return parse_list_records(page, disclosure_type)

    def _fetch_e2_first_page(self) -> list[dict]:
        html = fetch_list_page(1)
        return parse_e2_list_records(html)
