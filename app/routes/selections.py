from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, DocumentVersion, SectionNode, Selection, SelectionItem
from app.schemas import (
    SelectionCreate,
    SelectionItemResponse,
    SelectionResponse,
)

router = APIRouter(tags=["selections"])


def _selection_response(db: Session, selection: Selection) -> SelectionResponse:
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
    return SelectionResponse(
        id=selection.id,
        name=selection.name,
        document_id=selection.document_id,
        version_id=selection.version_id,
        version_number=version.version_number,
        created_at=selection.created_at.isoformat(),
        items=[
            SelectionItemResponse(
                node_id=item.node_id,
                logical_id=item.logical_id,
                version_id=item.version_id,
                content_hash=item.content_hash,
                heading=item.heading_snapshot,
                text=item.text_snapshot,
                section_number=item.section_number,
            )
            for item in items
        ],
    )


@router.post("/selections", response_model=SelectionResponse, status_code=201)
def create_selection(payload: SelectionCreate, db: Session = Depends(get_db)):
    if len(payload.node_ids) != len(set(payload.node_ids)):
        raise HTTPException(status_code=422, detail="node_ids must not contain duplicates.")
    if db.get(Document, payload.document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    nodes = list(
        db.scalars(select(SectionNode).where(SectionNode.id.in_(payload.node_ids)))
    )
    if len(nodes) != len(payload.node_ids):
        raise HTTPException(status_code=404, detail="One or more nodes were not found.")
    if any(
        db.get(DocumentVersion, node.version_id).document_id != payload.document_id
        for node in nodes
    ):
        raise HTTPException(
            status_code=422,
            detail="Every selected node must belong to the requested document.",
        )
    version_ids = {node.version_id for node in nodes}
    if len(version_ids) != 1:
        raise HTTPException(
            status_code=422,
            detail="A selection must be pinned to nodes from one document version.",
        )
    version_id = next(iter(version_ids))
    selection = Selection(
        name=payload.name,
        document_id=payload.document_id,
        version_id=version_id,
    )
    db.add(selection)
    db.flush()
    for node in nodes:
        exact_text = node.heading
        if node.body_text:
            exact_text = f"{exact_text}\n{node.body_text}"
        db.add(
            SelectionItem(
                selection_id=selection.id,
                node_id=node.id,
                logical_id=node.logical_id,
                version_id=node.version_id,
                content_hash=node.content_hash,
                heading_snapshot=node.heading,
                text_snapshot=exact_text,
                section_number=node.section_number,
            )
        )
    db.commit()
    db.refresh(selection)
    return _selection_response(db, selection)


@router.get("/selections/{selection_id}", response_model=SelectionResponse)
def get_selection(selection_id: str, db: Session = Depends(get_db)):
    selection = db.get(Selection, selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Selection not found.")
    return _selection_response(db, selection)
