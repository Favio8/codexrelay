"""Local controlled experiments. Preparing, scoring and reporting never call a model.

The request ledger is a pre-send guard, not a provider bill. Unknown settlement
retains its reservation. Main-model requests remain controlled by the Codex host.
"""
import argparse
import hashlib
import json
import math
import os
import statistics
import time
import uuid
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from . import evaluation_cases
from .usage import estimate, amount
from .state import process_identity

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_experiments(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evaluation_trials(id TEXT PRIMARY KEY,experiment_id TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evaluation_trials_experiment ON evaluation_trials(experiment_id);
CREATE TABLE IF NOT EXISTS evaluation_requests(id TEXT PRIMARY KEY,experiment_id TEXT NOT NULL,trial_id TEXT,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evaluation_requests_experiment ON evaluation_requests(experiment_id);
CREATE TABLE IF NOT EXISTS evaluation_usage_events(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,payload TEXT NOT NULL);
"""
GROUP_BACKENDS = {'A': None, 'B': 'deepseek', 'C': 'pi'}
TRIAL_ENDINGS = {'completed', 'failed', 'cancelled', 'budget_stopped', 'interrupted', 'timeout', 'not_delegated', 'completed_by_parent'}
KINDS = {'execution', 'summary', 'retry', 'continuation', 'probe'}
HASH_CHUNK_BYTES = 1024 * 1024
LOG_LINE_BYTES = 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path, limit=None):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        remaining=limit
        while remaining is None or remaining>0:
            chunk=stream.read(HASH_CHUNK_BYTES if remaining is None else min(HASH_CHUNK_BYTES,remaining))
            if not chunk:break
            result.update(chunk)
            if remaining is not None:remaining-=len(chunk)
    return result.hexdigest()


def integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(name + ' must be an integer >= ' + str(minimum))
    return value


def money(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('invalid decimal amount') from None
    if not number.is_finite() or number < 0:
        raise ValueError('amount must be finite and nonnegative')
    return number


def safe_relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in {'..', '.'} for p in path.parts) or '\\' in value or ':' in value:
        raise ValueError('case paths must be relative POSIX paths')
    return path


def tree_hashes(root):
    root = Path(root).resolve(strict=True)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('symbolic links are not allowed in experiment artifacts')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_hash(path)
    return files


def normalize_usage(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('usage must be counters')
    out = {}
    for key in ('prompt', 'completion', 'cached_input', 'cache_write_input'):
        v = value.get(key, 0 if key == 'cache_write_input' else None)
        if v is None and key in {'cached_input', 'cache_write_input'}:
            out[key] = None
        else:
            out[key] = integer(v, key)
    if out['cached_input'] is not None and out['cached_input'] > out['prompt']:
        raise ValueError('cached input cannot exceed prompt tokens')
    out['total'] = out['prompt'] + out['completion']
    return out


def price_snapshot(value):
    if value is None:
        return None
    required = ('input', 'output', 'currency', 'source', 'observed_at')
    if any(k not in value for k in required) or value['currency'] not in {'CNY', 'USD'}:
        raise ValueError('frozen prices require currency, source and observed_at')
    result = {k: value[k] for k in required}
    for key in ('input', 'output', 'cached_input', 'cache_write'):
        result[key] = format(money(value[key]), 'f') if value.get(key) is not None else None
    result['sha256'] = digest(result)
    return result


def owned_sessions(binding):
    return {value for value in (binding.get('session_id'),binding.get('root_session_id'),*binding.get('related_sessions',[]))
            if isinstance(value,str) and value.strip()}


def main_usage_selection(events):
    """Count one telemetry chain per session, retaining both sources as evidence.

    Direct events supersede matching cumulative windows or the same response ID.
    Incomplete overlap remains a visible gap instead of adding both streams.
    """
    groups={}
    for event in events:
        if event.get('attributable') and event.get('usage'):
            # Host cumulative counters cover the whole session, including model
            # switches. Splitting that chain by model would add its prefix twice.
            key=(event['trial_id'],event['session_id'])
            groups.setdefault(key,[]).append(event)
    selected,summaries,issues=[],[],[]
    lower_bound=0
    def tokens(rows):return sum(row['usage']['total'] for row in rows)
    def counters(rows):return tuple(sum(row['usage'][field] for row in rows) for field in ('prompt','completion'))
    def cache_counters(rows):
        return {field:None if any(row['usage'].get(field) is None for row in rows) else sum(row['usage'].get(field,0) for row in rows)
                for field in ('cached_input','cache_write_input')}
    def compatible(a,b):
        return all(a.get(field) is None or b.get(field) is None or a[field]==b[field]
                   for field in ('prompt','completion','cached_input','cache_write_input'))
    for key,rows in groups.items():
        direct=[row for row in rows if row.get('source_kind','direct')=='direct']
        cumulative=[row for row in rows if row.get('source_kind')=='cumulative']
        strategy='single_source';chosen=rows
        if any(row.get('source_kind')=='cumulative' and row.get('identity_version')!=2 for row in rows):
            issues.append('legacy_cumulative_identity_needs_review')
        if direct and cumulative:
            response_key=lambda row:(row.get('provider','unknown'),row.get('response_id'))
            ids={response_key(row):row for row in direct if row.get('response_id')}
            cumulative_ids={}
            for row in cumulative:
                if row.get('response_id'):cumulative_ids.setdefault(response_key(row),[]).append(row)
            def combined_usage(values):
                prompt,completion=counters(values)
                return dict(prompt=prompt,completion=completion,**cache_counters(values))
            # Some hosts publish several monotonic checkpoints for one response.
            # Compare their combined increments with its direct usage, not each
            # checkpoint delta in isolation.
            conflict=any(not compatible(combined_usage(values),ids[identifier]['usage'])
                         for identifier,values in cumulative_ids.items() if identifier in ids)
            if conflict:
                issues.append('conflicting_usage_streams')
            starts={row.get('session_started_at') for row in cumulative}
            start=next(iter(starts)) if len(starts)==1 else None
            end=max((row.get('occurred_at') for row in cumulative if row.get('occurred_at') is not None),default=None)
            covered=(start is not None and end is not None and all(
                row.get('occurred_at') is not None and start<=row['occurred_at']<=end and
                row.get('session_started_at')==start and not row.get('forked') for row in direct+cumulative))
            has_exact_ids=all(row.get('response_id') for row in cumulative)
            unmatched_direct=[row for row in direct if not row.get('response_id') or response_key(row) not in
                              cumulative_ids]
            # A cumulative response label is still a session prefix, not proof
            # that its whole amount is disjoint from earlier direct responses.
            distinct_after_window=(end is not None and all(row.get('occurred_at') is not None and row['occurred_at']>end for row in unmatched_direct))
            window_matches=covered and counters(direct)==counters(cumulative)
            identities={(row.get('provider','unknown'),row.get('model','unknown')) for row in rows}
            if len(identities)>1:
                # The cumulative total is still useful session evidence, but its
                # model allocation cannot be reconstructed from a later label.
                issues.append('cumulative_model_or_provider_allocation_unknown')
                has_exact_ids=False;window_matches=False
            if not has_exact_ids and window_matches and not compatible(cache_counters(direct),cache_counters(cumulative)):
                if not conflict:issues.append('conflicting_usage_streams')
                conflict=True
            if not conflict and has_exact_ids and distinct_after_window:
                chosen=direct+[row for row in cumulative if response_key(row) not in ids]
                strategy='direct_and_distinct_response_ids'
            elif not conflict and window_matches:
                chosen=direct;strategy='direct_matches_cumulative_window'
            else:
                # Retain unmatched cumulative evidence for review, but do not
                # fabricate request mappings or charge the two streams twice.
                chosen=direct
                strategy='direct_preferred_unresolved_overlap'
                issues.append('usage_stream_coverage_unresolved')
        selected.extend(chosen)
        lower=max(tokens(chosen),tokens(cumulative)) if strategy=='direct_preferred_unresolved_overlap' else tokens(chosen)
        lower_bound+=lower
        providers=sorted({row.get('provider','unknown') for row in rows});models=sorted({row.get('model','unknown') for row in rows})
        summaries.append(dict(session_id=key[1],provider=providers[0] if len(providers)==1 else 'mixed',
                              model=models[0] if len(models)==1 else 'mixed',providers=providers,models=models,strategy=strategy,
                              direct_tokens=tokens(direct) if direct else None,
                              cumulative_tokens=tokens(cumulative) if cumulative else None,
                              direct_cache_counters=cache_counters(direct) if direct else None,
                              cumulative_cache_counters=cache_counters(cumulative) if cumulative else None,
                              counted_tokens=tokens(chosen),known_token_lower_bound=lower))
    return selected,summaries,sorted(set(issues)),lower_bound


class Evaluation:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evaluation_experiments'").fetchone():
                db.executescript(SCHEMA_SQL)

    @staticmethod
    def _get(db, table, identifier):
        row = db.execute('SELECT payload FROM ' + table + ' WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('evaluation record not found')
        return json.loads(row[0])

    @staticmethod
    def _put(db, table, value):
        db.execute('UPDATE ' + table + ' SET payload=? WHERE id=?', (json.dumps(value, ensure_ascii=False, allow_nan=False), value['id']))

    def _trials(self, db, experiment_id):
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM evaluation_trials WHERE experiment_id=?', (experiment_id,))]

    def _requests(self, db, experiment_id):
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM evaluation_requests WHERE experiment_id=?', (experiment_id,))]

    def _consumption(self, db, experiment_id):
        requests = self._requests(db, experiment_id)
        trials = self._trials(db, experiment_id)
        events=[]
        for row in db.execute('SELECT e.payload FROM evaluation_usage_events e JOIN evaluation_trials t ON t.id=e.trial_id WHERE t.experiment_id=?',(experiment_id,)):
            event = json.loads(row[0])
            events.append(event)
        _,_,main_issues,main_tokens=main_usage_selection(events)
        return dict(charged_external_tokens=sum(r['charged_tokens'] for r in requests),
                    charged_external_cost=format(sum((money(r['charged_cost']) for r in requests),Decimal(0)),'f') if all(r['charged_cost'] is not None for r in requests) else None,
                    requests=len(requests), unsettled=sum(r['state']!='settled' for r in requests), usage_gaps=sum(r['gap'] for r in requests),
                    started_trials=sum('started_at' in t for t in trials), main_reported_tokens=main_tokens,
                    main_usage_stream_gaps=len(main_issues))

    def create_experiment(self, name, conditions, cases=None):
        """Freeze precise model/settings/environment identities, never provider credentials."""
        permitted = {'main_model', 'main_provider', 'main_reasoning', 'external_provider_revision', 'external_model', 'external_reasoning', 'external_slot',
                     'runtime_version', 'policy_version', 'dependency_version', 'environment', 'currency', 'main_price',
                     'external_price', 'main_billing', 'external_billing'}
        if not isinstance(conditions, dict) or set(conditions) - permitted:
            raise ValueError('unknown experiment condition; credentials and bodies are not accepted')
        conditions = dict(conditions)
        for key in ('main_model', 'external_model', 'runtime_version', 'policy_version', 'environment'):
            if not isinstance(conditions.get(key), str) or not conditions[key].strip():
                raise ValueError('freeze ' + key + ' explicitly; use unknown when unavailable')
        conditions.setdefault('main_provider', 'unknown')
        if conditions.setdefault('external_slot', 'flash') not in {'flash', 'pro'}:
            raise ValueError('invalid frozen external slot')
        for key in ('main_price', 'external_price'):
            conditions[key] = price_snapshot(conditions.get(key))
        currency = conditions.setdefault('currency', 'CNY')
        if currency not in {'CNY', 'USD'} or any(p and p['currency'] != currency for p in (conditions['main_price'], conditions['external_price'])):
            raise ValueError('prices require one frozen currency; convert explicitly before freezing')
        for key in ('main_billing', 'external_billing'):
            if conditions.setdefault(key, 'unknown') not in {'subscription', 'api', 'unknown'}:
                raise ValueError('invalid billing basis')
        cases = cases or evaluation_cases.builtin_cases()
        if not cases or len({c['id'] for c in cases}) != len(cases):
            raise ValueError('case IDs must be unique')
        for case in cases:
            for path in case['initial_files']:
                safe_relative(path)
            if case.get('kind') not in {'implementation', 'testing', 'review', 'analysis', 'documentation'}:
                raise ValueError('unsupported case kind')
        frozen = {'version': 1, 'conditions': conditions, 'cases': cases, 'scorer_sha256': file_hash(evaluation_cases.__file__)}
        value = dict(id=uuid.uuid4().hex, name=str(name)[:120], state='draft', created_at=time.time(), revision=1,
                     frozen=frozen, manifest_sha256=digest(frozen), authorization=None, authorization_history=[])
        with self.store.connect() as db:
            db.execute('INSERT INTO evaluation_experiments VALUES (?,?)', (value['id'], json.dumps(value)))
        return self.describe(value['id'])

    def describe(self, experiment_id):
        with self.store.connect() as db:
            value = self._get(db, 'evaluation_experiments', experiment_id)
            trials = self._trials(db, experiment_id)
        # No task/source bodies in default views.
        return {k: v for k, v in value.items() if k != 'frozen'} | {
            'conditions': value['frozen']['conditions'], 'scorer_sha256': value['frozen']['scorer_sha256'],
            'cases': [{'case_id': c['id'], 'kind': c['kind'], 'input_sha256': digest(c), 'difficulty': c.get('difficulty'),
                       'initial_files': {p: hashlib.sha256(b.encode()).hexdigest() for p, b in c['initial_files'].items()}}
                      for c in value['frozen']['cases']], 'trials': trials}

    def prepare_experiment(self, experiment_id, workspace_root, repetitions=1):
        integer(repetitions, 'repetitions', 1)
        if repetitions > 10:
            raise ValueError('at most ten repetitions per preparation')
        root = Path(workspace_root).resolve()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exp = self._get(db, 'evaluation_experiments', experiment_id)
            if exp['state'] != 'draft':
                raise ValueError('only draft experiments can be prepared')
            target = root / experiment_id
            if target.exists():
                raise ValueError('isolated experiment directory already exists')
            # Exclusive creation. Partial preparation remains explicit and is never reused.
            target.mkdir(parents=True, exist_ok=False)
            for case in exp['frozen']['cases']:
                for repeat in range(repetitions):
                    order = ('A', 'B', 'C')[repeat % 3:] + ('A', 'B', 'C')[:repeat % 3]
                    for position, group in enumerate(order):
                        trial_id = uuid.uuid4().hex
                        workspace = target / trial_id
                        workspace.mkdir()
                        for path, text in case['initial_files'].items():
                            dst = workspace / str(safe_relative(path))
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            dst.write_text(text, encoding='utf-8', newline='')
                        trial = dict(id=trial_id, experiment_id=experiment_id, case_id=case['id'], repeat=repeat,
                                     order=position, group=group, backend_id=GROUP_BACKENDS[group], workspace=str(workspace),
                                     input_sha256=digest(case), initial_hashes=tree_hashes(workspace), state='prepared',
                                     binding=None, runs=[], outcome=None, score=None, measurement_issues=[])
                        db.execute('INSERT INTO evaluation_trials VALUES (?,?,?)', (trial_id, experiment_id, json.dumps(trial)))
            exp.update(state='prepared', revision=exp['revision'] + 1, workspace_root=str(target), repetitions=repetitions)
            self._put(db, 'evaluation_experiments', exp)
        return self.describe(experiment_id)

    def authorization_preview(self, experiment_id, caps, data_scope):
        expected = {'external_tokens', 'trial_tokens', 'run_tokens', 'max_trials', 'main_tokens', 'external_cost'}
        if not isinstance(caps, dict) or set(caps) != expected:
            raise ValueError('authorization requires explicit experiment/trial/run/main/trial-count and cost limits')
        caps = dict(caps)
        for key in expected - {'external_cost'}:
            integer(caps[key], key)
        if caps['external_cost'] is not None:
            caps['external_cost'] = format(money(caps['external_cost']), 'f')
        if not isinstance(data_scope, dict) or set(data_scope) != {'usage_directory', 'raw_evidence_directory'}:
            raise ValueError('explicit local data scope is required; null means no import')
        for val in data_scope.values():
            if val is not None and (not isinstance(val, str) or not Path(val).is_absolute()):
                raise ValueError('data scope directories must be absolute or null')
        with self.store.connect() as db:
            exp = self._get(db, 'evaluation_experiments', experiment_id)
            consumption = self._consumption(db, experiment_id)
        preview = dict(experiment_id=experiment_id, revision=exp['revision'], manifest_sha256=exp['manifest_sha256'],
                       caps=caps, data_scope=data_scope, previous_version=len(exp['authorization_history']),
                       consumption=consumption,
                       main_control='host_observed_only; in-flight requests cannot be stopped by CodexRelay')
        return preview | {'preview_sha256': digest(preview)}

    def authorize(self, experiment_id, caps, data_scope, preview_sha256, confirmed=False):
        if confirmed is not True:
            raise ValueError('explicit user authorization is required')
        preview = self.authorization_preview(experiment_id, caps, data_scope)
        if preview['preview_sha256'] != preview_sha256:
            raise ValueError('AUTHORIZATION_PREVIEW_STALE')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exp = self._get(db, 'evaluation_experiments', experiment_id)
            if (exp['revision'] != preview['revision'] or self._consumption(db,experiment_id) != preview['consumption'] or
                    exp['state'] not in {'prepared', 'authorized', 'running', 'budget_stopped', 'needs_review', 'stopped'}):
                raise ValueError('AUTHORIZATION_PREVIEW_STALE')
            if any(preview['caps'][cap] < preview['consumption'][used] for cap,used in
                   (('external_tokens','charged_external_tokens'),('max_trials','started_trials'),('main_tokens','main_reported_tokens'))):
                raise ValueError('authorization cannot be below already charged consumption')
            if (preview['caps']['external_cost'] is not None and preview['consumption']['charged_external_cost'] is not None and
                    money(preview['caps']['external_cost']) < money(preview['consumption']['charged_external_cost'])):
                raise ValueError('authorization cannot be below already charged cost')
            auth = dict(version=len(exp['authorization_history']) + 1, confirmed_at=time.time(),
                        caps=preview['caps'], data_scope=data_scope, preview_sha256=preview_sha256)
            exp['authorization_history'].append(auth)
            exp.update(authorization=auth, state='authorized', revision=exp['revision'] + 1)
            self._put(db, 'evaluation_experiments', exp)
        return self.describe(experiment_id)

    def start_trial(self, trial_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial = self._get(db, 'evaluation_trials', trial_id)
            exp = self._get(db, 'evaluation_experiments', trial['experiment_id'])
            if exp['state'] not in {'authorized', 'running'} or not exp['authorization']:
                raise ValueError('EXPERIMENT_NOT_AUTHORIZED')
            if trial['state'] != 'prepared':
                raise ValueError('trial already started; restart requires a new trial identity')
            trials = self._trials(db, exp['id'])
            if any(t['state'] == 'running' for t in trials):
                raise ValueError('experiment trials execute serially')
            if sum('started_at' in t for t in trials) >= exp['authorization']['caps']['max_trials']:
                raise ValueError('EXPERIMENT_TRIAL_LIMIT')
            if tree_hashes(trial['workspace']) != trial['initial_hashes']:
                raise ValueError('INITIAL_FILES_CHANGED')
            trial.update(state='running', started_at=time.time())
            exp['state'] = 'running'
            self._put(db, 'evaluation_trials', trial)
            self._put(db, 'evaluation_experiments', exp)
        return trial

    def bind_session(self, trial_id, session_id, root_session_id, client_kind, confirmed=False, forked=False, related_sessions=None):
        if confirmed is not True or any(not isinstance(s,str) or not s.strip() or len(s)>256 for s in (session_id,root_session_id)) or client_kind not in {'desktop', 'cli', 'local_substitute'}:
            raise ValueError('explicit root/session/client binding is required')
        if related_sessions is not None and not isinstance(related_sessions,list):
            raise ValueError('related sessions must be session IDs in a list')
        related = sorted(set(related_sessions or []))
        if any(not isinstance(s, str) or not s.strip() or len(s)>256 for s in related):
            raise ValueError('invalid related session')
        binding = dict(session_id=session_id, root_session_id=root_session_id, client_kind=client_kind,
                       confirmed_at=time.time(), forked=bool(forked), related_sessions=related)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial = self._get(db, 'evaluation_trials', trial_id)
            if trial['state'] != 'running' or trial.get('binding'):
                raise ValueError('bind once after the trial starts')
            for row in db.execute('SELECT payload FROM evaluation_trials'):
                other = json.loads(row[0]).get('binding')
                if other and (owned_sessions(binding)&owned_sessions(other)):
                    raise ValueError('session already bound to another trial')
            trial['binding'] = binding
            self._put(db, 'evaluation_trials', trial)
        return binding

    def add_related_sessions(self,trial_id,related_sessions,confirmed=False):
        """Explicitly extend attribution without replacing the primary binding."""
        if confirmed is not True:
            raise ValueError('explicit confirmation is required for related sessions')
        if not isinstance(related_sessions,list) or any(not isinstance(s,str) or not s.strip() or len(s)>256 for s in related_sessions):
            raise ValueError('related sessions must be bounded, nonempty session IDs')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial=self._get(db,'evaluation_trials',trial_id)
            binding=trial.get('binding')
            if not binding or trial['state'] not in {'running','ended'}:
                raise ValueError('related sessions require an existing started-trial binding')
            requested=set(related_sessions)-{binding['session_id']}
            for row in db.execute('SELECT payload FROM evaluation_trials WHERE id<>?',(trial_id,)):
                other=json.loads(row[0]).get('binding')
                if other and requested & owned_sessions(other):
                    raise ValueError('session already bound to another trial')
            added=sorted(requested-set(binding['related_sessions']))
            if added:
                binding['related_sessions']=sorted(set(binding['related_sessions'])|requested)
                trial.setdefault('binding_extensions',[]).append(dict(id=uuid.uuid4().hex,confirmed_at=time.time(),added_sessions=added))
                self._put(db,'evaluation_trials',trial)
        return dict(trial_id=trial_id,binding=binding,added_sessions=added)

    def attach_run(self, trial_id, group_id, run_id, backend_id, workspace=None, model=None, provider_revision=None, reasoning=None):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial = self._get(db, 'evaluation_trials', trial_id)
            if trial['state'] != 'running' or trial['group'] == 'A' or backend_id != trial['backend_id']:
                raise ValueError('run does not match the frozen trial backend')
            exp = self._get(db, 'evaluation_experiments', trial['experiment_id'])
            conditions = exp['frozen']['conditions']
            if workspace is not None and Path(workspace).resolve() != Path(trial['workspace']).resolve():
                raise ValueError('run workspace differs from the isolated trial')
            for actual, key in ((model, 'external_model'), (provider_revision, 'external_provider_revision'), (reasoning, 'external_reasoning')):
                if actual is not None and conditions.get(key) not in {None, 'unknown', actual}:
                    raise ValueError('run differs from frozen ' + key)
            if not group_id or not run_id:
                raise ValueError('group/run identity required')
            task = self._get(db, 'tasks', run_id)
            membership = db.execute('SELECT group_id FROM group_runs WHERE run_id=?', (run_id,)).fetchone()
            if (not membership or membership[0] != group_id or task.get('group_id') != group_id or
                    Path(task.get('workspace', '')).resolve() != Path(trial['workspace']).resolve() or task.get('backend_id') != backend_id):
                raise ValueError('run must match actual engine task, group membership, workspace and backend')
            if task.get('model_slot') != conditions['external_slot']:
                raise ValueError('actual engine task differs from frozen external_slot')
            for key, field in (('external_model','model'), ('external_provider_revision','provider_revision'), ('external_reasoning','reasoning_effort')):
                if conditions.get(key) not in {None,'unknown'} and task.get(field) != conditions[key]:
                    raise ValueError('actual engine task differs from frozen ' + key)
            link = dict(group_id=group_id, run_id=run_id, backend_id=backend_id, source='engine_record')
            if link not in trial['runs']:
                for row in db.execute('SELECT payload FROM evaluation_trials WHERE id<>?', (trial_id,)):
                    if any(r['run_id'] == run_id for r in json.loads(row[0])['runs']):
                        raise ValueError('run already belongs to another trial')
                trial['runs'].append(link)
                self._put(db, 'evaluation_trials', trial)
        return link

    def reserve_request(self, experiment_id, trial_id, request_id, estimated_tokens, estimated_cost=None,
                        group_id=None, run_id=None, request_kind='execution'):
        integer(estimated_tokens, 'estimated_tokens', 1)
        if request_kind not in KINDS or not request_id:
            raise ValueError('request identity/kind required')
        cost = format(money(estimated_cost), 'f') if estimated_cost is not None else None
        value = dict(id=request_id, experiment_id=experiment_id, trial_id=trial_id, group_id=group_id, run_id=run_id,
                     request_kind=request_kind, reserved_tokens=estimated_tokens, reserved_cost=cost,
                     charged_tokens=estimated_tokens, charged_cost=cost, usage=None, actual_cost=None,
                     state='reserved', reserved_at=time.time(), gap=True, owner_pid=os.getpid(),
                     owner_process_identity=process_identity(os.getpid()))
        blocked = None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exp = self._get(db, 'evaluation_experiments', experiment_id)
            old = db.execute('SELECT payload FROM evaluation_requests WHERE id=?', (request_id,)).fetchone()
            if old:
                # A durable reservation is not permission to replay an in-flight HTTP request.
                raise ValueError('REQUEST_ALREADY_RESERVED; inspect outcome; automatic replay is prohibited')
            if exp['state'] not in {'authorized', 'running'} or not exp['authorization']:
                raise ValueError('EXPERIMENT_NOT_AUTHORIZED')
            caps = exp['authorization']['caps']
            value['authorization_version'] = exp['authorization']['version']
            requests = self._requests(db, experiment_id)
            stale = []
            for previous in requests:
                reservation_version = previous.get('authorization_version', previous.get('review_required_version', exp['authorization']['version']))
                if previous['state'] != 'reserved' or reservation_version < exp['authorization']['version']:
                    continue
                pid = previous.get('owner_pid')
                identity = process_identity(pid) if isinstance(pid,int) and pid > 0 else None
                owner_lost = identity is None or identity != previous.get('owner_process_identity')
                task_ended = False
                if previous.get('run_id'):
                    row = db.execute('SELECT payload FROM tasks WHERE id=?',(previous['run_id'],)).fetchone()
                    task = json.loads(row[0]) if row else {}
                    task_ended = not row or task.get('status') in TRIAL_ENDINGS | {'no_progress'}
                if owner_lost or task_ended:
                    stale.append(previous['id'])
                    previous['review_required_version'] = exp['authorization']['version']
                    self._put(db,'evaluation_requests',previous)
            trial_requests = [r for r in requests if r['trial_id'] == trial_id] if trial_id else []
            if trial_id:
                trial = self._get(db, 'evaluation_trials', trial_id)
                if trial['experiment_id'] != experiment_id or trial['state'] != 'running' or trial['group'] == 'A':
                    raise ValueError('trial does not authorize external execution')
                if not any(r['group_id'] == group_id and r['run_id'] == run_id for r in trial['runs']):
                    raise ValueError('request must bind an attached group/run')
            elif request_kind != 'probe':
                raise ValueError('only preparation probes may omit trial identity')
            if stale:
                blocked = 'STALE_REQUEST_RESERVATION; explicit updated authorization required'
                exp['stale_request_reservations'] = stale
            elif estimated_tokens + sum(r['charged_tokens'] for r in requests) > caps['external_tokens']:
                blocked = 'EXPERIMENT_TOKEN_LIMIT'
            elif trial_id and estimated_tokens + sum(r['charged_tokens'] for r in trial_requests) > caps['trial_tokens']:
                blocked = 'TRIAL_TOKEN_LIMIT'
            elif run_id and estimated_tokens + sum(r['charged_tokens'] for r in trial_requests if r['run_id'] == run_id) > caps['run_tokens']:
                blocked = 'RUN_TOKEN_LIMIT'
            elif caps['external_cost'] is not None and (cost is None or any(r['charged_cost'] is None for r in requests)):
                blocked = 'COST_RESERVATION_UNKNOWN'
            elif caps['external_cost'] is not None and money(cost) + sum((money(r['charged_cost']) for r in requests), Decimal(0)) > money(caps['external_cost']):
                blocked = 'EXPERIMENT_COST_LIMIT'
            if blocked:
                exp['state'] = 'needs_review' if blocked == 'COST_RESERVATION_UNKNOWN' or stale else 'budget_stopped'
                self._put(db, 'evaluation_experiments', exp)
            else:
                db.execute('INSERT INTO evaluation_requests VALUES (?,?,?,?)', (request_id, experiment_id, trial_id, json.dumps(value)))
        if blocked:
            raise ValueError(blocked)
        return value

    def settle_request(self, request_id, usage_tokens=None, actual_cost=None, usage=None):
        usage = normalize_usage(usage)
        if usage is not None:
            if usage_tokens is not None and usage_tokens != usage['total']:
                raise ValueError('usage counters disagree')
            usage_tokens = usage['total']
        if usage_tokens is not None:
            integer(usage_tokens, 'usage_tokens')
        actual_cost = format(money(actual_cost), 'f') if actual_cost is not None else None
        settlement = dict(usage_tokens=usage_tokens, actual_cost=actual_cost, usage=usage)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            value = self._get(db, 'evaluation_requests', request_id)
            if value.get('settlement'):
                if value['settlement'] == settlement:
                    return value
                raise ValueError('SETTLEMENT_CONFLICT; preserve the first observation')
            exp = self._get(db, 'evaluation_experiments', value['experiment_id'])
            reference = sum_costs([usage], exp['frozen']['conditions']['external_price']) if usage else None
            value.update(settlement=settlement, state='settled', settled_at=time.time(), usage=usage, actual_cost=actual_cost,
                         charged_tokens=usage_tokens if usage_tokens is not None else value['reserved_tokens'],
                         charged_cost=actual_cost if actual_cost is not None else reference['high'] if reference else value['reserved_cost'],
                         charged_cost_basis='actual_bill' if actual_cost is not None else 'reference_estimate' if reference else 'reservation',
                         gap=usage_tokens is None)
            self._put(db, 'evaluation_requests', value)
            caps = exp['authorization']['caps']
            requests = self._requests(db, exp['id'])
            if any(r['gap'] and r.get('authorization_version',0) >= exp['authorization']['version'] for r in requests):
                exp['state'] = 'needs_review'
            elif sum(r['charged_tokens'] for r in requests) >= caps['external_tokens']:
                exp['state'] = 'budget_stopped'
            elif caps['external_cost'] is not None and sum((money(r['charged_cost']) for r in requests if r['charged_cost'] is not None), Decimal(0)) >= money(caps['external_cost']):
                exp['state'] = 'budget_stopped'
            self._put(db, 'evaluation_experiments', exp)
        return value

    def record_main_usage(self, trial_id, event_id, session_id, root_session_id, usage, source_fingerprint,
                          quality='reported', forked=False, missing=False, model=None, provider=None, occurred_at=None,
                          source_kind='direct',response_id=None,session_started_at=None,identity_version=2):
        """Explicit counter-only import; no inference from time windows and no message bodies."""
        if not event_id or not source_fingerprint or quality not in {'reported', 'cumulative_delta', 'identity_inferred', 'identity_unknown'}:
            raise ValueError('usage source and quality required')
        for stamp in (occurred_at,session_started_at):
            if stamp is not None and (isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp)):
                raise ValueError('usage timestamp must be finite')
        if source_kind not in {'direct','cumulative'} or response_id is not None and not isinstance(response_id,str):
            raise ValueError('invalid usage source identity')
        value = dict(id=event_id, trial_id=trial_id, session_id=session_id, root_session_id=root_session_id,
                     usage=normalize_usage(usage), source_fingerprint=source_fingerprint, quality=quality,
                     forked=bool(forked), missing=bool(missing), model=model or 'unknown', provider=provider or 'unknown',
                     occurred_at=occurred_at,source_kind=source_kind,response_id=response_id,session_started_at=session_started_at,
                     identity_version=identity_version)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial = self._get(db, 'evaluation_trials', trial_id)
            if 'started_at' not in trial:
                raise ValueError('usage requires a started trial')
            binding = trial.get('binding')
            issues = trial['measurement_issues']
            exp = self._get(db, 'evaluation_experiments', trial['experiment_id'])
            frozen = exp['frozen']['conditions']
            for field, frozen_key in (('model', 'main_model'), ('provider', 'main_provider')):
                expected = frozen.get(frozen_key, 'unknown')
                if value[field] == 'unknown' or expected == 'unknown':
                    issues.append('main_' + field + '_unknown')
                elif value[field] != expected:
                    issues.append('main_' + field + '_mismatch')
            if not binding or root_session_id != binding['root_session_id'] or session_id not in {binding['session_id'], *binding['related_sessions']}:
                issues.append('unbound_or_mixed_session')
                value['attributable'] = False
            else:
                value['attributable'] = True
            if forked or binding and binding['forked']:
                issues.append('fork_inheritance')
            if missing or value['usage'] is None:
                issues.append('missing_usage')
            if quality not in {'reported','cumulative_delta'}:
                issues.append(quality)
            old = db.execute('SELECT trial_id,payload FROM evaluation_usage_events WHERE id=?', (event_id,)).fetchone()
            if old:
                previous=json.loads(old[1])
                fields=('trial_id','session_id','root_session_id','usage','quality','forked','missing','model','provider','attributable','occurred_at','response_id','session_started_at')
                if all(previous.get(key)==value.get(key) for key in fields) and previous.get('source_kind','direct')==source_kind:
                    # A file fingerprint changes when the same session log grows.
                    # Re-reading an unchanged event is ordinary idempotent import.
                    return {'recorded':False,'duplicate':True,'idempotent':True}
                # Reused response IDs with different counters/session/time are
                # genuinely ambiguous. Preserve the first count and the warning.
                issues.append('duplicate_usage_event')
                issues.append('conflicting_usage_event')
                self._put(db, 'evaluation_trials', trial)
                if old[0] != trial_id:
                    other = self._get(db, 'evaluation_trials', old[0])
                    other['measurement_issues'].append('cross_trial_usage_event')
                    self._put(db, 'evaluation_trials', other)
                return {'recorded': False, 'duplicate': True,'idempotent':False,'conflicting':True}
            db.execute('INSERT INTO evaluation_usage_events VALUES (?,?,?)', (event_id, trial_id, json.dumps(value)))
            self._put(db, 'evaluation_trials', trial)
            totals=self._consumption(db,exp['id'])['main_reported_tokens']
            if exp['authorization'] and totals >= exp['authorization']['caps']['main_tokens']:
                exp.update(state='needs_review', main_limit_notice='observed main-model limit reached; host controls in-flight requests')
                self._put(db, 'evaluation_experiments', exp)
        return {'recorded': True, 'attributable': value['attributable']}

    def import_main_log(self, trial_id, path, confirmed_session_id):
        """Opt-in JSONL import, tied to an explicit session, never a time window.

        Stores only counters, hashed source identity and coverage issues. The raw
        file stays in the chosen directory; it is never copied into a report.
        """
        from .codex_usage import event_record
        source = Path(path).resolve(strict=True)
        with self.store.connect() as db:
            trial = self._get(db, 'evaluation_trials', trial_id)
            exp = self._get(db, 'evaluation_experiments', trial['experiment_id'])
        binding = trial['binding']
        auth = exp['authorization']
        directory = auth['data_scope']['usage_directory'] if auth else None
        if not directory or not source.is_relative_to(Path(directory).resolve()) or not source.is_file():
            raise ValueError('log import requires an authorized, explicitly chosen directory')
        if not binding or confirmed_session_id not in {binding['session_id'], *binding['related_sessions']}:
            raise ValueError('confirm an explicitly bound session before importing')
        with self.store.connect() as db:
            previous=[json.loads(row[0]) for row in db.execute('SELECT payload FROM evaluation_usage_events WHERE trial_id=?',(trial_id,))]
        if any(row.get('source_kind')=='cumulative' and row.get('identity_version')!=2 for row in previous):
            raise ValueError('LEGACY_CUMULATIVE_IDENTITY: retain this trial and prepare a new identity before re-importing')
        snapshot_size=source.stat().st_size
        state, offset, added, unchanged, issues = {}, 0, 0, 0, []
        fingerprint = file_hash(source,snapshot_size)
        consumed_hash=hashlib.sha256();deferred_tail=False
        with source.open('rb') as stream:
            while offset<snapshot_size:
                beginning=offset
                raw=stream.readline(min(LOG_LINE_BYTES+1,snapshot_size-offset))
                if not raw:break
                consumed_hash.update(raw);offset+=len(raw)
                if len(raw) > LOG_LINE_BYTES:
                    issues.append('oversized_log_line')
                    while offset<snapshot_size and not raw.endswith(b'\n'):
                        raw=stream.readline(min(65536,snapshot_size-offset))
                        if not raw:break
                        consumed_hash.update(raw);offset+=len(raw)
                    continue
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError,UnicodeDecodeError):
                    if offset==snapshot_size and not raw.endswith(b'\n'):
                        deferred_tail=True
                    else:
                        issues.append('invalid_log_line')
                    continue
                try:
                    if not isinstance(event, dict):
                        raise ValueError('invalid log event')
                    result = event_record(event, state, str(source), beginning,include_cumulative=True)
                except (ValueError, TypeError, AttributeError):
                    issues.append('invalid_log_line')
                    result = None
                if result:
                    key, at, source_kind, value = result
                    # Keep both telemetry streams; selection never double-adds
                    # their overlapping counters. Direct request IDs stay stable.
                    key=('cumulative:'+key) if source_kind=='cumulative' else key
                    observed=event.get('payload',{}).get('thread_id') or state.get('thread')
                    if not isinstance(observed,str):observed='unknown'
                    if observed != confirmed_session_id:
                        issues.append('mixed_log_session')
                    recorded = self.record_main_usage(trial_id, key, observed, binding['root_session_id'],
                        {k: value.get(k) for k in ('prompt', 'completion', 'cached_input', 'cache_write_input')},
                        fingerprint, quality=value['quality'], forked=state.get('fork', False), model=value['model'], provider=value['provider'],
                        occurred_at=at,source_kind=source_kind,response_id=value.get('response_id'),session_started_at=value.get('session_started_at'),
                        identity_version=value.get('identity_version',1))
                    added += recorded['recorded']
                    unchanged += recorded.get('idempotent',False)
        try:
            prefix_unchanged=source.stat().st_size>=snapshot_size and file_hash(source,snapshot_size)==fingerprint
        except OSError:
            prefix_unchanged=False
        if offset!=snapshot_size or consumed_hash.hexdigest()!=fingerprint or not prefix_unchanged:
            issues.append('log_changed_during_import')
        for key in ('invalid', 'resets', 'fork_replayed', 'fork_baselines'):
            if state.get(key):
                issues.append('log_' + key)
        if state.get('fork'):
            issues.append('fork_inheritance')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._get(db,'evaluation_trials',trial_id)
            current['measurement_issues']=sorted(set(current['measurement_issues']+issues))
            source_id=digest(os.path.normcase(str(source)))
            current.setdefault('log_imports',{})[source_id]=dict(source_fingerprint=fingerprint,source_bytes=snapshot_size,
                deferred_tail=deferred_tail,session_id=confirmed_session_id,observed_at=time.time())
            self._put(db,'evaluation_trials',current)
            main=[json.loads(row[0]) for row in db.execute('SELECT payload FROM evaluation_usage_events WHERE trial_id=?',(trial_id,))]
        _,streams,stream_issues,_=main_usage_selection(main)
        pending_issues=['log_tail_pending'] if any(info['deferred_tail'] for info in current.get('log_imports',{}).values()) else []
        return dict(records_added=added,records_unchanged=unchanged,source_fingerprint=fingerprint,
                    issues=sorted(set(current['measurement_issues']+stream_issues+pending_issues)),usage_streams=streams,
                    source_bytes=snapshot_size,deferred_tail=deferred_tail,
                    status='imported' if added else 'unchanged' if unchanged else 'no_usage_records_yet')

    def finish_trial(self, trial_id, outcome, not_delegated_reason=None, timings=None, first_run_passed=False):
        if outcome not in TRIAL_ENDINGS:
            raise ValueError('invalid trial outcome')
        if outcome == 'not_delegated' and not_delegated_reason not in {'decision', 'disabled', 'integration', 'policy', 'error'}:
            raise ValueError('record why no delegation occurred')
        timings = timings or {}
        if set(timings) - {'queue', 'execution', 'verification', 'continuation', 'parent_takeover'} or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in timings.values()):
            raise ValueError('invalid phase durations')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trial = self._get(db, 'evaluation_trials', trial_id)
            if trial['state'] != 'running':
                raise ValueError('trial must be running')
            if outcome == 'not_delegated' and db.execute('SELECT 1 FROM evaluation_requests WHERE trial_id=? LIMIT 1',(trial_id,)).fetchone():
                raise ValueError('a provider request was admitted; cannot report no delegation without resolving its send evidence')
            trial.update(state='ended', outcome=outcome, finished_at=time.time(), timings=timings,
                         not_delegated_reason=not_delegated_reason, first_run_passed=bool(first_run_passed))
            self._put(db, 'evaluation_trials', trial)
        return trial

    def score_trial(self, trial_id, answer_file=None):
        with self.store.connect() as db:
            trial = self._get(db, 'evaluation_trials', trial_id)
            exp = self._get(db, 'evaluation_experiments', trial['experiment_id'])
        if trial['state'] != 'ended':
            raise ValueError('score only ended trials')
        if file_hash(evaluation_cases.__file__) != exp['frozen']['scorer_sha256']:
            raise ValueError('SCORER_CHANGED; prepare a new frozen experiment')
        case = next(c for c in exp['frozen']['cases'] if c['id'] == trial['case_id'])
        before = tree_hashes(trial['workspace'])
        answer = Path(answer_file).resolve(strict=True) if answer_file else None
        if answer and answer.is_relative_to(Path(trial['workspace']).resolve()):
            raise ValueError('structured answers must be retained outside readonly trial materials')
        answer_hash = file_hash(answer) if answer else None
        result = evaluation_cases.score(case, Path(trial['workspace']), answer)
        after = tree_hashes(trial['workspace'])
        if before != after:
            raise ValueError('ARTIFACT_CHANGED_DURING_SCORING')
        if answer and answer_hash != file_hash(answer):
            raise ValueError('ANSWER_CHANGED_DURING_SCORING')
        score = dict(id=uuid.uuid4().hex, input_sha256=digest(case), scorer_sha256=exp['frozen']['scorer_sha256'],
                     artifact_hashes=before, scored_at=time.time(), passed=result['passed'], checks=result['checks'],
                     answer_reference={'path': str(answer), 'sha256': answer_hash} if answer else None,
                     source='independent_local_evaluator')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._get(db, 'evaluation_trials', trial_id)
            if current['state'] != 'ended':
                raise ValueError('trial changed during scoring')
            current['score'] = score
            self._put(db, 'evaluation_trials', current)
        return score

    def set_state(self, experiment_id, state):
        if state not in {'finished', 'stopped', 'budget_stopped', 'needs_review'}:
            raise ValueError('invalid terminal experiment state')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exp = self._get(db, 'evaluation_experiments', experiment_id)
            if state == 'finished' and (any(t['state'] != 'ended' for t in self._trials(db, experiment_id)) or
                                        any(r['state'] != 'settled' or r['gap'] for r in self._requests(db, experiment_id))):
                raise ValueError('unfinished trials or request gaps require review')
            exp.update(state=state, revision=exp['revision'] + 1)
            self._put(db, 'evaluation_experiments', exp)
        return self.describe(experiment_id)

    def trial_instruction(self, trial_id):
        with self.store.connect() as db:
            trial = self._get(db, 'evaluation_trials', trial_id)
            exp = self._get(db, 'evaluation_experiments', trial['experiment_id'])
        case = next(c for c in exp['frozen']['cases'] if c['id'] == trial['case_id'])
        mode = 'readonly' if case['kind'] in {'review', 'analysis'} else 'coding'
        group_params = dict(goal=case['task'], task_kind=case['kind'], capability=mode, task_root=trial['workspace'],
                            write_paths=[str(Path(trial['workspace']) / p) for p in case.get('write_paths', [])],
                            acceptance='Run the frozen independent evaluator after inspecting actual artifacts; do not alter grader or initial material outside allowed paths.',
                            constraints='Isolated frozen trial. No unrelated edits. Keep all request kinds in the experiment ledger.',
                            client_binding_id='<id returned by bind_client_session>', experiment_id=exp['id'], trial_id=trial_id)
        steps = [] if trial['group'] == 'A' else [
            {'tool': 'bind_client_session', 'params': {'session_id': '<confirmed new chat ID>', 'root_session_id': '<confirmed root ID>',
             'client_kind': '<desktop or cli>', 'confirmed': True}},
            {'tool': 'create_task_group', 'params': group_params},
            {'tool': 'start_agent_readonly' if mode == 'readonly' else 'start_agent',
             'params': {'task': case['task'], 'group_id': '<group_id returned by create_task_group>',
                        'model': exp['frozen']['conditions'].get('external_slot') if exp['frozen']['conditions'].get('external_slot') in {'flash','pro'} else '<freeze flash or pro before execution>'}},
            {'tool': 'get_task_status', 'params': {'job_id': '<job_id returned by start_agent>'}},
            {'tool': 'get_task_checkpoint', 'params': {'checkpoint_id': '<current checkpoint_id>'}},
            {'tool': 'get_task_recovery', 'params': {'client_binding_id':'<id returned by bind_client_session>'}},
            {'tool': 'record_task_verification', 'params': {'job_id': '<job_id>', 'passed': '<true only after independent checks>',
             'evidence': '<actual file/test/source evidence>', 'checkpoint_id': '<current checkpoint_id>',
             'checkpoint_sha256': '<current sha256>', 'disposition': '<verified_complete, verified_partial or needs_more_evidence>'}}]
        for step in steps:
            if step['tool']!='bind_client_session':
                step['params']['client_binding_id']='<id returned by bind_client_session>'
        return dict(trial_id=trial_id, experiment_id=exp['id'], workspace=trial['workspace'], task=case['task'],
                    group=trial['group'], backend_id=trial['backend_id'], input_sha256=trial['input_sha256'],
                    client_binding_steps=steps,
                    instructions='Use a new chat. Explicitly bind its root/session. A: isolated delegation disabled. '
                    'B/C: selected backend, fixed provider/model/reasoning; independently inspect artifacts. '
                    'Before group creation, bind this same root/session to the trial via Evaluation.bind_session. '
                    'Always pass experiment_id, trial_id and client_binding_id in create_task_group, then reuse its group_id. '
                    'Pass this same client_binding_id to start, status/result, group/checkpoint reads, verification, recovery acknowledgements and resume. '
                    'Do not alter the frozen scorer. Daily settings require separate authorization.')

    def report(self, experiment_id):
        """Counter-only report. Unknown/partial cost never becomes a complete saving claim."""
        desc = self.describe(experiment_id)
        with self.store.connect() as db:
            exp = self._get(db, 'evaluation_experiments', experiment_id)
            requests = self._requests(db, experiment_id)
            events = {}
            for trial in desc['trials']:
                events[trial['id']] = [json.loads(r[0]) for r in db.execute('SELECT payload FROM evaluation_usage_events WHERE trial_id=?', (trial['id'],))]
            engine_runs = {}
            session_owners={}
            for row in db.execute('SELECT id,payload FROM evaluation_trials'):
                bound=json.loads(row[1]).get('binding')
                if bound:
                    for session in owned_sessions(bound):session_owners.setdefault(session,set()).add(row[0])
            for trial in desc['trials']:
                valid = []
                for reference in trial['runs']:
                    task_row = db.execute('SELECT payload FROM tasks WHERE id=?',(reference['run_id'],)).fetchone()
                    member = db.execute('SELECT group_id FROM group_runs WHERE run_id=?',(reference['run_id'],)).fetchone()
                    task = json.loads(task_row[0]) if task_row else {}
                    if (reference.get('source') == 'engine_record' and member and member[0] == reference['group_id'] and
                            task.get('group_id') == reference['group_id'] and task.get('backend_id') == trial['backend_id'] and
                            Path(task.get('workspace','')).resolve() == Path(trial['workspace']).resolve()):
                        valid.append(reference['run_id'])
                engine_runs[trial['id']] = valid
        conditions = exp['frozen']['conditions']
        rows, groups = [], {}
        for trial in desc['trials']:
            selected = [r for r in requests if r['trial_id'] == trial['id']]
            main = events[trial['id']]
            counted_main,main_streams,stream_issues,main_lower_bound=main_usage_selection(main)
            binding = trial['binding']
            issues = sorted(set(trial['measurement_issues']))
            issues.extend(stream_issues)
            if binding and any(len(session_owners.get(session,set()))>1 for session in owned_sessions(binding)):
                issues.append('shared_client_session_across_trials')
            if any(info['deferred_tail'] for info in trial.get('log_imports',{}).values()):
                issues.append('log_tail_pending')
            no_external_request=trial.get('outcome')=='not_delegated' and not selected
            if selected or trial['runs'] and not no_external_request:
                for key in ('external_model', 'external_provider_revision', 'external_reasoning'):
                    if conditions.get(key) in {None, 'unknown'}:
                        issues.append(key + '_unknown')
            for event in main:
                for field, frozen_key in (('model','main_model'),('provider','main_provider')):
                    observed, expected = event.get(field,'unknown'), conditions.get(frozen_key,'unknown')
                    if observed == 'unknown' or expected == 'unknown':
                        issues.append('main_' + field + '_unknown')
                    elif observed != expected:
                        issues.append('main_' + field + '_mismatch')
            related_seen = {e['session_id'] for e in main if e['attributable']}
            expected_sessions = {binding['session_id'], *binding['related_sessions']} if binding else set()
            if not binding:
                issues.append('missing_explicit_binding')
            if expected_sessions - related_seen:
                issues.append('missing_related_session_usage')
            if not main:
                issues.append('missing_main_usage')
            if any(r['gap'] for r in selected):
                issues.append('external_request_usage_gap')
            if trial['runs'] and not selected and not no_external_request:
                issues.append('external_requests_not_recorded')
            if len(engine_runs[trial['id']]) != len(trial['runs']):
                issues.append('unverified_or_stale_engine_run_reference')
            if binding and binding['forked']:
                issues.append('fork_inheritance')
            score = trial.get('score')
            try:
                score_valid = bool(score and score['scorer_sha256'] == file_hash(evaluation_cases.__file__) and
                                   score['artifact_hashes'] == tree_hashes(trial['workspace']) and
                                   score['input_sha256'] == trial['input_sha256'])
                if score_valid and score.get('answer_reference'):
                    score_valid = file_hash(score['answer_reference']['path']) == score['answer_reference']['sha256']
            except (OSError, ValueError):
                score_valid = False
            main_usage = [e['usage'] for e in counted_main]
            ext_usage = [r['usage'] for r in selected if r['usage']]
            main_cost = sum_costs(main_usage, conditions['main_price'])
            if any(issue.startswith(('main_model_', 'main_provider_')) for issue in issues):
                main_cost = None
            external_cost = sum_costs(ext_usage, conditions['external_price']) if selected else {'low': '0', 'high': '0'}
            if len(ext_usage) < len(selected) or any(issue.startswith(('external_model_', 'external_provider_revision_', 'external_reasoning_')) for issue in issues):
                external_cost = None
            known_cost = combine_bounds(main_cost, external_cost)
            complete = not issues and bool(main_usage) and all(r['state'] == 'settled' and r['usage'] is not None for r in selected)
            total = known_cost if complete else None
            delegated = any(r['run_id'] in engine_runs[trial['id']] and r['state']=='settled' for r in selected)
            row = dict(trial_id=trial['id'], case_id=trial['case_id'], repeat=trial['repeat'], order=trial['order'],
                       group=trial['group'], state=trial['state'], outcome=trial['outcome'], started='started_at' in trial,
                       delegated=delegated, parent_takeover=trial['outcome'] == 'completed_by_parent',
                       first_run_passed=delegated and score_valid and score['passed'] and len(trial['runs']) == 1 and trial['outcome'] != 'completed_by_parent',
                       not_delegated_reason=trial.get('not_delegated_reason'),
                       binding=binding, runs=trial['runs'], score=score, score_valid=score_valid,
                       quality_passed=score_valid and score['passed'], coverage={'attributable': bool(binding) and not any(x in issues for x in ('unbound_or_mixed_session', 'fork_inheritance', 'cross_trial_usage_event','shared_client_session_across_trials')),
                       'usage_complete': complete, 'priced': total is not None, 'independently_scored': score_valid, 'issues': sorted(set(issues))},
                       main_tokens=main_lower_bound if main_usage else None,main_usage_streams=main_streams,
                       main_tokens_basis='reported_lower_bound' if stream_issues else 'reported',
                       main_model_provenance=[{'model': model, 'provider': provider} for model, provider in sorted({(e.get('model','unknown'),e.get('provider','unknown')) for e in main})],
                       external_reported_tokens=sum(u['total'] for u in ext_usage) if ext_usage else None,
                       main_reference_estimate=main_cost, external_reference_estimate=external_cost,
                       known_reference_subtotal=known_cost, total_reference_estimate=total,
                       actual_external_bill=actual_bill(selected),
                       elapsed_seconds=trial.get('finished_at', time.time()) - trial['started_at'] if 'started_at' in trial else None,
                       timings=trial.get('timings', {}), continuations=max(0, len(trial['runs']) - 1))
            rows.append(row)
        for group in ('A', 'B', 'C'):
            planned = [r for r in rows if r['group'] == group]
            started = [r for r in planned if r['started']]
            passed = [r for r in started if r['quality_passed']]
            delegated = [r for r in started if r['delegated']]
            known = [r['total_reference_estimate'] for r in started if r['total_reference_estimate'] is not None]
            aggregate = total_bounds(known) if known else None
            full = bool(started) and len(known) == len(started)
            groups[group] = dict(planned=len(planned), started=len(started), quality_passed=len(passed),
                completion_rate=ratio(len(passed), len(started)),
                effective_delegation_rate=ratio(sum(r['quality_passed'] and r['delegated'] and not r['parent_takeover'] for r in started), len(planned)) if group != 'A' else None,
                first_run_pass_rate=ratio(sum(r['first_run_passed'] for r in delegated), len(delegated)),
                parent_takeover_rate=ratio(sum(r['parent_takeover'] for r in started), len(planned)) if group != 'A' else None,
                not_delegated_rate=ratio(sum(not r['delegated'] for r in started), len(planned)) if group != 'A' else None,
                outcomes=dict(Counter(r['outcome'] or 'in_progress' for r in started)),
                coverage={k: sum(r['coverage'][k] for r in started) for k in ('attributable', 'usage_complete', 'priced', 'independently_scored')},
                known_reference_subtotal=aggregate, total_reference_estimate=aggregate if full else None,
                successful_trials_reference_cost=total_bounds([r['total_reference_estimate'] for r in passed]) if passed and all(r['total_reference_estimate'] for r in passed) else None,
                all_attempt_cost_per_accepted_task=divide_bounds(aggregate, len(passed)) if full and passed else None,
                elapsed_seconds=distribution([r['elapsed_seconds'] for r in started]),
                continuations=sum(r['continuations'] for r in started))
        pairs = []
        for a in [r for r in rows if r['group'] == 'A']:
            for b in [r for r in rows if r['group'] in {'B', 'C'} and r['case_id'] == a['case_id'] and r['repeat'] == a['repeat']]:
                net = subtract_bounds(a['total_reference_estimate'], b['total_reference_estimate'])
                if not a['quality_passed'] or not b['quality_passed']:
                    category = 'quality_failed' if a['score_valid'] and b['score_valid'] else 'insufficient_independent_scoring'
                elif net is None:
                    category = 'insufficient_metering'
                elif Decimal(net['high']) < 0:
                    category = 'cost_increased'
                elif Decimal(net['low']) > 0:
                    category = 'quality_passed_positive_estimate'
                else:
                    category = 'quality_passed_direction_uncertain'
                pairs.append(dict(case_id=a['case_id'], repeat=a['repeat'], direct_trial=a['trial_id'],
                                  delegated_trial=b['trial_id'], group=b['group'], net_reference_estimate=net,
                                  category=category, elapsed_difference_seconds=a['elapsed_seconds'] - b['elapsed_seconds'] if a['elapsed_seconds'] is not None and b['elapsed_seconds'] is not None else None))
        probes = [r for r in requests if r['trial_id'] is None]
        preparation_cost = sum_costs([r['usage'] for r in probes], conditions['external_price']) if probes else {'low': '0', 'high': '0'}
        started_rows = [r for r in rows if r['started']]
        allocation = divide_bounds(preparation_cost, len(started_rows))
        amortized_rows = [dict(trial_id=r['trial_id'], reference_estimate=combine_bounds(r['total_reference_estimate'], allocation))
                          for r in started_rows]
        return dict(report_version=1, generated_at=time.time(), experiment={k: v for k, v in desc.items() if k != 'trials'},
                    trials=rows, groups=groups, pairs=pairs,
                    preparation={'requests': len(probes), 'charged_tokens': sum(r['charged_tokens'] for r in probes),
                                 'reference_estimate': preparation_cost,
                                 'unamortized': 'preparation reported once, separately; base trial and pair views exclude preparation',
                                 'amortized_view': {'rule': 'equal allocation across all actually started trials, including failures and not-delegated trials',
                                     'actual_trial_count': len(started_rows), 'per_trial': allocation, 'trials': amortized_rows,
                                     'total_with_preparation': combine_bounds(total_bounds([r['total_reference_estimate'] for r in started_rows]), preparation_cost),
                                     'double_counting': 'alternative view only; never add base and allocated totals together'}},
                    ledger={'requests': len(requests), 'charged_tokens': sum(r['charged_tokens'] for r in requests),
                            'unsettled': sum(r['state'] != 'settled' for r in requests), 'usage_gaps': sum(r['gap'] for r in requests)},
                    billing={'main_basis': conditions['main_billing'], 'external_basis': conditions['external_billing'],
                             'reference_estimate_is_actual_bill': False,
                             'subscription_savings': None, 'main_control': 'host-managed; observation and stop notice only'},
                    limits=['Five cases are a pilot, not category-wide evidence.', 'No general savings or significance claim.',
                            'No source bodies, transcripts, API keys or raw provider responses in this report.'])


def sum_costs(usages, price):
    if not usages or price is None or any(u is None for u in usages):
        return None
    if any(u.get('cache_write_input', 0) is None for u in usages):
        return None
    if price.get('cached_input') is None and any(u.get('cached_input') != 0 for u in usages):
        return None
    values = [estimate(u, price) for u in usages]
    return amount((sum((x[0] for x in values), Decimal(0)), sum((x[1] for x in values), Decimal(0)))) if all(v is not None for v in values) else None


def total_bounds(values):
    if not values or any(v is None for v in values):
        return None
    return {'low': format(sum((Decimal(v['low']) for v in values), Decimal(0)), 'f'),
            'high': format(sum((Decimal(v['high']) for v in values), Decimal(0)), 'f')}


def combine_bounds(a, b):
    return total_bounds([a, b])


def subtract_bounds(a, b):
    return None if a is None or b is None else {'low': format(Decimal(a['low']) - Decimal(b['high']), 'f'),
                                               'high': format(Decimal(a['high']) - Decimal(b['low']), 'f')}


def divide_bounds(value, count):
    return None if not value or not count else {k: format(Decimal(v) / count, 'f') for k, v in value.items()}


def actual_bill(requests):
    if not requests or any(r['actual_cost'] is None for r in requests):
        return None
    return format(sum((money(r['actual_cost']) for r in requests), Decimal(0)), 'f')


def ratio(numerator, denominator):
    return dict(numerator=numerator, denominator=denominator, value=numerator / denominator if denominator else None)


def distribution(values):
    values = [v for v in values if v is not None]
    return dict(median=statistics.median(values), min=min(values), max=max(values), samples=values) if values else None


def dispatch(store, action, params):
    """Explicit RPC surface. Polling snapshots do not load experiments or reports."""
    allowed = {'create_experiment', 'prepare_experiment', 'describe', 'authorization_preview', 'authorize', 'start_trial',
               'bind_session', 'add_related_sessions', 'finish_trial', 'score_trial', 'report', 'set_state', 'trial_instruction', 'record_main_usage', 'import_main_log'}
    if action not in allowed:
        raise ValueError('unsupported evaluation action')
    return getattr(Evaluation(store), action)(**params)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='explicit isolated state directory')
    parser.add_argument('action', choices=['create', 'prepare', 'describe', 'instruction', 'preview-authorization', 'authorize', 'start', 'bind', 'add-related', 'attach-run', 'finish', 'score', 'report', 'state', 'main-usage', 'import-main-log'])
    parser.add_argument('--params', required=True, help='JSON object file; no credentials or transcripts')
    parser.add_argument('--output', help='optional output JSON file')
    args = parser.parse_args(argv)
    from .state import Store
    evaluation = Evaluation(Store(Path(args.state).resolve()))
    params = json.loads(Path(args.params).read_text(encoding='utf-8-sig'))
    methods = {'create': 'create_experiment', 'prepare': 'prepare_experiment', 'describe': 'describe', 'instruction': 'trial_instruction',
               'preview-authorization': 'authorization_preview', 'authorize': 'authorize', 'start': 'start_trial',
               'bind': 'bind_session', 'add-related':'add_related_sessions', 'attach-run': 'attach_run', 'finish': 'finish_trial', 'score': 'score_trial',
               'report': 'report', 'state': 'set_state', 'main-usage': 'record_main_usage', 'import-main-log': 'import_main_log'}
    result = getattr(evaluation, methods[args.action])(**params)
    output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output:
        Path(args.output).write_text(output + '\n', encoding='utf-8')
    else:
        print(output)


if __name__ == '__main__':
    main()
