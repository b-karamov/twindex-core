from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .source_io import MAX_CONVERSATION_PREVIEW_BYTES


class ConsentRequired(PermissionError):
    pass


@dataclass(frozen=True)
class ConversationFile:
    path: Path
    size_bytes: int
    modified_at: float


def default_conversation_roots() -> list[Path]:
    home = Path.home()
    return [home / ".claude" / "projects", home / ".codex" / "sessions"]


def discover_conversations(
    roots: list[Path] | None = None, *, consent: bool = False
) -> list[ConversationFile]:
    if not consent:
        raise ConsentRequired(
            "Explicit consent is required before scanning conversation directories"
        )
    results: list[ConversationFile] = []
    for root in roots if roots is not None else default_conversation_roots():
        root = Path(root).expanduser()
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob("*.jsonl"):
            if path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
            results.append(
                ConversationFile(
                    path=path, size_bytes=info.st_size, modified_at=info.st_mtime
                )
            )
            if len(results) >= 5000:
                return sorted(results, key=lambda item: item.modified_at, reverse=True)
    return sorted(results, key=lambda item: item.modified_at, reverse=True)


def conversation_origin(path: str | Path) -> str:
    """Return a human label for a known conversation directory."""
    parts = {part.lower() for part in Path(path).parts}
    if ".claude" in parts or any("claude" in part for part in parts):
        return "Claude Code"
    if ".codex" in parts or any("codex" in part for part in parts):
        return "Codex"
    return "Диалог"


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
            and block.get("type") in {"text", "input_text", "output_text"}
        ).strip()
    return ""


def _chat_record_message(record: dict) -> tuple[str, str]:
    role = ""
    message = ""
    record_type = record.get("type")
    payload_value = record.get("payload")
    payload: dict = payload_value if isinstance(payload_value, dict) else {}
    if record_type in {"user", "assistant"} and isinstance(record.get("message"), dict):
        role = record["message"].get("role", record_type)
        message = _content_text(record["message"].get("content"))
    elif record_type == "event_msg" and payload.get("type") == "user_message":
        role, message = "user", str(payload.get("message", ""))
    elif record_type == "response_item" and payload.get("type") == "message":
        role = str(payload.get("role", ""))
        message = _content_text(payload.get("content"))
    elif record.get("role") in {"user", "assistant"}:
        role = str(record["role"])
        message = _content_text(record.get("content"))
    return role, message


_AUTOMATIC_CONTEXT = re.compile(
    r"^\s*<(?:environment_context|permissions|app-context|recommended_plugins)\b",
    re.IGNORECASE,
)

_REASONING_CHANNELS = frozenset({"analysis", "reasoning", "thinking"})


def _record_channel(record: dict) -> str:
    for container in (record, record.get("message"), record.get("payload")):
        if isinstance(container, dict) and container.get("channel"):
            return str(container["channel"]).lower()
    return ""


