from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None):
    url = database_url or settings.database_url
    options: dict[str, object] = {}
    if url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False}
    if url.startswith("sqlite:") and url.endswith(":memory:"):
        options["poolclass"] = StaticPool
    return create_engine(url, **options)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
