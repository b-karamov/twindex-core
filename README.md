# Twindex

Local-first knowledge framework with a terminal frontend. Turn notes, documents
and selected AI conversations into evidence-backed cards. Review proposals before
saving: import -> propose -> edit/select -> commit -> search/answer.

**Alpha 0.1.0a1**, not a completed v1. No Twindex server, account, Google ADK or
Vertex required. Core and CLI are separate installable packages; TUI lives in CLI.

## Install

Python 3.12+ (tested on 3.12 and 3.14). Install both from this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install \
  'twindex-core @ git+https://github.com/b-karamov/twindex-core.git@v0.1.0a1#subdirectory=packages/twindex-core' \
  'twindex-cli @ git+https://github.com/b-karamov/twindex-core.git@v0.1.0a1#subdirectory=packages/twindex-cli'
.venv/bin/twindex
```

Alternatively download both wheels from the GitHub prerelease and install them
with ordinary `pip install ./twindex_core-*.whl ./twindex_cli-*.whl`.
PyPI publication is not part of this alpha. Core alone does not install Textual.

## Local Models

Install Ollama separately. If its service already runs, do not start another.

```sh
ollama serve
ollama pull qwen3-vl:4b-instruct
ollama pull qwen3-embedding:0.6b
.venv/bin/twindex --profile ollama diagnose
.venv/bin/twindex --profile ollama
```

Override models with `--model` and `--embedding-model`. Import is local and does
not run a model. Images and scanned PDF analysis require verified vision support.
FTS5 search works without embeddings; optional Chroma is a rebuildable cache.

For cloud generation, set `OPENAI_API_KEY` in your shell, never in the vault.
Cloud content transmission requires explicit consent:

```sh
.venv/bin/twindex --profile cloud --allow-cloud-sources diagnose
.venv/bin/twindex --profile cloud --allow-cloud-sources
```

Cloud default: `gpt-5.6-luna`. Provider availability is not guaranteed by tests.

## Terminal Workflow

Run `twindex` for TUI, `twindex --help` for CLI. Global options precede commands.
TUI sections: Conversation, Sources, Cards, History, Settings. Use Tab for focus,
Enter to open, Space to select proposals, Escape to go back, Ctrl+Q to exit.
A fresh launch starts an empty conversation, while vault knowledge persists.

```sh
twindex --vault ~/knowledge ingest ./note.md
twindex --vault ~/knowledge inbox
twindex --vault ~/knowledge propose SOURCE_ID
twindex --vault ~/knowledge diff CHANGESET_ID
twindex --vault ~/knowledge stage CHANGESET_ID OPERATION_ID
twindex --vault ~/knowledge commit CHANGESET_ID
twindex --vault ~/knowledge answer 'What does my note say?'
```

`--json` supports scripts. CLI/Core accept several source IDs for `propose`;
TUI currently analyzes one selected source (not a selected card). With no source
selected, TUI opens Sources without calling a model. Empty proposals have an
explanation and Repeat Analysis action; they do not prove a source is useless.

Supported inputs: TXT, Markdown, JSON/JSONL conversations, PDF (text/scanned),
DOCX body/tables, PNG/JPEG, Obsidian directories/ZIP, public URLs. Source snapshots
are immutable. Service/system and reasoning content is excluded from conversation
analysis; assistant final replies remain explicitly unverified AI evidence.
macOS native file selection requests access as needed; denied OS access is not
silently bypassed. Other platforms support paths/URLs without Cocoa.

## Python API

```python
from twindex_core import Vault, SourceIngestor, KnowledgeEngine, OpenAICompatibleProvider

with Vault.open("~/knowledge") as vault:
    source = SourceIngestor(vault).ingest_path("note.md")[0]
    with OpenAICompatibleProvider(profile="ollama") as provider:
        engine = KnowledgeEngine(vault, provider)
        proposal = engine.propose([source.id])
        print(vault.diff(proposal.id))
        # Choose operations explicitly; generation never commits knowledge.
        if proposal.operations:
            vault.stage(proposal.id, [proposal.operations[0].id])
            vault.commit(proposal.id)
        answer = engine.answer("What does the note say?")
        print(answer.text, answer.citations)
```

Vault resolution: `--vault`, `TWINDEX_VAULT`, then system user data directory.
Existing vault paths and schemas are unchanged. Back up SQLite consistently; do
not sync a live database with a file-sync tool.

## Development

Install [uv](https://docs.astral.sh/uv/), then:

```sh
uv sync --locked --all-packages --extra semantic
uv run --no-sync bash scripts/check.sh
```

The lock covers the workspace; published wheels require only pip. CI runs Linux
and macOS, Python 3.12/3.14, plus a separate Linux semantic-index job. Releases
are manual: dispatch Release with a version; no publication on ordinary pushes.

## Alpha Limitations

- Proposal context truncates each evidence fragment to 12,000 characters.
- Core `Vault.revert` immediately commits the inverse; CLI/TUI review is separate.
- TUI multi-source selection is not implemented; use CLI or Core for batches.
- DOCX does not reproduce page layout; citations locate paragraphs/table cells.
- Multi-source ingestion is not one database transaction.
- Live Ollama/cloud runs are separate from deterministic offline tests. This
  alpha does not certify current services or complete the v1 live-model gate.

See [Core API](packages/twindex-core/README.md), [CLI guide](packages/twindex-cli/README.md),
[contributing](CONTRIBUTING.md), [security](SECURITY.md), [provenance](docs/PROVENANCE.md)
and [third-party licensing](docs/THIRD_PARTY.md).

Apache-2.0. Copyright 2026 Bulat Karamov. Dependencies retain their own licenses.
