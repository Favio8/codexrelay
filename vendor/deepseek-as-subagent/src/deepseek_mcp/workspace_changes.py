"""Observed file changes, including shell changes; not an atomic transaction log."""
import hashlib
import os
from pathlib import Path

IGNORED_DIRECTORIES = {'.git', '.hg', '.svn', '__pycache__', '.pytest_cache', '.ruff_cache'}


def file_metadata(root: Path):
    errors = []
    entries = {}
    def onerror(error):
        errors.append(str(Path(error.filename or root).relative_to(root)))
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        links = {name for name in dirs if name not in IGNORED_DIRECTORIES
                 and ((Path(directory) / name).is_symlink() or (Path(directory) / name).is_junction())}
        errors.extend(str((Path(directory) / name).relative_to(root)) for name in links)
        dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRECTORIES and name not in links)
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                errors.append(str(path.relative_to(root)))
                continue
            try:
                stat = path.stat()
                entries[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            except OSError:
                errors.append(str(path.relative_to(root)))
    return entries, errors


def workspace_revision(root: Path) -> str:
    entries, errors = file_metadata(root)
    return hashlib.sha256(repr((entries, errors)).encode()).hexdigest()


def snapshot(root: Path):
    entries, errors = file_metadata(root)
    hashes = {}
    for name in entries:
        try:
            with (root / name).open('rb') as stream:
                hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError:
            errors.append(name)
    return hashes, errors, entries


def changes(root: Path, before) -> dict:
    entries, errors = file_metadata(root)
    original, prior_errors, metadata = before
    after = {}
    for name, version in entries.items():
        if name in original and metadata.get(name) == version:
            after[name] = original[name]
        else:
            try:
                with (root / name).open('rb') as stream:
                    after[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            except OSError:
                errors.append(name)
    unreadable = sorted(set(errors + prior_errors))
    def known(name):
        return not any(error == '.' or name == error or Path(error) in Path(name).parents for error in unreadable)
    return {
        'root': str(root), 'kind': 'observed_diff',
        'method': 'metadata_then_sha256',
        'added': sorted(name for name in after.keys() - metadata.keys() if known(name)),
        'deleted': sorted(name for name in metadata.keys() - entries.keys() if known(name)),
        'modified': sorted(name for name in original.keys() & after.keys() if original[name] != after[name]),
        'complete': not (errors or prior_errors),
        'unreadable': unreadable,
        'excluded_directories': sorted(IGNORED_DIRECTORIES),
    }
