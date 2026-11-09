# <Milestone / effort title>

- **Date:** YYYY-MM-DD
- **Status:** Draft | Approved | In progress | Blocked | Done | Closed | Superseded (by …) | Shelved
- **Branch:** `<branch>`
- **Source:** <optional: link or one-line pointer to the request>
- **Owner time, total:** <optional: hours for reviews, live checks, labelling, decisions>

## Goal

What this effort delivers, in 2–4 sentences. State the scope boundary: what it deliberately does
**not** build yet, and why.

## Approach

The architecture in a few sentences: what changes, where the seams are, and any shared contract
whose sides must change together.

- **Rejected:** <optional: an approach considered and why it lost>

Promote decisions worth keeping beyond this effort to `docs/decisions.md`.

## Risks and open questions

- **<Risk or question>**: <impact and proposed resolution>

Resolve questions that need the owner before the status moves from Draft to Approved.

## Tasks

Independently verifiable units, in order. Separate tasks do not imply separate agents.

### T1 — <task title>

- **Status:** Pending | In progress | Complete | Escalated | Blocked
- **Depends on:** None | T<n>, ...
- **Objective:** <bounded description of the work>
- **Success criteria:**
  - <observable or testable result>
- **Verification:**
  - `<narrow command or manual check>`

<!-- Copy the task block as needed. When tasks will run at the same time, add the concurrency
table from the parallel-work skill. Delete unused examples before approval. -->

The execution log lives beside this file, in `<this plan's name>-log.md` (see the plans README).

## Completion summary

- **Delivered:** <concise outcome and PRs>
- **Deviations from the approved plan:** None | <summary>
- **Follow-ups:** None | <summary>
- **Review:** <reviewer verdict and where its findings are>
- **Missed by:** n/a | <one line per bug this plan fixed that was already on the default branch:
  the check that should have caught it, and the test, rule or review line this plan added; copy
  each line into the PR description>
