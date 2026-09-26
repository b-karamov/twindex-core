import json
import os
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from textual.widgets import Input

from twindex_core import Vault
from twindex_core.engine import VisionUnavailable
from twindex_core.models import OperationDraft
from twindex_cli.tui import TwindexApp


class FakeProvider:
    model = "fake"

    def structured(self, system, user, schema):
        return {
            "operations": [
                {
                    "kind": "create_card",
                    "temporary_id": "new:one",
                    "after": {
                        "type": "fact",
                        "title": "Blue labels",
                        "content": "Labels are blue",
                    },
                    "evidence_refs": ["E1"],
                    "reason": "Source",
                }
            ]
        }

    def text(self, system, user):
        return "Labels are blue"

    def require_vision(self):
        return None

    def describe_image(self, image, *, mime_type):
        return "Blue labels"


async def submit(app: TwindexApp, pilot, value: str) -> None:
    app.action_command()
    composer = app.query_one("#composer")
    composer.focus()
    composer.value = value
    await pilot.press("enter")


@pytest.mark.asyncio
async def test_tui_opens_as_single_chat_shell_with_twindex_identity(
    tmp_path: Path,
) -> None:
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=(100, 32)):
        assert len(app.query("#sidebar")) == 0
        assert len(app.query("#actions")) == 0
        assert app.query_one("#composer").has_focus
        assert not app.query("#brand-mark")
        assert app.query_one("#brand").styles.color.hex == "#E5B567"
        assert "Twindex" in str(app.query_one("#brand").render())
        environment = str(app.query_one("#environment").render())
        assert "qwen3-vl:4b-instruct" in environment
        assert "qwen3-embedding:0.6b" in environment
        assert "Добавьте источник" in str(app.query_one("#welcome-copy").render())
        assert str(app.query_one("#status").render()) == ""
        topbar = app.query_one("#topbar")
        assert app.query_one("#brand").region.y == topbar.region.y + 1


@pytest.mark.asyncio
async def test_model_settings_are_opened_from_the_shared_composer(
    tmp_path: Path,
) -> None:
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=(100, 32)) as pilot:
        composer = app.query_one("#composer")
        composer.value = "/model"
        await pilot.press("enter")

        assert "МОДЕЛЬ И ХРАНИЛИЩЕ" in str(app.query_one("#context-title").render())
        context = str(app.query_one("#context-body").render())
        assert "Ollama" in context
        assert "--allow-cloud-sources" in context
        assert app.query_one("#settings-profile").has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(40, 28), (60, 28), (130, 32)])
async def test_welcome_spacing_and_button_insets(tmp_path, size):
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.query_one("#topbar").styles.border_bottom[0] == "solid"
        marker = app.query_one("#welcome-marker")
        copy = app.query_one("#welcome-copy")
        assert copy.region.x == marker.region.x + 3
        assert copy.region.y == marker.region.y
        lines = [strip.text for strip in copy.render_lines(copy.region.reset_offset)]
        assert lines[0].startswith("С чего начнём?")
        assert not lines[1].strip()
        assert lines[2].startswith("Добавьте источник:")
        assert all(not line.startswith(" ") for line in lines if line.strip())
        assert app.query_one("#next-actions").region.y - copy.region.bottom == 1
        for button_id in ("next-primary", "next-secondary"):
            button = app.query_one(f"#{button_id}")
            assert button.styles.padding.left == button.styles.padding.right == 2
            assert button.region.right <= size[0]
            rendered = button.render_lines(button.region.reset_offset)[1].text
            assert rendered.startswith("│  ") and rendered.endswith("  │")


