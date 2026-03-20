from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class EndpointProbeResult(BaseModel):
    path: str
    method: str = "GET"
    status_code: int
    ok: bool
    unauthorized: bool = False
    forbidden: bool = False
    not_found: bool = False
    content_type: str | None = None
    response_preview: dict[str, Any] | list[Any] | str | None = None
    checked_at: dt.datetime = Field(default_factory=dt.datetime.utcnow)


class PaginationHint(BaseModel):
    # Qual estilo de paginação parece funcionar
    style: str
    # Parâmetros query a usar para obter uma "page" (quando aplicavel)
    params: dict[str, Any]
    # Caso a API retorne explicitamente um "next" URL, armazenamos aqui.
    next_field: str | None = None


class EndpointCapability(BaseModel):
    resource: str
    path: str
    # params base para requisições de amostra (ex: page=1&limit=50)
    sample_params: dict[str, Any] = Field(default_factory=dict)
    # hint de paginação (page/limit, offset/limit, take/skip)
    pagination: PaginationHint | None = None
    # Se existem filtros por data mapeados para params
    date_filter_params: dict[str, str] = Field(default_factory=dict)
    probe_status: list[EndpointProbeResult] = Field(default_factory=list)


class TifluxApiCapabilities(BaseModel):
    discovered_at: dt.datetime = Field(default_factory=dt.datetime.utcnow)
    base_url: str
    # resource -> capability
    resources: dict[str, EndpointCapability] = Field(default_factory=dict)
    # recursos que falharam ou nao existem
    unavailable: dict[str, list[EndpointProbeResult]] = Field(default_factory=dict)

