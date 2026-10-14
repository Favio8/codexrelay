"""Task-settlement usage and explicit, versioned price estimates. No log scanning."""
import json
import math
import time
import uuid
from collections import Counter
from datetime import datetime, time as day_time, timedelta
from decimal import Decimal, InvalidOperation

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'budget_stopped', 'no_progress'}
ACTIVE = {'queued', 'running', 'cancelling', 'compacting', 'wrapping_up'}
MILLION = Decimal(1_000_000)


def price_key(provider_id, provider_revision, model):
    return json.dumps([provider_id, provider_revision, model], ensure_ascii=False)


def validate_price(value):
    if not isinstance(value, dict) or value.get('currency', 'CNY') != 'CNY':
        raise ValueError('目前仅支持人民币价格')
    result = {'currency': 'CNY'}
    for name in ('input', 'cached_input', 'cache_write', 'output'):
        raw = value.get(name)
        if name in ('cached_input', 'cache_write') and raw in (None, ''):
            result[name] = None
            continue
        try:
            number = Decimal(str(raw))
        except InvalidOperation:
            raise ValueError('请输入有效的非负单价') from None
        if not number.is_finite() or number < 0 or number > 1_000_000:
            raise ValueError('单价必须是 0 至 1,000,000 之间的有限数值')
        if number.as_tuple().exponent < -8:
            raise ValueError('单价最多保留 8 位小数')
        result[name] = str(number)
    result.update(label=str(value.get('label', '')).strip()[:100], version=uuid.uuid4().hex,
                  created_at=time.time(), source='user')
    return result


def bump_revision(db):
    db.execute("INSERT INTO settings(key,value) VALUES ('usage_revision','1') "
               "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")


def settings_from(db):
    row = db.execute("SELECT value FROM settings WHERE key='usage_prices'").fetchone()
    if not row:
        return {'models': {}, 'reference': None}
    config = json.loads(row[0])
    # Prices saved before cache-write accounting existed must not raise on read.
    for entry in [config.get('reference'), *config.get('models', {}).values()]:
        if isinstance(entry, dict):
            entry.setdefault('cache_write', None)
    config.setdefault('models', {})
    config.setdefault('reference', None)
    return config


def save_price(store, price, provider_id=None, provider_revision=None, model=None, reference=False):
    validated = None if reference and price is None else validate_price(price)
    if not reference and (not isinstance(provider_id, str) or not provider_id or
                          not isinstance(model, str) or not model or
                          (provider_revision is not None and not isinstance(provider_revision, str))):
        raise ValueError('请选择供应商版本与实际模型')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        config = settings_from(db)
        if reference:
            config['reference'] = validated
        else:
            config['models'][price_key(provider_id, provider_revision, model)] = validated
        db.execute("INSERT OR REPLACE INTO settings VALUES ('usage_prices',?)", (json.dumps(config),))
        bump_revision(db)
    return config


def price_snapshot(db, provider, model):
    price = settings_from(db)['models'].get(price_key(provider['id'], provider.get('revision'), model))
    return {**price, 'source': 'task_start'} if price else None


def daily_window(now=None):
    current = now or datetime.now().astimezone()
    # Naive timestamp() uses the OS rules at each boundary (including DST).
    zone = now.tzinfo if now is not None else None
    start = datetime.combine(current.date(), day_time.min, tzinfo=zone)
    end = datetime.combine(current.date() + timedelta(days=1), day_time.min, tzinfo=zone)
    return current, start.timestamp(), end.timestamp()


