import pytest
from twindex_core import Vault
from twindex_cli.tui import TwindexApp


class EmptyProvider:
    model = "fake"
    calls = 0

    def structured(self, *args):
        self.calls += 1
        return {"operations": []}


@pytest.mark.asyncio
async def test_empty_result_has_explanation_and_repeats_exact_sources(tmp_path):
    with Vault.open(tmp_path) as vault:
        a = vault.ingest_text("First")
        b = vault.ingest_text("Second")
        change = vault.create_changeset([a.id, b.id], [], model="fake")
    provider = EmptyProvider()
    app = TwindexApp(tmp_path, provider_factory=lambda: provider)
    async with app.run_test(size=(100, 36)) as pilot:
        app._render_changeset(change)
        await pilot.pause()
        assert "не нашла полезной" in str(app.query_one("#context-body").render())
        assert app.query_one("#operations").has_class("hidden")
        assert app.query_one("#review-save").has_class("hidden")
        assert app._next_commands["next-primary"] == "/retry-propose"
        app.source_id = b.id
        app._dispatch("/retry-propose")
        assert provider.calls == 1
        assert app.vault.get_changeset(app.changeset_id).source_ids == [a.id, b.id]
        assert not app.vault.list_cards()


@pytest.mark.asyncio
async def test_propose_without_source_opens_picker_without_model_call(tmp_path):
    provider = EmptyProvider()
    app = TwindexApp(tmp_path, provider_factory=lambda: provider)
    async with app.run_test() as pilot:
        app.action_propose()
        await pilot.pause()
        assert app._section == "sources"
        assert provider.calls == 0
        assert not app.vault.list_changesets()


@pytest.mark.asyncio
async def test_repeat_while_busy_does_not_reset_active_request(tmp_path):
    provider = EmptyProvider()
    app = TwindexApp(tmp_path, provider_factory=lambda: provider)
    async with app.run_test():
        app._model_busy = True
        app._propose_sources(["source"])
        assert app._model_busy
        assert provider.calls == 0


@pytest.mark.asyncio
async def test_propose_arguments_are_not_silently_ignored(tmp_path):
    provider = EmptyProvider()
    app = TwindexApp(tmp_path, provider_factory=lambda: provider)
    async with app.run_test():
        app.source_id = app.vault.ingest_text("First").id
        with pytest.raises(ValueError, match="без аргументов"):
            app._dispatch("/propose 1 2")
        assert provider.calls == 0
        assert not app.vault.list_changesets()