@pytest.mark.asyncio
async def test_vision_failure_preserves_source_and_offers_model_recovery(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    with Vault.open(vault_path) as vault:
        vault.ingest_text("Scanned page", title="scan.pdf")

    class NoVisionProvider(FakeProvider):
        def structured(self, system, user, schema):
            raise VisionUnavailable(
                "Model local-text does not advertise vision support"
            )

    app = TwindexApp(vault_path, provider_factory=NoVisionProvider)
    async with app.run_test(size=(100, 32)) as pilot:
        if app.source_id is None:
            await submit(app, pilot, "/source 1")
        await submit(app, pilot, "/propose")

        assert "НУЖНА VISION-МОДЕЛЬ" in str(app.query_one("#context-title").render())
        assert "/model" in str(app.query_one("#context-body").render())
        assert len(app.vault.list_sources()) == 1
        assert app.vault.list_changesets() == []


@pytest.mark.asyncio
async def test_commit_conflict_keeps_changeset_open_and_explains_recovery(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    with Vault.open(vault_path) as vault:
        source = vault.ingest_text("Original", title="Source")
        evidence = vault.list_evidence(source.id)[0]
        created = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": "Plan", "content": "v1"},
                    evidence_ids=[evidence.id],
                )
            ],
        )
        vault.stage(created.id, [created.operations[0].id])
        vault.commit(created.id)
        card = vault.list_cards()[0]
        stale = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="update_card",
                    target_id=card.id,
                    base_version=card.version,
                    after={"content": "stale edit"},
                    evidence_ids=[evidence.id],
                )
            ],
        )
        vault.stage(stale.id, [stale.operations[0].id])
        concurrent = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="update_card",
                    target_id=card.id,
                    base_version=card.version,
                    after={"content": "newer edit"},
                    evidence_ids=[evidence.id],
                )
            ],
        )
        vault.stage(concurrent.id, [concurrent.operations[0].id])
        vault.commit(concurrent.id)

    app = TwindexApp(vault_path, provider_factory=FakeProvider)
    async with app.run_test(size=(100, 32)) as pilot:
        app.changeset_id = stale.id
        app.operation_id = stale.operations[0].id
        await submit(app, pilot, "/commit")
        await submit(app, pilot, "/commit confirm")

        assert "КОНФЛИКТ ВЕРСИЙ" in str(app.query_one("#context-title").render())
        assert "не применена" in str(app.query_one("#context-body").render())
        assert app.vault.get_changeset(stale.id).status == "open"


@pytest.mark.asyncio
async def test_conversation_discovery_uses_consent_then_keyboard_preview_and_import(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    older = root / "claude-chat.jsonl"
    older.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "Plan the launch"},
            }
        ),
        encoding="utf-8",
    )
    newer = root / "codex-chat.jsonl"
    newer.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "Review the knowledge graph",
                },
            }
        ),
        encoding="utf-8",
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    monkeypatch.setattr("twindex_cli.tui.default_conversation_roots", lambda: [root])
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=(100, 32)) as pilot:
        await submit(app, pilot, "/discover")

        assert "Найти диалоги" in str(app.query_one("#context-title").render())
        assert "Enter" in str(app.query_one("#context-body").render())
        chooser = app.query_one("#chooser")
        assert chooser.has_focus
        await pilot.press("enter")
        await pilot.pause()

        choices = "\n".join(str(label.render()) for label in chooser.query("Label"))
        assert "Codex" in choices
        assert "Review the knowledge graph" in choices
        assert "Claude Code" in choices
        assert "Plan the launch" in choices
        assert "Review the knowledge graph" in str(
            app.query_one("#chooser-preview").render()
        )

        await pilot.press("down")
        assert "Plan the launch" in str(app.query_one("#chooser-preview").render())
        await pilot.press("enter")

        assert app.vault.list_sources()[0].title == "claude-chat.jsonl"
        assert "Источник сохранён локально" in str(
            app.query_one("#conversation").render()
        )


@pytest.mark.asyncio
async def test_help_is_progressive_and_context_can_be_copied(
    tmp_path: Path, monkeypatch
) -> None:
    copied = []
    monkeypatch.setattr(
        "twindex_cli.tui.subprocess.run",
        lambda *args, **kwargs: copied.append(kwargs["input"]),
    )
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=(100, 32)) as pilot:
        await submit(app, pilot, "/help")

        body = str(app.query_one("#context-body").render())
        assert "Добавить источник" in body
        assert "После импорта" in body
        assert "/help all" in body
        await pilot.press("ctrl+c")
        assert "Добавить источник" in app.clipboard
        if sys.platform == "darwin":
            assert "Добавить источник" in copied[0]
        else:
            assert copied == []


