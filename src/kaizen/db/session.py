from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from kaizen.config import settings
from kaizen.db.models import Base

# connect_args={"check_same_thread": False} is required for SQLite with multi-threading
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create all tables in the database if they don't exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency for FastAPI or standalone scripts to acquire a db session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
