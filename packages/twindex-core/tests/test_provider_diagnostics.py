import json
import httpx
import pytest

from twindex_core.engine import GeneratedAnswer, ModelOutputError
from twindex_core.provider import OpenAICompatibleProvider


def test_schema_error_explains_failure_without_source_or_raw_output():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":"PRIVATE_SOURCE_TEXT"}'},
                    }
                ],
                "usage": {"completion_tokens": 15},
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost"
    ) as client:
        provider = OpenAICompatibleProvider(client=client)
        with pytest.raises(ModelOutputError) as caught:
            provider.structured("system", "PRIVATE_SOURCE_TEXT", GeneratedAnswer)
    details = caught.value.diagnostics
    assert details["schema"] == "GeneratedAnswer"
    assert details["attempts"][-1]["finish_reason"] == "stop"
    assert {e["field"] for e in details["attempts"][-1]["errors"]} == {
        "evidence_refs",
        "insufficient_evidence",
    }
    assert "PRIVATE_SOURCE_TEXT" not in json.dumps(details)
    assert "evidence_refs" in requests[1]["messages"][-1]["content"]


def test_truncated_valid_json_is_not_accepted():
    def respond(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": '{"text":"ok","evidence_refs":["C1"],"insufficient_evidence":false}'
                        },
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(respond), base_url="http://localhost"
    ) as client:
        with pytest.raises(ModelOutputError, match="truncat"):
            OpenAICompatibleProvider(client=client).structured(
                "s", "u", GeneratedAnswer
            )


@pytest.mark.parametrize(
    "choice",
    [
        None,
        {"message": None},
        {"message": {"content": []}},
        {"message": {"refusal": "PRIVATE_REFUSAL"}},
    ],
)
def test_malformed_completion_is_explained_without_provider_text(choice):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"choices": [choice]})
        ),
        base_url="http://localhost",
    ) as client:
        with pytest.raises(ModelOutputError) as caught:
            OpenAICompatibleProvider(client=client).structured(
                "s", "u", GeneratedAnswer
            )
        assert (
            caught.value.diagnostics["attempts"][0]["errors"][0]["code"]
            == "completion_invalid"
        )
        assert "PRIVATE_REFUSAL" not in json.dumps(caught.value.diagnostics)
