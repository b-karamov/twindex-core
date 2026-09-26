# Contributing

Use Python 3.12+ and uv. Run `uv sync --locked --all-packages --extra semantic`
and `uv run --no-sync bash scripts/check.sh` before submitting a pull request.
Do not submit vaults, dialogue exports, diagnostics, API keys or personal files.
Use synthetic fixtures and preserve provenance, record identity and atomic commits.

Use Conventional Commits (`feat(core): ...`, `fix(cli): ...`). Contributions are
licensed under Apache-2.0; retain attribution for third-party code. Discuss API or
vault schema changes before implementation. No automatic merges or releases.
