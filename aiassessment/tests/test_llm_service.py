import asyncio
import json

import httpx
import pytest

from app.llm_service import LLMGenerationError, generate_test_cases


SOURCE = {
    "section_number": "3.2",
    "heading": "Cuff Inflation Sequence",
    "text": "The cuff starts at 180 mmHg and increases in 30 mmHg increments.",
    "source_ref": {
        "node_id": "node-1",
        "logical_id": "logical-1",
        "version_id": "version-1",
        "content_hash": "hash-1",
    },
}


def _valid_output():
    source_ref = SOURCE["source_ref"]
    return {
        "test_cases": [
            {
                "title": f"Case {index}",
                "requirement_reference": "3.2",
                "preconditions": ["Device is ready for measurement."],
                "test_steps": ["Start a measurement."],
                "expected_result": "Inflation follows the selected source requirement.",
                "priority": "medium",
                "unsupported_details": [],
                "source_refs": [source_ref],
                "human_qa_review_required": True,
            }
            for index in range(1, 4)
        ]
    }


def test_malformed_model_output_retries_only_within_configured_limit():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]},
        )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(LLMGenerationError, match="after 3 attempt"):
                await generate_test_cases(
                    [SOURCE], "fake-key", "fake-model", 2, client=client
                )

    asyncio.run(exercise())
    assert calls == 3


def test_valid_output_is_structured_and_citations_must_be_selected_sources():
    body = json.dumps(_valid_output())

    async def handler(request):
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": body}]}}]},
        )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            cases, raw = await generate_test_cases(
                [SOURCE], "fake-key", "fake-model", 0, client=client
            )
            assert len(cases.test_cases) == 3
            assert raw == body

    asyncio.run(exercise())
