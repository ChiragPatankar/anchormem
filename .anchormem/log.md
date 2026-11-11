# anchormem log

Append-only record of memory changes. Newest at the bottom.

## [2026-11-10 20:08] add | fingerprint-whitespace | by agent | src/anchormem/anchors.py#fingerprint
## [2026-11-10 20:08] add | hooks-never-fail | by agent | src/anchormem/hooks.py#run
## [2026-11-10 20:08] add | frontmatter-yaml | by agent | src/anchormem/store.py#Memory.to_text, src/anchormem/store.py#parse
## [2026-11-10 20:08] add | exit-codes | by agent | src/anchormem/cli.py#cmd_check, src/anchormem/cli.py#main
## [2026-11-10 20:08] add | stdlib-only | by agent | pyproject.toml
## [2026-11-10 20:08] add | move-detection-order | by agent | src/anchormem/core.py#check_anchor
## [2026-11-10 20:08] verify | stdlib-only | pyproject.toml fp 648178d3c812 -> 079640b1bf52 @ 81089aa
## [2026-11-10 20:34] verify | exit-codes | src/anchormem/cli.py#cmd_check fp 836dec2b563c -> 3001bdab017f @ fdcc6c5
## [2026-11-10 20:34] verify | stdlib-only | pyproject.toml fp 079640b1bf52 -> 485488e03467 @ fdcc6c5
## [2026-11-10 20:34] add | handoff-claim-once | by agent | src/anchormem/hooks.py#session_start_text
## [2026-11-10 20:34] add | promote-dry-run | by agent | src/anchormem/cli.py#cmd_promote
