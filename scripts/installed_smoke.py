import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import twindex_cli
import twindex_core
from twindex_cli.tui import TwindexApp
from twindex_core import KnowledgeEngine, SourceIngestor, Vault

for module in (twindex_core, twindex_cli):
    assert "site-packages" in module.__file__, module.__file__


class LocalFixture:
    model = "offline-fixture"

    def structured(self, system, user, schema):
        if "context" in json.loads(user):
            return {
                "text": "Release labels are blue.",
                "evidence_refs": ["C1"],
                "insufficient_evidence": False,
            }
        return {
            "operations": [
                {
                    "kind": "create_card",
                    "after": {
                        "type": "fact",
                        "title": "Release labels",
                        "content": "Release labels are blue.",
                    },
                    "evidence_refs": ["E1"],
                    "reason": "The note states the color.",
                }
            ]
        }


async def smoke():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        note = root / "note.md"
        note.write_text("Release labels are blue.")
        with Vault.open(root / "vault") as vault:
            source = SourceIngestor(vault).ingest_path(note)[0]
            engine = KnowledgeEngine(vault, LocalFixture())
            proposal = engine.propose([source.id])
            assert vault.diff(proposal.id)
            vault.edit(
                proposal.id,
                proposal.operations[0].id,
                {
                    "type": "fact",
                    "title": "Release labels",
                    "content": "Release labels are blue. Edited.",
                },
            )
            vault.stage(proposal.id, [proposal.operations[0].id])
            commit = vault.commit(proposal.id)
            assert engine.garden_scan().id
            answer = engine.answer("blue")
            assert answer.citations[0].source_id == source.id
            assert answer.citations[0].card_version == 1
        command = [
            sys.executable,
            "-I",
            "-m",
            "twindex_cli",
            "--json",
            "--vault",
            str(root / "vault"),
            "cards",
        ]
        cards = json.loads(
            await asyncio.to_thread(
                subprocess.check_output, command, text=True, cwd=root
            )
        )
        assert len(cards) == 1
        app = TwindexApp(root / "vault")
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause()
            assert not app._messages
            assert app.query_one("#new-chat").has_class("hidden")
        with Vault.open(root / "vault") as vault:
            vault.revert(commit.id)
            assert not vault.list_cards()
    print(
        "Installed wheels: Core proposal/commit/garden/cited answer/revert, CLI and headless TUI passed with an offline fixture."
    )


asyncio.run(smoke())
