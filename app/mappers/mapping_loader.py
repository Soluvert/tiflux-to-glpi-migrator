from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml
from loguru import logger


@dataclass
class MappingConfig:
    """Configuração de mapeamento carregada do mapping.yaml."""
    clients_as_entities: bool = True
    clients_as_suppliers: bool = False
    clients_as_contacts_only: bool = False

    status_mapping: dict[str, str] = field(default_factory=dict)
    priority_mapping: dict[str, int] = field(default_factory=dict)

    mesas_use_as: str = "category"

    groups_mapping_enabled: bool = True

    attachments_use_as: str = "document"

    raw: dict[str, Any] = field(default_factory=dict)


def load_mapping_config(data_dir: str) -> MappingConfig:
    """Carrega configuração de mapeamento do arquivo mapping.yaml."""
    config = MappingConfig()

    mapping_paths = [
        os.path.join(data_dir, "..", "mapping.yaml"),
        os.path.join(data_dir, "mapping.yaml"),
        os.path.join(os.getcwd(), "mapping.yaml"),
    ]

    mapping_path = None
    for path in mapping_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            mapping_path = abs_path
            break

    if not mapping_path:
        logger.warning("mapping.yaml not found, using defaults")
        return config

    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        config.raw = raw

        strategy = raw.get("strategy", {})
        config.clients_as_entities = strategy.get("clients_as_entities", True)
        config.clients_as_suppliers = strategy.get("clients_as_suppliers", False)
        config.clients_as_contacts_only = strategy.get("clients_as_contacts_only", False)

        config.status_mapping = raw.get("status_mapping", {})
        config.priority_mapping = raw.get("priority_mapping", {})

        mesas = raw.get("mesas_mapping", {})
        config.mesas_use_as = mesas.get("use_as", "category")

        groups = raw.get("groups_mapping", {})
        config.groups_mapping_enabled = groups.get("technical_groups_to_glpi_groups", True)

        attachments = raw.get("attachments_mapping", {})
        config.attachments_use_as = attachments.get("use_as", "document")

        logger.info(f"Mapping config loaded from {mapping_path}")

    except Exception as e:
        logger.warning(f"Error loading mapping.yaml: {e}, using defaults")

    return config


def get_glpi_status(tiflux_status: str, mapping: MappingConfig) -> int:
    """Retorna o status GLPI correspondente ao status Tiflux."""
    default_map = {
        "New": 1,
        "In progress": 2,
        "Planned": 3,
        "Waiting": 4,
        "Solved": 5,
        "Closed": 6,
    }

    if tiflux_status in mapping.status_mapping:
        glpi_status_name = mapping.status_mapping[tiflux_status]
        return default_map.get(glpi_status_name, 1)

    normalized = tiflux_status.lower()
    if "open" in normalized or "new" in normalized:
        return 1
    if "progress" in normalized or "andamento" in normalized:
        return 2
    if "pend" in normalized or "aguard" in normalized or "wait" in normalized:
        return 4
    if "resolv" in normalized or "solv" in normalized:
        return 5
    if "closed" in normalized or "fechad" in normalized:
        return 6

    return 1


def get_glpi_priority(tiflux_priority: str | int | None, mapping: MappingConfig) -> int:
    """Retorna a prioridade GLPI correspondente à prioridade Tiflux."""
    if tiflux_priority is None:
        return 3

    if isinstance(tiflux_priority, int):
        if tiflux_priority <= 2:
            return 2
        if tiflux_priority <= 4:
            return 3
        return 4

    p = str(tiflux_priority).lower()

    if p in mapping.priority_mapping:
        return mapping.priority_mapping[p]

    if "low" in p or "baix" in p:
        return 2
    if "high" in p or "alt" in p or "urgent" in p:
        return 4
    if "crit" in p:
        return 5

    return 3
