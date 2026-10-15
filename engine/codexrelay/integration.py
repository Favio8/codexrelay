"""Selective, reversible Codex registration. Never changes the host provider."""
import base64
import contextlib
import ctypes
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
import tomlkit
from .capabilities import TOOL_NAMES, capability_states, digest, manifest, scope_tools

BEGIN = "<!-- BEGIN CodexRelay managed delegation -->"
END = "<!-- END CodexRelay managed delegation -->"
RULES_VERSION = "1.0.0"
OLD_BEGIN = "<!-- BEGIN deepseek-as-subagent global delegation policy -->"
OLD_END = "<!-- END deepseek-as-subagent global delegation policy -->"
RULES = BEGIN + """

<!-- CodexRelay rules contract: 1.0.0 -->

## CodexRelay external subagents

Use the codexrelay MCP for self-contained, execution-heavy tasks when available.
Before delegation, call ping and verify its workspace matches the actual project.
Use start_agent/delegate_to_agent (or their readonly variants) for the selected
backend. Legacy DeepSeek-named start/delegate tools always use the original engine.
Use get_task_status/get_task_result, send_task_message, cancel_task and
record_task_verification for task control. After coding, call get_task_recovery:
verify exact transaction IDs before acknowledge_task_mutations; separately inspect
workspace checks and their current hashes before acknowledge_workspace_review.
Switching backend does not remove pending checks. Never treat completed as verified.
Use readonly for pure Read/Glob/Grep analysis, coding for commands or mutations.
Pass the complete goal, absolute workspace, constraints and acceptance criteria.
Keep planning, architecture, ambiguous debugging and final verification in Codex.
Use flash by default; pro for tasks that clearly need the stronger configured model.
Pass bounded context rather than reading entire manuals before a small task. On
Windows, command execution uses cmd.exe, not PowerShell or POSIX Bash. Prefer
Read/Glob/Grep for inspection; do not repeat shell quoting probes. After two
equivalent tool failures, change the approach or return the remaining work.
If disabled, project-disabled, unavailable or busy, continue the task yourself.
LOCAL_TOKEN_BUDGET means the local cumulative task limit, not an account balance.
After any budget stop, inspect existing changes and acknowledge reviewed records,
then delegate only the remaining focused work; never replay the whole task blindly.
Never change CodexRelay controls through shell to bypass the user's switch.
Review actual diffs and tests after delegated work. Query recovery and verify exact
paths, contents and hashes before acknowledging transaction IDs. Command-generated
changes are not fully journaled, so inspect the entire workspace too.
While delegated coding is running, do not independently mutate that workspace.
Stopping preserves partial changes; inspect before taking over. Never auto-push,
deploy, modify host configuration or delete important files in a subagent task.
Create/reuse a TaskGroup for the same goal. Default budget inherits the provider; do not routinely impose a smaller token_budget. After every ended run, read get_task_checkpoint and actual evidence, clear recovery, record_task_verification with disposition/checkpoint ID/hash and remaining_items. Resume with resume_agent and an idempotency key, using exactly the verified remaining_items joined by newlines. Default authorization permits three continuations (four runs total). Never create another same-goal group to reset counters. Only App grants extra budget or rounds. If evidence is incomplete, supplement independent checks before resuming. App does not start continuation while Codex is offline.
This policy controls CodexRelay external agents only, not built-in Codex agents.

""" + END


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(value, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("配置备份需要 Windows DPAPI")
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    raw = (ctypes.c_ubyte * len(value))(*value)
    source, target = Blob(len(value), raw), Blob()
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("无法保护或读取配置备份")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def codex_dir(value=None):
    return Path(value or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()


def read(path):
    return path.read_bytes() if path.exists() else b""


def decode(value):
    return value.decode("utf-8-sig")


def same_executable(first, second):
    # Tauri starts Python with a Windows \\?\ path. Compare the actual file,
    # so normal, namespace and junction paths identify the same interpreter.
    try:
        return Path(first).samefile(second)
    except OSError:
        return Path(first).resolve() == Path(second).resolve()


def owned(entry, store=None, directory=None):
    if not entry or list(entry.get("args", [])) != ["-I", "-X", "utf8", "-m", "codexrelay", "mcp"]:
        return False
    if any(key in entry for key in ("cwd", "env", "env_vars", "url")):
        return False
    command = Path(entry.get("command", "")).resolve()
    if same_executable(command, sys.executable):
        return True
    if store is None or directory is None:
        return False
    directory = str(Path(directory).resolve())
    recorded = store.setting("integration_registration:" + directory)
    if recorded and same_executable(recorded.get("command", ""), command) and command.is_file():
        return True
    # Older releases saved an encrypted ownership backup, but bound detection
    # to the current interpreter. Recognize that managed installation across
    # desktop/dev runtime paths without accepting an arbitrary same-named entry.
    rendered = entry.as_string() if hasattr(entry, "as_string") else ""
    backup = store.setting("integration_backup:" + directory)
    package = command.parent / "Lib" / "site-packages" / "codexrelay"
    if "Managed by CodexRelay" not in rendered or not backup or not command.is_file():
        return False
    if command.name.lower() != "python.exe" or not all((package / name).is_file() for name in ("__init__.py", "service.py", "mcp_server.py")):
        return False
    try:
        manifest = json.loads(crypt(Path(backup).read_bytes(), decrypt=True))
        return Path(manifest.get("directory", "")).resolve() == Path(directory)
    except (OSError, ValueError, RuntimeError):
        return False


def strip_block(text, start, end):
    if text.count(start) != text.count(end) or text.count(start) > 1 or (start in text and text.index(start) >= text.index(end)):
        raise ValueError("委托规则标记不完整或重复，请先检查 AGENTS.md")
    return re.sub(re.escape(start) + r".*?" + re.escape(end), "", text, flags=re.S)


def rules_status(value):
    try:
        text = decode(value)
        strip_block(text, BEGIN, END)
        strip_block(text, OLD_BEGIN, OLD_END)
    except (ValueError, UnicodeError):
        return {"state": "malformed", "version": None, "expected_version": RULES_VERSION,
                "reason": "托管规则标记不完整或重复；无法安全自动替换"}
    block = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), text, re.S)
    version = re.search(r"CodexRelay rules contract: ([^ ]+)", block.group(0)) if block else None
    return {"state": "missing" if not block else "current" if block.group(0).replace("\r\n", "\n").strip() == RULES.strip() else "outdated",
            "version": version.group(1) if version else None, "expected_version": RULES_VERSION,
            "legacy_block": OLD_BEGIN in text}


