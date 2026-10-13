"""Per-task file scope, independent of the project execution lease."""
from pathlib import Path

from .safety import SandboxViolation, resolve_safe_path


def execution_root(config) -> Path:
    return config.task_root or config.workspace


def scoped_arguments(name: str, arguments: dict, config) -> dict:
    if (config.task_root is None and config.write_paths is None) or name == 'Bash':
        return arguments
    result = dict(arguments)
    value = result.get('path', '')
    if name not in {'Read', 'Write', 'Edit', 'NotebookEdit', 'Glob', 'Grep'}:
        return result
    if not isinstance(value, str):
        raise SandboxViolation('path must be a string')
    root = execution_root(config)
    path = Path(value) if value else root
    if not path.is_absolute():
        path = root / path
    path = resolve_safe_path(str(path), config.workspace)
    if not path.is_relative_to(root):
        raise SandboxViolation('path is outside the task root')
    if name in {'Write', 'Edit', 'NotebookEdit'} and config.write_paths is not None and path not in config.write_paths:
        raise SandboxViolation('path is not in the allowed write_paths')
    result['path'] = str(path)
    return result


def scope_violations(config, observed: dict) -> list[str]:
    root = execution_root(config)
    affected = observed['added'] + observed['deleted'] + observed['modified']
    return [name for name in affected
            if not (config.workspace / name).is_relative_to(root)
            or config.write_paths is not None and (config.workspace / name) not in config.write_paths]
