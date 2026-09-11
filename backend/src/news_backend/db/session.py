"""Explicit engine and short-lived session factories."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from .config import database_url


def make_engine(url: str | URL | None = None) -> Engine:
    return create_engine(database_url(url), pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)