def predict_tools(entry, store, directory, parse_error=None):
    unknown, tool_states = [], {}
    if parse_error:
        unknown.append(parse_error)
    values = entry.unwrap() if hasattr(entry, "unwrap") else entry or {}
    registration = "missing" if not entry else "owned" if owned(entry, store, directory) else "foreign"
    command_exists = Path(values.get("command", "")).is_file() if entry else None
    command_state = "command_available" if command_exists else "command_missing" if entry else "not_registered"
    enabled = values.get("enabled", True)
    if not isinstance(enabled, bool):
        unknown.append("enabled 不是布尔值")
        enabled = None
    allowed, denied = values.get("enabled_tools"), values.get("disabled_tools", [])
    if allowed is not None and (not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed)):
        unknown.append("enabled_tools 不是工具名列表")
        allowed = None
    if not isinstance(denied, list) or any(not isinstance(item, str) for item in denied):
        unknown.append("disabled_tools 不是工具名列表")
        denied = []
    policy = values.get("tools", {})
    if not isinstance(policy, dict):
        unknown.append("逐工具策略结构未知")
        policy = {}
    for name, item in policy.items():
        if not isinstance(item, dict) or set(item) - {"enabled", "approval_mode"} or ("enabled" in item and not isinstance(item["enabled"], bool)):
            unknown.append("逐工具策略无法完整解释: " + name)
    known_fields = {"command", "args", "enabled", "enabled_tools", "disabled_tools", "tools", "startup_timeout_sec", "startup_timeout_ms", "tool_timeout_sec", "default_tools_approval_mode", "required"}
    unknown.extend("未验证的服务器字段: " + key for key in values if key not in known_fields)
    for name in sorted(TOOL_NAMES):
        reasons = []
        if registration != "owned": reasons.append("server_" + registration)
        if command_state == "command_missing": reasons.append("command_missing")
        if enabled is False: reasons.append("server_disabled")
        if allowed is not None and name not in allowed: reasons.append("not_allowlisted")
        if name in denied: reasons.append("explicitly_denied")
        if isinstance(policy.get(name), dict) and policy[name].get("enabled") is False: reasons.append("tool_disabled")
        tool_states[name] = {"state": "blocked" if reasons else "unknown" if unknown else "predicted_available", "reasons": reasons}
    effective = [name for name, value in tool_states.items() if value["state"] == "predicted_available"]
    return {"registration_state": registration, "command_state": command_state, "command_exists": command_exists,
            "server_enabled": enabled, "allowlist_state": "unset" if "enabled_tools" not in values else "empty" if allowed == [] else "configured",
            "enabled_tools": allowed, "disabled_tools": denied, "tool_states": tool_states,
            "effective_tools": effective, "capabilities": capability_states(effective, bool(unknown)),
            "unknown_policies": unknown, "policy_semantics": "Codex MCP config 76ed3b484d62: allowlist then denylist; tools.enabled=false",
            "limitations": ["组织策略及客户端实际工具加载状态不可由静态配置证明", "注册目标运行时版本需要客户端 ping 核对"]}


