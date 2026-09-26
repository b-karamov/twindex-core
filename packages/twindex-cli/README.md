# Twindex CLI

Terminal frontend for the sibling `twindex-core` package. Both packages operate independently, without a server or hosted account.
SQLite remains the source of truth; the optional Chroma index is disposable.

## Install

Requires Python 3.12 or newer. From the repository root:

```sh
python3.12 -m venv .venv-core
.venv-core/bin/pip install -e 'packages/twindex-core[dev]' -e 'packages/twindex-cli[dev]'
.venv-core/bin/twindex --help
```

Run `twindex` with no subcommand for the chat-first Textual interface. It uses a
single conversation, a contextual review panel, and one composer instead of a
dashboard. `--vault` selects a vault; next comes `TWINDEX_VAULT`, then the
per-user system data directory. A vault contains `knowledge.sqlite3`, immutable
`sources/` snapshots, and optionally `semantic/`. Do not sync a live SQLite
directory with a file-sync tool; use a consistent backup instead.

The terminal layout fills the available window with consistent text gutters,
the terminal's own background, and warm amber accents. This avoids the old dark
blue fills turning into saturated blue on 256-color terminals. Content scrolls
independently; the conversation input and section controls stay at the bottom.
Contextual buttons guide source import, proposal review, and confirmation;
commands remain available for keyboard-oriented workflows. `NO_COLOR` is respected.

