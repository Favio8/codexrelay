"""Durable workspace checks for changes that a tool transaction cannot describe."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from deepseek_mcp.execution_lock import DEFAULT_LOCK_DIRECTORY, _secure_directory
from deepseek_mcp.transaction_recovery import _acquire, _release, TransactionRecoveryRequired
from deepseek_mcp.workspace_changes import snapshot, changes

DIRECTORY = DEFAULT_LOCK_DIRECTORY.parent / 'relay-reviews'
EMPTY = {'complete': False, 'added': [], 'modified': [], 'deleted': [], 'unreadable': ['workspace_changes_failed'], 'kind': 'observed_diff'}


def location(config):
    identity = hashlib.sha256(bytes.fromhex(config.expected_workspace_identity)).hexdigest()
    return DIRECTORY / (identity + '.json')


def audit_location(config):
    identity = hashlib.sha256(bytes.fromhex(config.expected_workspace_identity)).hexdigest()
    return DIRECTORY / (identity + '-acks.jsonl')


def _prepare_directory():
    # The same private-directory rules the engine applies to its locks and journals: a
    # readable or writable-by-others copy would expose workspace paths and hashes, and could
    # let another local subject forge or clear a pending check.
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    _secure_directory(DIRECTORY)


def save(config, value):
    _prepare_directory()
    target = location(config)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    temporary = DIRECTORY / f'{target.name}.{uuid.uuid4().hex}.tmp'
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def append_audit(config, record):
    _prepare_directory()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(audit_location(config), flags, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def require_clear(config):
    if location(config).exists():
        raise TransactionRecoveryRequired('WORKSPACE_REVIEW_REQUIRED: inspect get_task_recovery and acknowledge_workspace_review before new delegation')


def begin(config, baseline):
    value = {'review_id': uuid.uuid4().hex, 'workspace': str(config.workspace), 'backend': 'pi', 'baseline': baseline, 'status': 'running'}
    save(config, value)
    return value


def finish(config, value):
    baseline = value['baseline']
    observed = changes(config.workspace, baseline)
    if observed['complete'] and not any(observed[k] for k in ('added', 'modified', 'deleted')):
        location(config).unlink(missing_ok=True)
        return None
    value.update(status='pending', observed=observed)
    save(config, value)
    return {'review_id': value['review_id'], 'status': 'pending', 'observed': observed}


def block(config, value, error):
    """Keep the gate closed when the observed-change bookkeeping itself failed."""
    blocked = dict(value)
    blocked.update(status='pending', observed=dict(EMPTY), incomplete=True, error=error)
    save(config, blocked)
    return {'review_id': blocked['review_id'], 'status': 'pending', 'observed': blocked['observed']}


def _current(config):
    target = location(config)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding='utf-8'))
        baseline = value.pop('baseline')
        baseline[2] = {key: tuple(item) for key, item in baseline[2].items()}
        observed = changes(config.workspace, baseline)
        if value.get('incomplete'):
            observed['complete'] = False
            observed['unreadable'] = sorted(set(observed['unreadable'] + ['workspace_changes_failed']))
        hashes, errors, _ = snapshot(config.workspace)
    except (ValueError, TypeError, KeyError, IndexError, OSError) as error:
        # An unreadable record still blocks delegation; it must not break the recovery query.
        return {'review_id': 'unreadable', 'status': 'pending', 'corrupt': True,
                'error': type(error).__name__, 'observed': dict(EMPTY)}
    value['observed'] = observed
    value['current_hashes'] = hashes
    if value['status'] == 'running':
        value['status'] = 'interrupted'
    # The host verifies this exact state, so the status it is shown has to be part of the digest.
    value['state_sha256'] = hashlib.sha256(json.dumps([hashes, errors, value['status']], sort_keys=True).encode()).hexdigest()
    return value


def query(config):
    lease = _acquire(config, None)
    try:
        value = _current(config)
        return [value] if value else []
    finally:
        _release(lease)


def acknowledge(config, review_id, state_sha256, evidence):
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('请填写独立检查的依据')
    lease = _acquire(config, None)
    try:
        value = _current(config)
        if not value or value['review_id'] != review_id or value['state_sha256'] != state_sha256:
            raise ValueError('检查事项或文件状态已变化，请重新核验')
        observed = value.get('observed') or {}
        # Written before the review is removed: losing the audit would leave an unreviewed
        # backend change with no trace of who released it.
        append_audit(config, {'review_id': review_id, 'evidence': evidence.strip(),
                              'acknowledged_at': time.time(), 'backend': value.get('backend'),
                              'workspace': value.get('workspace'),
                              'observed_complete': bool(observed.get('complete')),
                              'observed': {key: len(observed.get(key) or {}) for key in ('added', 'modified', 'deleted')}})
        location(config).unlink()
        return {'ok': True, 'acknowledged': review_id, 'evidence': evidence.strip()}
    finally:
        _release(lease)
