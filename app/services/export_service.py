from __future__ import annotations

import datetime as dt
import os
import time
from typing import Any

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..clients.tiflux_api import TifluxApiClient
from ..constants import RAW_MANIFEST_PATH
from ..repositories.migration_state_repository import get_raw_export_page, upsert_raw_export_page
from ..repositories.raw_manifest_repository import append_manifest_record
from ..schemas.tiflux import EndpointCapability, TifluxApiCapabilities
from ..services.attachment_service import download_blobs_from_payload
from ..utils.hashing import payload_hash
from ..utils.io import ensure_dir, write_json


def _safe_resource_dir(resource: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in resource).strip("_")


def _iter_page_params(*, cap: EndpointCapability, max_pages: int = 10_000):
    if cap.pagination is None:
        # fallback: page/limit
        for page in range(1, max_pages + 1):
            if "limit" in cap.sample_params:
                yield {"page": page, "limit": cap.sample_params.get("limit", 50)}
            else:
                yield {"page": page}
        return

    style = cap.pagination.style
    if style == "page_limit":
        limit = cap.pagination.params.get("limit", cap.sample_params.get("limit"))
        for page in range(1, max_pages + 1):
            if limit is not None:
                yield {"page": page, "limit": limit}
            else:
                yield {"page": page}
    elif style == "offset_limit":
        limit = cap.pagination.params.get("limit", cap.sample_params.get("limit", 50))
        offset = cap.pagination.params.get("offset", 0)
        step = limit
        for i in range(0, max_pages):
            yield {"offset": offset + (i * step), "limit": limit}
    elif style == "take_skip":
        take = cap.pagination.params.get("take", cap.sample_params.get("take", 50))
        skip = cap.pagination.params.get("skip", 0)
        for i in range(0, max_pages):
            yield {"take": take, "skip": skip + (i * take)}
    elif style == "next_url":
        # nesse modo, o iterador precisa decidir via campo 'next' na resposta.
        # Para manter o pipeline simples, vamos tratar como page_limit quando falhar.
        limit = cap.pagination.params.get("limit", cap.sample_params.get("limit", 50))
        for page in range(1, max_pages + 1):
            yield {"page": page, "limit": limit}
    else:
        limit = cap.sample_params.get("limit")
        for page in range(1, max_pages + 1):
            if limit is not None:
                yield {"page": page, "limit": limit}
            else:
                yield {"page": page}


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=10),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _fetch_page(api: TifluxApiClient, *, path: str, params: dict[str, Any]) -> Any:
    return api.get_json(path, params=params or None)


