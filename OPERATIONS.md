# Operations

This file covers how this repository is run.
It lists the checks to run before pushing and the runbooks for tasks that recur.
It also holds the local conventions that sit on top of the carried fleet rules in [`GOVERNANCE.md`](./GOVERNANCE.md).

## Local Verification

Run these checks before pushing or opening a pull request.
The `scripts/lint` command covers ruff, `mypy --strict`, and pyright, which CI also gates.

```sh
scripts/setup                 # once: provision the uv-managed .venv
scripts/fix                   # apply ruff format and ruff check --fix
scripts/lint                  # verify: ruff format --check, ruff check, mypy --strict, pyright
.venv/bin/pytest              # the test suite with branch coverage, once tests exist
```

The `pre-commit` hook in [`.pre-commit-config.yaml`](./.pre-commit-config.yaml) runs the same four lint checks on every commit.
It also runs the diff-scoped prose gate and the whole-tree line-ending gate.
The `scripts/setup` command enables the hook in the base clone.

## Runbooks

Stub. No runbook has been written yet.

## Backup and Recovery

Stub. This repository holds no state outside git.

## Logs and Debugging

Stub. No debugging notes have been written yet.

## Tool Usage

Stub. No repository-specific tool notes have been written yet.

## Configuration Layout

Stub. The integration code under `custom_components/vantage_diagnostics/` does not exist yet.
