# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the furrify_fo4 kit (Fallout 4 furrifier).
#
# Usage (from the furrifier_fo4/ project root):
#     pyinstaller furrify_fo4.spec --noconfirm --clean
#
# Output:
#     dist/furrify_fo4/furrify_fo4.exe       — CLI entry point (console)
#     dist/furrify_fo4/furrify_fo4_gui.exe   — GUI entry point (windowed)
#     dist/furrify_fo4/_internal/            — shared Python runtime + packed modules
#     dist/furrify_fo4/schemes/              — race scheme TOMLs (user-editable)
#     dist/furrify_fo4/races/                — race catalog TOMLs (user-editable)
#     dist/furrify_fo4/builtin.toml          — built-in aliases/families (user-editable)
#     dist/furrify_fo4/README.md             — user docs
#
# Ship by zipping the entire dist/furrify_fo4/ folder.
#
# Mirrors furrifier/furrify_skyrim.spec — see that file's header for the
# rationale behind the two-exe / one-COLLECT pattern, the loose schemes/races
# copy, and the PyNifly bundling. FO4-specific notes:
#   - The package isn't pip-installed in the build env (esplib is), so `src/`
#     is added to pathex for the analyzer to find `import furrifier_fo4`.
#   - builtin.toml is a top-level FILE (aliases + families), copied loose
#     alongside the exes — loader._find_resource_dir('builtin.toml') and
#     world.default_races_dir() both use sys.frozen detection to find the loose
#     copies next to the exe.

import os

SRC = os.path.join(SPECPATH, 'src')

# --- PyNifly bundling (see furrify_skyrim.spec for the full explanation) --
PYNIFLY_ROOT = r'C:\Modding\PyNifly\io_scene_nifly'
NIFLY_DLL = r'C:\Modding\PyNifly\NiflyDLL\x64\Release\NiflyDLL.dll'

_PYNIFLY_DATAS = [
    (PYNIFLY_ROOT + r'\tri\trifile.py', 'tri'),
]
_PYNIFLY_BINARIES = [
    (NIFLY_DLL, '.'),
]

_DATAS = [
    ('src/furrifier_fo4/assets/*.svg', 'furrifier_fo4/assets'),
    ('src/furrifier_fo4/assets/*.png', 'furrifier_fo4/assets'),
    ('src/furrifier_fo4/preview/*.qml', 'furrifier_fo4/preview'),
    ('src/furrifier_fo4/facegen/_bc7enc.dll', 'furrifier_fo4/facegen'),
] + _PYNIFLY_DATAS


# --- CLI exe (console) ---------------------------------------------------

a_cli = Analysis(
    ['launcher.py'],
    pathex=[SRC, PYNIFLY_ROOT],
    binaries=_PYNIFLY_BINARIES,
    datas=_DATAS,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_cli = PYZ(a_cli.pure)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name='furrify_fo4',
    icon='furrify_fo4.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- GUI exe (windowed) --------------------------------------------------

a_gui = Analysis(
    ['launcher_gui.py'],
    pathex=[SRC, PYNIFLY_ROOT],
    binaries=_PYNIFLY_BINARIES,
    datas=_DATAS,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_gui = PYZ(a_gui.pure)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name='furrify_fo4_gui',
    icon='furrify_fo4.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- Shared COLLECT (_internal/) -----------------------------------------

coll = COLLECT(
    exe_cli,
    exe_gui,
    a_cli.binaries + a_gui.binaries,
    a_cli.datas + a_gui.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='furrify_fo4',
)

# --- Post-build: copy schemes/, races/, builtin.toml, README loose -------
#
# Kept out of PyInstaller's datas= so they land as editable siblings of the
# exes (not buried in _internal/). The frozen-mode resource lookups in
# loader.py and world.py expect them next to the exe.
import shutil
from pathlib import Path

_spec_dir = Path(SPECPATH)
_dist_dir = Path(DISTPATH) / coll.name

# Dev/test-only scheme files — kept in source (the test suite loads every
# scheme in schemes/ via list_available_schemes), but NOT shipped in the kit.
# ffo_scheme.toml is the shipping copy of ffo_test.toml.
_TEST_ONLY = {'ffo_test.toml', 'test_facegen.toml'}


def _ignore_test_files(dirname, names):
    return [n for n in names if n in _TEST_ONLY]


for _folder_name in ('schemes', 'races'):
    _src = _spec_dir / _folder_name
    _dst = _dist_dir / _folder_name
    if _src.is_dir():
        shutil.copytree(_src, _dst, dirs_exist_ok=True,
                        ignore=_ignore_test_files)
        print(f"Copied {_src} -> {_dst}")
    else:
        print(f"WARNING: {_folder_name}/ directory not found at {_src}")

for _file_name in ('builtin.toml', 'README.md'):
    _src = _spec_dir / _file_name
    _dst = _dist_dir / _file_name
    if _src.is_file():
        shutil.copyfile(_src, _dst)
        print(f"Copied {_src} -> {_dst}")
    else:
        print(f"WARNING: {_file_name} not found at {_src}")