@pytest.mark.asyncio
async def test_unsupported_import_explains_formats_without_raw_exception(
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "notes.xlsx"
    unsupported.write_bytes(b"not a real spreadsheet")
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=(100, 32)) as pilot:
        await submit(app, pilot, f'/add "{unsupported}"')

        conversation = str(app.query_one("#conversation").render())
        assert "формат .xlsx пока не поддерживается" in conversation
        assert "TXT, Markdown, JSON" in conversation
        assert "Unsupported source format" not in conversation


@pytest.mark.parametrize(
    "value,expected",
    [
        ("'/Users/test/Private Vault/Note.md'", "/Users/test/Private Vault/Note.md"),
        ('"/Users/test/Private Vault/Note.md"', "/Users/test/Private Vault/Note.md"),
        (r"/Users/test/Private\ Vault/Note.md", "/Users/test/Private Vault/Note.md"),
        ("/Users/test/Private Vault/Note.md", "/Users/test/Private Vault/Note.md"),
        ("/add /Users/test/Two  spaces.md", "/Users/test/Two  spaces.md"),
        ("'~/missing.docx'", "~/missing.docx"),
    ],
)
def test_pasted_paths_are_imports_not_model_questions(
    tmp_path, monkeypatch, value, expected
):
    app = TwindexApp(tmp_path / "vault")
    imported, questions = [], []
    monkeypatch.setattr(app, "_import", imported.append)
    monkeypatch.setattr(app, "_answer", questions.append)
    try:
        app._dispatch(value)
        assert imported == [expected]
        assert not questions
        app._dispatch("What is a 'knowledge card'?")
        assert questions == ["What is a 'knowledge card'?"]
    finally:
        app.vault.close()


@pytest.mark.asyncio
async def test_denied_stat_explains_access_without_sending_path_to_model(
    tmp_path, monkeypatch
):
    source = tmp_path / "iCloud" / "Note.md"
    app = TwindexApp(tmp_path / "vault")
    requested = []
    monkeypatch.setattr(
        app, "_start_native_import", lambda target, **kwargs: requested.append(target)
    )
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == source:
            raise PermissionError(1, "Operation not permitted", str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    monkeypatch.setattr("twindex_cli.tui.sys.platform", "darwin")
    async with app.run_test(size=(100, 32)) as pilot:
        await submit(app, pilot, f'/add "{source}"')
        message = str(app.query_one("#conversation").render())
        assert requested == [source]
        assert "системном окне" in message
        assert "Files and Folders" not in message
        assert "не найдены" not in message
        assert not app.vault.list_sources()


@pytest.mark.asyncio
async def test_review_shortcuts_report_missing_context_without_crashing(
    tmp_path: Path,
) -> None:
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=(80, 28)) as pilot:
        await pilot.press("ctrl+e")
        assert "Действие пока недоступно" in str(
            app.query_one("#conversation").render()
        )
        assert str(app.query_one("#status").render()) == ""
        await pilot.press("ctrl+s")
        assert app.query_one("#composer").is_mounted


