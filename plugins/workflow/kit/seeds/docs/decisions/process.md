# Decisions — Process & tooling

Full rationale for this theme's decisions, newest first. The lean, auto-loaded index is
`docs/decisions.md`.

---

## {{TODAY}} — Adopted the workflow kit: decisions record, review gates and the workflow check

**Status:** live, indexed

**Context.** <!-- fill: why this repository adopted a written workflow now. -->

**Decision.** The repository uses the workflow kit (github.com/ryangswe/claude-workflow): a
committed `.claude/` with agents, skills and a branch guard, this decisions record, and
`.claude/workflow/check.py` keeping them consistent. The kit's managed files are updated with
`workflow-kit sync`; `CLAUDE.md` and these docs are the repository's own.

**Rejected.** <!-- fill, or delete: alternatives considered. -->
