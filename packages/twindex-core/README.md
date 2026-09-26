# twindex-core

Standalone local knowledge engine. Install this package alone to use the Python
API without Textual, a Twindex server, Google ADK, Vertex, or an account. The
SQLite vault is canonical; source blobs are immutable SHA-256 snapshots, and
the optional Chroma index is rebuildable from committed cards.

```sh
python3.12 -m venv .venv-core
.venv-core/bin/pip install -e 'packages/twindex-core[dev]'
```

```python
from twindex_core import KnowledgeEngine, OpenAICompatibleProvider, SourceIngestor, Vault

with Vault.open("~/knowledge") as vault:
    source = SourceIngestor(vault).ingest_path("note.md")[0]
    with OpenAICompatibleProvider(profile="ollama") as provider:
        engine = KnowledgeEngine(vault, provider)
        changeset = engine.propose([source.id])
        print(vault.diff(changeset.id))
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        print(engine.answer("What does the note say?"))
```

`Vault` also exposes `edit`, `revert`, `search`, cards, relations, sources,
changeset export, and commit history. `KnowledgeEngine` exposes `propose`,
`garden_scan`, `search`, and `answer`. `SourceIngestor` handles local paths,
Obsidian Markdown/ZIP, PDF, DOCX, PNG/JPEG, supported dialogs, and public URLs.
Generated proposals never modify committed cards until explicitly staged and
committed. The model adapter validates structured output and fails closed.

The terminal frontend is a separate sibling package: `packages/twindex-cli`.
See its README for installation, consent, model profiles, and CLI commands.

Import is split into acquisition (`source_io.py`: bounded local/URL reads,
`AcquiredSource`), pure parsing (`source_adapters.py`, `docx_adapter.py`,
`conversations.py`: bytes to evidence with locators), and persistence
(`sources.py`: `SourceIngestor`). `ingest_acquired` also accepts bytes read by a
host application's native file chooser, preserving the original URI without
reopening the file. All inputs are parsed before persistence begins; multi-source
database writes are not a single transaction. OS permission UI belongs to the
host, never to the framework.

DOCX currently extracts body paragraphs and table cells. Citations identify
paragraphs and table positions, not pages. Non-imported document parts and
images produce explicit metadata warnings; macros and embedded files are
rejected. XML parsing prohibits DTDs and external entities.

Conversation imports exclude service roles and known leading transport wrappers
(for example, Codex environment context and AGENTS instructions). Original
snapshot bytes remain unchanged. The same filter runs during analysis of older
snapshots, so they do not need reimporting. User messages retain `user_assertion`
provenance; assistant replies are included as `assistant_unverified`, never as
independent evidence from an original work. This label survives committing,
search citations and answers; answers citing AI replies show an explicit warning.
Reasoning channels (`analysis`, `reasoning`, `thinking`), typed thinking blocks
and leading `<think>` blocks are excluded from proposal context. For existing
JSON/JSONL imports, analysis recovers channel metadata from the original snapshot
when the old evidence locator did not retain it. Final replies remain available.

`propose([source_id, ...])` accepts one or several sources and compares them with
existing cards. An empty changeset means no changes were proposed, not a provider
failure or proof that the source is useless. If filtering leaves no content, the
engine returns an empty changeset without calling the model (`model=None`).
