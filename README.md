# homeassistant-vantage-diagnostics

Home Assistant integration adding controller diagnostics to the Vantage InFusion integration.

**This integration requires the [Vantage InFusion][vantage-integration-link] integration by loopj to be installed and set up first.**
It adds diagnostics alongside that integration and does not replace it.
It reuses the stock integration's configuration and opens one extra connection of its own for the trigger and system log events.
It installs through the Home Assistant Marketplace as a custom repository.

## Build and Distribution

- **Source Code**: [GitHub][github-link] for source, issues, discussions, and CI/CD pipelines.
- **Versioned Releases**: [GitHub Releases][releases-link] for version-tagged source archives and the `vantage_diagnostics.zip` integration archive that the Marketplace installs.

### Build Status

[![Build Status][buildstatus-shield]][actions-link]\
[![Last Commit][lastcommit-shield]][commits-link]\
[![Coverage][coverage-shield]][coverage-link]

### Releases

[![GitHub Release][releaseversion-shield]][releases-link]\
[![GitHub Pre-Release][prereleaseversion-shield]][releases-link]

### Release Notes

**Version 0.1**:

- `vantage_trigger` events for each task a controller object starts, and `vantage_system_log` events for notable system log entries.
- Logbook entries for both events, with a trigger also shown on its source's stock entity where it has one.
- Module connectivity, hardware model, hardware revision, firmware version and bus error sensors, attached to the stock module devices.
- Event stream and host sensors on the controller device.
- The `get_trigger_history`, `get_system_log` and `get_module_status` response-only services.

See [Release History][history] for complete release notes and older versions.

## Table of Contents

