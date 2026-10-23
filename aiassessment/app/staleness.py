from typing import Any
import difflib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentVersion, SectionNode, VersionChange


def assess_generation_staleness(
    db: Session, generation: dict[str, Any]
) -> dict[str, Any]:
    source_version = db.get(DocumentVersion, generation["source_version_id"])
    if source_version is None:
        return {
            "status": "ambiguous_mapping",
            "human_review_required": True,
            "source_version": generation.get("source_version_number"),
            "current_version": None,
            "changes": [{"status": "ambiguous_mapping", "summary": "Source version is unavailable."}],
        }
    current_version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == source_version.document_id)
        .order_by(DocumentVersion.version_number.desc())
    )
    if current_version is None:
        raise RuntimeError("A stored generation references a document without a version.")

    source_refs = generation.get("source_refs", [])
    node_statuses: list[dict[str, Any]] = []
    priority = {"current": 0, "stale": 1, "removed_requirement": 2, "ambiguous_mapping": 3}
    for source in source_refs:
        current_node = db.scalar(
            select(SectionNode).where(
                SectionNode.version_id == current_version.id,
                SectionNode.logical_id == source["logical_id"],
            )
        )
        if current_node is None:
            history = list(
                db.scalars(
                    select(VersionChange)
                    .join(
                        DocumentVersion,
                        VersionChange.version_id == DocumentVersion.id,
                    )
                    .where(
                        DocumentVersion.document_id == source_version.document_id,
                        DocumentVersion.version_number > source_version.version_number,
                        VersionChange.logical_id == source["logical_id"],
                    )
                    .order_by(DocumentVersion.version_number, VersionChange.id)
                )
            )
            status = (
                "ambiguous_mapping"
                if any(change.status == "ambiguous" for change in history)
                else "removed_requirement"
            )
            change = next(
                (row for row in reversed(history) if row.status in ("removed", "ambiguous")),
                None,
            )
            summary = (
                change.summary
                if change
                else "No current node preserves the selected logical node."
            )
            node_statuses.append(
                {
                    "node_id": source["node_id"],
                    "logical_id": source["logical_id"],
                    "status": status,
                    "summary": summary,
                    "source_version": source_version.version_number,
                    "current_version": current_version.version_number,
                }
            )
        elif current_node.content_hash != source["content_hash"]:
            change = db.scalar(
                select(VersionChange)
                .where(
                    VersionChange.version_id.in_(
                        select(DocumentVersion.id).where(
                            DocumentVersion.document_id == source_version.document_id,
                            DocumentVersion.version_number > source_version.version_number,
                        )
                    ),
                    VersionChange.logical_id == source["logical_id"],
                )
                .order_by(VersionChange.id)
            )
            source_text = next(
                (
                    snapshot.get("text", "")
                    for snapshot in generation.get("source_snapshots", [])
                    if snapshot.get("source_ref", {}).get("logical_id")
                    == source["logical_id"]
                ),
                "",
            )
            current_text = f"{current_node.heading}\n{current_node.body_text}".strip()
            diff = list(
                difflib.unified_diff(
                    source_text.splitlines(),
                    current_text.splitlines(),
                    fromfile=f"version-{source_version.version_number}",
                    tofile=f"version-{current_version.version_number}",
                    lineterm="",
                )
            )
            node_statuses.append(
                {
                    "node_id": source["node_id"],
                    "logical_id": source["logical_id"],
                    "current_node_id": current_node.id,
                    "status": "stale",
                    "summary": "\n".join(diff[:12])
                    or (change.summary if change else "Current content hash differs."),
                    "source_version": source_version.version_number,
                    "current_version": current_version.version_number,
                }
            )
        else:
            node_statuses.append(
                {
                    "node_id": source["node_id"],
                    "logical_id": source["logical_id"],
                    "current_node_id": current_node.id,
                    "status": "current",
                    "summary": "Normalized content hash is unchanged.",
                    "source_version": source_version.version_number,
                    "current_version": current_version.version_number,
                }
            )

    overall_status = max(
        (item["status"] for item in node_statuses),
        key=lambda status: priority[status],
        default="current",
    )
    return {
        "status": overall_status,
        "human_review_required": True,
        "source_version": source_version.version_number,
        "current_version": current_version.version_number,
        "changes": node_statuses,
    }


def annotate_cases(
    db: Session, generation: dict[str, Any]
) -> list[dict[str, Any]]:
    annotated = []
    for case in generation.get("test_cases", []):
        case_copy = dict(case)
        single_source_generation = dict(generation)
        case_refs = {
            (
                reference["node_id"],
                reference["logical_id"],
                reference["version_id"],
                reference["content_hash"],
            )
            for reference in case.get("source_refs", [])
        }
        single_source_generation["source_refs"] = [
            reference
            for reference in generation.get("source_refs", [])
            if (
                reference["node_id"],
                reference["logical_id"],
                reference["version_id"],
                reference["content_hash"],
            )
            in case_refs
        ]
        case_copy["staleness"] = assess_generation_staleness(
            db, single_source_generation
        )
        annotated.append(case_copy)
    return annotated
