#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pytest -q packages/twindex-core/tests packages/twindex-cli/tests --ignore=packages/twindex-core/tests/test_semantic.py
ruff check packages/*/src packages/*/tests scripts
ruff format --check packages/*/src packages/*/tests scripts
mypy --config-file packages/twindex-core/mypy.ini packages/twindex-core/src/twindex_core packages/twindex-cli/src/twindex_cli
python -m build --outdir dist packages/twindex-core
python -m build --outdir dist packages/twindex-cli
python scripts/check_artifacts.py
python scripts/check_source.py
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python -m venv "$work/venv"
"$work/venv/bin/python" -m pip install dist/*.whl
"$work/venv/bin/python" -m pip check
cp scripts/installed_smoke.py "$work/smoke.py"
(cd "$work" && env -u PYTHONPATH "$work/venv/bin/python" -I smoke.py)
