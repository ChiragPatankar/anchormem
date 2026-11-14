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
## [2026-11-10 22:11] confirm | fingerprint-whitespace
## [2026-11-10 22:34] verify | exit-codes | src/anchormem/cli.py#main fp 0fac88cf4b15 -> fb6c44aa4990 @ 62a092e
## [2026-11-10 22:34] verify | stdlib-only | pyproject.toml fp 485488e03467 -> 69f3e580fa20 @ 62a092e
## [2026-11-10 22:35] add | add-stdin-only-when-piped | by agent | src/anchormem/cli.py#_stdin_has_data
## [2026-11-12 01:48] add | no-symlinks-outside-repo | by agent | src/anchormem/codemap.py#inside
## [2026-11-12 21:07] confirm | add-stdin-only-when-piped
## [2026-11-12 21:07] confirm | frontmatter-yaml
## [2026-11-12 21:07] confirm | handoff-claim-once
## [2026-11-12 21:07] confirm | hooks-never-fail
## [2026-11-12 21:07] confirm | move-detection-order
## [2026-11-12 21:07] confirm | no-symlinks-outside-repo
## [2026-11-12 21:07] confirm | promote-dry-run
## [2026-11-12 21:07] confirm | stdlib-only
## [2026-11-12 21:08] confirm | exit-codes
## [2026-11-14 02:45] verify | handoff-claim-once | src/anchormem/hooks.py#session_start_text fp d7e6fe3eaea0 -> c4d878d92af0 @ ddf6dbc
## [2026-11-14 02:45] add | hud-only-real-state | by agent | src/anchormem/hud.py#data
