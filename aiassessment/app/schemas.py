from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ContentBlock(BaseModel):
    type: str
    text: str | None = None
    page: int | None = None
    columns: list[str] | None = None
    rows: list[list[str]] | None = None


class NodeTree(ORMModel):
    id: str
    logical_id: str
    version_id: str
    parent_id: str | None
    heading: str
    section_number: str | None
    level: int
    body_text: str
    content_hash: str
    content_blocks: list[dict]
    source_pages: list[int]
    change_status: str
    children: list["NodeTree"] = Field(default_factory=list)


class DocumentVersionSummary(ORMModel):
    id: str
    version_number: int
    source_filename: str
    source_hash: str
    created_at: datetime
    parser_warnings: list[dict]


class DocumentSummary(BaseModel):
    id: str
    title: str
    latest_version: int | None


class IngestResponse(BaseModel):
    document_id: str
    version_id: str
    version_number: int
    idempotent: bool
    node_count: int
    warnings: list[dict]
    changes: list[dict]
    change_counts: dict[str, int]


class SearchResult(ORMModel):
    id: str
    logical_id: str
    version_id: str
    heading: str
    section_number: str | None
    body_text: str
    content_hash: str
    source_pages: list[int]


class SelectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    document_id: str = Field(min_length=1, max_length=80)
    node_ids: list[str] = Field(min_length=1)


class SelectionItemResponse(BaseModel):
    node_id: str
    logical_id: str
    version_id: str
    content_hash: str
    heading: str
    text: str
    section_number: str | None


class SelectionResponse(BaseModel):
    id: str
    name: str
    document_id: str
    version_id: str
    version_number: int
    created_at: str
    items: list[SelectionItemResponse]


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    logical_id: str
    version_id: str
    content_hash: str


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    requirement_reference: str = Field(min_length=1, max_length=300)
    preconditions: list[str]
    test_steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    priority: Literal["low", "medium", "high"]
    unsupported_details: list[str] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(min_length=1)
    human_qa_review_required: Literal[True]


class TestCaseSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_cases: list[TestCase] = Field(min_length=3, max_length=5)


class RetrievedTestCase(TestCase):
    staleness: dict


class GenerationRequest(BaseModel):
    selection_id: str
    regenerate: bool = False


class GenerationResponse(BaseModel):
    generation_id: str
    selection_id: str
    created_at: str
    model: str
    reused: bool = False
    source_version_id: str
    source_version_number: int
    source_refs: list[SourceReference]
    test_cases: list[RetrievedTestCase]
    staleness: dict
