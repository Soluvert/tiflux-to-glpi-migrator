from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..schemas.canonical import Organization, Person, Ticket, Queue

if TYPE_CHECKING:
    from .mapping_loader import MappingConfig


GLPI_STATUS_MAP = {
    "new": 1,
    "open": 1,
    "in_progress": 2,
    "pending": 4,
    "resolved": 5,
    "closed": 6,
}

GLPI_PRIORITY_MAP = {
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}

GLPI_TYPE_MAP = {
    "incident": 1,
    "request": 2,
}


def map_organization_to_glpi_entity(org: Organization) -> dict[str, Any]:
    """Mapeia Organization para payload de Entity no GLPI."""
    return {
        "name": org.name or f"Org-{org.source_id}",
        "comment": f"Importado do Tiflux. ID original: {org.source_id}",
    }


def map_person_to_glpi_contact(person: Person) -> dict[str, Any]:
    """Mapeia Person para payload de Contact no GLPI (não User)."""
    return {
        "name": person.name or f"Contact-{person.source_id}",
        "email": person.email,
        "phone": person.phone,
        "comment": f"Importado do Tiflux. ID original: {person.source_id}",
    }


def map_person_to_glpi_user(person: Person) -> dict[str, Any]:
    """Mapeia Person para payload de User no GLPI (técnicos)."""
    name = person.name or f"user-{person.source_id}"
    login = (person.email or name).split("@")[0].lower().replace(" ", ".")
    return {
        "name": login,
        "realname": name.split()[-1] if " " in name else name,
        "firstname": name.split()[0] if " " in name else "",
        "email": person.email,
        "phone": person.phone,
        "comment": f"Importado do Tiflux. ID original: {person.source_id}",
    }


def map_queue_to_glpi_category(queue: Queue) -> dict[str, Any]:
    """Mapeia Queue/Desk para ITILCategory no GLPI."""
    return {
        "name": queue.name or f"Cat-{queue.source_id}",
        "is_incident": 1,
        "is_request": 1,
        "comment": f"Importado do Tiflux. ID original: {queue.source_id}",
    }


def map_ticket_to_glpi(
    ticket: Ticket,
    *,
    entity_id: int = 0,
    requester_user_id: int | None = None,
    assign_user_id: int | None = None,
    observer_user_ids: list[int] | None = None,
    category_id: int | None = None,
    mapping_config: "MappingConfig | None" = None,
) -> dict[str, Any]:
    """Mapeia Ticket canônico para payload de Ticket no GLPI."""
    raw = ticket.raw or {}
    
    desk = raw.get("desk") or {}
    desk_name = desk.get("name", "").lower()
    ticket_type = 1 if "incid" in desk_name else 2
    
    created_at = ticket.created_at or raw.get("created_at")
    content_parts = [ticket.subject or "Sem descrição"]
    if raw.get("services_catalog"):
        sc = raw["services_catalog"]
        content_parts.append(f"\n\nCatálogo: {sc.get('catalog_name', '')} / {sc.get('area_name', '')} / {sc.get('item_name', '')}")
    if raw.get("created_by_way_of"):
        content_parts.append(f"\nOrigem: {raw['created_by_way_of']}")
    if ticket.stage:
        content_parts.append(f"\nEstágio Tiflux: {ticket.stage}")

    status = GLPI_STATUS_MAP.get(ticket.status or "new", 1)
    priority = GLPI_PRIORITY_MAP.get(ticket.priority or "medium", 3)

    if mapping_config:
        from .mapping_loader import get_glpi_status, get_glpi_priority
        raw_status = raw.get("status", {})
        if isinstance(raw_status, dict):
            status_name = raw_status.get("name", ticket.status or "")
            status = get_glpi_status(status_name, mapping_config)
        priority = get_glpi_priority(ticket.priority, mapping_config)
    
    payload: dict[str, Any] = {
        "name": ticket.subject or f"Ticket #{ticket.source_id}",
        "content": "".join(content_parts),
        "status": status,
        "priority": priority,
        "urgency": priority,
        "impact": 3,
        "type": ticket_type,
        "entities_id": entity_id,
    }
    
    if created_at:
        payload["date"] = created_at.replace("Z", "").replace("T", " ")[:19]
    
    if category_id:
        payload["itilcategories_id"] = category_id
    
    if requester_user_id:
        payload["_users_id_requester"] = requester_user_id
    
    if assign_user_id:
        payload["_users_id_assign"] = assign_user_id
    
    if observer_user_ids:
        payload["_users_id_observer"] = observer_user_ids

    if ticket.sla_info:
        if ticket.sla_info.attend_expiration:
            payload["time_to_own"] = ticket.sla_info.attend_expiration.replace("Z", "").replace("T", " ")[:19]
        if ticket.sla_info.solve_expiration:
            payload["time_to_resolve"] = ticket.sla_info.solve_expiration.replace("Z", "").replace("T", " ")[:19]
    
    return payload


def build_glpi_ticket_followup(
    ticket_id: int,
    content: str,
    is_private: bool = False,
) -> dict[str, Any]:
    """Cria payload para ITILFollowup no GLPI."""
    return {
        "items_id": ticket_id,
        "itemtype": "Ticket",
        "content": content,
        "is_private": 1 if is_private else 0,
    }
