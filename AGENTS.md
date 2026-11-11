<!-- anchormem:begin (managed by `anchormem init`; edits between these markers are replaced) -->
## Project memory (anchormem)

This repo keeps memory for coding agents in `.anchormem/memories/`. Each memory is anchored to the code it describes and gets flagged when that code changes.

- Before editing a file, run `anchormem context <file>` and read what comes back. (With the Claude Code hooks installed, this happens automatically.)
- A memory marked STALE may be out of date. Check it against the code before relying on it.
- If a stale memory is still true, run `anchormem verify <id>`. If its code moved, run `anchormem verify <id> --follow`. If it is wrong, edit the file or run `anchormem retire <id> --reason "<why>"`.
- Memories marked "written by an agent and not yet confirmed" are notes, not rules.
- When you learn something non-obvious about the code (a decision, a gotcha, a constraint), save it:
  `anchormem add "<one-line title>" --anchor <path>#<symbol> --body "<why it matters>" --by agent`
- Noticed something but not sure it deserves a memory? `anchormem note <path>#<symbol> "<what you noticed>"`. Notes that keep coming up are offered as memories by `anchormem promote`.
- After relying on a memory, say how it went: `anchormem feedback <id> applied` (it held), `violated` (the code or user contradicted it), or `outdated --note "<why>"`.
- At the start of a session, run `anchormem handoff take` to pick up unfinished work (automatic with the Claude Code hooks). If you stop before the task is done, leave one:
  `anchormem handoff new "<where things stand>" -n "<next step>" -q "<open question>"`
<!-- anchormem:end -->