def _repair_data(store, codex_home=None, capability_scope="full", remove_denials=None,
                 update_registration=True, update_rules=True, enable_server=False):
    if any(not isinstance(item, bool) for item in (update_registration, update_rules, enable_server)):
        raise ValueError("repair selections must be booleans")
    required = scope_tools(capability_scope)
    remove_denials = remove_denials or []
    if not isinstance(remove_denials, list) or any(name not in TOOL_NAMES for name in remove_denials):
        raise ValueError("remove_denials must select declared tool names")
    directory = codex_dir(codex_home)
    config_path, rules_path = directory / "config.toml", directory / "AGENTS.md"
    before_config, before_rules = read(config_path), read(rules_path)
    config = tomlkit.parse(decode(before_config))
    servers = config.get("mcp_servers", {})
    entry = servers.get("codexrelay")
    conflicts, changes = [], []
    if entry and not owned(entry, store, directory):
        conflicts.append("已有同名 codexrelay 配置不属于本软件；不能自动接管")
    if "deepseek" in servers and not entry:
        conflicts.append("检测到旧 DeepSeek 接入；请明确迁移，不能通过修复建立重复接入")
    additions, cleared, retained = [], [], []
    if update_registration and not conflicts:
        servers = config.setdefault("mcp_servers", tomlkit.table())
        if entry is None:
            entry = tomlkit.table()
            entry.add(tomlkit.comment("Managed by CodexRelay"))
            entry["command"] = str(Path(sys.executable).resolve())
            entry["args"] = ["-I", "-X", "utf8", "-m", "codexrelay", "mcp"]
            entry["startup_timeout_sec"], entry["tool_timeout_sec"] = 20, 18060
            entry["default_tools_approval_mode"] = "writes"
            servers["codexrelay"] = entry
            changes.append({"target": "config.toml:mcp_servers.codexrelay", "operation": "register_owned_runtime"})
        # Never replace a whole table: tomlkit preserves comments, extra entries
        # and per-tool approval settings while the chosen delta is applied.
        disabled = entry.get("disabled_tools", [])
        if not isinstance(disabled, list):
            conflicts.append("disabled_tools 结构未知，不能自动修复")
        else:
            for name in remove_denials:
                if name in disabled:
                    disabled.remove(name)
                    cleared.append(name)
            policies = entry.get("tools", {})
            for name in remove_denials:
                item = policies.get(name) if isinstance(policies, dict) else None
                if item is not None and item.get("enabled") == False:
                    item["enabled"] = True
                    cleared.append(name)
            retained = sorted(set(disabled).intersection(TOOL_NAMES) | {name for name, item in policies.items() if isinstance(item, dict) and item.get("enabled") == False}) if isinstance(policies, dict) else sorted(set(disabled).intersection(TOOL_NAMES))
            if "enabled_tools" in entry:
                allowed = entry["enabled_tools"]
                if not isinstance(allowed, list):
                    conflicts.append("enabled_tools 结构未知，不能自动修复")
                else:
                    for name in required:
                        if name not in allowed and name not in retained:
                            allowed.append(name)
                            additions.append(name)
            if additions: changes.append({"target": "config.toml:mcp_servers.codexrelay.enabled_tools", "operation": "append", "tools": additions})
            if cleared: changes.append({"target": "config.toml:mcp_servers.codexrelay", "operation": "remove_selected_denials", "tools": sorted(set(cleared))})
        if enable_server and entry.get("enabled") == False:
            entry["enabled"] = True
            changes.append({"target": "config.toml:mcp_servers.codexrelay.enabled", "operation": "enable_server", "before": False, "after": True})
    after_rules = before_rules
    if update_rules:
        rule_state = rules_status(before_rules)
        if rule_state["state"] == "malformed":
            conflicts.append(rule_state["reason"])
        elif rule_state["state"] != "current":
            text = strip_block(decode(before_rules), BEGIN, END)
            after_rules = (text.rstrip() + "\n\n" + RULES + "\n").lstrip("\n").encode("utf-8")
            changes.append({"target": "AGENTS.md:CodexRelay managed block", "operation": "replace_owned_rules", "before": rule_state["version"], "after": RULES_VERSION})
    after_config = tomlkit.dumps(config).encode("utf-8") if update_registration else before_config
    from .integration_evidence import sha256
    plan = {"schema_version": 1, "codex_home": str(directory), "runtime_fingerprint": manifest()["fingerprint"],
            "expected": {"config_sha256": sha256(before_config), "rules_sha256": sha256(before_rules), "config_exists": config_path.exists(), "rules_exists": rules_path.exists()},
            "selection": {"capability_scope": capability_scope, "remove_denials": sorted(set(remove_denials)), "update_registration": update_registration, "update_rules": update_rules, "enable_server": enable_server},
            "changes": changes, "added_tools": additions, "removed_denials": sorted(set(cleared)), "retained_denials": retained,
            "conflicts": conflicts, "reconnect_required": bool(changes), "backup_protection": "Windows DPAPI current user",
            "resulting_configuration": predict_tools(config.get("mcp_servers", {}).get("codexrelay"), store, directory)}
    plan["plan_id"] = digest(plan)
    return plan, (config_path, before_config, after_config), (rules_path, before_rules, after_rules)


