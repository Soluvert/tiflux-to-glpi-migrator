from __future__ import annotations

import os
from typing import Any

from loguru import logger

from ..mappers.tiflux_to_canonical import (
    map_client_to_organization,
    map_ticket_to_canonical,
    extract_unique_persons_from_tickets,
    extract_unique_queues_from_tickets,
)
from ..schemas.canonical import Organization, Person, Ticket, Queue
from ..utils.io import read_json, write_json, ensure_dir


class TransformResult:
    def __init__(self):
        self.organizations: list[Organization] = []
        self.persons: list[Person] = []
        self.queues: list[Queue] = []
        self.tickets: list[Ticket] = []


def transform_tiflux_data(*, data_dir: str) -> TransformResult:
    """Transforma dados brutos do Tiflux em modelo canônico."""
    raw_dir = os.path.join(data_dir, "raw")
    processed_dir = os.path.join(data_dir, "processed")
    ensure_dir(processed_dir)

    result = TransformResult()

    clients_file = os.path.join(raw_dir, "clients", "page_1.json")
    if os.path.exists(clients_file):
        clients_raw = read_json(clients_file)
        if isinstance(clients_raw, list):
            for c in clients_raw:
                org = map_client_to_organization(c)
                result.organizations.append(org)
            logger.info(f"Transformed {len(result.organizations)} organizations")

    all_tickets_raw: list[dict[str, Any]] = []
    tickets_dir = os.path.join(raw_dir, "tickets")
    if os.path.isdir(tickets_dir):
        for fname in sorted(os.listdir(tickets_dir)):
            if fname.endswith(".json"):
                fpath = os.path.join(tickets_dir, fname)
                page_data = read_json(fpath)
                if isinstance(page_data, list):
                    all_tickets_raw.extend(page_data)

    if all_tickets_raw:
        persons_dict = extract_unique_persons_from_tickets(all_tickets_raw)
        result.persons = list(persons_dict.values())
        logger.info(f"Extracted {len(result.persons)} unique persons")

        queues_dict = extract_unique_queues_from_tickets(all_tickets_raw)
        result.queues = list(queues_dict.values())
        logger.info(f"Extracted {len(result.queues)} unique queues")

        for t in all_tickets_raw:
            ticket = map_ticket_to_canonical(t)
            result.tickets.append(ticket)
        logger.info(f"Transformed {len(result.tickets)} tickets")

    canonical_data = {
        "organizations": [o.model_dump() for o in result.organizations],
        "persons": [p.model_dump() for p in result.persons],
        "queues": [q.model_dump() for q in result.queues],
        "tickets": [t.model_dump() for t in result.tickets],
    }
    canonical_path = os.path.join(processed_dir, "canonical_data.json")
    write_json(canonical_path, canonical_data)
    logger.info(f"Canonical data saved to {canonical_path}")

    return result
