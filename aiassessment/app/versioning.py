import difflib
import hashlib
import unicodedata
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentVersion, SectionNode, VersionChange
from app.parser import ParsedDocument, ParsedNode, parse_pdf


def _normalized_heading(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _node_signature(
    parent_logical_id: str | None, section_number: str | None, heading: str
) -> tuple[str | None, str | None, str]:
    return parent_logical_id, section_number, _normalized_heading(heading)


def _change_summary(old_text: str, new_text: str) -> str:
    diff = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
        )
    )
    return "\n".join(diff[:12]) or "Content changed."


def _match_nodes(
    parsed: ParsedDocument, previous_nodes: list[SectionNode]
) -> tuple[dict[str, dict], list[dict]]:
    previous_by_id = {node.id: node for node in previous_nodes}
    previous_by_signature: dict[tuple[str | None, str | None, str], list[SectionNode]] = (
        defaultdict(list)
    )
    for node in previous_nodes:
        parent_logical_id = (
            previous_by_id[node.parent_id].logical_id if node.parent_id else None
        )
        previous_by_signature[
            _node_signature(parent_logical_id, node.section_number, node.heading)
        ].append(node)

    result: dict[str, dict] = {}
    changes: list[dict] = []
    ambiguous_old_ids: set[str] = set()
    parsed_by_depth: dict[int, list[ParsedNode]] = defaultdict(list)
    for node in parsed.nodes:
        parsed_by_depth[node.level].append(node)

    for level in sorted(parsed_by_depth):
        nodes_at_level = parsed_by_depth[level]
        signatures: dict[tuple[str | None, str | None, str], list[ParsedNode]] = (
            defaultdict(list)
        )
        for node in nodes_at_level:
            parent_logical_id = (
                result[node.parent_key]["logical_id"] if node.parent_key else None
            )
            signature = _node_signature(
                parent_logical_id, node.section_number, node.heading
            )
            signatures[signature].append(node)

        for signature, siblings in signatures.items():
            old_candidates = previous_by_signature.get(signature, [])
            if len(siblings) == 1 and len(old_candidates) == 1:
                old = old_candidates[0]
                current = siblings[0]
                status = (
                    "unchanged"
                    if old.content_hash == current.content_hash
                    else "modified"
                )
                result[current.key] = {
                    "logical_id": old.logical_id,
                    "status": status,
                    "confidence": 1.0,
                    "old_node": old,
                }
                changes.append(
                    {
                        "logical_id": old.logical_id,
                        "old_node_id": old.id,
                        "new_key": current.key,
                        "status": status,
                        "confidence": 1.0,
                        "summary": (
                            "Content hash is unchanged."
                            if status == "unchanged"
                            else _change_summary(old.body_text, current.body_text)
                        ),
                    }
                )
                continue

            is_ambiguous = len(siblings) > 1 or len(old_candidates) > 1
            for current in siblings:
                status = "ambiguous" if is_ambiguous else "added"
                logical_id = str(uuid4())
                result[current.key] = {
                    "logical_id": logical_id,
                    "status": status,
                    "confidence": 0.0 if is_ambiguous else 1.0,
                    "old_node": None,
                }
                changes.append(
                    {
                        "logical_id": logical_id,
                        "old_node_id": old_candidates[0].id if old_candidates else None,
                        "new_key": current.key,
                        "status": status,
                        "confidence": 0.0 if is_ambiguous else 1.0,
                        "summary": (
                            "Multiple exact path candidates; matching was intentionally withheld."
                            if is_ambiguous
                            else "No prior node has the same section path and heading."
                        ),
                    }
                )
            if is_ambiguous:
                ambiguous_old_ids.update(node.id for node in old_candidates)

    matched_old_ids = {
        item["old_node"].id for item in result.values() if item["old_node"] is not None
    }
    for old in previous_nodes:
        if old.id in matched_old_ids:
            continue
        if old.id in ambiguous_old_ids:
            changes.append(
                {
                    "logical_id": old.logical_id,
                    "old_node_id": old.id,
                    "new_key": None,
                    "status": "ambiguous",
                    "confidence": 0.0,
                    "summary": "This prior node could not be uniquely matched to the current version.",
                }
            )
        else:
            changes.append(
                {
                    "logical_id": old.logical_id,
                    "old_node_id": old.id,
                    "new_key": None,
                    "status": "removed",
                    "confidence": 1.0,
                    "summary": "No exact section-path and heading match exists in the current version.",
                }
            )
    return result, changes


