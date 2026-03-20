from __future__ import annotations

from typing import Any

from ..utils.io import append_jsonl


def append_manifest_record(*, manifest_path: str, record: dict[str, Any]) -> None:
    append_jsonl(manifest_path, record)

