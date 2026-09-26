from pathlib import Path
import sqlite3

import pytest

from twindex_core import ConflictError, Vault
from twindex_core.models import OperationDraft


def test_vault_rejects_missing_migration_version(tmp_path: Path) -> None:
    root = tmp_path / "damaged"
    root.mkdir()
    with sqlite3.connect(root / "knowledge.sqlite3") as conn:
        conn.execute("CREATE TABLE schema_migrations(version INTEGER, checksum TEXT)")
    with pytest.raises(RuntimeError, match="schema"):
        Vault.open(root)


def test_source_changeset_commit_search_and_revert(tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    source = vault.ingest_text("The release uses blue labels.", title="Release note")
    assert (
        vault.ingest_text("The release uses blue labels.", title="Release note").id
        == source.id
    )
    evidence = vault.list_evidence(source.id)[0]

    changeset = vault.create_changeset(
        [source.id],
        [
            OperationDraft(
                kind="create_card",
                after={
                    "type": "fact",
                    "title": "Release labels",
                    "content": "Labels are blue.",
                },
                evidence_ids=[evidence.id],
                reason="The source describes the release labels.",
            )
        ],
    )
    assert vault.list_cards() == []
    assert "Release labels" in vault.diff(changeset.id)

    vault.stage(changeset.id, [changeset.operations[0].id])
    committed = vault.commit(changeset.id)
    assert len(vault.list_cards()) == 1
    hit = vault.search("blue")[0]
    assert hit.card.title == "Release labels"
    assert hit.citations[0].source_id == source.id
    assert committed.id == vault.list_commits()[0].id

    inverse = vault.revert(committed.id)
    assert vault.list_cards() == []
    assert inverse.inverse_of == committed.id


def test_commit_rejects_stale_card_version_atomically(tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    source = vault.ingest_text("Initial rule.")
    evidence_id = vault.list_evidence(source.id)[0].id
    create = vault.create_changeset(
        [source.id],
        [
            OperationDraft(
                kind="create_card",
                after={"type": "rule", "title": "Rule", "content": "Initial"},
                evidence_ids=[evidence_id],
            )
        ],
    )
    vault.stage(create.id, [create.operations[0].id])
    vault.commit(create.id)
    card = vault.list_cards()[0]

    stale = vault.create_changeset(
        [source.id],
        [
            OperationDraft(
                kind="update_card",
                target_id=card.id,
                base_version=1,
                after={"content": "Stale"},
                evidence_ids=[evidence_id],
            )
        ],
    )
    fresh = vault.create_changeset(
        [source.id],
        [
            OperationDraft(
                kind="update_card",
                target_id=card.id,
                base_version=1,
                after={"content": "Fresh"},
                evidence_ids=[evidence_id],
            )
        ],
    )
    vault.stage(stale.id, [stale.operations[0].id])
    vault.stage(fresh.id, [fresh.operations[0].id])
    vault.commit(fresh.id)
    with pytest.raises(ConflictError):
        vault.commit(stale.id)
    assert vault.get_card(card.id).content == "Fresh"


def test_partial_stage_relation_roundtrip_and_revert(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("A links to B")
        evidence_id = vault.list_evidence(source.id)[0].id
        create = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": "A", "content": "A"},
                    evidence_ids=[evidence_id],
                ),
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": "B", "content": "B"},
                    evidence_ids=[evidence_id],
                ),
            ],
        )
        vault.stage(create.id, [op.id for op in create.operations])
        vault.commit(create.id)
        a, b = [op.target_id for op in create.operations]
        changeset = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="add_relation",
                    after={"from_card_id": a, "to_card_id": b, "type": "links"},
                    evidence_ids=[evidence_id],
                ),
                OperationDraft(
                    kind="update_card",
                    target_id=a,
                    base_version=1,
                    after={"title": "Not staged"},
                    evidence_ids=[evidence_id],
                ),
            ],
        )
        vault.stage(changeset.id, [changeset.operations[0].id])
        committed = vault.commit(changeset.id)
        assert len(vault.list_relations()) == 1
        assert vault.get_card(a).title == "A"
        assert changeset.operations[0].target_id == vault.list_relations()[0].id
        vault.revert(committed.id)
        assert vault.list_relations() == []


