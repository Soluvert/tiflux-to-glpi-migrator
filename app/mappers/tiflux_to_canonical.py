from __future__ import annotations

from typing import Any

from ..schemas.canonical import (
    Organization,
    Person,
    Ticket,
    Queue,
    SlaInfo,
)


def map_client_to_organization(item: dict[str, Any]) -> Organization:
    return Organization(
        source_id=str(item.get("id", "")),
        name=item.get("name") or item.get("social"),
    )


def map_requestor_to_person(requestor: dict[str, Any] | None) -> Person | None:
    if not requestor:
        return None
    source_id = requestor.get("id") or requestor.get("email") or requestor.get("name")
    if not source_id:
        return None
    return Person(
        source_id=str(source_id),
        name=requestor.get("name"),
        email=requestor.get("email"),
        phone=requestor.get("telephone"),
    )


def map_responsible_to_person(responsible: dict[str, Any] | None) -> Person | None:
    if not responsible:
        return None
    source_id = responsible.get("id")
    if not source_id:
        return None
    return Person(
        source_id=str(source_id),
        name=responsible.get("name"),
    )


def map_desk_to_queue(desk: dict[str, Any] | None) -> Queue | None:
    if not desk:
        return None
    return Queue(
        source_id=str(desk.get("id", "")),
        name=desk.get("name"),
    )


def _normalize_status(status: dict[str, Any] | None, is_closed: bool) -> str:
    if is_closed:
        return "closed"
    if not status:
        return "new"
    name = (status.get("name") or "").lower()
    if "open" in name:
        return "open"
    if "progress" in name or "andamento" in name:
        return "in_progress"
    if "pend" in name or "aguard" in name:
        return "pending"
    if "resolv" in name or "solv" in name:
        return "resolved"
    if "closed" in name or "fechad" in name:
        return "closed"
    return "open"


def _normalize_priority(priority: Any) -> str:
    if priority is None:
        return "medium"
    if isinstance(priority, int):
        if priority <= 2:
            return "low"
        if priority <= 4:
            return "medium"
        return "high"
    p = str(priority).lower()
    if "low" in p or "baix" in p:
        return "low"
    if "high" in p or "alt" in p or "urgent" in p:
        return "high"
    if "crit" in p:
        return "critical"
    return "medium"


def _extract_sla_info(sla_data: dict[str, Any] | None) -> SlaInfo | None:
    if not sla_data:
        return None
    return SlaInfo(
        attend_expiration=sla_data.get("attend_expiration"),
        solve_expiration=sla_data.get("solve_expiration"),
        attend_sla=sla_data.get("attend_sla"),
        solved_in_time=sla_data.get("solved_in_time"),
    )


def _extract_followers(followers_raw: str | list | None) -> list[str]:
    if not followers_raw:
        return []
    if isinstance(followers_raw, str):
        return [f.strip() for f in followers_raw.split(",") if f.strip()]
    if isinstance(followers_raw, list):
        return [str(f) for f in followers_raw if f]
    return []


def map_ticket_to_canonical(item: dict[str, Any]) -> Ticket:
    client = item.get("client") or {}
    requestor = item.get("requestor")
    responsible = item.get("responsible")
    desk = item.get("desk")
    status = item.get("status")
    stage = item.get("stage") or {}
    is_closed = item.get("is_closed", False)

    return Ticket(
        source_id=str(item.get("ticket_number") or item.get("id", "")),
        organization_id=str(client.get("id", "")) if client.get("id") else None,
        requester_id=str(requestor.get("id") or requestor.get("email", "")) if requestor else None,
        owner_id=str(responsible.get("id", "")) if responsible and responsible.get("id") else None,
        queue_id=str(desk.get("id", "")) if desk and desk.get("id") else None,
        status=_normalize_status(status, is_closed),
        stage=stage.get("name") if isinstance(stage, dict) else str(stage) if stage else None,
        priority=_normalize_priority(item.get("priority")),
        subject=item.get("title"),
        created_at=item.get("created_at"),
        sla_info=_extract_sla_info(item.get("sla_info")),
        followers=_extract_followers(item.get("followers")),
        raw=item,
    )


def extract_unique_persons_from_tickets(tickets: list[dict[str, Any]]) -> dict[str, Person]:
    """Extrai pessoas únicas (requestors, responsibles, e followers) dos tickets."""
    persons: dict[str, Person] = {}
    for t in tickets:
        req = map_requestor_to_person(t.get("requestor"))
        if req and req.source_id not in persons:
            persons[req.source_id] = req
        resp = map_responsible_to_person(t.get("responsible"))
        if resp and resp.source_id not in persons:
            persons[resp.source_id] = resp

        followers = _extract_followers(t.get("followers"))
        for follower_email in followers:
            if follower_email and follower_email not in persons:
                persons[follower_email] = Person(
                    source_id=follower_email,
                    email=follower_email,
                    name=follower_email.split("@")[0].replace(".", " ").title(),
                )
    return persons


def extract_unique_queues_from_tickets(tickets: list[dict[str, Any]]) -> dict[str, Queue]:
    """Extrai filas/mesas únicas dos tickets."""
    queues: dict[str, Queue] = {}
    for t in tickets:
        q = map_desk_to_queue(t.get("desk"))
        if q and q.source_id not in queues:
            queues[q.source_id] = q
    return queues
