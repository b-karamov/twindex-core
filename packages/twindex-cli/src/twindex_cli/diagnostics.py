"""Private, content-free reports for model failures."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from twindex_core.engine import ModelOutputError


def write_model_report(root: Path, action: str, model: str, error: Exception) -> Path:
    # Deliberately exclude exception text, request/response bodies and HTTP URLs.
    details = {}
    if isinstance(error, ModelOutputError):
        source = error.diagnostics
        details["schema"] = source.get("schema")
        details["attempts"] = [
            {
                key: attempt[key]
                for key in ("finish_reason", "usage", "output_characters", "errors")
                if key in attempt
            }
            for attempt in source.get("attempts", [])[:2]
        ]
    report = {
        "id": uuid4().hex,
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "model": model,
        "error_type": type(error).__name__,
        "details": details,
    }
    directory = root / "diagnostics"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{report['id']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path
