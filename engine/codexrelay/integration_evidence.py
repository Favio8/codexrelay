"""Client reports and engine call observations are separate, bound evidence.

No report or ping claims that a different chat has reloaded its configuration.
Only metadata is stored here; call arguments, credentials and model text are not.
"""
import hashlib
import json
import time
import uuid
from pathlib import Path

from .capabilities import TOOL_NAMES, capability_states, manifest
from .state import canonical, process_identity

REPORTS_KEY = "integration_client_reports_v1"
CALLS_KEY = "integration_call_evidence_v1"
CLIENT_KINDS = {"codex_desktop", "codex_cli", "local_stdio_fixture", "app_self_test", "unknown"}


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def binding(codex_home=None):
    from .integration import codex_dir, read
    directory = codex_dir(codex_home)
    return {"codex_home": str(directory), "config_sha256": sha256(read(directory / "config.toml")),
            "rules_sha256": sha256(read(directory / "AGENTS.md")),
            "runtime_fingerprint": manifest()["fingerprint"]}


def _append(store, key, value, maximum):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        entries = json.loads(row[0]) if row else []
        entries.append(value)
        db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(entries[-maximum:])))
    return value


def record_client_report(store, *, instance, workspace, visible_tools, client_kind="unknown", client_version=None,
                         client_session_id=None, codex_home=None):
    if client_kind not in CLIENT_KINDS:
        raise ValueError("unknown client_kind")
    if not isinstance(instance, str) or not instance.strip():
        raise ValueError("MCP instance is required; copy it from this chat's ping")
    if not isinstance(visible_tools, list) or len(visible_tools) > 1000 or any(not isinstance(name, str) or len(name) > 180 for name in visible_tools):
        raise ValueError("visible_tools must be a bounded list of tool names")
    for field in (client_version, client_session_id):
        if field is not None and (not isinstance(field, str) or len(field) > 200):
            raise ValueError("client version/session must be bounded strings")
    tools = sorted({name.removeprefix("mcp__codexrelay__") for name in visible_tools})
    record = {"id": uuid.uuid4().hex, "source": "user_report", "reported_at": time.time(),
              "instance": instance, "workspace": canonical(workspace), "client_kind": client_kind,
              "client_version": client_version, "client_session_id": client_session_id,
              "visible_tools": tools, "unknown_tools": sorted(set(tools) - TOOL_NAMES), **binding(codex_home)}
    return _append(store, REPORTS_KEY, record, 100)


def record_call(store, *, instance, workspace, tool, success, codex_home=None, client_kind="unknown",
                client_version=None, client_session_id=None, result_reference=None, **identities):
    if tool not in TOOL_NAMES:
        raise ValueError("call is outside the registered MCP contract")
    if client_kind not in CLIENT_KINDS:
        raise ValueError("unknown client_kind")
    if not isinstance(success, bool):
        raise ValueError("success must be a boolean")
    record = {"id": uuid.uuid4().hex, "source": "engine_call", "observed_at": time.time(),
              "instance": instance, "workspace": canonical(workspace), "tool": tool, "success": success,
              "client_kind": client_kind, "client_version": client_version, "client_session_id": client_session_id,
              "result_reference": result_reference, **binding(codex_home)}
    # Allow only stable identifiers, never arbitrary RPC arguments or contents.
    for key in ("task_id", "run_id", "group_id", "checkpoint_id", "backend_id", "model"):
        if key in identities:
            record[key] = identities[key]
    return _append(store, CALLS_KEY, record, 1000)


