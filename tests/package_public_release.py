"""Build public assets from a committed ref and independently verified local outputs.

Keeps local originals. Never uploads, changes a registration, or runs a provider.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_EVIDENCE = {
    'daily-integration-readonly.json', 'daily-repair-preview-initial.json',
    'daily-repair-preview.json', 'daily-state-preserved.json', 'integration-local-readonly.json',
}
REGENERATED = {'evidence-index.json', 'source-hashes.json', 'packaging.log', 'public-export.json'}
PUBLIC_EVIDENCE = {
    'acceptance-27.json', 'delivery-verification.json', 'unit-tests-final.log', 'unit-tests-initial.log',
    'evaluation-final-identity.log', 'evaluation-deterministic-report.json', 'evaluation-ui.json',
    'integration-ui.json', 'connection-ui.json', 'compact-settings-ui.json', 'usage-ui.json',
    'task-evidence-ui.log', 'run_mcp-final.log', 'run_execution_mcp-final.log', 'run_lifecycle-final.log',
    'reliability-mcp.json', 'reliability-mcp.log', 'reliability-mcp-initial.log', 'reliability-mcp-write-fixture.log',
    'prepared-45-manifest.json', 'prepared-45-instructions.json', 'native-desktop-ui.json',
    'native-desktop-ui.log', 'native-roadmap.json', 'native-rpc-matrix.log',
    'installer-0.2.2.json', 'installer-0.3.1.json', 'public-native-smoke.log', 'public-installer-upgrade.log',
    'public-native-build.log', 'public-bundle.log', 'license-completeness.json', 'license-notices-tests.log',
    'public-runtime-preparation.json', 'public-build-verification.json', 'public-release-tests.log',
    'public-fresh-install-scan.json', 'public-native-result.json',
}
PINNED_INPUTS = {
    'src-tauri/Cargo.toml', 'src-tauri/Cargo.lock', 'src-tauri/tauri.conf.json',
    'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json',
    'vendor/pi-agent/package.json', 'vendor/pi-agent/package-lock.json', 'vendor/pi-agent/node-distribution.json',
    'scripts/build.ps1', 'scripts/generate-notices.py', 'scripts/prepare-public-runtime.py',
    'vendor/licenses/manifest.json',
}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def prefixes(repo=ROOT, profile=None):
    values = []
    for source, label in ((str(repo), '<workspace>'), (str(profile or Path.home()), '<user-home>')):
        variants = {source, source.replace('\\', '/'), source.replace('/', '\\')}
        for value in list(variants):
            variants.update({value.replace('\\', '\\\\'), value.replace('\\', '\\\\\\\\')})
        values.extend((value, label) for value in sorted(variants, key=len, reverse=True) if len(value) > 3)
    return values


def redact_text(value, replacements):
    for before, after in replacements:
        value = re.sub(re.escape(before), lambda _: after, value, flags=re.I)
    for name in PRIVATE_EVIDENCE:
        value = value.replace(name, 'private-host-observation-omitted')
    return value


def redact_json(value, replacements):
    if isinstance(value, str):
        return redact_text(value, replacements)
    if isinstance(value, list):
        return [redact_json(item, replacements) for item in value]
    if isinstance(value, dict):
        # Some original file-version maps use absolute paths as keys.
        return {redact_text(str(key), replacements): redact_json(item, replacements) for key, item in value.items()}
    return value


def private_matches(data, replacements):
    matches = []
    for before, label in replacements:
        for encoding in ('utf-8', 'utf-16-le'):
            if before.encode(encoding).lower() in data.lower():
                matches.append(label + ':' + encoding)
    return sorted(set(matches))


def public_evidence(directory, replacements):
    """Return a self-consistent public copy, with no private host observation."""
    outputs, origins, excluded = {}, {}, []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.name not in PUBLIC_EVIDENCE or path.name in PRIVATE_EVIDENCE or path.name in REGENERATED:
            excluded.append(path.name)
            continue
        raw = path.read_bytes()
        if path.suffix == '.json':
            value = json.loads(raw.decode('utf-8-sig'))
            if path.name == 'delivery-verification.json':
                value = dict(value)
                for key in ('daily_state', 'daily_repair', 'remaining_authorizations'):
                    value.pop(key, None)
                value['scope'] = 'Historical local verification snapshot, before the release commit; not an assertion of real-client completion.'
                value['private_host_records'] = 'omitted from public export'
            value = redact_json(value, replacements)
            content = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
        else:
            content = redact_text(raw.decode('utf-8-sig'), replacements).encode('utf-8')
        outputs[path.name] = content
        origins[path.name] = {'original_local_sha256': digest(raw)}
    summary_name = 'evaluation-deterministic-report.json'
    if summary_name in outputs:
        summary = json.loads(outputs[summary_name])
        prepared = summary.get('prepared_45', {})
        for key, public_name, hash_key in (
            ('manifest', 'prepared-45-manifest.json', 'manifest_file_sha256'),
            ('instructions', 'prepared-45-instructions.json', 'instructions_sha256'),
        ):
            if public_name in outputs:
                prepared['original_local_' + hash_key] = prepared.get(hash_key)
                prepared[key] = public_name
                prepared[hash_key] = digest(outputs[public_name])
        outputs[summary_name] = json.dumps(summary, ensure_ascii=False, indent=2).encode('utf-8')
    export = dict(kind='public_redacted_copy', model_requests_added=0,
                  transformations=['Private host observations and screenshots omitted.',
                                   'Local prefixes replaced in JSON values, map keys and logs.',
                                   'Public prepared-file hashes and evidence index recomputed.'],
                  excluded_files=excluded, files=origins)
    outputs['public-export.json'] = json.dumps(export, ensure_ascii=False, indent=2).encode('utf-8')
    for name, data in outputs.items():
        if private_matches(data, replacements):
            raise ValueError('Unredacted build prefix in public evidence: ' + name)
    index = {name: {'bytes': len(data), 'sha256': digest(data)} for name, data in sorted(outputs.items())}
    outputs['evidence-index.json'] = json.dumps(index, indent=2).encode('utf-8')
    return outputs


def verified_sources(source_zip, original_hashes):
    """Relate tested build inputs to Git's normalized text, without changing history."""
    mappings = []
    with zipfile.ZipFile(source_zip) as archive:
        names = archive.namelist()
        for name in names:
            if any(part in {'.git', '.tmp', 'node_modules', '__pycache__', 'runtime'} for part in Path(name).parts):
                raise ValueError('Private/generated source member: ' + name)
        for name, expected in original_hashes.items():
            if name not in PINNED_INPUTS and not name.startswith(('engine/codexrelay/', 'vendor/deepseek-as-subagent/src/', 'desktop/', 'src-tauri/src/')):
                continue
            raw = (ROOT / name).read_bytes()
            published = archive.read(name)
            if digest(raw) != expected:
                raise ValueError('Verified build input changed: ' + name)
            if raw != published and raw.replace(b'\r\n', b'\n') != published.replace(b'\r\n', b'\n'):
                raise ValueError('Release source differs from tested input: ' + name)
            mappings.append(dict(path=name, tested_workspace_sha256=expected, published_git_sha256=digest(published),
                                 normalization='identical' if raw == published else 'line_endings_only'))
        manifest = {name: digest(archive.read(name)) for name in names if not name.endswith('/')}
    return manifest, mappings


