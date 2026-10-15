# Reviewed release license supplements

`manifest.json` binds each supplement to an exact Cargo package version, the
Cargo.lock crate checksum, its published `.cargo_vcs_info.json` commit, the
official source URL and the stored document SHA-256. Text keeps the original
license/attribution wording; only CRLF line endings are normalized to LF. The
original download SHA-256 is retained separately. No network request is needed
when generating release notices.

Seven Cargo package archives omit license files that exist at their
upstream repository roots. The exact-version repository-root texts are retained
here, including both alternatives for the MIT/Apache dual-licensed packages.
The selectors source file retains its own MPL 2.0 notice; the license text comes
from Mozilla, the license steward. No copyright attribution was invented.

The npm package `@tauri-apps/plugin-dialog` 2.8.2 includes SPDX attribution
metadata but omits the full license grants. Its official npm registry gitHead
pins the plugin's upstream MIT/Apache texts retained here. The exact published
tarball was verified against package-lock's SHA-512 integrity value. Its SPDX
attribution remains included alongside the complete license texts.

Cargo normal dependency traversal for the Windows target classifies
alloc-stdlib, selectors, webview2-com and webview2-com-sys as runtime dependency
packages. defmt-parser, tauri-plugin and webview2-com-macros belong to the host
build graph or procedural-macro processing. This is dependency provenance, not
a claim that every resolved crate contributes linked executable symbols.

`source-archives/` retains the **unmodified** original crates for the five
MPL-2.0 dependencies in the resolved graph. Every archive SHA-256 matches
Cargo.lock. Release notices identify these source archives, their primary
crates.io download URLs and their hashes. The notice generator copies the
archives to `runtime/third-party-sources/` so recipients can obtain Source Code
Form offline alongside the binary distribution. Each archive preserves its
original source headers and notices.

webview2-com-sys also statically links Microsoft's WebView2 SDK loader. The
pinned upstream updater identifies SDK 1.0.3800.47; the x64 static-loader bytes
in the official NuGet archive match the crate's link input exactly. Its own
LICENSE.txt and full NOTICE.txt are retained and appended separately from the
Rust wrapper's MIT license. The manifest records the SDK archive, member and
hashes; the NuGet archive itself is not redistributed here.

`scripts/generate-notices.py` refuses missing/changed reviewed documents,
unreviewed missing license text, mismatched package versions/checksums/commits,
changed SDK loader bytes and missing MPL source availability. The generated
evidence record is `artifacts/evidence/<version>/license-completeness.json`.
