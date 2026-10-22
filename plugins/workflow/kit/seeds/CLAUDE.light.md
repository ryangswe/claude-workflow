# <Project name>

<!-- fill: two or three sentences — what this is, who it is for, and what exists today. -->

## Layout

<!-- fill: the top-level directories or packages, one line each. Name any shared contract
(types, schemas, wire formats) whose callers must change together. -->

## Commands (run from the repository root)

<!-- fill: the exact commands to install, typecheck/lint, test, build and run. /checkpoint reads
this section. -->
- Workflow check: `python3 .claude/workflow/check.py`

## Working agreements

- **Never change files on the default branch** (`main`/`master`). Branch first
  (`git checkout -b <slug>`). `.claude/hooks/guard-default-branch.py` blocks Edit/Write there as
  a backstop, but cannot see shell writes (`sed -i`, `>>`).
- Stage explicit paths; never `git add .` or a whole directory.
- Record decisions a later session could plausibly reopen (direction, architecture, contracts,
  security, rejected alternatives): one line in the auto-loaded index @docs/decisions.md and the
  full entry in its `docs/decisions/<theme>.md`. The index header sets the format and budget.
- `CLAUDE.md` loads in every session and is capped at 14 KB by the workflow check. Always-on text
  is for a rule every session needs; add some only by cutting some. Procedures go in a skill, area
  rules in `.claude/rules/` with a `paths:` header.

## Owner-only actions

<!-- fill: what an agent must never do without the owner's say-so each time — for example merging
to the default branch, publishing or deploying, production data, paid services. Remove this
section only if there is truly nothing. -->
- Merging into the default branch.

## Review and verification

- Run `/checkpoint` (the `checkpoint` skill) after each step and before asking for a merge.
- **Two strikes:** when the same test, typecheck or build error survives two fix attempts, stop
  patching and ask the `reviewer` agent for a root-cause read before a third attempt.
- Before declaring non-trivial work done, run the `reviewer` agent with fresh context on the task
  and the diff. Passing tests are not proof. Fix the material findings, re-run the checks.
- For lookups, use the `Explore` agent (cheap, read-only) rather than reading large files yourself.

<!-- Workflow kit (light). `workflow-kit sync` updates the managed files under .claude/; this file
is the repository's own. -->
