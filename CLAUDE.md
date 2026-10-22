# claude-workflow

The workflow kit: files copied into other repositories (`plugins/workflow/kit/`), the script that
copies and updates them (`plugins/workflow/bin/workflow-kit`), and the plugin skills that drive it
(`plugins/workflow/skills/`). `README.md` explains the design; read it before changing behaviour.

## Commands

- Tests: `python3 -m unittest discover -s tests`
- Manifests: `claude plugin validate .` and `claude plugin validate ./plugins/workflow`

## Rules

- Branch first; never change files on `main`.
- Everything is standard-library Python 3.9+ and plain Markdown: target repositories may have no
  Node, no pip installs and no network.
- `kit/files/` is **managed** (synced into repositories), `kit/seeds/` is **seeded** (written once).
  Every file is listed in `kit/manifest.json` with its levels and mode; a test should cover any new
  behaviour of `workflow-kit` or `check.py`.
- Kit files must stay generic: anything specific to one project belongs in that project's
  `CLAUDE.md`. RiftSight (ryangswe/RiftSight) is where the workflow is tried first; carry back
  improvements that proved general.
- A change to a managed file reaches every repository on its next `sync`: say in the commit message
  what changes for them.
