"""Versioned MCP contract shared by registration, diagnostics and evidence.

Aliases describe compatibility only: a DeepSeek-named executor never proves
selected-backend routing. A registration mismatch is a startup error.
"""
import hashlib
import json

from . import __version__

CONTRACT_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
ALIASES = {
    "get_task_status": "get_deepseek_status", "get_task_result": "get_deepseek_result",
    "cancel_task": "cancel_deepseek", "send_task_message": "send_deepseek_message",
    "record_task_verification": "record_deepseek_verification",
    "get_task_recovery": "get_deepseek_recovery",
    "acknowledge_task_mutations": "acknowledge_deepseek_mutations",
}
LEGACY_EXECUTORS = ("start_deepseek", "start_deepseek_readonly", "delegate_to_deepseek", "delegate_to_deepseek_readonly")
CANONICAL_TOOLS = (
    "ping", "start_agent", "start_agent_readonly", "delegate_to_agent", "delegate_to_agent_readonly",
    "get_task_status", "get_task_result", "cancel_task", "send_task_message",
    "record_task_verification", "get_task_recovery", "acknowledge_task_mutations",
    "acknowledge_workspace_review", "create_task_group", "get_task_group",
    "get_task_checkpoint", "resume_agent", "cancel_task_group",
    "bind_client_session", "get_client_evidence",
)
TOOL_NAMES = frozenset(CANONICAL_TOOLS + LEGACY_EXECUTORS + tuple(ALIASES.values()))
CAPABILITIES = (
    {"id": "basic_readonly", "version": "1.0.0",
     "required_tools": ["ping", "start_agent_readonly", "get_task_status", "get_task_result"],
     "compatible_aliases": {"start_agent_readonly": ["start_deepseek_readonly", "delegate_to_deepseek_readonly", "delegate_to_agent_readonly"],
                            "get_task_status": ["get_deepseek_status"], "get_task_result": ["get_deepseek_result"]},
     "dependencies": [], "legacy_limit": "兼容旧入口仅证明原引擎只读能力"},
    {"id": "selected_backend", "version": "1.0.0",
     "required_tools": ["start_agent", "start_agent_readonly", "get_task_status", "get_task_result", "cancel_task"],
     "compatible_aliases": {"start_agent": ["delegate_to_agent"], "start_agent_readonly": ["delegate_to_agent_readonly"]},
     "dependencies": ["basic_readonly"]},
    {"id": "recovery_review", "version": "1.0.0",
     "required_tools": ["get_task_recovery", "acknowledge_task_mutations", "acknowledge_workspace_review"],
     "compatible_aliases": {key: [ALIASES[key]] for key in ("get_task_recovery", "acknowledge_task_mutations")},
     "dependencies": ["selected_backend"]},
    {"id": "independent_verification", "version": "1.0.0",
     "required_tools": ["record_task_verification", "get_task_checkpoint"],
     "compatible_aliases": {"record_task_verification": ["record_deepseek_verification"]},
     "dependencies": ["recovery_review"]},
    {"id": "task_groups_continuation", "version": "1.0.0",
     "required_tools": ["create_task_group", "get_task_group", "get_task_checkpoint", "resume_agent", "cancel_task_group", "record_task_verification"],
     "compatible_aliases": {}, "dependencies": ["independent_verification"]},
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manifest():
    tools = [{"name": name, "semantic_version": TOOL_VERSION, "legacy": name not in CANONICAL_TOOLS,
              "backend_scope": "deepseek" if name in LEGACY_EXECUTORS else "shared"}
             for name in sorted(TOOL_NAMES)]
    body = {"contract_version": CONTRACT_VERSION, "runtime_version": __version__,
            "tools": tools, "capabilities": list(CAPABILITIES)}
    return {**body, "fingerprint": digest(body)}


def assert_registered_tools(tools):
    actual = {item if isinstance(item, str) else item.name for item in tools}
    missing, extra = TOOL_NAMES - actual, actual - TOOL_NAMES
    if missing or extra:
        raise RuntimeError(f"MCP capability contract mismatch: missing={sorted(missing)} extra={sorted(extra)}")
    return manifest()["fingerprint"]


def scope_tools(scope):
    if scope == "full":
        return list(CANONICAL_TOOLS)
    if scope == "basic":
        return ["ping", "start_agent_readonly", "get_task_status", "get_task_result", "cancel_task"]
    raise ValueError("capability_scope must be basic or full")


def capability_states(tools, unknown=False):
    available = set(tools)
    result = []
    for capability in CAPABILITIES:
        missing, used_aliases = [], {}
        for name in capability["required_tools"]:
            if name in available:
                continue
            aliases = available.intersection(capability["compatible_aliases"].get(name, []))
            if aliases:
                used_aliases[name] = sorted(aliases)
            else:
                missing.append(name)
        result.append({"id": capability["id"], "version": capability["version"],
                       "state": "unknown" if unknown else "limited" if missing else "available",
                       "missing_tools": missing, "aliases_used": used_aliases,
                       "dependencies": capability["dependencies"],
                       "legacy_engine_only": any(name in LEGACY_EXECUTORS for names in used_aliases.values() for name in names)})
    return result
