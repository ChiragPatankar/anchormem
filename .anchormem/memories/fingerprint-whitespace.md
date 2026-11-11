---
id: fingerprint-whitespace
title: Fingerprints ignore all whitespace
origin: agent
created: 2026-11-10
verified: 2026-11-10
commit: 81089aa
anchors:
  - src/anchormem/anchors.py#fingerprint  # fp:904a8218b1a8
---
Formatter runs (re-indenting, a=1 to a = 1) must not flag memories. Trade-off: a change only to spaces inside a string literal is not detected.
