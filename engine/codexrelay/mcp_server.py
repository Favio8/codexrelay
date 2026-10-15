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
from . import task_groups as groups
from . import client_evidence


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
        self.workspace = Path(canonical(workspace or Path.cwd()))
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

    def start(self, task, context, mode, slot, token_budget=None, task_root=None, write_paths=None, backend=None,
              group_id=None, task_kind=None, acceptance='', continuation=None):
        validate_delegation_input(task, context)
        if not self.store.setting('enabled', False): raise ValueError('DELEGATION_DISABLED: 新委托已关闭，请由 Codex 继续。')
        with self.store.connect() as db:
            project = db.execute('SELECT disabled FROM projects WHERE path=?', (canonical(self.workspace),)).fetchone()
            if project and project[0]: raise ValueError('PROJECT_DISABLED: 此项目不接受新委托。')
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
        group = groups.get(self.store, group_id) if group_id else None
        # A prepared evaluation workspace must not silently run outside its ledger.
        with self.store.connect() as db:
            trials = [json.loads(row[0]) for row in db.execute('SELECT payload FROM evaluation_trials')]
        scoped_trials = [trial for trial in trials if Path(trial['workspace']).resolve() == self.workspace.resolve()]
        if scoped_trials:
            trial = next((trial for trial in scoped_trials if trial['state'] == 'running'), None)
            if not trial or not group or group.get('experiment_id') != trial['experiment_id'] or group.get('trial_id') != trial['id']:
                raise ValueError('EXPERIMENT_BINDING_REQUIRED: use the running trial and its explicitly bound TaskGroup')
            if not continuation and task != group['original_goal']:
                raise ValueError('EXPERIMENT_INPUT_CHANGED: first Run must use the frozen task text')
        if group:
            contract = group['task_contract']
            if mode != contract['capability']: raise ValueError('capability cannot expand or change')
            if task_kind and task_kind != contract['task_kind']: raise ValueError('task purpose is frozen')
            task_kind = contract['task_kind']
            if task_root is not None or write_paths is not None: raise ValueError('group scope is frozen; use its original contract')
            task_root, write_paths = contract['task_root'], contract['write_paths']
        task_kind = task_kind or ('implementation' if mode == 'coding' else 'analysis')
        if task_kind not in groups.KINDS: raise ValueError('invalid task_kind')
        if task_kind in {'review', 'analysis'} and mode != 'readonly': raise ValueError('review/analysis require readonly')
        if task_kind == 'testing':
            config = replace(config, allowed_tools=[t for t in config.allowed_tools if t not in {'Write', 'Edit', 'NotebookEdit'}], write_paths=[])
            write_paths = write_paths or []
        policy = group['policy_snapshot'] if group else self.store.setting('execution_defaults', groups.DEFAULTS)
        source = 'codex_override' if token_budget is not None else 'provider_default'
        authorized_limit = (group or {}).get('next_round_budget') if continuation else None
        if authorized_limit is not None:
            config = replace(config, max_total_tokens=authorized_limit)
            source = 'user_task_authorization'
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
        config = replace(config, task_kind=task_kind, execution_options=policy, budget_source=source,
                         task_contract=group['task_contract'] if group else {})
        pending = recovery(self.workspace)
        if pending['pending']: raise ValueError('RECOVERY_REQUIRED: pending transaction_ids=' + json.dumps([r['transaction_id'] for r in pending['pending']]))
        if pending['checks']: raise ValueError('WORKSPACE_REVIEW_REQUIRED: pending review_ids=' + json.dumps([r['review_id'] for r in pending['checks']]))
        details = dict(task_root=str(config.task_root or config.workspace), token_budget=config.max_total_tokens, budget_source=source,
                       write_paths=[str(p) for p in config.write_paths] if config.write_paths is not None else None,
                       task_kind=task_kind, context=context, acceptance=acceptance, context_window=config.context_window,
                       reasoning_effort=config.reasoning_effort, model_slot=slot)
        if group:
            details.update({key: group[key] for key in ('client_binding_id', 'experiment_id', 'trial_id') if group.get(key)})
        details['mcp_instance'] = self.instance
        record = self.store.admit(task, self.workspace, mode, slot, expected_provider=provider, backend=backend, expected_backend=selected, backend_capabilities=capabilities,
                                  group_id=group_id, run_details=details, continuation=continuation)
        identifier = record["id"]
        if record.get('idempotent_replay'): return {'ok': True, 'job_id': identifier, 'group_id': record['group_id'], 'status': record['status'], 'idempotent_replay': True}
        actual_group = groups.get(self.store, record['group_id'])
        config = replace(config, task_contract=actual_group['task_contract'])
        try:
            if record.get('experiment_id'):
                from .evaluation import Evaluation
                from .request_gate import ExperimentRequestGate
                Evaluation(self.store).attach_run(record['trial_id'], record['group_id'], identifier, backend,
                    workspace=str(self.workspace), model=record['model'], provider_revision=record.get('provider_revision'),
                    reasoning=config.reasoning_effort)
                config = replace(config, request_guard=ExperimentRequestGate(self.store, record))
            def report(value):
                phase = value.get('execution_phase', 'running')
                current = self.store.task(identifier)
                updates = {'telemetry': scrub_strings(value, credential)}
                if current['status'] in groups.ACTIVE and not current['cancel_requested']:
                    updates['status'] = phase if phase in {'compacting', 'wrapping_up'} else 'running'
                return self.store.update_task(identifier, **updates)
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
        return {"ok": True, "job_id": identifier, "group_id": record['group_id'], 'round_index': record['round_index'], "status": "queued"}

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
                    failure = classify_error(safe.get('error')) if safe['status'] == 'failed' else None
                    status = 'budget_stopped' if failure and failure['code'] == 'local_token_budget' else 'no_progress' if failure and failure['code'] == 'no_progress' else safe['status']
                    self.store.update_task(identifier, status=status, finished_at=time.time(),
                                           result=safe.get("result"), error=safe.get("error"),
                                           telemetry=safe.get('telemetry') or stored.get('telemetry'),
                                           verification={'status': 'pending' if safe['status'] == 'completed' else 'not_applicable'},
                                           failure=failure)
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

    def create_group(self, goal, task_kind='implementation', capability='coding', task_root=None, write_paths=None, constraints='', acceptance='', title=None,
                     client_binding_id=None, experiment_id=None, trial_id=None):
        validate_delegation_input(goal, constraints)
        if task_kind == 'testing' and write_paths is None: write_paths = []
        config = Config(api_key='', workspace=self.workspace, allowed_tools=[], task_root=Path(task_root) if task_root else None,
                        write_paths=[Path(p) for p in write_paths] if write_paths is not None else None)
        if client_binding_id:
            binding = client_evidence.get_binding(self.store, client_binding_id, self.workspace, self.instance)
        if bool(experiment_id) != bool(trial_id):
            raise ValueError('experiment_id and trial_id must be provided together')
        if experiment_id:
            from .evaluation import Evaluation
            experiment = Evaluation(self.store).describe(experiment_id)
            trial = next((t for t in experiment['trials'] if t['id'] == trial_id), None)
            if not trial or trial['state'] != 'running' or trial['group'] == 'A' or Path(trial['workspace']).resolve() != self.workspace.resolve():
                raise ValueError('Experiment group requires its running isolated trial workspace')
            reported = trial.get('binding') or {}
            if not client_binding_id or not reported or any(binding[key] != reported[key] for key in ('session_id','root_session_id')):
                raise ValueError('Experiment group requires the trial\'s explicitly bound root/session')
            kind = {'desktop':'desktop','cli':'cli','local_stdio':'local_substitute',
                    'app_self_test':'local_substitute','other':'local_substitute'}.get(binding['client_kind'])
            if kind != reported.get('client_kind'):
                raise ValueError('EXPERIMENT_CLIENT_CHANGED: client kind must match the explicit trial binding')
            expected = Evaluation(self.store).trial_instruction(trial_id)['client_binding_steps'][1]['params']
            if (goal != expected['goal'] or task_kind != expected['task_kind'] or capability != expected['capability'] or
                Path(config.task_root or self.workspace).resolve() != Path(expected['task_root']).resolve() or
                {str(p).casefold() for p in config.write_paths or []} != {str(Path(p).resolve()).casefold() for p in expected['write_paths']}):
                raise ValueError('EXPERIMENT_CONTRACT_CHANGED: use the frozen task, kind, capability and scope')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group = groups.create(db, canonical(self.workspace), goal, task_kind, capability,
                config.task_root or self.workspace, [str(p) for p in config.write_paths] if config.write_paths is not None else None,
                constraints, acceptance, title)
            group.update({key: value for key, value in dict(client_binding_id=client_binding_id,
                          experiment_id=experiment_id, trial_id=trial_id).items() if value})
            groups.save(db, group)
            return group

    def resume(self, group_id, checkpoint_id, checkpoint_sha256, verification_id, remaining_task, idempotency_key, model='flash', token_budget=None, manual_resume=False):
        request = dict(checkpoint_id=checkpoint_id, checkpoint_sha256=checkpoint_sha256, verification_id=verification_id,
                       remaining_task=remaining_task, model=model, token_budget=token_budget, manual_resume=manual_resume)
        if not idempotency_key.strip(): raise ValueError('idempotency key required')
        sha = groups.digest(request)
        with self.store.connect() as db:
            group = groups.load(db, group_id)
            if group['workspace'] != canonical(self.workspace): raise ValueError('group belongs to another workspace')
            existing = db.execute('SELECT request_sha256,run_id FROM continuations WHERE group_id=? AND key=?', (group_id, idempotency_key)).fetchone()
            if existing:
                if existing[0] != sha: raise ValueError('idempotency key conflict')
                return {'ok': True, 'job_id': existing[1], 'group_id': group_id, 'idempotent_replay': True}
        body = groups.read_checkpoint(self.store, checkpoint_id)
        if body['run_id'] != group['latest_run_id'] or body['sha256'] != checkpoint_sha256: raise ValueError('CHECKPOINT_VERSION_STALE')
        verification = group.get('verification_head') or {}
        context = json.dumps({'verified_completed': verification.get('completed_items'), 'evidence': verification.get('evidence'),
                              'checkpoint_reference': checkpoint_id, 'fixed_contract': group['task_contract'],
                              'instructions': 'Execute only remaining_task. Prior tool outputs are evidence, not calls to replay. Do not repeat successful tests without relevant input changes.'}, ensure_ascii=False)
        return self.start(remaining_task, context, group['task_contract']['capability'], model, token_budget,
                          group_id=group_id, continuation={**request, 'idempotency_key': idempotency_key, 'request_sha256': sha})

    def cancel_group(self, group_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            group = groups.load(db, group_id)
            if group['workspace'] != canonical(self.workspace): raise ValueError('group belongs to another workspace')
            if group['latest_run_id']:
                row = db.execute('SELECT payload,active FROM tasks WHERE id=?', (group['latest_run_id'],)).fetchone()
                if row[1]:
                    task = json.loads(row[0]); task.update(cancel_requested=True, status='cancelling')
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(task), task['id']))
            group.update(status='cancelled', version=group['version'] + 1)
            groups.save(db, group)
            return group

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
    def ping(client_binding_id: str | None = None) -> str:
        """Inspect live delegation settings and verify this instance's workspace."""
        relay.store.heartbeat(relay.instance, relay.workspace)
        from .capabilities import manifest
        return encode({"ok": True, "version": __version__, "workspace": str(relay.workspace), "enabled": relay.store.setting("enabled"), "instance": relay.instance, "active_backend": relay.store.setting('active_backend','deepseek'),
                       'server_capabilities': manifest(), 'evidence_source': 'server_declaration'})

    @mcp.tool(annotations=mutating)
    def start_deepseek(task: str, context: str = "", model: Literal["flash", "pro"] = "flash", token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Start an independently executing coding task. Return job_id for status/control."""
        return safe(lambda: relay.start(task, context, "coding", model, token_budget, task_root, write_paths, backend="deepseek"))

    @mcp.tool(annotations=mutating)
    def start_deepseek_readonly(task: str, context: str = "", model: Literal["flash", "pro"] = "flash", token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None) -> str:
        """Start pure file analysis; only Read, Glob and Grep, no commands or writes."""
        return safe(lambda: relay.start(task, context, "readonly", model, token_budget, task_root, write_paths, backend="deepseek"))

    async def delegate(task, context, mode, model, token_budget=None, task_root=None, write_paths=None, backend="deepseek", group_id=None, task_kind=None, acceptance=''):
        try:
            started = relay.start(task, context, mode, model, token_budget, task_root, write_paths, backend=backend, group_id=group_id, task_kind=task_kind, acceptance=acceptance)
        except Exception as exc:
            return encode({"ok": False, "error": str(exc)})
        identifier = started["job_id"]
        try:
            while True:
                result = relay.store.task(identifier)
                if result["status"] not in groups.ACTIVE:
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
            return {**task, 'ready': task['status'] not in groups.ACTIVE}
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
    def record_deepseek_verification(job_id: str, passed: bool, evidence: str,
          disposition: str | None = None, checkpoint_id: str | None = None, checkpoint_sha256: str | None = None,
          completed_items: list[str] | None = None, remaining_items: list[str] | None = None,
          checked_files: list[str] | None = None, parent_completed: bool = False) -> str:
        """Record the parent's independent verification after execution, with evidence."""
        def verify():
            task = relay.owned(job_id)
            if passed and disposition in {None, 'verified_complete'}:
                pending = recovery(relay.workspace)
                if pending['pending'] or pending['checks']:
                    raise ValueError('RECOVERY_REQUIRED: clear exact transactions and workspace checks before final acceptance')
            meta = task.get('checkpoint') or {}
            # Preserve the old passed/evidence signature. Explicit new dispositions must bind a version.
            cp_id = checkpoint_id if disposition else meta.get('checkpoint_id')
            cp_sha = checkpoint_sha256 if disposition else meta.get('sha256')
            return groups.verify(relay.store, task, passed, evidence, disposition, cp_id, cp_sha,
                                 completed_items, remaining_items, checked_files, parent_completed)
        return safe(verify)

    @mcp.tool(annotations=mutating)
    def get_deepseek_recovery(client_binding_id: str | None = None) -> str:
        """Get pending file mutations. Also inspect command-generated workspace changes."""
        return safe(lambda: {"ok": True, **recovery(relay.workspace)})

    @mcp.tool(annotations=mutating)
    def acknowledge_deepseek_mutations(transaction_ids: list[str], client_binding_id: str | None = None) -> str:
        """Acknowledge only exact transaction IDs after verifying files and hashes."""
        def acknowledge():
            removed, pending = acknowledge_with_lease(relay.recovery(), transaction_ids)
            return {"ok": True, "acknowledged": removed, "pending": pending}
        return safe(acknowledge)

    @mcp.tool(annotations=mutating)
    def start_agent(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None, group_id: str | None = None, task_kind: str | None = None, acceptance: str = '') -> str:
        """Start a coding task on the selected backend; retain job_id for control."""
        return safe(lambda: relay.start(task,context,'coding',model,token_budget,task_root,write_paths, group_id=group_id, task_kind=task_kind, acceptance=acceptance))

    @mcp.tool(annotations=mutating)
    def start_agent_readonly(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None, group_id: str | None = None, task_kind: str | None = None, acceptance: str = '') -> str:
        """Start strict file-only inspection on the selected backend."""
        return safe(lambda: relay.start(task,context,'readonly',model,token_budget,task_root,write_paths, group_id=group_id, task_kind=task_kind, acceptance=acceptance))

    @mcp.tool(annotations=mutating)
    async def delegate_to_agent(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None, group_id: str | None = None, task_kind: str | None = None, acceptance: str = '') -> str:
        """Execute a selected-backend coding task and wait; parent verifies the evidence."""
        return await delegate(task,context,'coding',model,token_budget,task_root,write_paths,backend=None, group_id=group_id, task_kind=task_kind, acceptance=acceptance)

    @mcp.tool(annotations=mutating)
    async def delegate_to_agent_readonly(task: str, context: str = '', model: Literal['flash','pro'] = 'flash', token_budget: int | None = None, task_root: str | None = None, write_paths: list[str] | None = None, group_id: str | None = None, task_kind: str | None = None, acceptance: str = '') -> str:
        """Execute strict Read/Glob/Grep inspection on the selected backend and wait."""
        return await delegate(task,context,'readonly',model,token_budget,task_root,write_paths,backend=None, group_id=group_id, task_kind=task_kind, acceptance=acceptance)

    for function, name, annotations in [
        (get_deepseek_status,'get_task_status',readonly), (get_deepseek_result,'get_task_result',readonly),
        (cancel_deepseek,'cancel_task',mutating), (send_deepseek_message,'send_task_message',mutating),
        (record_deepseek_verification,'record_task_verification',mutating),
        (get_deepseek_recovery,'get_task_recovery',mutating),
        (acknowledge_deepseek_mutations,'acknowledge_task_mutations',mutating),
    ]:
        mcp.add_tool(function,name=name,annotations=annotations)

    @mcp.tool(annotations=mutating)
    def acknowledge_workspace_review(review_id: str, state_sha256: str, evidence: str, client_binding_id: str | None = None) -> str:
        """Acknowledge the exact observed workspace state. The evidence is recorded locally before the check is cleared."""
        return safe(lambda: review.acknowledge(relay.recovery(),review_id,state_sha256,evidence))

    @mcp.tool(annotations=mutating)
    def create_task_group(goal: str, task_kind: str = 'implementation', capability: str = 'coding', task_root: str | None = None,
                          write_paths: list[str] | None = None, constraints: str = '', acceptance: str = '', title: str | None = None,
                          client_binding_id: str | None = None, experiment_id: str | None = None, trial_id: str | None = None) -> str:
        """Create a fixed contract without a model call. Reuse this group for the same goal; budget/counters never reset."""
        return safe(lambda: relay.create_group(goal, task_kind, capability, task_root, write_paths, constraints, acceptance, title,
                                             client_binding_id, experiment_id, trial_id))

    @mcp.tool(annotations=readonly)
    def get_task_group(group_id: str) -> str:
        """Read persisted runs, cumulative usage and remaining authorized continuations. Does not execute."""
        def read():
            group = groups.get(relay.store, group_id)
            if group['workspace'] != canonical(relay.workspace): raise ValueError('group belongs to another workspace')
            return group
        return safe(read)

    @mcp.tool(annotations=readonly)
    def get_task_checkpoint(checkpoint_id: str, include_context: bool = False, context_epoch: int | None = None) -> str:
        """Read the full immutable progress index, evidence and integrity hash on demand; verify real files separately."""
        def read():
            body = groups.read_checkpoint(relay.store, checkpoint_id, include_context, context_epoch)
            relay.owned(body['run_id'])
            return body
        return safe(read)

    @mcp.tool(annotations=mutating)
    def resume_agent(group_id: str, checkpoint_id: str, checkpoint_sha256: str, verification_id: str, remaining_task: str,
                     idempotency_key: str, model: Literal['flash', 'pro'] = 'flash', token_budget: int | None = None, manual_resume: bool = False) -> str:
        """Start only independently verified remaining_items in the same group. Requires current evidence, cleared recovery, switches and authorization. App alone grants extensions."""
        return safe(lambda: relay.resume(group_id, checkpoint_id, checkpoint_sha256, verification_id, remaining_task, idempotency_key, model, token_budget, manual_resume))

    @mcp.tool(annotations=mutating)
    def cancel_task_group(group_id: str) -> str:
        """Cancel the execution chain; a checkpoint never automatically resumes cancellation."""
        return safe(lambda: relay.cancel_group(group_id))

    @mcp.tool(annotations=mutating)
    def bind_client_session(session_id: str, root_session_id: str, client_kind: Literal['desktop','cli','local_stdio','app_self_test','other'],
                            confirmed: bool, client_version: str | None = None, observation_id: str | None = None) -> str:
        """Explicitly report this chat's identity. Pass the returned id to create_task_group. This is a report, not client attestation."""
        return safe(lambda: client_evidence.bind(relay.store, instance=relay.instance, workspace=relay.workspace,
                    session_id=session_id, root_session_id=root_session_id, client_kind=client_kind,
                    confirmed=confirmed, client_version=client_version, observation_id=observation_id))

    @mcp.tool(annotations=readonly)
    def get_client_evidence(group_id: str | None = None) -> str:
        """Read engine-observed invocation identities. Local stdio, CLI and Desktop reports remain distinct."""
        return safe(lambda: client_evidence.chain(relay.store, workspace=relay.workspace, group_id=group_id))

    from .call_audit import install_call_audit
    install_call_audit(mcp, relay)
    from .capabilities import assert_registered_tools
    assert_registered_tools([tool.name for tool in mcp._tool_manager.list_tools()])
    mcp.run(transport="stdio")
