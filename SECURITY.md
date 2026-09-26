# Security

The alpha is experimental and has no production support guarantee. Report a
vulnerability privately using GitHub's private vulnerability reporting if enabled,
or contact the maintainer through their GitHub profile before sharing exploit
or sensitive data publicly. Do not include real vault contents or API keys.

Sources and model outputs are untrusted data. URL imports reject private/local
addresses and revalidate redirects. Archive/file size limits, safe XML parsing,
provenance validation and explicit cloud consent are security boundaries.
Local Ollama requests must remain local; changing providers requires consent.
Native file selection does not override macOS privacy restrictions.

Back up your vault before upgrades. Optional semantic indexes can be rebuilt;
SQLite and immutable snapshots are authoritative. Debug logs may contain model
content: never attach them unredacted. Dependencies are governed by their own
security policies. See README for known alpha limitations.
