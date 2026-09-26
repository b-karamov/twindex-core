"""Import facade: acquire bytes, parse all formats, then persist snapshots."""

from __future__ import annotations

import httpx
from pathlib import Path

from .conversations import (
    ConsentRequired as ConsentRequired,
    ConversationFile as ConversationFile,
    conversation_origin as conversation_origin,
    conversation_preview as conversation_preview,
    default_conversation_roots as default_conversation_roots,
    discover_conversations as discover_conversations,
)
from .source_io import (
    AcquiredSource,
    URLAcquirer,
    read_local_sources,
    SourceSecurityError as SourceSecurityError,
    _PublicBackend as _PublicBackend,
)
from .source_adapters import parse_source
from .models import SourceSnapshot
from .vault import Vault


class SourceIngestor:
    def __init__(self, vault: Vault, *, http_client: httpx.Client | None = None):
        self.vault = vault
        self.http_client = http_client

    def ingest_path(self, path: str | Path) -> list[SourceSnapshot]:
        return self.ingest_acquired(read_local_sources(Path(path)))

    def ingest_url(self, url: str) -> SourceSnapshot:
        return self.ingest_acquired([URLAcquirer(self.http_client).fetch(url)])[0]

    def ingest_acquired(self, sources: list[AcquiredSource]) -> list[SourceSnapshot]:
        prepared = [item for source in sources for item in parse_source(source)]
        return [
            self.vault.ingest_segments(
                item.raw,
                kind=item.kind,
                title=item.title,
                uri=item.uri,
                metadata=item.metadata,
                segments=item.segments,
            )
            for item in prepared
        ]
