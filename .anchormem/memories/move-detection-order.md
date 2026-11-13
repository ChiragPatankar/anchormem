---
id: move-detection-order
title: Move detection tries git renames, then whole-file fingerprints, then a symbol search
origin: agent
confirmed: 2026-11-12
created: 2026-11-10
verified: 2026-11-10
commit: 81089aa
anchors:
  - src/anchormem/core.py#check_anchor  # fp:4c6fd5c8d8aa
---
Git renames are the most reliable signal, so they come first. The symbol search is the slowest and can return several candidates, which is why verify --follow only follows a single match.
