"""Collect release dependency notices from installed, locked build inputs."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def license_files(directory):
    candidates = list(directory.iterdir())
    for name in ("licenses", "LICENSES"):
        child = directory / name
        if child.is_dir():
            candidates.extend(child.iterdir())
    return sorted(
        p for p in candidates if p.is_file()
        and p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))
    )


def render_component(name, version, license_id, directory):
    lines = [f"\n{'=' * 72}\n{name} {version}\nLicense: {license_id}\n"]
    for path in license_files(directory):
        lines.append(f"\n--- {path.name} ---\n")
        lines.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(lines)


def main():
    sections = ["CodexRelay third-party release notices\n",
                "Python and its installed dependencies retain the license files "
                "distributed in this runtime's LICENSE.txt and Lib/site-packages.\n"]
    npm_paths = subprocess.check_output(
        ["npm.cmd", "ls", "--omit=dev", "--all", "--parseable"],
        cwd=ROOT, text=True, encoding="utf-8",
    ).splitlines()
    seen = set()
    for raw in npm_paths:
        directory = Path(raw)
        if directory == ROOT:
            continue
        info = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        identity = (info["name"], info["version"])
        if identity not in seen:
            sections.append(render_component(*identity, info.get("license", "See upstream"), directory))
            seen.add(identity)
    cargo = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--filter-platform", "x86_64-pc-windows-msvc", "--format-version", "1",
         "--manifest-path", str(ROOT / "src-tauri" / "Cargo.toml")],
        cwd=ROOT, text=True, encoding="utf-8",
    ))
    graph = {node["id"]: node["dependencies"] for node in cargo["resolve"]["nodes"]}
    included = set()
    pending = [cargo["resolve"]["root"]]
    while pending:
        identity = pending.pop()
        if identity not in included:
            included.add(identity)
            pending.extend(graph.get(identity, []))
    packages = [p for p in cargo["packages"] if p["id"] in included]
    for info in sorted(packages, key=lambda p: (p["name"], p["version"])):
        if info["name"] == "codexrelay":
            continue
        sections.append(render_component(
            info["name"], info["version"], info.get("license") or "See upstream",
            Path(info["manifest_path"]).parent,
        ))
    destination = ROOT / "runtime" / "THIRD-PARTY-NOTICES.txt"
    destination.write_text("\n".join(sections), encoding="utf-8")
    print(f"Generated release notices: {len(seen)} npm packages, {len(packages) - 1} Rust packages")


if __name__ == "__main__":
    main()
