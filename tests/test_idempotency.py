import json

from sqlalchemy import select

from app.db.session import init_db, session_scope
from app.db.models import RawExportPage
from app.repositories.migration_state_repository import upsert_raw_export_page


def test_upsert_raw_export_page_updates_hash_and_attempts(tmp_path):
    db_path = str(tmp_path / "migrator.sqlite")
    engine = init_db(sqlite_path=db_path)

    upsert_raw_export_page(
        engine=engine,
        source_system="tiflux",
        resource="tickets",
        page=1,
        payload_hash="h1",
        path="/tmp/page_1.json",
        status="exported",
        last_error=None,
    )
    upsert_raw_export_page(
        engine=engine,
        source_system="tiflux",
        resource="tickets",
        page=1,
        payload_hash="h2",
        path="/tmp/page_1_v2.json",
        status="exported",
        last_error=None,
    )

    with session_scope(engine=engine) as session:
        row = session.execute(
            select(RawExportPage).where(
                RawExportPage.source_system == "tiflux",
                RawExportPage.resource == "tickets",
                RawExportPage.page == 1,
            )
        ).scalars().first()
        assert row is not None
        assert row.payload_hash == "h2"
        assert row.attempts == 2

