from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, build_engine
from app.models import DocumentVersion, SectionNode
from app.parser import ParsedDocument, ParsedNode, content_hash
from app.versioning import _match_nodes, ingest_pdf
from tests.fixtures import V1_PDF, V2_PDF


def test_v1_v2_detects_changes_additions_and_idempotent_reingest():
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine, expire_on_commit=False) as db:
        first, first_summary = ingest_pdf(db, "ct200", V1_PDF)
        repeated, repeated_summary = ingest_pdf(db, "ct200", V1_PDF)
        second, summary = ingest_pdf(db, "ct200", V2_PDF)
        assert first.version_number == repeated.version_number == 1
        assert repeated_summary["idempotent"] is True
        assert second.version_number == 2
        assert first_summary["counts"] == {"initial": 28}
        assert summary["counts"]["modified"] >= 4
        assert summary["counts"]["added"] == 1
        assert summary["counts"].get("removed", 0) == 0

        versions = list(
            db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == "ct200")
                .order_by(DocumentVersion.version_number)
            )
        )
        old_nodes = {
            node.section_number: node
            for node in db.scalars(
                select(SectionNode).where(SectionNode.version_id == versions[0].id)
            )
        }
        new_nodes = {
            node.section_number: node
            for node in db.scalars(
                select(SectionNode).where(SectionNode.version_id == versions[1].id)
            )
        }
        assert old_nodes["3.1"].logical_id == new_nodes["3.1"].logical_id
        assert old_nodes["3.1"].id != new_nodes["3.1"].id
        assert old_nodes["3.1"].content_hash == new_nodes["3.1"].content_hash
        assert old_nodes["3.2"].logical_id == new_nodes["3.2"].logical_id
        assert old_nodes["3.2"].content_hash != new_nodes["3.2"].content_hash
        assert new_nodes["5.3"].change_status == "added"
        assert old_nodes["4.2"].logical_id == new_nodes["4.2"].logical_id
        assert old_nodes["4.2"].content_hash != new_nodes["4.2"].content_hash
    engine.dispose()


def test_ambiguous_duplicate_path_is_not_guessed():
    old_root = SectionNode(
        id="old-root",
        version_id="v1",
        logical_id="root-logical",
        parent_id=None,
        heading="Manual",
        section_number=None,
        level=0,
        position=0,
        body_text="",
        content_hash=content_hash("Manual", ""),
    )
    old_nodes = [
        old_root,
        SectionNode(
            id="old-duplicate-a",
            version_id="v1",
            logical_id="old-a",
            parent_id="old-root",
            heading="Repeated",
            section_number="1",
            level=1,
            position=1,
            body_text="A",
            content_hash=content_hash("Repeated", "A"),
        ),
        SectionNode(
            id="old-duplicate-b",
            version_id="v1",
            logical_id="old-b",
            parent_id="old-root",
            heading="Repeated",
            section_number="1",
            level=1,
            position=2,
            body_text="B",
            content_hash=content_hash("Repeated", "B"),
        ),
    ]
    parsed = ParsedDocument(
        title="Manual",
        raw_text="",
        warnings=[],
        nodes=[
            ParsedNode("root", "Manual", None, 0, None),
            ParsedNode("a", "Repeated", "1", 1, "root", body_text="A"),
            ParsedNode("b", "Repeated", "1", 1, "root", body_text="B"),
        ],
    )
    matches, changes = _match_nodes(parsed, old_nodes)
    assert matches["a"]["status"] == "ambiguous"
    assert matches["b"]["status"] == "ambiguous"
    assert matches["a"]["logical_id"] not in {"old-a", "old-b"}
    assert {
        change["status"]
        for change in changes
        if change["old_node_id"] != "old-root"
    } == {"ambiguous"}


def test_unmatched_prior_node_is_explicitly_removed():
    old_root = SectionNode(
        id="root",
        version_id="v1",
        logical_id="root",
        parent_id=None,
        heading="Manual",
        section_number=None,
        level=0,
        position=0,
        body_text="",
        content_hash=content_hash("Manual", ""),
    )
    old_section = SectionNode(
        id="removed",
        version_id="v1",
        logical_id="removed-logical",
        parent_id="root",
        heading="Removed section",
        section_number="1",
        level=1,
        position=1,
        body_text="Old requirement",
        content_hash=content_hash("Removed section", "Old requirement"),
    )
    parsed = ParsedDocument(
        title="Manual",
        raw_text="",
        warnings=[],
        nodes=[ParsedNode("root", "Manual", None, 0, None)],
    )
    matches, changes = _match_nodes(parsed, [old_root, old_section])
    assert matches["root"]["status"] == "unchanged"
    removed = next(change for change in changes if change["old_node_id"] == "removed")
    assert removed["status"] == "removed"
