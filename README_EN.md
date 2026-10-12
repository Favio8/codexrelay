<div align="center">
<img src="desktop/assets/codexrelay-icon.png" width="96" alt="CodexRelay">

# CodexRelay

**Let Codex delegate to DeepSeek. Manage external agents with a switch.**

[![Release](https://img.shields.io/github/v/release/Favio8/codexrelay?include_prereleases)](https://github.com/Favio8/codexrelay/releases/tag/v0.1.2)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[简体中文](README.md) · English · [Download](https://github.com/Favio8/codexrelay/releases/tag/v0.1.2)
</div>

A Windows desktop manager for Codex external agents. Codex plans and reviews; DeepSeek or an OpenAI-compatible provider executes suitable independent tasks through MCP. Provider management and desktop workflow are inspired by [CC Switch](https://github.com/farion1231/cc-switch).

![Provider management](docs/provider-row-preview.png)

*Actual desktop UI with demo providers. The current UI is in Simplified Chinese.*

## Download

[Windows x64 installer — v0.1.2 Beta](https://github.com/Favio8/codexrelay/releases/download/v0.1.2/CodexRelay_0.1.2_x64-setup.exe) · [Release notes and checksums](https://github.com/Favio8/codexrelay/releases/tag/v0.1.2)

Verified on Windows 11 x64. Requires an existing Codex installation. Python is bundled; end users do not need developer tools. The installer downloads WebView2 if missing. This beta is unsigned and has no automatic updater.

## Get started

1. Add a DeepSeek or OpenAI-compatible provider, API key and models. Connection tests make a real API request.
2. Register CodexRelay in Settings. Existing deepseek-as-subagent registrations can be imported and backed up.
3. Restart Codex Desktop, call the MCP `ping` tool in a new session to verify the workspace, then enable delegation.

The same MCP registration can be used by Codex CLI. Codex chooses when to delegate; its main model and login remain under Codex's control.

## Features

- Global switch and per-project disable; switching off blocks new work while existing work finishes.
- Provider management with Flash / Pro model slots and per-task token budgets.
- Task status, tool records, cancellation, diagnostics and recovery records.
- Tray controls, registration backups and selective uninstall cleanup.
- API keys stored in Windows Credential Manager.
- Bundled runtime, 900 × 600 default window and compact provider rows.

One delegated task runs at a time. Read-only mode uses Read / Glob / Grep; coding mode can run commands and change files. Cancellation preserves partial changes. Commands run with the current user's permissions through the upstream trusted-host mechanism, not an OS sandbox.

The default 1,000,000-token budget is a cumulative local task limit, not an account balance or price. Older tool outputs are compacted; thinking-mode assistant history and opaque reasoning fields are preserved.

The switch controls CodexRelay external agents only. It does not disable Codex's built-in agents. Task context goes directly to the selected provider. Local state defaults to `%LOCALAPPDATA%\CodexRelay`.

## Development

Tauri 2 / Rust / React / TypeScript / Python 3.12 / SQLite.

See [development](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md), [testing](docs/TESTING.md) and [contributing](CONTRIBUTING.md). Automatic updates, cost reporting and concurrent agents are outside the current beta.

## Credits and license

The execution engine derives from [PsChina/deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent); see [upstream provenance](vendor/deepseek-as-subagent/UPSTREAM.md). CodexRelay is MIT licensed. Third-party software retains its own licenses; see [notices](THIRD_PARTY_NOTICES.md).
