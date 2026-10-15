"""Collect release dependency notices from installed, locked build inputs."""

import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parent.parent
SUPPLEMENTS = ROOT / "vendor" / "licenses"


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


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def verified_file(directory, relative, expected):
    base = Path(directory).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file() or sha256(path.read_bytes()) != expected:
        raise ValueError("Missing or changed reviewed license/source file: " + relative)
    return path


def load_supplements(directory=SUPPLEMENTS):
    value = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if value.get('schema_version') != 1:
        raise ValueError('Unsupported reviewed license manifest')
    return value


def supplemental_documents(name, version, license_id, supplements, directory=SUPPLEMENTS, checksum=None, vcs=None, npm_integrity=None):
    component = next((item for item in supplements['components'] if (item['name'], item['version']) == (name, version)), None)
    if not component or component['license'] != license_id:
        raise ValueError(f'Missing license text: {name} {version} ({license_id}); obtain and review exact upstream text')
    if checksum is not None and checksum != component['crate_checksum']:
        raise ValueError('Reviewed license crate checksum differs: ' + name)
    if vcs is not None and vcs.get('git', {}).get('sha1') != component['upstream_commit']:
        raise ValueError('Reviewed license source revision differs: ' + name)
    if component.get('package_manager') == 'npm' and npm_integrity != component.get('npm_integrity'):
        raise ValueError('Reviewed npm integrity differs: ' + name)
    output = []
    for relative in component['documents']:
        document = supplements['documents'][relative]
        path = verified_file(directory, relative, document['sha256'])
        output.append((relative, path.read_text(encoding='utf-8'), document['source_url']))
    if not output:
        raise ValueError('Reviewed mapping has no license text: ' + name)
    return output


def render_component(name, version, license_id, directory, *, scope='runtime', supplements=None,
                     supplement_directory=SUPPLEMENTS, checksum=None, npm_integrity=None):
    lines = [f"\n{'=' * 72}\n{name} {version}\nLicense: {license_id}\nDependency scope: {scope}\n"]
    local_files = license_files(directory)
    # SPDX metadata alone identifies a license; it does not reproduce its text.
    texts = [path for path in local_files if path.suffix.lower() != '.spdx' and len(path.read_bytes().strip()) >= 100]
    if texts:
        documents = [(path.name, path.read_text(encoding='utf-8', errors='strict'), None) for path in local_files]
    else:
        vcs_file = directory / '.cargo_vcs_info.json'
        vcs = json.loads(vcs_file.read_text()) if vcs_file.is_file() else None
        documents = supplemental_documents(name, version, license_id, supplements or load_supplements(),
                                             supplement_directory, checksum, vcs, npm_integrity)
        documents.extend((path.name, path.read_text(encoding='utf-8'), None) for path in local_files)
    for label, text, source in documents:
        lines.append(f"\n--- {label} ---\n")
        if source:
            lines.append('Exact reviewed source: ' + source + '\n')
        lines.append(text)
    if 'MPL-2.0' in license_id:
        data = supplements or load_supplements()
        source = next((item for item in data['source_archives'] if (item['name'], item['version']) == (name, version)), None)
        if not source:
            raise ValueError(f'MPL source availability is not reviewed: {name} {version}')
        verified_file(supplement_directory, source['path'], source['sha256'])
        if checksum is not None and checksum != source['sha256']:
            raise ValueError('MPL source does not match Cargo.lock: ' + name)
        lines.append('\nSource Code Form (unchanged original crate): third-party-sources/' + Path(source['path']).name + '\n')
        lines.append('Also available from: ' + source['source_url'] + '\nSHA256: ' + source['sha256'] + '\n')
    return "\n".join(lines)


def runtime_dependency_ids(cargo):
    """Normal target dependency closure, excluding host procedural macros.

    Build graph notices are retained too, but are explicitly labelled rather
    than claiming every metadata package is a linked runtime component.
    """
    packages = {item['id']: item for item in cargo['packages']}
    nodes = {item['id']: item for item in cargo['resolve']['nodes']}
    included, pending = set(), [cargo['resolve']['root']]
    while pending:
        identifier = pending.pop()
        if identifier in included:
            continue
        included.add(identifier)
        for dep in nodes[identifier]['deps']:
            target = packages[dep['pkg']]
            if any(kind['kind'] is None for kind in dep['dep_kinds']) and not any('proc-macro' in item['kind'] for item in target['targets']):
                pending.append(dep['pkg'])
    return included


