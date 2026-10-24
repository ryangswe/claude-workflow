---
name: init
description: Set up the workflow kit in the current repository (light or full level), or move a repository that has it up to the full level. Use when the owner asks to add or set up the workflow, agents, decisions record or review gates in a repo.
---

# Set up the workflow kit

The kit's files are copied into the repository and committed, so they work in local, cloud and CI
sessions alike. `workflow-kit` (on PATH while this plugin is enabled) does the copying; you do the
parts that need judgement: choosing the level and filling in `CLAUDE.md`.

1. **Check the repository.** Confirm you are at its root and on a feature branch, not
   `main`/`master` (create one: `git checkout -b workflow-kit`). If `.claude/workflow.lock.json`
   exists, the kit is already here: run `workflow-kit status` and switch to the upgrade steps
   below if the owner wants the full level.

2. **Choose the level with the owner.** Recommend one:
   - **light**: `CLAUDE.md`, the branch guard, the `reviewer` and `Explore` agents, `/checkpoint`,
     `/handoff`, the decisions record and the workflow check. For small or rarely changed projects.
   - **full**: adds the `planner`, `architect` and `executor` agents, the `parallel-work` skill,
     plans with their template, and the orchestration rules. For multi-session programmes.

   **Public repository?** Everything this writes gets committed and published. Ask the owner
   whether internal process notes may be public; if not, stop and say so.

3. **Copy the kit:** `workflow-kit init --level <light|full>`. Report any `CONFLICT` line (an
   existing file the kit would replace) to the owner; do not re-run with `--force` without their say.
   If the repository already had a `CLAUDE.md`, init kept it: merge the kit's sections into it
   (the skeletons are in the plugin's `kit/seeds/`), keeping what was there.

4. **Fill in `CLAUDE.md`.** Replace every `<!-- fill: ... -->` from what the repository shows:
   its README, manifest files (package.json, pyproject.toml, Cargo.toml…), CI config and layout.
   Ask the owner only for what the code cannot tell you: the owner-only actions, any
   single-instance resources. Keep it short; the check caps it at 14 KB. Fill the context line of
   `docs/decisions/process.md` the same way.

5. **Wire the check into CI or the test suite** if the repository has either, so
   `python3 .claude/workflow/check.py` runs on every change. Show the owner the change.

6. **Verify:** `python3 .claude/workflow/check.py` passes; stage the new files by explicit path and
   commit (`chore: set up the workflow kit (<level>)`). Do not push unless the owner asks.

## Moving up to full

`workflow-kit sync --level full` shows what it would add; `--apply` writes the new agents, the
`parallel-work` skill and the plans docs. It never replaces `CLAUDE.md`, so bring in from
`kit/seeds/CLAUDE.full.md` by hand:

- the plans bullet under "Working agreements" (`docs/plans/...`);
- the whole "Orchestration and delegation" section with its subsections. Its "Owner-only actions"
  subsection replaces the light file's top-level "Owner-only actions" section: move that section's
  items into it rather than keeping both;
- the light file's "Review and verification" section is replaced by "Review, integration and
  closeout".

Fill the new comments, run `python3 .claude/workflow/check.py` (it fails until `parallel-work` is
named) and commit.
