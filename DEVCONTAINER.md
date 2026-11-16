# Devcontainer Development

Home Assistant Core does not run on Windows natively, so development is Linux only.
Use a native Linux or WSL2 host, or the VS Code devcontainer this guide covers.
Open the repository in the devcontainer, and `scripts/setup` runs on container creation and installs everything into a uv-managed `.venv`.

## Dev Loop

```sh
scripts/setup     # provision the uv .venv: HA plus the test dependencies
scripts/develop   # boot Home Assistant against ./config with this integration loaded
scripts/fix       # apply ruff auto-fixes (format and check --fix)
scripts/lint      # verify only: ruff format --check, ruff check, mypy --strict, and pyright, as CI does
pytest            # run the test suite in the .venv, after scripts/setup
```

`scripts/lint` is the CI gate.
It fails non-zero on any ruff, format, `mypy --strict`, or `pyright` violation, so "green locally" matches "green on GitHub".
When it fails on an auto-fixable issue, run `scripts/fix` and run lint again.

This integration declares no `requirements` of its own.
It runs on the `aiovantage` version the stock Vantage InFusion integration installs.
The test requirements pin that version in `requirements-test.txt`.

Each script is wired as a VS Code task in [.vscode/tasks.json](.vscode/tasks.json) (**Command Palette -> Tasks: Run Task**):

| Script | VS Code task | Shortcut |
| --- | --- | --- |
| `scripts/setup` | **Setup: Install dev requirements** | Tasks: Run Task |
| `scripts/develop` | **HA: run Home Assistant** | Tasks: Run Task |
| `scripts/lint` | **HA: lint** | Tasks: Run Task |
| `pytest` | **Python Test** | Tasks: Run Test Task (default) |
| ruff, mypy, and pyright | **Python Format** | `Ctrl+Shift+B` (default build task) |

## Run and Debug Home Assistant

- **Run:** `scripts/develop` (or the **HA: run Home Assistant** task) boots HA against `./config` with the integration loaded. Open the forwarded port **8123** for the web UI.
- **Debug:** press **F5** (**Home Assistant (debug)** in [.vscode/launch.json](.vscode/launch.json)) to run HA under the debugger. Breakpoints in `custom_components/vantage_diagnostics/` hit, `PYTHONPATH` is preserved, and `./config` is created on first run.

On first run HA is seeded with a minimal config ([`scripts/init-config`](scripts/init-config)).
The `default_config` entry is intentionally omitted, since its heavy optional dependencies are not installed in the devcontainer.
This integration reuses the stock Vantage InFusion connection, so add that integration to the dev instance first.
Then add this integration from **Settings -> Devices & Services -> Add Integration -> Vantage Diagnostics**.

## Host Prerequisites

[`.devcontainer.json`](.devcontainer.json) provisions Python 3.14, uv, Node, and the linters through features.
It runs `scripts/setup` and forwards port 8123.
Commits are SSH-signed using your host's `ssh-agent`, which VS Code forwards into the container, plus the bind-mounted public signing key and `allowed_signers`.
The `gh` CLI uses the mounted host config.

Set up the following on the **host** before opening the devcontainer:

- **Git identity and SSH commit signing.** See GitHub's [Telling Git about your SSH key][gh-ssh-signing] and [About commit signature verification][gh-sig-verify].
- **A running `ssh-agent` with your signing key loaded**, so VS Code can forward it into the container. See VS Code's [Sharing Git credentials with your container][vscode-share-creds].
- **On WSL**, Docker Desktop's [WSL integration][docker-wsl] enabled.
- **The bind-mounted host paths.** A missing path fails the container build. The paths are `~/.ssh/id_ed25519.pub`, `~/.config/git/allowed_signers`, and `~/.config/gh`.

If you do not sign commits or do not want the mounts, remove the `mounts` block from `.devcontainer.json` locally.
You can also work on a plain Linux or WSL2 host instead.

[docker-wsl]: https://docs.docker.com/desktop/wsl/
[gh-sig-verify]: https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification
[gh-ssh-signing]: https://docs.github.com/en/authentication/managing-commit-signature-verification/telling-git-about-your-signing-key
[vscode-share-creds]: https://code.visualstudio.com/remote/advancedcontainers/sharing-git-credentials