def repair_preview(store, codex_home=None, capability_scope="full", remove_denials=None,
                   update_registration=True, update_rules=True, enable_server=False):
    """Read-only concrete repair preview; no plan/cache/config files are created."""
    return _repair_data(store, codex_home, capability_scope, remove_denials, update_registration, update_rules, enable_server)[0]


def _operation_backup(store, directory, plan, files):
    backup_dir = store.directory / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / (uuid.uuid4().hex + ".dpapi")
    payload = {"schema_version": 1, "directory": str(directory), "plan_id": plan["plan_id"], "created_at": time.time(),
               "files": [{"name": path.name, "existed": path.exists(), "before": base64.b64encode(before).decode(), "after": base64.b64encode(after).decode()} for path, before, after in files]}
    backup.write_bytes(crypt(json.dumps(payload).encode()))
    # Record the recoverable operation before any target is replaced.
    store.set_setting("integration_recovery:" + str(directory), {"backup": str(backup), "plan_id": plan["plan_id"], "state": "applying"})
    return backup


def _restore_files(directory, payload, applied=None):
    pending = []
    for item in reversed(payload["files"]):
        if applied is not None and item["name"] not in applied:
            continue
        path = directory / item["name"]
        before, after = base64.b64decode(item["before"]), base64.b64decode(item["after"])
        current = read(path)
        if current == before and path.exists() == item["existed"]:
            continue
        if current != after:
            pending.append({"file": str(path), "reason": "external_edit_preserved"})
            continue
        try:
            replace_checked(path, after, before)
            if not item["existed"] and read(path) == before:
                path.unlink()
        except (ValueError, OSError):
            pending.append({"file": str(path), "reason": "rollback_write_failed"})
    return pending


