"""PyInstaller build definition for the native executable.

The WebView2 build is `ProjectAnalysis.spec`; this one produces the same
application with the native interface and no browser engine of any kind.

    python tools/build_exe.py --native
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

if "SPECPATH" in globals():
    ROOT = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
else:  # pragma: no cover - only when a linter or a test imports this file
    ROOT = Path(__file__).resolve().parent

block_cipher = None

# pydantic v2 keeps its validation core in a compiled extension reached through
# native code the import graph cannot see, so it has to be pulled in whole.
_binaries = []
_datas = []
_hidden = []
for _pkg in ("pydantic", "pydantic_core", "pydantic_settings"):
    _b, _d, _h = collect_all(_pkg)
    _binaries += _b
    _datas += _d
    _hidden += _h

# The interface reads its translations and icon paths from these files at run
# time, so they ship even though no browser ever loads them.
_datas += [
    (str(ROOT / "web" / "i18n"), "web/i18n"),
    (str(ROOT / "web" / "js" / "dom.js"), "web/js"),
    (str(ROOT / "assets"), "assets"),
]

a = Analysis(  # noqa: F821 - injected by PyInstaller
    [str(ROOT / "app" / "ui" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=[
        # Reached only through the analyzer registry.
        "app.analyzers.c_family_analyzer",
        "app.analyzers.database_analyzer",
        "app.analyzers.go_rust_analyzer",
        "app.analyzers.infra_analyzer",
        "app.analyzers.jvm_dotnet_analyzer",
        "app.analyzers.python_analyzer",
        "app.analyzers.web_analyzer",
        "PySide6.QtSvg",
    ]
    + _hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # No browser engine, and none of the Qt modules this interface never opens.
    excludes=[
        "tkinter",
        "pytest",
        "matplotlib",
        "webview",
        "clr_loader",
        "pythonnet",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtBluetooth",
        "PySide6.QtNetworkAuth",
        "PySide6.QtDesigner",
    ],
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
    name="ProjectAnalysisNative",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    name="ProjectAnalysisNative",
)
