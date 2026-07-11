#!/usr/bin/env python3
"""Build portable packages for macOS and Windows.

macOS:  bundles the pre-built proposal-backend Mach-O binary.
Windows: bundles backend source + requirements.txt (user runs python -m venv).
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "release"
BACKEND_BINARY = ROOT / "backend" / "dist" / "proposal-backend"
BACKEND_SOURCE = ROOT / "backend"
DESKTOP_DIST = ROOT / "desktop-dist"


def macos() -> Path:
    name = "3GPP-Proposal-Analyzer-Portable-macOS-arm64"
    out = RELEASE / f"{name}.zip"
    tmp = Path(f"/tmp/{name}") if sys.platform == "darwin" else RELEASE / name
    if tmp.exists():
        shutil.rmtree(tmp)

    print(f"Building macOS portable → {out}")
    shutil.copytree(DESKTOP_DIST, tmp / "desktop-dist")
    shutil.copy2(BACKEND_BINARY, tmp / "proposal-backend")
    shutil.copy2(ROOT / "start.sh", tmp / "start.sh")
    (tmp / "proposal-backend").chmod(0o755)
    (tmp / "start.sh").chmod(0o755)

    # Write platform-specific README
    (tmp / "README.md").write_text(MACOS_README, "utf-8")

    _zip(tmp, out)
    shutil.rmtree(tmp)
    print(f"  Done: {out.name} ({_size(out)})")
    return out


def windows() -> Path:
    name = "3GPP-Proposal-Analyzer-Portable-Windows-x64"
    out = RELEASE / f"{name}.zip"
    tmp = RELEASE / name
    if tmp.exists():
        shutil.rmtree(tmp)

    print(f"Building Windows portable → {out}")

    # Copy full backend source (not binary — must be built on Windows)
    shutil.copytree(DESKTOP_DIST, tmp / "desktop-dist")
    shutil.copytree(BACKEND_SOURCE, tmp / "backend",
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pyinstaller",
                        "dist", "build", "localpycs", ".venv",
                    ))
    shutil.copy2(ROOT / "start.bat", tmp / "start.bat")
    shutil.copy2(BACKEND_SOURCE / "requirements.txt", tmp / "requirements.txt")

    # Write platform-specific README
    (tmp / "README.md").write_text(WINDOWS_README, "utf-8")

    _zip(tmp, out)
    shutil.rmtree(tmp)
    print(f"  Done: {out.name} ({_size(out)})")
    return out


def _zip(src: Path, dest: Path) -> None:
    RELEASE.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src.parent))


def _size(p: Path) -> str:
    mb = p.stat().st_size / (1024 * 1024)
    return f"{mb:.1f} MB" if mb < 100 else f"{mb:.0f} MB"


MACOS_README = """\
# 3GPP 提案洞察 — 便携版 (macOS)

## 适用环境

- macOS (Apple Silicon: M1/M2/M3/M4)
- 无需安装、无需网络、无需 Python

## 使用方法

```bash
cd 3GPP-Proposal-Analyzer-Portable-macOS-arm64

# 首次解除 macOS 隔离（仅一次）
xattr -d com.apple.quarantine proposal-backend start.sh

# 启动
./start.sh
```

浏览器打开 **http://localhost:3000**。按 `Ctrl+C` 停止。

## 数据存储

`~/Library/Application Support/3GPP Proposal Analyzer/`

## 常见问题

**空白页？** 确认终端显示"分析服务就绪"，刷新浏览器。
**端口占用？** 编辑 start.sh 修改 PORT_BACKEND 和 PORT_FRONTEND。
**Intel Mac？** 此包仅支持 Apple Silicon。Intel Mac 请使用 start.bat + venv 方式。
"""

WINDOWS_README = """\
# 3GPP 提案洞察 — 便携版 (Windows)

## 适用环境

- Windows 10/11 x64
- 需要 **Python 3.10+**（首次启动自动创建 venv）

## 首次启动

```cmd
cd 3GPP-Proposal-Analyzer-Portable-Windows-x64

:: 创建 Python 虚拟环境（仅需一次，约 2 分钟）
python -m venv venv
venv\\Scripts\\pip install -r backend\\requirements.txt

:: 启动
start.bat
```

浏览器打开 **http://localhost:3000**。关闭命令行窗口即停止。

## 数据存储

`%LOCALAPPDATA%\\3GPP Proposal Analyzer\\`

## 常见问题

**提示 python 不是命令？** 安装 Python 3.10+，勾选"Add Python to PATH"。
**端口占用？** 编辑 start.bat 修改端口号。
**想完全免 Python？** 需要在 Windows 上运行 `pip install pyinstaller && pyinstaller backend/proposal-backend.spec` 生成 proposal-backend.exe，然后放到此目录即可。
"""


if __name__ == "__main__":
    if BACKEND_BINARY.exists():
        macos()

    windows()
    print("\nAll done. Files in release/:")
    for f in sorted(RELEASE.glob("*Portable*.zip")):
        print(f"  {f.name}  ({_size(f)})")
