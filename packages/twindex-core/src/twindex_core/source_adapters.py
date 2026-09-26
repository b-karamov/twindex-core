from __future__ import annotations

import json
import re
import stat
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from .conversations import _chat_segments, _json_chat_segments, _markdown_chat_segments
from .source_io import (
    AcquiredSource,
    MAX_BYTES,
    MAX_IMAGE_BYTES,
    MAX_PDF_PAGES,
    MAX_ZIP_ENTRIES,
    SourceSecurityError,
)


@dataclass(frozen=True)
class ParsedSource:
    raw: bytes
    kind: str
    title: str
    uri: str
    segments: list[tuple[str, dict, str]]
    metadata: dict = field(default_factory=dict)


SUPPORTED_SUFFIXES = frozenset(
    {".txt", ".md", ".json", ".jsonl", ".pdf", ".png", ".jpg", ".jpeg", ".zip", ".docx"}
)


def parse_source(source: AcquiredSource) -> list[ParsedSource]:
    """Pure format dispatch: no filesystem, model, network or vault access."""
    if len(source.raw) > MAX_BYTES:
        raise SourceSecurityError("Source exceeds 20 MiB")
    suffix = source.suffix.lower()
    formats = FormatAdapters()
    if suffix == ".docx":
        from .docx_adapter import parse_docx

        return [parse_docx(source)]
    if suffix == ".pdf":
        return [formats._ingest_pdf(source.raw, title=source.title, uri=source.uri)]
    if suffix in {".png", ".jpg", ".jpeg"}:
        return [
            formats._ingest_image(
                source.raw, title=source.title, uri=source.uri, suffix=suffix
            )
        ]
    if suffix == ".zip":
        return formats._ingest_zip(source.raw, title=source.title, uri=source.uri)
    if source.metadata.get("transport") == "url" and suffix in {".html", ".txt"}:
        text = source.raw.decode("utf-8-sig", errors="replace")
        if suffix == ".html":
            parser = _HTMLText()
            parser.feed(text)
            text = " ".join(" ".join(parser.parts).split())
        return [
            ParsedSource(
                source.raw,
                kind="url",
                title=source.title,
                uri=source.uri,
                metadata=source.metadata,
                segments=[(text, {"kind": "url", "url": source.uri}, "source")],
            )
        ]
    if suffix == ".jsonl":
        chat = _chat_segments(source.raw)
        return [
            ParsedSource(
                source.raw,
                kind="conversation",
                title=source.title,
                uri=source.uri,
                segments=chat,
            )
        ]
    if suffix not in {".txt", ".md", ".json"}:
        raise ValueError(f"Unsupported source format: {suffix}")
    text = source.raw.decode("utf-8-sig")
    value = json.loads(text) if suffix == ".json" else None
    messages = value.get("messages") if isinstance(value, dict) else value
    is_chat = (
        isinstance(messages, list)
        and any(
            isinstance(row, dict)
            and row.get("role")
            in {"user", "human", "assistant", "system", "developer", "tool"}
            for row in messages
        )
    ) or (
        suffix == ".md"
        and bool(
            re.search(
                r"^\s*#+\s*(user|human|пользователь|assistant(?: \((?:analysis|reasoning|thinking)\))?|claude|codex|ассистент|system|developer|tool|система|analysis|reasoning|thinking)\s*$",
                text,
                re.MULTILINE | re.IGNORECASE,
            )
        )
    )
    chat = (
        _json_chat_segments(value)
        if suffix == ".json"
        else _markdown_chat_segments(text)
        if suffix == ".md"
        else []
    )
    return [
        ParsedSource(
            source.raw,
            kind="conversation" if is_chat else suffix.lstrip("."),
            title=source.title,
            uri=source.uri,
            segments=chat
            if is_chat
            else [(text, {"kind": "text", "start": 0, "end": len(text)}, "source")],
        )
    ]


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.suppressed += 1
        if tag in {"p", "div", "h1", "h2", "h3", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.suppressed = max(0, self.suppressed - 1)

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


class FormatAdapters:
    def _ingest_image(
        self, raw: bytes, *, title: str, uri: str, suffix: str
    ) -> ParsedSource:
        if len(raw) > MAX_IMAGE_BYTES:
            raise SourceSecurityError("Image exceeds 10 MiB")
        with Image.open(BytesIO(raw)) as image:
            image.verify()
        return ParsedSource(
            raw,
            kind="image",
            title=title,
            uri=uri,
            metadata={"format": suffix.lstrip(".")},
            segments=[("", {"kind": "image", "needs_vision": True}, "source")],
        )

    def _ingest_pdf(self, raw: bytes, *, title: str, uri: str) -> ParsedSource:
        reader = PdfReader(BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise SourceSecurityError("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise SourceSecurityError("PDF exceeds 100 pages")
        segments = []
        for index, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            segments.append(
                (
                    text,
                    {"kind": "pdf", "page": index + 1, "needs_vision": not bool(text)},
                    "source",
                )
            )
        return ParsedSource(
            raw,
            kind="pdf",
            title=title,
            uri=uri,
            metadata={"pages": len(reader.pages)},
            segments=segments,
        )

    def _ingest_zip(self, raw: bytes, *, title: str, uri: str) -> list[ParsedSource]:
        prepared = []
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise SourceSecurityError("Archive has too many entries")
            total = 0
            for info in infos:
                parts = Path(info.filename).parts
                if (
                    info.flag_bits & 1
                    or "\\" in info.filename
                    or "\x00" in info.filename
                    or info.filename.startswith("/")
                    or ".." in parts
                    or stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)
                ):
                    raise SourceSecurityError("Unsafe archive entry")
                if info.is_dir():
                    continue
                total += info.file_size
                if (
                    info.file_size > 2 * 1024 * 1024
                    or total > 100 * 1024 * 1024
                    or (info.file_size and not info.compress_size)
                    or (
                        info.compress_size and info.file_size / info.compress_size > 100
                    )
                ):
                    raise SourceSecurityError("Archive expansion limit exceeded")
                if not info.filename.lower().endswith(".md"):
                    raise SourceSecurityError(
                        "Only Markdown files are allowed in Obsidian archives"
                    )
                content = archive.read(info)
                text = content.decode("utf-8-sig")
                prepared.append((info.filename, content, text))
        return [
            ParsedSource(
                content,
                kind="md",
                title=Path(filename).name,
                uri=f"{uri}#{filename}",
                segments=[(text, {"kind": "obsidian", "path": filename}, "source")],
            )
            for filename, content, text in prepared
        ]
