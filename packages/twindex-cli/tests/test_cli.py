import json
from pathlib import Path

from twindex_cli.cli import main


def run_cli(capsys, *args: str) -> dict:
    assert main(["--json", *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_import_stage_commit_search_revert(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    source_file = tmp_path / "note.txt"
    source_file.write_text("Release labels are blue.", encoding="utf-8")
    source = run_cli(capsys, "--vault", str(vault), "ingest", str(source_file))[0]
    evidence = run_cli(capsys, "--vault", str(vault), "evidence", source["id"])[0]
    op_file = tmp_path / "operation.json"
    op_file.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "kind": "create_card",
                        "after": {
                            "type": "fact",
                            "title": "Release labels",
                            "content": "Blue labels",
                        },
                        "evidence_ids": [evidence["id"]],
                        "reason": "Source text",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    changeset = run_cli(
        capsys, "--vault", str(vault), "changeset-create", source["id"], str(op_file)
    )
    op_id = changeset["operations"][0]["id"]
    run_cli(capsys, "--vault", str(vault), "stage", changeset["id"], op_id)
    commit = run_cli(capsys, "--vault", str(vault), "commit", changeset["id"])
    assert (
        run_cli(capsys, "--vault", str(vault), "search", "blue")[0]["citations"][0][
            "source_id"
        ]
        == source["id"]
    )
    run_cli(capsys, "--vault", str(vault), "revert", commit["id"])
    assert run_cli(capsys, "--vault", str(vault), "cards") == []


def test_discovery_requires_grant_and_revokes(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "chat.jsonl").write_text("private content", encoding="utf-8")
    monkeypatch.setattr("twindex_cli.cli.default_conversation_roots", lambda: [root])
    vault = tmp_path / "vault"
    assert main(["--json", "--vault", str(vault), "discover"]) == 2
    capsys.readouterr()
    found = run_cli(capsys, "--vault", str(vault), "discover", "--grant")
    assert found[0]["size_bytes"] == 15
    assert "private content" not in json.dumps(found)
    run_cli(capsys, "--vault", str(vault), "discover", "--revoke")
    assert main(["--json", "--vault", str(vault), "discover"]) == 2
