"""Package the current uncommitted local delivery without publishing or installing."""
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    version=json.loads((ROOT/'package.json').read_text(encoding='utf-8'))['version']
    evidence=ROOT/'artifacts/evidence'/version
    receipt=json.loads((evidence/'delivery-verification.json').read_text(encoding='utf-8'))
    if not receipt.get('local_delivery_verified'):
        raise ValueError('A completed, honest local verification receipt is required')
    target=ROOT/'artifacts/delivery'/version;target.mkdir(parents=True,exist_ok=True)
    installer=ROOT/f'src-tauri/target/release/bundle/nsis/CodexRelay_{version}_x64-setup.exe'
    installed=target/installer.name;shutil.copyfile(installer,installed)
    # Include existing user edits and new files; a HEAD archive would omit this work.
    paths=set()
    for args in (['git','ls-files','-z'],['git','ls-files','--others','--exclude-standard','-z']):
        result=subprocess.run(args,cwd=ROOT,capture_output=True,check=True)
        paths.update(p for p in result.stdout.decode('utf-8').split('\0') if p)
    files=[ROOT/p for p in sorted(paths) if (ROOT/p).is_file()]
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in files}
    (evidence/'source-hashes.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf-8')
    source=target/f'CodexRelay_{version}_source.zip'
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in files:archive.write(file,file.relative_to(ROOT).as_posix())
    proof=target/f'CodexRelay_{version}_evidence.zip'
    with zipfile.ZipFile(proof,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(evidence.rglob('*')):
            if file.is_file():archive.write(file,file.relative_to(evidence).as_posix())
    for file in (source,proof):
        with zipfile.ZipFile(file) as archive:
            bad=archive.testzip()
            if bad:raise ValueError('Corrupt ZIP member: '+bad)
    with zipfile.ZipFile(source) as archive:
        for name,expected in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=expected:
                raise ValueError('Source package content mismatch: '+name)
    outputs=[installed,source,proof]
    (target/'SHA256SUMS.txt').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in outputs),encoding='utf-8')
    report=dict(version=version,uncommitted_workspace=True,source_files=len(files),
                files=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in outputs],
                zip_contents_verified=True,published=False,everyday_installation_changed=False)
    (target/'delivery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
