import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, DocumentVersion, SectionNode
from app.schemas import DocumentSummary, DocumentVersionSummary, IngestResponse, NodeTree
from app.versioning import ingest_pdf

router = APIRouter(tags=["documents"])


def latest_version(db: Session, document_id: str) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )


def version_by_number(
    db: Session, document_id: str, version_number: int | None
) -> DocumentVersion | None:
    if version_number is None:
        return latest_version(db, document_id)
    return db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )


def build_node_tree(nodes: list[SectionNode]) -> list[NodeTree]:
    output: dict[str, NodeTree] = {}
    roots: list[NodeTree] = []
    for node in sorted(nodes, key=lambda item: item.position):
        response = NodeTree(
            id=node.id,
            logical_id=node.logical_id,
            version_id=node.version_id,
            parent_id=node.parent_id,
            heading=node.heading,
            section_number=node.section_number,
            level=node.level,
            body_text=node.body_text,
            content_hash=node.content_hash,
            content_blocks=node.content_blocks,
            source_pages=node.source_pages,
            change_status=node.change_status,
            children=[],
        )
        output[node.id] = response
        if node.parent_id and node.parent_id in output:
            output[node.parent_id].children.append(response)
        else:
            roots.append(response)
    return roots


@router.get("/documents", response_model=list[DocumentSummary])
def list_documents(db: Session = Depends(get_db)):
    documents = list(db.scalars(select(Document).order_by(Document.created_at)))
    return [
        DocumentSummary(
            id=document.id,
            title=document.title,
            latest_version=(
                document.versions[-1].version_number if document.versions else None
            ),
        )
        for document in documents
    ]


@router.post(
    "/documents/{document_id}/ingest",
    response_model=IngestResponse,
    status_code=201,
)
async def ingest_document(
    document_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a file with a .pdf extension.")
    payload = await file.read(20 * 1024 * 1024 + 1)
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF exceeds the 20 MB upload limit.")
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="Uploaded file does not have a PDF signature.")
    try:
        with tempfile.TemporaryDirectory(prefix="ct200-ingest-") as temporary_directory:
            pdf_path = Path(temporary_directory) / Path(file.filename).name
            pdf_path.write_bytes(payload)
            version, summary = ingest_pdf(
                db, document_id, pdf_path, source_filename=Path(file.filename).name
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    nodes = list(
        db.scalars(
            select(SectionNode).where(SectionNode.version_id == version.id)
        )
    )
    return IngestResponse(
        document_id=document_id,
        version_id=version.id,
        version_number=version.version_number,
        idempotent=summary["idempotent"],
        node_count=len(nodes),
        warnings=version.parser_warnings,
        changes=summary["changes"],
        change_counts=summary["counts"],
    )


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionSummary],
)
def list_versions(document_id: str, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return list(
        db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number)
        )
    )


@router.get("/documents/{document_id}/sections", response_model=NodeTree)
def browse_sections(
    document_id: str,
    version: int | None = None,
    db: Session = Depends(get_db),
):
    selected_version = version_by_number(db, document_id, version)
    if selected_version is None:
        if db.get(Document, document_id) is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        raise HTTPException(status_code=404, detail="Requested document version not found.")
    nodes = list(
        db.scalars(
            select(SectionNode)
            .where(SectionNode.version_id == selected_version.id)
            .order_by(SectionNode.position)
        )
    )
    trees = build_node_tree(nodes)
    if len(trees) != 1:
        raise HTTPException(
            status_code=500,
            detail="Stored document version does not have exactly one root node.",
        )
    return trees[0]
