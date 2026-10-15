"""Independent local experiment contracts; no network, credentials or daily settings."""
import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.state import Store
from codexrelay.evaluation import Evaluation, file_hash, sum_costs, subtract_bounds
from codexrelay.evaluation_cases import builtin_cases

GOOD_CLAMP = 'def clamp(value, lower, upper):\n    if lower > upper: raise ValueError("bounds")\n    return min(upper, max(lower, value))\n'


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='evaluation-', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state')
        self.ev = Evaluation(self.store)

    def conditions(self, **change):
        price = dict(input='10', output='20', cached_input='2', cache_write='5', currency='CNY', source='fixture', observed_at='2026-10-15')
        return dict(main_model='codex-fixture', main_provider='fixture-openai', main_reasoning='unknown', external_model='worker-fixture', external_reasoning='none',
                    external_provider_revision='fixture-r1', runtime_version='local-fixture', policy_version='test-v1',
                    environment='isolated local fixture', currency='CNY', main_price=price, external_price=price,
                    main_billing='subscription', external_billing='api') | change

    def prepare(self, cases=None, repetitions=1, **conditions):
        value = self.ev.create_experiment('fixture', self.conditions(**conditions), cases or builtin_cases()[:1])
        return self.ev.prepare_experiment(value['id'], str(self.root / 'workspaces'), repetitions)

    def authorize(self, exp, **changes):
        caps = dict(external_tokens=1000, trial_tokens=500, run_tokens=300, max_trials=15, main_tokens=2000, external_cost='100') | changes
        scope = {'usage_directory': None, 'raw_evidence_directory': None}
        preview = self.ev.authorization_preview(exp['id'], caps, scope)
        return self.ev.authorize(exp['id'], caps, scope, preview['preview_sha256'], confirmed=True)

    def start(self, exp, group='B'):
        trial = next(t for t in exp['trials'] if t['group'] == group)
        return self.ev.start_trial(trial['id'])

    def attach(self, trial, run='r1'):
        with self.store.connect() as db:
            payload=dict(id=run,group_id='g1',workspace=trial['workspace'],backend_id=trial['backend_id'],
                         model='worker-fixture',model_slot='flash',provider_revision='fixture-r1',reasoning_effort='none')
            db.execute('INSERT OR IGNORE INTO tasks VALUES (?,?,0)',(run,json.dumps(payload)))
            db.execute('INSERT OR IGNORE INTO group_runs VALUES (?,?)',('g1',run))
        return self.ev.attach_run(trial['id'], 'g1', run, trial['backend_id'], workspace=trial['workspace'],
                                  model='worker-fixture', provider_revision='fixture-r1', reasoning='none')

    def reserve(self, exp, trial, identifier='q1', tokens=20, cost='1', run='r1', kind='execution'):
        return self.ev.reserve_request(exp['id'], trial['id'], identifier, tokens, cost, 'g1', run, kind)

    def bind_usage(self, trial, key='m1', **changes):
        session = 's-' + trial['id']
        self.ev.bind_session(trial['id'], session, session, 'local_substitute', confirmed=True)
        return self.ev.record_main_usage(trial['id'], key, session, session,
                                        dict(prompt=100, completion=20, cached_input=0, cache_write_input=0), 'fixture-file-hash',
                                        model='codex-fixture',provider='fixture-openai', **changes)

    def end_and_score(self, trial, outcome='completed'):
        (Path(trial['workspace']) / 'clamp.py').write_text(GOOD_CLAMP, encoding='utf-8')
        self.ev.finish_trial(trial['id'], outcome)
        return self.ev.score_trial(trial['id'])

    def test_preparation_isolated_frozen_and_does_not_authorize(self):
        exp = self.prepare(repetitions=3)
        self.assertEqual(exp['state'], 'prepared')
        self.assertEqual(len(exp['trials']), 9)
        self.assertEqual(len({t['workspace'] for t in exp['trials']}), 9)
        self.assertEqual(len({t['input_sha256'] for t in exp['trials']}), 1)
        self.assertEqual(len({json.dumps(t['initial_hashes']) for t in exp['trials']}), 1)
        orders = [[t['group'] for t in sorted([t for t in exp['trials'] if t['repeat'] == n], key=lambda t: t['order'])] for n in range(3)]
        self.assertEqual(orders, [['A', 'B', 'C'], ['B', 'C', 'A'], ['C', 'A', 'B']])
        with self.assertRaisesRegex(ValueError, 'NOT_AUTHORIZED'):
            self.start(exp)
        with self.assertRaisesRegex(ValueError, 'only draft'):
            self.ev.prepare_experiment(exp['id'], str(self.root / 'workspaces'))
        self.assertFalse(self.store.setting('enabled'))

    def test_initial_drift_and_serial_trial_limit(self):
        exp = self.authorize(self.prepare(), max_trials=1)
        a = self.start(exp, 'A')
        with self.assertRaisesRegex(ValueError, 'serially'):
            self.start(exp, 'B')
        self.ev.finish_trial(a['id'], 'failed')
        with self.assertRaisesRegex(ValueError, 'TRIAL_LIMIT'):
            self.start(exp, 'B')
        exp2 = self.authorize(self.prepare())
        t = exp2['trials'][0]
        (Path(t['workspace']) / 'clamp.py').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'INITIAL_FILES_CHANGED'):
            self.ev.start_trial(t['id'])

    def test_authorization_requires_current_preview_and_explicit_confirmation(self):
        exp = self.prepare()
        caps = dict(external_tokens=100, trial_tokens=100, run_tokens=100, max_trials=3, main_tokens=100, external_cost=None)
        scope = dict(usage_directory=None, raw_evidence_directory=None)
        preview = self.ev.authorization_preview(exp['id'], caps, scope)
        with self.assertRaisesRegex(ValueError, 'explicit user'):
            self.ev.authorize(exp['id'], caps, scope, preview['preview_sha256'])
        self.ev.authorize(exp['id'], caps, scope, preview['preview_sha256'], True)
        with self.assertRaisesRegex(ValueError, 'STALE'):
            self.ev.authorize(exp['id'], caps, scope, preview['preview_sha256'], True)

    def test_run_identity_workspace_backend_and_model_are_frozen(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp)
        for params in [dict(backend_id='pi'), dict(workspace=str(self.root)), dict(model='other'), dict(provider_revision='r2')]:
            values = dict(trial_id=trial['id'], group_id='g', run_id='r', backend_id='deepseek') | params
            with self.assertRaises(ValueError):
                self.ev.attach_run(**values)
        self.attach(trial)
        with self.assertRaisesRegex(ValueError, 'attached'):
            self.reserve(exp, trial, run='not-attached')

    def test_experiment_trial_and_run_limits_stop_new_requests(self):
        for caps, expected in [(dict(external_tokens=25), 'EXPERIMENT_TOKEN_LIMIT'),
                               (dict(trial_tokens=25), 'TRIAL_TOKEN_LIMIT'), (dict(run_tokens=25), 'RUN_TOKEN_LIMIT')]:
            with self.subTest(expected=expected):
                exp = self.authorize(self.prepare(), **caps)
                trial = self.start(exp)
                self.attach(trial, run=trial['id'])
                self.reserve(exp, trial, identifier=trial['id'], tokens=20, run=trial['id'])
                with self.assertRaisesRegex(ValueError, expected):
                    self.reserve(exp, trial, identifier=trial['id'] + '-next', tokens=6, run=trial['id'])
                report = self.ev.report(exp['id'])
                self.assertEqual(report['ledger']['requests'], 1)
                self.assertEqual(report['experiment']['state'], 'budget_stopped')

    def test_unknown_cost_fails_closed_and_decimal_cap_is_cumulative(self):
        exp = self.authorize(self.prepare(), external_cost='0.3')
        trial = self.start(exp)
        self.attach(trial)
        self.reserve(exp, trial, cost='0.1')
        with self.assertRaisesRegex(ValueError, 'COST_LIMIT'):
            self.reserve(exp, trial, identifier='q2', cost='0.20000000000000001')
        self.authorize(exp, external_cost='1')
        with self.assertRaisesRegex(ValueError, 'COST_RESERVATION_UNKNOWN'):
            self.reserve(exp, trial, identifier='q3', cost=None)

    def test_unknown_usage_preserves_reservation_and_is_not_replayed(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp)
        self.attach(trial)
        self.reserve(exp, trial, tokens=30, cost='2')
        settled = self.ev.settle_request('q1')
        self.assertEqual(settled['charged_tokens'], 30)
        self.assertEqual(settled['charged_cost'], '2')
        self.assertTrue(settled['gap'])
        self.assertEqual(self.ev.settle_request('q1'), settled)
        with self.assertRaisesRegex(ValueError, 'SETTLEMENT_CONFLICT'):
            self.ev.settle_request('q1', 0)
        self.authorize(exp)
        with self.assertRaisesRegex(ValueError, 'ALREADY_RESERVED'):
            self.reserve(exp, trial)

    def test_settlement_releases_only_known_difference_and_preserves_actual_bill_unknown(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp)
        self.attach(trial)
        self.reserve(exp, trial, tokens=100, cost='10')
        result = self.ev.settle_request('q1', usage=dict(prompt=10, completion=5, cached_input=0, cache_write_input=0))
        self.assertEqual(result['charged_tokens'], 15)
        self.assertEqual(result['charged_cost'], '0.0002')
        self.assertIsNone(result['actual_cost'])
        self.assertEqual(result['charged_cost_basis'], 'reference_estimate')

    def test_summary_retry_continuation_and_preparation_probe_share_ledger(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp)
        self.attach(trial)
        for n, kind in enumerate(('execution', 'summary', 'retry', 'continuation')):
            self.reserve(exp, trial, identifier='q' + str(n), kind=kind)
        self.ev.reserve_request(exp['id'], None, 'probe', 5, '0.1', request_kind='probe')
        report = self.ev.report(exp['id'])
        self.assertEqual(report['ledger']['charged_tokens'], 85)
        self.assertEqual(report['preparation']['requests'], 1)
        self.assertEqual(report['preparation']['charged_tokens'], 5)

    def test_binding_rejects_reuse_and_mixed_session_never_counts(self):
        exp = self.authorize(self.prepare())
        a = self.start(exp, 'A')
        self.bind_usage(a)
        self.ev.record_main_usage(a['id'], 'mixed', 'another', 'another', dict(prompt=999, completion=999), 'hash')
        self.ev.finish_trial(a['id'], 'failed')
        b = self.start(exp, 'B')
        session = 's-' + a['id']
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.ev.bind_session(b['id'], session, session, 'cli', confirmed=True)
        row = self.ev.report(exp['id'])['trials'][0]
        self.assertEqual(row['main_tokens'], 120)
        self.assertFalse(row['coverage']['usage_complete'])
        self.assertIn('unbound_or_mixed_session', row['coverage']['issues'])

    def test_fork_and_missing_related_sessions_reduce_coverage_even_after_idempotent_reimport(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp, 'A')
        self.ev.bind_session(trial['id'], 'root', 'root', 'desktop', True, forked=True, related_sessions=['child'])
        args = (trial['id'], 'event', 'root', 'root', dict(prompt=10, completion=5), 'hash')
        self.ev.record_main_usage(*args)
        self.ev.record_main_usage(*args)
        row = self.ev.report(exp['id'])['trials'][0]
        self.assertEqual(row['main_tokens'], 15)
        for issue in ('fork_inheritance', 'missing_related_session_usage'):
            self.assertIn(issue, row['coverage']['issues'])
        self.assertNotIn('duplicate_usage_event',row['coverage']['issues'])
        self.assertIsNone(row['total_reference_estimate'])

    def test_main_model_limit_is_observation_and_stops_followup(self):
        exp = self.authorize(self.prepare(), main_tokens=100)
        trial = self.start(exp)
        self.bind_usage(trial)
        self.attach(trial)
        with self.assertRaisesRegex(ValueError, 'NOT_AUTHORIZED'):
            self.reserve(exp, trial)
        report = self.ev.report(exp['id'])
        self.assertEqual(report['experiment']['state'], 'needs_review')
        self.assertIn('host', report['billing']['main_control'])

    def test_score_checks_actual_artifact_and_invalidates_changes(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp, 'A')
        self.bind_usage(trial)
        score = self.end_and_score(trial)
        self.assertTrue(score['passed'])
        self.assertTrue(self.ev.report(exp['id'])['trials'][0]['quality_passed'])
        (Path(trial['workspace']) / 'clamp.py').write_text('def clamp(*args): return 0\n', encoding='utf-8')
        report = self.ev.report(exp['id'])
        self.assertFalse(report['trials'][0]['score_valid'])
        self.assertEqual(report['groups']['A']['completion_rate']['numerator'], 0)

    def test_scorer_hash_drift_invalidates_score_and_refuses_cached_success(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp, 'A')
        self.end_and_score(trial)
        with patch('codexrelay.evaluation.file_hash', return_value='new-scorer'):
            self.assertFalse(self.ev.report(exp['id'])['trials'][0]['score_valid'])
            with self.assertRaisesRegex(ValueError, 'SCORER_CHANGED'):
                self.ev.score_trial(trial['id'])

    def test_denominators_keep_not_delegated_failure_and_parent_takeover(self):
        exp = self.authorize(self.prepare(repetitions=2))
        bs = [t for t in exp['trials'] if t['group'] == 'B']
        one = self.ev.start_trial(bs[0]['id'])
        self.ev.finish_trial(one['id'], 'not_delegated', not_delegated_reason='disabled')
        two = self.ev.start_trial(bs[1]['id'])
        self.attach(two)
        self.reserve(exp,two)
        self.ev.settle_request('q1',usage=dict(prompt=1,completion=1,cache_write_input=0))
        self.end_and_score(two, 'completed_by_parent')
        group = self.ev.report(exp['id'])['groups']['B']
        self.assertEqual(group['completion_rate'], {'numerator': 1, 'denominator': 2, 'value': .5})
        self.assertEqual(group['effective_delegation_rate']['numerator'], 0)
        self.assertEqual(group['effective_delegation_rate']['denominator'], 2)
        self.assertEqual(group['parent_takeover_rate']['value'], .5)
        self.assertEqual(group['not_delegated_rate']['value'], .5)
        self.assertIn('not_delegated', group['outcomes'])

    def test_decimal_cache_ranges_write_prices_negative_and_unknown(self):
        price = self.conditions()['main_price']
        value = sum_costs([dict(prompt=1000000, completion=100000, cached_input=None, cache_write_input=0)], price)
        self.assertEqual(value, {'low': '4', 'high': '12'})
        self.assertEqual(subtract_bounds({'low': '4', 'high': '12'}, {'low': '20', 'high': '30'}), {'low': '-26', 'high': '-8'})
        value = sum_costs([dict(prompt=1000000, completion=0, cached_input=200000, cache_write_input=100000)], price)
        self.assertEqual(value, {'low': '8.9', 'high': '8.9'})
        self.assertIsNone(sum_costs([dict(prompt=1, completion=1, cache_write_input=1)], price | {'cache_write': None}))
        self.assertIsNone(sum_costs([dict(prompt=1, completion=1, cache_write_input=None)], price))
        self.assertIsNone(sum_costs([dict(prompt=1, completion=1)], None))

    def test_frozen_independent_scorers_for_all_five_pilot_cases(self):
        exp = self.authorize(self.prepare(cases=builtin_cases()))
        answers = {
            'testing': {'exit_code': 1, 'failing_test': 'test_upper_bound', 'cause': 'exclusive_upper_bound', 'source': 'clamp.py:2', 'fix': 'include_upper_bound'},
            'review': {'findings': [{'path': 'auth.py', 'line': 2, 'issue': 'empty_token_authorized', 'impact': 'unauthenticated_access'}], 'normal_files': ['utils.py']},
            'analysis': {'requests': 3, 'completed': 2, 'timeouts': 1, 'timeout_request': 'r2', 'timeout_source': 'logs/requests.jsonl:2', 'configured_timeout_seconds': 12, 'configuration_source': 'settings.json:1', 'root_cause': 'unknown', 'latency_p50_ms': 40}}
        for original in [t for t in exp['trials'] if t['group'] == 'A']:
            with self.subTest(case=original['case_id']):
                trial = self.ev.start_trial(original['id'])
                kind = trial['case_id'].split('-')[0]
                answer = None
                if kind == 'implementation':
                    (Path(trial['workspace']) / 'clamp.py').write_text(GOOD_CLAMP, encoding='utf-8')
                elif kind == 'documentation':
                    (Path(trial['workspace']) / 'RUNBOOK.md').write_text('Sources: release.txt, operations.txt. Run python -m unittest discover -s tests. Timeout: 12 seconds. Use explicit cancellation; preserve partial changes. API key stays in Credential Manager.', encoding='utf-8')
                else:
                    answer = self.root / (trial['id'] + '.json')
                    answer.write_text(json.dumps(answers[kind]), encoding='utf-8')
                self.ev.finish_trial(trial['id'], 'completed')
                result = self.ev.score_trial(trial['id'], str(answer) if answer else None)
                self.assertTrue(result['passed'], result)

    def test_default_report_excludes_material_bodies_keys_and_subscription_savings_claim(self):
        marker = 'private-project-body-never-export'
        case = builtin_cases()[0]
        case['initial_files']['clamp.py'] += '# ' + marker + '\n'
        exp = self.prepare(cases=[case])
        report = self.ev.report(exp['id'])
        serialized = json.dumps(report)
        self.assertNotIn(marker, serialized)
        self.assertNotIn(case['task'], serialized)
        self.assertIsNone(report['billing']['subscription_savings'])
        self.assertFalse(report['billing']['reference_estimate_is_actual_bill'])
        with self.assertRaisesRegex(ValueError, 'credentials'):
            self.ev.create_experiment('invalid', self.conditions(api_key='secret'))

    def test_finished_refuses_incomplete_trials_or_request_gaps(self):
        exp = self.prepare()
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            self.ev.set_state(exp['id'], 'finished')

    def test_explicit_log_scope_session_duplicates_and_body_exclusion(self):
        exp = self.prepare()
        caps = dict(external_tokens=1000,trial_tokens=500,run_tokens=300,max_trials=15,main_tokens=2000,external_cost=None)
        scope = dict(usage_directory=str(self.root / 'logs'), raw_evidence_directory=None)
        preview = self.ev.authorization_preview(exp['id'], caps, scope)
        self.ev.authorize(exp['id'], caps, scope, preview['preview_sha256'], True)
        trial = self.start(exp, 'A')
        self.ev.bind_session(trial['id'], 'session-1', 'session-1', 'desktop', True)
        directory = self.root / 'logs'
        directory.mkdir()
        log = directory / 'rollout.jsonl'
        records = [dict(type='session_meta',timestamp='2026-10-15T01:00:00Z',payload=dict(id='session-1',cli_version='fixture')),
                   dict(type='event_msg',payload=dict(type='agent_message',message='BODY_NEVER_EXPORT')),
                   dict(type='token_usage_record',timestamp='2026-10-15T01:00:01Z',payload=dict(response_id='response-1',
                        usage=dict(input_tokens=10,output_tokens=5,cached_input_tokens=0,cache_write_input_tokens=0)))]
        log.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        imported = self.ev.import_main_log(trial['id'], str(log), 'session-1')
        self.assertEqual(imported['records_added'], 1)
        self.assertEqual(self.ev.report(exp['id'])['trials'][0]['main_tokens'], 15)
        self.assertNotIn('BODY_NEVER_EXPORT', json.dumps(self.ev.report(exp['id'])))
        self.ev.import_main_log(trial['id'], str(log), 'session-1')
        row = self.ev.report(exp['id'])['trials'][0]
        self.assertNotIn('duplicate_usage_event',row['coverage']['issues'])
        self.assertEqual(row['main_tokens'], 15)
        outside = self.root / 'not-selected.jsonl'
        outside.write_text(log.read_text(encoding='utf-8'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'explicitly chosen'):
            self.ev.import_main_log(trial['id'], str(outside), 'session-1')

    def test_full_cost_report_keeps_negative_difference_and_failure_spend(self):
        exp = self.authorize(self.prepare(), external_tokens=10000, trial_tokens=10000, run_tokens=10000)
        a = self.start(exp, 'A')
        self.bind_usage(a, 'main-a')
        self.end_and_score(a)
        b = self.start(exp, 'B')
        self.bind_usage(b, 'main-b')
        self.attach(b)
        self.reserve(exp, b, tokens=5000)
        self.ev.settle_request('q1', usage=dict(prompt=1000,completion=100,cached_input=0,cache_write_input=0))
        self.end_and_score(b)
        report = self.ev.report(exp['id'])
        pair = next(p for p in report['pairs'] if p['group'] == 'B')
        self.assertEqual(pair['category'], 'cost_increased')
        self.assertEqual(pair['net_reference_estimate'], {'low': '-0.0120', 'high': '-0.0120'})
        self.assertIsNone(report['trials'][1]['actual_external_bill'])
        self.assertEqual(report['groups']['B']['first_run_pass_rate']['numerator'], 1)

    def test_unknown_external_identity_cannot_price_a_complete_comparison(self):
        exp = self.authorize(self.prepare(external_provider_revision='unknown'))
        trial = self.start(exp)
        self.bind_usage(trial)
        self.attach(trial)
        self.reserve(exp, trial)
        self.ev.settle_request('q1', usage=dict(prompt=10,completion=2,cached_input=0,cache_write_input=0))
        self.end_and_score(trial)
        row = next(r for r in self.ev.report(exp['id'])['trials'] if r['trial_id'] == trial['id'])
        self.assertIn('external_provider_revision_unknown', row['coverage']['issues'])
        self.assertFalse(row['coverage']['usage_complete'])
        self.assertIsNone(row['external_reference_estimate'])
        self.assertIsNone(row['total_reference_estimate'])

    def test_unknown_cache_write_is_metered_but_unpriced(self):
        exp = self.authorize(self.prepare())
        trial = self.start(exp)
        self.bind_usage(trial)
        self.attach(trial)
        self.reserve(exp, trial)
        self.ev.settle_request('q1', usage=dict(prompt=1,completion=1,cached_input=None,cache_write_input=None))
        self.end_and_score(trial)
        row = next(r for r in self.ev.report(exp['id'])['trials'] if r['trial_id'] == trial['id'])
        self.assertTrue(row['coverage']['usage_complete'])
        self.assertFalse(row['coverage']['priced'])
        self.assertIsNone(row['total_reference_estimate'])

    def test_log_model_provider_mismatch_and_unknown_never_price_frozen_main_model(self):
        for actual_model, actual_provider, expected_issue in [('different-model','fixture-openai','main_model_mismatch'),
                                                             ('codex-fixture','other-provider','main_provider_mismatch'),
                                                             (None,None,'main_model_unknown')]:
            with self.subTest(issue=expected_issue):
                exp = self.authorize(self.prepare())
                trial = self.start(exp,'A')
                session='s-'+trial['id']
                self.ev.bind_session(trial['id'],session,session,'cli',True)
                caps=exp['authorization']['caps'];scope=dict(usage_directory=str(self.root),raw_evidence_directory=None)
                preview=self.ev.authorization_preview(exp['id'],caps,scope)
                self.ev.authorize(exp['id'],caps,scope,preview['preview_sha256'],True)
                log=self.root/(trial['id']+'.jsonl')
                records=[dict(type='session_meta',timestamp='2026-10-15T01:00:00Z',payload=dict(id=session,model_provider=actual_provider)),
                         dict(type='turn_context',payload=dict(model=actual_model,model_provider=actual_provider)),
                         dict(type='token_usage_record',timestamp='2026-10-15T01:00:01Z',payload=dict(response_id=trial['id'],
                              usage=dict(input_tokens=10,output_tokens=5,cached_input_tokens=0,cache_write_input_tokens=0)))]
                log.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
                self.ev.import_main_log(trial['id'],str(log),session)
                report=self.ev.report(exp['id'])
                row=next(t for t in report['trials'] if t['trial_id']==trial['id'])
                self.assertIn(expected_issue,row['coverage']['issues'])
                self.assertFalse(row['coverage']['usage_complete'])
                self.assertIsNone(row['total_reference_estimate'])

    def test_candidate_early_exit_cannot_bypass_frozen_grader(self):
        for source in ['raise SystemExit(0)\n','import os\nos._exit(0)\n']:
            exp=self.authorize(self.prepare());trial=self.start(exp,'A')
            (Path(trial['workspace'])/'clamp.py').write_text(source,encoding='utf-8')
            self.ev.finish_trial(trial['id'],'completed')
            self.assertFalse(self.ev.score_trial(trial['id'])['passed'])

    def test_fabricated_or_cross_workspace_engine_run_is_rejected(self):
        exp=self.authorize(self.prepare());trial=self.start(exp)
        with self.assertRaisesRegex(ValueError,'not found'):
            self.ev.attach_run(trial['id'],'fabricated-group','fabricated-run','deepseek')
        with self.store.connect() as db:
            payload=dict(id='fake',group_id='g',workspace=str(self.root),backend_id='deepseek')
            db.execute('INSERT INTO tasks VALUES (?,?,0)',('fake',json.dumps(payload)))
            db.execute('INSERT INTO group_runs VALUES (?,?)',('g','fake'))
        with self.assertRaisesRegex(ValueError,'actual engine task'):
            self.ev.attach_run(trial['id'],'g','fake','deepseek')

    def test_authorization_preview_includes_consumption_and_invalidates_on_new_reservation(self):
        exp=self.authorize(self.prepare());trial=self.start(exp);self.attach(trial)
        caps=exp['authorization']['caps'];scope=dict(usage_directory=None,raw_evidence_directory=None)
        preview=self.ev.authorization_preview(exp['id'],caps,scope)
        self.assertEqual(preview['consumption']['started_trials'],1)
        self.assertEqual(preview['consumption']['charged_external_tokens'],0)
        self.reserve(exp,trial)
        with self.assertRaisesRegex(ValueError,'PREVIEW_STALE'):
            self.ev.authorize(exp['id'],caps,scope,preview['preview_sha256'],True)

    def test_unknown_cache_price_is_not_ordinary_input_rate(self):
        price=self.conditions()['main_price']|{'cached_input':None}
        self.assertIsNone(sum_costs([dict(prompt=100,completion=1,cached_input=20,cache_write_input=0)],price))
        self.assertIsNone(sum_costs([dict(prompt=100,completion=1,cached_input=None,cache_write_input=0)],price))
        self.assertEqual(sum_costs([dict(prompt=100,completion=1,cached_input=0,cache_write_input=0)],price),{'low':'0.00102','high':'0.00102'})

    def log_trial(self):
        exp=self.authorize(self.prepare())
        folder=self.root/'logs';folder.mkdir()
        caps=exp['authorization']['caps'];scope=dict(usage_directory=str(folder),raw_evidence_directory=None)
        preview=self.ev.authorization_preview(exp['id'],caps,scope)
        self.ev.authorize(exp['id'],caps,scope,preview['preview_sha256'],True)
        trial=self.start(exp,'A');session='log-'+trial['id']
        self.ev.bind_session(trial['id'],session,session,'cli',True)
        path=folder/'rollout.jsonl'
        header=[dict(type='session_meta',timestamp='2026-10-15T01:00:00Z',payload=dict(id=session,model_provider='fixture-openai')),
                dict(type='turn_context',payload=dict(model='codex-fixture',model_provider='fixture-openai'))]
        return exp,trial,session,path,header

    @staticmethod
    def log_direct(session,stamp,identifier,prompt=10,completion=5):
        return dict(type='token_usage_record',timestamp=f'2026-10-15T01:00:{stamp:02d}Z',
                    payload=dict(response_id=identifier,thread_id=session,usage=dict(input_tokens=prompt,output_tokens=completion,cached_input_tokens=0,cache_write_input_tokens=0)))

    @staticmethod
    def log_cumulative(stamp,prompt,completion,identifier=None):
        payload=dict(type='token_count',info=dict(total_token_usage=dict(input_tokens=prompt,output_tokens=completion,cached_input_tokens=0,cache_write_input_tokens=0)))
        if identifier:payload['response_id']=identifier
        return dict(type='event_msg',timestamp=f'2026-10-15T01:00:{stamp:02d}Z',payload=payload)

    def import_records(self,trial,session,path,records):
        path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
        return self.ev.import_main_log(trial['id'],str(path),session)

    def reported_row(self,exp,trial):
        return next(r for r in self.ev.report(exp['id'])['trials'] if r['trial_id']==trial['id'])

    def test_normal_same_file_reimport_and_append_keep_complete_metering(self):
        exp,trial,session,path,records=self.log_trial()
        records.append(self.log_direct(session,1,'first'))
        self.import_records(trial,session,path,records)
        before=self.reported_row(exp,trial)
        self.assertTrue(before['coverage']['usage_complete'])
        unchanged=self.ev.import_main_log(trial['id'],str(path),session)
        self.assertEqual(unchanged['status'],'unchanged')
        self.assertEqual(unchanged['records_unchanged'],1)
        records.append(self.log_direct(session,2,'second'))
        added=self.import_records(trial,session,path,records)
        self.assertEqual((added['records_added'],added['records_unchanged']),(1,1))
        after=self.reported_row(exp,trial)
        self.assertTrue(after['coverage']['usage_complete'])
        self.assertTrue(after['coverage']['priced'])
        self.assertEqual(after['main_tokens'],30)
        self.assertEqual(after['coverage']['issues'],[])

    def test_response_id_reuse_at_a_different_time_is_still_ambiguous(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_direct(session,1,'reused'),self.log_direct(session,2,'reused')])
        result=self.import_records(trial,session,path,records)
        self.assertIn('conflicting_usage_event',result['issues'])
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],15)
        self.assertFalse(row['coverage']['usage_complete'])

    def test_bound_monotonic_cumulative_logs_are_complete_and_idempotent(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_cumulative(1,10,5),self.log_cumulative(2,20,10)])
        self.import_records(trial,session,path,records)
        self.assertTrue(self.reported_row(exp,trial)['coverage']['usage_complete'])
        records.append(self.log_cumulative(3,30,15))
        imported=self.import_records(trial,session,path,records)
        self.assertEqual((imported['records_added'],imported['records_unchanged']),(1,2))
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],45)
        self.assertTrue(row['coverage']['priced'])

    def test_cumulative_reset_still_keeps_a_gap(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_cumulative(1,10,5),self.log_cumulative(2,5,1),self.log_cumulative(3,8,2)])
        result=self.import_records(trial,session,path,records)
        self.assertIn('log_resets',result['issues'])
        self.assertFalse(self.reported_row(exp,trial)['coverage']['usage_complete'])

    def test_both_log_orders_prefer_direct_for_the_proven_cumulative_window(self):
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                # New isolated fixture per order; both sequences represent the
                # same request inside the same cumulative coverage window.
                helper=EvaluationTests();helper.setUp()
                try:
                    exp,trial,session,path,records=helper.log_trial()
                    pair=[helper.log_direct(session,1,'request-1'),helper.log_cumulative(2,10,5)]
                    records.extend(reversed(pair) if reverse else pair)
                    helper.import_records(trial,session,path,records)
                    row=helper.reported_row(exp,trial)
                    self.assertEqual(row['main_tokens'],15)
                    self.assertTrue(row['coverage']['usage_complete'])
                    self.assertEqual(row['main_usage_streams'][0]['strategy'],'direct_matches_cumulative_window')
                finally:helper.doCleanups()

    def test_direct_append_resolves_covered_cumulative_without_double_counting(self):
        exp,trial,session,path,records=self.log_trial()
        records.append(self.log_cumulative(2,10,5))
        self.import_records(trial,session,path,records)
        records.append(self.log_direct(session,1,'request-1'))
        result=self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],15)
        self.assertTrue(row['coverage']['usage_complete'])
        self.assertEqual(self.ev.describe(exp['id'])['authorization']['version'],2)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM evaluation_usage_events WHERE trial_id=?',(trial['id'],)).fetchone()[0],2)
        self.assertEqual(result['issues'],[])

    def test_equal_counters_outside_cumulative_window_do_not_prove_overlap(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_cumulative(1,10,5),self.log_direct(session,2,'later-request')])
        result=self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertIn('usage_stream_coverage_unresolved',result['issues'])
        self.assertFalse(row['coverage']['usage_complete'])
        self.assertEqual(row['main_tokens_basis'],'reported_lower_bound')
        self.assertEqual(row['main_usage_streams'][0]['cumulative_tokens'],15)
        self.assertEqual(row['main_usage_streams'][0]['direct_tokens'],15)

    def test_stable_distinct_response_ids_are_not_merged_as_the_same_request(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_cumulative(1,10,5,'earlier-request'),self.log_direct(session,2,'later-request')])
        self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],30)
        self.assertTrue(row['coverage']['usage_complete'])

    def test_mixed_unbound_session_remains_separate_and_unattributable(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_cumulative(2,10,5),self.log_direct('other-chat',1,'wrong-session')])
        self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],15)
        self.assertIn('unbound_or_mixed_session',row['coverage']['issues'])
        self.assertFalse(row['coverage']['usage_complete'])

    def test_attached_run_denied_before_admission_can_be_recorded_as_not_delegated(self):
        exp=self.authorize(self.prepare());trial=self.start(exp);self.attach(trial)
        self.bind_usage(trial)
        finished=self.ev.finish_trial(trial['id'],'not_delegated',not_delegated_reason='policy')
        self.assertEqual(len(finished['runs']),1)
        row=self.reported_row(exp,trial)
        self.assertFalse(row['delegated'])
        self.assertEqual(row['not_delegated_reason'],'policy')
        self.assertTrue(row['coverage']['usage_complete'])
        self.assertTrue(row['coverage']['priced'])
        self.assertEqual(row['external_reference_estimate'],{'low':'0','high':'0'})

    def test_admitted_request_still_prevents_a_false_no_delegation_outcome(self):
        exp=self.authorize(self.prepare());trial=self.start(exp);self.attach(trial);self.reserve(exp,trial)
        self.ev.settle_request('q1',usage=dict(prompt=10,completion=2,cached_input=0,cache_write_input=0))
        with self.assertRaisesRegex(ValueError,'provider request was admitted'):
            self.ev.finish_trial(trial['id'],'not_delegated',not_delegated_reason='policy')

    def test_matching_total_with_contradictory_cache_counters_is_not_complete_overlap(self):
        exp,trial,session,path,records=self.log_trial()
        direct=self.log_direct(session,1,'request-1')
        cumulative=self.log_cumulative(2,10,5)
        cumulative['payload']['info']['total_token_usage']['cached_input_tokens']=5
        records.extend([direct,cumulative])
        imported=self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertIn('conflicting_usage_streams',imported['issues'])
        self.assertFalse(row['coverage']['usage_complete'])
        self.assertEqual(row['main_tokens'],15)
        self.assertEqual(row['main_usage_streams'][0]['direct_cache_counters']['cached_input'],0)
        self.assertEqual(row['main_usage_streams'][0]['cumulative_cache_counters']['cached_input'],5)

    def test_related_sessions_can_be_explicitly_added_without_replacing_the_primary_binding(self):
        from codexrelay.evaluation import dispatch,main
        exp,trial,session,path,records=self.log_trial()
        records.append(self.log_direct(session,1,'parent-request'))
        self.import_records(trial,session,path,records)
        before=self.ev.describe(exp['id'])['trials'][0]['binding']
        with self.assertRaisesRegex(ValueError,'explicit confirmation'):
            self.ev.add_related_sessions(trial['id'],['child'])
        result=dispatch(self.store,'add_related_sessions',dict(trial_id=trial['id'],related_sessions=['child'],confirmed=True))
        self.assertEqual(result['added_sessions'],['child'])
        for key in ('session_id','root_session_id','client_kind','confirmed_at','forked'):
            self.assertEqual(result['binding'][key],before[key])
        child_path=path.parent/'child.jsonl'
        child_records=[dict(type='session_meta',timestamp='2026-10-15T01:00:02Z',payload=dict(id='child',model_provider='fixture-openai')),
                       dict(type='turn_context',payload=dict(model='codex-fixture',model_provider='fixture-openai')),
                       self.log_direct('child',3,'child-request',prompt=7,completion=2)]
        self.import_records(trial,'child',child_path,child_records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],24)
        self.assertTrue(row['coverage']['usage_complete'])
        # The standalone CLI exposes the same explicit extension contract.
        params=self.root/'add-related.json';params.write_text(json.dumps(dict(trial_id=trial['id'],related_sessions=['cli-child'],confirmed=True)),encoding='utf-8')
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            main(['--state',str(self.store.directory),'add-related','--params',str(params)])
        self.assertEqual(json.loads(output.getvalue())['added_sessions'],['cli-child'])

    def test_related_extension_keeps_cross_trial_ownership_and_does_not_wash_old_mixed_evidence(self):
        exp,trial,session,path,records=self.log_trial()
        records.append(self.log_direct('unbound-child',1,'mixed-request'))
        self.import_records(trial,session,path,records)
        self.ev.add_related_sessions(trial['id'],['unbound-child'],True)
        self.assertIn('unbound_or_mixed_session',self.reported_row(exp,trial)['coverage']['issues'])
        other_exp=self.authorize(self.prepare());other=self.start(other_exp,'A')
        self.ev.bind_session(other['id'],'another-trial','another-trial','cli',True)
        with self.assertRaisesRegex(ValueError,'already bound'):
            self.ev.add_related_sessions(trial['id'],['another-trial'],True)
        self.assertEqual(self.ev.add_related_sessions(trial['id'],['unbound-child'],True)['added_sessions'],[])

    def test_user_selected_normal_log_larger_than_16_mib_imports_with_bounded_reads(self):
        exp,trial,session,path,records=self.log_trial()
        filler=(json.dumps(dict(type='event_msg',payload=dict(type='agent_message',message='x'*4096)))+'\n').encode()
        with path.open('wb') as stream:
            for record in records:stream.write((json.dumps(record)+'\n').encode())
            for _ in range(4350):stream.write(filler)
            stream.write((json.dumps(self.log_direct(session,1,'long-log-request'))+'\n').encode())
        self.assertGreater(path.stat().st_size,16*1024*1024)
        original_open=Path.open
        reads=[]
        class BoundedReader:
            def __init__(reader,stream):reader.stream=stream
            def __enter__(reader):reader.stream.__enter__();return reader
            def __exit__(reader,*args):return reader.stream.__exit__(*args)
            def read(reader,size=-1):
                self.assertGreaterEqual(size,0);self.assertLessEqual(size,1024*1024);reads.append(size)
                return reader.stream.read(size)
            def readline(reader,size=-1):
                self.assertGreaterEqual(size,0);self.assertLessEqual(size,1024*1024+1);reads.append(size)
                return reader.stream.readline(size)
        def checked_open(value,*args,**kwargs):
            opened=original_open(value,*args,**kwargs)
            return BoundedReader(opened) if value==path and args and args[0]=='rb' else opened
        with patch.object(Path,'open',checked_open):
            result=self.ev.import_main_log(trial['id'],str(path),session)
        self.assertTrue(reads)
        self.assertEqual(result['records_added'],1)
        self.assertEqual(result['issues'],[])
        self.assertEqual(self.reported_row(exp,trial)['main_tokens'],15)
        self.assertTrue(self.reported_row(exp,trial)['coverage']['usage_complete'])

    def test_oversized_line_is_skipped_in_chunks_and_later_usage_is_not_lost(self):
        exp,trial,session,path,records=self.log_trial()
        with path.open('wb') as stream:
            for record in records:stream.write((json.dumps(record)+'\n').encode())
            stream.write(b'{"type":"event_msg","payload":{"message":"')
            for _ in range(40):stream.write(b'x'*65536)
            stream.write(b'"}}\n')
            stream.write(json.dumps(self.log_direct(session,1,'after-long-line')).encode())
        result=self.ev.import_main_log(trial['id'],str(path),session)
        self.assertEqual(result['records_added'],1)
        self.assertIn('oversized_log_line',result['issues'])
        self.assertFalse(result['deferred_tail'])
        self.assertEqual(self.reported_row(exp,trial)['main_tokens'],15)

    def test_append_during_import_and_partial_tail_completion_do_not_permanently_reduce_coverage(self):
        from codexrelay import codex_usage
        exp,trial,session,path,records=self.log_trial()
        tail=json.dumps(self.log_direct(session,2,'second-request')).encode()
        initial=b''.join((json.dumps(record)+'\n').encode() for record in records+[self.log_direct(session,1,'first-request')])+tail[:40]
        path.write_bytes(initial)
        original=codex_usage.event_record;appended=[]
        def append_while_reading(event,*args,**kwargs):
            if event.get('type')=='token_usage_record' and not appended:
                with path.open('ab') as stream:stream.write(tail[40:]+b'\n')
                appended.append(True)
            return original(event,*args,**kwargs)
        with patch.object(codex_usage,'event_record',append_while_reading):
            first=self.ev.import_main_log(trial['id'],str(path),session)
        self.assertEqual(first['source_bytes'],len(initial))
        self.assertTrue(first['deferred_tail'])
        self.assertIn('log_tail_pending',first['issues'])
        self.assertNotIn('invalid_log_line',first['issues'])
        self.assertNotIn('log_changed_during_import',first['issues'])
        self.assertFalse(self.reported_row(exp,trial)['coverage']['usage_complete'])
        second=self.ev.import_main_log(trial['id'],str(path),session)
        self.assertEqual((second['records_added'],second['records_unchanged']),(1,1))
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],30)
        self.assertTrue(row['coverage']['usage_complete'])
        self.assertEqual(row['coverage']['issues'],[])

    def test_mutating_an_already_read_prefix_still_creates_a_real_gap(self):
        from codexrelay import codex_usage
        exp,trial,session,path,records=self.log_trial()
        path.write_text(''.join(json.dumps(r)+'\n' for r in records+[self.log_direct(session,1,'request')]),encoding='utf-8')
        original=codex_usage.event_record;mutated=[]
        def mutate_prefix(event,*args,**kwargs):
            if event.get('type')=='token_usage_record' and not mutated:
                with path.open('r+b') as stream:stream.write(b' ')
                mutated.append(True)
            return original(event,*args,**kwargs)
        with patch.object(codex_usage,'event_record',mutate_prefix):
            result=self.ev.import_main_log(trial['id'],str(path),session)
        self.assertIn('log_changed_during_import',result['issues'])
        self.assertFalse(self.reported_row(exp,trial)['coverage']['usage_complete'])

    def test_model_switch_does_not_double_add_the_session_cumulative_prefix(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_direct(session,1,'model-a-request'),
                        dict(type='turn_context',payload=dict(model='model-b',model_provider='fixture-openai')),
                        self.log_cumulative(2,20,10)])
        result=self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],30)
        self.assertIn('cumulative_model_or_provider_allocation_unknown',result['issues'])
        self.assertFalse(row['coverage']['usage_complete'])
        with self.store.connect() as db:
            self.assertEqual(self.ev._consumption(db,exp['id'])['main_reported_tokens'],30)

    def test_same_timestamp_cumulative_checkpoints_do_not_collapse_equal_deltas(self):
        for identifier in (None,'checkpoint-response'):
            with self.subTest(response_id=identifier):
                helper=EvaluationTests();helper.setUp()
                try:
                    exp,trial,session,path,records=helper.log_trial()
                    records.extend([helper.log_cumulative(1,10,5,identifier),helper.log_cumulative(1,20,10,identifier)])
                    result=helper.import_records(trial,session,path,records)
                    self.assertEqual(result['records_added'],2)
                    self.assertEqual(helper.reported_row(exp,trial)['main_tokens'],30)
                    self.assertTrue(helper.reported_row(exp,trial)['coverage']['usage_complete'])
                    replay=helper.ev.import_main_log(trial['id'],str(path),session)
                    self.assertEqual((replay['records_added'],replay['records_unchanged']),(0,2))
                    if identifier:
                        records.append(helper.log_direct(session,2,identifier,prompt=20,completion=10))
                        helper.import_records(trial,session,path,records)
                        row=helper.reported_row(exp,trial)
                        self.assertEqual(row['main_tokens'],30)
                        self.assertTrue(row['coverage']['usage_complete'])
                finally:helper.doCleanups()

    def test_cumulative_response_label_does_not_make_its_session_prefix_disjoint(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_direct(session,1,'first-response'),self.log_cumulative(2,20,10,'second-response')])
        result=self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],30)
        self.assertIn('usage_stream_coverage_unresolved',result['issues'])
        self.assertFalse(row['coverage']['usage_complete'])
        with self.store.connect() as db:
            self.assertEqual(self.ev._consumption(db,exp['id'])['main_reported_tokens'],30)

    def test_same_response_id_does_not_turn_a_cumulative_prefix_into_per_request_usage(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_direct(session,1,'response'),self.log_cumulative(2,10,5,'response'),self.log_cumulative(2,20,10,'response')])
        result=self.import_records(trial,session,path,records)
        row=self.reported_row(exp,trial)
        self.assertEqual(row['main_tokens'],30)
        self.assertIn('conflicting_usage_streams',result['issues'])
        self.assertFalse(row['coverage']['usage_complete'])

    def test_root_identity_is_owned_across_trials_and_cannot_be_added_as_a_foreign_child(self):
        exp=self.authorize(self.prepare());trial=self.start(exp,'A')
        self.ev.bind_session(trial['id'],'first-primary','owned-root','cli',True)
        self.ev.record_main_usage(trial['id'],'root-counter','first-primary','owned-root',dict(prompt=10,completion=5,cached_input=0,cache_write_input=0),
            'source',model='codex-fixture',provider='fixture-openai')
        other_exp=self.authorize(self.prepare());other=self.start(other_exp,'A')
        with self.assertRaisesRegex(ValueError,'already bound'):
            self.ev.bind_session(other['id'],'second-primary','owned-root','cli',True)
        with self.assertRaisesRegex(ValueError,'already bound'):
            self.ev.bind_session(other['id'],'owned-root','second-root','cli',True)
        self.ev.bind_session(other['id'],'second-primary','second-root','cli',True)
        with self.assertRaisesRegex(ValueError,'already bound'):
            self.ev.add_related_sessions(other['id'],['owned-root'],True)
        # Historical ownership collisions are visible rather than silently
        # acquiring a complete attribution claim after the new guard ships.
        with self.store.connect() as db:
            payload=json.loads(db.execute('SELECT payload FROM evaluation_trials WHERE id=?',(other['id'],)).fetchone()[0])
            payload['binding']['root_session_id']='owned-root'
            db.execute('UPDATE evaluation_trials SET payload=? WHERE id=?',(json.dumps(payload),other['id']))
        row=self.reported_row(exp,trial)
        self.assertIn('shared_client_session_across_trials',row['coverage']['issues'])
        self.assertFalse(row['coverage']['attributable'])
        self.assertFalse(row['coverage']['usage_complete'])

    def test_legacy_cumulative_identity_is_retained_without_unsafe_reimport_or_complete_claim(self):
        exp,trial,session,path,records=self.log_trial()
        records.extend([self.log_cumulative(1,10,5),self.log_cumulative(2,20,10)])
        self.import_records(trial,session,path,records)
        with self.store.connect() as db:
            rows=db.execute('SELECT id,payload FROM evaluation_usage_events WHERE trial_id=?',(trial['id'],)).fetchall()
            for row in rows:
                payload=json.loads(row[1]);payload.pop('identity_version',None)
                db.execute('UPDATE evaluation_usage_events SET payload=? WHERE id=?',(json.dumps(payload),row[0]))
        with self.assertRaisesRegex(ValueError,'LEGACY_CUMULATIVE_IDENTITY'):
            self.ev.import_main_log(trial['id'],str(path),session)
        row=self.reported_row(exp,trial)
        self.assertIn('legacy_cumulative_identity_needs_review',row['coverage']['issues'])
        self.assertFalse(row['coverage']['usage_complete'])

    def test_same_actual_model_does_not_allow_wrong_frozen_slot(self):
        exp=self.authorize(self.prepare());trial=self.start(exp);self.attach(trial)
        with self.store.connect() as db:
            task=json.loads(db.execute('SELECT payload FROM tasks WHERE id=?',('r1',)).fetchone()[0])
            task['model_slot']='pro'
            db.execute('UPDATE tasks SET payload=? WHERE id=?',(json.dumps(task),'r1'))
        with self.assertRaisesRegex(ValueError,'frozen external_slot'):
            self.ev.attach_run(trial['id'],'g1','r1','deepseek',model='worker-fixture')

    def test_hard_killed_reservation_blocks_until_explicit_grant_and_keeps_gap(self):
        exp=self.authorize(self.prepare());trial=self.start(exp);self.attach(trial)
        original=self.reserve(exp,trial,tokens=30,cost='2')
        self.assertEqual(original['authorization_version'],1)
        self.assertGreater(original['owner_pid'],0)
        with patch('codexrelay.evaluation.process_identity',return_value=None):
            with self.assertRaisesRegex(ValueError,'STALE_REQUEST_RESERVATION'):
                self.reserve(exp,trial,identifier='after-kill',tokens=5)
        stopped=self.ev.report(exp['id'])
        self.assertEqual(stopped['experiment']['state'],'needs_review')
        self.assertEqual(stopped['ledger']['charged_tokens'],30)
        self.assertEqual(stopped['ledger']['unsettled'],1)
        scope=dict(usage_directory=None,raw_evidence_directory=None);caps=exp['authorization']['caps']
        preview=self.ev.authorization_preview(exp['id'],caps,scope)
        self.assertEqual(preview['consumption']['usage_gaps'],1)
        self.ev.authorize(exp['id'],caps,scope,preview['preview_sha256'],True)
        self.reserve(exp,trial,identifier='explicit-new-grant',tokens=5)
        self.ev.settle_request('explicit-new-grant',usage=dict(prompt=1,completion=1,cached_input=0,cache_write_input=0))
        report=self.ev.report(exp['id'])
        self.assertEqual(report['ledger']['charged_tokens'],32)
        self.assertEqual(report['ledger']['usage_gaps'],1)
        self.assertEqual(report['ledger']['unsettled'],1)
        self.assertEqual(report['experiment']['authorization']['version'],2)
        self.assertEqual(report['experiment']['state'],'authorized')

    def test_terminal_run_with_unsettled_reservation_requires_review(self):
        exp=self.authorize(self.prepare());trial=self.start(exp);self.attach(trial)
        self.reserve(exp,trial)
        with self.store.connect() as db:
            task=json.loads(db.execute('SELECT payload FROM tasks WHERE id=?',('r1',)).fetchone()[0])
            task['status']='interrupted'
            db.execute('UPDATE tasks SET payload=? WHERE id=?',(json.dumps(task),'r1'))
        with self.assertRaisesRegex(ValueError,'STALE_REQUEST_RESERVATION'):
            self.reserve(exp,trial,identifier='after-terminal')
        self.assertEqual(self.ev.report(exp['id'])['experiment']['state'],'needs_review')


if __name__ == '__main__':
    unittest.main()
