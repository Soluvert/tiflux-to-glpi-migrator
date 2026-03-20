from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def read_json(path: str | Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, value: object) -> None:
    ensure_dir(Path(path).parent)
    tmp_dir = Path(path).parent
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(tmp_dir), delete=False) as tmp:
        json.dump(value, tmp, ensure_ascii=True, sort_keys=True)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def append_jsonl(path: str | Path, record: object) -> None:
    ensure_dir(Path(path).parent)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True, sort_keys=True, default=str))
        f.write("\n")