- [homeassistant-vantage-diagnostics](#homeassistant-vantage-diagnostics)
  - [Build and Distribution](#build-and-distribution)
    - [Build Status](#build-status)
    - [Releases](#releases)
    - [Release Notes](#release-notes)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Events](#events)
  - [Entities](#entities)
  - [Services](#services)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Questions or Issues](#questions-or-issues)
  - [Development Environment Setup](#development-environment-setup)
  - [Contributing](#contributing)
  - [3rd Party Tools](#3rd-party-tools)
  - [License](#license)

## Overview

This integration adds controller diagnostics to a Home Assistant instance that already runs the stock Vantage InFusion integration.
The stock integration must be installed and loaded first, and the setup flow aborts until it is.
This integration has no connection settings of its own and takes the host and credentials from the stock integration.
It declares no `requirements`, so it runs on the `aiovantage` version the stock integration installs.
The diagnostic protocol code that `aiovantage` does not release yet is vendored in `custom_components/vantage_diagnostics/aiovantage_diag/`.
If the stock integration is reloaded, this integration reloads with it.

It adds the following, each described in its own section below:

- **Events**: `vantage_trigger` and `vantage_system_log`, fired on the Home Assistant event bus.
- **Entities**: diagnostic sensors on the stock integration's module and controller devices.
- **Services**: three response-only services that read the controller's trigger history, system log and module status.

## Events

Both events arrive on this integration's own event stream, which needs controller firmware with the enhanced log.
On older firmware the integration logs that no trigger or system log events will fire, and the rest of it still works.
Each event carries the `host` of the controller this integration connects to.
On a project with several masters, the stream also carries the other masters' events, and they carry that same `host`.
They keep the names the earlier fork of the stock integration fired, so automations written for that fork keep working.

### `vantage_trigger`

Fired each time a controller object, such as a button, a timer or a sensor, starts a task.
A button with no task assigned fires nothing.

| Field | Meaning |
| --- | --- |
| `trigger_type` | The kind of trigger, such as `button_down`, `button_up` or `timer`, or `unknown_<number>` for a kind not yet known |
| `event` | The controller's raw trigger number |
| `source_id`, `source_name` | The VID and name of the object that started the task |
| `source_path` | The object's name followed by the names of the objects it sits in, such as its keypad and station |
| `task_id`, `task_name` | The VID and name of the task it started |
| `param1`, `param2` | The trigger's two parameters, whose meaning depends on its kind |
| `entity_id` | The stock integration's entity for the source object, present only when it has one, such as a dry contact. A button has none |

For example, a press on a keypad button might fire `trigger_type: button_down`, `source_name: Button 2`, `source_path: [Button 2, Hall Keypad]` and `task_name: Hall Lights`.

The logbook shows every trigger as `Vantage`, naming the source, the object it sits in, the task and the kind, such as `Button 2 on Hall Keypad started task Hall Lights (button down)`.
A trigger with an `entity_id` also shows in that entity's logbook.
A busy program can add many entries a minute.

### `vantage_system_log`

Fired for each notable system log entry: every warning, error and fatal entry, and the module online, offline and status entries.
Compiler warnings from a program write are dropped.
A burst of warnings of one shape fires once, then once an hour while the burst continues.

| Field | Meaning |
| --- | --- |
| `type` | The entry type, such as `warning` or `error` |
| `source` | The controller subsystem that logged it |
| `message` | The log message |
| `object_id`, `object_name` | The VID and name of the object the message names, present only when it names one |

The logbook shows each one as `Vantage`, with its type and message.
A module online or offline entry also updates that module's connectivity sensor at once.

## Entities

Every entity has the diagnostic entity category and joins a device the stock integration owns, so it shows on that device's page.
Only the event stream sensor starts enabled.
Enable any of the others from the device page.

| Entity | On | What it reports |
| --- | --- | --- |
| **Connectivity** | Each module | Whether the module responds on its enclosure bus. It turns off when the module loses power, such as when its breaker trips. Its attributes hold the module's bus address and error count |
| **Hardware model** | Each module | The hardware model the module reports. The stock device's model is the Design Center object type instead |
| **Hardware revision** | Each module | The hardware revision the module reports, which changes when a module is swapped for a newer revision of the same hardware |
| **Firmware version** | Each module | The firmware version the module reports |
| **Bus errors** | Each module | How many times the module lost and regained the bus. The controller only raises this count, so the sensor has the `total_increasing` state class |
| **Event stream** | The controller | Whether this integration's own event stream is connected |
| **Host** | The controller | The host both integrations connect to, which the stock integration no longer shows |

The module entities read the enclosure buses every 60 seconds, and only while at least one of them is enabled.
A module online or offline log refreshes them at once.
A module missing from one read stays online until a second read confirms it.
So a module re-announcing itself after a program write does not flicker off.
The module entities keep their last values while a module is off the bus or its enclosure can't be read.
They go unavailable while the event stream is down, or when no enclosure at all can be read.

The event stream is a second Host Command session, apart from the stock integration's, and both events arrive on it.
Its sensor turns off when the stream drops after setup.
If the stream can't connect at setup, the entry retries and every entity stays unavailable.
So an automation that alerts on it watches for both off and unavailable.

## Services

Each service is response-only, so call it from an automation or script with a `response_variable`, or from **Developer tools -> Actions**.
Each reads every loaded controller, and on a project with several masters it reads each master in turn.
Every entry carries the `host` of the controller this integration connects to, which tells entries from different stock entries apart.
A trigger history or system log entry names the master it was read from in `master`.
The response holds what was read and an `errors` list naming each master or enclosure that could not be read.
The trigger history and system log services fail only when nothing at all could be read.
The module status service lists every module even when no enclosure could be read, with `online` null and the failures in `errors`.

| Service | Fields | Returns |
| --- | --- | --- |
| `vantage_diagnostics.get_trigger_history` | `limit`, entries per master, default 100 | `entries`: the trigger history, newest last, with the `vantage_trigger` fields other than `entity_id`, plus `time` and `master`. Times are the controller's local time |
| `vantage_diagnostics.get_system_log` | `limit`, entries per master, default 100 | `entries`: the system log, newest last, with `time`, `master`, `type`, `source` and `message` |
| `vantage_diagnostics.get_module_status` | None | `modules`: each module with `module_id`, `module_name`, `online`, `address`, `model`, `revision`, `version`, `status` and `errors` |

The module status service reads the bus whether or not any module entity is enabled.
Its `online` is null for a module whose enclosure could not be read.

## Installation

Install and set up the [Vantage InFusion][vantage-integration-link] integration by loopj first, and wait for it to load, then follow these steps.
Home Assistant 2026.11 folded HACS into Core as the built-in Marketplace.
On 2026.10, which is the minimum, install through HACS instead.

1. Confirm the Vantage InFusion integration is installed and shows as loaded under **Settings -> Devices & Services**.
1. On 2026.11 or later, open **Settings -> Marketplace**. On 2026.10, open **HACS -> Custom repositories**.
1. Add `https://github.com/ptr727/homeassistant-vantage-diagnostics` as a custom repository with category **Integration**.
1. Install **Vantage Diagnostics** and restart Home Assistant.
1. Open **Settings -> Devices & Services -> Add Integration** and add **Vantage Diagnostics**.

## Configuration

This integration has no configuration of its own.
It takes the connection settings of the stock Vantage InFusion integration, which must be set up and loaded before you add this one.
With several stock entries loaded, the setup flow asks which controller to add diagnostics to.

## Questions or Issues

- **General questions**:
  - Use the [Discussions][discussions-link] forum for general questions.
- **Bug reports**:
  - Ask in the [Discussions][discussions-link] forum if you are not sure if it is a bug.
  - Check the existing [Issues][issues-link] tracker for known problems.
  - If the issue is unique and a bug, file it in [Issues][issues-link], and include all pertinent steps to reproduce the issue.

## Development Environment Setup

See [`DEVCONTAINER.md`][devcontainer] for the devcontainer and local development setup.
See [`OPERATIONS.md`][operations] for the checks to run before pushing.

## Contributing

- **Branching workflow**:
  - Feature branch to `develop` via squash merge, and `develop` to `main` via merge commit.
  - See [`WORKFLOW.md`][workflow] and [`OPERATIONS.md`][operations] for the release flow.
- **Code style**:
  - See [`CODESTYLE.md`][codestyle] and [`.editorconfig`][editorconfig].
  - Apply auto-fixes with `scripts/fix`, and verify with `scripts/lint`.
- **Repository setup**:
  - See [`AUDIT.md`][audit] section 4 for repository configuration.

## 3rd Party Tools

The third-party tools, libraries, and actions this project depends on.

- [actionlint][actionlint-link]: GitHub Actions workflow linter.
- [aiovantage][aiovantage-link]: Async Python client for Vantage InFusion controllers.
- [Codecov][codecov-link]: Code coverage reporting service.
- [Create Pull Request][create-pull-request-link]: GitHub action that opens a pull request from workspace changes.
- [cspell][cspell-link]: Spell checker.
- [editorconfig-checker][editorconfig-checker-link]: Line-ending and whitespace linter.
- [GitHub Actions][github-actions-link]: CI and automation runner.
- [GitHub Dependabot][github-dependabot-link]: Dependency update bot.
- [hassfest][hassfest-link]: Home Assistant integration validator.
- [markdownlint-cli2][markdownlint-cli2-link]: Markdown linter.
- [mypy][mypy-link]: Python static type checker.
- [Nerdbank.GitVersioning][nerdbank-gitversioning-link]: Version computation from git height.
- [pre-commit][pre-commit-link]: Git hook manager.
- [pyright][pyright-link]: Python static type checker.
- [pytest][pytest-link]: Python test framework.
- [pytest-cov][pytest-cov-link]: Coverage plugin for pytest.
- [pytest-homeassistant-custom-component][pytest-homeassistant-custom-component-link]: Test harness for Home Assistant custom integrations.
- [ruff][ruff-link]: Python linter and formatter.
- [ShellCheck][shellcheck-link]: Shell script static analyzer.
- [shfmt][shfmt-link]: Shell script formatter.
- [uv][uv-link]: Python package and project manager.

## License

Licensed under the [MIT License][license]\
[![License][license-shield]][license]

<!-- Shields -->

[buildstatus-shield]: https://img.shields.io/github/actions/workflow/status/ptr727/homeassistant-vantage-diagnostics/test-pull-request.yml?logo=github&label=Build%20Status
[coverage-shield]: https://img.shields.io/codecov/c/github/ptr727/homeassistant-vantage-diagnostics?logo=codecov&label=Coverage
[lastcommit-shield]: https://img.shields.io/github/last-commit/ptr727/homeassistant-vantage-diagnostics?logo=github&label=Last%20Commit
[license-shield]: https://img.shields.io/github/license/ptr727/homeassistant-vantage-diagnostics?label=License
[prereleaseversion-shield]: https://img.shields.io/github/v/release/ptr727/homeassistant-vantage-diagnostics?include_prereleases&filter=*-g*&label=GitHub%20Pre-Release&logo=github&color=orange
[releaseversion-shield]: https://img.shields.io/github/v/release/ptr727/homeassistant-vantage-diagnostics?logo=github&label=GitHub%20Release

<!-- Distribution -->

[actions-link]: https://github.com/ptr727/homeassistant-vantage-diagnostics/actions
[commits-link]: https://github.com/ptr727/homeassistant-vantage-diagnostics/commits/main
[discussions-link]: https://github.com/ptr727/homeassistant-vantage-diagnostics/discussions
[github-link]: https://github.com/ptr727/homeassistant-vantage-diagnostics
[issues-link]: https://github.com/ptr727/homeassistant-vantage-diagnostics/issues
[releases-link]: https://github.com/ptr727/homeassistant-vantage-diagnostics/releases

<!-- Repo -->

[audit]: ./AUDIT.md
[codestyle]: ./CODESTYLE.md
[devcontainer]: ./DEVCONTAINER.md
[editorconfig]: ./.editorconfig
[history]: ./HISTORY.md
[license]: ./LICENSE
[operations]: ./OPERATIONS.md
[workflow]: ./WORKFLOW.md

<!-- External -->

[actionlint-link]: https://github.com/rhysd/actionlint
[aiovantage-link]: https://github.com/loopj/aiovantage
[codecov-link]: https://about.codecov.io/
[coverage-link]: https://app.codecov.io/gh/ptr727/homeassistant-vantage-diagnostics
[create-pull-request-link]: https://github.com/marketplace/actions/create-pull-request
[cspell-link]: https://cspell.org
[editorconfig-checker-link]: https://github.com/editorconfig-checker/editorconfig-checker
[github-actions-link]: https://github.com/actions
[github-dependabot-link]: https://github.com/dependabot
[hassfest-link]: https://github.com/home-assistant/actions
[markdownlint-cli2-link]: https://github.com/DavidAnson/markdownlint-cli2
[mypy-link]: https://mypy-lang.org/
[nerdbank-gitversioning-link]: https://github.com/dotnet/Nerdbank.GitVersioning
[pre-commit-link]: https://pre-commit.com/
[pyright-link]: https://github.com/microsoft/pyright
[pytest-cov-link]: https://github.com/pytest-dev/pytest-cov
[pytest-homeassistant-custom-component-link]: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
[pytest-link]: https://docs.pytest.org/
[ruff-link]: https://docs.astral.sh/ruff/
[shellcheck-link]: https://www.shellcheck.net/
[shfmt-link]: https://github.com/mvdan/sh
[uv-link]: https://docs.astral.sh/uv/
[vantage-integration-link]: https://github.com/loopj/home-assistant-vantage
