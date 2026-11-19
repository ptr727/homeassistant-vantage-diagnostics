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
- Logbook entries for the trigger and system log events, with a trigger also shown on its source's stock entity where it has one.
- Module connectivity, hardware model, hardware revision, firmware version and bus error sensors, attached to the stock module devices.
- Event stream, host and IP address sensors on the controller device.
- The `get_trigger_history`, `get_system_log` and `get_module_status` response-only services.
- A module fault report, fired as a `vantage_module_fault` event when a module loses a line feed or drops off its bus. A `vantage_module_recovered` event, a **Last fault** sensor per module and the `get_fault_report` service go with it.
- A `side_effect` flag on each out-of-range and back-in-range trigger, telling a trip's side effects from genuine readings.

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

- **Events**: `vantage_trigger`, `vantage_system_log`, `vantage_module_fault` and `vantage_module_recovered`, fired on the Home Assistant event bus.
- **Entities**: diagnostic sensors on the stock integration's module and controller devices.
- **Services**: four response-only services that read the controller's trigger history, system log and module status, and return each module's last fault report.

## Events

The trigger and system log events arrive on this integration's own event stream, which needs controller firmware with the enhanced log.
On older firmware the integration logs that no trigger or system log events will fire, and the rest of it still works.
There, only a module dropping off its bus starts a module fault.
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
| `side_effect` | On an `out_of_range` or `in_range` trigger only, whether the module losing or regaining power explains it rather than a genuine reading. See [`vantage_module_fault`](#vantage_module_fault) |

For example, a press on a keypad button might fire `trigger_type: button_down`, `source_name: Button 2`, `source_path: [Button 2, Hall Keypad]` and `task_name: Hall Lights`.

The logbook shows every trigger as `Vantage`, naming the source, the object it sits in, the task and the kind.
For example, `Button 2 on Hall Keypad started task Hall Lights (button down)`.
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

### `vantage_module_fault`

Fired when a module faults, such as when its breaker trips, carrying a report of the module's state at the fault.
A fault starts with whichever of these comes first:

- The controller's system log warning that the module detected a missing line feed. The `signal` is `system_log`.
- The module's `module_alert` trigger, which the controller raises with the missing line feed in `param1`. The `signal` is `module_alert`.
- The module dropping off its bus and staying off for 30 seconds. The `signal` is `offline`. The hold keeps a program write, which re-announces each module within a few seconds, from raising a report.

The other signals of the same fault add nothing, since a trip raises all three within seconds.
A fault lasts until the module is back on its bus.
One whose module never drops off the bus ends 2 minutes after it started, so a later warning reports again.
The `module_alert` trigger fires only when the Design Center program assigns a task to the module's Alert event.
The bus drop needs one of the module's connectivity, hardware, firmware or bus error sensors enabled, since only they read the bus.
They all start enabled, and the **Last fault** sensor alone reads nothing.

The event fires once the timeline is read, carrying the report as it stands then.
A trip's bus drop and side effects often arrive after that, so the event may lack them while the stored report has them.
The `get_fault_report` service returns the report as it stands now.

| Field | Meaning |
| --- | --- |
| `module_id`, `module_name` | The VID and name of the module |
| `signal` | What started the fault: `system_log`, `module_alert` or `offline` |
| `time` | When the fault started, in UTC. For a bus drop alone it is when the module dropped off |
| `line_feed`, `line_feed_name` | The number and name of the missing line feed, null when the controller named none |
| `offline` | When the module dropped off its bus during the fault, null until it does. It stays set once the module recovers |
| `recovered`, `outage` | When the module came back on its bus, and the seconds since the fault started. Null until it recovers |
| `temperature` | The module's temperature at the fault, in degrees Celsius, null where the sensor is not reporting |
| `lines` | Each line feed's number, letter, name, and last `power` and `current` reading, null where the sensor is not reporting |
| `loads` | Each load on the module, with its VID, name, `channel`, `line_feed` and last `level`. `last_on` and `last_off` are the UTC times it last turned on and off since the entry last loaded. `on_missing_line` marks the loads on the missing line feed |
| `timeline` | The controller's triggers and the module's load level changes over the 30 minutes before the fault, oldest first. Each row has its UTC `time`, its offset `t` in seconds from the fault, and its `kind`, `trigger` or `load`. At most the 60 rows nearest the fault are kept. Null until the trigger history is read. A restart or reload that cuts that read short leaves it null until the entry loads again, which reads it then and fires the event |
| `timeline_errors` | What could not be read for the timeline, each with its `host`, `master` and `error`. `master` is null for a failure that was not a master's. Empty when the whole history was read, so an empty `timeline` then means a quiet half hour. Null while `timeline` is |
| `side_effects` | The range triggers attributed to the fault |

A sensor reading is the last value aiovantage holds when the fault starts.
A line that has just lost power may still show its last power then, since its sensor reports the loss later.
A load's level is what the controller commanded, not measured power, which the module measures per line feed only.
Which channels a line feed powers depends on the module model.
The integration knows the SDM-12, where each line feed powers three consecutive channels.
A module of another model lists its loads by channel, with a null `line_feed`.
The timeline reads the controller's whole trigger history once per fault, and takes the controller's clock to be in Home Assistant's time zone.
A load change shows only if it happened while the entry was loaded.
The entry reloads whenever the stock entry does, which also clears what it saw of the loads.

When a module loses power, each of its range-monitored sensors raises an out-of-range trigger, 6 to 36 seconds after the fault starts.
So each `out_of_range` and `in_range` `vantage_trigger` carries `side_effect`.
An `out_of_range` trigger is a side effect when its module is off the bus, or a fault started on it in the last 2 minutes.
An `in_range` trigger is a side effect when its module joined or left the bus in the last 5 minutes.
It is also one when a fault started on the module in the last 2 minutes.
A side effect joins the `side_effects` of the fault it belongs to.
That is a fault in progress, or the one a running hold goes on to start.
For `in_range` it is also a fault recovered in the last 5 minutes.
One that belongs to none, such as a sensor back in range after a drop shorter than the hold, is flagged and joins nothing.
A fault keeps at most 50.
An automation that drops each trigger with `side_effect: true` is left with the genuine readings.

The report is kept across a restart, so a fault that spans one still recovers.
The **Last fault** sensor and the `get_fault_report` service read the stored report.

### `vantage_module_recovered`

Fired when a faulted module comes back on its bus, such as when its breaker is reset.
A module that came back while Home Assistant was down recovers at the first bus read after the restart.
Its outage is then measured up to that read.

| Field | Meaning |
| --- | --- |
| `module_id`, `module_name` | The VID and name of the module |
| `time` | When the fault started, as in the fault event |
| `recovered` | When the module came back on its bus, in UTC |
| `outage` | The seconds from the fault's start to the module's return |

## Entities

Every entity has the diagnostic entity category and joins a device the stock integration owns, so it shows on that device's page.
Every entity starts enabled, since its value rarely changes and the module entities share one bus read.
The **Last fault** sensor never reads the bus.
The hardware model, hardware revision and firmware version sensors read the same reply as the connectivity and bus error sensors.
So while a module's connectivity or bus error sensor is enabled, they add no polling.
The host and IP address sensors never read the bus.
Re-enabling a disabled entity reloads the entry about 30 seconds later, which reads the bus once more while a module entity is enabled.

| Entity | On | What it reports |
| --- | --- | --- |
| **Connectivity** | Each module | Whether the module responds on its enclosure bus. It turns off when the module loses power, such as when its breaker trips. Its attributes hold the module's bus address and error count |
| **Hardware model** | Each module | The hardware model the module reports. The stock device's model is the Design Center object type instead |
| **Hardware revision** | Each module | The hardware revision the module reports, which changes when a module is swapped for a newer revision of the same hardware |
| **Firmware version** | Each module | The firmware version the module reports |
| **Bus errors** | Each module | How many times the module lost and regained the bus. The controller only raises this count, so the sensor has the `total_increasing` state class |
| **Last fault** | Each module | When the module last faulted, unknown until it first does. Its attributes summarize the fault: `signal`, `line_feed`, `line_feed_name`, `recovered`, `outage`, and the count of `side_effects`. The full report is too big for attributes, so it is in the event and the service |
| **Event stream** | The controller | Whether this integration's own event stream is connected |
| **Host** | The controller | The host both integrations connect to, as configured in the stock entry, a host name or an address |
| **IP address** | The controller | The address this integration's event stream connects to, read from its socket rather than looked up. It replaces the IP address sensor the stock integration removed. It keeps the last address while the stream is down |

The module entities read the enclosure buses every 60 seconds, and only while at least one of them is enabled.
A module online or offline log refreshes them at once.
A module missing from one read stays online until a second read confirms it.
So a module re-announcing itself after a program write does not flicker off.
The module entities keep their last values while a module is off the bus or its enclosure can't be read.
They go unavailable while the event stream is down, or when no enclosure at all can be read.

The event stream is a second Host Command session, apart from the stock integration's, and the trigger and system log events arrive on it.
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
| `vantage_diagnostics.get_fault_report` | `module_id`, optional | `reports`: the last fault report of the module with that VID, or of every module that has faulted. Each has the fields of [`vantage_module_fault`](#vantage_module_fault) as they stand now |

The module status service reads the bus whether or not any module entity is enabled.
Its `online` is null for a module whose enclosure could not be read.
The fault report service reads nothing from the controller, and returns an empty `reports` for a module that has not faulted.

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
