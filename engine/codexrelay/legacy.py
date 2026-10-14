"""Import an existing deepseek-as-subagent setup without exposing its credential."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import tomlkit
from .integration import codex_dir, read, decode, install, status


def credential_environment(name):
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except FileNotFoundError:
            pass
    return os.environ.get(name, "").strip()


def configuration_path():
    return Path.home() / ".deepseek-mcp" / "config.json"


def read_legacy(codex_home=None):
    directory = codex_dir(codex_home)
    config = tomlkit.parse(decode(read(directory / "config.toml")))
    entry = config.get("mcp_servers", {}).get("deepseek")
    if not entry:
        raise ValueError("没有发现可导入的独立 DeepSeek MCP")
    command = str(entry.get("command", ""))
    args = list(entry.get("args", []))
    if Path(command).name.lower() not in {"deepseek-mcp.exe", "deepseek-mcp"} and not any(a == "deepseek_mcp.server" for a in args):
        raise ValueError("已有 deepseek 不是已识别的 deepseek-as-subagent 接入，请手动核对供应商配置")
    path = configuration_path()
    data = json.loads(decode(read(path))) if path.exists() else {}
    address = data.get("base_url", "https://api.deepseek.com")
    hostname = urlsplit(address).hostname or ""
    official = hostname == "api.deepseek.com" or hostname == "deepseek.com"
    variable = "DEEPSEEK_API_KEY" if official else "OPENAI_API_KEY"
    inline = dict(entry.get("env", {}))
    # Never carry the official credential to an unrelated custom hostname.
    secret = str(inline.get(variable) or credential_environment(variable) or data.get("api_key") or "").strip()
    provider = {"name": "DeepSeek（已导入）" if official else "兼容供应商（已导入）",
                "kind": "deepseek" if official else "custom", "base_url": address,
                "flash_model": data.get("flash", data.get("model", "deepseek-flash")),
                "pro_model": data.get("pro", data.get("model", "deepseek-v4-pro")),
                "reasoning_effort": data.get("flash_reasoning_effort", "high"),
                "pro_reasoning_effort": data.get("pro_reasoning_effort", "high"),
                "max_total_tokens": data.get("max_total_tokens", 1_000_000),
                "max_output_tokens": data.get("max_output_tokens", 16384),
                "max_turns": data.get("max_turns", 50), "max_run_seconds": data.get("max_run_seconds", 18000)}
    if "allowed_tools" in data:
        provider["allowed_tools"] = data["allowed_tools"]
    enabled = bool(entry.get("enabled", True)) and str(inline.get("DEEPSEEK_MODE") or os.environ.get("DEEPSEEK_MODE", "auto")) != "off"
    return {"codex_home": str(directory), "legacy_config": str(path), "runtime": command,
            "provider": provider, "credential_variable": variable,
            "credential_available": bool(secret), "enabled": enabled}, secret


def preview(codex_home=None):
    info, _ = read_legacy(codex_home)
    return info


def adopt(service, codex_home=None):
    directory = str(codex_dir(codex_home))
    source_key = "legacy_import:" + directory
    prior_import = service.store.setting(source_key)
    if not status(service.store, codex_home)["legacy_deepseek"] and prior_import:
        service.store.provider(prior_import)
        return {**status(service.store, codex_home), "provider_id": prior_import, "already_imported": True}
    info, secret = read_legacy(codex_home)
    if not secret and urlsplit(info["provider"]["base_url"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("已找到供应商配置，但没有找到对应 Key；请先设置 " + info["credential_variable"])
    if any(task["status"] in {"queued", "running", "cancelling", "compacting", "wrapping_up"} for task in service.store.snapshot()["tasks"]):
        raise ValueError("请先结束 CodexRelay 中正在执行的任务")
    previous = service.store.setting("active_provider_id")
    provider_id = service.save_provider(**info["provider"], api_key=secret)["id"]
    try:
        result = install(service.store, codex_home, migrate=True)
    except BaseException:
        service.delete_provider(provider_id)
        service.store.set_setting("active_provider_id", previous)
        raise
    service.store.set_setting("active_provider_id", provider_id)
    service.store.set_setting("enabled", info["enabled"])
    service.store.set_setting(source_key, provider_id)
    return {**result, "provider_id": provider_id, "credential_imported": bool(secret),
            "enabled": info["enabled"], "restart_required": True,
            "message": "供应商、模型与凭据已导入。已备份并替换旧接入；重启 Codex 后开始统一管理。"}
