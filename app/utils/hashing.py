from __future__ import annotations

import hashlib
import json


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_dumps(value: object) -> str:
    # JSON deterministico: garante idempotencia entre execucoes.
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(value: object) -> str:
    return sha256_text(canonical_json_dumps(value))

