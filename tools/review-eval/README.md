# review-eval

Does the reviewer flag a bug that later had to be fixed? Each case is a PR that introduced a bug a
later commit fixed. A run rebuilds the repository as it stood when that PR was open, asks the
reviewer under test to review the PR, and checks whether a Critical or Major finding points at the
bug's lines. Controls, PRs that nothing later fixed, measure how much a reviewer flags when there
is nothing known to find.

Standard-library Python 3.9+. Runs happen on the machine with the target checkout and the
reviewers' logins (`claude`, and `codex` for config C).

## Files

- `review_eval.py`: the tool (`anchors`, `controls`, `snapshot`, `probe`, `run`, `report`).
- `cases/example.jsonl`: the case format. **Real cases live with the repository they describe**,
  for example `.claude/eval/review-cases.jsonl` in that repository, and are passed with `--cases`.
  This repository is public; a private repository's commit ids, paths and bug descriptions never
  belong here.
- `configs/*.json`: one reviewer setup each: the files installed into the snapshot, the model and
  effort, and the command. `A-opus` is the baseline.
- `runs/` (gitignored, under the folder you run from): `run-N.json` per finished run, with the
  reviewer's full text, parsed findings, cost, time and the models used; `error-N.json` for a run
  that failed for reasons outside the review (see below).

## How a run stays honest

- **No future history.** The snapshot is a fresh repository with exactly two commits, the PR's
  base and the PR, built from `git archive`, with no remote, no other refs, an empty reflog and no
  unreachable objects. `git log` cannot show the fix. The tests check this.
- **The reviewer has no shell.** The snapshot holds only the PR's two commits, so git could add
  nothing but the diff and the files before the change, and a shell (git included) can read
  outside the snapshot in more ways than any allow list closes. Instead each run writes
  `.review/pr.diff`, `.review/commit.txt` and `.review/base/<path>` (the version before the change
  of each modified or deleted file) into the snapshot, untracked, and the reviewer gets Read, Glob
  and Grep only. `--restricted` confines those to the snapshot folder and ignores the settings
  files of the user and of the snapshot, and `--strict-mcp-config` loads no MCP servers. The
  reviewer cannot run tests, install anything or reach the network.
- **Symlinks cannot lead out.** Before the reviewer starts, every symlink in the working tree that
  resolves outside the snapshot is replaced by a text file naming its target (recorded in the run
  as `symlinks_replaced`).
- **Isolation is checked per config, not assumed.** `probe` asks a real run to reach a secret file
  outside the snapshot: Read on it, Glob over its folder, and Grep in it, where a second secret
  exists only as a file name, so a listing or a Grep in any output mode shows it, and a shell. The
  probe runs with `stream-json` and searches every tool result, not just what the model says. It
  passes only if the run finished, used its tools, reported its attempts, and neither secret
  appears anywhere.
  `run` refuses a config until its probe has passed, unless `--skip-probe`. A pass is tied to the
  config, its installed files, this tool's code and the `claude` version, so changing any of them
  needs a new probe. Only claude configs can be probed; Codex's isolation stays unproven.
- **Same reviewer for every config except what the config changes.** The config's `reviewer.md`
  and `review-checklists.md` are written into both commits, so they never appear in the diff under
  review, and the reviewer is passed with `--agents` and `--agent reviewer`, with `--model` and
  `--effort` from the config. The repository's CLAUDE.md and docs of that day stay as they were.
  Each run records `model_usage`, so the report can be checked against the model meant to run.
- **Anchors are checked by a person.** `run` refuses a case whose anchors are not all marked
  `"checked": true`, unless `--allow-unchecked` (whose results are not comparable).
- **Known answers, then a person's check.** A run catches a case when a Critical or Major finding
  names an anchor's file within 10 lines of it. A Minor match is "seen". A finished run with no
  parseable findings list is a miss, reported as unparsed. A timeout or a budget stop is the
  reviewer's own outcome and counts. A line match says where a finding points, not what it says: a
  finding about something else can land near an anchor, and one that names the bug can point where
  the fix went. So `matches` lists, for each graded run, the finding that decided it and the run's
  other severe findings, and a person records a verdict per run in a verdicts file kept beside the
  cases. `report --verdicts` uses a verdict in place of the line grade, for the run it was made on
  only (a redone run needs a new one), and says how many runs are still graded by line alone.
- **Failures are not misses.** A run whose command failed (a usage limit, a crash, a login
  problem) is written as `error-N.json`, ignored by the report and retried by the next `run`.
  Three in a row stop the run.

## Running

From this folder, on the machine with the checkout:

```sh
# Isolation probe, before anything else: a real run asked to read a secret outside the snapshot.
# It must print PROBE PASSED, which unlocks `run` for this config. Costs one short run.
python3 review_eval.py probe --repo <checkout> --cases <cases.jsonl> --case 1 \
  --config configs/A-opus.json

# Smoke test: one case, one run. Check runs/<config>/<case>/run-1.json: the reviewer's report,
# its findings list, the cost, and that model_usage names the model you meant.
python3 review_eval.py run --repo <checkout> --cases <cases.jsonl> \
  --config configs/A-opus.json --case 1 --runs 1

# A set of cases and the controls, 3 runs each. Resumable: re-running skips finished runs.
python3 review_eval.py run --repo <checkout> --cases <cases.jsonl> \
  --config configs/A-opus.json --set pilot --set control --runs 3

# What each case run was graded on, with a verdict line per run to copy into the verdicts file
# (for example .claude/eval/review-verdicts.jsonl beside the cases) and correct where the line
# grade is wrong.
python3 review_eval.py matches --cases <cases.jsonl> --config A-opus

python3 review_eval.py report --cases <cases.jsonl> --verdicts <verdicts.jsonl>
```

`max_budget_usd` in a config caps each Claude run and `timeout_s` its wall time. When `claude` is
logged in to a plan rather than an API key, runs count against the plan's limits, and the
reported cost is what the API would have charged.

`configs/C-codex.json` was written without `codex` installed: check its flags against
`codex exec --help`, then run `probe` with it. Codex's read-only sandbox may still read files
outside the snapshot; if the probe fails, Codex results can't be compared.

The kit's reviewer asks for read-only git commands. Here it has none and reads `.review/`
instead; the brief says so. Every config gets the same brief, so comparisons stay fair.

## Writing cases

```sh
# Draft anchors: the lines a later fix changed that the PR wrote, mapped back by line number to
# the PR's merge commit. Tests and docs are skipped unless --include-tests. Always check the result
# against the PR, then set "checked": true.
python3 review_eval.py anchors --repo <checkout> --intro <merge> --fix <fix commit>

# Pick controls: merged PRs over 30 days old with 100-1800 changed code lines, not a case and not
# named in a later fix, revert or regression commit (an approximation: a fix that names no PR is
# missed).
python3 review_eval.py controls --repo <checkout> --cases <cases.jsonl> --count 10

# Build one snapshot to look at, in a new folder, with the .review/ files the reviewer gets.
python3 review_eval.py snapshot --repo <checkout> --cases <cases.jsonl> --case 1 \
  --config configs/A-opus.json --out /tmp/snap-1
```

Each time a bug reaches the default branch, the review checklist's "Missed by" line records which
check should have caught it. Add those as cases: twenty-odd cases only show large differences.
