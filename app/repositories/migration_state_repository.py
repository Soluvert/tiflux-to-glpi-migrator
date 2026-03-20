from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..db.models import RawExportPage
from ..db.session import session_scope


def get_raw_export_page(*, engine, source_system: str, resource: str, page: int) -> RawExportPage | None:
    with session_scope(engine=engine) as session:
        stmt = select(RawExportPage).where(
            RawExportPage.source_system == source_system,
            RawExportPage.resource == resource,
            RawExportPage.page == page,
        )
        return session.execute(stmt).scalars().first()


def upsert_raw_export_page(
    *,
    engine,
    source_system: str,
    resource: str,
    page: int,
    payload_hash: str,
    path: str,
    status: str,
    last_error: str | None,
):
    with session_scope(engine=engine) as session:
        stmt = select(RawExportPage).where(
            RawExportPage.source_system == source_system,
            RawExportPage.resource == resource,
            RawExportPage.page == page,
        )
        row = session.execute(stmt).scalars().first()
        if row is None:
            row = RawExportPage(
                source_system=source_system,
                resource=resource,
                page=page,
                payload_hash=payload_hash,
                path=path,
                status=status,
                attempts=1,
                last_error=last_error,
                last_attempt_at=dt.datetime.utcnow(),
            )
            session.add(row)
            return

        row.payload_hash = payload_hash
        row.path = path
        row.status = status
        row.attempts += 1
        row.last_error = last_error
        row.last_attempt_at = dt.datetime.utcnow()


def list_failed_raw_pages(*, engine, source_system: str, resource: str | None = None):
    with session_scope(engine=engine) as session:
        stmt = select(RawExportPage).where(RawExportPage.source_system == source_system, RawExportPage.status != "exported")
        if resource is not None:
            stmt = stmt.where(RawExportPage.resource == resource)
        stmt = stmt.order_by(RawExportPage.resource.asc(), RawExportPage.page.asc())
        return list(session.execute(stmt).scalars().all())

