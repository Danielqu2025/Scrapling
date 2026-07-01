"""Scaffold portable.manifest.json and portable_launcher.py for an example app."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "portable.manifest.json"
LAUNCHER_NAME = "portable_launcher.py"


@dataclass
class ScaffoldOptions:
    app_dir: Path
    app_id: str
    display_name: str
    exe_name: str
    env_prefix: str
    port: int = 8080
    app_import: str = "from app.main import app"
    static_dir: str = "app/static"
    db_files: list[str] = field(default_factory=list)
    uses_playwright: bool = False
    hidden_imports: list[str] = field(default_factory=list)
    collect_all: list[str] = field(default_factory=list)


def _env_prefix_from_id(app_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", app_id.upper())


def _exe_name_from_id(app_id: str) -> str:
    parts = re.split(r"[_\-]+", app_id)
    if not parts:
        return "App"
    name_parts: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        if index > 0 and len(part) <= 4 and part.isalpha():
            name_parts.append(part.upper())
        else:
            name_parts.append(part[:1].upper() + part[1:])
    return "".join(name_parts) or "App"


def _title_from_id(app_id: str) -> str:
    return app_id.replace("_", " ").replace("-", " ").strip().title()


def _file_mentions_playwright(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "playwright" in text.lower()


def detect_scaffold_options(app_dir: Path, *, title: str = "", exe: str = "", env_prefix: str = "", port: int = 8080) -> ScaffoldOptions:
    app_dir = app_dir.resolve()
    if not app_dir.is_dir():
        raise FileNotFoundError(f"应用目录不存在: {app_dir}")

    app_id = app_dir.name
    display_name = title or _title_from_id(app_id)
    exe_name = exe or _exe_name_from_id(app_id)
    prefix = env_prefix or _env_prefix_from_id(app_id)

    app_import = "from app.main import app"
    if (app_dir / "app" / "main.py").exists():
        app_import = "from app.main import app"
    elif (app_dir / "main.py").exists():
        app_import = "from main import app"

    static_dir = "app/static"
    if not (app_dir / static_dir).is_dir():
        static_candidates = [p for p in app_dir.rglob("static") if p.is_dir() and "node_modules" not in p.parts]
        if static_candidates:
            static_dir = str(static_candidates[0].relative_to(app_dir)).replace("\\", "/")

    db_files: list[str] = []
    data_dir = app_dir / "data"
    if data_dir.is_dir():
        for db_path in sorted(data_dir.glob("*.db")):
            rel = db_path.relative_to(app_dir).as_posix()
            db_files.append(rel)

    uses_playwright = any(_file_mentions_playwright(path) for path in app_dir.rglob("*.py"))

    hidden_imports = [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "email.mime.multipart",
        "email.mime.text",
        "sqlite3",
        "_sqlite3",
        "multipart",
    ]
    if "apscheduler" in (app_dir / "04_run_server.py").read_text(encoding="utf-8", errors="ignore").lower() if (app_dir / "04_run_server.py").exists() else "":
        hidden_imports.append("apscheduler")

    collect_all = ["scrapling", "lxml", "orjson"]
    if uses_playwright:
        collect_all.extend(["playwright", "browserforge", "apify_fingerprint_datapoints"])

    detected_port = port
    run_server = app_dir / "04_run_server.py"
    if run_server.exists():
        match = re.search(r'SH_[A-Z0-9_]+_PORT["\'],\s*["\'](\d+)["\']', run_server.read_text(encoding="utf-8", errors="ignore"))
        if not match:
            match = re.search(r'PORT["\'],\s*["\'](\d+)["\']', run_server.read_text(encoding="utf-8", errors="ignore"))
        if match:
            detected_port = int(match.group(1))

    return ScaffoldOptions(
        app_dir=app_dir,
        app_id=app_id,
        display_name=display_name,
        exe_name=exe_name,
        env_prefix=prefix,
        port=detected_port,
        app_import=app_import,
        static_dir=static_dir,
        db_files=db_files,
        uses_playwright=uses_playwright,
        hidden_imports=hidden_imports,
        collect_all=collect_all,
    )


def _launcher_template(opts: ScaffoldOptions) -> str:
    playwright_block = ""
    if opts.uses_playwright:
        playwright_block = f'''
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
    app_root = Path(os.environ["{opts.env_prefix}_APP_ROOT"])
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and _bundled_browser_ready(app_root):
        print("已检测到内置浏览器。")
        return
    marker = app_root / "data" / ".playwright_ready"
    if marker.exists():
        return
    print("首次使用需要下载浏览器组件，请稍候...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        marker.write_text("ok", encoding="utf-8")
        print("浏览器组件已就绪。")
    except Exception as exc:
        print(f"浏览器组件安装失败: {{exc}}")
'''
        browser_setup = f'''
    if os.getenv("{opts.env_prefix}_SKIP_BROWSER_INSTALL", "0") not in {{"1", "true", "yes"}}:
        threading.Thread(target=_ensure_playwright_browser, daemon=True).start()
'''
        browsers_dir_setup = '''
    browsers_dir = app_root / "browsers"
    if browsers_dir.is_dir():
        for pattern in ("chromium-*", "chromium_headless_shell-*"):
            if any(browsers_dir.glob(pattern)):
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir.resolve())
                break
'''
    else:
        browser_setup = ""
        browsers_dir_setup = ""

    return f'''"""
Portable launcher for {opts.display_name}.

Generated by: python examples/package_app.py init {opts.app_id}

