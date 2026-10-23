from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import Base, engine
from app.generation_store import JsonGenerationStore
from app.routes import documents, generations, nodes, selections


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        Base.metadata.create_all(bind=api.state.database_engine)
        yield

    api = FastAPI(
        title="Tri9T CT-200 Manual QA API",
        version="1.0.0",
        description=(
            "Versioned, traceable extraction and QA test-idea generation for the "
            "CardioTrack CT-200 manual."
        ),
        lifespan=lifespan,
    )
    api.state.settings = settings
    api.state.database_engine = engine
    api.state.generation_store = JsonGenerationStore(settings.generated_store_path)
    api.include_router(documents.router)
    api.include_router(nodes.router)
    api.include_router(selections.router)
    api.include_router(generations.router)
    return api


app = create_app()
