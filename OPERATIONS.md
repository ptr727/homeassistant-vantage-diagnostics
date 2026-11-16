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
.venv/bin/pytest              # the test suite with branch coverage
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

The integration lives in `custom_components/vantage_diagnostics/`.
It follows one loaded stock `vantage` entry and reuses its client for requests.
It owns a second event stream for the trigger and system log lines, which the stock client must not subscribe to.

- `__init__.py`: setup, the stock-entry watcher that reloads this entry, and the unload order.
- `config_flow.py`: picks the loaded stock entry to follow.
- `rpc.py`: runs every request on the stock config connection in a shielded task, so a cancel never leaves a reply unread.
- `names.py`: resolves object names, from the stock client or by request.
- `triggers.py`, `system_log.py`, `ordered.py`: fire the `vantage_trigger` and `vantage_system_log` events in arrival order.
- `module_status.py`, `binary_sensor.py`: read the enclosure buses and expose one connectivity sensor per module, on the stock module device.
- `diagnostic_services.py`, `module_status_service.py`: the response-only services.
- `logbook.py`: describes the system log event.
- `aiovantage_diag/`: protocol code vendored from loopj/aiovantage pull requests, always used instead of aiovantage's own.

Tests live in `tests/components/vantage_diagnostics/` and run against fakes of the stock client and the event stream.
