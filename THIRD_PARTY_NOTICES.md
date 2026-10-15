# Third-party software

CodexRelay's own code is MIT licensed. Dependencies retain their own licenses.

- **deepseek-as-subagent**, by PsChina: MIT. Vendored source and original license are in [vendor/deepseek-as-subagent](vendor/deepseek-as-subagent/LICENSE); provenance and patches are recorded in UPSTREAM.md.
- **CPython**: Python Software Foundation license and bundled component notices. The Windows distribution includes Python's LICENSE.txt.
- **Python dependencies**: versions and integrity hashes are pinned in vendor/deepseek-as-subagent/requirements.lock. Their installed metadata and license files accompany the bundled runtime.
- **Frontend and native dependencies**: npm and Cargo lockfiles identify the versions. Release builds generate THIRD-PARTY-NOTICES.txt and bundled frontend/native license texts in the runtime directory.
- **WebView2**: Microsoft runtime installed separately when needed; its terms remain those of Microsoft.
- **Pi Agent SDK**: `@earendil-works/pi-agent-core` and `pi-ai` 1.2.0, MIT. Exact dependencies are pinned in `vendor/pi-agent/package-lock.json`; the upstream license is retained in `vendor/pi-agent/LICENSE`. Only the SDK bridge is bundled, without the Pi CLI, external plugins or its provider credential store.
- **Node.js**: the pinned Windows x64 distribution and SHA-256 are recorded in `vendor/pi-agent/node-distribution.json`. Its original LICENSE and the SDK bundle's component notices ship under `runtime/pi/` alongside a file-hash manifest.

The generated release notices describe the particular installer, including dependency versions. Development-only dependencies are not claimed to be runtime components.

Reviewed supplements, exact upstream revisions and document hashes are in [vendor/licenses](vendor/licenses/README.md). The release includes the matching Microsoft WebView2 SDK license/notice and unchanged Source Code Form archives for the resolved MPL components under `runtime/third-party-sources/`. Notice generation rejects unreviewed missing text or changed source artifacts.
