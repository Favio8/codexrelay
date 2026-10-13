"""MCP adapter around the upstream execution loop, with live desktop controls."""
import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from .backends import Config, HOST_INSTRUCTIONS, validate_delegation_input, query_with_lease, acknowledge_with_lease, build_config, create_manager, catalog, recovery
from .backends import review
from .state import Store, canonical
from .vault import Vault
from .failures import classify_error
from . import __version__


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def scrub_strings(value, secret):
    """Redact credentials in text without corrupting numeric telemetry or keys."""
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, '[REDACTED]')
    if isinstance(value, dict):
        return {name: scrub_strings(item, secret) for name, item in value.items()}
    if isinstance(value, list):
        return [scrub_strings(item, secret) for item in value]
    return value


class Relay:
    def __init__(self, store=None, vault=None, workspace=None, manager=None):
        self.store = store or Store()
        self.vault = vault or Vault()
        self.workspace = Path(workspace or Path.cwd()).resolve(strict=True)
        self.manager = manager or create_manager('deepseek')
        self.managers = {'deepseek': self.manager}
        self.job_backends = {}
        self.jobs = {}
        self.instance = uuid.uuid4().hex
        self.store.reap()
        self.store.discover(self.workspace)
        self.store.heartbeat(self.instance, self.workspace)

    def config(self, provider, mode, slot, credential=None):
        key = self.vault.get(provider['credential_id']) if credential is None else credential
        return build_config(provider, mode, slot, self.workspace, key)

    def start(self, task, context, mode, slot, token_budget=None, task_root=None, write_paths=None, backend=None):
        validate_delegation_input(task, context)
        if slot not in {"flash", "pro"}:
            raise ValueError("model must be flash or pro")
        selected = self.store.setting('active_backend', 'deepseek')
        backend = backend or selected
        if backend not in self.managers:
            self.managers[backend] = create_manager(backend)
        manager = self.managers[backend]
        provider = self.store.provider()
        credential = self.vault.get(provider['credential_id'])
        config = self.config(provider, mode, slot, credential)
        capabilities=next(item['capabilities'] for item in catalog() if item['id']==backend)
        # Unsupported requests are refused before any provider call, with the concrete gap.
        if mode == 'readonly' and not capabilities['strict_readonly']:
            raise ValueError(f"{backend} 后端未声明严格只读能力，请改用 coding 委托或原执行引擎")
        if not config.allowed_tools:
            raise ValueError('严格只读需要 Read/Glob/Grep，当前供应商的允许工具不包含它们，无法保证只读' if mode == 'readonly'
                             else '该供应商的允许工具为空，无法执行任何工具调用，请先检查供应商配置')
        if token_budget is not None and not capabilities['budget_enforcement']:
            raise ValueError(f"{backend} 后端不支持任务预算，请移除 token_budget")
        if token_budget is not None:
            if isinstance(token_budget, bool) or not isinstance(token_budget, int) or not 32768 <= token_budget <= config.max_total_tokens:
                raise ValueError('token_budget must be between 32768 and the provider task limit')
            config = replace(config, max_total_tokens=token_budget)
        if task_root is not None:
            config = replace(config, task_root=Path(task_root))
        if write_paths is not None:
            config = replace(config, write_paths=[Path(path) for path in write_paths])
        record = self.store.admit(task, self.workspace, mode, slot, expected_provider=provider, backend=backend, expected_backend=selected, backend_capabilities=capabilities)
        identifier = record["id"]
        try:
            report = lambda value: self.store.update_task(identifier, telemetry=scrub_strings(value, credential))
            result = manager.start(task, context, config, progress_reporter=report)
            self.job_backends[identifier] = backend
            self.jobs[identifier] = result["job_id"]
            self.store.update_task(identifier, task_root=str(config.task_root or config.workspace), token_budget=config.max_total_tokens,
                                   write_paths=[str(path) for path in config.write_paths] if config.write_paths is not None else None)
        except Exception as exc:
            error = scrub_strings(str(exc), credential)
            self.store.update_task(identifier, status="failed", finished_at=time.time(), error=error, failure=classify_error(error))
            raise ValueError(error) from None
        threading.Thread(target=self.watch, args=(identifier, credential), daemon=True).start()
        return {"ok": True, "job_id": identifier, "status": "queued"}

    def watch(self, identifier, key):
        upstream = self.jobs[identifier]
        manager = self.managers[self.job_backends.get(identifier, 'deepseek')]
        try:
            previous_telemetry = None
            while True:
                stored = self.store.task(identifier)
                if stored["cancel_requested"]:
                    manager.cancel(upstream)
                outcome = manager.result(upstream)
                if outcome["ready"]:
                    # Keep the final tool/result evidence, but scrub the selected credential.
                    safe = scrub_strings(outcome, key)
                    self.store.update_task(identifier, status=safe["status"], finished_at=time.time(),
                                           result=safe.get("result"), error=safe.get("error"),
                                           telemetry=safe.get('telemetry') or stored.get('telemetry'),
                                           verification={'status': 'pending' if safe['status'] == 'completed' else 'not_applicable'},
                                           failure=classify_error(safe.get("error")) if safe["status"] == "failed" else None)
                    return
                telemetry = outcome.get('telemetry')
                if telemetry is not None and telemetry != previous_telemetry:
                    safe_telemetry = scrub_strings(telemetry, key)
                    self.store.update_task(identifier, telemetry=safe_telemetry)
                    previous_telemetry = telemetry
                if not stored["started_at"]:
                    self.store.update_task(identifier, started_at=time.time())
                if not stored["cancel_requested"] and stored["status"] == "queued":
                    self.store.update_task(identifier, status="running")
                time.sleep(.2)
        except Exception as exc:
            # Do not release the global slot if the upstream worker might still be writing.
            manager.cancel(upstream)
            while not manager.result(upstream)["ready"]:
                time.sleep(.2)
            self.store.update_task(identifier, status="failed", finished_at=time.time(), error=type(exc).__name__)

    def owned(self, identifier):
        self.store.reap()
        task = self.store.task(identifier)
        if task["workspace"] != canonical(self.workspace):
            raise ValueError("此任务属于另一个工作区")
        return task

    def recovery(self):
        return Config(api_key="", workspace=self.workspace, allowed_tools=[])