def evidence_status(store, codex_home=None, *, instance=None, workspace=None, client_session_id=None, client_report_id=None):
    current = binding(codex_home)
    normalized = canonical(workspace) if workspace else None
    with store.connect() as db:
        live = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,payload FROM instances")}

    def annotated(record):
        reasons = [key + "_changed" for key, value in current.items() if record.get(key) != value]
        observed = live.get(record.get("instance"))
        if not observed or process_identity(observed.get("pid", 0)) != observed.get("process_identity"):
            reasons.append("instance_not_live")
        else:
            try:
                if canonical(observed["workspace"]) != record.get("workspace"):
                    reasons.append("instance_workspace_changed")
            except OSError:
                reasons.append("instance_workspace_missing")
        if instance is not None and record.get("instance") != instance:
            reasons.append("different_instance")
        if normalized is not None and record.get("workspace") != normalized:
            reasons.append("different_workspace")
        if client_session_id is not None and record.get("client_session_id") != client_session_id:
            reasons.append("different_client_session")
        return {**record, "current": not reasons, "stale_reasons": reasons}

    reports = [annotated(item) for item in store.setting(REPORTS_KEY, [])]
    applicable_reports = [item for item in reports if item["current"] and (not client_report_id or item['id'] == client_report_id)]
    selected_report = applicable_reports[-1] if applicable_reports else None
    calls = []
    for item in store.setting(CALLS_KEY, []):
        call = annotated(item)
        if selected_report:
            if item.get('client_kind') != selected_report.get('client_kind'):
                call['stale_reasons'].append('different_client_kind')
            if selected_report.get('client_version') is not None and item.get('client_version') != selected_report['client_version']:
                call['stale_reasons'].append('different_client_version')
            call['current'] = not call['stale_reasons']
        calls.append(call)
    # A Desktop status check has no identity for the user's current chat. It
    # must not silently choose another live instance as the current client.
    bound = bool(instance and normalized and client_session_id)
    called_tools = sorted({item["tool"] for item in calls if item["current"] and item["success"]})
    real_client = selected_report and selected_report["client_kind"] in {"codex_desktop", "codex_cli"}
    call_records = [item for item in calls if item["current"] and item["success"]
                    and item["client_kind"] in {"codex_desktop", "codex_cli"}]
    independently_observed = sorted({item["tool"] for item in call_records})
    visible = selected_report["visible_tools"] if selected_report else []
    verified_tools = set(visible).intersection(independently_observed) if real_client and bound else set()
    verified_capabilities = capability_states(verified_tools)
    return {"binding": current, "current_chat_bound": bound, "client_reports": reports[-20:],
            "selected_report_id": client_report_id, "selection_current": bool(bound and selected_report),
            "latest_report": selected_report, "client_report_capabilities": capability_states(visible, not bool(selected_report)),
            "actual_calls": calls[-100:], "successfully_called_tools": called_tools,
            "actual_call_capabilities": capability_states(called_tools),
            "verified_tools": sorted(verified_tools), "verified_capabilities": verified_capabilities,
            "state": "current_chat_verified" if bound and real_client and all(item["state"] == "available" for item in verified_capabilities)
                     else "partially_verified" if verified_tools else "waiting_client_verification",
            "limitations": ["人工导入始终标记为用户报告；App 自测及单次 ping 不证明全能力。",
                            "引擎无法独立识别未知客户端；未绑定当前聊天时不复用其他实例的就绪状态。"]}


def verification_instructions(codex_home=None):
    contract = manifest()
    return {"contract_version": contract["contract_version"], "runtime_fingerprint": contract["fingerprint"],
            "required_tools": [item["name"] for item in contract["tools"]],
            "prompt": "在本聊天调用 codexrelay ping，记录 instance、workspace 和版本；枚举实际可见工具，不要仅复述服务端目录。"
                      "为本次验证确认一个稳定的会话标识（可用明确标记的本地验证 ID，不冒充未知宿主 ID）。"
                      "在同一聊天调用 bind_client_session(session_id=该标识, root_session_id=已知 root 标识或同一本地验证 ID, "
                      "client_kind=desktop 或 cli, confirmed=true, client_version=已知版本或 null)，保存返回的 id。"
                      "再调用 ping(client_binding_id=该 id)；后续启动/委托、状态/结果、组和 checkpoint 查询、核验、恢复、续作与取消都带同一个 client_binding_id。"
                      "未带绑定的调用仍正常执行，但任务所属聊天不能冒充本次调用者，也不会升级为本聊天的调用证据。"
                      "输出导入报告 JSON，仅包含 instance、workspace、client_kind（codex_desktop/codex_cli）、client_version、"
                      "client_session_id（与绑定的 session_id 完全相同）、visible_tools。保存到 App 后，在“核对的聊天”明确选择该报告。"
                      "只读、编码恢复核验及续作案例按用户已授权范围执行；不要为检查而更改委托开关或发起未授权模型请求。",
            "codex_home": binding(codex_home)["codex_home"]}