def apply_plan(store, plan, codex_home=None):
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("invalid repair plan")
    directory = codex_dir(codex_home or plan.get("codex_home"))
    if str(directory) != plan.get("codex_home"):
        raise ValueError("repair plan belongs to another Codex directory")
    with lock(store):
        if store.setting("integration_recovery:" + str(directory)):
            raise ValueError("存在接入恢复事项，请先恢复或检查现有操作")
        current, config_file, rules_file = _repair_data(store, directory, **plan["selection"])
        if current["plan_id"] != plan.get("plan_id"):
            raise ValueError("修复预览已失效：配置、规则、选项或运行时发生改变，请重新预览")
        if current["conflicts"]:
            raise ValueError("；".join(current["conflicts"]))
        if any(task["status"] in {"queued", "running", "cancelling", "compacting", "wrapping_up"} for task in store.snapshot()["tasks"]):
            raise ValueError("请先结束运行中的任务")
        files = [item for item in (config_file, rules_file) if item[1] != item[2]]
        if not files:
            return {**status(store, directory), "restart_required": False, "message": "没有所选配置变更；仍需客户端验证"}
        directory.mkdir(parents=True, exist_ok=True)
        backup = _operation_backup(store, directory, current, files)
        payload = json.loads(crypt(backup.read_bytes(), decrypt=True))
        applied = []
        try:
            for path, before, after in files:
                replace_checked(path, before, after)
                applied.append(path.name)
        except BaseException as exc:
            pending = _restore_files(directory, payload, applied)
            store.set_setting("integration_recovery:" + str(directory), {"backup": str(backup), "plan_id": current["plan_id"], "state": "needs_review" if pending else "rolled_back", "pending": pending})
            raise ValueError("修复未完成；备份已保留。" + ("存在外部修改，需检查恢复事项。" if pending else "本次已写入部分已回退。") + " " + type(exc).__name__) from exc
        store.set_setting("integration_recovery:" + str(directory), None)
        store.set_setting("integration_repair_backup:" + str(directory), str(backup))
        if current["selection"]["update_registration"]:
            store.set_setting("integration_registration:" + str(directory), {"command": str(Path(sys.executable).resolve())})
        return {**status(store, directory), "restart_required": True, "operation_backup": str(backup), "message": "配置已更新，等待客户端重新连接并重新验证"}


def restore_repair(store, codex_home=None):
    directory = codex_dir(codex_home)
    with lock(store):
        recovery = store.setting("integration_recovery:" + str(directory))
        if not recovery:
            return {"restored": True, "pending": []}
        payload = json.loads(crypt(Path(recovery["backup"]).read_bytes(), decrypt=True))
        if Path(payload["directory"]).resolve() != directory or payload["plan_id"] != recovery["plan_id"]:
            raise ValueError("恢复备份归属不匹配")
        if any(item.get("name") not in {"config.toml", "AGENTS.md"} for item in payload["files"]):
            raise ValueError("恢复目标不属于本软件接入文件")
        pending = _restore_files(directory, payload)
        store.set_setting("integration_recovery:" + str(directory), {**recovery, "state": "needs_review", "pending": pending} if pending else None)
        return {"restored": not pending, "pending": pending, "backup": recovery["backup"]}


def status(store, codex_home=None, instance=None, workspace=None, client_session_id=None):
    directory = codex_dir(codex_home)
    try:
        config = tomlkit.parse(decode(read(directory / "config.toml")))
        parse_error = None
    except (ValueError, UnicodeError, tomlkit.exceptions.ParseError):
        config, parse_error = {}, "配置无法解析；保留原文件并先修复 TOML 语法"
    entry = config.get("mcp_servers", {}).get("codexrelay")
    old = config.get("mcp_servers", {}).get("deepseek")
    prediction = predict_tools(entry, store, directory, parse_error)
    rules = rules_status(read(directory / "AGENTS.md"))
    from .integration_evidence import evidence_status, verification_instructions
    evidence = evidence_status(store, directory, instance=instance, workspace=workspace, client_session_id=client_session_id)
    ready_config = prediction["registration_state"] == "owned" and not prediction["unknown_policies"] and rules["state"] == "current"
    limited = prediction["server_enabled"] is False or any(item["state"] == "limited" for item in prediction["capabilities"])
    return {"codex_home": str(directory), "config_exists": (directory / "config.toml").exists(),
            "registered": owned(entry, store, directory), "legacy_deepseek": bool(old),
            "state": "configuration_needs_repair" if not ready_config else "capabilities_limited" if limited else evidence["state"],
            "delegation_enabled": bool(store.setting("enabled", False)),
            "server_declaration": {"source": "server_declaration", **manifest()},
            "configuration_prediction": {"source": "configuration_prediction", **prediction},
            "managed_rules": rules, "client_evidence": evidence,
            "verification_instructions": verification_instructions(directory),
            "client_verification": "配置检查不证明当前聊天已加载；重新连接后导入客户端报告并核对实际调用。",
            "pending_recovery": store.setting("integration_recovery:" + str(directory)),
            "backup": store.setting("integration_backup:" + str(directory))}


