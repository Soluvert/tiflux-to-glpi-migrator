from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..db.models import IdMap, ImportIdempotency
from ..db.session import session_scope


def upsert_import_idempotency(
    *,
    engine,
    source_system: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str | None,
    payload_hash: str,
    status: str,
    last_error: str | None,
):
    with session_scope(engine=engine) as session:
        stmt = select(ImportIdempotency).where(
            ImportIdempotency.source_system == source_system,
            ImportIdempotency.source_type == source_type,
            ImportIdempotency.source_id == source_id,
            ImportIdempotency.target_type == target_type,
        )
        row = session.execute(stmt).scalars().first()
        if row is None:
            row = ImportIdempotency(
                source_system=source_system,
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
                payload_hash=payload_hash,
                status=status,
                attempts=1,
                last_error=last_error,
                last_attempt_at=dt.datetime.utcnow(),
            )
            session.add(row)
            return row

        row.target_id = target_id
        row.payload_hash = payload_hash
        row.status = status
        row.attempts += 1
        row.last_error = last_error
        row.last_attempt_at = dt.datetime.utcnow()
        return row


def get_import_idempotency(
    *,
    engine,
    source_system: str,
    source_type: str,
    source_id: str,
    target_type: str,
):
    with session_scope(engine=engine) as session:
        stmt = select(ImportIdempotency).where(
            ImportIdempotency.source_system == source_system,
            ImportIdempotency.source_type == source_type,
            ImportIdempotency.source_id == source_id,
            ImportIdempotency.target_type == target_type,
        )
        return session.execute(stmt).scalars().first()


def list_items_to_retry(*, engine, source_system: str, target_type: str | None = None):
    with session_scope(engine=engine) as session:
        from ..db.models import ImportIdempotency  # local import para evitar ciclo

        stmt = select(ImportIdempotency).where(ImportIdempotency.status != "imported")
        if target_type is not None:
            stmt = stmt.where(ImportIdempotency.target_type == target_type)
        stmt = stmt.order_by(ImportIdempotency.last_attempt_at.asc().nullsfirst())
        return list(session.execute(stmt).scalars().all())


def upsert_id_map(
    *,
    engine,
    source_system: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
):
    with session_scope(engine=engine) as session:
        stmt = select(IdMap).where(
            IdMap.source_system == source_system,
            IdMap.source_type == source_type,
            IdMap.source_id == source_id,
        )
        row = session.execute(stmt).scalars().first()
        if row is None:
            row = IdMap(
                source_system=source_system,
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
                created_at=dt.datetime.utcnow(),
            )
            session.add(row)
            return row

        row.target_type = target_type
        row.target_id = target_id
        return row

