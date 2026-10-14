"""OpenAI-compatible tool schemas kept separate from tool execution logic."""
from __future__ import annotations

from .bash_tool import DEFAULT_BASH_TIMEOUT, MAX_BASH_TIMEOUT

_TRUSTED_HOST_BASH = (
    "Run a bounded shell command on the trusted host in the workspace. "
    "The server freezes this backend before the task starts."
)

TOOL_SCHEMAS = {
    "Read": {
        "name": "Read",
        "description": "Read a bounded UTF-8 text range. For a focused edit in a large/minified file, prefer Grep of the target text; its snippet is sufficient inspection.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path within workspace."},
                "offset": {"type": "integer", "description": "Starting line, 0-indexed."},
                "limit": {"type": "integer", "description": "Number of lines to read."},
                "char_offset": {"type": "integer", "description": "Character offset within selected lines; use next_read for exact pagination."},
                "char_limit": {"type": "integer", "description": "Character cap, default 12000 (2000 for a long-line preview); independently bounded."},
                "full": {"type": "boolean", "description": "Explicit full-file request, still bounded by tool output limits."},
                "purpose": {"type": "string", "enum": ["inspect", "pre_edit", "verify"], "description": "Use pre_edit or verify to request a fresh check."},
            },
            "required": ["path"],
        },
    },
    "Write": {
        "name": "Write",
        "description": (
            "Create a new bounded UTF-8 file inside the workspace. "
            "Use Edit for an existing file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path within workspace."},
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
        },
    },
    "Edit": {
        "name": "Edit",
        "description": "Replace exact text in a bounded workspace file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string", "description": "Exact text to find."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "replace_all": {"type": "boolean", "description": "Replace every match."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    "Bash": {
        "name": "Bash",
        "description": (
            "Run a bounded program or shell command on the trusted host. Prefer executable/args/cwd for normal programs; use command for shell syntax. "
            f"Timeout defaults to {DEFAULT_BASH_TIMEOUT}s and is capped at {MAX_BASH_TIMEOUT}s."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command."},
                "executable": {"type": "string", "description": "Program path/name, executed directly without a shell."},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Literal program arguments; do not add shell quotes."},
                "cwd": {"type": "string", "description": "Optional working directory within the task root."},
                "timeout": {"type": "integer", "description": "Timeout in seconds."},
            },
            "anyOf": [{"required": ["command"]}, {"required": ["executable"]}],
        },
    },
    "Glob": {
        "name": "Glob",
        "description": "Find bounded workspace matches; external symlinks are hidden.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern."},
                "offset": {"type": "integer", "description": "Sorted match offset, default 0."},
                "limit": {"type": "integer", "description": "Page limit, default 100, maximum 500. Narrow pattern if traversal is incomplete."},
                "path": {"type": "string", "description": "Optional base directory."},
            },
            "required": ["pattern"],
        },
    },
    "Grep": {
        "name": "Grep",
        "description": "Search file contents with a safe RE2 expression. Accepts a file or directory path. Returns exact matching text with line/column locations, including matches late in minified files. A snippet is sufficient inspection before a focused Edit.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "RE2 expression; look-around/backreferences are unsupported.",
                },
                "path": {"type": "string", "description": "File to search, or base directory. Use the known file path directly."},
                "glob": {"type": "string", "description": "File glob filter."},
                "max_matches": {"type": "integer", "description": "Result limit."},
            },
            "required": ["pattern"],
        },
    },
    "NotebookEdit": {
        "name": "NotebookEdit",
        "description": (
            "Edit a bounded Jupyter notebook cell-by-cell. Insert adds a new "
            "cell immediately after the target anchor, or appends when no valid "
            "anchor is supplied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Notebook workspace path."},
                "edit_mode": {
                    "type": "string",
                    "enum": ["replace", "insert", "delete"],
                },
                "cell_id": {
                    "type": "string",
                    "description": "Stable target anchor; insert places the new cell after it.",
                },
                "cell_index": {
                    "type": "integer",
                    "description": "0-indexed target anchor; insert places the new cell after it.",
                },
                "new_source": {"type": "string", "description": "Replacement or new source."},
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown"],
                },
            },
            "required": ["path", "edit_mode"],
        },
    },
}


def build_tool_schemas(allowed: list[str]) -> list[dict]:
    """Return schemas in configured order, silently excluding unknown names."""
    return [
        {"type": "function", "function": TOOL_SCHEMAS[name]}
        for name in allowed
        if name in TOOL_SCHEMAS
    ]