The interaction principles are informed by OpenClaw's
[Clack wizard adapter](https://github.com/openclaw/openclaw/blob/main/src/wizard/clack-prompter.ts)
and [terminal theme](https://github.com/openclaw/openclaw/blob/main/packages/terminal-core/src/theme.ts):
one current decision, nearby keyboard hints, restrained color, and explicit
progress/completion feedback. Twindex keeps its own identity and Textual runtime.

## Local models

```sh
ollama serve
ollama pull qwen3-vl:4b-instruct
ollama pull qwen3-embedding:0.6b
twindex --profile ollama diagnose
```

If Ollama already runs as a service, do not start another `ollama serve`. The
vision model and embedding model can be replaced with `--model` and
`--embedding-model`. `diagnose` checks chat, embeddings, and Ollama's vision
metadata. Import never invokes a model; images and scanned PDF pages are blocked
at proposal time if vision is unavailable.
`diagnose` lists available and blocked operations for the selected model before
you start a proposal. If embeddings are unavailable, FTS search still works.

The `qwen3-vl:4b` tag currently resolves to a thinking variant. In a local
live test it exhausted the output budget in reasoning and returned empty JSON
content, so the usable v1 default is the explicit `4b-instruct` tag. You can
still select the thinking tag with `--model` if your Ollama setup handles it.

For GPT, set `OPENAI_API_KEY` interactively in your shell and explicitly opt in
to sending vault content off-device:

```sh
twindex --profile cloud --allow-cloud-sources diagnose
twindex --profile cloud --allow-cloud-sources propose SOURCE_ID
```

The default cloud model is `gpt-5.6-luna`. A custom remote endpoint also requires
`--allow-cloud-sources` and HTTPS. A custom model's declared `--vision` capability
is verified using a synthetic image before any source image is sent. No API key
is persisted in the vault.

## Workflow

Global options (`--vault`, `--json`, model settings) go before the subcommand.

```sh
twindex --vault ~/knowledge ingest ./note.md
twindex --vault ~/knowledge inbox
twindex --vault ~/knowledge propose SOURCE_ID
twindex --vault ~/knowledge diff CHANGESET_ID
twindex --vault ~/knowledge export-changeset CHANGESET_ID
twindex --vault ~/knowledge stage CHANGESET_ID OPERATION_ID
twindex --vault ~/knowledge commit CHANGESET_ID
twindex --vault ~/knowledge search 'blue labels'
twindex --vault ~/knowledge answer 'What color are release labels?'
twindex --vault ~/knowledge garden
twindex --vault ~/knowledge log
twindex --vault ~/knowledge revert COMMIT_ID
```

Use `--json` for scripts. `changeset-create SOURCE_ID operations.json` creates a
manual proposal; `edit CHANGESET_ID OPERATION_ID after.json` replaces one proposed
operation's `after` object; `unstage` removes a selection. `diff` is a readable
Twindex patch, **not** a `git apply` patch. Only staged operations commit. Stale
versions fail atomically; revert creates a new commit and never erases history.

In the TUI, ordinary text asks a cited question. A filesystem path, URL, or
`Добавь PATH` imports a source. The most useful commands are:

```text
/add PATH              import a file, directory, or URL
/pick                  choose and read a file with the macOS system dialog
/pick folder           choose a Markdown folder with the macOS system dialog
/inbox                 review imported sources
/source N              select a source from Inbox
/propose               generate a typed changeset for the selected source
/op N                  select an operation without the keyboard picker
/edit                  edit title/type/body; /edit raw for advanced JSON
/stage [N|all]         include operations in the atomic commit
/unstage [N|all]       remove operations from the commit selection
/diff raw              show the technical Git-like patch
/commit                review exactly what will be applied
/commit confirm        apply the staged operations atomically
/cards  /card N        browse confirmed knowledge and citations
/search QUERY          deterministic FTS search
/garden                propose duplicate/conflict maintenance
/log  /revert N        inspect history and prepare a safe revert
/model                 inspect local/cloud model privacy
/vault PATH            switch vault without moving or merging data
/help                  show the complete command summary
/back                  return to the previous view
/new                   start a fresh conversation without clearing the vault
```

After `/propose`, use `↑`/`↓` to preview each proposed card or relation and press
`Space` to include or exclude it; `Enter` opens its detail. The header shows the active generation
and embedding models; while a model call is active, the status line animates and
shows its model name and elapsed time. `Ctrl+P`, `Ctrl+E`, `Ctrl+S`, `Ctrl+G`, and
`Ctrl+L` are shortcuts for frequent review actions; `Escape` goes back one view.
Select visible text with the mouse and press `Ctrl+C` (or use `Ctrl+C`
without a selection to copy the current context panel). On macOS the TUI uses
`pbcopy` because macOS Terminal does not support Textual's OSC 52 clipboard path.
Commit and revert remain two-step operations so a shortcut cannot mutate
confirmed knowledge by accident.

### Sections and navigation

- **Разговор**: questions, import paths, and results for this session. Every launch
  starts fresh, without selecting an old source. **Новый разговор** clears only
  the visible conversation, not sources, proposals, cards, or commit history.
- **Источники**: searchable keyboard list. Enter opens a source, then create or
  resume proposals. Draft creation is not labelled as completed processing.
  Delete requires confirmation and removes only unused snapshots and their
  exclusive drafts, never original files. Sources referenced by cards, history,
  or shared drafts can only be archived; their evidence remains available.
  **Архив** opens the archived list; open an item to restore it.
- **Карточки**: search confirmed knowledge, open a card, follow its evidence and
  relations, or start a Gardener review. Proposals never directly change cards.
- **История**: saved changes and a separately confirmed revert.
- **Настройки**: generation/embedding models for this run, diagnosis, local vault
  selection, and conversation-discovery consent. These settings are not saved
  across launches; use CLI flags or environment configuration for the next run.

Use the always-visible section buttons to move between roots. **Назад / Esc**
returns one level, retaining list selection, search, and scroll position. At a
section root it returns to the conversation. **Tab** moves focus, never selects
a source or applies an operation; **Shift+Tab** moves back. Arrow keys navigate
the focused list, **Enter** opens/activates, **Space** stages proposals. Search a
list with its bottom search field and Enter. Press `/` outside a text field to
open command input. **Ctrl+Q** exits; **Ctrl+C** copies.

Leaving an edited proposal asks whether to save or discard; Escape cancels that
decision and preserves the draft text. Saving edits changes only the proposal.
Selected operations still require a separate review and commit confirmation.
Completed model work does not replace a section opened while it was running.

For dialog autodiscovery, `/discover` first asks for permission to inspect known
Claude Code and Codex directories. Permission allows a bounded read of the first
recognizable user phrase so the picker can show `source + conversation preview`;
it does not import anything or contact a model. Use `↑`/`↓` to move, `Enter` to
import the selected dialog, and `Escape` to cancel. `/discover revoke` removes
the permission. The noninteractive `twindex discover --grant` command remains
metadata-only and returns path/size/mtime for scripting.

Supported sources: TXT, Markdown, JSON, DOCX, Obsidian Markdown directory/ZIP, text or
scanned PDF, public HTTP(S) URL, PNG/JPEG, and Claude/Codex JSON/Markdown/JSONL
conversations. Assistant dialog messages are not treated as independent evidence.
Answers cite committed card version and source locator. No source is silently
transmitted during autodiscovery.

### File access and DOCX

On macOS, **Добавить источник → Выбрать файл / Выбрать папку** opens the system
file chooser. Nothing is requested at startup. If reading an explicitly entered
path is denied, the TUI opens that chooser just in time. Cancel imports nothing.
The short-lived AppKit helper reads only the selected file (or the selected
folder's Markdown files) while its access scope is active. It transfers bytes
and the original URI, not a path that the terminal would need to read again.
The source stays local; model use remains a separate, consented action.

This is not a Full Disk Access grant or a bypass of macOS protections. The OS
may still deny access or an iCloud file may be unavailable; the UI reports this
and offers another selection instead of demanding a Settings detour. Scripted
`twindex ingest PATH` never opens a dialog. PyObjC is a macOS-only CLI dependency;
the framework has no AppKit dependency.

DOCX imports main-body paragraphs and table cells with paragraph/table/row/cell
citations, not fabricated page numbers. It never starts Word, runs macros, or
fetches linked content. Headers, footers, notes, comments and embedded images
are not parsed and produce warnings. Image-only documents must be exported to
PDF for the existing vision flow. Malformed XML, unsafe ZIP entries, macros,
embedded files and excessive archive expansion are rejected before persistence.

## Library API

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

Other operations: `vault.edit`, `vault.revert`, `vault.search`,
`engine.garden_scan`, and `engine.search`. `twindex index-rebuild` reconstructs
the optional semantic index from SQLite. The v1 CLI works without Chroma using
FTS5, and without a network connection when pointed at local Ollama.

## Review And Error Recovery

Import opens the saved source, with an explicit next action to propose cards
(or continue an existing draft). Import does not call the model or save cards.
In the proposal list, use arrows to move, Enter to read the full card, and Space
to select it. The full-card view also has a selection button. The fixed footer
shows the selected count and opens a separate confirmation before saving.

The proposal editor uses a title field, type field and multiline text, rather
than escaped JSON. Saving edits changes only the draft and preserves evidence
and unedited metadata. Relations have separate endpoint/type/note fields.
Advanced JSON editing is still available with `/edit raw`. Esc checks for
unsaved edits; the fixed footer keeps Save reachable in a small terminal.
Buttons, navigation commands and Esc are not recorded as user chat messages.

`/propose` in the TUI analyzes the selected **source**, not a card. Without a
selected source it opens Sources and does not call the model. It takes no TUI
arguments; for multiple sources use the terminal CLI:

```sh
twindex --vault ~/knowledge propose SOURCE_ID1 SOURCE_ID2
```

An empty result shows an explanation and **Повторить анализ**. Retry analyzes
the exact sources of that changeset, even if the current source selection has
changed. It creates a new draft without removing old drafts or committed cards.
Empty results are not treated as model errors: a valid response may contain no
operations. Actual provider/schema failures still open the diagnostic flow.
Conversation service context is filtered out; AI replies are included with an
explicit unverified-origin label. Source snapshots are not rewritten.

After a model failure, `/debug` opens the latest local technical report.
`vault/diagnostics/<id>.json` is created with mode `0600` and contains the model,
schema, error class, finish reason, token counts and sanitized validation field
paths/codes when available. It does not store source text, questions, raw model
responses, API keys or HTTP URLs. Reports stay local; no telemetry is sent.
These reports describe new failures, not failures that happened before this
instrumentation. `/retry-answer` explicitly repeats the last question; it is
never retried automatically by the UI. `/diagnose` remains a separate live
connection/capability probe and can make a model request.

## Verification

```sh
PYTHON_BIN=.venv-core/bin/python packages/preflight.sh
```

Live Ollama and cloud-model runs are separate sign-off gates: deterministic
fixtures and mock HTTP tests do not prove model quality, latency, or real
provider compatibility. The package does not migrate or alter the old web/API.
