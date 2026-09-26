import pytest
from textual.widgets import Input, TextArea

from twindex_cli.tui import TwindexApp
from test_navigation import seed_proposal


@pytest.mark.asyncio
async def test_import_next_step_and_navigation_do_not_pollute_chat(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("Useful fact")
    app = TwindexApp(tmp_path / "vault")
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.click("#next-primary")
        app._import(str(note))
        await pilot.pause()
        assert app._section == "sources"
        assert "Не анализировался" in str(app.query_one("#context-body").render())
        assert app._next_commands["next-primary"] == "/propose"
        count = len(app._messages)
        await pilot.click("#next-secondary")
        assert str(app.query_one("#context-title").render()) == "ИСТОЧНИКИ"
        await pilot.press("escape")
        assert len(app._messages) == count


@pytest.mark.asyncio
async def test_full_card_detail_and_plain_field_editor_preserve_metadata(tmp_path):
    source, change = seed_proposal(tmp_path)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(110, 42)) as pilot:
        original = {
            **change.operations[0].after,
            "content": "Long text. " * 160 + "END_MARKER",
            "custom": {"keep": True},
        }
        change = app.vault.edit(change.id, change.operations[0].id, original)
        app._render_changeset(change)
        await pilot.pause()
        await pilot.press("enter")
        assert "END_MARKER" in str(app.query_one("#context-body").render())
        assert app._next_commands["next-primary"] == "/toggle-selected"
        app.action_edit()
        await pilot.pause()
        assert (
            app.query_one("#editor").styles.color
            == app.query_one("#composer").styles.color
        )
        assert app.query_one("#form-save").region.bottom <= app.size.height
        assert app.query_one("#editor", TextArea).text == original["content"]
        app.query_one("#edit-title", Input).value = "Edited title"
        app.query_one("#editor", TextArea).text = "Edited\nmultiline content"
        app.action_save_edit()
        saved = app.vault.get_changeset(change.id).operations[0]
        assert saved.after["title"] == "Edited title"
        assert saved.after["content"] == "Edited\nmultiline content"
        assert saved.after["custom"] == {"keep": True}
        assert saved.evidence_ids == change.operations[0].evidence_ids
        assert not app.vault.list_cards()
        await pilot.pause()
        await pilot.press("escape")
        assert not app.query_one("#operations").has_class("hidden")


@pytest.mark.asyncio
async def test_detail_select_back_and_confirm_updates_only_selected_card(tmp_path):
    _, change = seed_proposal(tmp_path)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        app._render_changeset(change)
        await pilot.pause()
        await pilot.press("enter")
        app._dispatch("/toggle-selected")
        await pilot.press("escape")
        assert "1/2" in str(app.query_one("#context-title").render())
        assert not app.vault.list_cards()
        app.action_commit()
        assert not app.vault.list_cards()
        app._dispatch("/commit confirm")
        assert len(app.vault.list_cards()) == 1


def test_transcript_wrap_never_enters_marker_column():
    from rich.console import Console
    from twindex_cli.ui_text import transcript_text

    rendered = transcript_text(
        ["[bold]›[/]  " + "слово " * 30 + "\nвторая строка"], Console(), 40
    )
    lines = rendered.plain.splitlines()
    assert lines[0].startswith("›  ")
    assert all(line.startswith("   ") for line in lines[1:])
    assert all(len(line) <= 40 for line in lines)


@pytest.mark.asyncio
async def test_settings_share_input_and_button_style(tmp_path):
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        app._render_model()
        await pilot.pause()
        assert (
            app.query_one("#settings-model").styles.border
            == app.query_one("#composer").styles.border
        )
        assert (
            app.query_one("#settings-vault").styles.border
            == app.query_one("#next-secondary").styles.border
        )
        await pilot.resize_terminal(60, 25)
        assert app.query_one("#form-save").region.bottom <= 25
        app._render_model()
        await pilot.pause()
        assert (
            app.query_one("#settings-profile").region.bottom
            <= app.query_one("#timeline").region.bottom
        )


@pytest.mark.asyncio
async def test_relation_form_preserves_note_and_endpoints(tmp_path):
    from twindex_core.models import OperationDraft

    source, cards = seed_proposal(tmp_path)
    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 40)) as pilot:
        app.vault.stage(cards.id, [op.id for op in cards.operations])
        app.vault.commit(cards.id)
        saved = app.vault.list_cards()
        change = app.vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="add_relation",
                    after={
                        "from_card_id": saved[0].id,
                        "to_card_id": saved[1].id,
                        "type": "related",
                        "note": "Old note",
                    },
                    evidence_ids=[app.vault.list_evidence(source.id)[0].id],
                    reason="Synthetic relationship",
                )
            ],
        )
        app._render_changeset(change)
        app.action_edit()
        await pilot.pause()
        assert app.query_one("#edit-title", Input).value == saved[0].id
        assert app.query_one("#edit-target", Input).value == saved[1].id
        app.query_one("#editor", TextArea).text = "Edited relationship"
        app.action_save_edit()
        after = app.vault.get_changeset(change.id).operations[0].after
        assert after == {**change.operations[0].after, "note": "Edited relationship"}


@pytest.mark.asyncio
async def test_failed_answer_has_private_diagnostics_and_explicit_retry(tmp_path):
    import json
    import stat
    from twindex_core.engine import ModelOutputError

    app = TwindexApp(tmp_path)
    async with app.run_test(size=(100, 40)):
        app._last_question = "Private question"
        app._model_failed(
            "answer",
            ModelOutputError(
                "PRIVATE_OUTPUT",
                diagnostics={
                    "schema": "GeneratedAnswer",
                    "attempts": [
                        {
                            "finish_reason": "stop",
                            "errors": [{"field": "evidence_refs", "code": "missing"}],
                        }
                    ],
                    "unsafe": "PRIVATE_OUTPUT",
                },
            ),
        )
        assert app._next_commands["next-primary"] == "/retry-answer"
        app._dispatch("/debug")
        report = app._last_diagnostic_path
        assert stat.S_IMODE(report.stat().st_mode) == 0o600
        content = report.read_text()
        assert "Private question" not in content and "PRIVATE_OUTPUT" not in content
        assert json.loads(content)["details"]["schema"] == "GeneratedAnswer"
        assert "evidence_refs" in str(app.query_one("#context-body").render())
        calls = []
        app._answer = calls.append
        app._dispatch("/retry-answer")
        assert calls == ["Private question"]
