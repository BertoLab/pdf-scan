from pathlib import Path


root = Path(SPEC).resolve().parent.parent
assets = root / "build" / "desktop-assets"
required = (
    assets / "apps" / "viewer" / "dist" / "index.html",
    assets / "vendor" / "poppler" / "bin" / "pdfinfo.exe",
    assets / "vendor" / "tesseract" / "tesseract.exe",
    assets / "vendor" / "webview2" / "msedgewebview2.exe",
)
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise RuntimeError(f"Desktop build inputs are missing: {', '.join(missing)}")

datas = [
    (str(path), str(path.relative_to(assets).parent))
    for path in assets.rglob("*")
    if path.is_file()
]
if any("sample" in Path(source).parts for source, _ in datas):
    raise RuntimeError("Development sample invoices must not enter the desktop bundle")

analysis = Analysis(
    [str(root / "scripts" / "desktop_app.py")],
    pathex=[str(root), str(root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright", "pip", "setuptools", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="EstrattoreScanFatture",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="EstrattoreScanFatture",
)
