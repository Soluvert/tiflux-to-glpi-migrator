from __future__ import annotations

import itertools
from typing import Any

from loguru import logger

from ..schemas.tiflux import EndpointCapability, PaginationHint, TifluxApiCapabilities

from .tiflux_api import TifluxApiClient


def _variants(resource: str) -> list[str]:
    # Cria variações de nome comum (singular/plural, snake/camel).
    res = resource.strip("_")
    variants = {res}
    if res.endswith("s"):
        variants.add(res[:-1])
    else:
        variants.add(res + "s")

    # Algumas APIs usam snake_case / camelCase.
    parts = res.split("_")
    if len(parts) > 1:
        camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
        variants.add(camel)
        pascal = "".join(p.capitalize() for p in parts)
        variants.add(pascal)
    return sorted(variants)


def _path_candidates(resource: str) -> list[str]:
    v = _variants(resource)
    prefixes = ["", "/api", "/api/v1", "/api/v2", "/v2", "/rest", "/rest/v1"]
    paths: list[str] = []
    for pref, name in itertools.product(prefixes, v):
        # Evita duplicidades e mantém formato de URL.
        path = f"{pref}/{name}".replace("//", "/")
        paths.append(path)
    # Ordena para dar prioridade a URLs mais simples.
    uniq = list(dict.fromkeys(paths))
    return sorted(uniq, key=lambda p: (len(p), p))


def _pagination_hint_from_payload(payload: Any, *, probe_params: dict[str, Any] | None = None) -> PaginationHint | None:
    if not isinstance(payload, (dict, list)):
        return None
    if isinstance(payload, list):
        # Se o probe usou offset=1 (estilo Tiflux: offset como page number), marcar como offset_page.
        if probe_params and "offset" in probe_params:
            return PaginationHint(style="offset_page", params={"offset": 1, "limit": 50}, next_field=None)
        # Sem metadados -> paginação pode existir via query params, mas não dá para inferir.
        return PaginationHint(style="page_limit", params={"page": 1, "limit": 50}, next_field=None)

    # Heurísticas: busca por keys comuns.
    keys = set(payload.keys())
    pagination_keys = {"page", "pages", "total", "limit", "offset", "take", "skip", "next", "nextPage", "has_more", "hasMore"}
    if keys & {"offset", "limit"}:
        return PaginationHint(style="offset_limit", params={"offset": 0, "limit": 50}, next_field=None)
    if keys & {"take", "skip"}:
        return PaginationHint(style="take_skip", params={"skip": 0, "take": 50}, next_field=None)
    if keys & {"page", "totalPages", "pages", "limit"}:
        return PaginationHint(style="page_limit", params={"page": 1, "limit": 50}, next_field=None)
    if keys & {"next", "nextPage"}:
        next_field = "next" if "next" in keys else "nextPage"
        return PaginationHint(style="next_url", params=probe_params or {}, next_field=next_field)
    if keys & {"data", "items"}:
        # Ainda que sem metadados, o estilo pode ser comum (page/limit).
        return PaginationHint(style="page_limit", params={"page": 1, "limit": 50}, next_field=None)
    return None


def _detect_date_filter_params(*, probe_result: dict[str, Any]) -> dict[str, str]:
    # Não temos como saber valores; apenas registramos nomes prováveis caso o endpoint aceite.
    # A validação real acontece no probe do discovery.
    return {}


class TifluxDiscoveryClient:
    def __init__(self, *, api_client: TifluxApiClient):
        self.api_client = api_client

    def discover_resource(self, *, resource: str) -> tuple[EndpointCapability | None, list[str]]:
        probe_log: list[str] = []
        candidates = _path_candidates(resource)

        # Query params comuns para provar paginação.
        sample_param_sets = [
            {},
            {"offset": 1, "limit": 1},
            {"page": 1, "limit": 1},
            {"offset": 0, "limit": 1},
            {"take": 1, "skip": 0},
        ]

        successful: list[tuple[int, EndpointCapability]] = []
        unavailable: list[Any] = []

        for path in candidates:
            for params in sample_param_sets:
                probe = self.api_client.probe(path, params=params or None)
                if probe.ok:
                    cap = EndpointCapability(
                        resource=resource,
                        path=path,
                        sample_params=params if params else {},
                        pagination=None,
                        date_filter_params={},
                        probe_status=[],
                    )

                    # tenta obter payload para inferir paginação/formato
                    try:
                        payload = self.api_client.get_json(path, params=params or None)
                        cap.pagination = _pagination_hint_from_payload(payload, probe_params=params)
                        successful.append((probe.status_code, cap))
                        logger.info(f"Discovered {resource} -> {path} (status {probe.status_code})")
                    except Exception as e:  # pragma: no cover
                        unavailable.append(probe)
                        logger.debug(f"Failed to load JSON for {resource} at {path}: {e}")
                else:
                    unavailable.append(probe)

                cap_status = probe.model_dump()
                if isinstance(unavailable, list) and isinstance(cap_status, dict):
                    probe_log.append(f"{resource}:{path} {params} => {probe.status_code}")

        if not successful:
            return None, [str(u) for u in unavailable[:20]]

        # Seleciona a melhor capacidade: preferir estilos de paginação explícitos
        # (offset_page, offset_limit, take_skip) sobre page_limit genérico.
        _pagination_preference = {"offset_page": 0, "offset_limit": 1, "take_skip": 2, "page_limit": 3, "next_url": 4}
        successful.sort(key=lambda x: (
            len(x[1].path),
            _pagination_preference.get(x[1].pagination.style, 5) if x[1].pagination else 5,
            x[1].path,
        ))
        chosen = successful[0][1]
        chosen.probe_status = []  # será preenchido pelo serviço
        return chosen, probe_log

    def discover_all(
        self,
        *,
        resources: list[str],
    ) -> TifluxApiCapabilities:
        caps: dict[str, EndpointCapability] = {}
        unavailable: dict[str, list[Any]] = {}

        for resource in resources:
            cap, _probe_log = self.discover_resource(resource=resource)
            if cap is not None:
                caps[resource] = cap
            else:
                unavailable[resource] = []
        return TifluxApiCapabilities(base_url=self.api_client.base_url, resources=caps, unavailable=unavailable)

