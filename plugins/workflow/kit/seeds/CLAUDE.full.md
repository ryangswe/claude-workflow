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
- Record decisions a later session could plausibly reopen (direction, architecture, contracts,
  security, rejected alternatives): one line in the auto-loaded index @docs/decisions.md and the
  full entry in its `docs/decisions/<theme>.md`. The index header sets the format and budget.
- For large milestones, write the plan to `docs/plans/YYYY-MM-DD-<slug>.md` from
  `docs/plans/_template.md`, so a fresh session can execute it from the file alone. Keep its
  `Status:` line and the active list in `docs/plans/README.md` current.
- `CLAUDE.md` loads in every session and is capped at 14 KB by the workflow check. Always-on text
  is for a rule every session needs; add some only by cutting some. Procedures go in a skill, area
  rules in `.claude/rules/` with a `paths:` header.

## Orchestration and delegation

For non-trivial work the lead session is the orchestrator: it owns the goal, the architecture, the
task graph, integration and every exchange with the owner. Workers are the agents in
`.claude/agents/` (`planner`, `architect`, `executor`, `reviewer`, `Explore`); their checklists
and report formats live there.

- **Milestone specs: do not edit files until the architecture and the first small change are
  explained and approved.** Inspect read-only first.
- Decompose work into tasks with dependencies, complexity class, success criteria, escalation
  conditions and verification. **Price every task in the owner's hours**: they are the scarcest
  resource.
- Work directly when a task is small, tightly coupled or cheaper to do than to brief. Delegate when
  work can run concurrently, is self-contained, would flood the lead's context, or needs a fresh
  judgment. Every worker gets a bounded task and ends with SUCCESS, ESCALATE or BLOCKED; on
  ESCALATE or BLOCKED, stop the tasks that depend on it and update the plan.
- Present the task graph, delegation, model classes and approval gates **before dispatching
  code-writing workers**, unless the owner has authorised autonomous work. Read-only workers need
  no approval. Ask before anything that changes product behaviour, architecture, compatibility,
  security or privacy posture, production data, scope or cost.

### Owner-only actions (each time, never implied by plan approval)

<!-- fill: for example publishing or deploying, store submissions, production data or migrations,
paid services and infrastructure. -->
- Merging into the default branch.

### Model routing

**The lead runs on Opus at high effort**, chosen at the start of a session. Plans name capability
classes; map them at dispatch: ROUTINE → Sonnet (Haiku for lookups), STRONG → Opus, FRONTIER →
Fable. Update the mapping when models change.

| Agent | Default | Use |
|---|---|---|
| `Explore` | Haiku, low | code search; fetching one decision entry, plan task or doc section |
| `executor` | Sonnet, medium | ROUTINE tasks; override to Opus for STRONG |
| `reviewer` | Opus, high | every review; Sonnet only for ROUTINE-only work |
| `planner` | Fable, high | milestone plans, re-plans after an ESCALATE |
| `architect` | Fable, high | judgement calls mid-task; resume it with `SendMessage` for follow-ups |

- Call FRONTIER where it pays: a milestone plan, a rule or bar set before measuring, a re-plan, a
  result the lead cannot explain, a root cause after two strikes.
- **Keep the lead's context small.** The lead does not read a decisions theme file or a plan over
  ~30 KB itself; `Explore` fetches the entry or task it needs.
- **Don't switch the lead's model or effort mid-session**: each model has its own prompt cache, so
  the next turn re-reads the whole conversation uncached. Ask `architect`, or start a session.
- Never set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`: it ignores the agent files' `model`.

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
- **Close out:** the plan's `Status:` line and the active list; the execution log compressed into
  the completion summary with its lesson or `NO_ENTRY`; decision lines that meet the index bar; a
  short summary for the owner.

<!-- Workflow kit (full). `workflow-kit sync` updates the managed files under .claude/; this file
is the repository's own. -->
