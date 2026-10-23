from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.generation_store import JsonGenerationStore
from app.llm_service import (
    PROMPT_VERSION,
    LLMGenerationError,
    LLMUnavailableError,
    generate_test_cases,
    new_generation_id,
)
from app.models import DocumentVersion, Selection, SelectionItem
from app.schemas import GenerationRequest, GenerationResponse
from app.staleness import annotate_cases, assess_generation_staleness

router = APIRouter(tags=["generations"])


def _store(request: Request) -> JsonGenerationStore:
    return request.app.state.generation_store


def _response(db: Session, generation: dict, reused: bool = False) -> dict:
    return {
        "generation_id": generation["generation_id"],
        "selection_id": generation["selection_id"],
        "created_at": generation["created_at"],
        "model": generation["model"],
        "reused": reused,
        "source_version_id": generation["source_version_id"],
        "source_version_number": generation["source_version_number"],
        "source_refs": generation["source_refs"],
        "test_cases": annotate_cases(db, generation),
        "staleness": assess_generation_staleness(db, generation),
    }


@router.post("/generations", response_model=GenerationResponse)
async def create_generation(
    payload: GenerationRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    selection = db.get(Selection, payload.selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Selection not found.")
    version = db.get(DocumentVersion, selection.version_id)
    if version is None:
        raise HTTPException(status_code=500, detail="Selection version is missing.")
    items = list(
        db.scalars(
            select(SelectionItem)
            .where(SelectionItem.selection_id == selection.id)
            .order_by(SelectionItem.id)
        )
    )
    if not items:
        raise HTTPException(status_code=422, detail="Selection has no source nodes.")
    store = _store(request)
    active_settings = getattr(request.app.state, "settings", settings)
    model = active_settings.gemini_model
    if not payload.regenerate:
        existing = store.find_reusable(selection.id, model, PROMPT_VERSION)
        if existing:
            return _response(db, existing, reused=True)
    refs = [
        {
            "node_id": item.node_id,
            "logical_id": item.logical_id,
            "version_id": item.version_id,
            "content_hash": item.content_hash,
        }
        for item in items
    ]
    sources = [
        {
            "section_number": item.section_number,
            "heading": item.heading_snapshot,
            "text": item.text_snapshot,
            "source_ref": refs[index],
        }
        for index, item in enumerate(items)
    ]
    try:
        validated, raw_output = await generate_test_cases(
            sources=sources,
            api_key=active_settings.gemini_api_key,
            model=model,
            max_retries=active_settings.llm_max_retries,
            client=getattr(request.app.state, "llm_client", None),
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMGenerationError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "selection_id": selection.id,
                "stored": False,
            },
        ) from exc
    record = {
        "generation_id": new_generation_id(),
        "selection_id": selection.id,
        "document_id": selection.document_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "source_version_id": version.id,
        "source_version_number": version.version_number,
        "source_refs": refs,
        "source_snapshots": sources,
        "test_cases": [case.model_dump(mode="json") for case in validated.test_cases],
        "raw_model_output": raw_output,
    }
    try:
        store.save(record)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Validated LLM output could not be persisted: {exc}",
        ) from exc
    return _response(db, record)


@router.get("/generations", response_model=list[GenerationResponse])
def retrieve_generations(
    request: Request,
    selection_id: str | None = None,
    node_id: str | None = None,
    db: Session = Depends(get_db),
):
    if not selection_id and not node_id:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of selection_id or node_id.",
        )
    records = _store(request).all()
    matching = []
    for generation in records:
        if selection_id and generation.get("selection_id") != selection_id:
            continue
        if node_id:
            refs = generation.get("source_refs", [])
            if not any(
                node_id in (ref.get("node_id"), ref.get("logical_id")) for ref in refs
            ):
                continue
        matching.append(generation)
    matching.sort(key=lambda record: record["created_at"], reverse=True)
    return [_response(db, record) for record in matching]


@router.get("/generations/{generation_id}", response_model=GenerationResponse)
def retrieve_generation(
    generation_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    generation = _store(request).get(generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="Generation not found.")
    return _response(db, generation)
