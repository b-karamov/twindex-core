import json

import httpx
import pytest

from twindex_core.engine import GeneratedProposal, ModelOutputError, VisionUnavailable
from twindex_core.provider import OpenAICompatibleProvider


def test_ollama_structured_vision_embeddings_and_metadata() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"operations": []})}}]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost:11434"
    ) as client:
        provider = OpenAICompatibleProvider(profile="ollama", client=client)
        provider.require_vision()
        assert provider.structured("system", "source", GeneratedProposal) == {
            "operations": []
        }
        provider.describe_image(b"fake", mime_type="image/png")
        assert provider.embed(["hello"]) == [[0.1, 0.2]]
    chat = [
        request for request in requests if request.url.path == "/v1/chat/completions"
    ]
    assert chat[0].read().find(b'"json_schema"') >= 0
    assert json.loads(chat[0].read())["reasoning_effort"] == "none"
    assert b"data:image/png;base64" in chat[1].read()
    assert all(request.url.host == "localhost" for request in requests)


def test_ollama_without_vision_fails_before_image_send() -> None:
    paths = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"capabilities": ["completion"]})

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost:11434"
    ) as client:
        provider = OpenAICompatibleProvider(profile="ollama", client=client)
        with pytest.raises(VisionUnavailable):
            provider.describe_image(b"image", mime_type="image/png")
    assert paths == ["/api/show"]


def test_diagnose_reports_blocked_multimodal_and_semantic_operations() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(404, json={"error": "model missing"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost:11434"
    ) as client:
        provider = OpenAICompatibleProvider(profile="ollama", client=client)
        result = provider.diagnose()
    assert result["vision"] is False
    assert result["embeddings"] is False
    assert result["operations"] == {
        "text_proposals": True,
        "image_proposals": False,
        "scanned_pdf_proposals": False,
        "fts_search": True,
        "semantic_search": False,
    }


def test_invalid_json_never_becomes_proposal() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "not json"}}]}
        )

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost:11434"
    ) as client:
        provider = OpenAICompatibleProvider(profile="ollama", client=client)
        with pytest.raises(ModelOutputError):
            provider.structured("system", "source", GeneratedProposal)


def test_cloud_requires_explicit_source_approval() -> None:
    provider = OpenAICompatibleProvider(
        profile="cloud", api_key="test", allow_cloud_sources=False
    )
    with pytest.raises(PermissionError):
        provider.structured("system", "local source", GeneratedProposal)


def test_custom_remote_endpoint_requires_approval_and_tls() -> None:
    remote = OpenAICompatibleProvider(
        profile="custom", base_url="https://llm.example", vision=False
    )
    with pytest.raises(PermissionError):
        remote.text("system", "private card")
    insecure = OpenAICompatibleProvider(
        profile="custom",
        base_url="http://llm.example",
        vision=False,
        allow_cloud_sources=True,
    )
    with pytest.raises(ValueError):
        insecure.text("system", "private card")


def test_custom_vision_is_probed_with_synthetic_image() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost:11434"
    ) as client:
        provider = OpenAICompatibleProvider(
            profile="custom", vision=True, client=client
        )
        provider.require_vision()
    assert len(requests) == 1
    assert b"data:image/png;base64" in requests[0].read()
    assert b"private source" not in requests[0].read()


def test_timeout_and_refusal_fail_closed() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow model", request=request)

    with httpx.Client(
        transport=httpx.MockTransport(timeout), base_url="http://localhost:11434"
    ) as client:
        provider = OpenAICompatibleProvider(profile="ollama", client=client)
        with pytest.raises(httpx.ReadTimeout):
            provider.structured("system", "data", GeneratedProposal)

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"refusal": "no"}}]}
            )
        ),
        base_url="http://localhost:11434",
    ) as client:
        provider = OpenAICompatibleProvider(profile="ollama", client=client)
        with pytest.raises(ModelOutputError):
            provider.structured("system", "data", GeneratedProposal)
