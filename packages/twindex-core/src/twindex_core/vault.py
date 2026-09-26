from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import (
    AnswerCitation,
    Card,
    Changeset,
    Commit,
    Evidence,
    OperationDraft,
    ProposalOperation,
    Relation,
    SearchHit,
    SourceSnapshot,
)


class ConflictError(RuntimeError):
    """The changeset no longer matches the committed knowledge state."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, uri TEXT,
 sha256 TEXT NOT NULL, imported_at TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence(
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
 locator_json TEXT NOT NULL, text TEXT NOT NULL, trust TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards(
 id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
 fields_json TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_evidence(
 card_id TEXT NOT NULL REFERENCES cards(id), card_version INTEGER NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES evidence(id),
 PRIMARY KEY(card_id, card_version, evidence_id)
);
CREATE TABLE IF NOT EXISTS relations(
 id TEXT PRIMARY KEY, from_card_id TEXT NOT NULL REFERENCES cards(id),
 to_card_id TEXT NOT NULL REFERENCES cards(id), type TEXT NOT NULL, note TEXT,
 version INTEGER NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changesets(
 id TEXT PRIMARY KEY, status TEXT NOT NULL, source_ids_json TEXT NOT NULL,
 created_at TEXT NOT NULL, model TEXT, prompt_version TEXT
);
CREATE TABLE IF NOT EXISTS operations(
 id TEXT PRIMARY KEY, changeset_id TEXT NOT NULL REFERENCES changesets(id),
 kind TEXT NOT NULL, target_id TEXT, base_version INTEGER, before_json TEXT,
 after_json TEXT NOT NULL, evidence_ids_json TEXT NOT NULL, reason TEXT NOT NULL,
 staged INTEGER NOT NULL DEFAULT 0, ordinal INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS commits(
 id TEXT PRIMARY KEY, changeset_id TEXT NOT NULL REFERENCES changesets(id),
 committed_at TEXT NOT NULL, inverse_of TEXT
);
CREATE TABLE IF NOT EXISTS card_revisions(
 card_id TEXT NOT NULL REFERENCES cards(id), version INTEGER NOT NULL,
 state_json TEXT NOT NULL, commit_id TEXT NOT NULL REFERENCES commits(id),
 PRIMARY KEY(card_id, version)
);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(card_id UNINDEXED, title, content);
CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(sha256);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_operations_changeset ON operations(changeset_id, ordinal);
"""
_SCHEMA_HASH = hashlib.sha256(_SCHEMA.encode()).hexdigest()


