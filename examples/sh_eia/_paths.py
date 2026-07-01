"""Resolve writable data dir vs bundled resources for dev and portable builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_root() -> Path:
    if root := os.environ.get("SH_EIA_APP_ROOT"):
        return Path(root)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def get_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", get_app_root()))
    return get_app_root()


APP_ROOT = get_app_root()
BUNDLE_DIR = get_bundle_dir()
APP_DIR = BUNDLE_DIR / "app"
DATA_DIR = APP_ROOT / "data"
OUTPUT_DIR = APP_ROOT / "output"
DB_PATH = DATA_DIR / "eia.db"
