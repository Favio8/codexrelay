# Development and builds

## Prerequisites

Use Windows x64, Node.js 22 LTS, Rust with the MSVC target, Visual Studio C++ build tools / Windows SDK, and a redistributable standalone Python 3.12 distribution. The published build uses python-build-standalone 3.12.13.

```powershell
npm ci
.\scripts\prepare-runtime.ps1 -Python 'C:\path\to\standalone-python\python.exe'
.\scripts\prepare-pi-runtime.ps1
npm run desktop:dev
```

prepare-runtime.ps1 copies the interpreter's base distribution into runtime/, installs hash-pinned Python dependencies, copies the application and vendored engine, and verifies imports. End users receive this runtime in the installer.

After Python source changes, run scripts/sync-engine.ps1. After dependency changes, update the dependency lock and prepare the runtime again.

The Pi SDK uses its own locked dependency manifest under vendor/pi-agent. prepare-pi-runtime.ps1 verifies the pinned Node archive, bundles the SDK bridge and retains licenses; it does not install Node globally. The application invokes the bundled executable by absolute path. After changing only Python code, synchronize the runtime and use `npm run tauri -- bundle --bundles nsis` to repackage an already-built desktop executable. Frontend/native changes require a new desktop build.

## Build the installer

```powershell
.\scripts\build.ps1
```

For a portable MSVC toolchain, pass `-Toolchain 'D:\path\to\msvc'`. The script sets build-process environment variables; it does not install a global toolchain.

The NSIS installer is produced under src-tauri/target/release/bundle/nsis/. The build generates release license notices from the locked npm/Cargo dependencies and copies project licenses into the bundled runtime.

Since 0.2.2, NSIS uses `src-tauri/installer.nsi`, pinned to the CLI 2.12.2 upstream template, plus `hooks.nsh` and `scripts/installer-maintenance.ps1` for in-place upgrades. After changing those files or bundled Python, repackage with `npm run tauri -- bundle --bundles nsis`. `scripts/update-installed.ps1` runs that same installer. Run the isolated installer fixtures before applying a maintenance update; template-only builds are insufficient to verify an upgrade with old processes alive.

## Layout

| Directory | Responsibility |
| --- | --- |
| desktop/ | React UI and typed desktop bridge |
| src-tauri/ | Rust host, tray, installer and uninstall hooks |
| engine/codexrelay/ | Controls, credentials, state, integration and MCP adapter |
| vendor/deepseek-as-subagent/ | Pinned execution engine and dependency lock |
| scripts/ | Runtime preparation, build and local maintenance |
| tests/ | Unit, protocol, native UI and opt-in live checks |

runtime/, build output, local evidence and machine-specific working documents are ignored. Never commit a live user configuration or credentials.

## Isolated testing

Set CODEXRELAY_HOME to a disposable application-data directory. Keep coding fixtures outside runtime/: the engine rejects a workspace containing its own runtime. Test-only WebView2 debugging must not become a production default.

See [testing](TESTING.md) for commands. The actual application icon is desktop/assets/codexrelay-icon.png; Tauri bundle icons are checked in under src-tauri/icons/.

## 0.3.0 execution iteration

After source changes synchronize Python and rebuild the Pi bridge if JavaScript changed. The existing preparation script now keeps Node/LICENSE files whose hashes match the pinned archive, avoiding unnecessary replacement of an in-use Node executable. Changes to the desktop still require a native rebuild; Python-only changes can repackage the built binary. See [schema 4, API and implementation decisions](EXECUTION-OPTIMIZATION.md). Run the protocol and isolated upgrade fixtures before applying an installer; this local delivery does not authorize everyday installation.

## 0.3.1 release artifacts

`tests/package_execution_patch.py` assembles the verified installer, complete source, evidence, dependency notices and SHA256 file. It needs the retained local 0.3.0 and 0.3.1 evidence; a fresh clone does not fabricate those records. Public assets are under the [v0.3.1 GitHub Release](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1). Acceptance tables name paths inside the evidence ZIP, so all listed records can be inspected without the development machine's temporary directories. Paid-model experiment scripts require separate authorization and must not be invoked as routine release checks.

## 0.4.0 release packaging

Use `scripts/sync-engine.ps1` before subprocess/stdio/native checks: the bundled interpreter imports its installed workspace copy, not necessarily `engine/` or `vendor/`. Schema 5 is additive. The independent grader and original case JSON are bundled as package resources. Never replace a graded workspace's scorer or reuse a started trial identity.

`tests/run_reliability_mcp.py` crosses real stdio, frozen trial/group/client identities, both execution backends, the per-request authorization boundary and fixed independent grading. `tests/run_desktop_ui.mjs` owns the isolated native fixture required by `desktop-ui.mjs` and refuses an unowned debugging port. Native fixtures must run serially because of the application's single-instance policy.

After verification, `tests/package_reliability_delivery.py` packages the actual dirty workspace, including pre-existing user edits and new files, alongside the local NSIS installer, evidence and SHA256 checks. It requires an explicit verification receipt and validates ZIP member hashes. It does not commit, publish or install the app. See [the acceptance matrix](DELEGATION-ACCEPTANCE.md).

Public releases use `tests/package_public_release.py` after committing: the source ZIP comes from the exact Git commit, while runtime build inputs are matched with explicit line-ending normalization. The public evidence ZIP excludes private host configuration and screenshots, replaces local paths, updates redacted-file hashes, and keeps the 19/7/1 coverage boundary. Local originals are preserved.

Use `scripts/build.ps1 -Toolchain <toolchain> -Public -NoBundle` to remap native build paths. Before bundling, remove only verified workspace runtime bytecode caches, then run the native/upgrade checks appropriate to the changed artifact. Include generated license text and required dependency source archives. `release-manifest.json` records provenance and final asset hashes. Publishing a GitHub Release does not update the everyday installation.
