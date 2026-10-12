# Third-party software

CodexRelay's own code is MIT licensed. Dependencies retain their own licenses.

- **deepseek-as-subagent**, by PsChina: MIT. Vendored source and original license are in [vendor/deepseek-as-subagent](vendor/deepseek-as-subagent/LICENSE); provenance and patches are recorded in UPSTREAM.md.
- **CPython**: Python Software Foundation license and bundled component notices. The Windows distribution includes Python's LICENSE.txt.
- **Python dependencies**: versions and integrity hashes are pinned in vendor/deepseek-as-subagent/requirements.lock. Their installed metadata and license files accompany the bundled runtime.
- **Frontend and native dependencies**: npm and Cargo lockfiles identify the versions. Release builds generate THIRD-PARTY-NOTICES.txt and bundled frontend/native license texts in the runtime directory.
- **WebView2**: Microsoft runtime installed separately when needed; its terms remain those of Microsoft.

The generated release notices describe the particular installer, including dependency versions. Development-only dependencies are not claimed to be runtime components.
