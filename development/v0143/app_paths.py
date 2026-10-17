"""Platform-aware, local-only application paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping


def default_data_root(
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform_name: str | None = None,
    app_folder: str = "QuotaLab-dev-v0143",
) -> Path:
    """Return the conventional per-user data path while preserving Windows paths."""
    env = os.environ if environment is None else environment
    user_home = Path.home() if home is None else Path(home)
    platform_name = sys.platform if platform_name is None else platform_name

    if platform_name == "win32":
        base = Path(env.get("LOCALAPPDATA") or user_home / "AppData" / "Local")
    elif platform_name == "darwin":
        base = user_home / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_DATA_HOME") or user_home / ".local" / "share")

    return base / app_folder
