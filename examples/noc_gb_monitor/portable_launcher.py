"""
Portable launcher for 标准公告查询.

Development:
    python portable_launcher.py

After build:
    double-click NocGbMonitor.exe
"""

from __future__ import annotations

import os
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

    os.environ["NOC_GB_MONITOR_APP_ROOT"] = str(app_root)
    os.chdir(app_root)
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "output").mkdir(parents=True, exist_ok=True)

    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    if str(bundle_dir) not in sys.path:
        sys.path.insert(0, str(bundle_dir))
    return app_root


def _open_browser(port: int) -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}/")


def main() -> None:
    app_root = _prepare_runtime()
    port = int(os.getenv("NOC_GB_MONITOR_PORT", "8765"))
    host = os.getenv("NOC_GB_MONITOR_HOST", "127.0.0.1")

    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    print("=" * 50)
    print("标准公告查询")
    print(f"数据目录: {app_root / 'output'}")
    print(f"请在浏览器打开: http://127.0.0.1:{port}")
    print("关闭本窗口即可停止服务。")
    print("=" * 50)

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
