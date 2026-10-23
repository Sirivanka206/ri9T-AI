import argparse
import json as jsonlib
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings, settings
from app.database import Base, build_engine, get_db
from app.generation_store import JsonGenerationStore
from app.main import create_app


class DemoLLMClient:
    async def post(self, url, params, json):
        prompt = json["contents"][0]["parts"][0]["text"]
        source_json = prompt.split("Manual source data:\n", 1)[1].split(
            "\nYour previous response", 1
        )[0]
        sources = jsonlib.loads(source_json)
        reference = sources[0]["source_ref"]
        body = {
            "test_cases": [
                {
                    "title": f"CT-200 sourced QA idea {index}",
                    "requirement_reference": sources[0]["section_number"] or sources[0]["heading"],
                    "preconditions": ["Use a CT-200 device with the selected manual requirement."],
                    "test_steps": ["Perform the action described by the selected requirement."],
                    "expected_result": "The observed result is checked against the selected source text.",
                    "priority": "medium",
                    "unsupported_details": [
                        "The manual does not provide a complete physical test procedure."
                    ],
                    "source_refs": [reference],
                    "human_qa_review_required": True,
                }
                for index in range(1, 4)
            ]
        }
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "candidates": [
                    {"content": {"parts": [{"text": jsonlib.dumps(body)}]}}
                ]
            },
        )


def run_demo(real_llm: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="ct200-demo-") as temporary_directory:
        root = Path(temporary_directory)
        engine = build_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        local_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        api = create_app()
        api.state.database_engine = engine
        api.state.generation_store = JsonGenerationStore(root / "generated")
        api.state.settings = settings if real_llm else Settings(
            database_url="sqlite:///:memory:",
            generated_store_path=root / "generated",
            gemini_api_key="demo-key",
            gemini_model="demo-model",
            llm_max_retries=2,
        )
        api.state.llm_client = None if real_llm else DemoLLMClient()

        def override_get_db():
            db = local_session()
            try:
                yield db
            finally:
                db.close()

        api.dependency_overrides[get_db] = override_get_db
        with TestClient(api) as client:
            with (ROOT / "data" / "ct200_manual.pdf").open("rb") as manual:
                first = client.post(
                    "/documents/ct200/ingest",
                    files={"file": ("ct200_manual.pdf", manual, "application/pdf")},
                )
            first.raise_for_status()
            tree = client.get("/documents/ct200/sections?version=1")
            tree.raise_for_status()
            node = _find_node(tree.json(), "3.2")
            node_response = client.get(f"/nodes/{node['id']}")
            node_response.raise_for_status()
            selection_response = client.post(
                "/selections",
                json={
                    "name": "CT-200 inflation sequence",
                    "document_id": "ct200",
                    "node_ids": [node["id"]],
                },
            )
            selection_response.raise_for_status()
            selection = selection_response.json()
            original_text = selection["items"][0]["text"]
            generation_response = client.post(
                "/generations", json={"selection_id": selection["id"]}
            )
            generation_response.raise_for_status()
            generation = generation_response.json()
            assert len(generation["test_cases"]) in (3, 4, 5)
            assert generation["staleness"]["status"] == "current"

            with (ROOT / "data" / "ct200_manual_v2.pdf").open("rb") as manual:
                second = client.post(
                    "/documents/ct200/ingest",
                    files={
                        "file": ("ct200_manual_v2.pdf", manual, "application/pdf")
                    },
                )
            second.raise_for_status()
            changes = client.get(f"/nodes/{node['id']}/changes")
            changes.raise_for_status()
            pinned = client.get(f"/selections/{selection['id']}")
            pinned.raise_for_status()
            retrieved = client.get(
                "/generations", params={"node_id": node["id"]}
            )
            retrieved.raise_for_status()
            stale_generation = next(
                item
                for item in retrieved.json()
                if item["generation_id"] == generation["generation_id"]
            )
            assert pinned.json()["items"][0]["text"] == original_text
            assert stale_generation["staleness"]["status"] == "stale"

            summary = {
                "document_id": "ct200",
                "version_1": first.json()["version_number"],
                "version_2": second.json()["version_number"],
                "top_level_sections": len(tree.json()["children"]),
                "retrieved_node": node_response.json()["section_number"],
                "generation_id": generation["generation_id"],
                "generated_case_count": len(generation["test_cases"]),
                "version_2_change_counts": second.json()["change_counts"],
                "version_1_selection_text_preserved": True,
                "retrieved_generation_staleness": stale_generation["staleness"]["status"],
                "llm_mode": "real" if real_llm else "deterministic mock",
            }
        engine.dispose()
        return summary


def _find_node(node: dict, section_number: str) -> dict:
    if node.get("section_number") == section_number:
        return node
    for child in node.get("children", []):
        try:
            return _find_node(child, section_number)
        except LookupError:
            continue
    raise LookupError(f"Section {section_number} was not found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="Use Gemini (requires GEMINI_API_KEY) instead of a deterministic local mock.",
    )
    arguments = parser.parse_args()
    print(jsonlib.dumps(run_demo(arguments.real_llm), indent=2))
