import json

from app.schemas import (
    SourceReference,
    TestCase as GeneratedTestCase,
    TestCaseSet as GeneratedTestCaseSet,
)
from app.llm_service import LLMGenerationError
from tests.conftest import V1_PDF, V2_PDF, find_tree_node, upload_pdf


def test_version_pinned_selection_and_generation_staleness_flow(
    test_client, monkeypatch
):
    calls = 0

    async def fake_generate(sources, api_key, model, max_retries, client=None):
        nonlocal calls
        calls += 1
        reference = SourceReference.model_validate(sources[0]["source_ref"])
        cases = [
            GeneratedTestCase(
                title=f"Review inflation increment {number}",
                requirement_reference="3.2",
                preconditions=["The device is ready."],
                test_steps=["Start a measurement and observe cuff inflation."],
                expected_result="Follow the sourced inflation sequence.",
                priority="medium",
                unsupported_details=[],
                source_refs=[reference],
                human_qa_review_required=True,
            )
            for number in range(3)
        ]
        result = GeneratedTestCaseSet(test_cases=cases)
        return result, json.dumps(result.model_dump(mode="json"))

    monkeypatch.setattr(
        "app.routes.generations.generate_test_cases", fake_generate
    )
    first = upload_pdf(test_client, V1_PDF)
    assert first.status_code == 201, first.text
    v1 = first.json()
    tree_response = test_client.get("/documents/ct200/sections?version=1")
    assert tree_response.status_code == 200
    source_node = find_tree_node(tree_response.json(), "3.2")
    assert source_node is not None
    exact_v1_text = source_node["heading"] + "\n" + source_node["body_text"]

    created = test_client.post(
        "/selections",
        json={
            "name": "Inflation behavior",
            "document_id": "ct200",
            "node_ids": [source_node["id"]],
        },
    )
    assert created.status_code == 201, created.text
    selection = created.json()
    assert selection["version_id"] == v1["version_id"]
    assert selection["items"][0]["text"] == exact_v1_text

    generated = test_client.post(
        "/generations", json={"selection_id": selection["id"]}
    )
    assert generated.status_code == 200, generated.text
    first_generation = generated.json()
    assert first_generation["staleness"]["status"] == "current"
    assert first_generation["test_cases"][0]["human_qa_review_required"] is True
    assert first_generation["test_cases"][0]["source_refs"][0]["node_id"] == source_node["id"]

    reused = test_client.post(
        "/generations", json={"selection_id": selection["id"]}
    )
    assert reused.json()["generation_id"] == first_generation["generation_id"]
    assert reused.json()["reused"] is True
    assert calls == 1

    forced = test_client.post(
        "/generations",
        json={"selection_id": selection["id"], "regenerate": True},
    )
    assert forced.status_code == 200
    assert forced.json()["generation_id"] != first_generation["generation_id"]
    assert calls == 2
    latest_reuse = test_client.post(
        "/generations", json={"selection_id": selection["id"]}
    )
    assert latest_reuse.json()["generation_id"] == forced.json()["generation_id"]
    assert latest_reuse.json()["reused"] is True

    second = upload_pdf(test_client, V2_PDF)
    assert second.status_code == 201, second.text
    v2 = second.json()
    assert v2["version_number"] == 2
    assert v2["change_counts"]["modified"] >= 4
    assert v2["change_counts"]["added"] == 1
    search = test_client.get("/nodes/search", params={"q": "1.5 seconds"})
    assert search.status_code == 200
    assert any(node["section_number"] == "4.2" for node in search.json())

    resolved = test_client.get(f"/selections/{selection['id']}").json()
    assert resolved["version_id"] == v1["version_id"]
    assert resolved["items"][0]["text"] == exact_v1_text

    retrieved = test_client.get(
        "/generations", params={"selection_id": selection["id"]}
    )
    assert retrieved.status_code == 200
    assert len(retrieved.json()) == 2
    old_generation = next(
        row
        for row in retrieved.json()
        if row["generation_id"] == first_generation["generation_id"]
    )
    assert old_generation["source_version_number"] == 1
    assert old_generation["staleness"]["status"] == "stale"
    assert old_generation["test_cases"][0]["staleness"]["status"] == "stale"
    assert old_generation["test_cases"][0]["staleness"]["changes"][0]["current_version"] == 2
    assert old_generation["source_refs"][0]["node_id"] == source_node["id"]
    by_node = test_client.get(
        "/generations", params={"node_id": source_node["id"]}
    )
    assert by_node.status_code == 200
    assert len(by_node.json()) == 2

    latest_tree = test_client.get("/documents/ct200/sections").json()
    added_node = find_tree_node(latest_tree, "5.3")
    added_history = test_client.get(f"/nodes/{added_node['id']}/changes")
    assert added_history.status_code == 200
    assert added_history.json()["changes"][0]["status"] == "added"


def test_api_rejects_bad_pdf_missing_nodes_and_unpinned_mixed_versions(test_client):
    unsupported = test_client.post(
        "/documents/ct200/ingest",
        files={"file": ("not.pdf", b"not a PDF", "application/pdf")},
    )
    assert unsupported.status_code == 415
    assert test_client.get("/nodes/missing-node").status_code == 404
    assert test_client.get("/generations").status_code == 422

    first = upload_pdf(test_client, V1_PDF)
    assert first.status_code == 201
    v1_tree = test_client.get("/documents/ct200/sections?version=1").json()
    v1_node = find_tree_node(v1_tree, "3.2")
    assert upload_pdf(test_client, V2_PDF).status_code == 201
    v2_tree = test_client.get("/documents/ct200/sections?version=2").json()
    v2_node = find_tree_node(v2_tree, "3.2")
    response = test_client.post(
        "/selections",
        json={
            "name": "Mixed versions",
            "document_id": "ct200",
            "node_ids": [v1_node["id"], v2_node["id"]],
        },
    )
    assert response.status_code == 422


def test_failed_generation_is_not_persisted_and_missing_key_is_explicit(
    test_client, monkeypatch
):
    assert upload_pdf(test_client, V1_PDF).status_code == 201
    tree = test_client.get("/documents/ct200/sections?version=1").json()
    node = find_tree_node(tree, "3.2")
    selection = test_client.post(
        "/selections",
        json={
            "name": "Failure handling",
            "document_id": "ct200",
            "node_ids": [node["id"]],
        },
    ).json()

    async def malformed(*args, **kwargs):
        raise LLMGenerationError("LLM output failed after 3 attempts.")

    monkeypatch.setattr("app.routes.generations.generate_test_cases", malformed)
    failed = test_client.post(
        "/generations", json={"selection_id": selection["id"]}
    )
    assert failed.status_code == 502
    assert failed.json()["detail"]["stored"] is False
    assert test_client.get(
        "/generations", params={"selection_id": selection["id"]}
    ).json() == []

    monkeypatch.undo()
    test_client.app.state.settings = test_client.app.state.settings.__class__(
        database_url="sqlite:///:memory:",
        generated_store_path=test_client.app.state.settings.generated_store_path,
        gemini_api_key=None,
        gemini_model="test-model",
        llm_max_retries=2,
    )
    missing_key = test_client.post(
        "/generations", json={"selection_id": selection["id"]}
    )
    assert missing_key.status_code == 503
