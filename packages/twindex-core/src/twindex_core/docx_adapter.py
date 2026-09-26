"""Extract WordprocessingML text without executing or resolving embedded content."""

from __future__ import annotations

import stat
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from .source_adapters import ParsedSource
from .source_io import AcquiredSource, SourceSecurityError, MAX_ZIP_ENTRIES

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _text(paragraph) -> str:
    # Deleted revisions and field instructions are not current document text.
    def walk(node):
        if node.tag in {W + "del", W + "moveFrom", W + "instrText"}:
            return ""
        if node.tag == W + "t":
            return node.text or ""
        if node.tag == W + "tab":
            return "\t"
        if node.tag in {W + "br", W + "cr"}:
            return "\n"
        return "".join(walk(child) for child in node)

    return walk(paragraph).strip()


def parse_docx(source: AcquiredSource) -> ParsedSource:
    try:
        with ZipFile(BytesIO(source.raw)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(infos) > MAX_ZIP_ENTRIES or len(names) != len(set(names)):
                raise SourceSecurityError("DOCX has too many or duplicate entries")
            total = 0
            for info in infos:
                name = info.filename
                if (
                    info.flag_bits & 1
                    or "\\" in name
                    or "\x00" in name
                    or name.startswith("/")
                    or ".." in PurePosixPath(name).parts
                    or stat.S_ISLNK(info.external_attr >> 16)
                ):
                    raise SourceSecurityError("Unsafe DOCX entry")
                if name.lower().endswith("vbaproject.bin") or name.lower().startswith(
                    "word/embeddings/"
                ):
                    raise SourceSecurityError(
                        "DOCX macros and embedded files are not supported"
                    )
                total += info.file_size
                if (
                    info.file_size > 10 * 1024 * 1024
                    or total > 100 * 1024 * 1024
                    or info.file_size > max(info.compress_size, 1) * 100
                ):
                    raise SourceSecurityError("DOCX expansion limit exceeded")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise SourceSecurityError("Not a valid DOCX document")
            types = fromstring(archive.read("[Content_Types].xml"), forbid_dtd=True)
            if not any(
                node.attrib.get("PartName") == "/word/document.xml"
                and node.attrib.get("ContentType")
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                for node in types
            ):
                raise SourceSecurityError("Unsupported DOCX main document type")
            root = fromstring(archive.read("word/document.xml"), forbid_dtd=True)
            body = root.find(W + "body")
            if root.tag != W + "document" or body is None:
                raise SourceSecurityError("DOCX body is missing")
            segments: list[tuple[str, dict, str]] = []
            counters = {"paragraph": 0, "table": 0}

            def visit(node, location: dict):
                if node.tag in {W + "del", W + "moveFrom"}:
                    return
                if node.tag == W + "p":
                    counters["paragraph"] += 1
                    text = _text(node)
                    if text:
                        segments.append(
                            (
                                text,
                                {
                                    "kind": "docx",
                                    "part": "word/document.xml",
                                    "paragraph": counters["paragraph"],
                                    **location,
                                },
                                "source",
                            )
                        )
                    return
                if node.tag == W + "tbl":
                    counters["table"] += 1
                    table = counters["table"]
                    for row_num, row in enumerate(node.findall(W + "tr"), 1):
                        for cell_num, cell in enumerate(row.findall(W + "tc"), 1):
                            for child in cell:
                                visit(
                                    child,
                                    {"table": table, "row": row_num, "cell": cell_num},
                                )
                    return
                for child in node:
                    visit(child, location)

            visit(body, {})
            if not segments:
                raise SourceSecurityError(
                    "DOCX contains no extractable text; export scanned pages as PDF for vision"
                )
            warnings = []
            if any(
                name.startswith(
                    (
                        "word/header",
                        "word/footer",
                        "word/footnotes",
                        "word/endnotes",
                        "word/comments",
                    )
                )
                for name in names
            ):
                warnings.append(
                    "Headers, footers, footnotes, endnotes and comments are not imported"
                )
            if any(name.startswith("word/media/") for name in names):
                warnings.append(
                    "Embedded images are not analyzed; export as PDF for vision"
                )
            return ParsedSource(
                source.raw,
                kind="docx",
                title=source.title,
                uri=source.uri,
                segments=segments,
                metadata={
                    "warnings": warnings,
                    "paragraphs": counters["paragraph"],
                    "tables": counters["table"],
                },
            )
    except (BadZipFile, KeyError, ParseError, DefusedXmlException, RuntimeError) as exc:
        raise SourceSecurityError("Malformed or unsafe DOCX document") from exc