def export_tiflux_raw(
    *,
    caps: TifluxApiCapabilities | dict[str, Any],
    tiflux_base_url: str,
    tiflux_api_token: str,
    data_dir: str,
    resume: bool,
    max_pages_per_resource: int = 2_000,
    download_blobs: bool = True,
    continue_on_error: bool = False,
    min_request_interval_seconds: float = 0.55,
) -> None:
    if isinstance(caps, dict):
        caps = TifluxApiCapabilities.model_validate(caps)

    processed_dir = os.path.join(data_dir, "processed")
    ensure_dir(processed_dir)

    engine_path = os.path.join(processed_dir, "migrator.sqlite")

    # import local para evitar acoplamento no startup do CLI.
    from ..db.session import init_db

    engine = init_db(sqlite_path=engine_path)

    raw_dir = os.path.join(data_dir, "raw")
    ensure_dir(raw_dir)

    manifest_path = os.path.join(data_dir, RAW_MANIFEST_PATH)
    if not resume and os.path.exists(manifest_path):
        os.remove(manifest_path)

    api = TifluxApiClient(base_url=tiflux_base_url, api_token=tiflux_api_token)

    try:
        resources = caps.resources
        logger.info(f"Export starting: {len(resources)} resources")

        for resource_name, cap in resources.items():
            resource_dir = os.path.join(raw_dir, _safe_resource_dir(resource_name))
            ensure_dir(resource_dir)
            logger.info(f"Exporting {resource_name} from {cap.path}")

            stop_resource = False
            for page_idx, params in enumerate(_iter_page_params(cap=cap), start=1):
                if page_idx > max_pages_per_resource:
                    logger.warning(f"Max pages reached for {resource_name}")
                    break
                if stop_resource:
                    break

                existing = get_raw_export_page(
                    engine=engine,
                    source_system="tiflux",
                    resource=resource_name,
                    page=page_idx,
                )
                if resume and existing is not None and existing.status == "exported":
                    logger.info(f"Skipping exported page: {resource_name} page {page_idx}")
                    continue

                out_path = os.path.join(resource_dir, f"page_{page_idx}.json")
                try:
                    payload = _fetch_page(api, path=cap.path, params=params)
                    h = payload_hash(payload)
                    write_json(out_path, payload)

                    upsert_raw_export_page(
                        engine=engine,
                        source_system="tiflux",
                        resource=resource_name,
                        page=page_idx,
                        payload_hash=h,
                        path=out_path,
                        status="exported",
                        last_error=None,
                    )

                    # manifesto por pagina (JSONL).
                    exported_at = dt.datetime.utcnow().isoformat()
                    append_manifest_record(
                        manifest_path=manifest_path,
                        record={
                            "resource": resource_name,
                            "source_id": f"{resource_name}:{page_idx}",
                            "page": page_idx,
                            "hash": h,
                            "exported_at": exported_at,
                            "path": out_path,
                        },
                    )

                    if download_blobs and resource_name in {"tickets", "ticket_files"}:
                        downloaded = download_blobs_from_payload(
                            http_client=api.client,
                            payload=payload,
                            tiflux_base_url=tiflux_base_url,
                            blobs_dir=os.path.join(data_dir, "blobs"),
                            resource=resource_name,
                        )
                        logger.info(f"Downloaded blobs for {resource_name} page {page_idx}: {downloaded}")

                    # parada heuristica: se vier vazio ou sem itens.
                    if payload is None:
                        stop_resource = True
                        continue
                    if isinstance(payload, list) and not payload:
                        stop_resource = True
                        continue
                    if isinstance(payload, dict):
                        empty_list_detected = False
                        for key in ("data", "items", "results", "content"):
                            if key in payload and isinstance(payload[key], list) and not payload[key]:
                                empty_list_detected = True
                                break
                        if empty_list_detected:
                            stop_resource = True
                            continue

                    # Evita estourar rate-limit em exportes longos.
                    if min_request_interval_seconds > 0:
                        time.sleep(min_request_interval_seconds)

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code if e.response is not None else None
                    body_preview = ""
                    if e.response is not None and e.response.text:
                        body_preview = e.response.text[:500].lower()

                    # Fallback: alguns endpoints não aceitam "limit" ou paginação padrão.
                    if status == 400 and page_idx == 1:
                        fallback_param_sets: list[dict[str, Any]] = []
                        if "limit" in params:
                            p = dict(params)
                            p.pop("limit", None)
                            fallback_param_sets.append(p)
                        fallback_param_sets.append({"range": "0-49"})
                        fallback_param_sets.append({"page": 1})
                        fallback_param_sets.append({})

                        fallback_param_sets = [fp for i, fp in enumerate(fallback_param_sets) if fp not in fallback_param_sets[:i]]
                        fallback_ok = False
                        for fp in fallback_param_sets:
                            try:
                                logger.warning(f"Retrying first page for {resource_name} using fallback params={fp}")
                                payload = _fetch_page(api, path=cap.path, params=fp or {})
                                h = payload_hash(payload)
                                write_json(out_path, payload)
                                upsert_raw_export_page(
                                    engine=engine,
                                    source_system="tiflux",
                                    resource=resource_name,
                                    page=page_idx,
                                    payload_hash=h,
                                    path=out_path,
                                    status="exported",
                                    last_error=None,
                                )
                                exported_at = dt.datetime.utcnow().isoformat()
                                append_manifest_record(
                                    manifest_path=manifest_path,
                                    record={
                                        "resource": resource_name,
                                        "source_id": f"{resource_name}:{page_idx}",
                                        "page": page_idx,
                                        "hash": h,
                                        "exported_at": exported_at,
                                        "path": out_path,
                                    },
                                )
                                fallback_ok = True
                                break
                            except Exception:
                                continue

                        if fallback_ok:
                            continue

                        # Se nem a primeira página funcionou com fallback, este recurso está indisponível
                        # para o formato atual de autenticação/permissão/parâmetros.
                        logger.exception(
                            f"Export failed {resource_name} first page with all fallback params: {e}"
                        )
                        upsert_raw_export_page(
                            engine=engine,
                            source_system="tiflux",
                            resource=resource_name,
                            page=page_idx,
                            payload_hash="__failed__",
                            path=out_path,
                            status="failed",
                            last_error=str(e),
                        )
                        stop_resource = True
                        continue

                    # Compatibilidade antiga: mantém uma tentativa isolada de range para casos residuais.
                    if status == 400 and page_idx == 1 and ("page" in params or "limit" in params):
                        try:
                            logger.warning(
                                f"Retrying first page for {resource_name} using range fallback params={{'range':'0-49'}}"
                            )
                            payload = _fetch_page(api, path=cap.path, params={"range": "0-49"})
                            h = payload_hash(payload)
                            write_json(out_path, payload)
                            upsert_raw_export_page(
                                engine=engine,
                                source_system="tiflux",
                                resource=resource_name,
                                page=page_idx,
                                payload_hash=h,
                                path=out_path,
                                status="exported",
                                last_error=None,
                            )
                            exported_at = dt.datetime.utcnow().isoformat()
                            append_manifest_record(
                                manifest_path=manifest_path,
                                record={
                                    "resource": resource_name,
                                    "source_id": f"{resource_name}:{page_idx}",
                                    "page": page_idx,
                                    "hash": h,
                                    "exported_at": exported_at,
                                    "path": out_path,
                                },
                            )
                            continue
                        except Exception:
                            pass

                    # Algumas rotas do Tiflux retornam 400 ao ultrapassar paginação.
                    # Nesse caso, consideramos fim do recurso (nao erro de migração).
                    pagination_params = {"page", "offset", "skip", "take", "limit"}
                    has_pagination_param = any(k in params for k in pagination_params)
                    looks_like_end_of_pagination = any(
                        token in body_preview for token in ("page", "pagina", "range", "offset", "limit", "out of range")
                    )

                    if status == 400 and page_idx > 1 and has_pagination_param and looks_like_end_of_pagination:
                        logger.warning(
                            f"Stopping resource {resource_name}: pagination ended with HTTP 400 at page {page_idx} params={params}"
                        )
                        stop_resource = True
                        continue

                    logger.exception(f"Export failed {resource_name} page {page_idx}: {e}")
                    upsert_raw_export_page(
                        engine=engine,
                        source_system="tiflux",
                        resource=resource_name,
                        page=page_idx,
                        payload_hash="__failed__",
                        path=out_path,
                        status="failed",
                        last_error=str(e),
                    )
                    if not continue_on_error:
                        raise
                    continue

                except Exception as e:
                    logger.exception(f"Export failed {resource_name} page {page_idx}: {e}")
                    upsert_raw_export_page(
                        engine=engine,
                        source_system="tiflux",
                        resource=resource_name,
                        page=page_idx,
                        payload_hash="__failed__",
                        path=out_path,
                        status="failed",
                        last_error=str(e),
                    )
                    if not continue_on_error:
                        raise
                    continue
    finally:
        api.close()

