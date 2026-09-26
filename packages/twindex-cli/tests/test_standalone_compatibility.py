from twindex_cli.tui import TwindexApp


def test_environment_label_before_app_starts(tmp_path):
    app = TwindexApp(tmp_path)
    try:
        assert "qwen3-vl:4b-instruct" in app._environment_label()
    finally:
        app.vault.close()
