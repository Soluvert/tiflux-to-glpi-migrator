from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..clients.glpi_legacy_api import GlpiLegacyApiClient
from ..mappers.canonical_to_glpi import map_ticket_to_glpi
from ..mappers.mapping_loader import load_mapping_config, MappingConfig
from ..schemas.canonical import Organization, Person, Ticket, Queue
from ..utils.io import read_json, write_json, ensure_dir


@dataclass
class ImportStats:
    entities_created: int = 0
    entities_skipped: int = 0
    users_created: int = 0
    users_skipped: int = 0
    categories_created: int = 0
    categories_skipped: int = 0
    tickets_created: int = 0
    tickets_skipped: int = 0
    tickets_failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class IdMapping:
    """Mapeia IDs do Tiflux para IDs do GLPI."""
    organizations: dict[str, int] = field(default_factory=dict)
    persons: dict[str, int] = field(default_factory=dict)
    queues: dict[str, int] = field(default_factory=dict)
    tickets: dict[str, int] = field(default_factory=dict)


def load_canonical_data(data_dir: str) -> dict[str, Any]:
    """Carrega dados canônicos transformados."""
    canonical_path = os.path.join(data_dir, "processed", "canonical_data.json")
    if not os.path.exists(canonical_path):
        raise FileNotFoundError(f"Canonical data not found: {canonical_path}. Run transform first.")
    return read_json(canonical_path)


def load_id_mapping(data_dir: str) -> IdMapping:
    """Carrega mapeamento de IDs existente ou retorna vazio."""
    mapping_path = os.path.join(data_dir, "processed", "id_mapping.json")
    if os.path.exists(mapping_path):
        data = read_json(mapping_path)
        return IdMapping(
            organizations=data.get("organizations", {}),
            persons=data.get("persons", {}),
            queues=data.get("queues", {}),
            tickets=data.get("tickets", {}),
        )
    return IdMapping()


def save_id_mapping(data_dir: str, mapping: IdMapping) -> None:
    """Persiste mapeamento de IDs."""
    mapping_path = os.path.join(data_dir, "processed", "id_mapping.json")
    ensure_dir(os.path.dirname(mapping_path))
    write_json(mapping_path, {
        "organizations": mapping.organizations,
        "persons": mapping.persons,
        "queues": mapping.queues,
        "tickets": mapping.tickets,
    })


def import_to_glpi(
    *,
    data_dir: str,
    glpi_base_url: str,
    glpi_user: str,
    glpi_password: str,
    glpi_user_token: str | None = None,
    glpi_app_token: str | None = None,
    dry_run: bool = False,
    skip_entities: bool = False,
    skip_users: bool = False,
    skip_categories: bool = False,
) -> ImportStats:
    """Importa dados canônicos para o GLPI."""
    stats = ImportStats()
    canonical = load_canonical_data(data_dir)
    id_mapping = load_id_mapping(data_dir)
    mapping_config = load_mapping_config(data_dir)

    if dry_run:
        logger.info("DRY RUN: Nenhuma alteração será feita no GLPI")
        stats.tickets_skipped = len(canonical.get("tickets", []))
        return stats

    client = GlpiLegacyApiClient(
        base_url=glpi_base_url,
        user=glpi_user,
        password=glpi_password,
        user_token=glpi_user_token,
        app_token=glpi_app_token,
    )

    try:
        client.init_session()
        logger.info("GLPI session initialized")

        if not skip_entities and mapping_config.clients_as_entities:
            _import_entities(client, canonical.get("organizations", []), id_mapping, stats)

        if not skip_users:
            _import_users(client, canonical.get("persons", []), id_mapping, stats)

        if not skip_categories:
            _import_categories(client, canonical.get("queues", []), id_mapping, stats, mapping_config)

        _import_tickets(client, canonical.get("tickets", []), id_mapping, stats, mapping_config)

        save_id_mapping(data_dir, id_mapping)

        _save_import_report(data_dir, stats, mapping_config)

    finally:
        client.close()

    return stats


def _import_entities(
    client: GlpiLegacyApiClient,
    organizations: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
) -> None:
    """Importa organizações como entidades no GLPI."""
    for org_data in organizations:
        source_id = org_data.get("source_id")
        if not source_id:
            continue

        if source_id in mapping.organizations:
            stats.entities_skipped += 1
            continue

        name = org_data.get("name") or f"Org-{source_id}"
        try:
            glpi_id = client.find_or_create_entity(name)
            if glpi_id:
                mapping.organizations[source_id] = glpi_id
                stats.entities_created += 1
                logger.debug(f"Entity created/found: {name} -> {glpi_id}")
        except Exception as e:
            stats.errors.append(f"Entity {name}: {e}")
            logger.error(f"Failed to create entity {name}: {e}")


