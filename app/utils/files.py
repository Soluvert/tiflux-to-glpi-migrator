from __future__ import annotations

import os
from pathlib import Path


def safe_join(*parts: str) -> str:
    return str(Path(*parts))


def file_exists(path: str) -> bool:
    return os.path.exists(path)

