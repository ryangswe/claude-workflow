---
name: sync
description: Update the workflow kit's managed files (agents, skills, branch guard, workflow check, plan template) in the current repository to the kit's latest version, without overwriting local edits. Use when the owner asks to update or sync the workflow kit, or to see whether it is current.
---

# Update the workflow kit

1. On a feature branch at the repository root, run `workflow-kit status`. Each managed file is one
   of: `current`, `kit updated` (safe to take), `edited here` (kept as is), `both changed` or
   `untracked` (needs a merge), `missing` / `new in kit` (will be written).

2. Run `workflow-kit sync` (a dry run) and show the owner what would change. For every `REVIEW`
   file it prints a diff between the repository's copy and the kit's: merge the kit's
   improvements into the repository's copy by hand, keeping the local changes that were
   deliberate, and explain each merge to the owner. Then `workflow-kit resolve <path>` records
   that the repository has taken the kit's current version into account; until the kit changes
   that file again it reads `edited here`.

3. Run `workflow-kit sync --apply`. It writes the safe updates and records the new versions in
   `.claude/workflow.lock.json`. Files edited here stay untouched. It exits non-zero while any file
   still needs review.

   A managed file the repository does not want (say, the `executor` agent): delete it and run
   `workflow-kit exclude <path>`, or sync will restore it. An `orphan` is a file the kit dropped:
   delete it if unused, then `exclude` it.

4. Run `python3 .claude/workflow/check.py`. A **new skill** from the kit fails it until
   `CLAUDE.md` names it (an unnamed skill is never opened): add the line the kit's `CLAUDE.md`
   skeleton for this level uses for it, and show the owner. Then commit the changed files by
   explicit path (`chore: sync the workflow kit`). Do not push unless the owner asks.

`CLAUDE.md`, the decisions record and `.claude/workflow.json` are the repository's own: sync never
changes them. If the kit's skeletons gained a rule worth having, propose it to the owner as a
`CLAUDE.md` edit.
