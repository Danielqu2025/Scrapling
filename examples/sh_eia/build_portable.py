"""
Build a portable Windows package for the Shanghai EIA app.

Run from examples/sh_eia (after installing Scrapling and app deps):

    pip install -r requirements-app.txt
    pip install -r requirements-portable.txt
    python build_portable.py

Output:
    dist/ShEIA_portable/ShEIA.exe
    dist/ShEIA_portable.zip
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
DIST = ROOT / "dist"
PLAYWRIGHT_CACHE = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"


def _sqlite_binaries() -> list[str]:
    sep = ";" if sys.platform.startswith("win") else ":"
    dlls_dir = Path(sysconfig.get_config_var("base")) / "DLLs"
    args: list[str] = []
    for name in ("_sqlite3.pyd", "sqlite3.dll"):
        binary = dlls_dir / name
        if binary.exists():
            args.extend(["--add-binary", f"{binary}{sep}."])
    return args


def _default_browser_folders() -> list[str]:
    import playwright

    browsers_json = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    data = json.loads(browsers_json.read_text(encoding="utf-8"))
    folders: list[str] = []
    for item in data["browsers"]:
        if not item.get("installByDefault"):
            continue
        name = item["name"]
        revision = item["revision"]
        if name == "chromium":
            folders.append(f"chromium-{revision}")
        elif name == "chromium-headless-shell":
            folders.append(f"chromium_headless_shell-{revision}")
    return folders


def _ensure_playwright_browsers_installed() -> None:
    missing = [name for name in _default_browser_folders() if not (PLAYWRIGHT_CACHE / name).exists()]
    if not missing:
        return
    print("本机尚未安装 Playwright 浏览器，正在下载（打包用）...")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


def _bundle_browsers(package_dir: Path) -> int:
    _ensure_playwright_browsers_installed()
    dest = package_dir / "browsers"
    dest.mkdir(parents=True, exist_ok=True)
    copied_mb = 0.0
    for folder in _default_browser_folders():
        src = PLAYWRIGHT_CACHE / folder
        if not src.exists():
            raise SystemExit(f"缺少浏览器目录: {src}\n请先运行: python -m playwright install chromium")
        target = dest / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        copied_mb += size / (1024 * 1024)
        print(f"  已打包浏览器: {folder} ({size / (1024 * 1024):.0f} MB)")
    return int(copied_mb)


def _pyinstaller_cmd() -> list[str]:
    sep = ";" if sys.platform.startswith("win") else ":"
    static_src = ROOT / "app" / "static"
    add_data = f"{static_src}{sep}app/static"

    return [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "ShEIA",
        "--paths",
        str(ROOT),
        "--paths",
        str(REPO_ROOT),
        "--add-data",
        add_data,
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan.on",
        "--hidden-import",
        "uvicorn.lifespan.off",
        "--hidden-import",
        "email.mime.multipart",
        "--hidden-import",
        "email.mime.text",
        "--hidden-import",
        "sqlite3",
        "--hidden-import",
        "_sqlite3",
        "--hidden-import",
        "apscheduler",
        "--hidden-import",
        "multipart",
        "--collect-all",
        "scrapling",
        "--collect-all",
        "playwright",
        "--collect-all",
        "browserforge",
        "--collect-all",
        "apify_fingerprint_datapoints",
        "--collect-all",
        "lxml",
        "--collect-all",
        "orjson",
        *_sqlite_binaries(),
        str(ROOT / "portable_launcher.py"),
    ]


def main() -> None:
    bundle_browsers = os.getenv("SH_EIA_BUNDLE_BROWSERS", "1").lower() not in {"0", "false", "no", "off"}

    if shutil.which("pyinstaller") is None:
        raise SystemExit("PyInstaller 未安装。请先运行: pip install -r requirements-portable.txt")

    static_src = ROOT / "app" / "static"
    if not static_src.exists():
        raise SystemExit(f"缺少静态资源目录: {static_src}")

    print("正在打包，可能需要几分钟...")
    subprocess.run(_pyinstaller_cmd(), cwd=ROOT, check=True)

    exe_name = "ShEIA.exe" if sys.platform.startswith("win") else "ShEIA"
    build_dir = DIST / "ShEIA"
    built_exe = build_dir / exe_name
    if not built_exe.exists():
        raise SystemExit(f"未找到输出文件: {built_exe}")

    package_dir = DIST / "ShEIA_portable"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(build_dir, package_dir)
    (package_dir / "data").mkdir(exist_ok=True)

    source_db = ROOT / "data" / "eia.db"
    if source_db.exists():
        shutil.copy2(source_db, package_dir / "data" / "eia.db")

    browser_mb = 0
    if bundle_browsers:
        print("\n正在打包内置浏览器（体积较大，请耐心等待）...")
        browser_mb = _bundle_browsers(package_dir)
    else:
        print("\n已跳过浏览器打包（SH_EIA_BUNDLE_BROWSERS=0）。")

    readme = package_dir / "使用说明.txt"
    browser_note = (
        f"已内置浏览器（约 {browser_mb} MB），同步官网无需再下载。"
        if bundle_browsers
        else "未内置浏览器；首次同步会自动下载 Chromium（约 200MB）。"
    )
    readme.write_text(
        f"""上海环评资料检索 - 便携版

【使用】
1. 双击 ShEIA.exe
2. 浏览器会自动打开；若未打开，请访问 http://127.0.0.1:8080
3. 关闭黑色命令行窗口即可退出

【数据】
- 数据库保存在 data 文件夹（eia.db）
- 页面中可「导出数据库」分享给同事
- 同事用「导入数据库」即可使用，无需重新抓取

【同步官网】
- {browser_note}
- 需要能访问上海市生态环境局官网

【分享给别人】
方式一：压缩整个 ShEIA_portable 文件夹发送
方式二：发送程序目录 + 导出的 sh_eia_backup_xxx.zip
""",
        encoding="utf-8",
    )

    zip_path = DIST / "ShEIA_portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            archive.write(path, Path("ShEIA_portable") / path.relative_to(package_dir))

    print("\n完成:")
    print(f"  程序目录: {package_dir}")
    print(f"  启动文件: {package_dir / exe_name}")
    if bundle_browsers:
        print(f"  内置浏览器: {package_dir / 'browsers'}（约 {browser_mb} MB）")
    print(f"  压缩包:   {zip_path}")


if __name__ == "__main__":
    main()
