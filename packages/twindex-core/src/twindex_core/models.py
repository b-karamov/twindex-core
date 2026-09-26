from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


OperationKind = Literal[
    "create_card",
    "update_card",
    "merge_card",
    "archive_card",
    "add_relation",
    "remove_relation",
]


class SourceSnapshot(BaseModel):
    id: str
    kind: str
    title: str
    uri: str | None = None
    sha256: str
    imported_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    id: str
    source_id: str
    locator: dict[str, Any]
    text: str
    trust: Literal["source", "user_assertion", "assistant_unverified"] = "source"


class Card(BaseModel):
    id: str
    type: str
    title: str
    content: str
    fields: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "archived"] = "active"
    version: int = 1


class Relation(BaseModel):
    id: str
    from_card_id: str
    to_card_id: str
    type: str
    note: str | None = None
    version: int = 1


class OperationDraft(BaseModel):
    kind: OperationKind
    target_id: str | None = None
    base_version: int | None = None
    after: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class ProposalOperation(OperationDraft):
    id: str
    before: dict[str, Any] | None = None
    staged: bool = False


class Changeset(BaseModel):
    id: str
    status: Literal["open", "committed", "discarded"] = "open"
    source_ids: list[str]
    operations: list[ProposalOperation]
    created_at: datetime
    model: str | None = None
    prompt_version: str | None = None


class Commit(BaseModel):
    id: str
    changeset_id: str
    committed_at: datetime
    inverse_of: str | None = None


class AnswerCitation(BaseModel):
    source_id: str
    evidence_id: str
    locator: dict[str, Any]
    snippet: str
    card_id: str
    card_version: int
    trust: Literal["source", "user_assertion", "assistant_unverified"] = "source"


class SearchHit(BaseModel):
    card: Card
    citations: list[AnswerCitation]
    score: float = 0.0


class Answer(BaseModel):
    text: str
    citations: list[AnswerCitation]
    insufficient_evidence: bool = False
