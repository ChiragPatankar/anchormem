---
id: handoff-claim-once
title: SessionStart takes a handoff once, and shows it again after a resume or compact
origin: agent
confirmed: 2026-11-12
created: 2026-11-10
verified: 2026-11-14
commit: ddf6dbc
anchors:
  - src/anchormem/hooks.py#session_start_text  # fp:c4d878d92af0
---
A handoff must not be picked up by two sessions, but Claude Code also fires SessionStart after compacting, which wipes the context. The session that took it is remembered in .anchormem/.state so it can see it again.
