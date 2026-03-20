from __future__ import annotations


def map_status(*, status: str | None, status_mapping: dict[str, str] | None = None) -> str | None:
    if not status:
        return None
    if status_mapping and status in status_mapping:
        return status_mapping[status]
    # fallback: sem mapeamento definido.
    return status

