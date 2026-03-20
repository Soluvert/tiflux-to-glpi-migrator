from __future__ import annotations

import datetime as dt


def try_parse_datetime(value: str) -> dt.datetime | None:
    # Heuristica simples baseada em fromisoformat.
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

