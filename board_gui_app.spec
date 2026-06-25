# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


project_dir = Path(r"D:\Works\board_cut_optimizer")
env_prefix = Path(sys.prefix)
library_bin = env_prefix / "Library" / "bin"
library_lib = env_prefix / "Library" / "lib"
icon_path = project_dir / "assets" / "board_gui_icon.ico"

required_dlls = [
    "tcl86t.dll",
    "tk86t.dll",
    "libexpat.dll",
    "liblzma.dll",
    "libbz2.dll",
    "libmpdec-4.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "zstd.dll",
]

binaries = [
    (str(library_bin / dll_name), ".")
    for dll_name in required_dlls
    if (library_bin / dll_name).exists()
]

datas = []
for folder_name in ("tcl8.6", "tk8.6"):
    folder = library_lib / folder_name
    if folder.exists():
        datas.append((str(folder), folder_name))

asset_dir = project_dir / "assets"
for asset_name in ("board_gui_icon.ico", "board_gui_icon.png"):
    asset_path = asset_dir / asset_name
    if asset_path.exists():
        datas.append((str(asset_path), "assets"))

a = Analysis(
    [str(project_dir / "board_gui_app.py")],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="board_gui_app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="board_gui_app",
)
