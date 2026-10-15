<div align="center">
<img src="desktop/assets/codexrelay-icon.png" width="96" alt="CodexRelay">

# CodexRelay

**Let Codex delegate to DeepSeek. Manage external agents with a switch.**

[![Release](https://img.shields.io/github/v/release/Favio8/codexrelay?include_prereleases)](https://github.com/Favio8/codexrelay/releases/tag/v0.4.1)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[简体中文](README.md) · English · [Download](https://github.com/Favio8/codexrelay/releases/tag/v0.4.1)
</div>

**0.4.1 Windows Beta** fixes provider-save races, integration repair, verification and continuation gates, and evaluation accounting. The evaluation UI now exposes Pro selection, sourced reference prices, and failure/takeover outcomes. See the [release record](docs/RELEASE-0.4.1.md), [27-item acceptance matrix](docs/DELEGATION-ACCEPTANCE.md) and [experiment instructions](docs/DELEGATION-EXPERIMENTS.md). Real-client six-scenario and paid-model comparisons have not run; no real savings claim is made.

A Windows desktop manager for Codex external agents. Codex plans and reviews; DeepSeek or an OpenAI-compatible provider executes suitable independent tasks through MCP.

![Provider management](docs/provider-row-preview.png)

*Actual desktop UI with demo providers. The current UI is in Simplified Chinese.*

## Download

[Windows x64 installer — v0.4.1 Beta](https://github.com/Favio8/codexrelay/releases/download/v0.4.1/CodexRelay_0.4.1_x64-setup.exe) · [Release notes and checksums](https://github.com/Favio8/codexrelay/releases/tag/v0.4.1)

The current release is **0.4.1 Beta**. Both backends retain independent Run budgets, checkpoints and verified remaining-only continuation. After upgrading, inspect integration, review selected tool/rule repairs, then reconnect Codex and verify the current chat. Saved configuration and a ping do not establish full client capability. See the [release record](docs/RELEASE-0.4.1.md).

Verified on Windows 11 x64. Requires an existing Codex installation. Python and the Node runtime for Pi are bundled; end users do not need developer tools. The installer downloads WebView2 if missing. This beta is unsigned and has no automatic updater.

## Get started

1. Add a DeepSeek or OpenAI-compatible provider, API key and models. Quick connection tests make one real model request with a 12-second deadline. Full tool verification is separate, with a 45-second execution budget; quick success only confirms that the model responds.
2. Register CodexRelay in Settings. Existing deepseek-as-subagent registrations can be imported and backed up.
3. Restart Codex Desktop, call the MCP `ping` tool in a new session to verify the workspace, then enable delegation.

The same MCP registration can be used by Codex CLI. Codex chooses when to delegate; its main model and login remain under Codex's control.

Registration is a one-time setup. Afterwards, open Codex directly: the saved switch state persists, and Codex starts the execution engine itself. CodexRelay only needs to be open when changing settings or viewing tasks. Closing the window or quitting its tray application does not disable delegation or terminate Codex's MCP tasks. Closing Codex or its execution engine can interrupt work; review partial changes after restarting.

## Features

- Global switch and per-project disable; switching off blocks new work while existing work finishes.
- Provider management with Flash / Pro model slots and per-task token budgets.
- Task status, tool records, cancellation, diagnostics and recovery records.
- Versioned capability diagnosis, selective repair previews, encrypted backups and concurrent-edit checks.
- Separate response, tool-protocol and real backend file-read probes with independent evidence and usage.
- Frozen evaluation cases, explicit sessions, cumulative authorization and independent scoring; failures and unknown costs remain visible.
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

See [development](docs/DEVELOPMENT.md), [architecture](docs/ARCHITECTURE.md), [testing](docs/TESTING.md) and [contributing](CONTRIBUTING.md). The **0.4.1 Beta** acceptance matrix records 19 locally verified conditions, seven partially verified conditions and one real-client six-scenario condition not run. Forty-five trials are prepared only, without paid authorization. Real net benefit is unknown. Automatic updates and concurrent agents remain outside this release.

## Credits and license

The execution engine derives from [PsChina/deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent); see [upstream provenance](vendor/deepseek-as-subagent/UPSTREAM.md). CodexRelay is MIT licensed. Third-party software retains its own licenses; see [notices](THIRD_PARTY_NOTICES.md).
