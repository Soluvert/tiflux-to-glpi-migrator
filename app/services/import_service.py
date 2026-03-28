from __future__ import annotations

import os
import subprocess
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
    tasks_created: int = 0
    tasks_skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class IdMapping:
    """Mapeia IDs do Tiflux para IDs do GLPI."""
    organizations: dict[str, int] = field(default_factory=dict)
    persons: dict[str, int] = field(default_factory=dict)
    queues: dict[str, int] = field(default_factory=dict)
    tickets: dict[str, int] = field(default_factory=dict)
    subcategories: dict[str, int] = field(default_factory=dict)


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
            subcategories=data.get("subcategories", {}),
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
        "subcategories": mapping.subcategories,
    })


def import_to_glpi(
    *,
    data_dir: str,
    glpi_base_url: str,
    glpi_user: str,
    glpi_password: str,
    glpi_user_token: str | None = None,
    glpi_app_token: str | None = None,
    glpi_db_host: str = "db",
    glpi_db_port: int = 3306,
    glpi_db_name: str = "glpi",
    glpi_db_user: str = "glpi",
    glpi_db_password: str = "glpi",
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
            _import_service_catalog_hierarchy(
                client, canonical.get("tickets", []), id_mapping, stats,
            )

        name_to_glpi = _build_name_to_glpi_map(canonical.get("persons", []), id_mapping)
        _import_tickets(client, canonical.get("tickets", []), id_mapping, stats, mapping_config, name_to_glpi=name_to_glpi)

        _import_worked_hours(client, canonical.get("tickets", []), id_mapping, stats)

        save_id_mapping(data_dir, id_mapping)

        _fix_post_import_via_sql(
            canonical.get("tickets", []), id_mapping, canonical.get("persons", []),
            db_host=glpi_db_host, db_port=glpi_db_port,
            db_name=glpi_db_name, db_user=glpi_db_user, db_password=glpi_db_password,
        )

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


def _build_name_to_glpi_map(persons: list[dict], mapping: IdMapping) -> dict[str, int]:
    """Build a case-insensitive author name -> GLPI user ID map."""
    name_map: dict[str, int] = {}
    for p in persons:
        name = (p.get("name") or "").strip()
        glpi_id = mapping.persons.get(p.get("source_id", ""))
        if name and glpi_id:
            name_map[name.lower()] = glpi_id
    return name_map


def _import_service_catalog_hierarchy(
    client: GlpiLegacyApiClient,
    tickets: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
) -> None:
    """Create sub-categories from services_catalog hierarchy under desk categories."""
    # Collect unique (desk_id, area_name, item_name) combos
    combos: dict[tuple[str, str, str], str] = {}  # (desk_id, area, item) -> desk_name
    for t in tickets:
        raw = t.get("raw", {})
        desk = raw.get("desk") or {}
        sc = raw.get("services_catalog")
        desk_id = str(desk.get("id", ""))
        if not desk_id or not sc or not isinstance(sc, dict):
            continue
        area = sc.get("area_name", "")
        item = sc.get("item_name", "")
        if area:
            combos[(desk_id, area, item)] = desk.get("name", "")

    if not combos:
        return

    # Create area sub-categories under desk, then item sub-categories under area
    area_cache: dict[tuple[str, str], int] = {}  # (desk_id, area) -> glpi_id

    for (desk_id, area, item), desk_name in combos.items():
        parent_desk_glpi = mapping.queues.get(desk_id, 0)
        if not parent_desk_glpi:
            continue

        # Create area-level category
        area_key = (desk_id, area)
        if area_key not in area_cache:
            cache_key = f"area:{desk_id}:{area}"
            if cache_key in mapping.subcategories:  # type: ignore[attr-defined]
                area_cache[area_key] = mapping.subcategories[cache_key]  # type: ignore[attr-defined]
            else:
                try:
                    area_id = client.find_or_create_category(area, parent_id=parent_desk_glpi)
                    if area_id:
                        area_cache[area_key] = area_id
                        mapping.subcategories[cache_key] = area_id  # type: ignore[attr-defined]
                        stats.categories_created += 1
                except Exception as e:
                    logger.warning(f"Failed area category {area}: {e}")
                    continue

        area_glpi = area_cache.get(area_key, 0)
        if not area_glpi or not item:
            continue

        # Create item-level category
        item_cache_key = f"item:{desk_id}:{area}:{item}"
        if item_cache_key in mapping.subcategories:  # type: ignore[attr-defined]
            continue
        try:
            item_id = client.find_or_create_category(item, parent_id=area_glpi)
            if item_id:
                mapping.subcategories[item_cache_key] = item_id  # type: ignore[attr-defined]
                stats.categories_created += 1
        except Exception as e:
            logger.warning(f"Failed item category {item}: {e}")

    logger.info(f"Service catalog hierarchy: {len(area_cache)} areas, {len(mapping.subcategories)} total subcategories")  # type: ignore[attr-defined]


def _import_tickets(
    client: GlpiLegacyApiClient,
    tickets: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
    config: MappingConfig,
    *,
    name_to_glpi: dict[str, int] | None = None,
) -> None:
    """Importa tickets no GLPI."""
    name_map = name_to_glpi or {}
    for ticket_data in tickets:
        source_id = ticket_data.get("source_id")
        if not source_id:
            continue

        if source_id in mapping.tickets:
            stats.tickets_skipped += 1
            continue

        try:
            ticket = Ticket(**ticket_data)

            # Entity linking is handled post-import via SQL to avoid API permission issues
            entity_id = 0

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
                # Try to use most specific subcategory from services_catalog
                sc = (ticket.raw or {}).get("services_catalog")
                if sc and isinstance(sc, dict):
                    desk_id = str(((ticket.raw or {}).get("desk") or {}).get("id", ""))
                    area = sc.get("area_name", "")
                    item = sc.get("item_name", "")
                    subcats = mapping.subcategories
                    # Prefer item > area > desk
                    item_key = f"item:{desk_id}:{area}:{item}"
                    area_key = f"area:{desk_id}:{area}"
                    if item and item_key in subcats:
                        category_id = subcats[item_key]
                    elif area and area_key in subcats:
                        category_id = subcats[area_key]

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

                # Import answers as followups
                answers = ticket.raw.get("answers", [])
                if isinstance(answers, list):
                    for ans in sorted(answers, key=lambda a: a.get("answer_time", "")):
                        ans_content = ans.get("name", "")
                        author = ans.get("author", "")
                        ans_time = ans.get("answer_time")
                        if not ans_content:
                            continue
                        followup_content = f"<strong>{author}</strong><br>{ans_content}" if author else ans_content
                        followup_date = ans_time.replace("Z", "").replace("T", " ")[:19] if ans_time else None
                        author_glpi_id = name_map.get((author or "").strip().lower())
                        try:
                            client.create_followup(glpi_id, followup_content, date=followup_date, users_id=author_glpi_id)
                        except Exception as fe:
                            logger.warning(f"Failed followup for ticket #{source_id}: {fe}")

        except Exception as e:
            stats.tickets_failed += 1
            stats.errors.append(f"Ticket {source_id}: {e}")
            logger.error(f"Failed to create ticket {source_id}: {e}")


def _import_worked_hours(
    client: GlpiLegacyApiClient,
    tickets: list[dict],
    mapping: IdMapping,
    stats: ImportStats,
) -> None:
    """Import worked_hours as TicketTasks."""
    for ticket_data in tickets:
        source_id = ticket_data.get("source_id")
        glpi_id = mapping.tickets.get(source_id or "")
        if not glpi_id:
            continue
        raw = ticket_data.get("raw", {})
        wh = raw.get("worked_hours", "00:00")
        if not wh or wh == "00:00":
            continue
        # Parse HH:MM to seconds
        try:
            parts = wh.split(":")
            hours = int(parts[0])
            minutes = int(parts[1]) if len(parts) > 1 else 0
            seconds = hours * 3600 + minutes * 60
        except (ValueError, IndexError):
            continue
        if seconds <= 0:
            continue

        # Resolve tech user (responsible)
        tech_id = None
        responsible = raw.get("responsible") or {}
        if responsible.get("id"):
            tech_id = mapping.persons.get(str(responsible["id"]))

        created_at = raw.get("created_at", "")
        task_date = created_at.replace("Z", "").replace("T", " ")[:19] if created_at else None

        try:
            client.create_ticket_task(
                glpi_id,
                "Tempo trabalhado importado do Tiflux",
                actiontime=seconds,
                date=task_date,
                users_id_tech=tech_id,
            )
            stats.tasks_created += 1
        except Exception as e:
            stats.tasks_skipped += 1
            logger.warning(f"Failed task for ticket #{source_id}: {e}")

    logger.info(f"TicketTasks: {stats.tasks_created} created, {stats.tasks_skipped} skipped")


def _fix_post_import_via_sql(
    tickets: list[dict],
    mapping: IdMapping,
    persons: list[dict] | None = None,
    *,
    db_host: str = "db",
    db_port: int = 3306,
    db_name: str = "glpi",
    db_user: str = "glpi",
    db_password: str = "glpi",
) -> None:
    """Post-import SQL fixes: dates, authors, technician profiles, entity linking, satisfaction."""
    # Build name -> GLPI user ID map for ticket creator attribution
    name_to_glpi: dict[str, int] = {}
    if persons:
        for p in persons:
            name = (p.get("name") or "").strip()
            glpi_id = mapping.persons.get(p.get("source_id", ""))
            if name and glpi_id:
                name_to_glpi[name.lower()] = glpi_id

    statements: list[str] = []

    # --- 1. Fix dates and authors on tickets ---
    for ticket_data in tickets:
        source_id = ticket_data.get("source_id")
        if not source_id:
            continue
        glpi_id = mapping.tickets.get(source_id)
        if not glpi_id:
            continue
        raw = ticket_data.get("raw", {})
        updated_at = raw.get("updated_at")
        if not updated_at:
            continue
        date_mod = updated_at.replace("Z", "").replace("T", " ")[:19]
        created_at = raw.get("created_at", "")
        date_creation = created_at.replace("Z", "").replace("T", " ")[:19] if created_at else date_mod

        # Resolve ticket creator: requestor or responsible
        creator_id = 0
        requestor = raw.get("requestor") or {}
        req_name = (requestor.get("name") or "").strip()
        if req_name:
            creator_id = name_to_glpi.get(req_name.lower(), 0)
        if not creator_id:
            responsible = raw.get("responsible") or {}
            resp_name = (responsible.get("name") or "").strip()
            if resp_name:
                creator_id = name_to_glpi.get(resp_name.lower(), 0)

        set_parts = [f"date_mod='{date_mod}'", f"date_creation='{date_creation}'"]
        if creator_id:
            set_parts.append(f"users_id_recipient={creator_id}")
        statements.append(
            f"UPDATE glpi_tickets SET {', '.join(set_parts)} WHERE id={glpi_id};"
        )

    # --- 2. Promote technicians (assigned users) to Technician profile (id=6) ---
    statements.append(
        "UPDATE glpi_profiles_users SET profiles_id=6 WHERE users_id IN "
        "(SELECT DISTINCT users_id FROM glpi_tickets_users WHERE type=2) "
        "AND profiles_id=1;"
    )

    # --- 3. Link tickets to entities ---
    for ticket_data in tickets:
        source_id = ticket_data.get("source_id")
        if not source_id:
            continue
        glpi_id = mapping.tickets.get(source_id)
        if not glpi_id:
            continue
        org_id = ticket_data.get("organization_id")
        if not org_id:
            continue
        entity_id = mapping.organizations.get(org_id, 0)
        if entity_id:
            statements.append(
                f"UPDATE glpi_tickets SET entities_id={entity_id} WHERE id={glpi_id};"
            )

    # --- 4. Import feedback as ticket satisfaction ---
    for ticket_data in tickets:
        source_id = ticket_data.get("source_id")
        if not source_id:
            continue
        glpi_id = mapping.tickets.get(source_id)
        if not glpi_id:
            continue
        raw = ticket_data.get("raw", {})
        feedback = raw.get("feedback")
        if not feedback or not isinstance(feedback, dict):
            continue
        rating = feedback.get("rating")
        if not rating or not isinstance(rating, (int, float)) or rating <= 0:
            continue
        comment = (feedback.get("comments") or "").replace("'", "\\'")
        updated_at = raw.get("updated_at", "")
        date_answered = updated_at.replace("Z", "").replace("T", " ")[:19] if updated_at else "NOW()"
        date_str = f"'{date_answered}'" if updated_at else "NOW()"
        statements.append(
            f"INSERT IGNORE INTO glpi_ticketsatisfactions "
            f"(tickets_id, type, date_begin, date_answered, satisfaction, comment) "
            f"VALUES ({glpi_id}, 2, {date_str}, {date_str}, {int(rating)}, '{comment}');"
        )

    if not statements:
        return

    sql = "\n".join(statements)
    try:
        result = subprocess.run(
            [
                "mysql",
                f"-h{db_host}",
                f"-P{db_port}",
                f"-u{db_user}",
                f"-p{db_password}",
                "--skip-ssl",
                db_name,
            ],
            input=sql,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info(f"Post-import SQL: {len(statements)} statements executed")
        else:
            logger.warning(f"Post-import SQL failed: {result.stderr[:300]}")
    except Exception as e:
        logger.warning(f"Could not run post-import SQL: {e}")


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
        f.write(f"- Tasks created: {stats.tasks_created}\n")
        f.write(f"- Tasks skipped: {stats.tasks_skipped}\n")

        if stats.errors:
            f.write("\n## Errors\n\n")
            for err in stats.errors[:50]:
                f.write(f"- {err}\n")
            if len(stats.errors) > 50:
                f.write(f"\n... and {len(stats.errors) - 50} more errors\n")

    logger.info(f"Import report saved to {report_path}")
