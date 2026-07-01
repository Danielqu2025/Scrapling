"""Load portable.manifest.json for an example app."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_NAME = "portable.manifest.json"


@dataclass
class AddDataItem:
    src: Path
    dest: str


@dataclass
class SeedFile:
    src: Path
    dest: str


@dataclass
class PortableManifest:
    app_dir: Path
    app_id: str
    display_name: str
    exe_name: str
    entry_script: Path
    package_suffix: str = "_portable"
    include_paths: list[str] = field(default_factory=lambda: ["."])
    include_repo_root: bool = True
    add_data: list[AddDataItem] = field(default_factory=list)
    hidden_imports: list[str] = field(default_factory=list)
    collect_all: list[str] = field(default_factory=list)
    bundle_sqlite: bool = True
    bundle_playwright_browsers: bool = True
    browser_bundle_env: str = ""
    seed_files: list[SeedFile] = field(default_factory=list)
    readme_template: str = ""

    @property
    def repo_root(self) -> Path:
        return self.app_dir.parent.parent

    @property
    def dist_dir(self) -> Path:
        return self.app_dir / "dist"

    @property
    def package_dir_name(self) -> str:
        return f"{self.exe_name}{self.package_suffix}"


def _resolve(app_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return app_dir / path


def load_manifest(app_dir: Path) -> PortableManifest:
    manifest_path = app_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"未找到 {MANIFEST_NAME}：{manifest_path}")

    raw: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    app_id = raw.get("id") or app_dir.name
    exe_name = raw["exe_name"]
    entry = _resolve(app_dir, raw.get("entry_script", "portable_launcher.py"))

    add_data = [
        AddDataItem(src=_resolve(app_dir, item["from"]), dest=item["to"])
        for item in raw.get("add_data", [])
    ]
    seed_files = [
        SeedFile(src=_resolve(app_dir, item["from"]), dest=item["to"])
        for item in raw.get("seed_data", [])
    ]

    browser_cfg = raw.get("bundle_playwright_browsers", True)
    if isinstance(browser_cfg, dict):
        bundle_browsers = bool(browser_cfg.get("enabled_by_default", True))
        browser_env = str(browser_cfg.get("env_var", ""))
    else:
        bundle_browsers = bool(browser_cfg)
        browser_env = str(raw.get("browser_bundle_env", ""))

    return PortableManifest(
        app_dir=app_dir.resolve(),
        app_id=app_id,
        display_name=str(raw.get("display_name", exe_name)),
        exe_name=exe_name,
        entry_script=entry,
        package_suffix=str(raw.get("package_suffix", "_portable")),
        include_paths=list(raw.get("include_paths", ["."])),
        include_repo_root=bool(raw.get("include_repo_root", True)),
        add_data=add_data,
        hidden_imports=list(raw.get("hidden_imports", [])),
        collect_all=list(raw.get("collect_all", [])),
        bundle_sqlite=bool(raw.get("bundle_sqlite", True)),
        bundle_playwright_browsers=bundle_browsers,
        browser_bundle_env=browser_env,
        seed_files=seed_files,
        readme_template=str(raw.get("readme_template", "")),
    )


def discover_example_dirs(examples_root: Path) -> list[Path]:
    apps: list[Path] = []
    for child in sorted(examples_root.iterdir()):
        if not child.is_dir() or child.name.startswith("_") or child.name == "packager":
            continue
        if (child / MANIFEST_NAME).exists():
            apps.append(child)
    return apps