def _import_users(
    client: GlpiLegacyApiClient,
    persons: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
) -> None:
    """Importa pessoas como usuários no GLPI."""
    for person_data in persons:
        source_id = person_data.get("source_id")
        if not source_id:
            continue

        if source_id in mapping.persons:
            stats.users_skipped += 1
            continue

        name = person_data.get("name") or f"User-{source_id}"
        email = person_data.get("email")
        try:
            glpi_id = client.find_or_create_user(name, email)
            if glpi_id:
                mapping.persons[source_id] = glpi_id
                stats.users_created += 1
                logger.debug(f"User created/found: {name} -> {glpi_id}")
        except Exception as e:
            stats.errors.append(f"User {name}: {e}")
            logger.error(f"Failed to create user {name}: {e}")


def _import_categories(
    client: GlpiLegacyApiClient,
    queues: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
    config: MappingConfig,
) -> None:
    """Importa filas/mesas como categorias ITIL no GLPI."""
    if config.mesas_use_as != "category":
        logger.info(f"Skipping categories import (mesas_use_as={config.mesas_use_as})")
        return

    for queue_data in queues:
        source_id = queue_data.get("source_id")
        if not source_id:
            continue

        if source_id in mapping.queues:
            stats.categories_skipped += 1
            continue

        name = queue_data.get("name") or f"Queue-{source_id}"
        try:
            glpi_id = client.find_or_create_category(name)
            if glpi_id:
                mapping.queues[source_id] = glpi_id
                stats.categories_created += 1
                logger.debug(f"Category created/found: {name} -> {glpi_id}")
        except Exception as e:
            stats.errors.append(f"Category {name}: {e}")
            logger.error(f"Failed to create category {name}: {e}")


def _import_tickets(
    client: GlpiLegacyApiClient,
    tickets: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
    config: MappingConfig,
) -> None:
    """Importa tickets no GLPI."""
    for ticket_data in tickets:
        source_id = ticket_data.get("source_id")
        if not source_id:
            continue

        if source_id in mapping.tickets:
            stats.tickets_skipped += 1
            continue

        try:
            ticket = Ticket(**ticket_data)

            entity_id = 0
            if ticket.organization_id and config.clients_as_entities:
                entity_id = mapping.organizations.get(ticket.organization_id, 0)

            requester_id = None
            if ticket.requester_id:
                requester_id = mapping.persons.get(ticket.requester_id)

            assign_id = None
            if ticket.owner_id:
                assign_id = mapping.persons.get(ticket.owner_id)

            observer_ids = []
            if ticket.followers:
                for follower_email in ticket.followers:
                    follower_id = mapping.persons.get(follower_email)
                    if follower_id:
                        observer_ids.append(follower_id)

            category_id = None
            if ticket.queue_id and config.mesas_use_as == "category":
                category_id = mapping.queues.get(ticket.queue_id)

            payload = map_ticket_to_glpi(
                ticket,
                entity_id=entity_id,
                requester_user_id=requester_id,
                assign_user_id=assign_id,
                observer_user_ids=observer_ids if observer_ids else None,
                category_id=category_id,
                mapping_config=config,
            )

            glpi_id = client.create_ticket(payload)
            if glpi_id:
                mapping.tickets[source_id] = glpi_id
                stats.tickets_created += 1
                logger.info(f"Ticket #{source_id} -> GLPI #{glpi_id}")

        except Exception as e:
            stats.tickets_failed += 1
            stats.errors.append(f"Ticket {source_id}: {e}")
            logger.error(f"Failed to create ticket {source_id}: {e}")


def _save_import_report(data_dir: str, stats: ImportStats, config: MappingConfig) -> None:
    """Salva relatório da importação."""
    reports_dir = os.path.join(data_dir, "reports")
    ensure_dir(reports_dir)
    report_path = os.path.join(reports_dir, "import_report.md")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Import Report\n\n")
        f.write("## Configuration\n\n")
        f.write(f"- clients_as_entities: {config.clients_as_entities}\n")
        f.write(f"- mesas_use_as: {config.mesas_use_as}\n")
        f.write("\n## Summary\n\n")
        f.write(f"- Entities created: {stats.entities_created}\n")
        f.write(f"- Entities skipped: {stats.entities_skipped}\n")
        f.write(f"- Users created: {stats.users_created}\n")
        f.write(f"- Users skipped: {stats.users_skipped}\n")
        f.write(f"- Categories created: {stats.categories_created}\n")
        f.write(f"- Categories skipped: {stats.categories_skipped}\n")
        f.write(f"- Tickets created: {stats.tickets_created}\n")
        f.write(f"- Tickets skipped: {stats.tickets_skipped}\n")
        f.write(f"- Tickets failed: {stats.tickets_failed}\n")

        if stats.errors:
            f.write("\n## Errors\n\n")
            for err in stats.errors[:50]:
                f.write(f"- {err}\n")
            if len(stats.errors) > 50:
                f.write(f"\n... and {len(stats.errors) - 50} more errors\n")

    logger.info(f"Import report saved to {report_path}")
