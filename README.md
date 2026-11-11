# anchormem

[![ci](https://github.com/ChiragPatankar/anchormem/actions/workflows/ci.yml/badge.svg)](https://github.com/ChiragPatankar/anchormem/actions/workflows/ci.yml)

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
- With Claude Code, hooks show the agent the memories about a file as soon as it reads or edits that file.
- It records who wrote each memory. Memories written by an agent are labelled as notes until a person confirms them.
- It uses only the Python standard library. Git is optional, but with git it can follow renamed files.
- `anchormem check` exits with 1 when anything is stale, so you can run it in CI or a pre-commit hook.

## Install

```bash
pip install git+https://github.com/ChiragPatankar/anchormem
```

Requires Python 3.9 or newer. Run `anchormem doctor` in a repo to check the setup.

## Quick start

```bash
cd your-repo
anchormem init                 # add --hooks if you use Claude Code
anchormem add "Pool capped at 10 because of the Postgres plan limit" \
  --anchor src/db/pool.py#POOL_SIZE \
  --body "Hosted plan allows 25 connections across 2 workers. Raise it only after upgrading."
```

To see it catch a change, run the demo. It builds a throwaway repo, changes and renames code, and shows each command's output:

```bash
bash examples/demo.sh
```

## Using it with Claude Code

```bash
anchormem hooks install        # writes .claude/settings.json; --local for settings.local.json
```

Then restart Claude Code. Two hooks are installed:

- **SessionStart** gives the agent a short summary: how many memories exist, which are stale and why, and how to fix them.
- **PostToolUse**, on `Read`, `Edit`, `Write`, `MultiEdit` and `NotebookEdit`, adds the memories anchored to that file to the agent's context. Each file is shown once per session, and again only if a memory's status changes. Output is capped at about 3,000 characters: details are dropped first, then whole memories, and a note says what was left out.

The hooks never fail a session. Any error makes them exit quietly. `anchormem hooks remove` takes them out and leaves the rest of your settings alone.

Other agents (Codex, Cursor and so on) follow the rules `init` writes into `AGENTS.md` and run `anchormem context <file>` themselves.

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
| `CHANGED` | The code is different. | Read the memory and the code. If it's still true, `anchormem verify <id>`. If not, edit it or retire it. |
| `MOVED` | The file was renamed, or the symbol now lives in another file. | `anchormem verify <id> --follow` updates the anchor. |
| `MISSING` | The file or symbol is gone. | Edit the anchor, or `anchormem retire <id> --reason "..."`. |
| `UNVERIFIED` | The anchor was written by hand and has no fingerprint yet. | `anchormem verify <id>`. |

Whitespace doesn't count as a change, so reformatting or re-indenting code won't flag anything.

## Who wrote it

Agents write most memories, and later agents act on them, so anchormem records each memory's `origin` (`user` or `agent`). Inside Claude Code, `add` defaults to `agent`. Elsewhere it defaults to `user`, and `--by` overrides either default.

An agent-written memory is labelled "written by an agent and not yet confirmed by a person" wherever an agent sees it, until a person runs:

```bash
anchormem confirm <id>
```

`confirm` shows the memory and asks for a yes. It refuses when nobody is at the terminal, so an agent can't confirm its own notes. (`--yes` skips the question, for scripts you trust.)

## Keeping memory clean

- **Retire instead of delete.** `anchormem retire <id> --reason "plan upgraded" [--superseded-by <id>]` moves a memory to `.anchormem/retired/` with the reason and date. `anchormem restore <id>` brings it back, and `anchormem list --retired` shows them. `rm` still deletes permanently.
- **Lint.** `anchormem lint` finds duplicate memories and memories about the same code that seem to contradict each other. It also finds empty explanations, file paths mentioned in a memory that don't exist, fragile line-range anchors, and agent-written memories containing risky instructions such as `curl … | sh` or "ignore previous instructions". It needs no LLM or network. `--strict` makes warnings fail the run.
- **Log.** Every add, verify, confirm, retire, restore and rm is appended to `.anchormem/log.md`, one line each, in a format that's easy to grep: `## [2026-11-10 14:03] verify | pool-size-why | …`. The file is marked `merge=union`, so branches that both add lines merge without conflicts.

## Commands

| Command | What it does |
|---|---|
| `anchormem init [--claude] [--hooks] [--dry-run] [--remove]` | Creates `.anchormem/` and writes the agent rules between marked lines in `AGENTS.md` (and `CLAUDE.md`). Running it again updates the rules in place, and `--remove` takes them out. |
| `anchormem add "<title>" -a <anchor> [-a …] [--body …] [--id …] [--by user\|agent]` | Saves a memory. Fails if an anchor doesn't point at real code. The body can also be piped in on stdin. |
| `anchormem check [ids…] [--json] [-q] [-v]` | Checks every memory against the code. Exits with 1 if anything is stale. |
| `anchormem context <paths…> [--all] [--budget N] [--json]` | Prints the memories relevant to those files or folders for an agent to read, stale ones first. |
| `anchormem verify <id…> [--follow]` | Marks memories as still true and records new fingerprints and the current commit. |
| `anchormem confirm <id…>` | Lets a person confirm memories an agent wrote. |
| `anchormem retire <id…> -r <reason> [--superseded-by <id>]` / `restore <id…>` | Retires memories with a reason, or brings them back. |
| `anchormem lint [--strict] [--json]` | Checks the memories themselves for problems. |
| `anchormem doctor` | Checks the setup: memory files parse, agent rules are current, hooks, PATH, gitignore, git. |
| `anchormem hooks install\|remove [--local] [--dry-run]` | Installs or removes the Claude Code hooks. |
| `anchormem list [--retired] [--json]` / `rm <id…>` | Lists or deletes memories. |

## The memory file

```markdown
---
id: pool-capped-10-postgres-plan
title: Pool capped at 10 because of the Postgres plan limit
origin: agent
created: 2026-11-10
verified: 2026-11-10
commit: 6ca7e9a
anchors:
  - src/db/pool.py#POOL_SIZE  # fp:9a525c65e0ef
---
Hosted plan allows 25 connections across 2 workers. Raise it only after upgrading.
```

The frontmatter is valid YAML, and each fingerprint is stored as a YAML comment. You can edit these files by hand. An anchor you add without a fingerprint shows as `UNVERIFIED` until you run `verify`.

```
.anchormem/
  memories/        active memories (commit these)
  retired/         retired memories, with reasons
  log.md           append-only change log
  .state/          per-machine hook state (gitignored)
```

## In CI

Copy [examples/github-action.yml](examples/github-action.yml) to `.github/workflows/`. A pull request that changes code without re-checking the memories about it will then fail. This repo does the same with its own memories: see [.anchormem/memories](.anchormem/memories).

To check before every commit instead, add this as `.git/hooks/pre-commit`:

```bash
#!/bin/sh
exec anchormem check -q
```

## Where the ideas came from

Version 0.2 borrowed from a study of other agent-memory projects:
- **Hook delivery with a size limit:** open-second-brain and ai-memory.
- **Instruction block between marked lines, `--remove`, `doctor`, and trust labels:** gbrain.
- **Retiring with a reason instead of deleting:** open-second-brain and ai-memory.
- **Rule-based lint and an append-only log:** Karpathy's LLM wiki, ai-memory and COG.

What anchormem adds is the anchor: memory tied to specific code, so it can tell when it's out of date.

## Development

```bash
python3 -m unittest discover -s tests
```

## License

MIT
