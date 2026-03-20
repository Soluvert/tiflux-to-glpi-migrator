from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from ..clients.tiflux_discovery import TifluxDiscoveryClient
from ..clients.tiflux_api import TifluxApiClient
from ..constants import RAW_SAMPLES_DIR, RESOURCE_CANDIDATES
from ..utils.io import ensure_dir, write_json


def _candidate_base_urls(raw_base_url: str) -> list[str]:
    """
    Gera candidatos de base URL para evitar erro comum:
    usar app.tiflux.com (frontend) em vez da API v2.
    """
    base = raw_base_url.rstrip("/")
    out = [base]

    parsed = urlparse(base)
    host = parsed.netloc.lower()
    scheme = parsed.scheme or "https"

    if host == "app.tiflux.com":
        out.append("https://api.tiflux.com/api/v2")
    elif host.endswith(".tiflux.com") and host.startswith("app."):
        out.append(f"{scheme}://api.tiflux.com/api/v2")

    if "/api/v2" not in base:
        out.append(base + "/api/v2")
    if "/api" not in base:
        out.append(base + "/api")

    # remove duplicatas mantendo ordem.
    uniq = []
    seen: set[str] = set()
    for item in out:
        if item not in seen:
            uniq.append(item)
            seen.add(item)
    return uniq


def _markdown_capabilities(capabilities: Any) -> str:
    base_url = capabilities.get("base_url")
    lines: list[str] = []
    lines.append(f"# Tiflux API capabilities\n")
    lines.append(f"- Discovered at: {capabilities.get('discovered_at')}\n")
    lines.append(f"- Base URL: {base_url}\n")
    lines.append("\n## Resources\n")

    resources: dict[str, Any] = capabilities.get("resources", {})
    unavailable: dict[str, Any] = capabilities.get("unavailable", {})

    for name in sorted(RESOURCE_CANDIDATES):
        if name in resources:
            c = resources[name]
            path = c.get("path")
            pagination = c.get("pagination", {}) if c else {}
            pagination_style = pagination.get("style") if isinstance(pagination, dict) else None
            sample_params = c.get("sample_params") or {}
            lines.append(
                f"\n### `{name}`\n- path: `{path}`\n- pagination: `{pagination_style}`\n- sample_params: `{json.dumps(sample_params, ensure_ascii=True)}`\n"
            )
        elif name in unavailable:
            lines.append(f"\n### `{name}`\n- unavailable\n")
        else:
            lines.append(f"\n### `{name}`\n- not attempted\n")

    return "\n".join(lines).strip() + "\n"


def run_discovery(
    *,
    tiflux_base_url: str,
    tiflux_api_token: str,
    data_dir: str,
    resources: list[str] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    if resources is None:
        resources = list(RESOURCE_CANDIDATES)

    logger.info(f"Starting discovery for {len(resources)} resources...")

    caps_dict: dict[str, Any] | None = None
    for candidate_base in _candidate_base_urls(tiflux_base_url):
        logger.info(f"Trying discovery base URL: {candidate_base}")
        api_client = TifluxApiClient(base_url=candidate_base, api_token=tiflux_api_token)
        discovery_client = TifluxDiscoveryClient(api_client=api_client)
        caps = discovery_client.discover_all(resources=resources)
        current = json.loads(caps.model_dump_json())

        if current.get("resources"):
            caps_dict = current
            # garante base_url efetiva usada
            caps_dict["base_url"] = candidate_base
            break

    if caps_dict is None:
        # última tentativa (sem recursos) para manter relatório consistente.
        caps_dict = current

    # Persistência: relatórios e capacidades em JSON (para export retomar sem re-probe).
    reports_dir = os.path.join(data_dir, "reports")
    ensure_dir(reports_dir)

    processed_dir = os.path.join(data_dir, "processed")
    ensure_dir(processed_dir)

    md_path = os.path.join(reports_dir, "tiflux_api_capabilities.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_markdown_capabilities(caps_dict))

    caps_json_path = os.path.join(processed_dir, "tiflux_api_capabilities.json")
    write_json(caps_json_path, caps_dict)

    # Salva exemplos reais.
    samples_dir = os.path.join(data_dir, "raw", "_samples")
    ensure_dir(samples_dir)

    resources_caps: dict[str, Any] = caps_dict.get("resources", {})
    api_client = TifluxApiClient(base_url=caps_dict.get("base_url", tiflux_base_url), api_token=tiflux_api_token)
    for resource_name, cap in resources_caps.items():
        path = cap.get("path")
        if not path:
            continue
        sample_params = cap.get("sample_params") or {}
        try:
            payload = api_client.get_json(path, params=sample_params or None)
            write_json(os.path.join(samples_dir, f"{resource_name}.json"), payload)
        except Exception as e:  # pragma: no cover
            logger.warning(f"Failed to save sample for {resource_name}: {e}")

    return caps_dict