Development:
    python {LAUNCHER_NAME}

After build:
    double-click {opts.exe_name}.exe
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

    os.environ["{opts.env_prefix}_APP_ROOT"] = str(app_root)
    os.chdir(app_root)
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "data").mkdir(parents=True, exist_ok=True)
    (app_root / "output").mkdir(parents=True, exist_ok=True)
{browsers_dir_setup}
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    if str(bundle_dir) not in sys.path:
        sys.path.insert(0, str(bundle_dir))
    return app_root
{playwright_block}
def _open_browser(port: int) -> None:
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{{port}}/")


def main() -> None:
    app_root = _prepare_runtime()
    port = int(os.getenv("{opts.env_prefix}_PORT", "{opts.port}"))
    host = os.getenv("{opts.env_prefix}_HOST", "127.0.0.1")
{browser_setup}
    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    print("=" * 50)
    print("{opts.display_name}")
    print(f"数据目录: {{app_root / 'data'}}")
    print(f"请在浏览器打开: http://127.0.0.1:{{port}}")
    print("关闭本窗口即可停止服务。")
    print("=" * 50)

    import uvicorn
    {opts.app_import}

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
'''


def _manifest_dict(opts: ScaffoldOptions) -> dict:
    manifest: dict = {
        "id": opts.app_id,
        "display_name": opts.display_name,
        "exe_name": opts.exe_name,
        "entry_script": LAUNCHER_NAME,
        "package_suffix": "_portable",
        "include_paths": ["."],
        "include_repo_root": True,
        "add_data": [],
        "hidden_imports": opts.hidden_imports,
        "collect_all": opts.collect_all,
        "bundle_sqlite": bool(opts.db_files),
        "seed_data": [],
        "readme_template": (
            "{title} - 便携版\\n\\n"
            "【使用】\\n"
            "1. 双击 {exe_name}.exe\\n"
            "2. 浏览器会自动打开；若未打开，请访问 http://127.0.0.1:"
            + str(opts.port)
            + "\\n"
            "3. 关闭命令行窗口即可退出\\n\\n"
            "【数据】\\n"
            "- 数据库保存在 data 文件夹\\n"
            "- {browser_note}\\n"
        ),
    }

    static_path = opts.app_dir / opts.static_dir
    if static_path.is_dir():
        manifest["add_data"].append({"from": opts.static_dir, "to": opts.static_dir.replace("\\", "/")})

    for db_rel in opts.db_files:
        manifest["seed_data"].append({"from": db_rel, "to": db_rel})

    if opts.uses_playwright:
        manifest["bundle_playwright_browsers"] = {
            "enabled_by_default": True,
            "env_var": f"{opts.env_prefix}_BUNDLE_BROWSERS",
        }

    return manifest


def _paths_snippet(opts: ScaffoldOptions) -> str:
    return f'''# 若尚无 _paths.py，可添加以下内容以支持便携版数据目录：

import os
import sys
from pathlib import Path

def get_app_root() -> Path:
    if root := os.environ.get("{opts.env_prefix}_APP_ROOT"):
        return Path(root)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

APP_ROOT = get_app_root()
DATA_DIR = APP_ROOT / "data"
DB_PATH = DATA_DIR / "your.db"   # 按实际文件名修改
'''


def init_portable_files(
    app_dir: Path,
    *,
    title: str = "",
    exe: str = "",
    env_prefix: str = "",
    port: int = 8080,
    force: bool = False,
) -> dict[str, Path | str]:
    opts = detect_scaffold_options(app_dir, title=title, exe=exe, env_prefix=env_prefix, port=port)
    created: dict[str, Path | str] = {}

    manifest_path = opts.app_dir / MANIFEST_NAME
    launcher_path = opts.app_dir / LAUNCHER_NAME

    for path in (manifest_path, launcher_path):
        if path.exists() and not force:
            raise FileExistsError(f"已存在 {path.name}，请加 --force 覆盖。")

    manifest_path.write_text(
        json.dumps(_manifest_dict(opts), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    launcher_path.write_text(_launcher_template(opts), encoding="utf-8")
    created["manifest"] = manifest_path
    created["launcher"] = launcher_path

    paths_file = opts.app_dir / "_paths.py"
    if not paths_file.exists():
        hint_path = opts.app_dir / "_paths.portable.example.py"
        hint_path.write_text(_paths_snippet(opts), encoding="utf-8")
        created["paths_hint"] = hint_path

    created["summary"] = (
        f"应用: {opts.display_name} ({opts.app_id})\n"
        f"exe: {opts.exe_name}.exe\n"
        f"环境变量前缀: {opts.env_prefix}_APP_ROOT\n"
        f"静态资源: {opts.static_dir if (opts.app_dir / opts.static_dir).is_dir() else '（未检测到）'}\n"
        f"数据库: {', '.join(opts.db_files) if opts.db_files else '（未检测到，打包时不带 db）'}\n"
        f"Playwright: {'是' if opts.uses_playwright else '否'}\n"
        f"\n下一步:\n"
        f"  1. 检查 {MANIFEST_NAME} 与 {LAUNCHER_NAME}\n"
        f"  2. 若使用 data 目录，请确保 _paths.py 读取 {opts.env_prefix}_APP_ROOT\n"
        f"  3. pip install -r requirements-app.txt && pip install -r ../requirements-portable.txt\n"
        f"  4. python ../package_app.py build {opts.app_id}"
    )
    return created
