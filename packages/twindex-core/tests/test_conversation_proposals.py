import json
from pathlib import Path

import pytest

from twindex_core import Vault
from twindex_core.conversations import (
    _chat_segments,
    _json_chat_segments,
    _markdown_chat_segments,
)
from twindex_core.engine import KnowledgeEngine
from test_engine import FakeProvider
from twindex_core.source_adapters import parse_source
from twindex_core.source_io import AcquiredSource


@pytest.mark.parametrize("parse", [_chat_segments, _json_chat_segments])
def test_reasoning_channels_excluded_and_final_answer_preserved(parse):
    rows = [
        {
            "id": "analysis",
            "role": "assistant",
            "channel": "analysis",
            "content": "Private reasoning",
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "channel": "analysis",
                "content": [{"type": "output_text", "text": "Private Codex reasoning"}],
            },
        },
        {
            "id": "final",
            "role": "assistant",
            "channel": "final",
            "content": [
                {"type": "thinking", "thinking": "Private Claude reasoning"},
                {"type": "text", "text": "Labels are blue."},
            ],
        },
    ]
    value = (
        "\n".join(json.dumps(row) for row in rows).encode()
        if parse is _chat_segments
        else rows
    )
    parts = parse(value)
    assert [part[0] for part in parts] == ["Labels are blue."]
    assert parts[0][1]["message_id"] == "final"


def test_markdown_reasoning_and_leading_think_blocks_excluded():
    parts = _markdown_chat_segments(
        "# Assistant\n<think>Private reasoning</think>\nLabels are blue.\n"
        "# Reasoning\nMore private reasoning\n# Assistant (analysis)\nHidden analysis\n"
        "# User\nExplain the <think> tag"
    )
    assert [part[0] for part in parts] == [
        "Labels are blue.",
        "Explain the <think> tag",
    ]


@pytest.mark.parametrize(
    "suffix,content",
    [
        (".md", "# Reasoning\nPrivate reasoning"),
        (
            ".json",
            '[{"role":"assistant","channel":"analysis","content":"Private reasoning"}]',
        ),
    ],
)
def test_reasoning_only_dialogue_has_no_analysis_content(suffix, content):
    parsed = parse_source(AcquiredSource(content.encode(), "Chat", "local", suffix))[0]
    assert parsed.kind == "conversation"
    assert parsed.segments == []


def test_legacy_codex_reasoning_filtered_from_snapshot(tmp_path):
    rows = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "channel": "analysis",
                "content": [{"type": "output_text", "text": "Private reasoning"}],
            },
        },
        {
            "role": "assistant",
            "id": "answer",
            "content": "<think>Internal deliberation</think>Labels are blue.",
        },
    ]
    raw = "\n".join(json.dumps(row) for row in rows).encode()

    class InspectProvider(FakeProvider):
        def structured(self, system, user, schema):
            payload = json.loads(user)
            assert [item["text"] for item in payload["evidence"]] == [
                "Labels are blue."
            ]
            return super().structured(system, user, schema)

    with Vault.open(tmp_path) as vault:
        source = vault.ingest_segments(
            raw,
            kind="conversation",
            title="Old chat",
            segments=[
                (
                    "Private reasoning",
                    {
                        "kind": "conversation",
                        "role": "assistant",
                        "line": 1,
                        "message_id": "line:1",
                    },
                    "assistant_unverified",
                ),
                (
                    "<think>Internal deliberation</think>Labels are blue.",
                    {
                        "kind": "conversation",
                        "role": "assistant",
                        "line": 2,
                        "message_id": "answer",
                    },
                    "assistant_unverified",
                ),
            ],
        )
        change = KnowledgeEngine(vault, InspectProvider()).propose([source.id])
        assert len(change.operations) == 1
        assert change.operations[0].evidence_ids == [
            vault.list_evidence(source.id)[1].id
        ]
        assert vault.source_bytes(source.id) == raw


