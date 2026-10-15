"""Retained synthetic evaluation report and unapproved 45-trial preparation.

No model requests, host settings, credentials or daily installation are used.
All recorded usage/authorization below is deterministic test data only.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.state import Store
from codexrelay.evaluation import Evaluation, file_hash
from codexrelay.evaluation_cases import builtin_cases

ANSWERS = {
    'testing': {'exit_code':1,'failing_test':'test_upper_bound','cause':'exclusive_upper_bound','source':'clamp.py:2','fix':'include_upper_bound'},
    'review': {'findings':[{'path':'auth.py','line':2,'issue':'empty_token_authorized','impact':'unauthenticated_access'}],'normal_files':['utils.py']},
    'analysis': {'requests':3,'completed':2,'timeouts':1,'timeout_request':'r2','timeout_source':'logs/requests.jsonl:2','configured_timeout_seconds':12,'configuration_source':'settings.json:1','root_cause':'unknown','latency_p50_ms':40}}


def main():
    folder = Path(tempfile.mkdtemp(prefix='evaluation-reliability-', dir=ROOT / '.tmp'))
    store=Store(folder/'state');ev = Evaluation(store)
    price = dict(input='10',output='20',cached_input='2',cache_write='5',currency='CNY',source='synthetic fixture only',observed_at='2026-10-15')
    conditions = dict(main_model='synthetic-codex',main_provider='synthetic-openai',main_reasoning='fixture',external_model='synthetic-worker',external_reasoning='none',
                      external_provider_revision='synthetic-r1',runtime_version='0.4.0-local',policy_version='fixture-v1',dependency_version='bundled',
                      environment='deterministic local fixture; no HTTP or model execution',currency='CNY',main_price=price,external_price=price,
                      main_billing='subscription',external_billing='api')
    exp = ev.create_experiment('SYNTHETIC LOCAL FIXTURE — not a model benchmark', conditions)
    exp = ev.prepare_experiment(exp['id'], str(folder / 'trials'))
    caps = dict(external_tokens=1000000,trial_tokens=100000,run_tokens=100000,max_trials=15,main_tokens=1000000,external_cost='100')
    scope = dict(usage_directory=None,raw_evidence_directory=None)
    preview = ev.authorization_preview(exp['id'],caps,scope)
    ev.authorize(exp['id'],caps,scope,preview['preview_sha256'],True)
    for t in exp['trials']:
        ev.start_trial(t['id'])
        kind = t['case_id'].split('-')[0]
        session = 'synthetic-' + t['id']
        ev.bind_session(t['id'],session,session,'local_substitute',True)
        gap = kind == 'analysis' and t['group'] == 'B'
        ev.record_main_usage(t['id'],'main-'+t['id'],session,session,
            None if gap else dict(prompt=100,completion=20,cached_input=0,cache_write_input=0),
            'synthetic-counter-fixture', missing=gap,model='synthetic-codex',provider='synthetic-openai')
        not_delegated = kind == 'testing' and t['group'] == 'B'
        parent = kind == 'review' and t['group'] == 'C'
        failed = kind == 'review' and t['group'] == 'B'
        if t['group'] != 'A' and not not_delegated:
            run = 'synthetic-run-' + t['id']
            with store.connect() as db:
                payload=dict(id=run,group_id='synthetic-group-'+t['id'],workspace=t['workspace'],backend_id=t['backend_id'],
                             model='synthetic-worker',model_slot='flash',provider_revision='synthetic-r1',reasoning_effort='none',source='synthetic_fixture')
                db.execute('INSERT INTO tasks VALUES (?,?,0)',(run,json.dumps(payload)))
                db.execute('INSERT INTO group_runs VALUES (?,?)',('synthetic-group-'+t['id'],run))
            ev.attach_run(t['id'],'synthetic-group-'+t['id'],run,t['backend_id'],workspace=t['workspace'])
            request = 'synthetic-request-'+t['id']
            ev.reserve_request(exp['id'],t['id'],request,10000,'1','synthetic-group-'+t['id'],run)
            ev.settle_request(request,usage=dict(prompt=1000 if t['group']=='B' else 20,completion=10,cached_input=0,cache_write_input=0))
        answer = None
        workspace = Path(t['workspace'])
        if kind == 'implementation':
            (workspace / 'clamp.py').write_text('def clamp(value, lower, upper):\n    if lower > upper: raise ValueError("bounds")\n    return min(upper, max(lower, value))\n',encoding='utf-8')
        elif kind == 'documentation':
            (workspace / 'RUNBOOK.md').write_text('Sources: release.txt; operations.txt. Run python -m unittest discover -s tests. Timeout: 12 seconds. Use explicit cancellation and review partial changes. API key stays in Credential Manager.',encoding='utf-8')
        else:
            answer = folder / ('answer-'+t['id']+'.json')
            answer.write_text(json.dumps({} if failed else ANSWERS[kind]),encoding='utf-8')
        ev.finish_trial(t['id'],'not_delegated' if not_delegated else 'completed_by_parent' if parent else 'failed' if failed else 'completed',
                        not_delegated_reason='disabled' if not_delegated else None,
                        timings=dict(queue=0,execution=1,verification=.2,continuation=0,parent_takeover=1 if parent else 0))
        ev.score_trial(t['id'],str(answer) if answer else None)
    ev.set_state(exp['id'],'needs_review')
    report = ev.report(exp['id'])
    # A second independently frozen experiment illustrates unknown prices without
    # rewriting prices or the identity of any completed trial.
    unpriced = ev.create_experiment('SYNTHETIC unknown-price fixture',conditions|{'main_price':None,'external_price':None},builtin_cases()[:1])
    unpriced = ev.prepare_experiment(unpriced['id'],str(folder/'unpriced'))
    pv = ev.authorization_preview(unpriced['id'],caps|{'external_cost':None},scope)
    ev.authorize(unpriced['id'],caps|{'external_cost':None},scope,pv['preview_sha256'],True)
    t = next(t for t in unpriced['trials'] if t['group']=='A')
    ev.start_trial(t['id']);ev.bind_session(t['id'],'unknown-price','unknown-price','local_substitute',True)
    ev.record_main_usage(t['id'],'unknown-price-event','unknown-price','unknown-price',dict(prompt=10,completion=10,cache_write_input=0),'fixture',model='synthetic-codex',provider='synthetic-openai')
    ev.finish_trial(t['id'],'failed');ev.score_trial(t['id'])
    unknown_report = ev.report(unpriced['id'])
    unknown = {k: 'unknown' for k in ('main_model','main_reasoning','external_model','external_reasoning','external_provider_revision','runtime_version','policy_version','dependency_version','environment')}
    unknown |= dict(currency='CNY',main_billing='unknown',external_billing='unknown')
    planned = ev.create_experiment('45 trials PREPARED ONLY — NOT AUTHORIZED',unknown)
    planned = ev.prepare_experiment(planned['id'],str(folder/'prepared-45'),repetitions=3)
    prepared_manifest = folder / 'prepared-45-manifest.json'
    prepared_manifest.write_text(json.dumps(planned,indent=2,ensure_ascii=False),encoding='utf-8')
    instructions = folder / 'prepared-45-instructions.json'
    instructions.write_text(json.dumps([ev.trial_instruction(t['id']) for t in planned['trials']],indent=2,ensure_ascii=False),encoding='utf-8')
    assert planned['authorization'] is None and ev.report(planned['id'])['ledger']['requests']==0
    output = ROOT/'artifacts/evidence/0.4.0/evaluation-deterministic-report.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(evidence_kind='deterministic_local_fixture', real_model_requests=0,
        paid_authorization=False,fixture_directory=str(folder), reports=[report,unknown_report],
        prepared_45=dict(manifest=str(prepared_manifest),manifest_file_sha256=file_hash(prepared_manifest),
                         instructions=str(instructions),instructions_sha256=file_hash(instructions),
                         experiment_id=planned['id'],authorization=None,requests=0),
        limits=['Synthetic counters test reporting mechanics only; no model-quality or real savings evidence.',
                'True Desktop/CLI sessions and paid providers have not run.']),indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(dict(report=str(output),fixture_directory=str(folder),prepared_manifest=str(prepared_manifest),
                          instructions=str(instructions),trials=45,paid_requests=0),indent=2))


if __name__=='__main__':
    main()
