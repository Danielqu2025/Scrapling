"""Sync Shanghai EIA disclosures from all configured sources."""

from __future__ import annotations

import logging
from typing import Any

from db.store import EIAStore
from sources.e2_qygk.sync import E2SyncService
from sources.link_sthj.sync import LinkSthjSyncService
from sources.types import DISCLOSURE_TYPES, E2_QYGK_TYPES, LINK_STHJ_TYPES, SOURCE_E2_QYGK, SOURCE_LINK_STHJ
from sync.completeness import CompletenessChecker

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, store: EIAStore | None = None) -> None:
        self.store = store or EIAStore()
        self.link_sync = LinkSthjSyncService(self.store)
        self.e2_sync = E2SyncService(self.store)
        self.completeness_checker = CompletenessChecker(self.store)

    def check_completeness(
        self,
        sources: list[str] | None = None,
        disclosure_types: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.completeness_checker.audit(sources=sources, disclosure_types=disclosure_types)

    def sync(
        self,
        sources: list[str] | None = None,
        disclosure_types: list[str] | None = None,
        max_pages: int | None = None,
        fetch_e2_details: bool = True,
        trigger_mode: str = "manual",
        force: bool = False,
        skip_completeness_check: bool = False,
    ) -> dict[str, Any]:
        selected_sources = sources or [SOURCE_LINK_STHJ, SOURCE_E2_QYGK]
        types = disclosure_types or list(DISCLOSURE_TYPES)
        job_id = self.store.start_sync_job(trigger_mode)
        stats: dict[str, Any] = {
            "sources": {},
            "types": {},
            "errors": [],
            "full_sync": max_pages is None,
            "skipped": False,
        }

        try:
            force_refresh = bool(force)
            sync_sources = list(selected_sources)
            if force:
                stats["force_refresh"] = True
            elif max_pages is None and not skip_completeness_check:
                self.store.update_sync_progress(job_id, "正在检查本地数据是否完整…", stats)
                audit = self.completeness_checker.audit(sources=selected_sources, disclosure_types=types)
                stats["completeness_audit"] = audit
                if audit["complete"]:
                    message = audit["message"]
                    stats["skipped"] = True
                    self.store.finish_sync_job(job_id, "success", message, stats)
                    return {
                        "job_id": job_id,
                        "status": "success",
                        "message": message,
                        "stats": stats,
                        "skipped": True,
                    }
                force_refresh = True
                stats["force_refresh"] = True
                sync_sources = []
                if SOURCE_LINK_STHJ in selected_sources and not audit.get("link_complete", False):
                    sync_sources.append(SOURCE_LINK_STHJ)
                if SOURCE_E2_QYGK in selected_sources and not audit.get("e2_complete", False):
                    sync_sources.append(SOURCE_E2_QYGK)
                if not sync_sources:
                    sync_sources = list(selected_sources)
                stats["sync_sources"] = sync_sources
                logger.info(
                    "Completeness check failed (%s issues), re-download sources: %s",
                    audit["issue_count"],
                    sync_sources,
                )

            if SOURCE_LINK_STHJ in sync_sources:
                link_types = [t for t in types if t in LINK_STHJ_TYPES]
                for disclosure_type in link_types:
                    label = DISCLOSURE_TYPES[disclosure_type]["label"]
                    self.store.update_sync_progress(job_id, f"正在同步：{label}", stats)
                    type_stats = self.link_sync.sync_type(
                        disclosure_type,
                        max_pages=max_pages,
                        job_id=job_id,
                        stats=stats,
                    )
                    stats["types"][disclosure_type] = type_stats

            if SOURCE_E2_QYGK in sync_sources:
                e2_types = [t for t in types if t in E2_QYGK_TYPES]
                for disclosure_type in e2_types:
                    label = DISCLOSURE_TYPES[disclosure_type]["label"]
                    self.store.update_sync_progress(job_id, f"正在同步：{label}", stats)
                    type_stats = self.e2_sync.sync_type(
                        disclosure_type,
                        max_pages=max_pages,
                        fetch_details=fetch_e2_details,
                        job_id=job_id,
                        stats=stats,
                        resume=not force_refresh,
                        force_refresh=force_refresh,
                    )
                    stats["types"][disclosure_type] = type_stats

            if stats.get("skipped"):
                message = stats.get("completeness_audit", {}).get("message", "已跳过同步")
            elif max_pages is None:
                pages_summary = ", ".join(
                    f"{DISCLOSURE_TYPES[key]['label']} {value.get('pages', 0)} 页"
                    for key, value in stats["types"].items()
                )
                message = f"全量同步完成（{pages_summary}）"
            else:
                message = f"同步完成（每类 {max_pages} 页）"
            self.store.finish_sync_job(job_id, "success", message, stats)
            return {"job_id": job_id, "status": "success", "message": message, "stats": stats, "skipped": False}
        except Exception as exc:
            logger.exception("Sync failed")
            stats["errors"].append(str(exc))
            message = f"同步失败: {exc}"
            self.store.finish_sync_job(job_id, "failed", message, stats)
            return {"job_id": job_id, "status": "failed", "message": message, "stats": stats, "skipped": False}
