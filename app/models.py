from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", order_by="DocumentVersion.version_number"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    source_filename: Mapped[str] = mapped_column(String(500))
    source_hash: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str] = mapped_column(Text)
    parser_warnings: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    document: Mapped[Document] = relationship(back_populates="versions")
    nodes: Mapped[list["SectionNode"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class SectionNode(Base):
    __tablename__ = "section_nodes"
    __table_args__ = (
        UniqueConstraint("version_id", "logical_id"),
        UniqueConstraint("version_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"), index=True
    )
    logical_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("section_nodes.id"), nullable=True, index=True
    )
    heading: Mapped[str] = mapped_column(String(1000))
    section_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    level: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)
    body_text: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_blocks: Mapped[list[dict]] = mapped_column(JSON, default=list)
    source_pages: Mapped[list[int]] = mapped_column(JSON, default=list)
    change_status: Mapped[str] = mapped_column(String(30), default="initial")
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    version: Mapped[DocumentVersion] = relationship(back_populates="nodes")
    parent: Mapped["SectionNode | None"] = relationship(
        remote_side="SectionNode.id", back_populates="children"
    )
    children: Mapped[list["SectionNode"]] = relationship(
        back_populates="parent", order_by="SectionNode.position"
    )


class VersionChange(Base):
    __tablename__ = "version_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"), index=True
    )
    logical_id: Mapped[str] = mapped_column(String(36), index=True)
    old_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    new_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text, default="")


class Selection(Base):
    __tablename__ = "selections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(300))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    items: Mapped[list["SelectionItem"]] = relationship(
        back_populates="selection", cascade="all, delete-orphan"
    )


class SelectionItem(Base):
    __tablename__ = "selection_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    selection_id: Mapped[str] = mapped_column(ForeignKey("selections.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(36), index=True)
    logical_id: Mapped[str] = mapped_column(String(36), index=True)
    version_id: Mapped[str] = mapped_column(String(36), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    heading_snapshot: Mapped[str] = mapped_column(String(1000))
    text_snapshot: Mapped[str] = mapped_column(Text)
    section_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    selection: Mapped[Selection] = relationship(back_populates="items")
