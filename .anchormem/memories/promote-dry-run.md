---
id: promote-dry-run
title: promote never writes memories without --apply
origin: agent
confirmed: 2026-11-12
created: 2026-11-10
verified: 2026-11-10
commit: fdcc6c5
anchors:
  - src/anchormem/cli.py#cmd_promote  # fp:00ba60753f1a
---
Promoted memories come from agent notes, so a person should see the candidates first. Promoted memories are marked agent-written and unconfirmed.
