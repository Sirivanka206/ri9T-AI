from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base, build_engine, get_db
from app.generation_store import JsonGenerationStore
from app.main import create_app
from tests.fixtures import PROJECT_ROOT, V1_PDF, V2_PDF


@pytest.fixture
def test_client(tmp_path):
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    api = create_app()
    api.state.database_engine = engine
    api.state.generation_store = JsonGenerationStore(tmp_path / "generated")
    api.state.settings = Settings(
        database_url="sqlite:///:memory:",
        generated_store_path=tmp_path / "generated",
        gemini_api_key="test-key",
        gemini_model="test-model",
        llm_max_retries=2,
    )

    def override_get_db():
        db = local_session()
        try:
            yield db
        finally:
            db.close()

    api.dependency_overrides[get_db] = override_get_db
    with TestClient(api) as client:
        yield client
    engine.dispose()


def upload_pdf(client: TestClient, path: Path, document_id: str = "ct200"):
    with path.open("rb") as pdf_file:
        return client.post(
            f"/documents/{document_id}/ingest",
            files={"file": (path.name, pdf_file, "application/pdf")},
        )


def find_tree_node(node: dict, section_number: str) -> dict | None:
    if node.get("section_number") == section_number:
        return node
    for child in node.get("children", []):
        found = find_tree_node(child, section_number)
        if found:
            return found
    return None