@pytest.mark.parametrize(
    "suffix,content",
    [
        (".json", '[{"role":"system","content":"secret system"}]'),
        (".md", "# System\nsecret system"),
        (".md", "# User\n<environment_context>secret</environment_context>"),
    ],
)
def test_service_only_dialogue_never_falls_back_to_plain_text(suffix, content):
    parsed = parse_source(AcquiredSource(content.encode(), "Chat", "local", suffix))[0]
    assert parsed.kind == "conversation"
    assert parsed.segments == []


def test_service_only_existing_source_skips_model(tmp_path):
    class NoCalls(FakeProvider):
        def structured(self, *args):
            raise AssertionError("No useful context to send")

    with Vault.open(tmp_path) as vault:
        source = vault.ingest_text("Old snapshot")
        vault.conn.execute(
            "UPDATE evidence SET text=?,locator_json=? WHERE source_id=?",
            (
                "<environment_context>secret</environment_context>",
                json.dumps({"kind": "conversation", "role": "user"}),
                source.id,
            ),
        )
        change = KnowledgeEngine(vault, NoCalls()).propose([source.id])
        assert not change.operations
        assert change.model is None


@pytest.mark.parametrize(
    "parse,raw",
    [
        (
            _chat_segments,
            "\n".join(
                json.dumps(row)
                for row in [
                    {"role": "system", "content": "secret system"},
                    {
                        "role": "user",
                        "content": "<environment_context>secret env</environment_context>\n\n## My request:\nSummarize",
                    },
                    {"role": "assistant", "content": "Labels are blue."},
                ]
            ).encode(),
        ),
        (
            _json_chat_segments,
            [
                {
                    "role": "user",
                    "content": "<recommended_plugins>secret plugins</recommended_plugins>",
                },
                {"role": "user", "content": "Summarize"},
                {"role": "assistant", "content": "Labels are blue."},
            ],
        ),
        (
            _markdown_chat_segments,
            "# System\nsecret system\n# User\nSummarize\n# Assistant\nLabels are blue.\n# Developer\nsecret dev",
        ),
    ],
)
def test_service_context_removed_but_dialogue_preserved(parse, raw):
    parts = parse(raw)
    assert [part[0] for part in parts] == ["Summarize", "Labels are blue."]
    assert [part[2] for part in parts] == ["user_assertion", "assistant_unverified"]


def test_existing_snapshot_filters_service_context_and_cites_ai(tmp_path: Path):
    class InspectProvider(FakeProvider):
        def structured(self, system, user, schema):
            payload = json.loads(user)
            if "evidence" in payload:
                assert [item["text"] for item in payload["evidence"]] == [
                    "Labels are blue."
                ]
                assert payload["evidence"][0]["trust"] == "assistant_unverified"
                assert "assistant_unverified" in system
            return super().structured(system, user, schema)

    with Vault.open(tmp_path) as vault:
        source = vault.ingest_text("Old snapshot", title="Chat")
        vault.conn.execute(
            "UPDATE evidence SET text=?,locator_json=? WHERE source_id=?",
            (
                "<environment_context>secret</environment_context>",
                json.dumps({"kind": "conversation", "role": "user"}),
                source.id,
            ),
        )
        vault.add_evidence(
            source.id,
            "Labels are blue.",
            {"kind": "conversation", "role": "assistant"},
            trust="assistant_unverified",
        )
        before = vault.source_bytes(source.id)
        change = KnowledgeEngine(vault, InspectProvider()).propose([source.id])
        assert len(change.operations) == 1
        assert not vault.list_cards()
        vault.stage(change.id, [change.operations[0].id])
        vault.commit(change.id)
        cites = vault.hit(vault.list_cards()[0].id).citations
        assert cites[0].trust == "assistant_unverified"
        assert vault.source_bytes(source.id) == before
        answer = KnowledgeEngine(vault, InspectProvider()).answer("Labels")
        assert "не проверено" in answer.text
        assert answer.citations[0].trust == "assistant_unverified"
