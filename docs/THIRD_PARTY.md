# Third-party Licensing

Twindex's own code is Apache-2.0, authored by Bulat Karamov. OpenClaw inspired interaction principles; no OpenClaw/Clack code
was copied.

Direct runtime dependencies (upstream package metadata is authoritative):

| Component | License |
| --- | --- |
| httpx, httpcore | BSD-3-Clause |
| pydantic | MIT |
| pypdf | BSD-3-Clause |
| pypdfium2 | Apache-2.0 OR BSD-3-Clause; bundled PDFium has additional notices |
| Pillow | MIT-CMU |
| defusedxml | Python Software Foundation License |
| Textual, platformdirs, PyObjC/Cocoa | MIT |
| Chroma (optional) | Apache-2.0 |

Dependencies are downloaded separately, not vendored. Their installed wheels
retain their own license files, including PDFium's third-party notices. Twindex
LICENSE/NOTICE never replaces these. Before distributing a combined executable,
collect the exact installed dependency notices, including transitive components.
`scripts/dependency_licenses.py` inventories the active environment's license
metadata and notice paths for review. The uv lock records exact resolved versions.
License metadata is evidence, not a substitute for review of upstream terms.
