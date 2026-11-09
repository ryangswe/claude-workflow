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
  (`git checkout -b <slug>`). `.claude/hooks/guard-default-branch.py` blocks edits, `git commit`
  and pushes to it as a backstop, but does not judge other shell writes (`sed -i`, `>>`).
- Record decisions a later session could plausibly reopen (direction, architecture, contracts,
  security, rejected alternatives): one line in the auto-loaded index @docs/decisions.md and the
  full entry in its `docs/decisions/<theme>.md`. The index header sets the format and budget.
- **Size the task first, and say which lane in one line.** A *small change* (a few files, easy to
  revert, and no change to a shared contract, security or privacy posture, or product behaviour)
  takes the short lane: branch, change, `/checkpoint`, one `reviewer` pass, then a pull request
  whose description ends with a `Decision:` line if one was made. No plan, no decisions-record
  entry, no closeout. Anything bigger, or a small change that grows, takes the full process.
  A `Decision:` line is promoted to the decisions record only if a later session could reopen it.
- For large milestones, write the plan to `docs/plans/YYYY-MM-DD-<slug>.md` from
  `docs/plans/_template.md`, so a fresh session can execute it from the file alone. Keep its
  `Status:` line and the active list in `docs/plans/README.md` current.
- `CLAUDE.md` loads in every session and is capped at 14 KB by the workflow check. Always-on text
  is for a rule every session needs; add some only by cutting some. Procedures go in a skill, area
  rules in `.claude/rules/` with a `paths:` header.

## Orchestration and delegation

For non-trivial work the lead session is the orchestrator: it owns the goal, the architecture, the
task graph, integration and every exchange with the owner. Workers are the agents in
`.claude/agents/` (`planner`, `architect`, `executor`, `reviewer`, `Explore`); their report formats
live there, and the reviewer's checklists in `.claude/review-checklists.md`.

- **Milestone specs: do not edit files until the architecture and the first small change are
  explained and approved.** Inspect read-only first.
- Decompose work into tasks with dependencies, success criteria and verification. **Price the
  work in the owner's hours**: they are the scarcest resource.
- Work directly when a task is small, tightly coupled or cheaper to do than to brief. Delegate when
  work can run concurrently, is self-contained, would flood the lead's context, or needs a fresh
  judgment. Every worker gets a bounded task and ends with SUCCESS, ESCALATE or BLOCKED; on
  ESCALATE or BLOCKED, stop the tasks that depend on it and update the plan.
- Present the task graph, delegation and approval gates **before dispatching code-writing
  workers**, unless the owner has authorised autonomous work. Read-only workers need no approval.
  Ask before anything that changes product behaviour, architecture, compatibility, security or
  privacy posture, production data, scope or cost.

### Owner-only actions (each time, never implied by plan approval)

<!-- fill: for example publishing or deploying, store submissions, production data or migrations,
paid services and infrastructure. -->
- Merging into the default branch.

### Models

- **The lead runs on Opus at high effort**, chosen at the start of a session and not switched
  during it. Each model has its own prompt cache, so a switch re-reads the whole conversation
  uncached; ask `architect`, or start a session. Each agent's model and effort are set in its
  agent file; when a plan task names a stronger model, dispatch it on that one.
- **Keep the lead's context small.** The lead does not read a decisions theme file or a plan over
  ~30 KB itself; `Explore` fetches the entry or task it needs.

### Concurrency

- Before running workers at the same time, or beside another lead session, load the
  `parallel-work` skill.
- Every concurrent code-writing worker gets its own worktree. Stage explicit paths; never
  `git add .` or a whole directory.
<!-- fill: this repository's single-instance resources (ports, local databases, GPUs, browser
profiles) and what a new worktree lacks, or delete this comment if none. -->

### Review, integration and closeout

- Run `/checkpoint` (the `checkpoint` skill) after each integrated step.
- **Two strikes:** when the same test, typecheck or build error survives two fix attempts, stop
  patching. Ask `reviewer` (or `architect` when the cause is unclear) for a root-cause read before
  a third attempt; a worker reports ESCALATE instead.
- Before declaring non-trivial work done, run `reviewer` with fresh context on the plan and the
  diff. Passing tests are not proof. Fix the material findings, re-run the checks.
- Ending a session mid-task, switching topic, or past ~300–400k tokens: run `/handoff` (the
  `handoff` skill). It records state in the repository and prints a short prompt for the next
  session. Don't let a conversation be the only place work's state lives.
- Weekly, at a handoff, or when `.claude/worktrees/` piles up: run `/gc` (the `gc` skill) to remove
  worktrees and branches whose work is already in the default branch.
- **Close out:** the plan's `Status:` line and the active list; the execution log compressed into
  the completion summary; decision lines that meet the index bar; a short summary for the owner.
- **Bugs already on the default branch:** the fix's PR description carries one `Missed by:` line
  per bug, naming the check that should have caught it, and the fix adds that check (review
  checklist 7).

<!-- Workflow kit (full). `workflow-kit sync` updates the managed files under .claude/; this file
is the repository's own. -->
