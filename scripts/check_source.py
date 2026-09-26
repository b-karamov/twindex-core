"""Validate reviewed source integrity and block accidental private-file publication."""

import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "docs/source-manifest.json").read_text())
for entry in manifest["files"]:
    if entry["disposition"] == "excluded":
        assert entry["reason"] and (root / entry["replacement"]).is_file()
        continue
    path = root / entry["source"]
    assert path.is_file(), path
    if entry["disposition"] == "copied unchanged":
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], path
for folder in (root / "packages", root / "docs", root / "scripts", root / ".github"):
    for path in folder.rglob("*"):
        if not path.is_file() or any(
            part in {"__pycache__", "build"} or part.endswith(".egg-info")
            for part in path.parts
        ):
            continue
        assert path.suffix not in {".sqlite", ".sqlite3", ".db", ".jsonl"}, path
        if path.suffix in {".py", ".md", ".toml", ".yml", ".json", ".sh"}:
            text = path.read_text()
            assert not re.search(
                r"/Users/"
                + "b-karamov/|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----|\bsk-[a-zA-Z0-9]{24,}",
                text,
            ), path
print(f"Verified {len(manifest['files'])} source entries and publishable text scan")
