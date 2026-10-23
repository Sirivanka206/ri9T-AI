from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.models import DocumentVersion, SectionNode, VersionChange
from app.routes.documents import build_node_tree, version_by_number
from app.schemas import NodeTree, SearchResult

router = APIRouter(tags=["nodes"])


@router.get("/nodes/search", response_model=list[SearchResult])
def search_nodes(
    q: str = Query(min_length=1, max_length=300),
    version: int | None = None,
    document_id: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(SectionNode).join(
        DocumentVersion, SectionNode.version_id == DocumentVersion.id
    )
    if document_id:
        selected_version = version_by_number(db, document_id, version)
        if selected_version is None:
            raise HTTPException(status_code=404, detail="Document version not found.")
        query = query.where(SectionNode.version_id == selected_version.id)
    elif version is not None:
        latest_ids = select(DocumentVersion.id).where(
            DocumentVersion.version_number == version
        )
        query = query.where(SectionNode.version_id.in_(latest_ids))
    else:
        current_version = aliased(DocumentVersion)
        latest_number = (
            select(func.max(DocumentVersion.version_number))
            .where(DocumentVersion.document_id == current_version.document_id)
            .correlate(current_version)
            .scalar_subquery()
        )
        query = query.where(
            SectionNode.version_id.in_(
                select(current_version.id).where(
                    current_version.version_number == latest_number
                )
            )
        )
    pattern = f"%{q}%"
    query = query.where(
        or_(SectionNode.heading.ilike(pattern), SectionNode.body_text.ilike(pattern))
    ).order_by(SectionNode.position)
    return list(db.scalars(query))


@router.get("/nodes/{node_id}", response_model=NodeTree)
def get_node(node_id: str, db: Session = Depends(get_db)):
    node = db.get(SectionNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")
    nodes = list(
        db.scalars(
            select(SectionNode)
            .where(SectionNode.version_id == node.version_id)
            .order_by(SectionNode.position)
        )
    )
    all_roots = build_node_tree(nodes)

    def find(current: NodeTree) -> NodeTree | None:
        if current.id == node_id:
            return current
        for child in current.children:
            found = find(child)
            if found:
                return found
        return None

    for root in all_roots:
        found = find(root)
        if found is not None:
            return found
    raise HTTPException(status_code=500, detail="Node exists but could not be assembled.")


@router.get("/nodes/{node_id}/changes")
def get_node_changes(node_id: str, db: Session = Depends(get_db)):
    node = db.get(SectionNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")
    source_version = db.get(DocumentVersion, node.version_id)
    if source_version is None:
        raise HTTPException(status_code=500, detail="Node version is missing.")
    versions = list(
        db.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == source_version.document_id,
                DocumentVersion.version_number > source_version.version_number,
            )
            .order_by(DocumentVersion.version_number)
        )
    )
    own_changes = list(
        db.scalars(
            select(VersionChange).where(
                VersionChange.version_id == node.version_id,
                VersionChange.new_node_id == node.id,
            )
        )
    )
    changes = [
        {
            "version_number": source_version.version_number,
            "status": change.status,
            "old_node_id": change.old_node_id,
            "new_node_id": change.new_node_id,
            "confidence": change.confidence,
            "summary": change.summary,
        }
        for change in own_changes
    ]
    for version in versions:
        rows = list(
            db.scalars(
                select(VersionChange).where(
                    VersionChange.version_id == version.id,
                    VersionChange.logical_id == node.logical_id,
                )
            )
        )
        changes.extend(
            {
                "version_number": version.version_number,
                "status": change.status,
                "old_node_id": change.old_node_id,
                "new_node_id": change.new_node_id,
                "confidence": change.confidence,
                "summary": change.summary,
            }
            for change in rows
        )
    return {
        "node_id": node.id,
        "logical_id": node.logical_id,
        "source_version": source_version.version_number,
        "changes": changes,
    }