def package(ref='HEAD'):
    git = lambda *args: subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    if git('status', '--porcelain'):
        raise ValueError('Commit reviewed source before public packaging')
    commit = git('rev-parse', '--verify', '--end-of-options', ref + '^{commit}')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid release commit')
    if commit != git('rev-parse', 'HEAD'):
        raise ValueError('Package the checked-out release commit; a different ref could mix source and assets')
    version = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
    evidence = ROOT / 'artifacts/evidence' / version
    verification = json.loads((evidence / 'public-build-verification.json').read_text(encoding='utf-8'))
    if not verification.get('ok'):
        raise ValueError('A verified public build is required')
    destination = ROOT / 'artifacts/releases' / version
    destination.mkdir(parents=True, exist_ok=True)
    source = destination / f'CodexRelay_{version}_source.zip'
    subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(source), commit], cwd=ROOT, check=True)
    replacements = prefixes()
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if not name.endswith('/') and private_matches(archive.read(name), replacements):
                raise ValueError('Private prefix in committed source: ' + name)
    originals = json.loads((evidence / 'source-hashes.json').read_text(encoding='utf-8'))
    for name, expected in verification.get('build_input_hashes', {}).items():
        if name not in PINNED_INPUTS:
            raise ValueError('Unexpected public build input: ' + name)
        originals[name] = expected
    manifest, mappings = verified_sources(source, originals)
    (destination / 'SOURCE_MANIFEST.json').write_text(json.dumps(dict(commit=commit, files=manifest), indent=2), encoding='utf-8')
    (destination / 'BUILD_SOURCE_MANIFEST.json').write_text(json.dumps(dict(commit=commit, inputs=mappings), indent=2), encoding='utf-8')
    installer = ROOT / f'src-tauri/target/release/bundle/nsis/CodexRelay_{version}_x64-setup.exe'
    if digest(installer.read_bytes()) != verification['installer_sha256']:
        raise ValueError('Installer changed since public verification')
    shutil.copyfile(installer, destination / installer.name)
    public = public_evidence(evidence, replacements)
    proof = destination / f'CodexRelay_{version}_evidence.zip'
    with zipfile.ZipFile(proof, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in public.items():
            archive.writestr(name, data)
    assets = [(ROOT / 'LICENSE', 'LICENSE'), (ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
              (ROOT / 'runtime/THIRD-PARTY-NOTICES.txt', 'THIRD-PARTY-NOTICES.txt'),
              (ROOT / 'runtime/pi/THIRD_PARTY_LICENSES.txt', 'PI-THIRD-PARTY-LICENSES.txt'),
              (ROOT / 'runtime/pi/LICENSE', 'NODE-LICENSE.txt'), (ROOT / 'runtime/LICENSE.txt', 'PYTHON-LICENSE.txt')]
    assets.extend((ROOT / 'docs' / name, name) for name in ('RELEASE-0.4.0.md', 'DELEGATION-ACCEPTANCE.md', 'DELEGATION-EXPERIMENTS.md'))
    expected_names = {source.name, proof.name, installer.name, 'SOURCE_MANIFEST.json', 'BUILD_SOURCE_MANIFEST.json',
                      'release-manifest.json', 'SHA256SUMS.txt', *(name for _, name in assets)}
    unexpected = {path.name for path in destination.iterdir()} - expected_names
    if unexpected:
        raise ValueError('Unreviewed files in public asset directory: ' + ', '.join(sorted(unexpected)))
    for original, name in assets:
        data = original.read_bytes()
        if private_matches(data, replacements):
            raise ValueError('Private prefix in release asset: ' + name)
        shutil.copyfile(original, destination / name)
    for archive_path in (source, proof):
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip():
                raise ValueError('ZIP integrity check failed')
    files = {p.name: dict(bytes=p.stat().st_size, sha256=digest(p.read_bytes())) for p in sorted(destination.iterdir())
             if p.is_file() and p.name not in {'SHA256SUMS.txt', 'release-manifest.json'}}
    release = dict(version=version, commit=commit, tag='v' + version, prerelease=True, files=files,
                   build_source_scope='business source, desktop/native source and pinned build/dependency configuration',
                   local_verification_snapshot='delivery-verification.json in the evidence archive',
                   public_build_verification='public-build-verification.json in the evidence archive',
                   coverage={'locally_verified': 19, 'partially_verified': 7, 'real_client_condition_not_run': 1},
                   paid_experiments=0, prepared_trials=45, real_net_benefit='unknown')
    (destination / 'release-manifest.json').write_text(json.dumps(release, indent=2), encoding='utf-8')
    files['release-manifest.json'] = {'sha256': digest((destination / 'release-manifest.json').read_bytes())}
    (destination / 'SHA256SUMS.txt').write_text(''.join(f"{record['sha256']}  {name}\n" for name, record in sorted(files.items())), encoding='utf-8')
    print(json.dumps(dict(commit=commit, output=str(destination), assets=len(files) + 1, source_files=len(manifest),
                          public_evidence_files=len(public), installer_sha256=files[installer.name]['sha256']), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    package(parser.parse_args().ref)
