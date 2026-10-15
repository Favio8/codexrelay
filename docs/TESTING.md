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

## 0.3.0 execution optimization

`test_execution_optimization.py` covers bounded reads, summaries/epochs, Pi state synchronization, checkpoint integrity, independent Run counters, grants, stale files and task contracts. `run_execution_mcp.py` uses real stdio/tools/Pi with fixed responses for budget-stop -> partial verification -> remaining-only continuation. `native-roadmap.mjs` now checks defaults and checkpoint presentation with actual Tauri RPC. `installer-execution-upgrade.mjs` uses the exact installed 0.2.2 runtime in an isolated install scope.

`run_execution_quality.py --authorized` and `--focused` are paid opt-in experiments and must not be replayed without authorization. Fixed-provider responses are not model quality. The 54-row evidence map and uncovered conditions are in [the acceptance report](EXECUTION-ACCEPTANCE.md). Evidence is retained under `artifacts/evidence/0.3.0/`, while install/browser fixtures stay under `.tmp/`.

## 0.3.1 focused execution fix

The full regression passes 170 tests. New cases cover an adaptive 2,000-character long-line preview with exact pagination, both schedulers completing Read/Grep/Edit/verify with thinking protocol and a 32,768-token fixture budget, and a budget notice before 80% when repeated input would consume the remaining space. Fixed responses exercise mechanisms; they do not establish model quality.

The separately authorized `run_execution_quality.py --authorized --focused --low-budget-validation` ran the original minified JSON task once on each backend with the same real model, high reasoning, 32,768 budget and 2,048 output limit. Both executed to a final report. `verify_low_budget_fix.py` independently compared exact bytes, all 900 items, tool outputs, mutation hashes and Pi workspace checks before accepting the results. Evidence: `artifacts/evidence/0.3.1/real-low-budget-validation.json` and `parent-low-budget-audit.json`. These are two measured samples, not a general completion-rate claim. Prior successful real summary reconstruction evidence remains under `0.3.0/real-summary-ordinary-validation.json`.

## 0.4.0 reliability candidate

The local originals remain in `artifacts/evidence/0.4.0/`; public copies of the verification receipt and initial failures are in the [release evidence archive](https://github.com/Favio8/codexrelay/releases/download/v0.4.0/CodexRelay_0.4.0_evidence.zip). Paths are replaced, private host records and screenshots are omitted, and the public evidence index is regenerated. The [27-item acceptance matrix](DELEGATION-ACCEPTANCE.md) distinguishes implementation, local evidence and untested real-client conditions. The frozen 45-trial material is prepared only, with no paid authorization or real model requests.

After synchronization, use the full unit suite and real stdio fixtures `run_mcp.py`, `run_execution_mcp.py`, `run_reliability_mcp.py` and `run_lifecycle.py`. All use local deterministic providers. Production frontend/RPC fixtures include `connection-settings-ui.mjs`, `integration-reliability-ui.mjs`, `evaluation-ui.mjs`, `compact-settings-ui.mjs` and `usage-ui.mjs`. `task-evidence-ui.mjs` only verifies rendering and must be labeled accordingly.

Native checks are `run_desktop_ui.mjs` and `native-roadmap.mjs`; isolated installation/upgrading uses `installer-execution-upgrade.mjs`. The upgrade fixture compares installed Python/MJS/JSON resources, including frozen cases, and the native bundle marker. Native/installer tests never target the everyday registration or installation. Windows cancellation tests require complete descendant exit before reporting cleanup success and verify immediate cwd removal rather than masking a lock with a test delay.