def valid_timestamp(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def non_negative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def cache_write_total(telemetry):
    """Cache-write tokens only exist per response; the task snapshot has to sum them."""
    events = telemetry.get('usage_events') if isinstance(telemetry, dict) else None
    if not isinstance(events, list):
        return 0
    total = 0
    for event in events:
        if isinstance(event, dict) and non_negative_int(event.get('cache_write_input')):
            total += event['cache_write_input']
    return total


def normalize_tokens(task):
    telemetry = task.get('telemetry')
    if isinstance(telemetry, dict) and 'tokens' in telemetry:
        tokens = telemetry['tokens']
    else:
        result = task.get('result')
        tokens = result.get('tokens') if isinstance(result, dict) else None
    if not isinstance(tokens, dict) or tokens.get('available') is False:
        return None, 'missing'
    values = [tokens.get(k) for k in ('prompt', 'completion')]
    if any(not non_negative_int(v) for v in values):
        return None, 'missing'
    prompt, completion = values
    reported = {'prompt': prompt, 'completion': completion, 'total': prompt + completion}
    cached = tokens.get('cached_input')
    if non_negative_int(cached) and cached <= prompt:
        reported['cached_input'] = cached
    written = cache_write_total(telemetry)
    if written:
        reported['cache_write_input'] = written
    if task.get('status') == 'interrupted' or tokens.get('interrupted_request') or tokens.get('responses_without_usage'):
        coverage = 'partial'
    elif not all(k in tokens for k in ('available', 'responses_with_usage', 'responses_without_usage', 'interrupted_request')):
        coverage = 'legacy'
    else:
        coverage = 'reported'
    return reported, coverage


def estimate(tokens, price, reference=False):
    # The reference model has no observed cache behaviour, so only the executed model is gated.
    if reference or not tokens.get('cache_write_input'):
        return _estimate(tokens, price, reference)
    rate = price.get('cache_write')
    if rate is None:
        # A separate cache-write charge must never be dropped from a supposedly exact total.
        return None
    return _estimate(tokens, price, reference, cache_write=Decimal(rate))


def _estimate(tokens, price, reference, cache_write=None):
    inp, out = Decimal(price['input']), Decimal(price['output'])
    cached = Decimal(price['cached_input']) if price.get('cached_input') is not None else inp
    output_cost = tokens['completion'] * out
    # A reference model's cache behavior cannot be inferred from the executor.
    hit = None if reference else tokens.get('cached_input')
    if hit is None:
        bounds = tuple((tokens['prompt'] * rate + output_cost) / MILLION for rate in (min(inp, cached), max(inp, cached)))
    else:
        cost = ((tokens['prompt'] - hit) * inp + hit * cached + output_cost) / MILLION
        bounds = (cost, cost)
    if cache_write is None:
        return bounds
    written = Decimal(tokens['cache_write_input']) * cache_write / MILLION
    return (bounds[0] + written, bounds[1] + written)


def amount(bounds):
    # Fixed-point output: JSON consumers and spreadsheets cannot read 1E-14 notation.
    return {'low': format(bounds[0], 'f'), 'high': format(bounds[1], 'f')}


def add_bounds(target, source):
    target[0] += source[0]
    target[1] += source[1]


def read_tasks(db):
    for row in db.execute('SELECT id,payload FROM tasks'):
        try:
            task = json.loads(row['payload'])
            if not isinstance(task, dict):
                raise ValueError('invalid task')
            if task.get('backend_probe'):
                continue
            task['id'] = row['id']
            yield task
        except (ValueError, TypeError):
            yield None


def usage_today(store, now=None):
    current, start, end = daily_window(now)
    counts, coverage, totals = Counter(), Counter(), Counter(prompt=0, completion=0, total=0)
    groups, recent = {}, []
    cost, comparison = [Decimal(0), Decimal(0)], [Decimal(0), Decimal(0)]
    priced = 0
    retained_since = None
    with store.connect() as db:
        # One consistent read snapshot, without a write lock or the GUI's 100-row cap.
        db.execute('BEGIN')
        config = settings_from(db)
        revision_row = db.execute("SELECT value FROM settings WHERE key='usage_revision'").fetchone()
        revision = int(revision_row[0]) if revision_row else 0
        for task in read_tasks(db):
            if task is None:
                coverage['invalid_records'] += 1
                continue
            created = task.get('created_at')
            if valid_timestamp(created):
                retained_since = min(retained_since, created) if retained_since is not None else created
            status = task.get('status')
            if status in ACTIVE:
                counts['unsettled'] += 1
                continue
            if status not in TERMINAL:
                continue
            finished = task.get('finished_at')
            if not valid_timestamp(finished):
                coverage['undated_records'] += 1
                continue
            if not start <= finished < end:
                continue
            counts['settled'] += 1
            counts[status] += 1
            if task.get('verification', {}).get('status') == 'passed':
                counts['verified'] += 1
            if status == 'interrupted':
                coverage['approximate_date'] += 1
            tokens, quality = normalize_tokens(task)
            coverage[quality] += 1
            if tokens:
                totals.update({k: tokens[k] for k in ('prompt', 'completion', 'total')})
            key = price_key(task.get('provider_id'), task.get('provider_revision'), task.get('model'))
            group = groups.setdefault(key, dict(key=key, provider_id=task.get('provider_id'),
                provider_revision=task.get('provider_revision'), provider_name=task.get('provider_name', '历史供应商'),
                model=task.get('model', '未知模型'), tasks=0, unpriced=0, reported_tokens=0))
            group['tasks'] += 1
            group['reported_tokens'] += tokens['total'] if tokens else 0
            price = task.get('usage_price')
            row_cost = None
            bounds = estimate(tokens, price) if tokens is not None and price else None
            if bounds is not None:
                add_bounds(cost, bounds)
                priced += 1
                row_cost = amount(bounds)
                if config['reference']:
                    ref = estimate(tokens, config['reference'], reference=True)
                    add_bounds(comparison, (ref[0] - bounds[1], ref[1] - bounds[0]))
            elif tokens is not None:
                group['unpriced'] += 1
                if price and tokens.get('cache_write_input'):
                    coverage['cache_write_unpriced'] += 1
            recent.append(dict(id=task['id'], model=task.get('model'), provider_name=task.get('provider_name'),
                status=status, finished_at=finished, coverage=quality, tokens=tokens, cost=row_cost,
                price=price, verification=task.get('verification', {}).get('status')))
    known = counts['settled'] - coverage['missing']
    return dict(method_version=1, date=current.date().isoformat(), timezone=current.tzname(),
        utc_offset=current.strftime('%z'), day_start=start, day_end=end, generated_at=time.time(),
        source=str(store.directory.resolve()), revision=revision, retained_since=retained_since,
        counts=dict(counts), coverage=dict(coverage), tokens=dict(totals) if known else None,
        priced_tasks=priced, unpriced_tasks=known-priced, cost=amount(cost) if priced else None,
        comparison=amount(comparison) if priced and config['reference'] else None,
        reference=config['reference'], currency='CNY', groups=list(groups.values()),
        recent=sorted(recent, key=lambda task: task['finished_at'], reverse=True)[:50])


def backfill_price(store, date, provider_id, provider_revision, model):
    current, start, end = daily_window()
    if date != current.date().isoformat():
        raise ValueError('日期已变化，请刷新后再应用价格')
    key = price_key(provider_id, provider_revision, model)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        price = settings_from(db)['models'].get(key)
        if not price:
            raise ValueError('请先保存该供应商版本与模型的价格')
        updates = []
        for task in read_tasks(db):
            if task is None or task.get('status') not in TERMINAL or task.get('usage_price'):
                continue
            finished = task.get('finished_at')
            if not valid_timestamp(finished) or not start <= finished < end:
                continue
            if price_key(task.get('provider_id'), task.get('provider_revision'), task.get('model')) != key:
                continue
            if normalize_tokens(task)[0] is None:
                continue
            task['usage_price'] = {**price, 'source': 'backfill', 'applied_at': time.time()}
            updates.append((json.dumps(task, ensure_ascii=False), task['id']))
        db.executemany('UPDATE tasks SET payload=? WHERE id=?', updates)
        if updates:
            bump_revision(db)
    return {'updated': len(updates)}


def requests_today(store, now=None):
    current,start,end=daily_window(now)
    totals=Counter(prompt=0,completion=0,cached_input=0,total=0)
    counts=Counter();coverage=Counter();cost=[Decimal(0),Decimal(0)]
    with store.connect() as db:
        # One snapshot for both passes; a single bad row is reported, never fatal.
        db.execute('BEGIN')
        for row in db.execute('SELECT e.payload AS event,t.payload AS task FROM usage_events e JOIN tasks t ON t.id=e.job_id WHERE e.occurred_at>=? AND e.occurred_at<?',(start,end)):
            try:
                event=json.loads(row['event']);task=json.loads(row['task'])
                if not isinstance(event,dict) or not isinstance(task,dict):raise ValueError('invalid record')
                available=event.get('available')
                prompt,completion=event.get('prompt'),event.get('completion')
                if not isinstance(available,bool) or available is False:
                    counts['missing']+=1;continue
                if not non_negative_int(prompt) or not non_negative_int(completion):
                    raise ValueError('invalid counters')
            except (ValueError, TypeError, KeyError):
                coverage['invalid_records']+=1;continue
            counts['responses']+=1
            totals.update(prompt=prompt,completion=completion,total=prompt+completion)
            cached=event.get('cached_input')
            if cached is None:counts['cache_unknown']+=1
            elif non_negative_int(cached):totals['cached_input']+=cached
            else:coverage['invalid_records']+=1
            price=task.get('usage_price')
            bounds=estimate(event,price) if price else None
            if bounds is None:
                counts['unpriced']+=1
                if price and event.get('cache_write_input'):coverage['cache_write_unpriced']+=1
            else:
                add_bounds(cost,bounds);counts['priced']+=1
        for task in read_tasks(db):
            if task is None:continue
            finished=task.get('finished_at')
            if valid_timestamp(finished) and start<=finished<end:
                if not (task.get('telemetry') or {}).get('usage_events'):counts['without_events']+=1
                if task.get('status')=='interrupted' or ((task.get('telemetry') or {}).get('tokens') or {}).get('interrupted_request'):
                    counts['interrupted']+=1
    return dict(date=current.date().isoformat(),timezone=current.tzname(),generated_at=time.time(),
                tokens=dict(totals) if counts['responses']>counts['missing'] else None,
                counts=dict(counts),coverage=dict(coverage),cost=amount(cost) if counts['priced'] else None)