@pytest.mark.asyncio
async def test_tui_keyboard_review_edit_commit_narrow(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    with Vault.open(vault_path) as vault:
        vault.ingest_text("Labels are blue", title="Source")
    app = TwindexApp(vault_path, provider_factory=FakeProvider)
    async with app.run_test(size=(60, 25)) as pilot:
        if app.source_id is None:
            await submit(app, pilot, "/source 1")
        await submit(app, pilot, "/propose")
        assert "ВЫБРАНО" in str(app.query_one("#context-title").render())
        assert "Blue labels" in str(app.query_one("#chooser-preview").render())
        assert "Labels are blue" in str(app.query_one("#chooser-preview").render())
        assert app.query_one("#operations").has_focus
        await pilot.press("space")
        assert app.vault.get_changeset(app.changeset_id or "").operations[0].staged
        editor = app.query_one("#editor")
        await submit(app, pilot, "/edit")
        assert editor.has_focus
        app.query_one("#edit-title", Input).value = "Edited labels"
        editor.text = "Labels are blue"
        await pilot.press("escape")
        assert "НЕСОХРАНЁННЫЕ" in str(app.query_one("#context-title").render())
        await submit(app, pilot, "/edit-leave save")
        await submit(app, pilot, "/commit")
        assert "ПЕРЕД COMMIT" in str(app.query_one("#context-title").render())
        await submit(app, pilot, "/commit confirm")
        assert "Commit сохранён" in str(app.query_one("#status").render())
    with Vault.open(vault_path) as vault:
        assert vault.list_cards()[0].title == "Edited labels"


@pytest.mark.asyncio
async def test_activity_indicator_keeps_label_aligned(tmp_path):
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)
    async with app.run_test(size=(120, 32)):
        app._begin_model_activity("Модель анализирует источник")
        app._busy_timer.pause()
        app._busy_frame = 0
        for frame in ("[=  ]", "[ = ]", "[  =]", "[ = ]"):
            app._tick_model_activity()
            text = str(app.query_one("#status").render())
            assert text.startswith(f"{frame} Модель анализирует источник")
            assert text.index("Модель") == 6
        app._end_model_activity()


@pytest.mark.asyncio
async def test_tui_model_request_runs_without_blocking_ui(
    tmp_path: Path, monkeypatch
) -> None:
    vault_path = tmp_path / "vault"
    with Vault.open(vault_path) as vault:
        vault.ingest_text("Labels are blue")
    release = Event()

    class DelayedProvider(FakeProvider):
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def structured(self, system, user, schema):
            release.wait(timeout=2)
            return super().structured(system, user, schema)

    monkeypatch.setattr("twindex_cli.tui.OpenAICompatibleProvider", DelayedProvider)
    app = TwindexApp(vault_path)
    async with app.run_test(size=(60, 25)) as pilot:
        if app.source_id is None:
            await submit(app, pilot, "/source 1")
        await submit(app, pilot, "/propose")
        first_status = str(app.query_one("#status").render())
        assert "Модель анализирует источник" in first_status
        assert "qwen3-vl:4b-instruct" in first_status
        first_frame = app._busy_frame
        await pilot.pause(0.2)
        # The animation is cyclic; equal glyphs can still mean multiple ticks.
        assert app._busy_frame > first_frame
        release.set()
        await pilot.pause(0.15)
        assert "Blue labels" in str(app.query_one("#chooser-preview").render())


@pytest.mark.asyncio
async def test_changeset_review_uses_readable_cards_and_keyboard_selection(
    tmp_path: Path,
) -> None:
    class TwoCardProvider(FakeProvider):
        def structured(self, system, user, schema):
            return {
                "operations": [
                    {
                        "kind": "create_card",
                        "temporary_id": "new:one",
                        "after": {
                            "type": "fact",
                            "title": "First card",
                            "content": "First readable summary",
                        },
                        "evidence_refs": ["E1"],
                        "reason": "First source fragment",
                    },
                    {
                        "kind": "create_card",
                        "temporary_id": "new:two",
                        "after": {
                            "type": "fact",
                            "title": "Second card",
                            "content": "Second readable summary",
                        },
                        "evidence_refs": ["E1"],
                        "reason": "Second source fragment",
                    },
                ]
            }

    vault_path = tmp_path / "vault"
    with Vault.open(vault_path) as vault:
        vault.ingest_text("Both facts", title="Source")
    app = TwindexApp(vault_path, provider_factory=TwoCardProvider)

    async with app.run_test(size=(100, 32)) as pilot:
        if app.source_id is None:
            await submit(app, pilot, "/source 1")
        await submit(app, pilot, "/propose")

        body = str(app.query_one("#context-body").render())
        assert "↑↓" in body
        assert "Space" in body
        assert "changeset cs_" not in body
        assert "First readable summary" in str(
            app.query_one("#chooser-preview").render()
        )

        await pilot.press("down")
        assert "Second readable summary" in str(
            app.query_one("#chooser-preview").render()
        )
        await pilot.press("space")
        changeset = app.vault.get_changeset(app.changeset_id or "")
        assert [operation.staged for operation in changeset.operations] == [False, True]
        assert "1/2 ВЫБРАНО" in str(app.query_one("#context-title").render())
        assert app.query_one("#operations").index == 1
        await pilot.press("space")
        changeset = app.vault.get_changeset(app.changeset_id or "")
        assert not any(operation.staged for operation in changeset.operations)
        assert app.query_one("#operations").index == 1

        await submit(app, pilot, "/diff raw")
        assert "changeset cs_" in str(app.query_one("#context-body").render())


