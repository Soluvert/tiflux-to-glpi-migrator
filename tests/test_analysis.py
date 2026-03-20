import json
import os
from pathlib import Path

from app.services.analysis_service import analyze_data


def test_analysis_detects_duplicate_emails_and_missing_fields(tmp_path: Path):
    data_dir = tmp_path / "data"
    raw_clients = data_dir / "raw" / "clients"
    raw_tickets = data_dir / "raw" / "tickets"
    raw_clients.mkdir(parents=True, exist_ok=True)
    raw_tickets.mkdir(parents=True, exist_ok=True)

    clients_payload = {
        "data": [
            {"id": "c1", "name": "Alice", "email": "alice@example.com"},
            {"id": "c2", "name": "Bob", "email": "alice@example.com"},
        ]
    }
    (raw_clients / "page_1.json").write_text(json.dumps(clients_payload), encoding="utf-8")

    tickets_payload = {
        "data": [
            {
                "id": "t1",
                "requesterId": None,
                "ownerId": "u1",
                "priority": "high",
                "status": "open",
                "subject": "S1",
                "description": "<b>HTML</b>",
                "updated_at": "not-iso",
            },
            {
                "id": "t2",
                "requesterId": "u2",
                "ownerId": None,
                "priority": None,
                "status": None,
                "subject": "S2",
            },
        ]
    }
    (raw_tickets / "page_1.json").write_text(json.dumps(tickets_payload), encoding="utf-8")

    analyze_data(data_dir=str(data_dir))

    summary = json.loads((data_dir / "processed" / "catalog_summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["persons"] == 2
    assert summary["issues"]["tickets_without_requester"] == 1
    assert summary["issues"]["tickets_without_owner"] == 1
    assert summary["issues"]["tickets_missing_priority"] == 1
    assert summary["issues"]["unknown_status"] == 1
    assert summary["counts"]["duplicates_by_email"] == 1

