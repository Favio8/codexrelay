"""One small transactional database shared by the desktop and MCP instances."""
import contextlib
import ctypes
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


def home():
    return Path(os.environ.get("CODEXRELAY_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CodexRelay")


def canonical(path):
    value = str(Path(path).resolve(strict=True))
    if value.startswith('\\\\?\\UNC\\'): value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'): value = value[4:]
    return os.path.normcase(value)


def process_identity(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return str(pid)
        except OSError:
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
    handle = kernel.OpenProcess(0x1000, 0, pid)
    if not handle:
        return "unknown" if ctypes.get_last_error() == 5 else None
    try:
        values = [ctypes.c_ulonglong() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return "unknown"
        if values[1].value:
            return None
        return str(values[0].value)
    finally:
        kernel.CloseHandle(handle)


class Store:
    SCHEMA_VERSION = 4

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else home()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "relay.db"
        self._wal = True
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] >= self.SCHEMA_VERSION:
                return
            # RPC starts a fresh process for each refresh. Initialize once, atomically,
            # rather than acquiring schema/write locks on every read request.
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (path TEXT PRIMARY KEY, disabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL, active INTEGER NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_task ON tasks(active) WHERE active=1;
                CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_events (job_id TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,sequence));
                CREATE INDEX IF NOT EXISTS usage_events_date ON usage_events(occurred_at);
                INSERT OR IGNORE INTO settings VALUES ('enabled','false');
                INSERT OR IGNORE INTO settings VALUES ('active_provider_id','null');
                CREATE TABLE IF NOT EXISTS task_groups (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS group_runs (group_id TEXT NOT NULL, run_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS checkpoints (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, metadata TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS checkpoints_run ON checkpoints(run_id);
                CREATE TABLE IF NOT EXISTS continuations (group_id TEXT NOT NULL, key TEXT NOT NULL, request_sha256 TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(group_id,key));
                CREATE TABLE IF NOT EXISTS execution_evidence (run_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,sequence));
                CREATE TABLE IF NOT EXISTS task_bodies (run_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS context_archives (run_id TEXT NOT NULL, epoch INTEGER NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(run_id,epoch));
                PRAGMA user_version=4;
            """)
            # Move old full results without changing their execution/verification semantics.
            # Counters remain in the lightweight row for existing usage aggregation.
            for row in db.execute('SELECT id,payload FROM tasks').fetchall():
                try: record = json.loads(row[1])
                except (ValueError, TypeError): continue
                result = record.get('result') if isinstance(record, dict) else None
                if result is None or db.execute('SELECT 1 FROM task_bodies WHERE run_id=?', (row[0],)).fetchone(): continue
                db.execute('INSERT INTO task_bodies VALUES (?,?)', (row[0], json.dumps(result, ensure_ascii=False)))
                record['result'] = {'tokens': result['tokens']} if isinstance(result, dict) and 'tokens' in result else None
                db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(record, ensure_ascii=False), row[0]))
            # Alias spellings must not bypass a saved project disable policy.
            for row in db.execute('SELECT path,disabled FROM projects').fetchall():
                try: normalized = canonical(row[0])
                except OSError: continue
                if normalized != row[0]:
                    db.execute('INSERT INTO projects VALUES (?,?) ON CONFLICT(path) DO UPDATE SET disabled=MAX(disabled,excluded.disabled)', (normalized, row[1]))
                    db.execute('DELETE FROM projects WHERE path=?', (row[0],))

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            # Statistics scans run while delegation accepts, polls and settles tasks; WAL keeps
            # those readers from blocking the writers instead of serialising the whole GUI.
            if self._wal:
                mode = db.execute('PRAGMA journal_mode').fetchone()[0].lower()
                if mode != 'wal':
                    mode = db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()
                self._wal = mode == 'wal'
                if not self._wal:
                    db.execute('PRAGMA busy_timeout=10000')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def providers(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM providers")]

    def provider(self, identifier=None):
        identifier = identifier or self.setting("active_provider_id")
        with self.connect() as db:
            row = db.execute("SELECT payload FROM providers WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("请先配置并选择供应商")
            return json.loads(row[0])

    def save_provider(self, provider):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO providers VALUES (?,?)", (provider["id"], json.dumps(provider)))
            current = json.loads(db.execute("SELECT value FROM settings WHERE key='active_provider_id'").fetchone()[0])
            if not current:
                db.execute("UPDATE settings SET value=? WHERE key='active_provider_id'", (json.dumps(provider["id"]),))

    def delete_provider(self, identifier):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT payload FROM tasks WHERE active=1"):
                if json.loads(row[0])["provider_id"] == identifier:
                    raise ValueError("此供应商仍有运行中的任务")
            db.execute("DELETE FROM providers WHERE id=?", (identifier,))
            if json.loads(db.execute("SELECT value FROM settings WHERE key='active_provider_id'").fetchone()[0]) == identifier:
                db.execute("UPDATE settings SET value='null' WHERE key='active_provider_id'")

    def set_project(self, path, disabled):
        path = canonical(path)
        if not Path(path).is_dir():
            raise ValueError("请选择项目目录")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR REPLACE INTO projects VALUES (?,?)", (path, int(disabled)))

    def discover(self, path):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO projects VALUES (?,0)", (canonical(path),))

    def _reap(self, db):
        for row in db.execute("SELECT id,payload FROM tasks WHERE active=1").fetchall():
            task = json.loads(row["payload"])
            identity = process_identity(task["pid"])
            if identity is None or (identity != "unknown" and identity != task["process_identity"]):
                if task.get('backend_probe'):
                    db.execute('DELETE FROM tasks WHERE id=?', (row['id'],))
                    continue
                task.update(status="interrupted", finished_at=time.time(), error="引擎已退出；请检查工作区修改后继续。")
                telemetry = task.get('telemetry') or {}
                request = telemetry.get('request') or {}
                if request.get('in_flight'):
                    estimate = request.get('input_estimate', 0)
                    estimate = estimate if isinstance(estimate, int) and not isinstance(estimate, bool) and estimate >= 0 else 0
                    tokens = telemetry.setdefault('tokens', {})
                    tokens.update(interrupted_request=True, responses_without_usage=tokens.get('responses_without_usage', 0) + 1)
                    budget = telemetry.setdefault('budget', {})
                    budget['metered_tokens'] = budget.get('metered_tokens', 0) + estimate
                    events = telemetry.setdefault('usage_events', [])
                    sequence = max([event.get('sequence', 0) for event in events if isinstance(event, dict)] or [0]) + 1
                    event = dict(sequence=sequence, occurred_at=time.time(), request_kind=request.get('kind', 'execution'),
                                 available=False, prompt=None, completion=None, input_estimate=estimate, gap='engine_disconnected')
                    events.append(event)
                    db.execute('INSERT OR IGNORE INTO usage_events VALUES (?,?,?,?)', (task['id'], sequence, event['occurred_at'], json.dumps(event)))
                    request['in_flight'] = False
                    task['telemetry'] = telemetry
                from .task_groups import checkpoint
                checkpoint(db, task)
                db.execute("UPDATE tasks SET payload=?,active=0 WHERE id=?", (json.dumps(task), row["id"]))
                from .usage import bump_revision
                bump_revision(db)

    def reap(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._reap(db)

    @contextlib.contextmanager
    def backend_probe(self, provider, backend, slot, workspace):
        identifier = uuid.uuid4().hex
        task = dict(id=identifier, title='后端连接测试', workspace=canonical(workspace), mode='readonly',
                    model=provider[slot+'_model'], provider_id=provider['id'], provider_name=provider['name'],
                    backend_id=backend, backend_probe=True, status='running', created_at=time.time(),
                    started_at=time.time(), finished_at=None, cancel_requested=False,
                    pid=os.getpid(), process_identity=process_identity(os.getpid()), result=None, error=None)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._reap(db)
            if db.execute('SELECT 1 FROM tasks WHERE active=1').fetchone():
                raise ValueError('已有委托任务执行中，已跳过后端测试以避免并发消耗同一供应商额度')
            db.execute('INSERT INTO tasks VALUES (?,?,1)', (identifier, json.dumps(task)))
        try:
            yield
        finally:
            with self.connect() as db:
                db.execute('DELETE FROM tasks WHERE id=?', (identifier,))

    def admit(self, title, workspace, mode, model_slot, expected_provider=None, backend='deepseek', expected_backend=None, backend_capabilities=None,
              group_id=None, run_details=None, continuation=None):
        workspace = canonical(workspace)
        if model_slot not in {"flash", "pro"} or mode not in {"coding", "readonly"}:
            raise ValueError("不支持的执行模式或模型档位")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reap(db)
            from . import task_groups as groups
            details = run_details or {}
            if continuation:
                prior = db.execute('SELECT request_sha256,run_id FROM continuations WHERE group_id=? AND key=?', (group_id, continuation['idempotency_key'])).fetchone()
                if prior:
                    if prior[0] != continuation['request_sha256']: raise ValueError('idempotency key conflict')
                    return {**json.loads(db.execute('SELECT payload FROM tasks WHERE id=?', (prior[1],)).fetchone()[0]), 'idempotent_replay': True}
            settings = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM settings")}
            if not settings["enabled"]:
                raise ValueError("DELEGATION_DISABLED: 新委托已关闭，请由 Codex 继续完成任务。")
            project = db.execute("SELECT disabled FROM projects WHERE path=?", (workspace,)).fetchone()
            if project and project[0]:
                raise ValueError("PROJECT_DISABLED: 此项目不接受新委托，请由 Codex 继续。")
            if db.execute("SELECT 1 FROM tasks WHERE active=1").fetchone():
                raise ValueError("BUSY: 已有子代理正在执行，请等待或由 Codex 继续。")
            row = db.execute("SELECT payload FROM providers WHERE id=?", (settings["active_provider_id"],)).fetchone()
            if not row:
                raise ValueError("请先选择供应商")
            provider = json.loads(row[0])
            if expected_provider is not None and expected_provider != provider:
                raise ValueError("供应商配置已变化，请重新提交任务")
            if expected_backend is not None and settings.get('active_backend','deepseek') != expected_backend:
                raise ValueError('执行后端已变化，请重新提交任务')
            group = groups.load(db, group_id) if group_id else groups.create(db, workspace, title, details.get('task_kind', 'implementation' if mode == 'coding' else 'analysis'), mode,
                details.get('task_root'), details.get('write_paths'), details.get('context', ''), details.get('acceptance', ''))
            if group['workspace'] != workspace: raise ValueError('group belongs to another workspace')
            if continuation:
                if group['status'] == 'cancelled' and not continuation.get('manual_resume'): raise ValueError('TASK_GROUP_CANCELLED')
                if group['used_continuations'] >= group['max_auto_continuations']: raise ValueError('AUTHORIZATION_REQUIRED: continuation limit reached')
                previous = json.loads(db.execute('SELECT payload FROM tasks WHERE id=?', (group['latest_run_id'],)).fetchone()[0])
                if previous['status'] in groups.ACTIVE: raise ValueError('previous run has not exited')
                verification = group.get('verification_head') or {}
                if verification.get('id') != continuation['verification_id'] or verification.get('disposition') != 'verified_partial': raise ValueError('valid partial verification required')
                if verification.get('checkpoint_id') != continuation['checkpoint_id'] or verification.get('checkpoint_sha256') != continuation['checkpoint_sha256']: raise ValueError('CHECKPOINT_VERSION_STALE')
                if (previous.get('failure') or {}).get('code') in {'provider_auth', 'provider_quota'} and previous.get('provider_revision') == provider.get('revision'): raise ValueError('PROVIDER_CONFIGURATION_REQUIRED: do not repeat authentication/quota failure')
                if title != '\n'.join(verification['remaining_items']): raise ValueError('remaining_task must match independently verified remaining_items')
                row = db.execute('SELECT body FROM checkpoints WHERE id=?', (continuation['checkpoint_id'],)).fetchone()
                changed = groups.stale_files(json.loads(row[0]))
                changed += groups.stale_files({'files': verification.get('evidence_files', {})})
                if changed: raise ValueError('EVIDENCE_STALE: ' + json.dumps(changed))
                group['used_continuations'] += 1
            elif group['latest_run_id']:
                raise ValueError('existing group requires resume_agent; do not reset continuation counts')
            identifier = uuid.uuid4().hex
            from .usage import price_snapshot, bump_revision
            from . import __version__
            task = dict(id=identifier, title=title[:160], workspace=workspace, mode=mode,
                        model=provider[model_slot + "_model"], provider_id=provider["id"], provider_name=provider["name"],
                        provider_revision=provider.get('revision'), backend_id=backend,
                        backend_version='1.2.0' if backend=='pi' else '7e540263+relay-'+__version__,
                        adapter_version=__version__, schema_version=1,
                        backend_capabilities=backend_capabilities,
                        usage_price=price_snapshot(db, provider, provider[model_slot + '_model']),
                        status="queued", created_at=time.time(), started_at=None, finished_at=None,
                        cancel_requested=False, pid=os.getpid(), process_identity=process_identity(os.getpid()),
                        result=None, error=None)
            task.update(details, group_id=group['group_id'], round_index=len(group['run_ids']) + 1,
                        continuation_of=group['latest_run_id'], task_input=title,
                        execution_policy=group['policy_snapshot'])
            group['run_ids'].append(identifier)
            group.update(latest_run_id=identifier, status='executing', version=group['version'] + 1)
            groups.save(db, group)
            db.execute('INSERT INTO group_runs VALUES (?,?)', (group['group_id'], identifier))
            if continuation:
                db.execute('INSERT INTO continuations VALUES (?,?,?,?)', (group['group_id'], continuation['idempotency_key'], continuation['request_sha256'], identifier))
            db.execute("INSERT INTO tasks VALUES (?,?,1)", (identifier, json.dumps(task)))
            bump_revision(db)
            db.execute("INSERT OR IGNORE INTO projects VALUES (?,0)", (workspace,))
            return task

    def update_task(self, identifier, **updates):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("找不到任务")
            task = json.loads(row[0])
            task.update(updates)
            archive = (task.get('telemetry') or {}).pop('context_archive_payload', None)
            if archive:
                from .task_groups import digest
                sha = digest(archive['messages'])
                db.execute('INSERT OR REPLACE INTO context_archives VALUES (?,?,?,?)', (identifier, archive['epoch'], sha, json.dumps(archive['messages'], ensure_ascii=False)))
                task['context_archive_ref'] = {'epoch': archive['epoch'], 'sha256': sha}
            telemetry = updates.get('telemetry') or {}
            from .usage import valid_timestamp
            for event in telemetry.get('usage_events', []):
                # A malformed event must not roll back the status, result and telemetry of the
                # whole task; an event whose time is unusable would also fall out of every window.
                if not isinstance(event, dict):
                    continue
                sequence, occurred = event.get('sequence'), event.get('occurred_at')
                if (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1
                        or not valid_timestamp(occurred)):
                    continue
                added = db.execute('INSERT OR IGNORE INTO usage_events VALUES (?,?,?,?)',
                    (identifier,sequence,float(occurred),json.dumps(event, ensure_ascii=False))).rowcount
                if added:
                    from .usage import bump_revision
                    bump_revision(db)
            from .task_groups import checkpoint, ACTIVE
            checkpoint(db, task)
            result = task.get('result')
            if result is not None:
                if isinstance(result, dict): result.pop('context_archive_payload', None)
                db.execute('INSERT OR REPLACE INTO task_bodies VALUES (?,?)', (identifier, json.dumps(result, ensure_ascii=False)))
                task['result'] = {'tokens': result['tokens']} if isinstance(result, dict) and 'tokens' in result else None
            progress = (task.get('telemetry') or {}).get('progress')
            if progress:
                task['telemetry']['progress'] = {k: v for k, v in progress.items() if k not in {'tool_evidence', 'summary', 'context_events'}}
            active = task["status"] in ACTIVE
            db.execute("UPDATE tasks SET payload=?,active=? WHERE id=?", (json.dumps(task, ensure_ascii=False), int(active), identifier))
            if not active:
                from .usage import bump_revision
                bump_revision(db)
            return {**task, 'result': result} if result is not None else task

    def task(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("找不到任务")
            task = json.loads(row[0])
            body = db.execute('SELECT body FROM task_bodies WHERE run_id=?', (identifier,)).fetchone()
            if body: task['result'] = json.loads(body[0])
            return task

    def cancel(self, identifier):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,active FROM tasks WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("找不到任务")
            task = json.loads(row[0])
            if row[1]:
                task.update(cancel_requested=True, status="cancelling")
                db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(task), identifier))
                if task.get('group_id'):
                    from .task_groups import load, save
                    group = load(db, task['group_id'])
                    group.update(status='cancelled', version=group['version'] + 1)
                    save(db, group)
            return task

    def snapshot(self):
        with self.connect() as db:
            db.execute('BEGIN')
            if db.execute('SELECT 1 FROM tasks WHERE active=1 LIMIT 1').fetchone():
                # Reaping may write; end the read snapshot before taking the writer
                # lock so it cannot fail with SQLITE_BUSY_SNAPSHOT on an upgrade.
                db.rollback()
                db.execute('BEGIN IMMEDIATE')
                self._reap(db)
            result = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM settings")}
            result.pop('usage_prices', None)
            result['usage_source'] = str(self.directory.resolve())
            result["providers"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM providers")]
            result["projects"] = [dict(path=r[0], disabled=bool(r[1])) for r in db.execute("SELECT path,disabled FROM projects")]
            result["tasks"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM tasks ORDER BY rowid DESC LIMIT 100")]
            from .task_groups import defaults
            result['execution_defaults'] = defaults(db)
            result['task_groups'] = [json.loads(r[0]) for r in db.execute('SELECT payload FROM task_groups ORDER BY rowid DESC LIMIT 100')]
            for task in result['tasks']:
                progress = (task.get('telemetry') or {}).get('progress')
                if progress:
                    task['telemetry']['progress'] = {k: v for k, v in progress.items() if k not in {'tool_evidence', 'summary', 'context_events'}}
                task['result'] = None  # Long bodies are available through get_task only.
            result["instances"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM instances")]
            return result

    def heartbeat(self, identifier, workspace):
        payload = dict(id=identifier, workspace=str(workspace), pid=os.getpid(), process_identity=process_identity(os.getpid()), seen_at=time.time())
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO instances VALUES (?,?)", (identifier, json.dumps(payload)))
