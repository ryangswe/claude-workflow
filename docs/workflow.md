# How the workflow fits together

The [README](../README.md#how-it-works) shows the agent setup at each level. This page shows where
each kind of knowledge lives, and how work carries from one session to the next.

## 1. Where a session's knowledge lives

Every fact is written once, in the layer that matches how long it stays true. Months → a
decision. Until the task ends → the plan. Only for the next session → the handoff, which points at
the plan rather than repeating it.

```mermaid
%%{init: {"flowchart": {"curve": "step", "nodeSpacing": 40, "rankSpacing": 50, "padding": 14, "wrappingWidth": 260}}}%%
flowchart TB
  chat(["<b>The conversation</b><br/><i>gone when the session ends</i>"]):::you
  index["<b>docs/decisions.md</b><br/>one line per live decision<br/><i>always loaded · months</i>"]:::lead
  plan["<b>docs/plans/…md</b><br/>tasks, status, next step, log<br/><i>until the work ends</i>"]:::check
  handoff["<b>Handoff prompt</b><br/>at most 10 lines<br/><i>one session</i>"]:::file
  theme["<b>docs/decisions/theme.md</b><br/>the why, rejected options<br/><i>read on demand</i>"]:::agent
  claude["<b>CLAUDE.md</b><br/>rules for this repo · 14 KB cap<br/><i>always loaded</i>"]:::lead
  rules["<b>skills · agents · rules</b><br/>procedures, area rules<br/><i>read on demand</i>"]:::agent

  chat -- "a decision a later<br/>session could reopen" --> index
  chat -- "progress,<br/>next step" --> plan
  chat -- "at the end" --> handoff
  handoff -. "points to" .-> plan
  index -.-> theme
  claude -.-> rules

  classDef you fill:#fff4d6,stroke:#b7791f,stroke-width:2px,color:#5c3d00
  classDef lead fill:#e6ecfb,stroke:#2f5bd3,stroke-width:2px,color:#16244d
  classDef agent fill:#efe6fb,stroke:#7c4dcc,stroke-width:2px,color:#36195f
  classDef check fill:#e3f4e8,stroke:#2e8b57,stroke-width:2px,color:#123f25
  classDef file fill:#f3f4f6,stroke:#6b7280,stroke-width:2px,color:#1f2937
  classDef muted fill:#ffffff,stroke:#9ca3af,stroke-width:1px,stroke-dasharray:4 3,color:#4b5563
```

| Layer | Holds | Lasts | Must not hold |
|---|---|---|---|
| Decisions | Settled choices and why; rejected alternatives | Months, until superseded | Progress, to-dos, today's state |
| Plan | Tasks, status, the next step, the log | Until the work closes | Decisions that outlive it: promote those |
| Handoff | Where to resume, what's uncommitted, which skills to load | One session | Anything already in the plan or a decision |

The layers only clash through copies: a handoff that restates the plan, or a `CLAUDE.md` that
repeats a decision, drifts from its source. Point at the source instead. `check.py` guards the
decisions record and the plans list.

## 2. One session, start to finish

A handoff is a habit at a boundary, not a token alarm. Most happen because the task finished or
the topic changed; the token figure is a backstop for long sessions. If nothing is mid-flight, no
handoff is needed: the plan already says where things stand.

```mermaid
%%{init: {"flowchart": {"curve": "step", "nodeSpacing": 40, "rankSpacing": 50, "padding": 14, "wrappingWidth": 260}}}%%
flowchart TB
  start(["<b>Start</b><br/>“Continue docs/plans/X.md”"]):::you
  work["<b>Work</b>"]:::lead
  done["<b>Step done</b><br/>tick the plan · commit"]:::check
  stop{"Stop here?"}:::lead
  handoff["<b>/handoff</b><br/>plan updated · pushed · prompt printed"]:::check
  next(["<b>New session</b><br/>paste the prompt, pick up from the plan"]):::you

  start --> work --> done --> stop
  stop -- "no" --> work
  stop -- "task done, topic changes,<br/>~300–400k tokens, end of day" --> handoff
  handoff --> next

  classDef you fill:#fff4d6,stroke:#b7791f,stroke-width:2px,color:#5c3d00
  classDef lead fill:#e6ecfb,stroke:#2f5bd3,stroke-width:2px,color:#16244d
  classDef agent fill:#efe6fb,stroke:#7c4dcc,stroke-width:2px,color:#36195f
  classDef check fill:#e3f4e8,stroke:#2e8b57,stroke-width:2px,color:#123f25
  classDef file fill:#f3f4f6,stroke:#6b7280,stroke-width:2px,color:#1f2937
  classDef muted fill:#ffffff,stroke:#9ca3af,stroke-width:1px,stroke-dasharray:4 3,color:#4b5563
```

## 3. A session that runs unattended

An overnight run needs no handoff, because nobody switches sessions. What protects it is
committing after every task and keeping the plan current, so a compaction or a restarted sandbox
resumes from files, not memory. The plan's stop points stand in for you: spending, publishing and
merging still wait.

```mermaid
%%{init: {"flowchart": {"curve": "step", "nodeSpacing": 40, "rankSpacing": 50, "padding": 14, "wrappingWidth": 260}}}%%
flowchart TB
  approve(["<b>Before you leave</b><br/>approve the plan, stop points in it"]):::you
  pick["<b>Pick the next task</b>"]:::lead
  doit["<b>Do it · verify</b>"]:::lead
  commit["<b>Commit · tick the plan</b><br/>one log line"]:::check
  gate{"Stop point<br/>or blocked?"}:::lead
  wait(["<b>Stop and wait for you</b>"]):::you
  resume["<b>Resume from the plan</b><br/>and the commits"]:::file
  later(["<b>Later</b><br/>read the plan's log and the diff"]):::you

  approve --> pick --> doit --> commit --> gate
  gate -- "no" --> pick
  gate -- "yes" --> wait --> later
  commit -. "context fills, or<br/>the sandbox restarts" .-> resume
  resume --> pick

  classDef you fill:#fff4d6,stroke:#b7791f,stroke-width:2px,color:#5c3d00
  classDef lead fill:#e6ecfb,stroke:#2f5bd3,stroke-width:2px,color:#16244d
  classDef agent fill:#efe6fb,stroke:#7c4dcc,stroke-width:2px,color:#36195f
  classDef check fill:#e3f4e8,stroke:#2e8b57,stroke-width:2px,color:#123f25
  classDef file fill:#f3f4f6,stroke:#6b7280,stroke-width:2px,color:#1f2937
  classDef muted fill:#ffffff,stroke:#9ca3af,stroke-width:1px,stroke-dasharray:4 3,color:#4b5563
```

## 4. Why the kit is committed, not just installed

```mermaid
%%{init: {"flowchart": {"curve": "step", "nodeSpacing": 40, "rankSpacing": 50, "padding": 14, "wrappingWidth": 260}}}%%
flowchart TB
  kit["<b>claude-workflow kit</b>"]:::file
  repo["<b>Your repository</b><br/>CLAUDE.md · .claude/ · docs/<br/>all committed"]:::check
  local["Local sessions"]:::lead
  cloud["Cloud sessions"]:::lead
  ci["CI · check.py"]:::lead
  people["Collaborators"]:::lead
  plugin["Plugin alone<br/>not committed"]:::muted

  kit -- "workflow-kit init / sync" --> repo
  repo --> local & cloud & ci & people
  plugin -. "loads only where installed" .-> local

  classDef you fill:#fff4d6,stroke:#b7791f,stroke-width:2px,color:#5c3d00
  classDef lead fill:#e6ecfb,stroke:#2f5bd3,stroke-width:2px,color:#16244d
  classDef agent fill:#efe6fb,stroke:#7c4dcc,stroke-width:2px,color:#36195f
  classDef check fill:#e3f4e8,stroke:#2e8b57,stroke-width:2px,color:#123f25
  classDef file fill:#f3f4f6,stroke:#6b7280,stroke-width:2px,color:#1f2937
  classDef muted fill:#ffffff,stroke:#9ca3af,stroke-width:1px,stroke-dasharray:4 3,color:#4b5563
```

`sync` updates the **managed** files (agents, skills, the hook, the check, the plan template) and
never overwrites a local edit. **Seeded** files (`CLAUDE.md`, the decisions docs, the plans README,
`.claude/workflow.json`) are written once and then belong to the repository.
