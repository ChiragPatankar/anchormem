---
id: exit-codes
title: check exits 0 when fresh, 1 when stale, 2 on usage errors
origin: agent
created: 2026-11-10
verified: 2026-11-10
commit: fdcc6c5
anchors:
  - src/anchormem/cli.py#cmd_check  # fp:3001bdab017f
  - src/anchormem/cli.py#main  # fp:0fac88cf4b15
---
CI jobs and pre-commit hooks depend on these codes. Changing them breaks users' pipelines.
