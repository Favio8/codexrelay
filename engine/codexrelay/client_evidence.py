"""Explicit client identity and body-free, engine-observed MCP invocation evidence.

A server instance can serve multiple chats. Never infer a root session from its
PID, workspace or timestamps. A binding is an explicit report, not attestation.
"""
import json
import time
import uuid
from .state import canonical

CLIENT_KINDS = {'desktop', 'cli', 'local_stdio', 'app_self_test', 'other'}


def bind(store, *, instance, workspace, session_id, root_session_id, client_kind,
         confirmed, client_version=None, observation_id=None):
    if confirmed is not True:
        raise ValueError('Explicit session/root confirmation is required')
    if client_kind not in CLIENT_KINDS:
        raise ValueError('Unknown client kind')
    for value in (session_id, root_session_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError('A bounded explicit session identity is required')
    payload = dict(id=uuid.uuid4().hex, instance=instance, workspace=canonical(workspace),
                   session_id=session_id, root_session_id=root_session_id,
                   client_kind=client_kind, client_version=client_version,
                   observation_id=observation_id, source='explicit_client_report',
                   created_at=time.time(), attested=False)
    with store.connect() as db:
        db.execute('INSERT INTO client_bindings VALUES (?,?)', (payload['id'], json.dumps(payload)))
    return payload


def get_binding(store, binding_id, workspace, instance=None):
    with store.connect() as db:
        row = db.execute('SELECT payload FROM client_bindings WHERE id=?', (binding_id,)).fetchone()
    if not row:
        raise ValueError('Client binding not found')
    binding = json.loads(row[0])
    if binding['workspace'] != canonical(workspace):
        raise ValueError('Client binding belongs to another workspace')
    if instance and binding['instance'] != instance:
        raise ValueError('Client binding belongs to an earlier MCP instance; bind again')
    return binding


def record_call(store, *, instance, workspace, tool, success, arguments=None, result=None):
    """Persist identifiers only. Do not retain prompts, paths, secrets or outputs."""
    arguments, result = arguments or {}, result if isinstance(result, dict) else {}
    refs = {}
    for key in ('group_id', 'checkpoint_id', 'verification_id', 'trial_id', 'experiment_id'):
        value = result.get(key, arguments.get(key))
        if isinstance(value, str) and len(value) <= 256:
            refs[key] = value
    run_id = result.get('run_id', result.get('job_id', arguments.get('job_id')))
    caller_binding = arguments.get('client_binding_id')
    caller_binding = caller_binding if isinstance(caller_binding, str) and len(caller_binding) <= 256 else None
    owner_binding = None
    with store.connect() as db:
        if run_id:
            row = db.execute('SELECT payload FROM tasks WHERE id=?', (run_id,)).fetchone()
            if row:
                task = json.loads(row[0])
                refs.update({k: task[k] for k in ('group_id', 'trial_id', 'experiment_id') if task.get(k)})
                owner_binding = task.get('client_binding_id')
                refs['run_id'] = run_id
        if refs.get('group_id') and not owner_binding:
            row = db.execute('SELECT payload FROM task_groups WHERE id=?', (refs['group_id'],)).fetchone()
            if row:
                group = json.loads(row[0])
                refs.update({k: group[k] for k in ('trial_id', 'experiment_id') if group.get(k)})
                owner_binding = group.get('client_binding_id')
        # Keep the historical owner reference for readers of older records;
        # it is never evidence of who made this particular invocation.
        if owner_binding:
            refs['client_binding_id'] = owner_binding
        payload = dict(id=uuid.uuid4().hex, instance=instance, workspace=canonical(workspace),
                       tool=tool, success=bool(success), occurred_at=time.time(),
                       source='engine_observed_call', owner_binding_id=owner_binding,
                       caller_binding_id=caller_binding, **refs)
        db.execute('INSERT INTO client_calls VALUES (?,?,?,?)',
                   (payload['id'], instance, payload['occurred_at'], json.dumps(payload)))
    return payload


def chain(store, *, workspace, group_id=None, instance=None):
    normalized = canonical(workspace)
    with store.connect() as db:
        # This is an explicit evidence read, never part of snapshot polling.
        records = [json.loads(r[0]) for r in db.execute('SELECT payload FROM client_calls ORDER BY occurred_at')]
    records = [r for r in records if r['workspace'] == normalized and
               (not group_id or r.get('group_id') == group_id) and (not instance or r['instance'] == instance)]
    return {'source': 'engine_observed_call', 'calls': records,
            'client_identity': 'explicit_report_only', 'caller_identity_field': 'caller_binding_id',
            'owner_identity_field': 'owner_binding_id',
            'legacy_client_binding_id': 'task owner reference; not caller identity',
            'external_model_quality': 'not_established'}