class Vault:
    def __init__(self, root: Path, conn: sqlite3.Connection):
        self.root = root
        self.conn = conn

    @classmethod
    def open(cls, root: str | Path) -> Vault:
        root = Path(root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        blobs = root / "sources"
        blobs.mkdir(exist_ok=True, mode=0o700)
        db_path = root / "knowledge.sqlite3"
        conn = sqlite3.connect(db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if row:
            version = conn.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if not version or (
                version["version"] != 1 or version["checksum"] != _SCHEMA_HASH
            ):
                conn.close()
                raise RuntimeError("Unsupported or modified vault schema")
        else:
            conn.executescript(_SCHEMA)
            conn.execute("INSERT INTO schema_migrations VALUES(1, ?)", (_SCHEMA_HASH,))
        os.chmod(db_path, 0o600)
        return cls(root, conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Vault:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ingest_text(
        self,
        text: str,
        *,
        title: str = "Untitled",
        kind: str = "text",
        uri: str | None = None,
        locator: dict | None = None,
        trust: str = "source",
        metadata: dict | None = None,
    ) -> SourceSnapshot:
        return self.ingest_segments(
            text.encode("utf-8"),
            kind=kind,
            title=title,
            uri=uri,
            metadata=metadata,
            segments=[
                (text, locator or {"kind": "text", "start": 0, "end": len(text)}, trust)
            ],
        )

    def ingest_segments(
        self,
        raw: bytes,
        *,
        kind: str,
        title: str,
        uri: str | None = None,
        metadata: dict | None = None,
        segments: list[tuple[str, dict, str]],
    ) -> SourceSnapshot:
        digest = hashlib.sha256(raw).hexdigest()
        identity = hashlib.sha256(
            kind.encode() + b"\0" + (uri or "").encode() + b"\0" + raw
        ).hexdigest()
        source_id = "src_" + identity[:32]
        existing = self.conn.execute(
            "SELECT * FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        if existing:
            return self._source(existing)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                "SELECT * FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if existing:
                self.conn.execute("COMMIT")
                return self._source(existing)
            # Share the deletion lock before publishing a snapshot on disk.
            blob = self.root / "sources" / digest
            fd, temporary = tempfile.mkstemp(dir=blob.parent, prefix=".snapshot-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, blob)
                except FileExistsError:
                    if hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
                        raise RuntimeError("Source blob failed integrity check")
            finally:
                os.unlink(temporary)
            imported_at = _now()
            self.conn.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?,?)",
                (
                    source_id,
                    kind,
                    title,
                    uri,
                    digest,
                    imported_at,
                    _json(metadata or {}),
                ),
            )
            for text, locator, trust in segments:
                self.conn.execute(
                    "INSERT INTO evidence VALUES(?,?,?,?,?)",
                    ("ev_" + uuid4().hex, source_id, _json(locator), text, trust),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return self.get_source(source_id)

    def _source(self, row: sqlite3.Row) -> SourceSnapshot:
        return SourceSnapshot(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            uri=row["uri"],
            sha256=row["sha256"],
            imported_at=row["imported_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def get_source(self, source_id: str) -> SourceSnapshot:
        row = self.conn.execute(
            "SELECT * FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        if not row:
            raise KeyError(source_id)
        return self._source(row)

    def source_text(self, source_id: str) -> str:
        return self.source_bytes(source_id).decode("utf-8")

    def source_bytes(self, source_id: str) -> bytes:
        source = self.get_source(source_id)
        raw = (self.root / "sources" / source.sha256).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source.sha256:
            raise RuntimeError("Source blob failed integrity check")
        return raw

    def add_evidence(
        self, source_id: str, text: str, locator: dict, *, trust: str = "source"
    ) -> Evidence:
        self.get_source(source_id)
        evidence_id = "ev_" + uuid4().hex
        self.conn.execute(
            "INSERT INTO evidence VALUES(?,?,?,?,?)",
            (evidence_id, source_id, _json(locator), text, trust),
        )
        return next(
            item for item in self.list_evidence(source_id) if item.id == evidence_id
        )

    def list_sources(self, *, include_archived: bool = True) -> list[SourceSnapshot]:
        return [
            self._source(row)
            for row in self.conn.execute(
                "SELECT * FROM sources ORDER BY imported_at DESC"
            )
            if include_archived
            or not json.loads(row["metadata_json"]).get("archived", False)
        ]

    def archive_source(self, source_id: str, *, archived: bool = True) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            metadata = self.get_source(source_id).metadata.copy()
            metadata["archived"] = archived
            self.conn.execute(
                "UPDATE sources SET metadata_json=? WHERE id=?",
                (_json(metadata), source_id),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def source_removal_impact(self, source_id: str) -> dict:
        self.get_source(source_id)
        evidence_ids = {item.id for item in self.list_evidence(source_id)}
        related = [
            change
            for change in self.list_changesets()
            if source_id in change.source_ids
            or any(
                evidence_ids.intersection(op.evidence_ids) for op in change.operations
            )
        ]
        cards = self.conn.execute(
            "SELECT DISTINCT card_id FROM card_evidence WHERE evidence_id IN "
            "(SELECT id FROM evidence WHERE source_id=?)",
            (source_id,),
        ).fetchall()
        committed = {item.changeset_id for item in self.list_commits()}
        history = [
            item.id for item in related if item.id in committed or item.status != "open"
        ]
        shared = [
            item.id
            for item in related
            if set(item.source_ids) - {source_id}
            or any(set(op.evidence_ids) - evidence_ids for op in item.operations)
        ]
        return {
            "card_ids": [row["card_id"] for row in cards],
            "history_ids": history,
            "shared_draft_ids": shared,
            "draft_ids": sorted(item.id for item in related if item.status == "open"),
            "can_delete": not (cards or history or shared),
        }

    def delete_source(self, source_id: str, *, expected_drafts: list[str]) -> None:
        """Delete an unused snapshot, never its original file or committed evidence."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            source = self.get_source(source_id)
            impact = self.source_removal_impact(source_id)
            if not impact["can_delete"]:
                raise ValueError(
                    "Source is used by cards, history or a shared proposal; archive it instead"
                )
            if sorted(expected_drafts) != impact["draft_ids"]:
                raise ValueError("Source dependencies changed; review deletion again")
            for draft_id in impact["draft_ids"]:
                self.conn.execute(
                    "DELETE FROM operations WHERE changeset_id=?", (draft_id,)
                )
                self.conn.execute("DELETE FROM changesets WHERE id=?", (draft_id,))
            self.conn.execute("DELETE FROM evidence WHERE source_id=?", (source_id,))
            self.conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        # Commit the logical deletion first: a crash may leave an orphan blob,
        # but must never leave a surviving source without its immutable snapshot.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if not self.conn.execute(
                "SELECT 1 FROM sources WHERE sha256=?", (source.sha256,)
            ).fetchone():
                (self.root / "sources" / source.sha256).unlink(missing_ok=True)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def list_evidence(self, source_id: str) -> list[Evidence]:
        self.get_source(source_id)
        return [
            Evidence(
                id=row["id"],
                source_id=row["source_id"],
                locator=json.loads(row["locator_json"]),
                text=row["text"],
                trust=row["trust"],
            )
            for row in self.conn.execute(
                "SELECT * FROM evidence WHERE source_id=? ORDER BY rowid", (source_id,)
            )
        ]

    def _card(self, row: sqlite3.Row) -> Card:
        return Card(
            id=row["id"],
            type=row["type"],
            title=row["title"],
            content=row["content"],
            fields=json.loads(row["fields_json"]),
            status=row["status"],
            version=row["version"],
        )

    def get_card(self, card_id: str) -> Card:
        row = self.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if not row:
            raise KeyError(card_id)
        return self._card(row)

    def list_cards(self, *, include_archived: bool = False) -> list[Card]:
        query = (
            "SELECT * FROM cards"
            + ("" if include_archived else " WHERE status='active'")
            + " ORDER BY title, id"
        )
        return [self._card(row) for row in self.conn.execute(query)]

    def get_relation(self, relation_id: str) -> Relation:
        row = self.conn.execute(
            "SELECT * FROM relations WHERE id=?", (relation_id,)
        ).fetchone()
        if not row:
            raise KeyError(relation_id)
        return Relation(
            id=row["id"],
            from_card_id=row["from_card_id"],
            to_card_id=row["to_card_id"],
            type=row["type"],
            note=row["note"],
            version=row["version"],
        )

    def list_relations(self, *, include_archived: bool = False) -> list[Relation]:
        query = (
            "SELECT id FROM relations"
            + ("" if include_archived else " WHERE status='active'")
            + " ORDER BY id"
        )
        return [self.get_relation(row["id"]) for row in self.conn.execute(query)]

    def create_changeset(
        self,
        source_ids: list[str],
        operations: list[OperationDraft],
        *,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> Changeset:
        for source_id in source_ids:
            self.get_source(source_id)
        changeset_id = "cs_" + uuid4().hex
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO changesets VALUES(?,?,?,?,?,?)",
                (
                    changeset_id,
                    "open",
                    _json(source_ids),
                    _now(),
                    model,
                    prompt_version,
                ),
            )
            for ordinal, raw in enumerate(operations):
                op = OperationDraft.model_validate(raw)
                for evidence_id in op.evidence_ids:
                    row = self.conn.execute(
                        "SELECT source_id FROM evidence WHERE id=?", (evidence_id,)
                    ).fetchone()
                    if not row or (source_ids and row["source_id"] not in source_ids):
                        raise ValueError(
                            f"Unknown evidence for changeset: {evidence_id}"
                        )
                target_id = op.target_id or (
                    "card_" + uuid4().hex
                    if op.kind == "create_card"
                    else "rel_" + uuid4().hex
                    if op.kind == "add_relation"
                    else None
                )
                before = None
                if op.kind in ("update_card", "archive_card"):
                    if not target_id:
                        raise ValueError("Card operation requires target_id")
                    before = self.get_card(target_id).model_dump(mode="json")
                    if op.base_version is None:
                        raise ValueError(
                            "Existing card operation requires base_version"
                        )
                elif op.kind == "merge_card":
                    source_card_id = op.after.get("source_card_id")
                    if (
                        not target_id
                        or not source_card_id
                        or source_card_id == target_id
                        or op.base_version is None
                        or not isinstance(op.after.get("source_base_version"), int)
                    ):
                        raise ValueError(
                            "Merge needs distinct target/source IDs and both versions"
                        )
                    target_card = self.get_card(target_id)
                    source_card = self.get_card(source_card_id)
                    relations = [
                        self.get_relation(row["id"]).model_dump(mode="json")
                        for row in self.conn.execute(
                            "SELECT id FROM relations WHERE status='active' AND (from_card_id=? OR to_card_id=?)",
                            (source_card_id, source_card_id),
                        )
                    ]
                    before = {
                        "target": target_card.model_dump(mode="json"),
                        "source": source_card.model_dump(mode="json"),
                        "relations": relations,
                        "target_evidence": self._revision_evidence(
                            target_id, target_card.version
                        ),
                        "source_evidence": self._revision_evidence(
                            source_card_id, source_card.version
                        ),
                    }
                elif op.kind == "remove_relation":
                    if not target_id or op.base_version is None:
                        raise ValueError("Existing relation requires ID and version")
                    before = self.get_relation(target_id).model_dump(mode="json")
                self.conn.execute(
                    "INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "op_" + uuid4().hex,
                        changeset_id,
                        op.kind,
                        target_id,
                        op.base_version,
                        _json(before) if before else None,
                        _json(op.after),
                        _json(op.evidence_ids),
                        op.reason,
                        0,
                        ordinal,
                    ),
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return self.get_changeset(changeset_id)

    def get_changeset(self, changeset_id: str) -> Changeset:
        row = self.conn.execute(
            "SELECT * FROM changesets WHERE id=?", (changeset_id,)
        ).fetchone()
        if not row:
            raise KeyError(changeset_id)
        operations = []
        for op in self.conn.execute(
            "SELECT * FROM operations WHERE changeset_id=? ORDER BY ordinal",
            (changeset_id,),
        ):
            operations.append(
                ProposalOperation(
                    id=op["id"],
                    kind=op["kind"],
                    target_id=op["target_id"],
                    base_version=op["base_version"],
                    before=json.loads(op["before_json"]) if op["before_json"] else None,
                    after=json.loads(op["after_json"]),
                    evidence_ids=json.loads(op["evidence_ids_json"]),
                    reason=op["reason"],
                    staged=bool(op["staged"]),
                )
            )
        return Changeset(
            id=row["id"],
            status=row["status"],
            source_ids=json.loads(row["source_ids_json"]),
            operations=operations,
            created_at=row["created_at"],
            model=row["model"],
            prompt_version=row["prompt_version"],
        )

    def list_changesets(self, *, status: str | None = None) -> list[Changeset]:
        rows = self.conn.execute(
            "SELECT id FROM changesets WHERE (? IS NULL OR status=?) ORDER BY created_at DESC",
            (status, status),
        )
        return [self.get_changeset(row["id"]) for row in rows]

    def edit(self, changeset_id: str, operation_id: str, after: dict) -> Changeset:
        changeset = self.get_changeset(changeset_id)
        if changeset.status != "open":
            raise ConflictError("Only open changesets can be edited")
        if not any(op.id == operation_id for op in changeset.operations):
            raise KeyError(operation_id)
        self.conn.execute(
            "UPDATE operations SET after_json=? WHERE id=?",
            (_json(after), operation_id),
        )
        return self.get_changeset(changeset_id)

    def stage(
        self, changeset_id: str, operation_ids: list[str], *, staged: bool = True
    ) -> Changeset:
        changeset = self.get_changeset(changeset_id)
        if changeset.status != "open":
            raise ConflictError("Only open changesets can be staged")
        known = {op.id for op in changeset.operations}
        if not set(operation_ids) <= known:
            raise KeyError("Unknown operation")
        self.conn.executemany(
            "UPDATE operations SET staged=? WHERE id=?",
            [(int(staged), op) for op in operation_ids],
        )
        return self.get_changeset(changeset_id)

    def diff(self, changeset_id: str) -> str:
        changeset = self.get_changeset(changeset_id)
        lines = [f"changeset {changeset.id} ({changeset.status})"]
        for op in changeset.operations:
            lines.append(
                f"{'[staged]' if op.staged else '[ ]'} {op.kind} {op.target_id or ''}"
            )
            before = op.before or {}
            for key in sorted(set(before) | set(op.after)):
                old, new = before.get(key), op.after.get(key, before.get(key))
                if old != new:
                    if key in before:
                        lines.append(f"- {key}: {old}")
                    lines.append(f"+ {key}: {new}")
            if op.evidence_ids:
                lines.append("  evidence: " + ", ".join(op.evidence_ids))
        return "\n".join(lines) + "\n"

    def export_changeset(self, changeset_id: str) -> str:
        return self.get_changeset(changeset_id).model_dump_json(indent=2)

    def _write_card(
        self,
        card: Card,
        *,
        commit_id: str,
        evidence_ids: list[str],
        created_at: str | None = None,
    ) -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "type=excluded.type,title=excluded.title,content=excluded.content,fields_json=excluded.fields_json,"
            "status=excluded.status,version=excluded.version,updated_at=excluded.updated_at",
            (
                card.id,
                card.type,
                card.title,
                card.content,
                _json(card.fields),
                card.status,
                card.version,
                created_at or now,
                now,
            ),
        )
        self.conn.execute("DELETE FROM cards_fts WHERE card_id=?", (card.id,))
        if card.status == "active":
            self.conn.execute(
                "INSERT INTO cards_fts VALUES(?,?,?)",
                (card.id, card.title, card.content),
            )
        self.conn.execute(
            "INSERT INTO card_revisions VALUES(?,?,?,?)",
            (card.id, card.version, card.model_dump_json(), commit_id),
        )
        self.conn.executemany(
            "INSERT INTO card_evidence VALUES(?,?,?)",
            [
                (card.id, card.version, evidence_id)
                for evidence_id in dict.fromkeys(evidence_ids)
            ],
        )

    def _revision_evidence(self, card_id: str, version: int) -> list[str]:
        return [
            row["evidence_id"]
            for row in self.conn.execute(
                "SELECT evidence_id FROM card_evidence WHERE card_id=? AND card_version=? ORDER BY evidence_id",
                (card_id, version),
            )
        ]

    def commit(self, changeset_id: str, *, inverse_of: str | None = None) -> Commit:
        changeset = self.get_changeset(changeset_id)
        if changeset.status != "open":
            raise ConflictError("Changeset is not open")
        selected = [op for op in changeset.operations if op.staged]
        if not selected:
            raise ValueError("No staged operations")
        commit_id = "cm_" + uuid4().hex
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "INSERT INTO commits VALUES(?,?,?,?)",
                (commit_id, changeset_id, _now(), inverse_of),
            )
            for op in selected:
                if op.kind == "create_card":
                    if self.conn.execute(
                        "SELECT 1 FROM cards WHERE id=?", (op.target_id,)
                    ).fetchone():
                        raise ConflictError("Card already exists")
                    card = Card(id=op.target_id or "", **op.after)
                    self._write_card(
                        card, commit_id=commit_id, evidence_ids=op.evidence_ids
                    )
                elif op.kind in ("update_card", "archive_card"):
                    current = self.get_card(op.target_id or "")
                    if current.version != op.base_version:
                        raise ConflictError(
                            f"Card {current.id} changed from version {op.base_version} to {current.version}"
                        )
                    payload = current.model_dump()
                    payload.update(op.after)
                    if op.kind == "archive_card":
                        payload["status"] = "archived"
                    payload["version"] = current.version + 1
                    self._write_card(
                        Card.model_validate(payload),
                        commit_id=commit_id,
                        evidence_ids=op.evidence_ids,
                    )
                elif op.kind == "merge_card":
                    target = self.get_card(op.target_id or "")
                    source = self.get_card(op.after["source_card_id"])
                    if (
                        target.version != op.base_version
                        or source.version != op.after["source_base_version"]
                        or target.status != "active"
                        or source.status != "active"
                    ):
                        raise ConflictError("Merge target or source changed")
                    before_relations = (op.before or {}).get("relations", [])
                    current_relations = [
                        self.get_relation(row["id"])
                        for row in self.conn.execute(
                            "SELECT id FROM relations WHERE status='active' AND (from_card_id=? OR to_card_id=?)",
                            (source.id, source.id),
                        )
                    ]
                    if {rel.id: rel.version for rel in current_relations} != {
                        rel["id"]: rel["version"] for rel in before_relations
                    }:
                        raise ConflictError("Relations changed since merge proposal")
                    payload = target.model_dump()
                    payload.update(
                        {
                            key: value
                            for key, value in op.after.items()
                            if key not in {"source_card_id", "source_base_version"}
                        }
                    )
                    payload["version"] = target.version + 1
                    self._write_card(
                        Card.model_validate(payload),
                        commit_id=commit_id,
                        evidence_ids=op.evidence_ids,
                    )
                    source_payload = source.model_dump()
                    source_payload.update(status="archived", version=source.version + 1)
                    self._write_card(
                        Card.model_validate(source_payload),
                        commit_id=commit_id,
                        evidence_ids=[],
                    )
                    for relation in current_relations:
                        self.conn.execute(
                            "UPDATE relations SET from_card_id=?,to_card_id=?,version=version+1 WHERE id=?",
                            (
                                target.id
                                if relation.from_card_id == source.id
                                else relation.from_card_id,
                                target.id
                                if relation.to_card_id == source.id
                                else relation.to_card_id,
                                relation.id,
                            ),
                        )
                elif op.kind == "add_relation":
                    rel = op.after
                    for card_id in (rel.get("from_card_id"), rel.get("to_card_id")):
                        if not card_id or self.get_card(card_id).status != "active":
                            raise ConflictError("Relation requires active cards")
                    existing = self.conn.execute(
                        "SELECT version,status FROM relations WHERE id=?",
                        (op.target_id,),
                    ).fetchone()
                    if existing:
                        if (
                            existing["status"] != "archived"
                            or existing["version"] != op.base_version
                        ):
                            raise ConflictError("Relation changed")
                        self.conn.execute(
                            "UPDATE relations SET from_card_id=?,to_card_id=?,type=?,note=?,"
                            "version=version+1,status='active' WHERE id=?",
                            (
                                rel["from_card_id"],
                                rel["to_card_id"],
                                rel["type"],
                                rel.get("note"),
                                op.target_id,
                            ),
                        )
                    else:
                        self.conn.execute(
                            "INSERT INTO relations VALUES(?,?,?,?,?,?,?)",
                            (
                                op.target_id,
                                rel["from_card_id"],
                                rel["to_card_id"],
                                rel["type"],
                                rel.get("note"),
                                1,
                                "active",
                            ),
                        )
                elif op.kind == "remove_relation":
                    row = self.conn.execute(
                        "SELECT version FROM relations WHERE id=? AND status='active'",
                        (op.target_id,),
                    ).fetchone()
                    if not row or row["version"] != op.base_version:
                        raise ConflictError("Relation changed")
                    self.conn.execute(
                        "UPDATE relations SET status='archived',version=version+1 WHERE id=?",
                        (op.target_id,),
                    )
                else:
                    raise ValueError(f"Unsupported operation: {op.kind}")
            dangling = self.conn.execute(
                "SELECT r.id FROM relations r JOIN cards a ON a.id=r.from_card_id "
                "JOIN cards b ON b.id=r.to_card_id WHERE r.status='active' "
                "AND (a.status!='active' OR b.status!='active') LIMIT 1"
            ).fetchone()
            if dangling:
                raise ConflictError("Active relation points to an archived card")
            self.conn.execute(
                "UPDATE changesets SET status='committed' WHERE id=?", (changeset_id,)
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return self.get_commit(commit_id)

    def get_commit(self, commit_id: str) -> Commit:
        row = self.conn.execute(
            "SELECT * FROM commits WHERE id=?", (commit_id,)
        ).fetchone()
        if not row:
            raise KeyError(commit_id)
        return Commit(
            id=row["id"],
            changeset_id=row["changeset_id"],
            committed_at=row["committed_at"],
            inverse_of=row["inverse_of"],
        )

    def list_commits(self) -> list[Commit]:
        return [
            self.get_commit(row["id"])
            for row in self.conn.execute(
                "SELECT id FROM commits ORDER BY committed_at DESC"
            )
        ]

    def revert(self, commit_id: str) -> Commit:
        original = self.get_changeset(self.get_commit(commit_id).changeset_id)
        inverse = []
        source_ids = set(original.source_ids)
        for op in reversed([op for op in original.operations if op.staged]):
            if op.kind == "create_card":
                if self.get_card(op.target_id or "").version != 1:
                    raise ConflictError("Cannot revert creation after later edits")
                inverse.append(
                    OperationDraft(
                        kind="archive_card", target_id=op.target_id, base_version=1
                    )
                )
            elif op.kind in ("update_card", "archive_card"):
                current = self.get_card(op.target_id or "")
                if current.version != (op.base_version or 0) + 1:
                    raise ConflictError("Cannot revert after later edits")
                if not op.before:
                    raise ConflictError("Original state unavailable")
                inverse.append(
                    OperationDraft(
                        kind="update_card",
                        target_id=current.id,
                        base_version=current.version,
                        after={
                            key: op.before[key]
                            for key in ("type", "title", "content", "fields", "status")
                        },
                        evidence_ids=self._revision_evidence(
                            current.id, op.base_version or 0
                        ),
                    )
                )
            elif op.kind == "merge_card":
                before = op.before or {}
                original_target = before.get("target")
                original_source = before.get("source")
                if not original_target or not original_source:
                    raise ConflictError("Merge snapshot unavailable")
                target = self.get_card(original_target["id"])
                source = self.get_card(original_source["id"])
                if (
                    target.version != original_target["version"] + 1
                    or source.version != original_source["version"] + 1
                    or source.status != "archived"
                ):
                    raise ConflictError("Cannot revert merge after card edits")
                inverse.extend(
                    [
                        OperationDraft(
                            kind="update_card",
                            target_id=target.id,
                            base_version=target.version,
                            after={
                                key: original_target[key]
                                for key in (
                                    "type",
                                    "title",
                                    "content",
                                    "fields",
                                    "status",
                                )
                            },
                            evidence_ids=before.get("target_evidence", []),
                        ),
                        OperationDraft(
                            kind="update_card",
                            target_id=source.id,
                            base_version=source.version,
                            after={
                                key: original_source[key]
                                for key in (
                                    "type",
                                    "title",
                                    "content",
                                    "fields",
                                    "status",
                                )
                            },
                            evidence_ids=before.get("source_evidence", []),
                        ),
                    ]
                )
                for relation in before.get("relations", []):
                    relation_current = self.get_relation(relation["id"])
                    if relation_current.version != relation["version"] + 1:
                        raise ConflictError("Cannot revert merge after relation edits")
                    inverse.extend(
                        [
                            OperationDraft(
                                kind="remove_relation",
                                target_id=relation_current.id,
                                base_version=relation_current.version,
                            ),
                            OperationDraft(
                                kind="add_relation",
                                target_id=relation_current.id,
                                base_version=relation_current.version + 1,
                                after={
                                    key: relation[key]
                                    for key in (
                                        "from_card_id",
                                        "to_card_id",
                                        "type",
                                        "note",
                                    )
                                },
                            ),
                        ]
                    )
            elif op.kind == "add_relation":
                row = self.conn.execute(
                    "SELECT version,status FROM relations WHERE id=?", (op.target_id,)
                ).fetchone()
                expected = (op.base_version or 0) + 1
                if not row or row["version"] != expected or row["status"] != "active":
                    raise ConflictError("Relation unavailable")
                inverse.append(
                    OperationDraft(
                        kind="remove_relation",
                        target_id=op.target_id,
                        base_version=row["version"],
                    )
                )
            elif op.kind == "remove_relation":
                row = self.conn.execute(
                    "SELECT version,status FROM relations WHERE id=?", (op.target_id,)
                ).fetchone()
                if (
                    not row
                    or row["version"] != (op.base_version or 0) + 1
                    or row["status"] != "archived"
                    or not op.before
                ):
                    raise ConflictError("Cannot restore changed relation")
                inverse.append(
                    OperationDraft(
                        kind="add_relation",
                        target_id=op.target_id,
                        base_version=row["version"],
                        after={
                            key: op.before[key]
                            for key in ("from_card_id", "to_card_id", "type", "note")
                        },
                    )
                )
            else:
                raise ConflictError(f"Revert of {op.kind} is not yet supported")
        for operation in inverse:
            for evidence_id in operation.evidence_ids:
                row = self.conn.execute(
                    "SELECT source_id FROM evidence WHERE id=?", (evidence_id,)
                ).fetchone()
                if row:
                    source_ids.add(row["source_id"])
        changeset = self.create_changeset(sorted(source_ids), inverse)
        self.stage(changeset.id, [op.id for op in changeset.operations])
        return self.commit(changeset.id, inverse_of=commit_id)

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return []
        expression = " OR ".join(
            '"' + term.replace('"', "") + '"' for term in terms[:12]
        )
        rows = self.conn.execute(
            "SELECT card_id, bm25(cards_fts) AS rank FROM cards_fts WHERE cards_fts MATCH ? ORDER BY rank LIMIT ?",
            (expression, limit),
        ).fetchall()
        hits = []
        for row in rows:
            hits.append(self.hit(row["card_id"], score=-float(row["rank"])))
        return hits

    def hit(self, card_id: str, *, score: float = 0.0) -> SearchHit:
        card = self.get_card(card_id)
        evidence = self.conn.execute(
            "SELECT e.* FROM evidence e JOIN card_evidence ce ON ce.evidence_id=e.id "
            "WHERE ce.card_id=? AND ce.card_version=? ORDER BY e.rowid",
            (card.id, card.version),
        )
        citations = [
            AnswerCitation(
                source_id=e["source_id"],
                evidence_id=e["id"],
                locator=json.loads(e["locator_json"]),
                snippet=e["text"][:240],
                card_id=card.id,
                card_version=card.version,
                trust=e["trust"],
            )
            for e in evidence
        ]
        return SearchHit(card=card, citations=citations, score=score)
