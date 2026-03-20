from __future__ import annotations


def map_priority(*, priority: str | None, priority_mapping: dict[str, int] | None = None) -> int | None:
    if not priority:
        return None
    if priority_mapping and priority in priority_mapping:
        return int(priority_mapping[priority])
    return None

