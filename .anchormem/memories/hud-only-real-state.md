---
id: hud-only-real-state
title: The HUD shows only real state: red means the code really changed
origin: agent
created: 2026-11-14
verified: 2026-11-14
commit: ddf6dbc
anchors:
  - src/anchormem/hud.py#data  # fp:c63a01012454
---
The control-room view is used in promo videos, so it must never invent anything: a memory is red only when check says so, the code panel diffs the anchored symbol (not the whole file), and the hook feed reads hook-events.jsonl, which the hooks really write. Animation may decorate the data, never change it.
