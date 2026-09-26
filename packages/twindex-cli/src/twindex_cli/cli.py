from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpcore
import httpx
from platformdirs import user_data_path
from pydantic import BaseModel

from twindex_core.engine import KnowledgeEngine
from twindex_core.models import OperationDraft
from twindex_core.provider import OpenAICompatibleProvider
from twindex_core.sources import (
    ConsentRequired,
    SourceIngestor,
    default_conversation_roots,
    discover_conversations,
)
from twindex_core.vault import Vault


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twindex", description="Local knowledge vault and inbox"
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get(
            "TWINDEX_VAULT", str(user_data_path("twindex") / "default")
        ),
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument(
        "--profile", choices=["ollama", "cloud", "custom"], default="ollama"
    )
    parser.add_argument("--model")
    parser.add_argument("--embedding-model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--vision", action="store_true", help="Declare custom model vision capability"
    )
    parser.add_argument(
        "--allow-cloud-sources",
        action="store_true",
        help="Explicitly permit sending vault data to cloud",
    )
    parser.add_argument(
        "--request-timeout", type=float, help="Model request timeout in seconds"
    )
    commands = parser.add_subparsers(dest="command")

    def one(name: str, argument: str) -> None:
        commands.add_parser(name).add_argument(argument)

    one("ingest", "path")
    one("ingest-url", "url")
    commands.add_parser("sources")
    one("evidence", "source_id")
    commands.add_parser("cards")
    commands.add_parser("inbox")
    created = commands.add_parser("changeset-create")
    created.add_argument("source_id")
    created.add_argument("operations_json")
    commands.add_parser("changesets")
    one("diff", "changeset_id")
    one("export-changeset", "changeset_id")
    edit = commands.add_parser("edit")
    edit.add_argument("changeset_id")
    edit.add_argument("operation_id")
    edit.add_argument("after_json")
    for command in ("stage", "unstage"):
        sub = commands.add_parser(command)
        sub.add_argument("changeset_id")
        sub.add_argument("operation_ids", nargs="+")
    one("commit", "changeset_id")
    commands.add_parser("log")
    one("revert", "commit_id")
    propose = commands.add_parser("propose")
    propose.add_argument("source_ids", nargs="+")
    commands.add_parser("garden")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument(
        "--semantic", action="store_true", help="Combine FTS and model embeddings"
    )
    commands.add_parser("index-rebuild")
    one("answer", "question")
    commands.add_parser("diagnose")
    discover = commands.add_parser("discover")
    exclusive = discover.add_mutually_exclusive_group()
    exclusive.add_argument(
        "--grant",
        action="store_true",
        help="Allow metadata scan of known conversation directories",
    )
    exclusive.add_argument(
        "--revoke", action="store_true", help="Revoke conversation discovery permission"
    )
    return parser


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _config_path(vault: Vault) -> Path:
    return vault.root / "config.json"


def _consent(vault: Vault, setting: bool | None = None) -> bool:
    path = _config_path(vault)
    if setting is None:
        return (
            json.loads(path.read_text(encoding="utf-8")).get(
                "conversation_discovery", False
            )
            if path.exists()
            else False
        )
    payload = {"conversation_discovery": setting}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return setting


def _provider(args: argparse.Namespace) -> OpenAICompatibleProvider:
    if args.profile == "cloud" and not args.allow_cloud_sources:
        raise PermissionError("Cloud transfer requires --allow-cloud-sources")
    return OpenAICompatibleProvider(
        profile=args.profile,
        model=args.model,
        embedding_model=args.embedding_model,
        base_url=args.base_url,
        allow_cloud_sources=args.allow_cloud_sources,
        vision=args.vision if args.profile == "custom" else None,
        request_timeout=args.request_timeout,
    )


def _execute(args: argparse.Namespace, vault: Vault) -> Any:
    command = args.command
    if command == "ingest":
        return SourceIngestor(vault).ingest_path(args.path)
    if command == "ingest-url":
        return SourceIngestor(vault).ingest_url(args.url)
    if command == "sources":
        return vault.list_sources()
    if command == "evidence":
        return vault.list_evidence(args.source_id)
    if command == "cards":
        return vault.list_cards()
    if command == "inbox":
        processed = {
            source_id
            for changeset in vault.list_changesets()
            for source_id in changeset.source_ids
        }
        return [source for source in vault.list_sources() if source.id not in processed]
    if command == "changeset-create":
        data = json.loads(Path(args.operations_json).read_text(encoding="utf-8"))
        operations = [
            OperationDraft.model_validate(item) for item in data["operations"]
        ]
        return vault.create_changeset([args.source_id], operations)
    if command == "changesets":
        return vault.list_changesets()
    if command == "diff":
        return {"patch": vault.diff(args.changeset_id)}
    if command == "export-changeset":
        return json.loads(vault.export_changeset(args.changeset_id))
    if command == "edit":
        after = json.loads(Path(args.after_json).read_text(encoding="utf-8"))
        return vault.edit(args.changeset_id, args.operation_id, after)
    if command in {"stage", "unstage"}:
        return vault.stage(
            args.changeset_id, args.operation_ids, staged=command == "stage"
        )
    if command == "commit":
        return vault.commit(args.changeset_id)
    if command == "log":
        return vault.list_commits()
    if command == "revert":
        return vault.revert(args.commit_id)
    if command in {"propose", "garden", "answer", "diagnose", "index-rebuild"} or (
        command == "search" and args.semantic
    ):
        with _provider(args) as provider:
            if command == "diagnose":
                return provider.diagnose()
            if command == "index-rebuild":
                from twindex_core.semantic import SemanticIndex

                return {
                    "indexed_cards": SemanticIndex(vault, provider).rebuild(),
                    "embedding_model": provider.embedding_model,
                }
            engine = KnowledgeEngine(vault, provider)
            if command == "propose":
                return engine.propose(args.source_ids)
            if command == "garden":
                return engine.garden_scan()
            if command == "search":
                return engine.search(args.query)
            return engine.answer(args.question)
    if command == "search":
        return vault.search(args.query)
    if command == "discover":
        if args.grant:
            _consent(vault, True)
        if args.revoke:
            _consent(vault, False)
            return {"conversation_discovery": False}
        if not _consent(vault):
            raise ConsentRequired(
                "Run 'twindex discover --grant' before scanning known conversation folders"
            )
        return [
            {
                "path": str(item.path),
                "size_bytes": item.size_bytes,
                "modified_at": item.modified_at,
            }
            for item in discover_conversations(
                default_conversation_roots(), consent=True
            )
        ]
    raise ValueError(f"Unknown command: {command}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        from .tui import run_tui

        run_tui(Path(args.vault), args)
        return 0
    try:
        with Vault.open(args.vault) as vault:
            result = _plain(_execute(args, vault))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif isinstance(result, dict) and "patch" in result:
            print(result["patch"], end="")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        KeyError,
        ValueError,
        RuntimeError,
        PermissionError,
        OSError,
        httpx.HTTPError,
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
        httpcore.ProxyError,
    ) as exc:
        print(f"twindex: {exc}", file=sys.stderr)
        return 2


def entrypoint() -> None:
    raise SystemExit(main())