def reasoning_message_keys(raw: bytes) -> set[tuple[str, str]]:
    """Recover discarded channel metadata for immutable, previously imported chats."""
    text = raw.decode("utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for line, content in enumerate(text.splitlines(), 1):
            try:
                rows.append((line, json.loads(content)))
            except json.JSONDecodeError:
                continue
        jsonl = True
    else:
        messages = value.get("messages") if isinstance(value, dict) else value
        rows = list(enumerate(messages)) if isinstance(messages, list) else [(1, value)]
        jsonl = not isinstance(messages, list)
    keys: set[tuple[str, str]] = set()
    for index, record in rows:
        if not isinstance(record, dict):
            continue
        role, _ = _chat_record_message(record)
        if role != "assistant" or _record_channel(record) not in _REASONING_CHANNELS:
            continue
        fallback = f"line:{index}" if jsonl else f"index:{index}"
        keys.add(
            ("message_id", str(record.get("uuid") or record.get("id") or fallback))
        )
        if jsonl:
            keys.add(("line", str(index)))
    return keys


def conversation_text(role: str, text: str, *, channel: str = "") -> str:
    """Remove known transport wrappers, not quoted instructions or dialogue content."""
    if role not in {"user", "assistant"}:
        return ""
    if role == "assistant":
        if channel.lower() in _REASONING_CHANNELS:
            return ""
        value = text.strip()
        while match := re.match(
            r"^<(think|thinking|analysis|reasoning)\b[^>]*>", value, re.IGNORECASE
        ):
            closing = re.search(
                r"</" + match[1] + r"\s*>", value[match.end() :], re.IGNORECASE
            )
            if closing is None:
                return ""
            value = value[match.end() + closing.end() :].strip()
        return value
    value = text.strip()
    if value.startswith("# AGENTS.md instructions for "):
        instruction_end = value.find("</INSTRUCTIONS>")
        if instruction_end < 0:
            return ""
        value = value[instruction_end + len("</INSTRUCTIONS>") :].strip()
    while True:
        match = re.match(
            r"^<(environment_context|permissions|app-context|recommended_plugins|in-app-browser-context|skills_instructions|INSTRUCTIONS)\b[^>]*>",
            value,
            re.IGNORECASE,
        )
        if not match:
            break
        closing = re.search(
            r"</" + re.escape(match[1]) + r"\s*>", value[match.end() :], re.IGNORECASE
        )
        if closing is None:
            return ""
        value = value[match.end() + closing.end() :].strip()
    return re.sub(r"^#{1,2} My request:\s*", "", value).strip()


def _clean_segments(
    segments: list[tuple[str, dict, str]],
) -> list[tuple[str, dict, str]]:
    return [
        (clean, locator, trust)
        for text, locator, trust in segments
        if (
            clean := conversation_text(
                str(locator.get("role", "")),
                text,
                channel=str(locator.get("channel", "")),
            )
        )
    ]


def _preview_text(message: str, *, max_chars: int) -> str:
    message = conversation_text("user", message)
    request_markers = ("## My request:", "# My request:")
    for marker in request_markers:
        if marker in message:
            message = message.rsplit(marker, 1)[1]
            break
    if _AUTOMATIC_CONTEXT.match(message):
        return ""
    value = " ".join(message.split())
    return value if len(value) <= max_chars else value[: max_chars - 1].rstrip() + "…"


def conversation_preview(
    path: str | Path,
    *,
    max_bytes: int = MAX_CONVERSATION_PREVIEW_BYTES,
    max_chars: int = 160,
) -> str:
    """Read a bounded prefix and return the first recognizable user phrase."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return "Превью недоступно"
    try:
        raw = path.read_bytes()[:max_bytes]
    except OSError:
        return "Превью недоступно"
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        role, message = _chat_record_message(record)
        if role != "user" or not message:
            continue
        preview = _preview_text(message, max_chars=max_chars)
        if preview:
            return preview
    return "Без первой пользовательской фразы"


def _chat_segments(raw: bytes) -> list[tuple[str, dict, str]]:
    segments: list[tuple[str, dict, str]] = []
    for index, line in enumerate(raw.decode("utf-8-sig").splitlines()):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            continue
        role, message = _chat_record_message(record)
        if role not in {"user", "assistant"} or not message:
            continue
        locator = {
            "kind": "conversation",
            "message_index": len(segments),
            "line": index + 1,
            "message_id": record.get("uuid") or record.get("id") or f"line:{index + 1}",
            "role": role,
        }
        if channel := _record_channel(record):
            locator["channel"] = channel
        segments.append(
            (
                message,
                locator,
                "user_assertion" if role == "user" else "assistant_unverified",
            )
        )
    if not segments:
        raise ValueError("No supported user or assistant messages found in JSONL")
    return _clean_segments(segments)


def _markdown_chat_segments(text: str) -> list[tuple[str, dict, str]]:
    segments: list[tuple[str, dict, str]] = []
    current_role: str | None = None
    lines: list[str] = []
    start = 1
    for line_no, line in enumerate(text.splitlines(), 1):
        heading = line.strip().lstrip("# ").strip().lower()
        role = (
            "user"
            if heading in {"user", "human", "пользователь"}
            else (
                "assistant"
                if heading in {"assistant", "claude", "codex", "ассистент"}
                else None
            )
        )
        service_heading = heading in {
            "system",
            "developer",
            "tool",
            "система",
            "analysis",
            "reasoning",
            "thinking",
            "assistant (analysis)",
            "assistant (reasoning)",
            "assistant (thinking)",
        }
        if line.lstrip().startswith("#") and (role or service_heading):
            if current_role and "\n".join(lines).strip():
                segments.append(
                    (
                        "\n".join(lines).strip(),
                        {
                            "kind": "conversation",
                            "message_index": len(segments),
                            "line": start,
                            "role": current_role,
                        },
                        "user_assertion"
                        if current_role == "user"
                        else "assistant_unverified",
                    )
                )
            current_role, lines, start = role, [], line_no + 1
        elif current_role:
            lines.append(line)
    if current_role and "\n".join(lines).strip():
        segments.append(
            (
                "\n".join(lines).strip(),
                {
                    "kind": "conversation",
                    "message_index": len(segments),
                    "line": start,
                    "role": current_role,
                },
                "user_assertion" if current_role == "user" else "assistant_unverified",
            )
        )
    return _clean_segments(segments)


def _json_chat_segments(value: object) -> list[tuple[str, dict, str]]:
    messages = value.get("messages") if isinstance(value, dict) else value
    if not isinstance(messages, list):
        return []
    segments: list[tuple[str, dict, str]] = []
    for index, record in enumerate(messages):
        if not isinstance(record, dict):
            continue
        role = "user" if record.get("role") == "human" else record.get("role")
        text = _content_text(record.get("content"))
        if role not in {"user", "assistant"} or not text:
            continue
        locator = {
            "kind": "conversation",
            "message_index": len(segments),
            "message_id": str(
                record.get("id") or record.get("uuid") or f"index:{index}"
            ),
            "role": role,
        }
        if channel := _record_channel(record):
            locator["channel"] = channel
        segments.append(
            (
                text,
                locator,
                "user_assertion" if role == "user" else "assistant_unverified",
            )
        )
    return _clean_segments(segments)
