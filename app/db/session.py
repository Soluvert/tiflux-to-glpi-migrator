from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .base import Base


def make_engine(*, sqlite_path: str):
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
    return create_engine(f"sqlite:///{sqlite_path}", future=True)


@contextmanager
def session_scope(*, engine) -> Session:
    # Evita DetachedInstanceError ao acessar atributos fora do bloco
    # em leituras simples (ex.: checagem de status para resume).
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(*, sqlite_path: str):
    engine = make_engine(sqlite_path=sqlite_path)
    Base.metadata.create_all(engine)
    return engine

