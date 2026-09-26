from pathlib import Path

import pytest

from twindex_core import Vault
from twindex_core.models import OperationDraft


def proposal(vault, source):
    return vault.create_changeset(
        [source.id],
        [
            OperationDraft(
                kind="create_card",
                after={"type": "fact", "title": "Note", "content": "Fact"},
                evidence_ids=[vault.list_evidence(source.id)[0].id],
                reason="Source",
            )
        ],
    )


def test_unused_source_delete_removes_only_local_snapshot_and_draft(tmp_path: Path):
    original = tmp_path / "original.txt"
    original.write_text("Fact")
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Fact", uri=str(original))
        draft = proposal(vault, source)
        impact = vault.source_removal_impact(source.id)
        assert impact["draft_ids"] == [draft.id]
        vault.delete_source(source.id, expected_drafts=[draft.id])
        assert not vault.list_sources()
        assert not vault.list_changesets()
        assert not (vault.root / "sources" / source.sha256).exists()
        assert original.read_text() == "Fact"


def test_delete_checks_new_dependencies_and_preserves_shared_blob(tmp_path):
    with Vault.open(tmp_path) as vault:
        first = vault.ingest_text("same", uri="first")
        second = vault.ingest_text("same", uri="second")
        draft = proposal(vault, first)
        with pytest.raises(ValueError):
            vault.delete_source(first.id, expected_drafts=[])
        vault.delete_source(first.id, expected_drafts=[draft.id])
        assert vault.source_bytes(second.id) == b"same"


def test_used_source_cannot_be_deleted_but_archive_preserves_provenance(tmp_path):
    with Vault.open(tmp_path) as vault:
        source = vault.ingest_text("Fact")
        draft = proposal(vault, source)
        vault.stage(draft.id, [draft.operations[0].id])
        vault.commit(draft.id)
        with pytest.raises(ValueError):
            vault.delete_source(source.id, expected_drafts=[])
        vault.archive_source(source.id)
        assert not vault.list_sources(include_archived=False)
        assert vault.source_bytes(source.id) == b"Fact"
        assert vault.search("Fact")[0].citations
        vault.archive_source(source.id, archived=False)
        assert len(vault.list_sources(include_archived=False)) == 1


def test_shared_draft_prevents_deleting_one_of_its_sources(tmp_path):
    with Vault.open(tmp_path) as vault:
        a = vault.ingest_text("a")
        b = vault.ingest_text("b")
        vault.create_changeset([a.id, b.id], [])
        with pytest.raises(ValueError):
            vault.delete_source(a.id, expected_drafts=[])
        assert len(vault.list_sources()) == 2


def test_shared_evidence_blocks_delete_even_without_declared_source_ids(tmp_path):
    with Vault.open(tmp_path) as vault:
        a = vault.ingest_text("a")
        b = vault.ingest_text("b")
        draft = vault.create_changeset(
            [],
            [
                OperationDraft(
                    kind="create_card",
                    after={"title": "Both", "content": "a and b", "type": "fact"},
                    evidence_ids=[
                        vault.list_evidence(a.id)[0].id,
                        vault.list_evidence(b.id)[0].id,
                    ],
                    reason="Both sources",
                )
            ],
        )
        with pytest.raises(ValueError, match="archive"):
            vault.delete_source(a.id, expected_drafts=[draft.id])
        assert vault.get_changeset(draft.id)


def test_failed_sql_delete_preserves_source_snapshot_and_draft(tmp_path):
    with Vault.open(tmp_path) as vault:
        source = vault.ingest_text("Fact")
        draft = proposal(vault, source)
        vault.conn.execute(
            "CREATE TRIGGER deny_delete BEFORE DELETE ON sources BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            vault.delete_source(source.id, expected_drafts=[draft.id])
        assert vault.source_bytes(source.id) == b"Fact"
        assert vault.get_changeset(draft.id).operations
