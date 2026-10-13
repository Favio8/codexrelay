"""Application operations used by the desktop bridge."""
import importlib.metadata
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from .state import Store
from .vault import Vault


def validate_provider(values):
    result = {}
    for key in ("name", "base_url", "flash_model", "pro_model"):
        value = str(values.get(key, "")).strip()
        if not value or len(value) > 500:
            raise ValueError("请填写有效的 " + key)
        result[key] = value
    address = urlsplit(result["base_url"])
    local = address.hostname in {"localhost", "127.0.0.1", "::1"}
    if address.scheme != "https" and not (local and address.scheme == "http"):
        raise ValueError("远程供应商必须使用 HTTPS，本机服务可以使用 HTTP")
    if not address.hostname or address.username or address.password or address.query or address.fragment:
        raise ValueError("API 基础地址无效")
    if address.path.rstrip("/").endswith("/chat/completions"):
        raise ValueError("请填写基础地址，不要包含 /chat/completions")
    result["kind"] = values.get("kind", "custom")
    if result["kind"] not in {"custom", "deepseek"}:
        raise ValueError("未知供应商类型")
    result["reasoning_effort"] = values.get("reasoning_effort", "provider-default")
    if result["reasoning_effort"] not in {"provider-default", "none", "low", "high", "max"}:
        raise ValueError("未知推理设置")
    result["pro_reasoning_effort"] = values.get("pro_reasoning_effort", result["reasoning_effort"])
    if result["pro_reasoning_effort"] not in {"provider-default", "none", "low", "high", "max"}:
        raise ValueError("未知增强模型推理设置")
    for key, default, minimum, maximum in (("max_total_tokens", 1_000_000, 32768, 20_000_000),
                                           ("max_output_tokens", 16384, 256, 16384),
                                           ("max_turns", 50, 1, 100), ("max_run_seconds", 18000, 1, 172800)):
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{key} 必须是 {minimum} 至 {maximum} 之间的整数")
        result[key] = value
    if "allowed_tools" in values:
        allowed = values["allowed_tools"]
        known = {"Read", "Write", "Edit", "Bash", "Glob", "Grep", "NotebookEdit"}
        if not isinstance(allowed, list) or any(not isinstance(tool, str) or tool not in known for tool in allowed):
            raise ValueError("导入的工具权限列表无效")
        result["allowed_tools"] = list(dict.fromkeys(allowed))
    return result


