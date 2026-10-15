"""Persistent execution contracts, checkpoints and parent-controlled continuation."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

KINDS = {'implementation', 'testing', 'review', 'analysis', 'documentation'}
ACTIVE = {'queued', 'running', 'cancelling', 'compacting', 'wrapping_up'}
DEFAULTS = dict(version=1, auto_continue=True, max_auto_continuations=3, context_optimization=True,
                warning_ratio=.8, wrap_ratio=.9, context_ratio=.8, summary_tokens=3000,
                unknown_context_bytes=128000)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def defaults(db):
    row = db.execute("SELECT value FROM settings WHERE key='execution_defaults'").fetchone()
    return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def set_defaults(store, **values):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = defaults(db)
        current = {**prior, **values, 'version': prior['version'] + 1}
        if set(values) - (set(DEFAULTS) - {'version'}):
            raise ValueError('unknown execution setting')
        for key in ('auto_continue', 'context_optimization'):
            if not isinstance(current[key], bool): raise ValueError(key + ' must be boolean')
        for key, lo, hi in [('max_auto_continuations', 0, 10), ('summary_tokens', 256, 4000), ('unknown_context_bytes', 16000, 2000000)]:
            v = current[key]
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi: raise ValueError(f'{key}: {lo}..{hi}')
        for key in ('warning_ratio', 'wrap_ratio', 'context_ratio'):
            v = current[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not .5 <= v <= .95: raise ValueError(key + ': .5.. .95')
        if current['warning_ratio'] > current['wrap_ratio']: raise ValueError('warning must precede wrapping')
        db.execute("INSERT OR REPLACE INTO settings VALUES ('execution_defaults',?)", (json.dumps(current),))
        return current


def load(db, group_id):
    row = db.execute('SELECT payload FROM task_groups WHERE id=?', (group_id,)).fetchone()
    if not row: raise ValueError('task group not found')
    return json.loads(row[0])


def save(db, group):
    db.execute('INSERT OR REPLACE INTO task_groups VALUES (?,?)', (group['group_id'], json.dumps(group, ensure_ascii=False)))


def create(db, workspace, goal, kind, mode, root, paths, constraints='', acceptance='', title=None):
    if kind not in KINDS or mode not in {'readonly', 'coding'}: raise ValueError('invalid task contract')
    if kind in {'review', 'analysis'} and mode != 'readonly': raise ValueError('review/analysis require readonly; command validation is a testing task')
    if not isinstance(goal, str) or not goal.strip(): raise ValueError('goal required')
    policy = defaults(db)
    identifier = uuid.uuid4().hex
    contract = dict(version=1, original_goal=goal, constraints=constraints, acceptance=acceptance, task_kind=kind,
                    capability=mode, task_root=str(root or workspace), write_paths=paths)
    group = dict(group_id=identifier, workspace=workspace, title=(title or goal)[:160], original_goal=goal,
                 task_kind=kind, task_contract=contract, policy_snapshot=policy, status='ready', version=1,
                 created_at=time.time(), max_auto_continuations=policy['max_auto_continuations'] if policy['auto_continue'] else 0,
                 used_continuations=0, authorization_extensions=[], latest_run_id=None, latest_checkpoint_id=None,
                 verification_head=None, next_round_budget=None, run_ids=[], completion_source=None)
    save(db, group)
    return group


def get(store, group_id):
    with store.connect() as db:
        group = load(db, group_id)
        runs = [json.loads(r[0]) for r in db.execute('SELECT payload FROM tasks WHERE id IN (SELECT run_id FROM group_runs WHERE group_id=?) ORDER BY rowid', (group_id,))]
        group['runs'] = [{k: t.get(k) for k in ('id', 'round_index', 'status', 'backend_id', 'model', 'token_budget', 'budget_source', 'verification', 'checkpoint', 'created_at', 'finished_at')} for t in runs]
        reported = [(t.get('telemetry') or {}).get('tokens', {}).get('total') for t in runs]
        group['usage_summary'] = {'reported_tokens': sum(v for v in reported if v is not None),
                                 'metered_tokens': sum((t.get('telemetry') or {}).get('budget', {}).get('metered_tokens', 0) for t in runs),
                                 'has_gaps': any(v is None or (t.get('telemetry') or {}).get('tokens', {}).get('responses_without_usage', 0) for v, t in zip(reported, runs))}
        from .usage import normalize_tokens, estimate, amount, add_bounds
        from decimal import Decimal
        cost = [Decimal(0), Decimal(0)]
        priced = 0
        for run in runs:
            tokens, _ = normalize_tokens(run)
            bounds = estimate(tokens, run.get('usage_price')) if tokens and run.get('usage_price') else None
            if bounds is not None:
                add_bounds(cost, bounds); priced += 1
        group['usage_summary'].update(cost=amount(cost) if priced else None, priced_runs=priced, unpriced_runs=len(runs) - priced)
        group['remaining_continuations'] = max(0, group['max_auto_continuations'] - group['used_continuations'])
        active_provider = db.execute("SELECT value FROM settings WHERE key='active_provider_id'").fetchone()
        provider_row = db.execute('SELECT payload FROM providers WHERE id=?', (json.loads(active_provider[0]),)).fetchone() if active_provider else None
        provider_default = json.loads(provider_row[0]).get('max_total_tokens', 1000000) if provider_row else None
        group['next_effective_budget'] = group['next_round_budget'] if group['next_round_budget'] is not None else provider_default
        group['next_budget_source'] = 'user_task_authorization' if group['next_round_budget'] is not None else 'provider_default'
        setting = db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
        project = db.execute('SELECT disabled FROM projects WHERE path=?', (group['workspace'],)).fetchone()
        if group['status'] == 'ready_to_continue' and (not json.loads(setting[0]) or project and project[0]):
            group['status'] = 'delegation_disabled'
        group['next_action'] = {'awaiting_verification': 'verify_then_resume', 'ready_to_continue': 'resume_verified_remaining',
                                'awaiting_authorization': 'authorization_required', 'delegation_disabled': 'delegation_disabled'}.get(group['status'], group['status'])
        return group


def extend(store, group_id, expected_version, idempotency_key, add_continuations=0, next_round_budget=None):
    if not isinstance(idempotency_key, str) or not idempotency_key.strip(): raise ValueError('idempotency key required')
    payload = dict(add_continuations=add_continuations, next_round_budget=next_round_budget)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        group = load(db, group_id)
        for entry in group['authorization_extensions']:
            if entry['idempotency_key'] == idempotency_key:
                if entry['payload'] != payload: raise ValueError('idempotency key conflict')
                return group
        if group['version'] != expected_version: raise ValueError('AUTHORIZATION_VERSION_STALE: refresh task details')
        if group['status'] in {'completed', 'completed_by_parent'}: raise ValueError('completed group cannot be extended')
        if isinstance(add_continuations, bool) or not isinstance(add_continuations, int) or not 0 <= add_continuations <= 10: raise ValueError('add_continuations: 0..10')
        if next_round_budget is not None and (isinstance(next_round_budget, bool) or not isinstance(next_round_budget, int) or not 32768 <= next_round_budget <= 20000000): raise ValueError('budget: 32768..20000000')
        if not add_continuations and next_round_budget is None: raise ValueError('empty authorization')
        old = dict(max_auto_continuations=group['max_auto_continuations'], next_round_budget=group['next_round_budget'])
        group['max_auto_continuations'] += add_continuations
        if next_round_budget is not None: group['next_round_budget'] = next_round_budget
        group['version'] += 1
        group['authorization_extensions'].append(dict(id=uuid.uuid4().hex, idempotency_key=idempotency_key,
            source='app_user', at=time.time(), policy_version=group['policy_snapshot']['version'], payload=payload,
            old=old, new=dict(max_auto_continuations=group['max_auto_continuations'], next_round_budget=group['next_round_budget'])))
        if group['status'] == 'awaiting_authorization': group['status'] = 'ready_to_continue'
        save(db, group)
        return group


def file_versions(task, evidence):
    from deepseek_mcp.safety import normalized_path
    workspace = Path(task['workspace'])
    root = Path(task.get('task_root') or workspace)
    paths = set(task.get('write_paths') or [])
    for item in evidence:
        value = item.get('arguments', {}).get('path')
        if value and item['name'] in {'Read', 'Write', 'Edit', 'NotebookEdit'}: paths.add(value)
    result = {}
    for value in paths:
        path = Path(value)
        path = normalized_path((path if path.is_absolute() else root / path).resolve())
        if not path.is_relative_to(workspace.resolve()): continue
        key = os.path.normcase(str(path))
        try: result[key] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        except OSError: result[key] = 'unreadable'
    return result


def checkpoint(db, task):
    if not task.get('group_id'): return
    progress = (task.get('telemetry') or {}).get('progress', {})
    previous_body = None
    if task.get('checkpoint'):
        row = db.execute('SELECT body FROM checkpoints WHERE id=?', (task['checkpoint']['checkpoint_id'],)).fetchone()
        previous_body = json.loads(row[0]) if row else None
    if 'tool_evidence' not in progress and previous_body:
        progress = {**previous_body['progress'], **progress}
    if task['status'] in ACTIVE and (task.get('telemetry') or {}).get('active_tool'): return
    version_key = digest([progress, task['status']])
    if task.get('checkpoint', {}).get('evidence_revision') == version_key: return
    group = load(db, task['group_id'])
    sequence = (task.get('checkpoint') or {}).get('sequence', 0) + 1
    identifier = uuid.uuid4().hex
    telemetry = task.get('telemetry') or {}
    evidence = progress.get('tool_evidence', [])
    references = []
    for index, item in enumerate(evidence, 1):
        if 'result' in item:
            db.execute('INSERT OR IGNORE INTO execution_evidence VALUES (?,?,?)', (task['id'], index, json.dumps(item, ensure_ascii=False)))
            references.append({k: v for k, v in item.items() if k != 'result'} | {'result_sha256': hashlib.sha256(item['result'].encode('utf-8', errors='replace')).hexdigest()})
        else: references.append(item)
    progress = {**progress, 'tool_evidence': references}
    files = file_versions(task, evidence)
    # Include observed command changes, even when write_paths was not explicit.
    for key in ('added', 'modified', 'deleted'):
        for value in telemetry.get('workspace_changes', {}).get(key, []):
            path = Path(task['workspace']) / value
            try: files[os.path.normcase(str(path.resolve()))] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            except OSError: files[os.path.normcase(str(path.resolve()))] = 'unreadable'
    body = dict(schema_version=1, group_id=group['group_id'], run_id=task['id'], checkpoint_id=identifier,
                sequence=sequence, created_at=time.time(), fixed_contract=group['task_contract'],
                environment={k: task.get(k) for k in ('backend_id', 'backend_version', 'provider_revision', 'model', 'context_window',
                             'mcp_instance', 'client_binding_id', 'experiment_id', 'trial_id')},
                progress=progress, files=files, mutations=telemetry.get('mutations'), workspace_changes=telemetry.get('workspace_changes'),
                workspace_review=telemetry.get('workspace_review'), tests=telemetry.get('bash'),
                active_tool=telemetry.get('active_tool'),
                context_archive_ref=task.get('context_archive_ref'),
                budget={'limit': task.get('token_budget'), 'source': task.get('budget_source'), 'tokens': telemetry.get('tokens'),
                        'metered': telemetry.get('budget'), 'used_continuations': group['used_continuations'],
                        'request_events': telemetry.get('usage_events', [])},
                termination={'status': task['status'], 'error': task.get('error'), 'needs_verification': True},
                evidence_revision=version_key, gaps=progress.get('gaps', ['semantic_summary_unavailable']))
    sha = digest(body)
    meta = dict(checkpoint_id=identifier, sequence=sequence, sha256=sha, evidence_revision=version_key,
                created_at=body['created_at'], context_epoch=progress.get('context_epoch', 0), gaps=body['gaps'])
    db.execute('INSERT INTO checkpoints VALUES (?,?,?,?)', (identifier, task['id'], json.dumps(meta), json.dumps(body, ensure_ascii=False)))
    task['checkpoint'] = meta
    group['latest_checkpoint_id'] = identifier
    group['latest_run_id'] = task['id']
    if task['status'] not in ACTIVE:
        if group['status'] != 'cancelled': group['status'] = 'awaiting_verification'
        task['verification'] = {'status': 'pending'}
    save(db, group)


def read_checkpoint(store, checkpoint_id, include_context=False, context_epoch=None):
    with store.connect() as db:
        row = db.execute('SELECT metadata,body FROM checkpoints WHERE id=?', (checkpoint_id,)).fetchone()
        if not row: raise ValueError('checkpoint not found')
        meta, body = json.loads(row[0]), json.loads(row[1])
        if digest(body) != meta['sha256']: raise ValueError('checkpoint integrity mismatch')
        records = [json.loads(r[0]) for r in db.execute('SELECT payload FROM execution_evidence WHERE run_id=? AND sequence<=? ORDER BY sequence', (body['run_id'], len(body['progress'].get('tool_evidence', []))))]
        refs = body['progress'].get('tool_evidence', [])
        if refs and all('result' in item for item in refs): records = refs  # Development schema 2 compatibility.
        if len(records) != len(refs): raise ValueError('checkpoint evidence missing')
        for reference, record in zip(refs, records):
            if reference.get('result_sha256') and (reference['tool_id'] != record['tool_id'] or reference['result_sha256'] != hashlib.sha256(record['result'].encode('utf-8', errors='replace')).hexdigest()):
                raise ValueError('checkpoint evidence integrity mismatch')
        result = {**body, 'sha256': meta['sha256'], 'tool_evidence_records': records}
        if include_context:
            archive = body.get('context_archive_ref')
            if archive:
                epoch = archive['epoch'] if context_epoch is None else context_epoch
                if isinstance(epoch, bool) or not isinstance(epoch, int) or not 0 <= epoch <= body['progress'].get('context_epoch', 0): raise ValueError('invalid archived context epoch')
                row = db.execute('SELECT sha256,body FROM context_archives WHERE run_id=? AND epoch=?', (body['run_id'], epoch)).fetchone()
                if not row or epoch == archive['epoch'] and row[0] != archive['sha256'] or row and digest(json.loads(row[1])) != row[0]:
                    raise ValueError('context archive integrity mismatch')
                result['context_archive_messages'] = json.loads(row[1])
                result['context_archive_epoch'] = epoch
        return result


def stale_files(body):
    changed = []
    for value, expected in body['files'].items():
        path = Path(value)
        try: actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        except OSError: actual = 'unreadable'
        if actual != expected or actual == 'unreadable': changed.append(value)
    return changed


def verify(store, task, passed, evidence, disposition=None, checkpoint_id=None, checkpoint_sha256=None,
           completed_items=None, remaining_items=None, checked_files=None, parent_completed=False):
    if task['status'] in ACTIVE or not evidence.strip(): raise ValueError('verification requires ended execution and evidence')
    disposition = disposition or ('verified_complete' if passed else 'rejected')
    if disposition not in {'verified_complete', 'verified_partial', 'rejected', 'needs_more_evidence'}: raise ValueError('invalid disposition')
    if disposition == 'verified_partial' and not remaining_items: raise ValueError('remaining items required for continuation')
    meta = task.get('checkpoint')
    changed = []
    if meta:
        if checkpoint_id != meta['checkpoint_id'] or checkpoint_sha256 != meta['sha256']:
            raise ValueError('CHECKPOINT_VERSION_STALE: provide current checkpoint ID and hash')
        body = read_checkpoint(store, checkpoint_id)
        changed = stale_files(body)
        if not body['progress'].get('tool_evidence') and disposition in {'verified_complete', 'verified_partial'} and not checked_files:
            raise ValueError('NEEDS_MORE_EVIDENCE: provide independently checked files or sources')
    inspected = {}
    from deepseek_mcp.safety import normalized_path
    for value in checked_files or []:
        path = Path(value)
        path = normalized_path((path if path.is_absolute() else Path(task['task_root']) / path).resolve())
        if not path.is_relative_to(Path(task['workspace']).resolve()) or path.exists() and not path.is_file() or not path.exists() and not parent_completed:
            raise ValueError('checked_files must be actual workspace files')
        inspected[os.path.normcase(str(path))] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if changed and not (parent_completed and passed and disposition == 'verified_complete' and set(changed) <= set(inspected)):
        raise ValueError('EVIDENCE_STALE: ' + json.dumps(changed))
    value = dict(id=uuid.uuid4().hex, status='passed' if passed else 'failed', disposition=disposition,
                 evidence=evidence, verified_at=time.time(), checkpoint_id=checkpoint_id, checkpoint_sha256=checkpoint_sha256,
                 completed_items=completed_items or [], remaining_items=remaining_items or [], checked_files=checked_files or [], evidence_files=inspected,
                 parent_rechecked_changes=changed if parent_completed else [])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = json.loads(db.execute('SELECT payload FROM tasks WHERE id=?', (task['id'],)).fetchone()[0])
        if current.get('checkpoint') != meta: raise ValueError('CHECKPOINT_VERSION_STALE')
        current['verification'] = value
        if current.get('result'): current['result']['verification'] = value
        saved_result = db.execute('SELECT body FROM task_bodies WHERE run_id=?', (task['id'],)).fetchone()
        if saved_result:
            result = json.loads(saved_result[0]); result['verification'] = value
            db.execute('UPDATE task_bodies SET body=? WHERE run_id=?', (json.dumps(result, ensure_ascii=False), task['id']))
        db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(current, ensure_ascii=False), task['id']))
        if task.get('group_id'):
            group = load(db, task['group_id'])
            if group['latest_run_id'] != task['id']: raise ValueError('verification must target latest run')
            group['verification_head'] = value
            group['version'] += 1
            if disposition == 'verified_complete':
                group['status'] = 'completed_by_parent' if parent_completed else 'completed'
                group['completion_source'] = 'parent' if parent_completed else 'verified_external'
            elif group['status'] != 'cancelled':
                group['status'] = ('ready_to_continue' if group['used_continuations'] < group['max_auto_continuations'] else 'awaiting_authorization') if disposition == 'verified_partial' else 'needs_parent_decision'
            save(db, group)
    return {'ok': True, 'job_id': task['id'], 'verification': value}
