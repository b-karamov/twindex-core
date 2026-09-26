import json
import socket
from pathlib import Path

import httpx
import pytest
from test_source_adapters import docx_bytes
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from twindex_core import Vault
from twindex_core.engine import GeneratedAnswer, KnowledgeEngine
from twindex_core.sources import SourceIngestor


class FixtureProvider:
    model = "fixture-local"

    def structured(self, system, user, schema):
        if schema is GeneratedAnswer:
            return {
                "text": "The clue is blue.",
                "evidence_refs": ["C1"],
                "insufficient_evidence": False,
            }
        return {
            "operations": [
                {
                    "kind": "create_card",
                    "after": {
                        "type": "fact",
                        "title": "Blue clue",
                        "content": "The clue is blue.",
                    },
                    "evidence_refs": ["E1"],
                    "reason": "Fixture evidence",
                }
            ]
        }

    def text(self, system, user):
        return "The clue is blue."

    def require_vision(self):
        return None

    def describe_image(self, image, *, mime_type):
        return "The clue is blue."


def _text_pdf(path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (The clue is blue.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    with path.open("wb") as handle:
        writer.write(handle)


@pytest.mark.parametrize(
    "format_name",
    [
        "txt",
        "md",
        "docx",
        "json",
        "obsidian",
        "pdf-text",
        "pdf-scan",
        "png",
        "jpeg",
        "claude-jsonl",
        "codex-jsonl",
        "dialog-md",
        "url",
    ],
)
def test_full_path_for_each_source_format(
    tmp_path: Path, format_name: str, monkeypatch
) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        ingestor = SourceIngestor(vault)
        if format_name == "url":
            monkeypatch.setattr(
                socket,
                "getaddrinfo",
                lambda *args, **kwargs: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))
                ],
            )
            client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        text="The clue is blue.",
                        headers={"content-type": "text/plain"},
                        request=request,
                    )
                )
            )
            source = SourceIngestor(vault, http_client=client).ingest_url(
                "https://example.com/clue"
            )
        else:
            path = tmp_path / format_name
            if format_name in {"txt", "md", "json"}:
                path = path.with_suffix("." + format_name)
                path.write_text(
                    json.dumps({"clue": "blue"})
                    if format_name == "json"
                    else "The clue is blue.",
                    encoding="utf-8",
                )
            elif format_name == "docx":
                path = path.with_suffix(".docx")
                path.write_bytes(
                    docx_bytes("<w:p><w:r><w:t>The clue is blue.</w:t></w:r></w:p>")
                )
            elif format_name == "obsidian":
                path.mkdir()
                (path / "note.md").write_text("The clue is blue.", encoding="utf-8")
            elif format_name in {"pdf-text", "pdf-scan"}:
                path = path.with_suffix(".pdf")
                if format_name == "pdf-text":
                    _text_pdf(path)
                else:
                    Image.new("RGB", (80, 80), "blue").save(path, "PDF")
            elif format_name in {"png", "jpeg"}:
                path = path.with_suffix(".png" if format_name == "png" else ".jpg")
                Image.new("RGB", (80, 80), "blue").save(path)
            elif format_name in {"claude-jsonl", "codex-jsonl"}:
                path = path.with_suffix(".jsonl")
                record = (
                    {
                        "type": "user",
                        "uuid": "m1",
                        "message": {"role": "user", "content": "The clue is blue."},
                    }
                    if format_name == "claude-jsonl"
                    else {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "The clue is blue.",
                        },
                    }
                )
                path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            else:
                path = path.with_suffix(".md")
                path.write_text(
                    "# User\nThe clue is blue.\n# Assistant\nAcknowledged.\n",
                    encoding="utf-8",
                )
            source = ingestor.ingest_path(path)[0]
        engine = KnowledgeEngine(vault, FixtureProvider())
        changeset = engine.propose([source.id])
        assert "Blue clue" in vault.diff(changeset.id)
        vault.edit(
            changeset.id,
            changeset.operations[0].id,
            {
                "type": "fact",
                "title": "Blue clue edited",
                "content": "The clue is blue.",
            },
        )
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        garden = engine.garden_scan()
        assert garden.status == "open"
        answer = engine.answer("blue clue")
        assert answer.insufficient_evidence is False
        assert answer.citations[0].source_id == source.id
        if format_name == "docx":
            assert vault.list_evidence(source.id)[0].locator["paragraph"] == 1
