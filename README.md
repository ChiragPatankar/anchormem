# anchormem

**Agent memory that knows when it's wrong.**

Coding agents (Claude Code, Codex, Cursor and the rest) forget everything between sessions, so people give them memory files: notes like "the pool is capped at 10 because of our Postgres plan" or "invoices round per line item". Those notes go stale as the code changes, and the agent keeps trusting them anyway.

anchormem ties each memory to the code it describes. When that code changes, moves or disappears, the memory gets flagged, so the agent knows to check it first.

```
$ anchormem check
✓ invoices-round-per-line-item  OK          src/billing/lines.ts#lineTotal
✗ pool-capped-10-postgres-plan  CHANGED     src/db/pool.py#POOL_SIZE  since 6ca7e9a

1 of 2 memories need checking (2 anchors)
```

- Memories are plain Markdown in `.anchormem/memories/` and are committed with your code. There's no database or server.
- It works with any agent. `anchormem init` writes short instructions into `AGENTS.md` (and `CLAUDE.md` if you have one).
- It uses only the Python standard library. Git is optional, but with git it can follow renamed files.
- `anchormem check` exits with 1 when anything is stale, so you can run it in CI or a pre-commit hook.

## Install

```bash
pip install git+https://github.com/ChiragPatankar/anchormem
```

Or from a local clone:

```bash
pip install -e .
```

Requires Python 3.9 or newer.

## Quick start

```bash
cd your-repo
anchormem init
anchormem add "Pool capped at 10 because of the Postgres plan limit" \
  --anchor src/db/pool.py#POOL_SIZE \
  --body "Hosted plan allows 25 connections across 2 workers. Raise it only after upgrading."
```

To see it catch a change, run the demo. It builds a throwaway repo, changes and renames code, and shows each command's output:

```bash
bash examples/demo.sh
```

## Anchors

An anchor says which code a memory is about. Paths are relative to the repo root, or to your current folder when you type them.

| Anchor | Points at |
|---|---|
| `src/db/pool.py` | the whole file |
| `src/db/pool.py#get_pool` | a function, class or constant |
| `src/api/app.py#App.build` | a method inside a class |
| `src/config.py:10-24` | a line range (fragile, so prefer symbols) |

Python symbols are found with Python's own parser. JavaScript, TypeScript, Go, Rust, Java, C# and similar languages use a text-based finder that matches braces.

A memory can have several anchors. It's stale if any of them is.

## Statuses

| Status | Meaning | What to do |
|---|---|---|
| `OK` | The anchored code is unchanged since the memory was last verified. | Nothing. |
| `CHANGED` | The code is different. | Read the memory and the code. If it's still true, run `anchormem verify <id>`. If not, edit the memory. |
| `MOVED` | The file was renamed, or the symbol now lives in another file. | `anchormem verify <id> --follow` updates the anchor. |
| `MISSING` | The file or symbol is gone. | Edit the anchor or delete the memory. |
| `UNVERIFIED` | The anchor was written by hand and has no fingerprint yet. | `anchormem verify <id>`. |

Whitespace doesn't count as a change, so reformatting or re-indenting code won't flag anything.

## Commands

| Command | What it does |
|---|---|
| `anchormem init [--claude]` | Creates `.anchormem/memories/` and adds agent instructions to `AGENTS.md` (and to `CLAUDE.md` if it exists or you pass `--claude`). |
| `anchormem add "<title>" -a <anchor> [-a …] [--body …] [--id …]` | Saves a memory. Fails if an anchor doesn't point at real code. The body can also be piped in on stdin. |
| `anchormem check [ids…] [--json] [-q] [-v]` | Checks every memory against the code. Exits with 1 if anything is stale. |
| `anchormem context <file-or-folder…> [--all] [--json]` | Prints the memories relevant to those paths, with stale ones marked, for an agent to read before editing. |
| `anchormem verify <id…> [--follow]` | Marks memories as still true and records new fingerprints and the current commit. |
| `anchormem list [--json]` | Lists memories. |
| `anchormem rm <id…>` | Deletes memories. |

## The memory file

```markdown
---
id: pool-capped-10-postgres-plan
title: Pool capped at 10 because of the Postgres plan limit
created: 2026-11-10
verified: 2026-11-10
commit: 6ca7e9a
anchors:
  - src/db/pool.py#POOL_SIZE  # fp:9a525c65e0ef
---
Hosted plan allows 25 connections across 2 workers. Raise it only after upgrading.
```

The frontmatter is valid YAML, and each fingerprint is stored as a YAML comment. You can edit these files by hand. An anchor you add without a fingerprint shows as `UNVERIFIED` until you run `verify`.

## In CI

Copy [examples/github-action.yml](examples/github-action.yml) to `.github/workflows/`. A pull request that changes code without re-checking the memories about it will then fail.

To check before every commit instead, add this as `.git/hooks/pre-commit`:

```bash
#!/bin/sh
exec anchormem check -q
```

## Development

```bash
python3 -m unittest discover -s tests
```

## License

MIT
