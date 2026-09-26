from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from twindex_core import Vault
from twindex_core.sources import SourceIngestor, SourceSecurityError


def docx_bytes(body: str, extra: dict[str, bytes] | None = None) -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + body
            + "</w:body></w:document>",
        )
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return data.getvalue()


def test_docx_paragraph_table_provenance_and_idempotence(tmp_path: Path):
    path = tmp_path / "Research.docx"
    raw = docx_bytes(
        "<w:p><w:r><w:t>First</w:t></w:r><w:r><w:tab/><w:t>claim</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell fact</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>Last</w:t></w:r></w:p>"
    )
    path.write_bytes(raw)
    with Vault.open(tmp_path / "vault") as vault:
        ingestor = SourceIngestor(vault)
        source = ingestor.ingest_path(path)[0]
        assert source.kind == "docx"
        assert ingestor.ingest_path(path)[0].id == source.id
        evidence = vault.list_evidence(source.id)
        assert [ev.text for ev in evidence] == ["First\tclaim", "Cell fact", "Last"]
        assert evidence[0].locator == {
            "kind": "docx",
            "part": "word/document.xml",
            "paragraph": 1,
        }
        assert evidence[1].locator["table"] == 1
        assert evidence[1].locator["row"] == evidence[1].locator["cell"] == 1
        assert all("page" not in ev.locator for ev in evidence)
        assert vault.source_bytes(source.id) == raw


@pytest.mark.parametrize(
    "extra",
    [
        {"../secret": b"no"},
        {"word/vbaProject.bin": b"macro"},
        {"word/embeddings/embedded.zip": b"nested"},
    ],
)
def test_unsafe_docx_has_no_partial_writes(tmp_path, extra):
    path = tmp_path / "unsafe.docx"
    path.write_bytes(docx_bytes("<w:p><w:r><w:t>Fact</w:t></w:r></w:p>", extra))
    with Vault.open(tmp_path / "vault") as vault:
        with pytest.raises(SourceSecurityError):
            SourceIngestor(vault).ingest_path(path)
        assert not vault.list_sources()


def test_format_adapter_accepts_bytes_without_reading_original(tmp_path):
    from twindex_core.source_io import AcquiredSource
    from twindex_core.source_adapters import parse_source

    acquired = AcquiredSource(
        raw=b"Fact", title="Note.md", uri="file:///unreadable/Note.md", suffix=".md"
    )
    parsed = parse_source(acquired)
    assert parsed[0].segments[0][0] == "Fact"
    with Vault.open(tmp_path) as vault:
        result = SourceIngestor(vault).ingest_acquired([acquired])
        assert result[0].uri == acquired.uri
        assert vault.source_bytes(result[0].id) == b"Fact"


@pytest.mark.parametrize(
    "payload",
    [b"not a zip", b"<broken", b'<!DOCTYPE x [<!ENTITY a "unsafe">]><x>&a;</x>'],
)
def test_docx_rejects_malformed_and_entity_xml(tmp_path, payload):
    raw = docx_bytes("<w:p><w:r><w:t>Fact</w:t></w:r></w:p>")
    if payload != b"not a zip":
        data = BytesIO()
        with ZipFile(BytesIO(raw)) as original, ZipFile(data, "w") as changed:
            changed.writestr(
                "[Content_Types].xml", original.read("[Content_Types].xml")
            )
            changed.writestr("word/document.xml", payload)
        raw = data.getvalue()
    else:
        raw = payload
    path = tmp_path / "bad.docx"
    path.write_bytes(raw)
    with Vault.open(tmp_path / "vault") as vault:
        with pytest.raises(SourceSecurityError):
            SourceIngestor(vault).ingest_path(path)
        assert not vault.list_sources()


def test_docx_rejects_compression_bomb(tmp_path):
    data = BytesIO()
    with ZipFile(data, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * 1_000_000)
    path = tmp_path / "bomb.docx"
    path.write_bytes(data.getvalue())
    with Vault.open(tmp_path / "vault") as vault:
        with pytest.raises(SourceSecurityError, match="expansion"):
            SourceIngestor(vault).ingest_path(path)
        assert not vault.list_sources()


def test_docx_reports_omissions_and_ignores_deleted_text(tmp_path):
    path = tmp_path / "review.docx"
    path.write_bytes(
        docx_bytes(
            "<w:p><w:del><w:r><w:t>Old</w:t></w:r></w:del><w:ins><w:r><w:t>Current</w:t></w:r></w:ins><w:r><w:instrText>DO NOT EXECUTE</w:instrText></w:r></w:p>",
            {"word/header1.xml": b"unused", "word/media/image.png": b"unused"},
        )
    )
    with Vault.open(tmp_path / "vault") as vault:
        source = SourceIngestor(vault).ingest_path(path)[0]
        assert vault.list_evidence(source.id)[0].text == "Current"
        assert len(source.metadata["warnings"]) == 2


def test_parse_batch_failure_does_not_persist_earlier_sources(tmp_path):
    from twindex_core.source_io import AcquiredSource

    with Vault.open(tmp_path) as vault:
        with pytest.raises(SourceSecurityError):
            SourceIngestor(vault).ingest_acquired(
                [
                    AcquiredSource(b"Fact", "note.md", "file:///note.md", ".md"),
                    AcquiredSource(b"bad zip", "bad.docx", "file:///bad.docx", ".docx"),
                ]
            )
        assert not vault.list_sources()
