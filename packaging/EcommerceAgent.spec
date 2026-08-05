# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).resolve().parent
web_ui_root = project_root / "web-ui"
automation_src = project_root / "ziniao-automation" / "src"
for import_root in (project_root, web_ui_root, automation_src):
    normalized_root = str(import_root)
    if normalized_root not in sys.path:
        sys.path.insert(0, normalized_root)


def runtime_module(name: str) -> bool:
    parts = name.split(".")
    return not any(
        part == "tests"
        or part.startswith("test_")
        or part == "scripts"
        for part in parts
    )


hiddenimports = []
for package in ("config", "tasks", "creator_contact", "mailing"):
    hiddenimports += collect_submodules(package, filter=runtime_module)
hiddenimports += collect_submodules(
    "ziniao_automation",
    filter=runtime_module,
)
hiddenimports += collect_submodules("shared", filter=runtime_module)
hiddenimports += [
    "django.core.management.commands.check",
    "django.core.management.commands.migrate",
    "django.core.management.commands.runserver",
]

datas = []
for application in ("tasks", "creator_contact", "mailing"):
    for resource_name in ("templates", "static"):
        source = web_ui_root / application / resource_name
        if source.is_dir():
            datas.append((str(source), f"{application}/{resource_name}"))
mailing_assets = web_ui_root / "mailing" / "assets"
if mailing_assets.is_dir():
    datas.append((str(mailing_assets), "web-ui/mailing/assets"))
datas += collect_data_files("selenium")


analysis = Analysis(
    [str(project_root / "packaging" / "windows_entrypoint.py")],
    pathex=[
        str(project_root),
        str(web_ui_root),
        str(automation_src),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "notebook",
        "pytest",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="EcommerceAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EcommerceAgent",
)
