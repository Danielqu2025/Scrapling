"""
Example 4: Team web app - search, sync, and download Shanghai EIA disclosures.

Documentation: README.md in this directory; portable packaging: ../README.md

Run from examples/sh_eia:

    pip install -r requirements-app.txt
    python 04_run_server.py

Optional environment variables:
    SH_EIA_HOST=0.0.0.0
    SH_EIA_PORT=8080
    SH_EIA_SYNC_HOURS=24        # periodic sync interval; set 0 to disable
    SH_EIA_SYNC_MAX_PAGES=1     # pages per type for scheduled sync
    SH_EIA_WEEKLY_SYNC=1        # weekly random weekday sync; set 0 to disable
    SH_EIA_WEEKLY_MAX_PAGES=2   # pages per type for the weekly sync (incremental)
    SH_EIA_STARTUP_CHECK=1      # check official site on startup; set 0 to disable
    SH_EIA_STARTUP_CHECK_MODE=remind  # remind | auto | off
    SH_EIA_AUTH_ENABLED=0       # set 1 for public deploy (JWT + approval)
    SH_EIA_JWT_SECRET=...       # required when auth enabled
    SH_EIA_ADMIN_USERNAME=admin
    SH_EIA_ADMIN_PASSWORD=...

Public deploy: see DEPLOY-PUBLIC.md; or use run.sh / run.ps1 to load .env.
"""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from app.main import _run_sync_job, app
from sources.types import (
    DISCLOSURE_TYPES,
    SOURCE_DISTRICT_FENGXIAN,
    SOURCE_DISTRICT_MINHANG,
    SOURCE_DISTRICT_PUDONG,
    SOURCE_DISTRICT_SONGJIANG,
    SOURCE_E2_QYGK,
    SOURCE_LINK_STHJ,
)

logger = logging.getLogger(__name__)

# 全来源：投用前 + 投用后 + 各区级源
ALL_SOURCES = [
    SOURCE_LINK_STHJ,
    SOURCE_E2_QYGK,
    SOURCE_DISTRICT_FENGXIAN,
    SOURCE_DISTRICT_MINHANG,
    SOURCE_DISTRICT_PUDONG,
    SOURCE_DISTRICT_SONGJIANG,
]


def _next_random_weekday_run() -> datetime:
    """计算下一周内一个随机工作日（周一~周五）的随机工作时间（上海时区 9:00–18:00）。

    服务器时区为 UTC，APScheduler 默认按本地（UTC）解释 run_date，
    因此在此用 Asia/Shanghai 生成随机时间后转换为 UTC 再传入，避免偏移。
    """
    from datetime import timezone
    from zoneinfo import ZoneInfo

    tz_sh = ZoneInfo("Asia/Shanghai")
    now_sh = datetime.now(tz_sh)
    # 从明天起 1~7 天内随机挑一天
    base = now_sh + timedelta(days=random.randint(1, 7))
    # 若落在周末则顺延到周一（仅跳过周六/周日，不处理法定节假日）
    while base.weekday() >= 5:  # 5=Sat, 6=Sun
        base += timedelta(days=1)
    # 工作时间 9:00–18:00 之间随机一个时刻（精确到分钟）
    hour = random.randint(9, 17)
    minute = random.randint(0, 59)
    run_sh = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # 转为 UTC naive 传给 APScheduler（调度器时区为 UTC）
    return run_sh.astimezone(timezone.utc).replace(tzinfo=None)


def _weekly_random_sync_job(scheduler: BackgroundScheduler) -> None:
    """执行一次每周增量同步，并安排下一周的随机任务（自循环）。"""
    max_pages = int(os.getenv("SH_EIA_WEEKLY_MAX_PAGES", "2"))
    try:
        logger.info("Weekly random sync starting (incremental, max_pages=%s)", max_pages)
        _run_sync_job(
            ALL_SOURCES,
            list(DISCLOSURE_TYPES),
            max_pages,
            True,
            "schedule",
        )
    except Exception as exc:  # 单次失败不影响后续排程
        logger.exception("Weekly random sync failed: %s", exc)
    finally:
        # 安排下一周的随机任务
        _schedule_weekly_random(scheduler)


def _schedule_weekly_random(scheduler: BackgroundScheduler) -> None:
    run_at = _next_random_weekday_run()
    scheduler.add_job(
        lambda: _weekly_random_sync_job(scheduler),
        trigger="date",
        run_date=run_at,
        id="sh_eia_weekly_random",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Next weekly random sync scheduled at %s", run_at.isoformat())


def main() -> None:
    host = os.getenv("SH_EIA_HOST", "0.0.0.0")
    port = int(os.getenv("SH_EIA_PORT", "8080"))
    sync_hours = float(os.getenv("SH_EIA_SYNC_HOURS", "0"))
    sync_max_pages = os.getenv("SH_EIA_SYNC_MAX_PAGES", "1")
    max_pages = None if sync_max_pages.lower() in {"all", "none", "null", "full"} else int(sync_max_pages)

    scheduler = BackgroundScheduler()
    if sync_hours > 0:
        scheduler.add_job(
            lambda: _run_sync_job(
                [SOURCE_LINK_STHJ, SOURCE_E2_QYGK],
                list(DISCLOSURE_TYPES),
                max_pages,
                max_pages == 1,
                "schedule",
            ),
            trigger="interval",
            hours=sync_hours,
            id="sh_eia_periodic_sync",
            replace_existing=True,
        )
        scheduler.start()

    # 每周随机工作日工作时间增量同步（默认开启）
    if os.getenv("SH_EIA_WEEKLY_SYNC", "1").lower() not in {"0", "false", "off", "no"}:
        _schedule_weekly_random(scheduler)
        scheduler.start()

    print(f"Open http://127.0.0.1:{port} in your browser.")
    if sync_hours > 0:
        print(f"Scheduled sync every {sync_hours} hour(s), max_pages={max_pages}.")
    print("Weekly random weekday sync: enabled.")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
