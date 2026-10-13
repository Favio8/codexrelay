# Architecture

CodexRelay has a desktop control plane and an MCP execution engine. The GUI does not proxy Codex's main-model traffic.

```mermaid
flowchart LR
    GUI["React UI / Tauri host"] --> RPC["Python control service"]
    RPC --> DB["SQLite state and task records"]
    RPC --> Vault["Windows Credential Manager"]
    Codex["Codex Desktop / CLI"] --> MCP["CodexRelay MCP"]
    MCP --> DB
    MCP --> Vault
    MCP --> Provider["Selected external provider"]
    MCP --> Tools["Workspace tools and recovery journal"]
    MCP <-->|"model/tool bridge"| Pi["Bundled Pi Agent SDK / Node"]
```

The desktop host starts a bundled Python process per control request. Codex starts its own long-lived MCP process from the installed runtime. Both consult shared local state, so changing a switch does not require restarting Codex.

Before a new task, the engine checks the global switch, workspace policy, provider configuration and active-task state. The first release permits one active delegated task. Disabling delegation does not cancel it; explicit cancellation stops further execution and leaves partial changes for review.

Providers have Flash and Pro model slots plus a cumulative task token limit. API keys are kept in Windows Credential Manager. Configuration, jobs and recovery records are stored under the application data directory, which tests can override with CODEXRELAY_HOME.

Registration edits only the application's owned MCP entry and managed delegation block. It backs up host configuration, supports legacy DeepSeek import and detects foreign name conflicts. Codex's main provider and login remain intact.

The execution engine derives from the pinned upstream in vendor/. Read-only mode exposes file inspection; coding mode adds writes and commands. Commands run with host permissions. File mutations have recovery records, but command-generated changes require reviewing the entire workspace.

Thinking-mode history compaction preserves original assistant messages, tool calls and opaque reasoning content, while shortening older tool output. Codex remains responsible for task selection, ambiguous debugging and final acceptance.

Task-settlement usage reporting aggregates all retained tasks, including known usage from failures and cancellations. Provider/model prices are versioned and frozen at admission; historical backfill is explicit. Separate idempotent response events support request-day reporting and cache counters. Estimates use Decimal and distinguish missing usage, unknown caching and unpriced records. A frontend cache uses the shared usage revision and local day; aggregation does not enter snapshot polling.

Codex log import is a separate, default-off feature. It incrementally stores counters and cursors, never message bodies, and does not proxy main-model traffic. Pi scheduling uses a credential-free Node SDK process; all model requests and workspace tools return to the Python control boundary. Both backends share admission, workspace leases, transaction recovery and independent verification. Pi coding also persists observed-workspace checks. See [0.2.0 implementation and verified scope](ROADMAP-0.2.0.md).
