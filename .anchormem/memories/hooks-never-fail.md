---
id: hooks-never-fail
title: Hooks must never break the agent's session
origin: agent
confirmed: 2026-11-12
created: 2026-11-10
verified: 2026-11-10
commit: 81089aa
anchors:
  - src/anchormem/hooks.py#run  # fp:42dbd2464f29
---
Claude Code runs these on every file read or edit. Any exception, bad stdin or broken memory file must exit 0 with no output, never a traceback.