def preview(store, codex_home=None, capability_scope="full", remove_denials=None, update_registration=True, update_rules=True, enable_server=False):
    result = status(store, codex_home)
    directory = codex_dir(codex_home)
    config = tomlkit.parse(decode(read(directory / "config.toml")))
    entry = config.get("mcp_servers", {}).get("codexrelay")
    if entry and not owned(entry, store, directory):
        result["conflict"] = "已有同名 codexrelay 配置不属于本软件，请先处理名称冲突"
    elif result["legacy_deepseek"]:
        result["conflict"] = "检测到已有 DeepSeek 接入；请先结束旧任务，再选择迁移已有接入"
    result["changes"] = ["备份受影响文件（Windows 加密）", "注册本地 MCP 引擎", "追加有边界标记的委托规则"]
    result["repair_plan"] = repair_preview(store, codex_home, capability_scope, remove_denials, update_registration, update_rules, enable_server)
    return result


def replace_checked(path, previous, value):
    if read(path) != previous:
        raise ValueError("配置刚被其他程序修改，请重新检查后操作")
    temporary = path.with_name(path.name + ".relay-" + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(value)
        if read(path) != previous:
            raise ValueError("检测到并发配置编辑，未覆盖更改")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(store):
    import msvcrt
    path = store.directory / "integration.lock"
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError("另一个接入操作正在进行") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def install(store, codex_home=None, migrate=False):
    directory = codex_dir(codex_home)
    directory.mkdir(parents=True, exist_ok=True)
    with lock(store):
        state = preview(store, codex_home)
        if state.get("conflict") and not (migrate and state["legacy_deepseek"]):
            raise ValueError(state["conflict"])
        config_path, rules_path = directory / "config.toml", directory / "AGENTS.md"
        before_config, before_rules = read(config_path), read(rules_path)
        config = tomlkit.parse(decode(before_config))
        servers = config.setdefault("mcp_servers", tomlkit.table())
        previous_entry = servers.get("codexrelay")
        if servers.get("codexrelay") and not owned(servers["codexrelay"], store, directory):
            raise ValueError("同名配置不属于本软件")
        if any(t["status"] in {"queued", "running", "cancelling", "compacting", "wrapping_up"} for t in store.snapshot()["tasks"]):
            raise ValueError("请先结束运行中的任务")
        rules = strip_block(decode(before_rules), BEGIN, END)
        legacy = None
        legacy_rules = ""
        if migrate and "deepseek" in servers:
            legacy = servers.pop("deepseek").unwrap()
            match = re.search(re.escape(OLD_BEGIN) + r".*?" + re.escape(OLD_END), rules, re.S)
            legacy_rules = match.group(0) if match else ""
            rules = strip_block(rules, OLD_BEGIN, OLD_END)
        table = tomlkit.table()
        table.add(tomlkit.comment("Managed by CodexRelay"))
        table["command"] = str(Path(sys.executable).resolve())
        table["args"] = ["-I", "-X", "utf8", "-m", "codexrelay", "mcp"]
        table["startup_timeout_sec"] = 20
        table["tool_timeout_sec"] = 18060
        table["default_tools_approval_mode"] = "writes"
        policy = legacy or (previous_entry.unwrap() if previous_entry else {})
        for key in ("default_tools_approval_mode", "enabled", "enabled_tools", "disabled_tools", "tools", "startup_timeout_sec", "tool_timeout_sec"):
            if key in policy:
                table[key] = policy[key]
        servers["codexrelay"] = table
        after_config = tomlkit.dumps(config).encode("utf-8")
        after_rules = (rules.rstrip() + "\n\n" + RULES + "\n").lstrip("\n").encode("utf-8")
        backup_dir = store.directory / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / (uuid.uuid4().hex + ".dpapi")
        manifest = {"directory": str(directory), "config": base64.b64encode(before_config).decode(), "rules": base64.b64encode(before_rules).decode(),
                    "legacy": legacy, "legacy_rules": legacy_rules, "created_at": time.time()}
        previous_backup = store.setting("integration_backup:" + str(directory))
        if previous_backup and Path(previous_backup).is_file():
            prior = json.loads(crypt(Path(previous_backup).read_bytes(), decrypt=True))
            if not legacy:
                manifest["legacy"], manifest["legacy_rules"] = prior.get("legacy"), prior.get("legacy_rules", "")
        backup.write_bytes(crypt(json.dumps(manifest).encode()))
        replace_checked(config_path, before_config, after_config)
        try:
            replace_checked(rules_path, before_rules, after_rules)
        except BaseException:
            replace_checked(config_path, after_config, before_config)
            raise
        store.set_setting("integration_backup:" + str(directory), str(backup))
        store.set_setting("integration_registration:" + str(directory), {"command": str(Path(sys.executable).resolve())})
        return {**status(store, codex_home), "restart_required": True, "message": "配置已写入，等待 Codex 重新连接"}


def uninstall(store, codex_home=None):
    directory = codex_dir(codex_home)
    with lock(store):
        store.set_setting("enabled", False)
        if any(t["status"] in {"queued", "running", "cancelling", "compacting", "wrapping_up"} for t in store.snapshot()["tasks"]):
            raise ValueError("已禁止新委托；请等待或停止当前任务后再解除接入")
        # Recovery remains owned by the host; never acknowledge on its behalf.
        from .backends import ensure_removable
        for project in store.snapshot()["projects"]:
            if Path(project["path"]).is_dir():
                ensure_removable(project["path"])
        config_path, rules_path = directory / "config.toml", directory / "AGENTS.md"
        before_config, before_rules = read(config_path), read(rules_path)
        config = tomlkit.parse(decode(before_config))
        servers = config.get("mcp_servers", {})
        if servers.get("codexrelay") and not owned(servers["codexrelay"], store, directory):
            raise ValueError("接入配置已被修改，无法自动移除")
        servers.pop("codexrelay", None)
        rules = strip_block(decode(before_rules), BEGIN, END).rstrip() + "\n"
        backup = store.setting("integration_backup:" + str(directory))
        if backup:
            manifest = json.loads(crypt(Path(backup).read_bytes(), decrypt=True))
            if manifest.get("legacy"):
                if "deepseek" in servers:
                    raise ValueError("已有新的 DeepSeek 配置，未覆盖；请先处理恢复冲突")
                servers["deepseek"] = manifest["legacy"]
            if manifest.get("legacy_rules") and OLD_BEGIN not in rules:
                rules += "\n" + manifest["legacy_rules"] + "\n"
        after_config = tomlkit.dumps(config).encode("utf-8")
        replace_checked(config_path, before_config, after_config)
        try:
            replace_checked(rules_path, before_rules, rules.encode("utf-8"))
        except BaseException:
            replace_checked(config_path, after_config, before_config)
            raise
        store.set_setting("integration_backup:" + str(directory), None)
        store.set_setting("integration_registration:" + str(directory), None)
        return {"registered": False, "message": "已选择性移除本软件接入；保留其他配置与凭据"}


def dispatch(store, method, params):
    from .integration_evidence import record_client_report, verification_instructions
    action = {"integration_status": status, "integration_preview": preview, "integration_install": install,
              "integration_refresh": install, "integration_uninstall": uninstall,
              "integration_repair_preview": repair_preview, "integration_apply_plan": apply_plan,
              "integration_restore_repair": restore_repair, "integration_client_report": record_client_report,
              "integration_verification_instructions": lambda _store, **options: verification_instructions(**options)}.get(method)
    if not action:
        raise ValueError("未知接入操作")
    return action(store, **params)
