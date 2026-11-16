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

- Initial repository standup. There is no release yet.

See [Release History][history] for complete release notes and older versions.

## Table of Contents

- [homeassistant-vantage-diagnostics](#homeassistant-vantage-diagnostics)
  - [Build and Distribution](#build-and-distribution)
    - [Build Status](#build-status)
    - [Releases](#releases)
    - [Release Notes](#release-notes)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
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

It adds the following:

- **`vantage_trigger` events**: Events fired for controller triggers.
- **`vantage_system_log` events**: Events fired for controller system log entries.
- **Module connectivity sensors**: One diagnostic binary sensor per module that reports whether the module responds on its enclosure bus. They are disabled by default.
- **Module diagnostic sensors**: Four diagnostic sensors per module, read from the module's status on its enclosure bus. They are disabled by default, and they keep their last values while the module is off the bus.
  - **Hardware model**: The model the module reports, which the stock device does not show. The stock device's model is the Design Center object type.
  - **Hardware revision**: The hardware revision the module reports, which changes when a module is swapped for a newer revision of the same hardware.
  - **Firmware version**: The firmware version the module reports.
  - **Bus errors**: How many times the module lost and regained the bus. The controller only raises this count, so the sensor has the `total_increasing` state class.
- **Event stream sensor**: A diagnostic connectivity binary sensor on the controller, which reports whether this integration's own event stream is connected. That stream is a second Host Command session, apart from the stock integration's. The trigger and system log events arrive on it. It is enabled by default and turns off rather than unavailable while the stream is down.
- **Host sensor**: A diagnostic sensor on the controller with the host both integrations connect to. The stock integration no longer shows it. It is disabled by default.
- **`get_trigger_history` service**: A response-only service that returns recent trigger history.
- **`get_system_log` service**: A response-only service that returns the controller system log.
- **`get_module_status` service**: A response-only service that returns the module status, including each module's address, hardware model, hardware revision, firmware version, status bytes, and error count.

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
