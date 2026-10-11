"""FastAPI application factory for the Kaizen web dashboard."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from kaizen.db.session import init_db
from kaizen.web.routes.dashboard import router as dashboard_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Kaizen - The System Dashboard", version="0.1.0", lifespan=lifespan)

    app.include_router(dashboard_router)

    # Serve static files (CSS, etc.)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = create_app()
