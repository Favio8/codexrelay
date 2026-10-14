<div align="center">
<img src="desktop/assets/codexrelay-icon.png" width="96" alt="CodexRelay">

# CodexRelay

**Let Codex delegate to DeepSeek. Manage external agents with a switch.**

[![Release](https://img.shields.io/github/v/release/Favio8/codexrelay?include_prereleases)](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[简体中文](README.md) · English · [Download](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1)
</div>

**0.3.1 Windows Beta** adds verified continuations, persistent checkpoints and per-task App authorization, and fixes excessive reading of minified files under low budgets. See [release details](docs/RELEASE-0.3.1.md) and [acceptance evidence](docs/EXECUTION-ACCEPTANCE.md).

A Windows desktop manager for Codex external agents. Codex plans and reviews; DeepSeek or an OpenAI-compatible provider executes suitable independent tasks through MCP.

![Provider management](docs/provider-row-preview.png)

*Actual desktop UI with demo providers. The current UI is in Simplified Chinese.*

## Download

[Windows x64 installer — v0.3.1 Beta](https://github.com/Favio8/codexrelay/releases/download/v0.3.1/CodexRelay_0.3.1_x64-setup.exe) · [Release notes and checksums](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1)

The current release is **0.3.1 Beta**. Both the original engine and Pi support independent Run budgets, structured context summaries, persistent checkpoints and remaining-only continuation after Codex verification. It retains the 0.2.2 single-instance and in-place upgrade fixes. After upgrading, update managed rules in Settings and restart Codex to load the new tools. See the [0.3.1 release record](docs/RELEASE-0.3.1.md) for changes and verification.

Verified on Windows 11 x64. Requires an existing Codex installation. Python and the Node runtime for Pi are bundled; end users do not need developer tools. The installer downloads WebView2 if missing. This beta is unsigned and has no automatic updater.

## Get started

1. Add a DeepSeek or OpenAI-compatible provider, API key and models. Connection tests make a real API request.
2. Register CodexRelay in Settings. Existing deepseek-as-subagent registrations can be imported and backed up.
3. Restart Codex Desktop, call the MCP `ping` tool in a new session to verify the workspace, then enable delegation.

The same MCP registration can be used by Codex CLI. Codex chooses when to delegate; its main model and login remain under Codex's control.

Registration is a one-time setup. Afterwards, open Codex directly: the saved switch state persists, and Codex starts the execution engine itself. CodexRelay only needs to be open when changing settings or viewing tasks. Closing the window or quitting its tray application does not disable delegation or terminate Codex's MCP tasks. Closing Codex or its execution engine can interrupt work; review partial changes after restarting.

## Features

- Global switch and per-project disable; switching off blocks new work while existing work finishes.
- Provider management with Flash / Pro model slots and per-task token budgets.
- Task status, tool records, cancellation, diagnostics and recovery records.
- Independent Run budgets with up to three continuations by default, each following parent verification.
- App defaults and per-task grants for additional rounds or subsequent budgets.
- Bounded reading, structured context rebuilds and checkpoints with actual evidence and recovery references.
- Tray controls, registration backups and selective uninstall cleanup.
- API keys stored in Windows Credential Manager.
- Bundled runtime, 900 × 600 default window and compact provider rows.

One delegated task runs at a time. Read-only mode uses Read / Glob / Grep; coding mode can run commands and change files. Cancellation preserves partial changes. Commands run with the current user's permissions through the upstream trusted-host mechanism, not an OS sandbox.

The default 1,000,000-token budget is cumulative within each Run, including execution, summary, retry and repeated-history usage. A TaskGroup retains total usage and used continuation counts across independent Runs. Older tool outputs can be compacted, and structured context rebuilds preserve legal thinking/tool protocols. Every continuation requires Codex to verify actual progress and delegate only remaining work. App grants can extend a task's rounds or subsequent budget.

The switch controls CodexRelay external agents only. It does not disable Codex's built-in agents. Task context goes directly to the selected provider. Local state defaults to `%LOCALAPPDATA%\CodexRelay`.

## Development

Tauri 2 / Rust / React / TypeScript / Python 3.12 / SQLite.

See [development](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md), [testing](docs/TESTING.md) and [contributing](CONTRIBUTING.md). The published **0.3.1 Beta** includes the Pi Agent SDK backend, delegated usage/cost estimates, response-level cache accounting, default-off Codex log statistics and verified continuations with per-task grants. See [54-case acceptance scope](docs/EXECUTION-ACCEPTANCE.md). Automatic updates and concurrent agents remain outside this release.

## Credits and license

The execution engine derives from [PsChina/deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent); see [upstream provenance](vendor/deepseek-as-subagent/UPSTREAM.md). CodexRelay is MIT licensed. Third-party software retains its own licenses; see [notices](THIRD_PARTY_NOTICES.md).
