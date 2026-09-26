from pathlib import Path

import pytest
from textual.widgets import Input, ListView, TextArea

from twindex_core import Vault
from twindex_cli.tui import TwindexApp
from twindex_core.models import OperationDraft


def seed_proposal(path):
    with Vault.open(path) as vault:
        source = vault.ingest_text("Fact", title="Test note")
        change = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"title": title, "content": "Fact", "type": "fact"},
                    evidence_ids=[vault.list_evidence(source.id)[0].id],
                    reason="Evidence",
                )
                for title in ("First fact", "Second fact")
            ],
        )
    return source, change


@pytest.mark.asyncio
async def test_full_window_fresh_start_and_keyboard_back(tmp_path: Path):
    with Vault.open(tmp_path) as vault:
        vault.ingest_text("First", title="First")
        vault.ingest_text("Second", title="Second")
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(150, 40)) as pilot:
        assert app.source_id is None
        assert app.query_one("#composer").region.bottom >= 37
        assert app.query_one("#flow").region.width >= 140
        assert (
            app.query_one("#next-actions").region.x
            == app.query_one("#welcome-copy").region.x
        )
        await pilot.click("#nav-sources")
        chooser = app.query_one("#chooser", ListView)
        assert chooser.has_focus
        await pilot.press("down", "enter")
        assert "First" in str(app.query_one("#context-body").render())
        await pilot.press("escape")
        assert chooser.has_focus and chooser.index == 1
        await pilot.press("escape")
        assert app.query_one("#composer").has_focus
        assert app.source_id is None
        await pilot.resize_terminal(180, 50)
        assert app.query_one("#composer").region.bottom >= 47
        assert app.query_one("#flow").region.width >= 170


@pytest.mark.asyncio
async def test_source_removal_requires_explicit_confirmation(tmp_path):
    with Vault.open(tmp_path) as vault:
        source = vault.ingest_text("Delete me")
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.click("#nav-sources")
        await pilot.press("enter")
        await pilot.click("#source-remove")
        assert app.vault.get_source(source.id)
        await pilot.press("escape")
        assert app.vault.get_source(source.id)
        await pilot.click("#source-remove")
        await pilot.click("#next-primary")
        assert app.vault.list_sources() == []


@pytest.mark.asyncio
async def test_archive_restore_preserves_card_evidence(tmp_path):
    source, change = seed_proposal(tmp_path)
    with Vault.open(tmp_path) as vault:
        vault.stage(change.id, [change.operations[0].id])
        vault.commit(change.id)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 35)) as pilot:
        assert app._source_status(source.id) == "Часть изменений сохранена"
        await pilot.click("#nav-sources")
        await pilot.press("enter")
        await pilot.click("#source-remove")
        assert "ИСПОЛЬЗУЕТСЯ" in str(app.query_one("#context-title").render())
        await pilot.click("#next-primary")
        assert app.vault.get_source(source.id).metadata["archived"]
        assert app.vault.search("Fact")[0].citations
        await pilot.click("#source-archive")
        await pilot.press("enter")
        await pilot.click("#source-remove")
        await pilot.click("#next-primary")
        assert not app.vault.get_source(source.id).metadata["archived"]
        assert app.vault.source_bytes(source.id) == b"Fact"


@pytest.mark.asyncio
async def test_search_back_and_unsaved_editor_navigation(tmp_path):
    _, change = seed_proposal(tmp_path)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.click("#nav-sources")
        app.query_one("#section-search", Input).value = "Test"
        app.query_one("#section-search").focus()
        await pilot.press("enter", "enter", "escape")
        assert app.query_one("#section-search", Input).value == "Test"
        assert app.query_one("#chooser", ListView).index == 0
        app._render_changeset(change)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.click("#next-secondary")
        editor = app.query_one("#editor", TextArea)
        app.query_one("#edit-title", Input).value = "Edited fact"
        await pilot.click("#nav-cards")
        assert "НЕСОХРАНЁННЫЕ" in str(app.query_one("#context-title").render())
        await pilot.press("escape")
        assert app.query_one("#edit-title", Input).value == "Edited fact"
        assert not editor.has_class("hidden")
        await pilot.click("#nav-cards")
        await pilot.click("#next-secondary")
        assert app._section == "cards"
        assert (
            app.vault.get_changeset(change.id).operations[0].after["title"]
            == "First fact"
        )


@pytest.mark.asyncio
async def test_model_completion_does_not_replace_active_section(tmp_path):
    _, change = seed_proposal(tmp_path)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        app._begin_model_activity("Анализирую")
        await pilot.click("#nav-cards")
        app._model_success("propose", change)
        assert app._section == "cards"
        assert "КАРТОЧКИ" in str(app.query_one("#context-title").render())
        assert not app._model_busy


@pytest.mark.asyncio
async def test_back_from_commit_keeps_selection_and_staging(tmp_path):
    _, change = seed_proposal(tmp_path)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 36)) as pilot:
        app._render_changeset(change)
        await pilot.pause()
        await pilot.press("space", "down", "space")
        await pilot.press("tab", "enter")
        assert "ПЕРЕД COMMIT" in str(app.query_one("#context-title").render())
        await pilot.press("escape")
        operations = app.query_one("#operations", ListView)
        assert len(operations.children) == 2
        assert operations.index == 1
        assert all(op.staged for op in app.vault.get_changeset(change.id).operations)
        assert not app._commit_armed
        assert not app.vault.list_cards()
