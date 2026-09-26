from pathlib import Path

from twindex_core import Vault
from twindex_core.models import OperationDraft
from twindex_core.semantic import SemanticIndex


class FakeEmbeddings:
    embedding_model = "fake-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "blue" in text.lower() else [0.0, 1.0] for text in texts]


def test_semantic_index_rebuild_and_model_version(tmp_path: Path) -> None:
    with Vault.open(tmp_path / "vault") as vault:
        source = vault.ingest_text("Blue color")
        evidence_id = vault.list_evidence(source.id)[0].id
        changeset = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="create_card",
                    after={
                        "type": "fact",
                        "title": "Cerulean",
                        "content": "Blue color",
                    },
                    evidence_ids=[evidence_id],
                )
            ],
        )
        vault.stage(changeset.id, [changeset.operations[0].id])
        vault.commit(changeset.id)
        index = SemanticIndex(vault, FakeEmbeddings())
        index.rebuild()
        hits = index.search("blue question")
        assert hits[0].card.title == "Cerulean"
        assert hits[0].citations[0].source_id == source.id
        assert index.model_id == "fake-v1"
        update = vault.create_changeset(
            [source.id],
            [
                OperationDraft(
                    kind="update_card",
                    target_id=hits[0].card.id,
                    base_version=1,
                    after={"content": "Green color"},
                    evidence_ids=[evidence_id],
                )
            ],
        )
        vault.stage(update.id, [update.operations[0].id])
        vault.commit(update.id)
        assert index.search("green")[0].card.version == 2
