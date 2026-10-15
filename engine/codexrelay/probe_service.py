"""Probe evidence is immutable history; current badges are revision-checked views."""
import json
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from . import connection_probe as probe


def records(store, *, provider_id=None, limit=100):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('探针记录数量必须为 1 至 1000')
    with store.connect() as db:
        if provider_id is None:
            rows = db.execute('SELECT payload FROM probe_records ORDER BY occurred_at DESC LIMIT ?', (limit,)).fetchall()
        else:
            rows = db.execute("SELECT payload FROM probe_records WHERE json_extract(payload,'$.provider_id')=? ORDER BY occurred_at DESC LIMIT ?", (provider_id, limit)).fetchall()
    return [json.loads(row[0]) for row in rows]


def _record(store, record):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM providers WHERE id=?', (record['provider_id'],)).fetchone()
        current = json.loads(row[0]) if row else None
        record['current_revision'] = bool(current and current.get('revision') == record['provider_revision'])
        db.execute('UPDATE probe_records SET payload=? WHERE id=?', (json.dumps(record), record['probe_id']))
        if record['ok'] and record['current_revision']:
            identity = json.dumps([record[key] for key in ('slot', 'model', 'actual_model', 'reasoning_mode', 'backend', 'backend_version', 'adapter_version', 'verification')])
            current.setdefault('probe_evidence', {})[identity] = record
            if record['verification'] == 'quick':
                current['last_connection_test'] = record
            elif record['verification'] == 'full':
                current['last_test'] = record
            db.execute('UPDATE providers SET payload=? WHERE id=?', (json.dumps(current), current['id']))
        from .usage import bump_revision
        bump_revision(db)
    return record


def test(service, provider_id, slot='flash', verification='full', backend=None, probe_id=None, experiment_id=None):
    probe.validate_probe(slot, verification, allow_backend=backend is not None)
    if not isinstance(provider_id, str) or not provider_id:
        raise ValueError('请选择供应商')
    if probe_id is not None and (not isinstance(probe_id, str) or not 1 <= len(probe_id) <= 100):
        raise ValueError('探针标识无效')
    if experiment_id is not None and (not isinstance(experiment_id, str) or not 1 <= len(experiment_id) <= 100):
        raise ValueError('实验标识无效')
    started, occurred_at = time.monotonic(), time.time()
    provider = service.store.provider(provider_id)
    if experiment_id is not None:
        from .evaluation import Evaluation
        conditions = Evaluation(service.store).describe(experiment_id)['conditions']
        # Preparation probes may deliberately use ordinary reasoning. Prices still
        # belong to one frozen provider revision, selected model and slot.
        for field, actual in (('external_provider_revision', provider.get('revision')),
                              ('external_model', provider[slot + '_model']), ('external_slot', slot)):
            expected = conditions.get(field)
            if expected is None or not isinstance(expected, str) or expected.strip().lower() in {'', 'unknown'}:
                raise ValueError('PROBE_EXPERIMENT_IDENTITY_UNKNOWN: 实验尚未冻结 ' + field + '；请另建实验或不绑定实验单独记录探针。')
            if actual != expected:
                raise ValueError('PROBE_EXPERIMENT_IDENTITY_MISMATCH: 探针与实验冻结的 ' + field + ' 不同；请另建实验或不绑定实验单独记录。')
    credential = service.vault.get(provider['credential_id'])
    if not credential and urlsplit(provider['base_url']).hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('请先保存 API Key')
    backend_version = None
    if backend is not None:
        from .backends import catalog
        entry = next((item for item in catalog() if item['id'] == backend and item['installed']), None)
        if entry is None:
            raise ValueError('此执行后端尚未安装或不可用')
        backend_version = entry['version']
    identifier = probe_id or uuid.uuid4().hex
    class Cancellation:
        def is_set(self):
            return service.store.setting('probe_cancel:' + identifier, False)
    from deepseek_mcp.provider_settings import is_deepseek_endpoint
    from . import __version__
    reasoning = (provider.get('pro_reasoning_effort', provider.get('reasoning_effort', 'provider-default'))
                 if slot == 'pro' else provider.get('reasoning_effort', 'provider-default')) if verification == 'backend' else (
                 'none' if is_deepseek_endpoint(provider['base_url']) else 'provider-default')
    identity = dict(probe_id=identifier, purpose='probe', occurred_at=occurred_at, provider_id=provider_id,
                    provider_revision=provider['revision'], revision=provider['revision'], slot=slot,
                    model=provider[slot + '_model'], reasoning_mode=reasoning, verification=verification,
                    backend=backend if verification == 'backend' else None,
                    backend_version=backend_version if verification == 'backend' else None,
                    adapter_version=__version__ if verification == 'backend' else None,
                    experiment_id=experiment_id,
                    probe_protocol_version=probe.PROBE_PROTOCOL_VERSION, evidence_source='actual_call')
    with service.store.connect() as db:
        from .usage import price_snapshot
        identity['price'] = price_snapshot(db, provider, identity['model'])
        # Claim before any network call, including concurrent fresh RPC processes.
        cursor = db.execute('INSERT OR IGNORE INTO probe_records VALUES (?,?,?)',
                            (identifier, occurred_at, json.dumps({**identity, 'ok': False, 'status': 'running',
                                                                'request_count': None, 'usage_events': []})))
        if cursor.rowcount != 1:
            raise ValueError('此探针标识已经使用，已拒绝重放；请查询原始记录。')
    try:
        if verification == 'backend':
            if 'Read' not in provider.get('allowed_tools', ['Read']):
                raise ValueError('此供应商不允许 Read，无法验证实际文件读取')
            with tempfile.TemporaryDirectory(prefix='codexrelay-probe-') as directory:
                workspace, marker = Path(directory), uuid.uuid4().hex
                (workspace / 'probe.txt').write_text(marker, encoding='utf-8')
                with service.store.backend_probe(provider, backend, slot, workspace):
                    result = probe.probe_backend(provider, credential, slot, backend, workspace, marker,
                                                 started=started, cancel=Cancellation(), experiment_id=experiment_id,
                                                 evaluation_state_directory=str(service.store.directory))
        else:
            result = probe.probe_model(provider, credential, slot, verification, started=started, cancel=Cancellation(),
                                       experiment_id=experiment_id, evaluation_state_directory=str(service.store.directory))
    except probe.ProbeFailure as error:
        _record(service.store, {**identity, **error.record, 'ok': False, 'error': str(error)})
        raise
    except ValueError as error:
        _record(service.store, {**identity, 'ok': False, 'status': 'not_started', 'request_count': 0,
                               'usage_events': [], 'error': str(error)})
        raise
    except Exception as error:
        # A local bootstrap failure must not persist arbitrary exception text.
        message = '连接测试未完成，请运行诊断；未自动重放。'
        _record(service.store, {**identity, 'ok': False, 'status': 'interrupted', 'request_count': None,
                               'usage_events': [], 'error': message, 'error_type': type(error).__name__})
        raise probe.ProbeFailure(message) from None
    finally:
        with service.store.connect() as db:
            db.execute('DELETE FROM settings WHERE key=?', ('probe_cancel:' + identifier,))
    return _record(service.store, {**identity, **result})


