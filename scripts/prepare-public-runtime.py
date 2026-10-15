"""Remove generated build paths from this workspace's bundled runtime only."""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def markers(workspace, profile):
    values = {str(workspace), workspace.as_posix(), str(profile), profile.as_posix()}
    for value in list(values):
        values.update({value.replace('\\', '\\\\'), value.replace('\\', '\\\\\\\\')})
    return [value.encode(encoding).lower() for value in values for encoding in ('utf-8', 'utf-16-le')]


def prepare(workspace, *, check_only=False, profile=None):
    requested_workspace = Path(workspace).absolute()
    workspace = requested_workspace.resolve(strict=True)
    runtime = workspace / 'runtime'
    if runtime.is_symlink() or not runtime.is_dir() or not runtime.resolve().is_relative_to(workspace):
        raise ValueError('Expected an owned runtime directory below the workspace')
    runtime = runtime.resolve()
    requested_profile = Path(profile or Path.home()).absolute()
    # Canonical paths enforce the deletion boundary; raw aliases still occur
    # in generated metadata (Windows short names, junctions and caller paths).
    needles = markers(workspace, requested_profile.resolve()) + markers(requested_workspace, requested_profile)
    removed, launchers = 0, []
    if not check_only:
        for path in list(runtime.rglob('__pycache__')):
            if not path.resolve().is_relative_to(runtime) or path.is_symlink():
                raise ValueError('Unexpected cache target')
            if path.is_dir():
                shutil.rmtree(path)
                removed += 1
        for path in list(runtime.rglob('*.pyc')) + list(runtime.rglob('*.pyo')):
            if not path.resolve().is_relative_to(runtime):
                raise ValueError('Unexpected bytecode target')
            path.unlink()
            removed += 1
        # pip's generated CLI launchers embed an absolute interpreter shebang.
        # The app invokes its interpreter/modules directly; these wrappers are unused.
        for path in (runtime / 'Lib/site-packages/bin').glob('*.exe'):
            if not path.resolve().is_relative_to(runtime):
                raise ValueError('Unexpected entry-point launcher target')
            data = path.read_bytes().lower()
            if any(value in data for value in needles):
                launchers.append(path.relative_to(runtime).as_posix())
                path.unlink()
    leaks = []
    for path in runtime.rglob('*'):
        if path.is_file():
            if not path.resolve().is_relative_to(runtime):
                raise ValueError('Unexpected runtime file target')
            data = path.read_bytes().lower()
            if any(value in data for value in needles):
                leaks.append(path.relative_to(runtime).as_posix())
    result = dict(removed_cache_entries=removed, unused_generated_launchers_removed=launchers,
                  bytecode_files_remaining=len(list(runtime.rglob('*.pyc'))) + len(list(runtime.rglob('*.pyo'))),
                  private_prefix_files=leaks, path_policy='workspace/user prefixes excluded; upstream attribution retained')
    if leaks or result['bytecode_files_remaining']:
        raise ValueError('Public runtime still contains local build metadata: ' + json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--report')
    args = parser.parse_args()
    result = prepare(ROOT, check_only=args.check)
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
