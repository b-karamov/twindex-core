"""Print license metadata and preserved notice paths; never inspect user data."""

import importlib.metadata
import json

rows = []
for dist in importlib.metadata.distributions():
    rows.append(
        {
            "name": dist.metadata["Name"],
            "version": dist.version,
            "license": dist.metadata.get("License-Expression")
            or dist.metadata.get("License"),
            "notices": [
                str(p)
                for p in (dist.files or [])
                if any(s in str(p).upper() for s in ("LICENSE", "NOTICE", "COPYING"))
            ],
        }
    )
print(json.dumps(sorted(rows, key=lambda r: r["name"].lower()), indent=2))
