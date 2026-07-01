#!/usr/bin/env python3
"""
Build a portable zip + exe for an example app under examples/.

Usage (from repo root or examples/):

    pip install pyinstaller
    pip install -r examples/sh_eia/requirements-app.txt   # app-specific deps

    python examples/package_app.py list
    python examples/package_app.py build sh_eia
    python examples/package_app.py build sh_eia --no-browsers
    python examples/package_app.py build --all

    python examples/package_app.py init my_app       # 自动生成 manifest + launcher
    python examples/package_app.py init my_app --title "我的应用" --exe MyApp

Each packagable app needs portable.manifest.json and portable_launcher.py.
Use `init` to generate them automatically.

Output: examples/<app>/dist/<Name>_portable.zip
See examples/README.md for full documentation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parent
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from packager.build import build_example, discover_examples, list_examples
from packager.scaffold import init_portable_files


def main() -> None:
    parser = argparse.ArgumentParser(description="将 examples 下的应用打包为便携版（exe + 数据）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出可打包的示例应用")

    build_parser = sub.add_parser("build", help="打包指定应用")
    build_parser.add_argument("app", nargs="?", help="应用目录名，如 sh_eia")
    build_parser.add_argument("--all", action="store_true", help="打包所有带 manifest 的应用")
    build_parser.add_argument(
        "--no-browsers",
        action="store_true",
        help="不打包 Playwright 浏览器（减小体积，使用者需自行下载）",
    )

    init_parser = sub.add_parser("init", help="自动生成为便携打包所需的 manifest 与 launcher")
    init_parser.add_argument("app", help="应用目录名或路径，如 sh_eia 或 examples/my_app")
    init_parser.add_argument("--title", default="", help="显示名称")
    init_parser.add_argument("--exe", default="", help="exe 文件名（不含 .exe）")
    init_parser.add_argument("--env-prefix", default="", help="环境变量前缀，如 SH_EIA")
    init_parser.add_argument("--port", type=int, default=8080, help="默认 Web 端口")
    init_parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")

    args = parser.parse_args()

    if args.command == "list":
        apps = list_examples(EXAMPLES_ROOT)
        if not apps:
            print("未发现 portable.manifest.json。请在 examples/<app>/ 下添加配置。")
            return
        print("可打包的应用：")
        for item in apps:
            print(f"  {item['id']:12}  {item['name']}  ({item['path']})")
        return

    bundle_browsers = None if not getattr(args, "no_browsers", False) else False

    if args.command == "build":
        if args.all:
            apps = discover_examples(EXAMPLES_ROOT)
            if not apps:
                raise SystemExit("没有可打包的应用。")
            for app_dir in apps:
                print(f"\n{'=' * 60}\n打包 {app_dir.name}\n{'=' * 60}")
                build_example(app_dir, examples_root=EXAMPLES_ROOT, bundle_browsers=bundle_browsers)
            return
        if not args.app:
            raise SystemExit("请指定应用名，或使用 --all。运行 list 查看可用应用。")
        build_example(args.app, examples_root=EXAMPLES_ROOT, bundle_browsers=bundle_browsers)
        return

    if args.command == "init":
        app_path = Path(args.app)
        if not app_path.is_dir():
            app_path = EXAMPLES_ROOT / args.app
        result = init_portable_files(
            app_path,
            title=args.title,
            exe=args.exe,
            env_prefix=args.env_prefix,
            port=args.port,
            force=args.force,
        )
        print("已生成:")
        print(f"  {result['manifest']}")
        print(f"  {result['launcher']}")
        if "paths_hint" in result:
            print(f"  {result['paths_hint']}  （_paths.py 参考模板）")
        print()
        print(result["summary"])


if __name__ == "__main__":
    main()