def ingest_pdf(
    db: Session,
    document_id: str,
    pdf_path: str | Path,
    source_filename: str | None = None,
) -> tuple[DocumentVersion, dict]:
    path = Path(pdf_path)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    document = db.get(Document, document_id)
    if document is None:
        document = Document(id=document_id, title=path.stem)
        db.add(document)
        db.flush()

    duplicate = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.source_hash == source_hash,
        )
    )
    if duplicate is not None:
        return duplicate, {
            "idempotent": True,
            "version_number": duplicate.version_number,
            "changes": [],
            "counts": {},
        }

    parsed = parse_pdf(path)
    if document.title in (path.stem, ""):
        document.title = parsed.title
    previous_version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    previous_nodes = (
        list(
            db.scalars(
                select(SectionNode).where(
                    SectionNode.version_id == previous_version.id
                )
            )
        )
        if previous_version
        else []
    )
    version = DocumentVersion(
        document_id=document_id,
        version_number=(previous_version.version_number + 1 if previous_version else 1),
        source_filename=source_filename or path.name,
        source_hash=source_hash,
        raw_text=parsed.raw_text,
        parser_warnings=parsed.warnings,
    )
    db.add(version)
    db.flush()

    matches, change_records = (
        _match_nodes(parsed, previous_nodes) if previous_version else ({}, [])
    )
    parsed_by_key = {node.key: node for node in parsed.nodes}
    persisted_by_key: dict[str, SectionNode] = {}
    for position, parsed_node in enumerate(parsed.nodes):
        match = matches.get(parsed_node.key)
        logical_id = match["logical_id"] if match else str(uuid4())
        node = SectionNode(
            version_id=version.id,
            logical_id=logical_id,
            parent_id=None,
            heading=parsed_node.heading,
            section_number=parsed_node.section_number,
            level=parsed_node.level,
            position=position,
            body_text=parsed_node.body_text,
            raw_text=parsed_node.raw_text,
            content_hash=parsed_node.content_hash,
            content_blocks=parsed_node.content_blocks,
            source_pages=parsed_node.source_pages,
            change_status=match["status"] if match else "initial",
            match_confidence=match["confidence"] if match else None,
        )
        if parsed_node.parent_key:
            parent = persisted_by_key.get(parsed_node.parent_key)
            if parent is None:
                raise ValueError(
                    f"Parser emitted child before parent: {parsed_node.key}"
                )
            node.parent_id = parent.id
        db.add(node)
        db.flush()
        persisted_by_key[parsed_node.key] = node

    if previous_version:
        counts: dict[str, int] = defaultdict(int)
        for change in change_records:
            new_node = (
                persisted_by_key.get(change["new_key"])
                if change["new_key"]
                else None
            )
            if new_node is not None and change["status"] in ("unchanged", "modified"):
                change["logical_id"] = new_node.logical_id
            db.add(
                VersionChange(
                    version_id=version.id,
                    logical_id=change["logical_id"],
                    old_node_id=change["old_node_id"],
                    new_node_id=new_node.id if new_node else None,
                    status=change["status"],
                    confidence=change["confidence"],
                    summary=change["summary"],
                )
            )
            counts[change["status"]] += 1
        summary = {
            "idempotent": False,
            "version_number": version.version_number,
            "changes": [
                {
                    "logical_id": change["logical_id"],
                    "old_node_id": change["old_node_id"],
                    "new_node_id": (
                        persisted_by_key[change["new_key"]].id
                        if change["new_key"]
                        else None
                    ),
                    "status": change["status"],
                    "confidence": change["confidence"],
                    "summary": change["summary"],
                }
                for change in change_records
            ],
            "counts": dict(counts),
        }
    else:
        summary = {
            "idempotent": False,
            "version_number": version.version_number,
            "changes": [],
            "counts": {"initial": len(parsed.nodes)},
        }
    db.commit()
    db.refresh(version)
    return version, summary