def cancel(store, probe_id):
    if not isinstance(probe_id, str) or not 1 <= len(probe_id) <= 100:
        raise ValueError('探针标识无效')
    store.set_setting('probe_cancel:' + probe_id, True)
    return {'cancel_requested': True, 'probe_id': probe_id}


def usage_today(store):
    from decimal import Decimal
    from .usage import daily_window, estimate, add_bounds, amount
    _, start, end = daily_window()
    with store.connect() as db:
        rows = db.execute('SELECT payload FROM probe_records WHERE occurred_at>=? AND occurred_at<?', (start, end)).fetchall()
    values = [json.loads(row[0]) for row in rows]
    events = [event for value in values for event in value.get('usage_events', [])]
    reported = [event for event in events if event.get('available')]
    bounds, priced, unknown_price, unknown_cache_write = [Decimal(0), Decimal(0)], 0, 0, 0
    for value in values:
        for event in value.get('usage_events', []):
            if not event.get('available'):
                continue
            if value.get('price') is None:
                unknown_price += 1
            if event.get('cache_write_input') is None:
                unknown_cache_write += 1
                continue
            if value.get('price') is None:
                continue
            tokens = {**event, 'cache_write_input': event['cache_write_input']}
            cost = estimate(tokens, value.get('price')) if value.get('price') else None
            if cost is None:
                unknown_price += 1
            else:
                priced += 1
                add_bounds(bounds, cost)
    return dict(purpose='probe', records=len(values), requests=sum(value.get('request_count') or 0 for value in values),
                unknown_requests=sum(value.get('request_count') is None for value in values),
                reported_tokens=sum(event.get('prompt', 0) + event.get('completion', 0) for event in reported),
                missing_usage=len(events) - len(reported), unknown_price=unknown_price, priced_requests=priced,
                unknown_cache_write=unknown_cache_write,
                cost=amount(bounds) if priced else None,
                cost_complete=bool(events) and priced == len(events) and all(value.get('request_count') is not None for value in values),
                cost_basis='api_reference_estimate', legacy_task_usage='task totals exclude probes',
                currency='CNY', actual_bill=None)
