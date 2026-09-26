from __future__ import annotations

import hashlib
from typing import Protocol

from .models import SearchHit
from .vault import Vault


class Embedder(Protocol):
    embedding_model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SemanticIndex:
    """Disposable Chroma acceleration; committed SQLite cards remain canonical."""

    def __init__(self, vault: Vault, embedder: Embedder):
        self.vault = vault
        self.embedder = embedder
        self.model_id = embedder.embedding_model
        self.collection_name = (
            "cards_" + hashlib.sha256(self.model_id.encode()).hexdigest()[:20]
        )
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise RuntimeError(
                "Install twindex-core[semantic] to enable vector search"
            ) from exc
        self.client = chromadb.PersistentClient(
            path=str(vault.root / "semantic"),
            settings=Settings(anonymized_telemetry=False),
        )

    def _collection(self):
        return self.client.get_or_create_collection(
            self.collection_name, metadata={"embedding_model": self.model_id}
        )

    def rebuild(self) -> int:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        collection = self._collection()
        cards = self.vault.list_cards()
        for start in range(0, len(cards), 32):
            batch = cards[start : start + 32]
            texts = [f"{card.title}\n{card.content}" for card in batch]
            vectors = self.embedder.embed(texts)
            collection.add(
                ids=[card.id for card in batch],
                embeddings=vectors,
                documents=texts,
                metadatas=[{"version": card.version} for card in batch],
            )
        return len(cards)

    def sync(self) -> None:
        collection = self._collection()
        stored = collection.get(include=["metadatas"])
        versions = {
            card_id: metadata.get("version")
            for card_id, metadata in zip(stored["ids"], stored["metadatas"] or [])
        }
        current = {card.id: card.version for card in self.vault.list_cards()}
        if versions != current:
            self.rebuild()

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        self.sync()
        collection = self._collection()
        if collection.count() == 0:
            return []
        vector = self.embedder.embed([query])[0]
        results = collection.query(
            query_embeddings=[vector], n_results=min(limit, collection.count())
        )
        ids = results["ids"][0]
        distances = results.get("distances", [[]])[0]
        return [
            self.vault.hit(card_id, score=1 / (1 + float(distance)))
            for card_id, distance in zip(ids, distances)
        ]


def hybrid_search(
    vault: Vault, embedder: Embedder, query: str, *, limit: int = 10
) -> list[SearchHit]:
    lexical = vault.search(query, limit=limit)
    semantic = SemanticIndex(vault, embedder).search(query, limit=limit)
    ranks: dict[str, float] = {}
    hits: dict[str, SearchHit] = {}
    for group in (lexical, semantic):
        for rank, hit in enumerate(group, 1):
            ranks[hit.card.id] = ranks.get(hit.card.id, 0) + 1 / (60 + rank)
            hits[hit.card.id] = hit
    return [
        hits[card_id]
        for card_id in sorted(ranks, key=lambda card_id: ranks[card_id], reverse=True)[
            :limit
        ]
    ]
