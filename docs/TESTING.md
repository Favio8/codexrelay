# Testing and verified scope

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
