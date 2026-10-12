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

BEGIN = "<!-- BEGIN CodexRelay managed delegation -->"
END = "<!-- END CodexRelay managed delegation -->"
OLD_BEGIN = "<!-- BEGIN deepseek-as-subagent global delegation policy -->"
OLD_END = "<!-- END deepseek-as-subagent global delegation policy -->"
RULES = BEGIN + """

## CodexRelay external subagents

Use the codexrelay MCP for self-contained, execution-heavy tasks when available.
Before delegation, call ping and verify its workspace matches the actual project.
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


def owned(entry, store=None, directory=None):
    if not entry or list(entry.get("args", [])) != ["-I", "-X", "utf8", "-m", "codexrelay", "mcp"]:
        return False
    if any(key in entry for key in ("cwd", "env", "env_vars", "url")):
        return False
    command = Path(entry.get("command", "")).resolve()
    if command == Path(sys.executable).resolve():
        return True
    if store is None or directory is None:
        return False
    directory = str(Path(directory).resolve())
    recorded = store.setting("integration_registration:" + directory)
    if recorded and Path(recorded.get("command", "")).resolve() == command and command.is_file():
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
    if text.count(start) != text.count(end) or text.count(start) > 1:
        raise ValueError("委托规则标记不完整或重复，请先检查 AGENTS.md")
    return re.sub(re.escape(start) + r".*?" + re.escape(end), "", text, flags=re.S)


def status(store, codex_home=None):
    directory = codex_dir(codex_home)
    config = tomlkit.parse(decode(read(directory / "config.toml")))
    entry = config.get("mcp_servers", {}).get("codexrelay")
    old = config.get("mcp_servers", {}).get("deepseek")
    return {"codex_home": str(directory), "config_exists": (directory / "config.toml").exists(),
            "registered": owned(entry, store, directory), "legacy_deepseek": bool(old),
            "client_verification": "请在新的 Codex 聊天调用 codexrelay ping 核对工作区",
            "backup": store.setting("integration_backup:" + str(directory))}


def preview(store, codex_home=None):
    result = status(store, codex_home)
    directory = codex_dir(codex_home)
    config = tomlkit.parse(decode(read(directory / "config.toml")))
    entry = config.get("mcp_servers", {}).get("codexrelay")
    if entry and not owned(entry, store, directory):
        result["conflict"] = "已有同名 codexrelay 配置不属于本软件，请先处理名称冲突"
    elif result["legacy_deepseek"]:
        result["conflict"] = "检测到已有 DeepSeek 接入；请先结束旧任务，再选择迁移已有接入"
    result["changes"] = ["备份受影响文件（Windows 加密）", "注册本地 MCP 引擎", "追加有边界标记的委托规则"]
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
        if any(t["status"] in {"queued", "running", "cancelling"} for t in store.snapshot()["tasks"]):
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
        for key in ("default_tools_approval_mode", "enabled_tools", "disabled_tools", "tools", "startup_timeout_sec", "tool_timeout_sec"):
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
        if any(t["status"] in {"queued", "running", "cancelling"} for t in store.snapshot()["tasks"]):
            raise ValueError("已禁止新委托；请等待或停止当前任务后再解除接入")
        # Recovery remains owned by the host; never acknowledge on its behalf.
        from deepseek_mcp.config import Config
        from deepseek_mcp.transaction_recovery import query_with_lease
        for project in store.snapshot()["projects"]:
            if Path(project["path"]).is_dir() and query_with_lease(Config(api_key="", workspace=Path(project["path"]), allowed_tools=[])):
                raise ValueError("仍有待核验修改，请先在 Codex 中完成恢复检查")
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
    action = {"integration_status": status, "integration_preview": preview, "integration_install": install, "integration_uninstall": uninstall}.get(method)
    if not action:
        raise ValueError("未知接入操作")
    return action(store, **params)
