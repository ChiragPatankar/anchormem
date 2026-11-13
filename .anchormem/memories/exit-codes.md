---
id: exit-codes
title: check exits 0 when fresh, 1 when stale, 2 on usage errors
origin: agent
confirmed: 2026-11-12
created: 2026-11-10
verified: 2026-11-10
commit: 62a092e
anchors:
  - src/anchormem/cli.py#cmd_check  # fp:3001bdab017f
  - src/anchormem/cli.py#main  # fp:fb6c44aa4990
---
CI jobs and pre-commit hooks depend on these codes. Changing them breaks users' pipelines.
