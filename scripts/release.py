"""Publish verified artifacts as a draft first, then a GitHub prerelease. No PyPI."""

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import tomllib

root = Path(__file__).resolve().parents[1]
version = os.environ["RELEASE_VERSION"]
if not re.fullmatch(r"\d+\.\d+\.\d+a\d+", version):
    raise SystemExit("Only explicit alpha versions are supported")
for package in ("twindex-core", "twindex-cli"):
    metadata = tomllib.loads(
        (root / "packages" / package / "pyproject.toml").read_text()
    )
    if metadata["project"]["version"] != version:
        raise SystemExit("Release version must match both package versions")
repo = os.environ["GITHUB_REPOSITORY"]
if repo != "b-karamov/twindex-core":
    raise SystemExit("Release publishing is restricted to the upstream repository")
artifacts = sorted((root / "dist").glob("*"))
if len(artifacts) != 4:
    raise SystemExit("Expected two wheels and two source distributions")


def request(url, data, content_type="application/json", method="POST"):
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
            "Accept": "application/vnd.github+json",
            "Content-Type": content_type,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


base = f"https://api.github.com/repos/{repo}"
release = request(
    base + "/releases",
    json.dumps(
        {
            "tag_name": "v" + version,
            "target_commitish": os.environ["GITHUB_SHA"],
            "name": "Twindex " + version,
            "draft": True,
            "prerelease": True,
            "body": (root / "CHANGELOG.md").read_text()
            + "\n\nLive model gates are not certified by offline CI.\n",
        }
    ).encode(),
)
for artifact in artifacts:
    url = (
        release["upload_url"].split("{")[0]
        + "?name="
        + urllib.parse.quote(artifact.name)
    )
    request(url, artifact.read_bytes(), "application/octet-stream")
result = request(
    base + f"/releases/{release['id']}", b'{"draft":false}', method="PATCH"
)
print(result["html_url"])
