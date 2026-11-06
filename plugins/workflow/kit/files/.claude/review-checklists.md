# Review checklists

The reviewer (`.claude/agents/reviewer.md`) reads this file on every review. Each checklist
targets one kind of risk. Most bugs that reviews missed were orderings and lifecycles nobody
asked about: a late message from a replaced socket, a failed load that left the loop running, a
publish step that ran without the previous step's verdict. These lists make those questions
routine.

## How to use them

- Pick every checklist whose "Use when" the diff meets, usually one to three. A routine change
  (docs, copy, a rename, a pure refactor with tests unchanged) may need none: say so in one line.
- Answer for the whole flow the diff takes part in, not only the changed lines. Bugs that
  reviews miss usually sit in unchanged code the change sits beside or relies on: a sibling
  handler, the step before, a precondition written in a doc. (This widens the questions you ask,
  never the files you may plant breaks in.)
- Report one line per checklist: evidence for each item that needed real checking, `→ finding N`
  or `→ existing issue N` for an item that found a problem, and the rest grouped as
  `n/a: <items> (<one reason>)`. An item you could not settle goes under UNRESOLVED QUESTIONS.
- Then apply any review additions this repository keeps: project-specific risks such as supported
  browsers, shipped client versions or data that must stay sealed, in `CLAUDE.md` or a file it
  names (for example `docs/review-additions.md`, which keeps the always-loaded `CLAUDE.md` small).
  This file is managed by the workflow kit, so project lines never go here.

## 1. State and lifecycle

Use when the diff adds or changes something that records where a thing is in its life: a status
or mode, a flag such as `stopped`, `ready`, `loaded` or `dirty`, an owner or slot, a retry
counter, a timer or socket or worker handle, a cursor, or a stored record that moves through
stages.

1. List the states and every place that writes each one. Is there a state the code reaches but
   never names, or are there two flags that can disagree (for example "not stopped" while the
   core says "failed")?
2. For each outside event (a reply, a close, a timeout, a message, a click), what happens if it
   arrives twice, late (after its owner was replaced), out of order, or never?
3. If the work fails or crashes midway, what state is left? Does a retry or a restart recover
   from it, or does it skip or repeat work?
4. Does every handler that acts for an owner (a socket, worker, request or generation) check
   that the owner is still the current one? Search for every place that sets or uses that
   owner, including handlers the diff did not change, and compare them: if three check and one
   doesn't, the odd one out needs a reason, even when a comment gives one.
5. On teardown and on every failure path, is everything released: timers, workers, sockets,
   listeners, slots, tickets?
6. Can a failure turn into an apparent success, such as an empty result treated as fine or a
   failure counter reset by a reply that did nothing?
7. Retries: is there one timer per thing, is the backoff reset only by real success, and is
   there a state where it gives up and says so?
8. Do sequence numbers, versions and expiry times survive a reconnect, restart or restore the way
   their readers assume? Does restored state keep its original expiry, or restart it? Does any
   timer or ordering read the wall clock where a step backwards would break it?

## 2. Concurrency and multiple instances

Use when the diff touches anything shared between processes, replicas, tabs or workers (a bus
or cache such as Redis, a database row, a file or index more than one writer updates, a setting
read at startup), or async code where two tasks read and write the same state across an await.

1. If two copies run at once (two replicas, two tabs, two runs of a script), who wins, and is
   that intended? Can another writer get between a read and the write that depends on it,
   including a read from one store (a database) and a write to another (a cache)? Name each such
   window, even one accepted for now.
2. Is every promise, task or coroutine awaited, or deliberately left running with its errors
   handled and a reference kept?
3. A value loaded at startup: what if it changes before the load finishes?
4. What does a freshly started instance do with state another instance already stored?
5. Does the test or in-memory backend behave like the production one on these paths?

## 3. Publish and release pipelines

Use when the diff touches anything that builds, packages, uploads, publishes, flips a flag, moves
a pointer, writes a manifest or index, or changes a step of an operator procedure.

1. Can a step run without proof that the step before it passed (a verdict, a receipt, a hash)?
   Is that proof tied to exactly this artifact and version? Read the procedure's docs (playbook,
   runbook, README): each precondition they list for this step, such as "both gates say PASS",
   should be refused by code when it is missing, or the doc should say why it stays a manual
   check.
2. Running it twice, or again after a crash midway: is the result the same, with nothing lost or
   added twice? Check every write the step makes, including writes behind a condition such as
   "only when something was uploaded".
3. Does the dry run check everything the real run checks?
4. Does the documented way back work from the state a real failure leaves, and does it keep
   changes made after the release it restores?
5. Does it ship exactly what was approved: no extra or locally edited files, no unpinned inputs?
   If served or cached files change, does the version move, and does the old address keep
   working for as long as clients may still use it?

## 4. Security and privacy

Use when the diff touches sign-in, tokens, sessions, permissions or user data, adds a log line,
error message or request that carries user data, a token or a path, publishes something, or adds
third-party code.

1. Is every input from outside (a message, URL, file, request or stored value) validated
   before use, not cast?
2. Can one user see or change another user's data? Do sign-in and link flows move only forward
   where they should?
3. Can a secret, personal data, a private name or an internal path reach a log, an error, a
   query string, a Referer header, a status command's output or a published file?
4. Are new permissions, origins or network destinations needed, and as narrow as they can be?
5. Is copied or newly added third-party code under a compatible licence, from a pinned source?

## 5. Compatibility and migrations

Use when the diff changes a stored shape, a wire message, a config or manifest key, a command-line
flag or exit code, a public API, or a database schema.

1. Check both directions. New code must read old data, and old code must read what new code
   writes. That includes old instances during a rolling deploy and old client builds still in
   users' hands.
2. Is anything removed or renamed still used outside the diff: other packages, scripts, docs or
   other repositories?
3. Can a migration run twice, can it be undone, and what does a half-applied one leave?
4. Is a breaking change labelled as breaking where readers will see it (changelog, release
   notes, PR text)?
5. Does the diff bring back something a decision record or a code-header note lists as rejected
   or dropped? If so, the PR must say why it is safe now and update those records.

## 6. Test quality

Use with any of checklists 1 to 5, and for a diff that adds or changes tests.

1. Would each new test fail without the change? Plant breaks only when the brief allows it (see
   the reviewer file) and the diff changes an owner check, an ordering, a gate, or a stored or
   wire shape. Plant three to five (drop a guard, swap two steps, skip a cleanup) and report the
   ones no test caught. Otherwise answer by reading and write `not planted: <reason>`, listing
   the breaks you would plant.
2. Were tests deleted, skipped, loosened, or changed to match new output without a stated
   reason?
3. Does each fake reject or ignore what the real service rejects or ignores (a real service may
   answer "OK" to a request it ignored)? If not, is there at least one check against the real
   service?
