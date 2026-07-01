"""
Portable launcher for the Shanghai EIA web app.

Usage (development):
    python portable_launcher.py

After PyInstaller build:
    double-click ShEIA.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _prepare_runtime() -> Path:
    if getattr(sys, "frozen", False):
        app_root = Path(sys.executable).parent
        bundle_dir = Path(getattr(sys, "_MEIPASS", app_root))
    else:
        app_root = Path(__file__).resolve().parent
        bundle_dir = app_root

    os.environ["SH_EIA_APP_ROOT"] = str(app_root)
    os.chdir(app_root)
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "data").mkdir(parents=True, exist_ok=True)
    (app_root / "output").mkdir(parents=True, exist_ok=True)

    browsers_dir = app_root / "browsers"
    if browsers_dir.is_dir():
        for pattern in ("chromium-*", "chromium_headless_shell-*"):
            if any(browsers_dir.glob(pattern)):
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir.resolve())
                break

    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    if str(bundle_dir) not in sys.path:
        sys.path.insert(0, str(bundle_dir))
    return app_root


def _bundled_browser_ready(app_root: Path) -> bool:
    browsers_dir = app_root / "browsers"
    if not browsers_dir.is_dir():
        return False
    for pattern in ("chromium-*", "chromium_headless_shell-*"):
        for folder in browsers_dir.glob(pattern):
            exe = folder / "chrome-win64" / "chrome.exe"
            if not exe.exists():
                exe = folder / "chrome-win" / "chrome.exe"
            if exe.exists():
                return True
    return False


def _ensure_playwright_browser() -> None:
    app_root = Path(os.environ["SH_EIA_APP_ROOT"])
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and _bundled_browser_ready(app_root):
        print("已检测到内置浏览器，可直接同步官网。")
        return

    marker = app_root / "data" / ".playwright_ready"
    if marker.exists():
        return
    print("首次使用同步功能需要下载浏览器组件，请稍候...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        marker.write_text("ok", encoding="utf-8")
        print("浏览器组件已就绪。")
    except Exception as exc:
        print(f"浏览器组件安装失败: {exc}")
        print("仍可搜索和导入已有数据库；需要同步时请稍后重试。")


def _open_browser(port: int) -> None:
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{port}/")


def main() -> None:
    app_root = _prepare_runtime()
    port = int(os.getenv("SH_EIA_PORT", "8080"))
    host = os.getenv("SH_EIA_HOST", "127.0.0.1")

    if os.getenv("SH_EIA_SKIP_BROWSER_INSTALL", "0") not in {"1", "true", "yes"}:
        threading.Thread(target=_ensure_playwright_browser, daemon=True).start()

    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    print("=" * 50)
    print("上海环评资料检索")
    print(f"数据目录: {app_root / 'data'}")
    print(f"请在浏览器打开: http://127.0.0.1:{port}")
    print("关闭本窗口即可停止服务。")
    print("=" * 50)

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
