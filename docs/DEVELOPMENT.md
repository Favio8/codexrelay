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
