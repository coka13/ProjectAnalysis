"""PyInstaller build definition for the production executable.

Run through tools/build_exe.py rather than directly, so the icon and the
version resource are regenerated from app/branding.py first and cannot drift
away from what the About page reports.

    python tools/build_exe.py
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# PyInstaller execs this file with its own globals, in which SPECPATH is defined
# but __file__ is not. The fallback has to stay lazy: naming __file__ in a
# default argument evaluates it immediately and raises NameError before SPECPATH
# is ever read.
if "SPECPATH" in globals():
    ROOT = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
else:  # pragma: no cover - only when a linter or a test imports this file
    ROOT = Path(__file__).resolve().parent

block_cipher = None

# pydantic v2 keeps its validation core in pydantic_core, a compiled extension
# (_pydantic_core.pyd) reached through native code the import graph cannot see,
# so PyInstaller drops it and the frozen app dies with "No module named
# pydantic_core". Pull each package in whole - binaries, data and submodules.
_pydantic_binaries = []
_pydantic_datas = []
_pydantic_hiddenimports = []
for _pkg in ("pydantic", "pydantic_core", "pydantic_settings"):
    _bins, _data, _hidden = collect_all(_pkg)
    _pydantic_binaries += _bins
    _pydantic_datas += _data
    _pydantic_hiddenimports += _hidden

# An optional WebView2 Fixed Version Runtime dropped into ./webview2 ships with
# the app so machines without the Evergreen runtime still get a Chromium window
# (app/desktop/window.py points the loader at it). The folder is large and
# absent by default, so include it only when it is actually there.
_webview2_datas = []
_webview2_dir = ROOT / "webview2"
if _webview2_dir.is_dir():
    _webview2_datas.append((str(_webview2_dir), "webview2"))

a = Analysis(  # noqa: F821 - injected by PyInstaller
    [str(ROOT / "app" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=_pydantic_binaries,
    # The UI is plain files loaded over file://, so it has to ship verbatim
    # rather than be frozen into the archive.
    datas=[
        (str(ROOT / "web"), "web"),
        (str(ROOT / "assets"), "assets"),
    ]
    + _pydantic_datas
    + _webview2_datas,
    hiddenimports=[
        # Reached only through the analyzer registry, so the import graph does
        # not show them and PyInstaller would otherwise leave them out.
        "app.analyzers.c_family_analyzer",
        "app.analyzers.database_analyzer",
        "app.analyzers.go_rust_analyzer",
        "app.analyzers.infra_analyzer",
        "app.analyzers.jvm_dotnet_analyzer",
        "app.analyzers.python_analyzer",
        "app.analyzers.web_analyzer",
        "clr_loader",
        "webview.platforms.winforms",
    ]
    + _pydantic_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "matplotlib"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ProjectAnalysis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A desktop app must not open a console window behind its own window.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "appicon.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ProjectAnalysis",
)
