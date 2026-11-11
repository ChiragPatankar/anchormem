---
id: stdlib-only
title: Standard library only, Python 3.9 and newer
origin: agent
created: 2026-11-10
verified: 2026-11-10
commit: 62a092e
anchors:
  - pyproject.toml  # fp:69f3e580fa20
---
No dependencies keeps pip install fast and safe. CI tests 3.9 and 3.13, so avoid newer syntax such as match statements or X | None at runtime.
