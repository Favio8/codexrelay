"""MCP adapter around the upstream execution loop, with live desktop controls."""
import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from deepseek_mcp.config import Config, DEFAULT_ALLOWED_TOOLS
from deepseek_mcp.host_instructions import HOST_INSTRUCTIONS
from deepseek_mcp.job_manager import DeepSeekJobManager, validate_delegation_input
from deepseek_mcp.transaction_recovery import query_with_lease, acknowledge_with_lease
from .state import Store, canonical
from .vault import Vault
from .failures import classify_error


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class Relay:
    def __init__(self, store=None, vault=None, workspace=None, manager=None):
        self.store = store or Store()
        self.vault = vault or Vault()
        self.workspace = Path(workspace or Path.cwd()).resolve(strict=True)
        self.manager = manager or DeepSeekJobManager()
        self.jobs = {}
        self.instance = uuid.uuid4().hex
        self.store.discover(self.workspace)
        self.store.heartbeat(self.instance, self.workspace)

    def config(self, provider, mode, slot):
        key = self.vault.get(provider["credential_id"])
        from urllib.parse import urlsplit
        if not key and urlsplit(provider["base_url"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("供应商未配置 API Key")
        return Config(api_key=key or "local", workspace=self.workspace,
                      model=provider[slot + "_model"], base_url=provider["base_url"],
                      allowed_tools=[tool for tool in provider.get("allowed_tools", DEFAULT_ALLOWED_TOOLS) if mode != "readonly" or tool in {"Read", "Glob", "Grep"}],
                      delegation_capability=mode, flash_model=provider["flash_model"], pro_model=provider["pro_model"],
                      reasoning_effort=provider.get("pro_reasoning_effort", provider["reasoning_effort"]) if slot == "pro" else provider["reasoning_effort"],
                      flash_reasoning_effort=provider["reasoning_effort"], pro_reasoning_effort=provider.get("pro_reasoning_effort", provider["reasoning_effort"]),
                      max_total_tokens=provider.get("max_total_tokens", 1_000_000), max_output_tokens=provider.get("max_output_tokens", 16384),
                      max_turns=provider.get("max_turns", 50), max_run_seconds=provider.get("max_run_seconds", 18000))

    def start(self, task, context, mode, slot):
        validate_delegation_input(task, context)
        if slot not in {"flash", "pro"}:
            raise ValueError("model must be flash or pro")
        provider = self.store.provider()
        config = self.config(provider, mode, slot)
        record = self.store.admit(task, self.workspace, mode, slot, expected_provider=provider)
        identifier = record["id"]
        try:
            result = self.manager.start(task, context, config)
            self.jobs[identifier] = result["job_id"]
        except Exception as exc:
            error = str(exc).replace(config.api_key, "[REDACTED]")
            self.store.update_task(identifier, status="failed", finished_at=time.time(), error=error, failure=classify_error(error))
            raise ValueError(str(exc).replace(config.api_key, "[REDACTED]")) from None
        threading.Thread(target=self.watch, args=(identifier, config.api_key), daemon=True).start()
        return {"ok": True, "job_id": identifier, "status": "queued"}

    def watch(self, identifier, key):
        upstream = self.jobs[identifier]
        try:
            while True:
                stored = self.store.task(identifier)
                if stored["cancel_requested"]:
                    self.manager.cancel(upstream)
                outcome = self.manager.result(upstream)
                if outcome["ready"]:
                    # Keep the final tool/result evidence, but scrub the selected credential.
                    safe = json.loads(json.dumps(outcome, default=str).replace(key, "[REDACTED]")) if key else outcome
                    self.store.update_task(identifier, status=safe["status"], finished_at=time.time(),
                                           result=safe.get("result"), error=safe.get("error"),
                                           failure=classify_error(safe.get("error")) if safe["status"] == "failed" else None)
                    return
                if not stored["started_at"]:
                    self.store.update_task(identifier, started_at=time.time())
                if not stored["cancel_requested"] and stored["status"] == "queued":
                    self.store.update_task(identifier, status="running")
                time.sleep(.2)
        except Exception as exc:
            # Do not release the global slot if the upstream worker might still be writing.
            self.manager.cancel(upstream)
            while not self.manager.result(upstream)["ready"]:
                time.sleep(.2)
            self.store.update_task(identifier, status="failed", finished_at=time.time(), error=type(exc).__name__)

    def owned(self, identifier):
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
        return encode({"ok": True, "version": "0.1.2", "workspace": str(relay.workspace), "enabled": relay.store.setting("enabled"), "instance": relay.instance})

    @mcp.tool(annotations=mutating)
    def start_deepseek(task: str, context: str = "", model: Literal["flash", "pro"] = "flash") -> str:
        """Start an independently executing coding task. Return job_id for status/control."""
        return safe(lambda: relay.start(task, context, "coding", model))

    @mcp.tool(annotations=mutating)
    def start_deepseek_readonly(task: str, context: str = "", model: Literal["flash", "pro"] = "flash") -> str:
        """Start pure file analysis; only Read, Glob and Grep, no commands or writes."""
        return safe(lambda: relay.start(task, context, "readonly", model))

    async def delegate(task, context, mode, model):
        try:
            started = relay.start(task, context, mode, model)
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
    async def delegate_to_deepseek(task: str, context: str = "", model: Literal["flash", "pro"] = "flash") -> str:
        """Execute a complete coding task and wait for its result; host verifies changes."""
        return await delegate(task, context, "coding", model)

    @mcp.tool(annotations=mutating)
    async def delegate_to_deepseek_readonly(task: str, context: str = "", model: Literal["flash", "pro"] = "flash") -> str:
        """Execute pure Read/Glob/Grep analysis and wait for its result."""
        return await delegate(task, context, "readonly", model)

    @mcp.tool(annotations=readonly)
    def get_deepseek_status(job_id: str) -> str:
        """Get task state; available even when delegation is switched off."""
        return safe(lambda: {k: v for k, v in relay.owned(job_id).items() if k != "result"})

    @mcp.tool(annotations=readonly)
    def get_deepseek_result(job_id: str) -> str:
        """Retrieve persisted result; completed is execution status, not host verification."""
        return safe(lambda: {**relay.owned(job_id), "ready": relay.owned(job_id)["status"] not in {"queued", "running", "cancelling"}})

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
            return relay.manager.send_message(relay.jobs[job_id], message)
        return safe(send)

    @mcp.tool(annotations=mutating)
    def get_deepseek_recovery() -> str:
        """Get pending file mutations. Also inspect command-generated workspace changes."""
        return safe(lambda: {"ok": True, "pending": query_with_lease(relay.recovery())})

    @mcp.tool(annotations=mutating)
    def acknowledge_deepseek_mutations(transaction_ids: list[str]) -> str:
        """Acknowledge only exact transaction IDs after verifying files and hashes."""
        def acknowledge():
            removed, pending = acknowledge_with_lease(relay.recovery(), transaction_ids)
            return {"ok": True, "acknowledged": removed, "pending": pending}
        return safe(acknowledge)

    mcp.run(transport="stdio")
