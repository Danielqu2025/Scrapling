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
    SH_EIA_STARTUP_CHECK=1      # check official site on startup; set 0 to disable
    SH_EIA_STARTUP_CHECK_MODE=remind  # remind | auto | off
    SH_EIA_AUTH_ENABLED=0       # set 1 for public deploy (JWT + approval)
    SH_EIA_JWT_SECRET=...       # required when auth enabled
    SH_EIA_ADMIN_USERNAME=admin
    SH_EIA_ADMIN_PASSWORD=...

Public deploy: see DEPLOY-PUBLIC.md; or use run.sh / run.ps1 to load .env.
"""

from __future__ import annotations

import os

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from app.main import _run_sync_job, app
from sources.types import DISCLOSURE_TYPES, SOURCE_E2_QYGK, SOURCE_LINK_STHJ


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

    print(f"Open http://127.0.0.1:{port} in your browser.")
    if sync_hours > 0:
        print(f"Scheduled sync every {sync_hours} hour(s), max_pages={max_pages}.")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
