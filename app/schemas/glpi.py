from __future__ import annotations

from pydantic import BaseModel


class GlpiConnectionStatus(BaseModel):
    # "legacy" ou "v2"
    api_mode: str
    ok: bool
    detail: str | None = None

