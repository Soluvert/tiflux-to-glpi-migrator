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
from ..utils.io import ensure_dir, read_json, write_json


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
    if style == "offset_page":
        # Tiflux-style: offset é o número da página (1-based), limit é o tamanho.
        limit = cap.pagination.params.get("limit", cap.sample_params.get("limit", 50))
        for page in range(1, max_pages + 1):
            yield {"offset": page, "limit": limit}
    elif style == "page_limit":
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


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=10),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _fetch_page_with_headers(api: TifluxApiClient, *, path: str, params: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    return api.get_json_with_headers(path, params=params or None)


def _export_closed_tickets(
    api: TifluxApiClient,
    cap: EndpointCapability,
    *,
    resource_dir: str,
    engine: Any,
    manifest_path: str,
    resume: bool = False,
    max_pages: int = 2_000,
    min_request_interval_seconds: float = 0.55,
) -> int:
    """Export closed tickets (is_closed=true) as separate page files."""
    total_items: int | None = None
    total_exported = 0
    limit = cap.pagination.params.get("limit", 50) if cap.pagination else 50

    for api_page in range(1, max_pages + 1):
        if total_items is not None and total_exported >= total_items:
            logger.info(f"All {total_items} closed tickets exported")
            break

        existing = get_raw_export_page(
            engine=engine,
            source_system="tiflux",
            resource="tickets_closed",
            page=api_page,
        )
        if resume and existing is not None and existing.status == "exported":
            out_path = os.path.join(resource_dir, f"page_closed_{api_page}.json")
            if os.path.exists(out_path):
                existing_data = read_json(out_path)
                if isinstance(existing_data, list):
                    total_exported += len(existing_data)
            logger.info(f"Skipping exported closed ticket page {api_page}")
            continue

        params = {"offset": api_page, "limit": limit, "is_closed": "true"}
        out_path = os.path.join(resource_dir, f"page_closed_{api_page}.json")

        try:
            payload, resp_headers = _fetch_page_with_headers(api, path=cap.path, params=params)
        except Exception as e:
            logger.warning(f"Failed to fetch closed tickets page {api_page}: {e}")
            break

        if total_items is None:
            x_total = resp_headers.get("x-total-items")
            if x_total and x_total.isdigit():
                total_items = int(x_total)
                logger.info(f"Closed tickets: x-total-items={total_items}")

        if isinstance(payload, list) and not payload:
            break

        h = payload_hash(payload)
        write_json(out_path, payload)

        upsert_raw_export_page(
            engine=engine,
            source_system="tiflux",
            resource="tickets_closed",
            page=api_page,
            payload_hash=h,
            path=out_path,
            status="exported",
            last_error=None,
        )

        exported_at = dt.datetime.utcnow().isoformat()
        append_manifest_record(
            manifest_path=manifest_path,
            record={
                "resource": "tickets",
                "source_id": f"tickets_closed:{api_page}",
                "page": api_page,
                "hash": h,
                "exported_at": exported_at,
                "path": out_path,
            },
        )

        page_items = len(payload) if isinstance(payload, list) else 0
        total_exported += page_items
        logger.info(f"Closed tickets page {api_page} ({page_items} items, total: {total_exported})")

        if min_request_interval_seconds > 0:
            time.sleep(min_request_interval_seconds)

    return total_exported


def _enrich_tickets_with_details(
    api: TifluxApiClient,
    *,
    resource_dir: str,
    tickets_api_path: str,
    min_request_interval_seconds: float = 1.1,
) -> int:
    """Fetch individual ticket details to merge description and extra fields."""
    enriched_count = 0

    for fname in sorted(os.listdir(resource_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(resource_dir, fname)
        page_data = read_json(fpath)
        if not isinstance(page_data, list):
            continue

        modified = False
        for ticket in page_data:
            if not isinstance(ticket, dict):
                continue

            needs_detail = "description" not in ticket
            needs_answers = "answers" not in ticket
            if not needs_detail and not needs_answers:
                continue

            ticket_number = ticket.get("ticket_number")
            if not ticket_number:
                continue

            if needs_detail:
                try:
                    detail = api.get_json(f"{tickets_api_path}/{ticket_number}")
                    if isinstance(detail, dict):
                        for key in ("description", "custom_fields", "equipment",
                                    "feedback", "worked_hours", "url_external_path",
                                    "url_internal_path", "ticket_reference"):
                            if key in detail:
                                ticket[key] = detail[key]
                        modified = True
                        enriched_count += 1
                except Exception as e:
                    logger.warning(f"Failed to enrich ticket #{ticket_number}: {e}")

            if needs_answers:
                try:
                    answers = api.get_json(f"{tickets_api_path}/{ticket_number}/answers")
                    if isinstance(answers, list):
                        ticket["answers"] = answers
                        modified = True
                except Exception as e:
                    logger.warning(f"Failed to fetch answers for ticket #{ticket_number}: {e}")

            if min_request_interval_seconds > 0:
                time.sleep(min_request_interval_seconds)

        if modified:
            write_json(fpath, page_data)
            logger.info(f"Enriched {fname}")

    return enriched_count


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
            total_items: int | None = None
            page_size: int = cap.pagination.params.get("limit", 50) if cap.pagination else 50
            total_exported_items = 0
            for page_idx, params in enumerate(_iter_page_params(cap=cap), start=1):
                if page_idx > max_pages_per_resource:
                    logger.warning(f"Max pages reached for {resource_name}")
                    break
                if stop_resource:
                    break
                # Se já sabemos o total e já exportamos tudo, parar.
                if total_items is not None and total_exported_items >= total_items:
                    logger.info(f"All {total_items} items exported for {resource_name}")
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
                    payload, resp_headers = _fetch_page_with_headers(api, path=cap.path, params=params)

                    # Capturar x-total-items na primeira página para saber quantas páginas existem.
                    if total_items is None:
                        x_total = resp_headers.get("x-total-items")
                        if x_total and x_total.isdigit():
                            total_items = int(x_total)
                            logger.info(f"{resource_name}: x-total-items={total_items}")

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

                    # Contabilizar itens exportados nesta página.
                    if isinstance(payload, list):
                        total_exported_items += len(payload)
                    elif isinstance(payload, dict):
                        for key in ("data", "items", "results", "content"):
                            if key in payload and isinstance(payload[key], list):
                                total_exported_items += len(payload[key])
                                break

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

        # ── Export closed tickets and enrich all tickets with descriptions ──
        if "tickets" in resources:
            tickets_cap = resources["tickets"]
            tickets_dir = os.path.join(raw_dir, _safe_resource_dir("tickets"))

            logger.info("Exporting closed tickets...")
            closed_count = _export_closed_tickets(
                api, tickets_cap,
                resource_dir=tickets_dir,
                engine=engine,
                manifest_path=manifest_path,
                resume=resume,
                min_request_interval_seconds=min_request_interval_seconds,
            )
            logger.info(f"Exported {closed_count} closed tickets")

            logger.info("Enriching tickets with detailed descriptions...")
            enriched = _enrich_tickets_with_details(
                api,
                resource_dir=tickets_dir,
                tickets_api_path=tickets_cap.path,
                min_request_interval_seconds=min_request_interval_seconds,
            )
            logger.info(f"Enriched {enriched} tickets")

    finally:
        api.close()

