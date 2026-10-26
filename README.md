# LEO — a local AI desktop companion

[![Tests](https://github.com/andresrubiop/leo-desktop/actions/workflows/tests.yml/badge.svg)](https://github.com/andresrubiop/leo-desktop/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-e95420.svg)](LICENSE)

<img src="assets/leo/sit.svg" alt="LEO, an illustrated Shar-Pei desktop companion" width="180" align="right">

LEO is a desktop companion and **local AI agent for GNOME on Wayland**. An illustrated Shar-Pei walks on the desktop, opens a conversation balloon and proposes actions through typed tools. **Ollama runs inference locally; a policy broker decides what may execute.**

Built with **Python, GJS, GTK4 and D-Bus**. The reference environment is Ubuntu 26.04 / GNOME 50. This is experimental desktop software, not a claim of compatibility with every GNOME version.

## What it does

- Local text/vision through Ollama, with no remote inference fallback.
- Typed tool calls for authorized file operations, notes, reminders and desktop settings.
- One-use, expiring approvals for mutations; an undo journal for supported actions.
- Screen and accessibility features controlled by explicit capabilities. Optional attentive mode is off by default.
- A movable avatar and conversation balloon, with keyboard focus only when requested.
- Optional local document indexing through Cerveau, voice tools and account-usage integrations.

External documents and tool output are data, not instructions. Model output is never executed as arbitrary shell code. Permissions, the tool schema and approval handling are implemented in [`leo/broker.py`](leo/broker.py), [`leo/agent.py`](leo/agent.py) and [`leo/actions.py`](leo/actions.py).

## Try it

On Ubuntu with GNOME 50, install the system dependencies through your package manager:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gjs
git clone https://github.com/andresrubiop/leo-desktop.git
cd leo-desktop
python3 scripts/install.py
gnome-extensions enable leo@local.desktop
```

The installer writes to your user directories. It does not enable the extension for you. A newly installed extension may require logging out and back in. Install and start [Ollama](https://ollama.com) separately, then choose an already installed local model in LEO. No model is downloaded by these commands.

For a text-only check with Ollama running:

```bash
bash scripts/run.sh ctl status
bash scripts/run.sh ctl models
bash scripts/run.sh ctl chat 'Hello, Leo'
```

The interface currently uses Spanish. Optional features need their own dependencies: `espeak-ng` for speech output, whisper.cpp for dictation, `poppler-utils` and `python3-cairo` for PDF/figure handling, and a separately configured Cerveau installation for indexed document search. Core conversation and the avatar do not require Cerveau. Its paths can be configured with `LEO_CERVEAU_CLI` and `CERVEAU_HOME`.

## Architecture

```mermaid
flowchart LR
    UI[GNOME avatar / GTK settings] -->|D-Bus| Agent[Python agent]
    Agent -->|loopback HTTP| Ollama[Ollama local model]
    Ollama -->|proposed typed calls| Broker[Capabilities + approval broker]
    Broker --> Tools[Authorized tools]
    Tools --> Undo[Undo journal]
```

See [architecture](docs/architecture.md), [testing](docs/testing.md), and [security boundaries](SECURITY.md).

## Verification and limits

```bash
python3 -m unittest discover -s tests -v
node tests/movement.mjs
```

The public source derives from LEO 0.19.1. During release preparation, 195 Python unit tests were run: 194 passed and one photo-analysis test was skipped because OpenCV was unavailable in that environment. These are unit tests; they do not establish real desktop, microphone or GPU integration. CI also runs the JavaScript movement checks.

Known limitations include GNOME-version coupling, optional dependencies, and imperfect local-model tool selection. Visual pointing still needs validation on each real multi-monitor setup. An unavailable local model produces an error rather than a simulated answer.

## Source and contribution

This repository is a curated public source snapshot. It contains code, vector illustrations and synthetic tests; personal photos, conversations, runtime state and the private operational journal are excluded. See [provenance](PROVENANCE.md).

Changes should preserve local inference, capability checks and approvals. Run the unit tests and describe any manual integration validation separately. For a desktop test, use `scripts/test_shell.py` in its isolated session; do not enable or replace a user's running extension as part of an automated test.

MIT-licensed; existing component notices are preserved. By [Andrés Rubio Proaño](https://www.andresrubiop.com).
