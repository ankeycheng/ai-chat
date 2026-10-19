"""Application version — single source of truth is ``[project].version`` in pyproject.toml.

The backend is not installed as a package (``[tool.uv] package = false``), so installed
distribution metadata is normally absent; in that case the version is read from the
pyproject.toml shipped next to ``app/`` (also copied into the Docker image).
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path

DIST_NAME = "ai-chat-backend"
UNKNOWN_VERSION = "0.0.0+unknown"
_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@lru_cache(maxsize=1)
def get_version() -> str:
    try:
        return _dist_version(DIST_NAME)
    except PackageNotFoundError:
        pass
    try:
        with _PYPROJECT.open("rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return UNKNOWN_VERSION