def serve():
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    relay = Relay()
    mcp = FastMCP("codexrelay", instructions="CodexRelay: delegate suitable independent work through these tools. Check ping workspace before delegation. A disabled/busy response means continue yourself. Never change global or project switches through shell to bypass the user. " + HOST_INSTRUCTIONS)
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    mutating = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)

    def safe(operation):
        try:
            return encode(operation())
        except Exception as exc:
            return encode({"ok": False, "error": str(exc)})

    @mcp.tool(annotations=readonly)
    def ping() -> str:
        """Inspect live delegation settings and verify this instance's workspace."""
        relay.store.heartbeat(relay.instance, relay.workspace)
        return encode({"ok": True, "version": __version__, "workspace": str(relay.workspace), "enabled": relay.store.setting("enabled"), "instance": relay.instance, "active_backend": relay.store.setting('active_backend','deepseek')})

    @mcp.tool(annotations=mutating)
    def start_deepseek(task: str, context: str = "", model: Literal["flash", "pro"] = "flash", token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Start an independently executing coding task. Return job_id for status/control."""
        return safe(lambda: relay.start(task, context, "coding", model, token_budget, task_root, write_paths, backend="deepseek"))

    @mcp.tool(annotations=mutating)
    def start_deepseek_readonly(task: str, context: str = "", model: Literal["flash", "pro"] = "flash", token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Start pure file analysis; only Read, Glob and Grep, no commands or writes."""
        return safe(lambda: relay.start(task, context, "readonly", model, token_budget, task_root, write_paths, backend="deepseek"))

    async def delegate(task, context, mode, model, token_budget=None, task_root=None, write_paths=None, backend="deepseek"):
        try:
            started = relay.start(task, context, mode, model, token_budget, task_root, write_paths, backend=backend)
        except Exception as exc:
            return encode({"ok": False, "error": str(exc)})
        identifier = started["job_id"]
        try:
            while True:
                result = relay.store.task(identifier)
                if result["status"] not in {"queued", "running", "cancelling"}:
                    return encode({"ok": result["status"] == "completed", "job_id": identifier, **result})
                await asyncio.sleep(.2)
        except asyncio.CancelledError:
            relay.store.cancel(identifier)
            raise

    @mcp.tool(annotations=mutating)
    async def delegate_to_deepseek(task: str, context: str = "", model: Literal["flash", "pro"] = "flash", token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Execute a complete coding task and wait for its result; host verifies changes."""
        return await delegate(task, context, "coding", model, token_budget, task_root, write_paths)

    @mcp.tool(annotations=mutating)
    async def delegate_to_deepseek_readonly(task: str, context: str = "", model: Literal["flash", "pro"] = "flash", token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Execute pure Read/Glob/Grep analysis and wait for its result."""
        return await delegate(task, context, "readonly", model, token_budget, task_root, write_paths)

    @mcp.tool(annotations=readonly)
    def get_deepseek_status(job_id: str) -> str:
        """Get task state; available even when delegation is switched off."""
        return safe(lambda: {k: v for k, v in relay.owned(job_id).items() if k != "result"})

    @mcp.tool(annotations=readonly)
    def get_deepseek_result(job_id: str) -> str:
        """Retrieve persisted result; completed is execution status, not host verification."""
        def result():
            task = relay.owned(job_id)
            return {**task, 'ready': task['status'] not in {'queued','running','cancelling'}}
        return safe(result)

    @mcp.tool(annotations=mutating)
    def cancel_deepseek(job_id: str) -> str:
        """Request termination; existing file changes are preserved for review."""
        def cancel():
            relay.owned(job_id)
            return relay.store.cancel(job_id)
        return safe(cancel)

    @mcp.tool(annotations=mutating)
    def send_deepseek_message(job_id: str, message: str) -> str:
        """Steer this instance's running background task at the next safe point."""
        def send():
            relay.owned(job_id)
            if job_id not in relay.jobs:
                raise ValueError("此任务不属于当前运行中的引擎实例")
            backend = relay.job_backends.get(job_id, 'deepseek')
            if not next(item['capabilities'] for item in catalog() if item['id'] == backend)['steering']:
                raise ValueError(f"{backend} 后端不支持中途指令")
            result = relay.managers[backend].send_message(relay.jobs[job_id], message)
            return {**result, 'job_id': job_id}
        return safe(send)

    @mcp.tool(annotations=mutating)
    def record_deepseek_verification(job_id: str, passed: bool, evidence: str) -> str:
        """Record the parent's independent verification after execution, with evidence."""
        def verify():
            task = relay.owned(job_id)
            if task['status'] != 'completed' or not evidence.strip():
                raise ValueError('verification requires completed execution and non-empty evidence')
            value = {'status': 'passed' if passed else 'failed', 'evidence': evidence, 'verified_at': time.time()}
            relay.store.update_task(job_id, verification=value, result={**task['result'], 'verification': value})
            return {'ok': True, 'job_id': job_id, 'verification': value}
        return safe(verify)

    @mcp.tool(annotations=mutating)
    def get_deepseek_recovery() -> str:
        """Get pending file mutations. Also inspect command-generated workspace changes."""
        return safe(lambda: {"ok": True, **recovery(relay.workspace)})

    @mcp.tool(annotations=mutating)
    def acknowledge_deepseek_mutations(transaction_ids: list[str]) -> str:
        """Acknowledge only exact transaction IDs after verifying files and hashes."""
        def acknowledge():
            removed, pending = acknowledge_with_lease(relay.recovery(), transaction_ids)
            return {"ok": True, "acknowledged": removed, "pending": pending}
        return safe(acknowledge)

    @mcp.tool(annotations=mutating)
    def start_agent(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Start a coding task on the selected backend; retain job_id for control."""
        return safe(lambda: relay.start(task,context,'coding',model,token_budget,task_root,write_paths))

    @mcp.tool(annotations=mutating)
    def start_agent_readonly(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Start strict file-only inspection on the selected backend."""
        return safe(lambda: relay.start(task,context,'readonly',model,token_budget,task_root,write_paths))

    @mcp.tool(annotations=mutating)
    async def delegate_to_agent(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Execute a selected-backend coding task and wait; parent verifies the evidence."""
        return await delegate(task,context,'coding',model,token_budget,task_root,write_paths,backend=None)

    @mcp.tool(annotations=mutating)
    async def delegate_to_agent_readonly(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Execute strict Read/Glob/Grep inspection on the selected backend and wait."""
        return await delegate(task,context,'readonly',model,token_budget,task_root,write_paths,backend=None)

    for function, name, annotations in [
        (get_deepseek_status,'get_task_status',readonly), (get_deepseek_result,'get_task_result',readonly),
        (cancel_deepseek,'cancel_task',mutating), (send_deepseek_message,'send_task_message',mutating),
        (record_deepseek_verification,'record_task_verification',mutating),
        (get_deepseek_recovery,'get_task_recovery',mutating),
        (acknowledge_deepseek_mutations,'acknowledge_task_mutations',mutating),
    ]:
        mcp.add_tool(function,name=name,annotations=annotations)

    @mcp.tool(annotations=mutating)
    def acknowledge_workspace_review(review_id: str, state_sha256: str, evidence: str) -> str:
        """Acknowledge the exact observed workspace state. The evidence is recorded locally before the check is cleared."""
        return safe(lambda: review.acknowledge(relay.recovery(),review_id,state_sha256,evidence))

    mcp.run(transport="stdio")