class Service:
    def __init__(self, store=None, vault=None):
        self.store = store or Store()
        self.vault = vault or Vault()

    def snapshot(self):
        data = self.store.snapshot()
        for provider in data["providers"]:
            provider["has_key"] = bool(self.vault.get(provider.get("credential_id", provider["id"])))
            provider.pop("credential_id", None)
        return data

    def save_provider(self, **params):
        provider = validate_provider(params)
        identifier = params.get("id") or uuid.uuid4().hex
        previous = self.store.provider(identifier) if params.get("id") else None
        provider.update(id=identifier, revision=uuid.uuid4().hex,
                        credential_id=previous["credential_id"] if previous else identifier)
        secret = params.get("api_key", "").strip()
        if secret:
            provider["credential_id"] = uuid.uuid4().hex
            self.vault.set(provider["credential_id"], secret)
        try:
            self.store.save_provider(provider)
        except BaseException:
            if secret:
                self.vault.delete(provider["credential_id"])
            raise
        if secret and previous and previous["credential_id"] != provider["credential_id"]:
            self.vault.delete(previous["credential_id"])
        return {"id": identifier}

    def delete_provider(self, id):
        provider = self.store.provider(id)
        self.store.delete_provider(id)
        self.vault.delete(provider["credential_id"])
        return {"deleted": True}

    def test_provider(self, id):
        from openai import OpenAI
        provider = self.store.provider(id)
        secret = self.vault.get(provider["credential_id"])
        address = urlsplit(provider["base_url"])
        if not secret and address.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("请先保存 API Key")
        client = OpenAI(api_key=secret or "local", base_url=provider["base_url"], timeout=25, max_retries=0)
        model = provider["flash_model"]
        tools = [{"type": "function", "function": {"name": "relay_probe", "description": "Verify the tool connection", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]
        messages = [{"role": "user", "content": "Call relay_probe once, then reply CONNECTED after its result."}]
        try:
            first = client.chat.completions.create(model=model, messages=messages, tools=tools, max_tokens=512)
            message = first.choices[0].message
            if not message.tool_calls or any(c.function.name != "relay_probe" for c in message.tool_calls):
                raise ValueError("模型未返回要求的结构化工具调用，请检查模型兼容性")
            messages.append(message.model_dump(exclude_none=True))
            messages.extend({"role": "tool", "tool_call_id": call.id, "content": "CONNECTED"} for call in message.tool_calls)
            final = client.chat.completions.create(model=model, messages=messages, max_tokens=512)
            if not final.choices[0].message.content:
                raise ValueError("模型收到工具结果后未返回有效回复")
        except ValueError:
            raise
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            raise ValueError(f"连接测试失败：{type(exc).__name__}" + (f" (HTTP {code})" if code else "，请检查网络与供应商设置")) from None
        finally:
            client.close()
        latest = self.store.provider(id)
        if latest["revision"] == provider["revision"]:
            latest["last_test"] = {"ok": True, "at": time.time(), "model": model, "revision": provider["revision"]}
            self.store.save_provider(latest)
        return {"ok": True, "model": model, "tool_roundtrip": True}

    def diagnostics(self):
        from . import __version__
        from .integration import status
        snapshot = self.snapshot()
        return {"version": __version__, "state_directory": str(self.store.directory),
                "runtime": {name: importlib.metadata.version(name) for name in ("mcp", "openai", "tomlkit")},
                "enabled": snapshot["enabled"], "providers": snapshot["providers"],
                "active_tasks": [t["id"] for t in snapshot["tasks"] if t["status"] in {"queued", "running", "cancelling"}],
                "connections": snapshot["instances"], "integration": status(self.store)}

    def test_backend(self, backend, provider_id, slot='flash'):
        import tempfile
        from dataclasses import replace
        from .backends import build_config, create_manager
        if slot not in {'flash','pro'}:raise ValueError('未知模型档位')
        provider=self.store.provider(provider_id)
        credential=self.vault.get(provider['credential_id'])
        with tempfile.TemporaryDirectory(prefix='codexrelay-probe-') as directory:
            workspace=Path(directory)
            marker=uuid.uuid4().hex
            (workspace/'probe.txt').write_text(marker,encoding='utf-8')
            config=replace(build_config(provider,'readonly',slot,workspace,credential),
                           max_output_tokens=512,max_turns=4,max_run_seconds=45,max_total_tokens=32768)
            # Reserve the same database slot as delegation before calling the provider;
            # a snapshot check alone races with another MCP or GUI process.
            with self.store.backend_probe(provider, backend, slot, workspace):
                try:
                    result=create_manager(backend).run_sync('Read probe.txt with the Read tool and return its exact contents.',config)
                except Exception as error:
                    message=str(error)
                    raise ValueError(message.replace(credential,'[REDACTED]') if credential else message) from None
            if not result.get('tool_calls') or marker not in result.get('final_message',''):
                raise ValueError('后端未完成实际文件读取，请检查模型工具兼容性')
        status={'ok':True,'backend':backend,'model':provider[slot+'_model'],'revision':provider['revision'],'at':time.time(),'tool_roundtrip':True}
        self.store.set_setting('backend_test:'+backend+':'+provider_id+':'+slot,status)
        return status

    def dispatch(self, method, params):
        if method == 'test_backend':
            return self.test_backend(**params)
        if method == 'usage_requests_today':
            from .usage import requests_today
            return requests_today(self.store)
        if method.startswith('codex_usage_'):
            from . import codex_usage
            action = {'codex_usage_today':codex_usage.today,'codex_usage_settings':codex_usage.settings,
                'codex_usage_configure':codex_usage.configure,'codex_usage_clear':codex_usage.clear,
                'codex_usage_save_price':codex_usage.save_price}.get(method)
            if action is None:raise ValueError('未知统计操作')
            return action(self.store,**params)
        if method == 'backend_catalog':
            from .backends import catalog
            return catalog()
        if method == 'activate_execution':
            from .backends import catalog
            backend = params['backend']
            if not any(item['id']==backend and item['installed'] for item in catalog()):
                raise ValueError('此执行后端尚未安装或不可用')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM providers WHERE id=?',(params['provider_id'],)).fetchone():
                    raise ValueError('请选择供应商')
                db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',[
                    ('active_backend',json.dumps(backend)),('active_provider_id',json.dumps(params['provider_id']))])
            return {'backend':backend,'provider_id':params['provider_id']}
        if method == 'usage_today':
            from .usage import usage_today
            return usage_today(self.store)
        if method == 'get_usage_prices':
            from .usage import settings_from
            with self.store.connect() as db:
                return settings_from(db)
        if method == 'save_usage_price':
            from .usage import save_price
            return save_price(self.store, **params)
        if method == 'backfill_usage_price':
            from .usage import backfill_price
            return backfill_price(self.store, **params)
        if method == 'get_task':
            return self.store.task(params['id'])
        if method == "snapshot":
            return self.snapshot()
        if method == "set_enabled":
            if not isinstance(params.get("enabled"), bool):
                raise ValueError("enabled 必须是布尔值")
            if params["enabled"]:
                provider = self.store.provider()
                if not self.vault.get(provider["credential_id"]) and urlsplit(provider["base_url"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
                    raise ValueError("请先填写供应商 API Key")
            self.store.set_setting("enabled", params["enabled"])
            return {"enabled": params["enabled"]}
        if method == "save_provider":
            return self.save_provider(**params)
        if method == "delete_provider":
            return self.delete_provider(params["id"])
        if method == "activate_provider":
            self.store.provider(params["id"])
            self.store.set_setting("active_provider_id", params["id"])
            return {"active_provider_id": params["id"]}
        if method == "set_project":
            if not isinstance(params.get("disabled"), bool):
                raise ValueError("disabled 必须是布尔值")
            self.store.set_project(params["path"], params["disabled"])
            return {"saved": True}
        if method == "cancel_task":
            return self.store.cancel(params["id"])
        if method == "test_provider":
            return self.test_provider(params["id"])
        if method == "legacy_preview":
            from .legacy import preview
            return preview(params.get("codex_home"))
        if method == "adopt_legacy":
            from .legacy import adopt
            return adopt(self, params.get("codex_home"))
        if method == "diagnostics":
            return self.diagnostics()
        if method == "export_diagnostics":
            report = self.diagnostics()
            report.pop("connections", None)
            report.pop("state_directory", None)
            report.pop("integration", None)
            for provider in report["providers"]:
                provider["base_url"] = urlsplit(provider["base_url"]).scheme + "://" + (urlsplit(provider["base_url"]).hostname or "")
            Path(params["path"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"exported": True}
        if method.startswith("integration_"):
            from .integration import dispatch
            result = dispatch(self.store, method, params)
            if method in {"integration_status", "integration_preview"} and result.get("legacy_deepseek"):
                from .legacy import preview
                result["legacy"] = preview(params.get("codex_home"))
            return result
        raise ValueError("未知操作")
