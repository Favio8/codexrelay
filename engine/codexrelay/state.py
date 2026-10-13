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
    return os.path.normcase(str(Path(path).resolve(strict=True)))


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
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else home()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "relay.db"
        self._wal = True
        with self.connect() as db:
            db.executescript("""
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
            """)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            # Statistics scans run while delegation accepts, polls and settles tasks; WAL keeps
            # those readers from blocking the writers instead of serialising the whole GUI.
            if self._wal:
                db.execute('PRAGMA journal_mode=WAL')
                self._wal = db.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
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

    def admit(self, title, workspace, mode, model_slot, expected_provider=None, backend='deepseek', expected_backend=None, backend_capabilities=None):
        workspace = canonical(workspace)
        if model_slot not in {"flash", "pro"} or mode not in {"coding", "readonly"}:
            raise ValueError("不支持的执行模式或模型档位")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reap(db)
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
            active = task["status"] in {"queued", "running", "cancelling"}
            db.execute("UPDATE tasks SET payload=?,active=? WHERE id=?", (json.dumps(task, ensure_ascii=False), int(active), identifier))
            if not active:
                from .usage import bump_revision
                bump_revision(db)
            return task

    def task(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ValueError("找不到任务")
            return json.loads(row[0])

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
            return task

    def snapshot(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reap(db)
            result = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM settings")}
            result.pop('usage_prices', None)
            result['usage_source'] = str(self.directory.resolve())
            result["providers"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM providers")]
            result["projects"] = [dict(path=r[0], disabled=bool(r[1])) for r in db.execute("SELECT path,disabled FROM projects")]
            result["tasks"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM tasks ORDER BY rowid DESC LIMIT 100")]
            result["instances"] = [json.loads(r[0]) for r in db.execute("SELECT payload FROM instances")]
            return result

    def heartbeat(self, identifier, workspace):
        payload = dict(id=identifier, workspace=str(workspace), pid=os.getpid(), process_identity=process_identity(os.getpid()), seen_at=time.time())
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO instances VALUES (?,?)", (identifier, json.dumps(payload)))
