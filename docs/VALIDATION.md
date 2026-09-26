# Alpha Validation

- 141 tests pass on macOS Python 3.12 and 3.14.
- GitHub CI: 140 base tests on Linux/macOS Python 3.12/3.14 plus a separate
  Linux semantic-index test.
- Ruff, mypy and independent wheel/sdist builds pass.
- Exact Apache-2.0 LICENSE, NOTICE and SPDX metadata verified in both distributions.
- Clean wheel installation outside the checkout passed Core import/propose/edit/
  stage/commit/Gardener/cited answer/revert, CLI and headless TUI with an offline
  provider. No server or cloud account was used.
- Core-only installation passed without Textual or CLI.
- Interactive macOS PTY launch and Ctrl+Q exit passed with a synthetic vault.
- Direct dependency license metadata and packaged license files reviewed.
- Manual Release workflow repeats CI before alpha publication.
- Live Ollama and cloud model gates are unverified for this release.

This is alpha validation, not a declaration of full v1 readiness.