def main():
    supplements = load_supplements()
    sections = ["CodexRelay third-party release notices\n",
                "Python and its installed dependencies retain the license files "
                "distributed in this runtime's LICENSE.txt and Lib/site-packages.\n"]
    npm_paths = subprocess.check_output(
        ["npm.cmd", "ls", "--omit=dev", "--all", "--parseable"],
        cwd=ROOT, text=True, encoding="utf-8",
    ).splitlines()
    seen = set()
    npm_lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))['packages']
    for raw in npm_paths:
        directory = Path(raw)
        if directory == ROOT:
            continue
        info = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        identity = (info["name"], info["version"])
        if identity not in seen:
            sections.append(render_component(*identity, info.get("license", "See upstream"), directory, supplements=supplements,
                npm_integrity=npm_lock.get(directory.relative_to(ROOT).as_posix(), {}).get('integrity')))
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
    runtime_ids = runtime_dependency_ids(cargo)
    package_ids = {(p['name'], p['version']): p['id'] for p in packages}
    locked = {(item['name'], item['version']): item for item in tomllib.loads((ROOT / 'src-tauri/Cargo.lock').read_text(encoding='utf-8'))['package']}
    for info in sorted(packages, key=lambda p: (p["name"], p["version"])):
        if info["name"] == "codexrelay":
            continue
        sections.append(render_component(
            info["name"], info["version"], info.get("license") or "See upstream",
            Path(info["manifest_path"]).parent,
            scope='runtime' if info['id'] in runtime_ids else 'host/build graph or proc-macro; not classified as linked runtime',
            supplements=supplements, checksum=locked[(info['name'], info['version'])].get('checksum'),
        ))
    for component in supplements.get('embedded_components', []):
        owner = next((p for p in packages if (p['name'], p['version']) == (component['owner_crate'], component['owner_version'])), None)
        if not owner:
            continue
        loader = Path(owner['manifest_path']).parent / 'x64' / 'WebView2LoaderStatic.lib'
        if not loader.is_file() or sha256(loader.read_bytes()) != component['shipping_member_sha256']:
            raise ValueError('Embedded SDK loader differs from the reviewed exact SDK: ' + component['name'])
        lines = [f"\n{'=' * 72}\n{component['name']} {component['version']}\nEmbedded by: {component['owner_crate']} {component['owner_version']}\n",
                 'Exact SDK package: ' + component['sdk_source_url'] + '\n']
        for relative in component['documents']:
            doc = supplements['documents'][relative]
            path = verified_file(SUPPLEMENTS, relative, doc['sha256'])
            lines.extend(['\n--- ' + relative + ' ---\n', path.read_text(encoding='utf-8')])
        sections.append('\n'.join(lines))
    # Verify everything before replacing any shipped notice. Preserve MPL source
    # archives as ordinary redistributable license materials for offline access.
    sources = [(item, verified_file(SUPPLEMENTS, item['path'], item['sha256'])) for item in supplements['source_archives']]
    source_directory = ROOT / 'runtime/third-party-sources'
    source_directory.mkdir(exist_ok=True)
    for item, path in sources:
        shutil.copyfile(path, source_directory / path.name)
    destination = ROOT / "runtime" / "THIRD-PARTY-NOTICES.txt"
    temporary = destination.with_suffix('.txt.tmp')
    temporary.write_text("\n".join(sections), encoding="utf-8")
    temporary.replace(destination)
    version = json.loads((ROOT / 'package.json').read_text())['version']
    evidence = ROOT / 'artifacts/evidence' / version / 'license-completeness.json'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({'schema_version': 1, 'target': 'x86_64-pc-windows-msvc',
        'reviewed_manifest': 'vendor/licenses/manifest.json', 'reviewed_manifest_sha256': sha256((SUPPLEMENTS / 'manifest.json').read_bytes()),
        'runtime_rust_packages': len(runtime_ids) - 1, 'all_included_rust_packages': len(packages) - 1,
        'supplemented_components': [{**item, 'actual_scope': 'frontend runtime' if item.get('package_manager') == 'npm' else 'runtime' if package_ids[(item['name'],item['version'])] in runtime_ids else 'host/build graph or proc-macro'} for item in supplements['components']],
        'documents': supplements['documents'], 'embedded_components': supplements['embedded_components'],
        'mpl_source_archives': supplements['source_archives'], 'missing_license_texts': [],
        'notice_sha256': sha256(destination.read_bytes()), 'basis': 'exact input metadata/checksums and reviewed upstream license text; dependency scope is not a binary symbol audit'},
        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Generated release notices: {len(seen)} npm packages, {len(packages) - 1} Rust packages")


if __name__ == "__main__":
    main()
