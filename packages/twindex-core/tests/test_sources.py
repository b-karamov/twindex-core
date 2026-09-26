import json
import socket
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pypdf import PdfWriter

from twindex_core import Vault
from twindex_core.sources import (
    ConsentRequired,
    SourceIngestor,
    SourceSecurityError,
    conversation_origin,
    conversation_preview,
    discover_conversations,
)
from twindex_core.sources import _PublicBackend


def test_import_claude_and_codex_jsonl_preserves_roles(tmp_path: Path) -> None:
    claude = tmp_path / "claude.jsonl"
    claude.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "The deadline is Friday"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "uuid": "a1",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Understood"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    codex = tmp_path / "codex.jsonl"
    codex.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Use blue labels"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with Vault.open(tmp_path / "vault") as vault:
        ingestor = SourceIngestor(vault)
        for path in (claude, codex):
            source = ingestor.ingest_path(path)[0]
            evidence = vault.list_evidence(source.id)
            assert [item.trust for item in evidence] == [
                "user_assertion",
                "assistant_unverified",
            ]
            assert [item.locator["message_index"] for item in evidence] == [0, 1]
            assert vault.ingest_text("unrelated").id != source.id


def test_discovery_reads_metadata_only_after_consent(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("not valid json\n", encoding="utf-8")
    with pytest.raises(ConsentRequired):
        discover_conversations([tmp_path], consent=False)
    found = discover_conversations([tmp_path], consent=True)
    assert [item.path for item in found] == [path]
    assert found[0].size_bytes == path.stat().st_size


def test_conversation_preview_identifies_provider_and_first_real_user_phrase(
    tmp_path: Path,
) -> None:
    codex = tmp_path / ".codex" / "sessions" / "session.jsonl"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "<environment_context>hidden</environment_context>",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "## My request:\nOrganize the release notes",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    claude = tmp_path / ".claude" / "projects" / "dialog.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Map the customer interviews",
                },
            }
        ),
        encoding="utf-8",
    )

    assert conversation_origin(codex) == "Codex"
    assert conversation_origin(claude) == "Claude Code"
    assert conversation_preview(codex) == "Organize the release notes"
    assert conversation_preview(claude) == "Map the customer interviews"


def test_image_and_scanned_pdf_are_stored_for_vision(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    Image.new("RGB", (20, 20), "red").save(image)
    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as handle:
        writer.write(handle)
    with Vault.open(tmp_path / "vault") as vault:
        ingestor = SourceIngestor(vault)
        image_source = ingestor.ingest_path(image)[0]
        pdf_source = ingestor.ingest_path(pdf)[0]
        assert vault.list_evidence(image_source.id)[0].locator["needs_vision"] is True
        assert vault.list_evidence(pdf_source.id)[0].locator["needs_vision"] is True
        assert vault.source_bytes(image_source.id) == image.read_bytes()


def test_url_redirect_to_private_address_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = socket.getaddrinfo

    def public_dns(host: str, *args: object, **kwargs: object):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/private"},
                request=request,
            )
        )
    )
    with Vault.open(tmp_path / "vault") as vault:
        with pytest.raises(SourceSecurityError):
            SourceIngestor(vault, http_client=client).ingest_url(
                "https://example.com/page"
            )
        assert vault.list_sources() == []


def test_network_backend_dials_checked_ip_not_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpcore

    dialed = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))
        ],
    )
    monkeypatch.setattr(
        httpcore.SyncBackend,
        "connect_tcp",
        lambda self, host, port, **kwargs: dialed.append((host, port)) or object(),
    )
    _PublicBackend().connect_tcp("example.com", 443)
    assert dialed == [("93.184.215.14", 443)]


def test_json_conversation_preserves_message_ids_and_roles(tmp_path: Path) -> None:
    path = tmp_path / "conversation.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"id": "u-1", "role": "user", "content": "The deadline is Friday"},
                    {"id": "a-1", "role": "assistant", "content": "Agreed"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with Vault.open(tmp_path / "vault") as vault:
        source = SourceIngestor(vault).ingest_path(path)[0]
        evidence = vault.list_evidence(source.id)
        assert source.kind == "conversation"
        assert [item.locator["message_id"] for item in evidence] == ["u-1", "a-1"]
        assert [item.trust for item in evidence] == [
            "user_assertion",
            "assistant_unverified",
        ]


def test_malformed_and_unsafe_sources_never_enter_vault(tmp_path: Path) -> None:
    malformed = tmp_path / "broken.json"
    malformed.write_text("{not json", encoding="utf-8")
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("safe.md", "already parsed")
        handle.writestr("..\\outside.md", "secret")
    with Vault.open(tmp_path / "vault") as vault:
        ingestor = SourceIngestor(vault)
        with pytest.raises(ValueError):
            ingestor.ingest_path(malformed)
        with pytest.raises(SourceSecurityError):
            ingestor.ingest_path(archive)
        assert vault.list_sources() == []
