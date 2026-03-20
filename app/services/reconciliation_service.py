from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..clients.glpi_legacy_api import GlpiLegacyApiClient
from ..utils.io import read_json, ensure_dir


@dataclass
class ReconciliationResult:
    source_tickets: int = 0
    glpi_tickets: int = 0
    matched: int = 0
    missing_in_glpi: list[str] = field(default_factory=list)
    orphaned_in_glpi: list[int] = field(default_factory=list)
    field_mismatches: list[dict] = field(default_factory=list)
    ok: bool = True


def reconcile(
    *,
    data_dir: str,
    glpi_base_url: str,
    glpi_user: str,
    glpi_password: str,
    glpi_user_token: str | None = None,
    glpi_app_token: str | None = None,
) -> ReconciliationResult:
    """Reconcilia dados importados no GLPI com fonte Tiflux."""
    result = ReconciliationResult()

    canonical_path = os.path.join(data_dir, "processed", "canonical_data.json")
    if not os.path.exists(canonical_path):
        logger.error("Canonical data not found. Run transform first.")
        result.ok = False
        return result

    mapping_path = os.path.join(data_dir, "processed", "id_mapping.json")
    if not os.path.exists(mapping_path):
        logger.error("ID mapping not found. Run import first.")
        result.ok = False
        return result

    canonical = read_json(canonical_path)
    id_mapping = read_json(mapping_path)

    source_tickets = canonical.get("tickets", [])
    result.source_tickets = len(source_tickets)
    ticket_mapping = id_mapping.get("tickets", {})

    client = GlpiLegacyApiClient(
        base_url=glpi_base_url,
        user=glpi_user,
        password=glpi_password,
        user_token=glpi_user_token,
        app_token=glpi_app_token,
    )

    try:
        client.init_session()
        logger.info("GLPI session initialized for reconciliation")

        glpi_tickets = _fetch_all_glpi_tickets(client)
        result.glpi_tickets = len(glpi_tickets)
        glpi_ticket_ids = {t["id"] for t in glpi_tickets}
        glpi_tickets_by_id = {t["id"]: t for t in glpi_tickets}

        for source_ticket in source_tickets:
            source_id = source_ticket.get("source_id")
            if not source_id:
                continue

            glpi_id = ticket_mapping.get(source_id)
            if not glpi_id:
                result.missing_in_glpi.append(source_id)
                continue

            if glpi_id not in glpi_ticket_ids:
                result.missing_in_glpi.append(source_id)
                continue

            result.matched += 1

            glpi_ticket = glpi_tickets_by_id.get(glpi_id, {})
            mismatches = _compare_ticket_fields(source_ticket, glpi_ticket)
            if mismatches:
                result.field_mismatches.append({
                    "source_id": source_id,
                    "glpi_id": glpi_id,
                    "mismatches": mismatches,
                })

        mapped_glpi_ids = set(ticket_mapping.values())
        for glpi_id in glpi_ticket_ids:
            if glpi_id not in mapped_glpi_ids:
                result.orphaned_in_glpi.append(glpi_id)

        result.ok = len(result.missing_in_glpi) == 0 and len(result.field_mismatches) == 0

        _save_reconciliation_report(data_dir, result)

    finally:
        client.close()

    return result


def _fetch_all_glpi_tickets(client: GlpiLegacyApiClient) -> list[dict[str, Any]]:
    """Busca todos os tickets do GLPI."""
    all_tickets = []
    start = 0
    batch_size = 50

    while True:
        try:
            batch = client.get_items("Ticket", range_start=start, range_end=start + batch_size - 1)
            if not batch:
                break
            all_tickets.extend(batch)
            if len(batch) < batch_size:
                break
            start += batch_size
        except Exception as e:
            logger.warning(f"Error fetching tickets batch: {e}")
            break

    return all_tickets


def _compare_ticket_fields(source: dict, glpi: dict) -> list[dict]:
    """Compara campos entre ticket fonte e GLPI."""
    mismatches = []

    source_subject = source.get("subject", "")
    glpi_name = glpi.get("name", "")
    if source_subject and glpi_name and source_subject[:50] != glpi_name[:50]:
        mismatches.append({
            "field": "name/subject",
            "source": source_subject[:50],
            "glpi": glpi_name[:50],
        })

    source_status = source.get("status", "")
    glpi_status = glpi.get("status", 0)
    acceptable_statuses = {
        "new": {1},
        "open": {1, 2},
        "in_progress": {2, 3},
        "pending": {4},
        "resolved": {5},
        "closed": {6},
    }
    valid_statuses = acceptable_statuses.get(source_status, {1})
    if glpi_status not in valid_statuses:
        mismatches.append({
            "field": "status",
            "source": source_status,
            "glpi": glpi_status,
            "acceptable": list(valid_statuses),
        })

    return mismatches


def _save_reconciliation_report(data_dir: str, result: ReconciliationResult) -> None:
    """Salva relatório de reconciliação."""
    reports_dir = os.path.join(data_dir, "reports")
    ensure_dir(reports_dir)
    report_path = os.path.join(reports_dir, "reconciliation_report.md")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Reconciliation Report\n\n")
        f.write("## Summary\n\n")
        f.write(f"- Source tickets: {result.source_tickets}\n")
        f.write(f"- GLPI tickets: {result.glpi_tickets}\n")
        f.write(f"- Matched: {result.matched}\n")
        f.write(f"- Missing in GLPI: {len(result.missing_in_glpi)}\n")
        f.write(f"- Orphaned in GLPI: {len(result.orphaned_in_glpi)}\n")
        f.write(f"- Field mismatches: {len(result.field_mismatches)}\n")
        f.write(f"- Status: {'OK' if result.ok else 'ISSUES FOUND'}\n")

        if result.missing_in_glpi:
            f.write("\n## Missing in GLPI\n\n")
            for sid in result.missing_in_glpi[:20]:
                f.write(f"- Source ID: {sid}\n")
            if len(result.missing_in_glpi) > 20:
                f.write(f"\n... and {len(result.missing_in_glpi) - 20} more\n")

        if result.orphaned_in_glpi:
            f.write("\n## Orphaned in GLPI (not in mapping)\n\n")
            for gid in result.orphaned_in_glpi[:20]:
                f.write(f"- GLPI ID: {gid}\n")

        if result.field_mismatches:
            f.write("\n## Field Mismatches\n\n")
            for m in result.field_mismatches[:10]:
                f.write(f"### Ticket {m['source_id']} -> GLPI #{m['glpi_id']}\n")
                for mm in m["mismatches"]:
                    f.write(f"- {mm['field']}: source=`{mm['source']}` vs glpi=`{mm['glpi']}`\n")

    logger.info(f"Reconciliation report saved to {report_path}")
