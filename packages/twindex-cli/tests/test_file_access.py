from pathlib import Path

import pytest


def test_native_reader_keeps_permission_until_bytes_are_read(monkeypatch):
    from twindex_cli.native_picker import read_authorized_url

    events = []

    class URL:
        def startAccessingSecurityScopedResource(self):
            events.append("start")
            return True

        def stopAccessingSecurityScopedResource(self):
            events.append("stop")

        def path(self):
            return "/selected/Note.md"

    def read(path):
        events.append("read")
        assert path == Path("/selected/Note.md")
        return []

    monkeypatch.setattr("twindex_cli.native_picker.read_local_sources", read)
    assert read_authorized_url(URL()) == []
    assert events == ["start", "read", "stop"]


def test_native_reader_releases_scope_after_error(monkeypatch):
    from twindex_cli.native_picker import read_authorized_url

    events = []

    class URL:
        def startAccessingSecurityScopedResource(self):
            return True

        def stopAccessingSecurityScopedResource(self):
            events.append("stop")

        def path(self):
            return "/selected/Note.md"

    def read(path):
        raise PermissionError("denied")

    monkeypatch.setattr("twindex_cli.native_picker.read_local_sources", read)
    with pytest.raises(PermissionError):
        read_authorized_url(URL())
    assert events == ["stop"]


def test_cancelled_picker_never_reads_a_source():
    from twindex_cli.file_access import decode_picker_result

    assert decode_picker_result('{"status":"cancelled"}') is None


def test_picker_payload_preserves_original_uri():
    from twindex_cli.file_access import decode_picker_result

    result = decode_picker_result(
        '{"status":"ok","sources":[{"raw":"RmFjdA==","title":"Note.md","suffix":".md","uri":"file:///selected/Note.md"}]}'
    )
    assert result[0].raw == b"Fact"
    assert result[0].uri == "file:///selected/Note.md"


@pytest.mark.asyncio
async def test_picker_cancel_and_success_do_not_call_model(tmp_path, monkeypatch):
    from twindex_cli.tui import TwindexApp
    from twindex_core.source_io import AcquiredSource

    app = TwindexApp(tmp_path)
    monkeypatch.setattr("twindex_cli.tui.sys.platform", "darwin")
    monkeypatch.setattr(app._file_picker, "pick", lambda *args, **kwargs: None)
    async with app.run_test(size=(100, 35)):
        app._start_native_import()
        await app.workers.wait_for_complete()
        assert not app.vault.list_sources()
        assert "отменён" in str(app.query_one("#status").render())
        monkeypatch.setattr(
            app._file_picker,
            "pick",
            lambda *args, **kwargs: [
                AcquiredSource(b"Fact", "Note.md", "file:///selected/Note.md", ".md")
            ],
        )
        app._start_native_import()
        await app.workers.wait_for_complete()
        assert app.vault.list_sources()[0].uri == "file:///selected/Note.md"
        assert not app.vault.list_cards()
        assert not app._native_busy


@pytest.mark.asyncio
async def test_import_action_offers_native_file_and_folder_selection(
    tmp_path, monkeypatch
):
    from twindex_cli.tui import TwindexApp

    app = TwindexApp(tmp_path)
    monkeypatch.setattr("twindex_cli.tui.sys.platform", "darwin")
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.click("#next-primary")
        assert str(app.query_one("#next-primary").label) == "Выбрать файл…"
        assert str(app.query_one("#next-secondary").label) == "Выбрать папку…"
        assert "DOCX" in str(app.query_one("#context-body").render())
