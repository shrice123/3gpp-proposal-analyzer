from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path


APP_VERSION = "0.3.1"
SCHEMA_VERSION = 8


def default_data_dir() -> Path:
    override = os.getenv("PROPOSAL_TOOL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    system = platform.system()
    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "3GPP Proposal Analyzer"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    cache_dir: Path
    packages_dir: Path
    reports_dir: Path
    templates_dir: Path
    logs_dir: Path
    key_path: Path

    @classmethod
    def load(cls) -> "Settings":
        data_dir = default_data_dir()
        settings = cls(
            data_dir=data_dir,
            database_path=data_dir / "proposal_analyzer.sqlite3",
            cache_dir=data_dir / "cache",
            packages_dir=data_dir / "packages",
            reports_dir=data_dir / "reports",
            templates_dir=data_dir / "templates",
            logs_dir=data_dir / "logs",
            key_path=data_dir / ".master.key",
        )
        settings.ensure_directories()
        return settings

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.cache_dir,
            self.packages_dir,
            self.reports_dir,
            self.templates_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.load()
