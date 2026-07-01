"""PyInstaller-based portable package builder for example apps."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

from packager.manifest import PortableManifest, discover_example_dirs, load_manifest

EXAMPLES_ROOT = Path(__file__).resolve().parent.parent
PLAYWRIGHT_CACHE = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"


def discover_examples(examples_root: Path | None = None) -> list[Path]:
    return discover_example_dirs(examples_root or EXAMPLES_ROOT)


def list_examples(examples_root: Path | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for app_dir in discover_examples(examples_root):
        manifest = load_manifest(app_dir)
        rows.append(
            {
                "id": manifest.app_id,
                "name": manifest.display_name,
                "path": str(app_dir.relative_to(examples_root or EXAMPLES_ROOT)),
            }
        )
    return rows


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
    copied_mb = 0
    for folder in _default_browser_folders():
        src = PLAYWRIGHT_CACHE / folder
        if not src.exists():
            raise SystemExit(f"缺少浏览器目录: {src}\n请先运行: python -m playwright install chromium")
        target = dest / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        copied_mb += int(size / (1024 * 1024))
        print(f"  已打包浏览器: {folder} ({size / (1024 * 1024):.0f} MB)")
    return copied_mb


def _should_bundle_browsers(manifest: PortableManifest, bundle_browsers: bool | None) -> bool:
    if bundle_browsers is not None:
        return bundle_browsers
    if not manifest.bundle_playwright_browsers:
        return False
    if manifest.browser_bundle_env:
        return os.getenv(manifest.browser_bundle_env, "1").lower() not in {"0", "false", "no", "off"}
    return True


def _pyinstaller_cmd(manifest: PortableManifest) -> list[str]:
    sep = ";" if sys.platform.startswith("win") else ":"
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        manifest.exe_name,
    ]

    for rel in manifest.include_paths:
        cmd.extend(["--paths", str((manifest.app_dir / rel).resolve())])
    if manifest.include_repo_root:
        cmd.extend(["--paths", str(manifest.repo_root)])

    for item in manifest.add_data:
        if not item.src.exists():
            raise FileNotFoundError(f"add_data 源路径不存在: {item.src}")
        cmd.extend(["--add-data", f"{item.src}{sep}{item.dest}"])

    for module in manifest.hidden_imports:
        cmd.extend(["--hidden-import", module])
    for pkg in manifest.collect_all:
        cmd.extend(["--collect-all", pkg])

    if manifest.bundle_sqlite:
        cmd.extend(_sqlite_binaries())

    cmd.append(str(manifest.entry_script))
    return cmd


def _render_readme(manifest: PortableManifest, *, browser_mb: int, bundle_browsers: bool) -> str:
    if manifest.readme_template.strip():
        browser_note = (
            f"已内置浏览器（约 {browser_mb} MB），同步官网无需再下载。"
            if bundle_browsers
            else "未内置浏览器；首次同步会自动下载 Chromium（约 200MB）。"
        )
        return manifest.readme_template.format(
            title=manifest.display_name,
            exe_name=manifest.exe_name,
            browser_note=browser_note,
        )

    return f"""{manifest.display_name} - 便携版

【使用】
1. 双击 {manifest.exe_name}.exe
2. 浏览器会自动打开；若未打开，请访问 http://127.0.0.1:8080
3. 关闭命令行窗口即可退出

【数据】
- 数据库保存在 data 文件夹
- 可在页面中导出/导入数据库与同事共享

【说明】
- {'已内置浏览器。' if bundle_browsers else '未内置浏览器，首次同步需下载 Chromium。'}
"""


def build_example(
    app_id_or_dir: str | Path,
    *,
    examples_root: Path | None = None,
    bundle_browsers: bool | None = None,
) -> Path:
    root = examples_root or EXAMPLES_ROOT
    app_path = Path(app_id_or_dir)
    if not app_path.is_dir():
        matches = [d for d in discover_example_dirs(root) if d.name == str(app_id_or_dir)]
        if not matches:
            raise SystemExit(f"未找到示例应用: {app_id_or_dir}")
        app_path = matches[0]

    manifest = load_manifest(app_path)
    return _build(manifest, bundle_browsers=bundle_browsers)


def build_from_manifest(manifest: PortableManifest, *, bundle_browsers: bool | None = None) -> Path:
    return _build(manifest, bundle_browsers=bundle_browsers)


def _build(manifest: PortableManifest, *, bundle_browsers: bool | None) -> Path:
    if shutil.which("pyinstaller") is None:
        raise SystemExit("PyInstaller 未安装。请先运行: pip install pyinstaller")

    if not manifest.entry_script.exists():
        raise SystemExit(f"缺少入口脚本: {manifest.entry_script}")

    for item in manifest.add_data:
        if not item.src.exists():
            raise SystemExit(f"缺少静态资源: {item.src}")

    do_browsers = _should_bundle_browsers(manifest, bundle_browsers)
    dist = manifest.dist_dir
    dist.mkdir(parents=True, exist_ok=True)

    print(f"正在打包 {manifest.display_name}（{manifest.app_id}），可能需要几分钟...")
    subprocess.run(_pyinstaller_cmd(manifest), cwd=manifest.app_dir, check=True)

    exe_name = f"{manifest.exe_name}.exe" if sys.platform.startswith("win") else manifest.exe_name
    build_dir = dist / manifest.exe_name
    built_exe = build_dir / exe_name
    if not built_exe.exists():
        raise SystemExit(f"未找到输出文件: {built_exe}")

    package_dir = dist / manifest.package_dir_name
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(build_dir, package_dir)
    (package_dir / "data").mkdir(exist_ok=True)

    for seed in manifest.seed_files:
        if seed.src.exists():
            target = package_dir / seed.dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed.src, target)
            print(f"  已复制数据: {seed.src.name} -> {seed.dest}")

    browser_mb = 0
    if do_browsers and manifest.collect_all and "playwright" in manifest.collect_all:
        print("\n正在打包内置浏览器（体积较大，请耐心等待）...")
        browser_mb = _bundle_browsers(package_dir)
    elif manifest.collect_all and "playwright" in manifest.collect_all:
        print("\n已跳过浏览器打包。")

    readme_path = package_dir / "使用说明.txt"
    readme_path.write_text(
        _render_readme(manifest, browser_mb=browser_mb, bundle_browsers=do_browsers),
        encoding="utf-8",
    )

    zip_path = dist / f"{manifest.package_dir_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package_dir.rglob("*"):
            archive.write(path, Path(manifest.package_dir_name) / path.relative_to(package_dir))

    print("\n完成:")
    print(f"  程序目录: {package_dir}")
    print(f"  启动文件: {package_dir / exe_name}")
    if browser_mb:
        print(f"  内置浏览器: {package_dir / 'browsers'}（约 {browser_mb} MB）")
    print(f"  压缩包:   {zip_path}")
    return zip_path
