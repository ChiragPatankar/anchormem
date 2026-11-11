---
id: handoff-claim-once
title: SessionStart takes a handoff once, and shows it again after a resume or compact
origin: agent
created: 2026-11-10
verified: 2026-11-10
commit: fdcc6c5
anchors:
  - src/anchormem/hooks.py#session_start_text  # fp:d7e6fe3eaea0
---
A handoff must not be picked up by two sessions, but Claude Code also fires SessionStart after compacting, which wipes the context. The session that took it is remembered in .anchormem/.state so it can see it again.
