from pathlib import Path

import httpx
import pytest
from PIL import Image

from twindex_core import Vault
from twindex_core.engine import (
    GeneratedAnswer,
    KnowledgeEngine,
    ModelOutputError,
    VisionUnavailable,
)
from twindex_core.sources import SourceIngestor


class FakeProvider:
    model = "fake-local"
    vision = True

    def __init__(self) -> None:
        self.images_seen = 0

    def require_vision(self) -> None:
        if not self.vision:
            raise VisionUnavailable("vision required")

    def describe_image(self, image: bytes, *, mime_type: str) -> str:
        self.images_seen += 1
        return "The image says that release labels are blue."

    def structured(self, system: str, user: str, schema: type) -> dict:
        if schema is GeneratedAnswer:
            return {
                "text": "Release labels are blue.",
                "evidence_refs": ["C1"],
                "insufficient_evidence": False,
            }
        assert "E1" in user
        return {
            "operations": [
                {
                    "kind": "create_card",
                    "temporary_id": "new:labels",
                    "after": {
                        "type": "fact",
                        "title": "Release labels",
                        "content": "Labels are blue.",
                    },
                    "evidence_refs": ["E1"],
                    "reason": "The selected source says so.",
                }
            ]
        }

    def text(self, system: str, user: str) -> str:
        return "Release labels are blue."

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


