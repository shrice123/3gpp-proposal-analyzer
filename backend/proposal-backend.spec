# PyInstaller specification used by Windows/macOS release workflows.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("uvicorn")
datas += [("../desktop-dist", "desktop-dist")]
block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["app.main", "multipart", "openpyxl", "pypdf", "docx"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="proposal-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
