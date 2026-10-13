# Testing and verified scope

## 0.2.2 upgrade and single instance

The patch passed 127 unit tests. `node tests/installer-upgrade.mjs` exercises the actual NSIS installer with legacy GUI/MCP processes, a live-task refusal, retained state, unified shortcuts and duplicate GUI activation. `node tests/installer-locations.mjs` covers fresh installation and relocation of an old installation from the data folder. Fixtures use an isolated installation scope under `.tmp/`; they do not modify everyday registrations. Legacy executable/package snapshots for the first fixture are retained in `.tmp/upgrade-baseline/`. See [implementation, validation and template provenance](RELEASE-0.2.2.md).

Exit the everyday management application from its tray before native GUI fixtures: the single-instance rule also applies to test copies. Codex-managed MCP engines can remain running.

## 0.2.1 startup and compact settings

The local 0.2.1 patch passed 119 unit tests. `test_startup.py` checks reads under an active database writer, legacy schema initialization without resetting settings, distinct SQLite busy/permission errors and private error details. `node tests/compact-settings-ui.mjs` uses the built frontend with actual Python RPC in isolated state; injected IPC failures check finite read retries, no settings/model-test replay, independent error recovery and dismissal, provider display and 900/800-width layouts. The updated native roadmap fixture also passed against the actual 0.2.1 Tauri/WebView2 and bundled Python/Pi, using a local model endpoint. See [patch scope and limits](RELEASE-0.2.1.md).

The 0.1.2 beta was tested on Windows 11 x64 with the bundled Python 3.12 runtime. Unit validation passed 30 tests; native build and NSIS packaging passed.

Local MCP fixtures cover delegation, workspace tools, switches, cancellation and recovery without paid-provider access. Native UI checks used an isolated application-data directory and demo providers; they verified 900 × 600 default size, the 800 × 520 minimum layout, provider rows and existing-registration behavior.

Official DeepSeek checks exercised real reading, coding and testing. A focused history test reproduced the thinking-mode API 400 with synthetic assistant history and verified the corrected history was accepted. A fresh Codex session after restart called the installed 0.1.2 MCP and completed real read-only delegation with the correct workspace.

These observations do not claim every Codex version, third-party provider or Windows environment is validated. In particular, arbitrary OpenAI-compatible services, Windows 10, ARM64 and fresh-machine installation remain outside the verified matrix.

## Routine checks

```powershell
.\runtime\python.exe -X utf8 -m unittest discover -s tests -v
.\runtime\python.exe -I -X utf8 tests\run_mcp.py
npm run build
```

CI installs the Python packages on Windows and builds the frontend separately. CI does not use a real API key.

## Native UI

The 0.1.3 registration regression passes 31 unit tests. tests/registration-lifecycle.mjs exercises actual Tauri RPC with normal and Windows namespace paths, a re-formatted host configuration, desktop exit/restart, and automatic status refresh. Tests use isolated state and never change the everyday Codex registration.

The native registration fixture passed. A separate live check completely exited the installed management application, confirmed its GUI process was absent, and then completed a new official DeepSeek read-only delegation through the existing Codex MCP. This verifies delegation remains available while the management application is closed; it does not establish the cause of an unrelated Codex chat stall.

tests/native-compact-ui.mjs checks the actual Tauri/WebView2 application. Use an isolated CODEXRELAY_HOME, the fixtures required by the script, and the test-only WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS remote debugging port. Inspect the script before running: it is an integration fixture, not a test against your everyday application data.

## Optional real-provider checks

tests/run_live.py requires DEEPSEEK_API_KEY in the process environment and consumes provider quota. It creates disposable workspaces under .tmp/ and cleans up temporary credential entries.

tests/verify_thinking_compaction.py is a focused real-provider protocol check using configured credentials. It also consumes quota. Keep keys and raw sensitive provider output out of source control and Issue reports.

After any coding test, review actual diffs and recovery records. Cancellation preserves changes; it is not rollback.

## Usage development and backend probes (2026-10-13)

The initial U1 development milestone passed 67 unit tests, including 12 usage-specific cases, plus the then-current 12-tool local stdio MCP regression. Usage arithmetic, missing/partial data, settlement dates, frozen prices, explicit backfill and 10,001-task aggregation were covered. The latter took about 0.11–0.12 seconds on the development machine. Final 0.2.0 results appear below.

After `npm run build`, `node tests/usage-ui.mjs` uses installed Chrome and the production frontend with actual per-request Python RPC in an isolated state directory. It checks price entry, historical backfill, signed comparison, cache reuse/invalidation and task-detail navigation at 900 × 600. This is a browser-plus-RPC integration check, not a new native Tauri or installer certification. See [implementation and limits](USAGE-MVP.md).

`tests/probe_backends.py` is a separate optional B1 experiment requiring pinned candidate packages under `.tmp/backend-probe`. It uses only a local fake model endpoint. See [candidate versions, results and unverified gates](BACKEND-EVALUATION.md); these probes do not enable a second production backend.

## 0.2.0 multi-backend and request/log usage

The production Pi adapter now uses the bundled Agent SDK with the existing Python provider/tool boundaries. `test_pi_backend.py` covers real SDK scheduling, writes, scope, cancellation, cross-backend leases, crash recovery and Windows namespace paths. `test_usage_sources.py` covers response-event persistence and opt-in direct/cumulative log parsing, replay, truncation, cache pricing and bounded oversized-line handling. The final full regression passed 113 cases with no skips, including recovery after failed bookkeeping, atomic backend-probe admission, required output prices and nonfinite response times. See the [final build and installed-runtime verification](RELEASE-0.2.0.md).

`tests/run_mcp.py` now checks 24 tools, both backends, legacy routing, review/transaction acknowledgement and cross-backend cancellation. `tests/native-roadmap.mjs` drives the real built Tauri application and bundled Python/Pi with isolated state and a local HTTP model. It also tests log opt-in/stop/clear, per-model prices and managed-rule upgrades, with system-only PATH for the application. Fixtures stay under `.tmp/roadmap-native-*` for inspection. See [acceptance mapping and remaining environment checks](ROADMAP-0.2.0.md).