def test_image_proposal_requires_vision_and_answer_cites_source(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    Image.new("RGB", (20, 20), "blue").save(image)
    with Vault.open(tmp_path / "vault") as vault:
        source = SourceIngestor(vault).ingest_path(image)[0]
        provider = FakeProvider()
        provider.vision = False
        with pytest.raises(VisionUnavailable):
            KnowledgeEngine(vault, provider).propose([source.id])
        assert vault.list_changesets() == []

        provider.vision = True
        engine = KnowledgeEngine(vault, provider)
        changeset = engine.propose([source.id])
        assert provider.images_seen == 1
        assert vault.list_cards() == []
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        answer = engine.answer("What color are release labels?")
        assert answer.insufficient_evidence is False
        assert answer.citations[0].source_id == source.id
        assert answer.citations[0].locator["kind"] == "image"


def test_no_evidence_means_no_model_answer(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        answer = KnowledgeEngine(vault, FakeProvider()).answer("Unknown project")
        assert answer.insufficient_evidence is True
        assert answer.citations == []


def test_direct_source_quote_is_explicitly_sufficient_for_answer(
    tmp_path: Path,
) -> None:
    class DirectQuoteProvider(FakeProvider):
        def structured(self, system: str, user: str, schema: type) -> dict:
            if schema is GeneratedAnswer:
                assert "Do not require external verification" in system
                assert '"ref": "C1"' in user
            return super().structured(system, user, schema)

    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Release labels are blue")
        engine = KnowledgeEngine(vault, DirectQuoteProvider())
        changeset = engine.propose([source.id])
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        answer = engine.answer("What color are release labels?")
        assert answer.insufficient_evidence is False
        assert answer.citations[0].source_id == source.id


def test_answer_falls_back_to_fts_when_embeddings_are_unavailable(
    tmp_path: Path,
) -> None:
    class OfflineEmbeddings(FakeProvider):
        embedding_model = "offline-embeddings"

        def embed(self, texts: list[str]) -> list[list[float]]:
            raise httpx.ConnectError("embedding endpoint is offline")

    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Release labels are blue")
        engine = KnowledgeEngine(vault, OfflineEmbeddings())
        changeset = engine.propose([source.id])
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        answer = engine.answer("blue labels")
        assert answer.insufficient_evidence is False
        assert answer.citations[0].source_id == source.id


def test_generated_relation_uses_new_card_ids(tmp_path: Path) -> None:
    class LinkProvider(FakeProvider):
        def structured(self, system: str, user: str, schema: type) -> dict:
            return {
                "operations": [
                    {
                        "kind": "create_card",
                        "temporary_id": "new:a",
                        "after": {"type": "fact", "title": "A", "content": "A"},
                        "evidence_refs": ["E1"],
                        "reason": "Source",
                    },
                    {
                        "kind": "create_card",
                        "temporary_id": "new:b",
                        "after": {"type": "fact", "title": "B", "content": "B"},
                        "evidence_refs": ["E1"],
                        "reason": "Source",
                    },
                    {
                        "kind": "add_relation",
                        "after": {
                            "from_card_id": "new:a",
                            "to_card_id": "new:b",
                            "type": "links",
                        },
                        "evidence_refs": ["E1"],
                        "reason": "Source",
                    },
                ]
            }

    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("A links to B")
        changeset = KnowledgeEngine(vault, LinkProvider()).propose([source.id])
        assert (
            changeset.operations[2].after["from_card_id"]
            == changeset.operations[0].target_id
        )
        vault.stage(changeset.id, [op.id for op in changeset.operations])
        vault.commit(changeset.id)
        assert vault.list_relations()[0].to_card_id == changeset.operations[1].target_id


def test_invalid_model_evidence_reference_leaves_vault_unchanged(
    tmp_path: Path,
) -> None:
    class BadProvider(FakeProvider):
        def structured(self, system: str, user: str, schema: type) -> dict:
            return {
                "operations": [
                    {
                        "kind": "create_card",
                        "after": {
                            "type": "fact",
                            "title": "Invented",
                            "content": "No source",
                        },
                        "evidence_refs": ["E999"],
                        "reason": "Invented",
                    }
                ]
            }

    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Known fact")
        with pytest.raises(Exception):
            KnowledgeEngine(vault, BadProvider()).propose([source.id])
        assert vault.list_changesets() == []
        assert vault.list_cards() == []


def test_answer_rejects_unknown_citation_reference(tmp_path: Path) -> None:
    class BadAnswerProvider(FakeProvider):
        def structured(self, system: str, user: str, schema: type) -> dict:
            if schema is GeneratedAnswer:
                return {
                    "text": "Invented",
                    "evidence_refs": ["C999"],
                    "insufficient_evidence": False,
                }
            return super().structured(system, user, schema)

    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Labels are blue")
        engine = KnowledgeEngine(vault, BadAnswerProvider())
        changeset = engine.propose([source.id])
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        with pytest.raises(ModelOutputError):
            engine.answer("blue labels")


def test_assistant_dialogue_retains_unverified_attribution(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text(
            "The secret code is blue", trust="assistant_unverified"
        )
        evidence_id = vault.list_evidence(source.id)[0].id
        from twindex_core.models import OperationDraft

        changeset = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={
                        "type": "claim",
                        "title": "Secret code",
                        "content": "Blue code",
                    },
                    evidence_ids=[evidence_id],
                )
            ],
        )
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        answer = KnowledgeEngine(vault, FakeProvider()).answer("blue code")
        assert answer.insufficient_evidence is False
        assert answer.citations[0].trust == "assistant_unverified"
        assert "не проверено по первоисточнику" in answer.text


def test_relation_to_source_id_is_rejected_before_changeset(tmp_path: Path) -> None:
    class InvalidRelationProvider(FakeProvider):
        calls = 0

        def structured(self, system: str, user: str, schema: type) -> dict:
            self.calls += 1
            return {
                "operations": [
                    {
                        "kind": "add_relation",
                        "after": {
                            "from_card_id": "src_not_a_card",
                            "to_card_id": "card_missing",
                            "type": "links",
                        },
                        "evidence_refs": ["E1"],
                        "reason": "Bad link",
                    }
                ]
            }

    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Release labels are blue")
        provider = InvalidRelationProvider()
        with pytest.raises(ModelOutputError):
            KnowledgeEngine(vault, provider).propose([source.id])
        assert provider.calls == 2
        assert vault.list_changesets() == []
