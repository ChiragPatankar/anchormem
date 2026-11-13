---
id: no-symlinks-outside-repo
title: Never read files through symlinks that point outside the repo
origin: agent
confirmed: 2026-11-12
created: 2026-11-12
verified: 2026-11-12
commit: 486395a
anchors:
  - src/anchormem/codemap.py#inside  # fp:02c2df5b8a1e
---
anchormem try runs on repos cloned from anywhere. A symlink like notes.py -> ~/.ssh/config would otherwise be read and its contents shown on the dashboard. Both the code map and seed call inside() before reading a file.
