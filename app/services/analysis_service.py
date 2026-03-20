from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from loguru import logger

from ..schemas.canonical import Person, Ticket, TicketAttachment
from ..utils.io import ensure_dir, read_json, write_json
from ..utils.validation import looks_like_html


def _iter_items(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(payload, dict):
        return
    for key in ("data", "items", "results", "content"):
        val = payload.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    yield item
            return
    # fallback: assume payload itself is one item
    if all(isinstance(payload.get(k), (str, int, float, bool, dict, list, type(None))) for k in payload.keys()):
        yield payload


_EMAIL_RE = re.compile(r"(?P<email>[\w.+-]+@[\w-]+\.[\w.-]+)")


def _extract_str(item: dict[str, Any], keys: list[str]) -> str | None:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_id(item: dict[str, Any], keys: list[str]) -> str | None:
    for k in keys:
        v = item.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return None


def _normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    m = _EMAIL_RE.search(email)
    if not m:
        return None
    return m.group("email").lower()


def analyze_data(*, data_dir: str) -> None:
    raw_dir = os.path.join(data_dir, "raw")
    processed_dir = os.path.join(data_dir, "processed")
    reports_dir = os.path.join(data_dir, "reports")
    ensure_dir(processed_dir)
    ensure_dir(reports_dir)

    resources = [
        "clients",
        "contacts",
        "tickets",
        "ticket_history",
        "ticket_files",
        "contracts",
        "addresses",
        "technical_groups",
    ]

    persons: list[Person] = []
    tickets: list[Ticket] = []
    orphan_attachments: list[TicketAttachment] = []

    tickets_without_requester: list[str] = []
    tickets_without_owner: list[str] = []
    tickets_missing_priority: list[str] = []
    unknown_status: list[str] = []
    html_fields_hits: list[str] = []
    invalid_dates: list[str] = []

    email_counts: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()

    # Load JSON pages per resource.
    for resource in resources:
        rdir = os.path.join(raw_dir, resource)
        if not os.path.isdir(rdir):
            continue

        page_files = sorted([f for f in os.listdir(rdir) if f.startswith("page_") and f.endswith(".json")])
        for pf in page_files:
            payload = read_json(os.path.join(rdir, pf))
            for item in _iter_items(payload):
                if resource in {"clients", "contacts"}:
                    source_id = _extract_id(item, ["id", "clientId", "contactId", "uuid", "externalId"]) or "unknown"
                    name = _extract_str(item, ["name", "fullName", "displayName"])
                    email = _normalize_email(_extract_str(item, ["email", "mail", "primaryEmail"]))
                    phone = _extract_str(item, ["phone", "mobile", "tel"])
                    email_counts[email or ""] += 1 if email else 0
                    # HTML scan em campos string.
                    for k, v in item.items():
                        if isinstance(v, str) and looks_like_html(v):
                            html_fields_hits.append(f"{resource}.{source_id}.{k}")
                    persons.append(Person(source_id=source_id, name=name, email=email, phone=phone))

                if resource == "tickets":
                    source_id = _extract_id(item, ["id", "ticketId", "uuid", "externalId"]) or "unknown"
                    requester_id = _extract_id(item, ["requesterId", "requester_id", "requester", "clientId"])
                    owner_id = _extract_id(item, ["ownerId", "owner_id", "assigneeId", "assignee_id", "owner"])
                    priority = _extract_str(item, ["priority", "priority_name", "priorityLabel"])
                    status = _extract_str(item, ["status", "status_name", "ticketStatus"])

                    if not requester_id:
                        tickets_without_requester.append(source_id)
                    if not owner_id:
                        tickets_without_owner.append(source_id)
                    if not priority:
                        tickets_missing_priority.append(source_id)
                    if not status:
                        unknown_status.append(source_id)
                    else:
                        status_counter[status] += 1

                    # HTML scan (campos relevantes).
                    for k, v in item.items():
                        if isinstance(v, str) and looks_like_html(v):
                            html_fields_hits.append(f"tickets.{source_id}.{k}")

                    tickets.append(
                        Ticket(
                            source_id=source_id,
                            requester_id=requester_id,
                            owner_id=owner_id,
                            priority=priority,
                            status=status,
                            subject=_extract_str(item, ["subject", "title", "summary"]),
                            raw=item,
                        )
                    )

                    # Datas inválidas (heuristica: campos com "date"/"at" e sem ISO)
                    for k, v in item.items():
                        if not isinstance(v, str):
                            continue
                        if "date" in k.lower() or k.lower().endswith("_at") or k.lower().endswith("at"):
                            try:
                                dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
                            except Exception:
                                invalid_dates.append(f"tickets.{source_id}.{k}")

    # Duplicidade por email (somente emails nao vazios).
    duplicates = {email: cnt for email, cnt in email_counts.items() if email and cnt > 1}

    catalog_summary = {
        "generated_at": dt.datetime.utcnow().isoformat(),
        "counts": {
            "persons": len(persons),
            "tickets": len(tickets),
            "duplicates_by_email": len(duplicates),
        },
        "issues": {
            "tickets_without_requester": len(tickets_without_requester),
            "tickets_without_owner": len(tickets_without_owner),
            "tickets_missing_priority": len(tickets_missing_priority),
            "unknown_status": len(unknown_status),
            "html_fields_hits": len(html_fields_hits),
            "invalid_dates": len(invalid_dates),
        },
        "status_distribution": dict(status_counter.most_common(10)),
    }

    write_json(os.path.join(processed_dir, "catalog_summary.json"), catalog_summary)

    # Relatorios (markdown).
    profile_path = os.path.join(reports_dir, "data_profile.md")
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write("# Data profile\n\n")
        f.write(f"- Generated at: {catalog_summary['generated_at']}\n")
        f.write(f"- Persons detected: {len(persons)}\n")
        f.write(f"- Tickets detected: {len(tickets)}\n")
        f.write(f"- Duplicate emails: {len(duplicates)}\n\n")
        f.write("## Top status distribution\n\n")
        for status, cnt in status_counter.most_common(15):
            f.write(f"- `{status}`: {cnt}\n")

    quality_path = os.path.join(reports_dir, "data_quality.md")
    with open(quality_path, "w", encoding="utf-8") as f:
        f.write("# Data quality\n\n")
        f.write(f"- Tickets without requester: {len(tickets_without_requester)}\n")
        f.write(f"- Tickets without owner: {len(tickets_without_owner)}\n")
        f.write(f"- Tickets missing priority: {len(tickets_missing_priority)}\n")
        f.write(f"- Unknown/empty status: {len(unknown_status)}\n")
        f.write(f"- HTML field hits: {len(html_fields_hits)}\n")
        f.write(f"- Invalid date strings: {len(invalid_dates)}\n\n")

        if duplicates:
            f.write("## Duplicate emails (top)\n\n")
            for email, cnt in sorted(duplicates.items(), key=lambda x: x[1], reverse=True)[:30]:
                f.write(f"- `{email}`: {cnt}\n")

        if tickets_without_requester:
            f.write("\n## Tickets without requester (sample)\n\n")
            for tid in tickets_without_requester[:30]:
                f.write(f"- `{tid}`\n")

    logger.info("Analysis completed.")