@pytest.mark.asyncio
async def test_header_uses_explicit_model_names(tmp_path: Path) -> None:
    args = SimpleNamespace(
        profile="ollama",
        model="local-model:latest",
        embedding_model="local-embed:latest",
    )
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider, args=args)

    async with app.run_test(size=(120, 28)):
        environment = str(app.query_one("#environment").render())
        assert "local-model:latest" in environment
        assert "local-embed:latest" in environment


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 28), (120, 36), (160, 40)])
async def test_guided_actions_import_select_review_and_confirm(tmp_path, size):
    source = tmp_path / "note.md"
    source.write_text("Labels are blue", encoding="utf-8")
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)

    async with app.run_test(size=size) as pilot:
        actions = app.query_one("#next-actions")
        composer = app.query_one("#composer")
        assert composer.region.bottom >= size[1] - 3
        assert actions.region.x == app.query_one("#welcome-copy").region.x
        column = app.query_one("#flow").region
        assert column.x == composer.region.x == app.query_one("#brand").region.x
        assert abs(column.x - (size[0] - column.right)) <= 1
        app.query_one("#next-primary").focus()
        await pilot.press("enter")
        assert composer.has_focus
        assert composer.value == "/add "
        await submit(app, pilot, f'/add "{source}"')
        assert "Предложить карточки" in str(app.query_one("#next-primary").label)

        app.query_one("#next-primary").focus()
        await pilot.press("enter")
        assert app.query_one("#operations").has_focus
        highlighted = app.query_one("#operations").highlighted_child
        assert highlighted is not None
        assert highlighted.styles.color.hex == "#E5B567"
        assert highlighted.styles.background.ansi == -1
        assert composer.region.bottom < size[1]
        assert app.query_one("#next-primary").disabled
        await pilot.press("space")
        assert not app.query_one("#next-primary").disabled
        assert app.vault.list_cards() == []

        app.query_one("#next-primary").focus()
        await pilot.press("enter")
        assert "Сохранить выбранное" in str(app.query_one("#next-primary").label)
        assert app.vault.list_cards() == []
        app.query_one("#next-primary").focus()
        await pilot.press("enter")
        assert app.vault.list_cards()[0].title == "Blue labels"


@pytest.mark.asyncio
async def test_start_action_opens_discovery_consent(tmp_path):
    app = TwindexApp(tmp_path / "vault", provider_factory=FakeProvider)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.click("#next-secondary")
        assert "Найти диалоги" in str(app.query_one("#context-title").render())
        assert app.query_one("#chooser").has_focus
        highlighted = app.query_one("#chooser").highlighted_child
        assert highlighted is not None
        assert highlighted.styles.color.hex == "#E5B567"
        assert highlighted.styles.background.ansi == -1
        assert app.vault.list_sources() == []


@pytest.mark.asyncio
async def test_tui_can_switch_vault_without_moving_data(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    with Vault.open(first) as vault:
        vault.ingest_text("First vault")
    app = TwindexApp(first, provider_factory=FakeProvider)
    async with app.run_test(size=(60, 25)) as pilot:
        await submit(app, pilot, f'/vault "{second}"')
        assert app.vault.root == second.resolve()
        assert app.vault.list_sources() == []
    with Vault.open(first) as vault:
        assert len(vault.list_sources()) == 1