def test_revert_rejects_intervening_card_edit(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("A")
        evidence_id = vault.list_evidence(source.id)[0].id
        create = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": "A", "content": "A"},
                    evidence_ids=[evidence_id],
                )
            ],
        )
        vault.stage(create.id, [create.operations[0].id])
        first = vault.commit(create.id)
        card = vault.list_cards()[0]
        update = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="update_card",
                    target_id=card.id,
                    base_version=1,
                    after={"content": "B"},
                    evidence_ids=[evidence_id],
                )
            ],
        )
        vault.stage(update.id, [update.operations[0].id])
        vault.commit(update.id)
        with pytest.raises(ConflictError):
            vault.revert(first.id)


def test_same_content_different_origins_keeps_provenance(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        first = vault.ingest_text("Same text", uri="https://one.example/note")
        second = vault.ingest_text("Same text", uri="https://two.example/note")
        assert first.id != second.id
        assert first.sha256 == second.sha256
        assert (
            vault.ingest_text("Same text", uri="https://one.example/note").id
            == first.id
        )
        assert len(vault.list_sources()) == 2


def test_revert_restores_previous_card_evidence(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        first = vault.ingest_text("Labels are blue", uri="file:///first")
        second = vault.ingest_text("Labels are red", uri="file:///second")
        first_ev = vault.list_evidence(first.id)[0].id
        second_ev = vault.list_evidence(second.id)[0].id
        create = vault.create_changeset(
            [first.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": "Labels", "content": "Blue"},
                    evidence_ids=[first_ev],
                )
            ],
        )
        vault.stage(create.id, [create.operations[0].id])
        vault.commit(create.id)
        card = vault.list_cards()[0]
        update = vault.create_changeset(
            [second.id],
            [
                OperationDraft(
                    kind="update_card",
                    target_id=card.id,
                    base_version=1,
                    after={"content": "Red"},
                    evidence_ids=[second_ev],
                )
            ],
        )
        vault.stage(update.id, [update.operations[0].id])
        updated = vault.commit(update.id)
        assert [cite.source_id for cite in vault.search("red")[0].citations] == [
            second.id
        ]
        vault.revert(updated.id)
        assert [cite.source_id for cite in vault.search("blue")[0].citations] == [
            first.id
        ]


def test_merge_cards_redirects_relations_and_reverts(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("A and B link to C")
        evidence_id = vault.list_evidence(source.id)[0].id
        create = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": title, "content": title},
                    evidence_ids=[evidence_id],
                )
                for title in ("A", "B", "C")
            ],
        )
        vault.stage(create.id, [op.id for op in create.operations])
        vault.commit(create.id)
        a, b, c = [op.target_id for op in create.operations]
        link = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="add_relation",
                    evidence_ids=[evidence_id],
                    after={"from_card_id": b, "to_card_id": c, "type": "links"},
                )
            ],
        )
        vault.stage(link.id, [link.operations[0].id])
        vault.commit(link.id)
        merge = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="merge_card",
                    target_id=a,
                    base_version=1,
                    after={
                        "source_card_id": b,
                        "source_base_version": 1,
                        "content": "A and B",
                    },
                    evidence_ids=[evidence_id],
                )
            ],
        )
        vault.stage(merge.id, [merge.operations[0].id])
        committed = vault.commit(merge.id)
        assert vault.get_card(b).status == "archived"
        assert vault.list_relations()[0].from_card_id == a
        assert vault.get_card(a).content == "A and B"
        vault.revert(committed.id)
        assert vault.get_card(b).status == "active"
        assert vault.get_card(a).content == "A"
        assert vault.list_relations()[0].from_card_id == b


def test_modified_source_blob_is_rejected(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Original")
        (vault.root / "sources" / source.sha256).write_bytes(b"Tampered")
        with pytest.raises(RuntimeError, match="integrity"):
            vault.source_bytes(source.id)


def test_multi_operation_commit_rolls_back_and_schema_checksum_is_verified(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    with Vault.open(vault_path) as vault:
        source = vault.ingest_text("A has a link")
        evidence_id = vault.list_evidence(source.id)[0].id
        changeset = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={"type": "fact", "title": "A", "content": "A"},
                    evidence_ids=[evidence_id],
                ),
                OperationDraft(
                    kind="add_relation",
                    after={
                        "from_card_id": "missing",
                        "to_card_id": "missing",
                        "type": "links",
                    },
                    evidence_ids=[evidence_id],
                ),
            ],
        )
        vault.stage(changeset.id, [op.id for op in changeset.operations])
        with pytest.raises(KeyError):
            vault.commit(changeset.id)
        assert vault.list_cards() == []
        assert vault.list_commits() == []
        vault.conn.execute(
            "UPDATE schema_migrations SET checksum='wrong' WHERE version=1"
        )
    with pytest.raises(RuntimeError, match="schema"):
        Vault.open(vault_path)
