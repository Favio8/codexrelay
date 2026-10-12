# Contributing

Open an Issue with a reproducible problem or proposed change. Include the application version, Windows version, provider type, expected behavior and relevant redacted error. Never include API keys or private task content.

For a pull request, explain the resulting behavior and relevant validation. Keep changes focused. Use English commit messages in `emoji type: description` format, for example `✨ feat: add provider filtering`.

Follow [development instructions](docs/DEVELOPMENT.md) and run checks appropriate to the change. CI does not call paid APIs. Real-provider tests require explicit credentials and consume quota. Review file diffs after delegated coding and before submitting.

The vendored execution engine has its own upstream history and MIT license. Record substantive vendor changes in its UPSTREAM.md. Contributions are licensed under the project's MIT license.
