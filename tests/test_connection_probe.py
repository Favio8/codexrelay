import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay import connection_probe
from codexrelay.service import Service
from codexrelay.state import Store
from test_control import MemoryVault


class ConnectionProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.requests = []
        self.mode = 'normal'
        self.report_usage = False
        self.cache_write_usage = 0
        self.response_model = None
        self.response_gate = None
        self.request_seen = threading.Event()
        self.disconnected = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(body)
                owner.request_seen.set()
                if owner.response_gate is not None:
                    owner.response_gate.wait(10)
                if owner.mode == 'stall':
                    self.connection.settimeout(.1)
                    until = time.monotonic() + 20
                    while time.monotonic() < until:
                        try:
                            if not self.connection.recv(1):
                                owner.disconnected.set()
                                return
                        except TimeoutError:
                            continue
                        except OSError:
                            owner.disconnected.set()
                            return
                    return
                if owner.mode in {'auth', 'rate'}:
                    self.send_response(401 if owner.mode == 'auth' else 429)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': {'message': 'fixture-secret echoed by provider'}}).encode())
                    return
                if owner.mode == 'no-tools' or not body.get('tools') or any(m['role'] == 'tool' for m in body['messages']):
                    results = [m['content'] for m in body['messages'] if m['role'] == 'tool']
                    marker = re.search(r'\b[a-f0-9]{32}\b', results[-1].split('\n', 1)[-1]) if results else None
                    message = {'role': 'assistant', 'content': marker.group() if marker else 'CONNECTED'}
                else:
                    tool = body['tools'][0]['function']['name']
                    message = {'role': 'assistant', 'content': None,
                               'tool_calls': [{'id': 'probe', 'type': 'function',
                                               'function': {'name': tool, 'arguments': json.dumps({'path': 'probe.txt'}) if tool == 'Read' else '{}'}}]}
                response = {'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                            'model': owner.response_model or body['model'], 'choices': [{'index': 0, 'message': message,
                            'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]}
                if owner.report_usage:
                    response['usage'] = {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25,
                                         'prompt_tokens_details': {'cached_tokens': 10}, 'cache_creation_input_tokens': owner.cache_write_usage}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                encoded = json.dumps(response).encode()
                if owner.mode == 'trickle':
                    for byte in encoded:
                        try:
                            self.wfile.write(bytes([byte])); self.wfile.flush()
                        except OSError:
                            owner.disconnected.set(); return
                        time.sleep(.15)
                else:
                    self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.store = Store(Path(self.temp.name) / 'state')
        self.service = Service(self.store, MemoryVault())
        self.provider = self.service.save_provider(name='Probe fixture', kind='custom',
            base_url=f'http://127.0.0.1:{self.server.server_port}/v1',
            flash_model='fast', pro_model='strong', api_key='fixture-secret')

    def test_quick_uses_one_real_request_and_does_not_claim_tools(self):
        result = self.service.test_backend('deepseek', self.provider['id'], 'pro', 'quick')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]['model'], 'strong')
        self.assertNotIn('tools', self.requests[0])
        self.assertLessEqual(self.requests[0]['max_tokens'], 64)
        self.assertFalse(result['tool_roundtrip'])
        self.assertFalse(result['backend_verified'])
        self.assertGreater(result['elapsed_ms'], 0)
        self.assertNotIn('last_test', self.store.provider(self.provider['id']))
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_full_roundtrip_is_separate_and_quick_preserves_its_evidence(self):
        full = self.service.test_provider(self.provider['id'])
        self.assertTrue(full['tool_roundtrip'])
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(any(message['role'] == 'tool' for message in self.requests[1]['messages']))
        saved = self.store.provider(self.provider['id'])['last_test']
        self.service.test_provider(self.provider['id'], verification='quick')
        latest = self.store.provider(self.provider['id'])
        self.assertEqual(latest['last_test'], saved)
        self.assertEqual(latest['last_connection_test']['verification'], 'quick')

    def test_reachable_model_that_cannot_call_tools_fails_full_validation(self):
        self.mode = 'no-tools'
        self.assertTrue(self.service.test_provider(self.provider['id'], verification='quick')['ok'])
        with self.assertRaisesRegex(ValueError, '结构化工具调用'):
            self.service.test_provider(self.provider['id'], verification='full')
        self.assertNotIn('last_test', self.store.provider(self.provider['id']))

    def test_auth_failure_is_actionable_and_redacts_provider_response(self):
        self.mode = 'auth'
        with self.assertRaisesRegex(ValueError, '鉴权失败') as error:
            self.service.test_provider(self.provider['id'], verification='quick')
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertEqual(len(self.requests), 1, 'paid probes must not retry')

    def test_wall_timeout_kills_worker_closes_socket_and_allows_retry(self):
        self.mode = 'stall'
        processes = []
        original = connection_probe.subprocess.Popen
        def track(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        started = time.monotonic()
        # Exercise the actual 12s production deadline. A 2s test deadline could
        # kill a cold SDK import before it opens a socket on a busy Windows host.
        with patch.object(connection_probe.subprocess, 'Popen', side_effect=track):
            with self.assertRaisesRegex(ValueError, '12 秒内完成'):
                self.service.test_provider(self.provider['id'], verification='quick')
        self.assertGreaterEqual(time.monotonic() - started, 11.5)
        self.assertLess(time.monotonic() - started, 14)
        self.assertEqual(len(self.requests), 1, 'the deadline must interrupt an established request')
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(self.disconnected.wait(2), 'timeout must close the actual HTTP socket')
        self.mode = 'normal'
        self.assertTrue(self.service.test_provider(self.provider['id'], verification='quick')['ok'])

    def test_invalid_probe_rejected_before_network(self):
        for params in [{'slot': 'invalid'}, {'verification': 'invalid'}]:
            with self.assertRaises(ValueError):
                self.service.test_provider(self.provider['id'], **params)
        self.assertEqual(self.requests, [])

    def test_dispatch_preserves_all_slot_and_verification_combinations(self):
        for method in ('test_provider', 'test_backend'):
            for slot, model in [('flash', 'fast'), ('pro', 'strong')]:
                for verification, expected in [('quick', 1), ('full', 2)]:
                    before = len(self.requests)
                    params = dict(slot=slot, verification=verification)
                    params.update({'id': self.provider['id']} if method == 'test_provider' else
                                  {'provider_id': self.provider['id'], 'backend': 'deepseek'})
                    result = self.service.dispatch(method, params)
                    observed = self.requests[before:]
                    self.assertEqual(len(observed), expected)
                    self.assertTrue(all(body['model'] == model for body in observed))
                    self.assertEqual(result['slot'], slot)
                    self.assertEqual(result['verification'], verification)
                    self.assertFalse(result['backend_verified'])

    def test_real_rpc_preserves_selected_pro_quick_and_full(self):
        environment = {**os.environ, 'CODEXRELAY_HOME': str(self.store.directory)}
        bootstrap = f"import sys;sys.path.insert(0,{str(ROOT / 'engine')!r});from codexrelay.__main__ import main;main()"
        for method in ('test_provider', 'test_backend'):
            for verification, expected in [('quick', 1), ('full', 2)]:
                before = len(self.requests)
                params = dict(slot='pro', verification=verification)
                params.update({'id': self.provider['id']} if method == 'test_provider' else
                              {'provider_id': self.provider['id'], 'backend': 'deepseek'})
                result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap, 'rpc'],
                    input=json.dumps(dict(method=method, params=params)), capture_output=True, text=True, encoding='utf-8',
                    env=environment, timeout=55)
                response = json.loads(result.stdout)
                self.assertTrue(response['ok'], response)
                self.assertEqual(len(self.requests) - before, expected)
                self.assertEqual(self.requests[-1]['model'], 'strong')
                self.assertEqual(response['data']['verification'], verification)

    def test_real_backend_requires_actual_read_on_both_schedulers(self):
        from codexrelay.backends import catalog
        self.service.save_provider(**{**self.store.provider(self.provider['id']),
                                      'reasoning_effort': 'low', 'pro_reasoning_effort': 'high'})
        for backend in catalog():
            self.assertTrue(backend['installed'], 'bundled test runtime must include both backends')
            before = len(self.requests)
            result = self.service.dispatch('test_backend', dict(backend=backend['id'],
                provider_id=self.provider['id'], slot='pro', verification='backend'))
            self.assertTrue(result['backend_verified'])
            self.assertEqual(result['verification'], 'backend')
            self.assertEqual(result['backend_version'], backend['version'])
            self.assertEqual(result['reasoning_mode'], 'high')
            self.assertEqual(self.requests[-1]['reasoning_effort'], 'high')
            self.assertEqual(result['request_count'], 2)
            self.assertEqual(len(self.requests) - before, 2)
            self.assertEqual(result['tool_evidence'][0]['name'], 'Read')
            self.assertTrue(result['tool_evidence'][0]['marker_matched'])
            self.assertEqual(self.store.snapshot()['tasks'], [])
        self.mode = 'no-tools'
        with self.assertRaisesRegex(ValueError, '实际文件读取'):
            self.service.test_backend('deepseek', self.provider['id'], verification='backend')

    def test_slot_and_level_records_do_not_overwrite_each_other(self):
        for slot in ('flash', 'pro'):
            self.service.test_provider(self.provider['id'], slot, 'full')
            self.service.test_provider(self.provider['id'], slot, 'quick')
        current = self.store.provider(self.provider['id'])
        evidence = list(current['probe_evidence'].values())
        self.assertEqual({(r['slot'], r['verification']) for r in evidence},
                         {('flash', 'quick'), ('flash', 'full'), ('pro', 'quick'), ('pro', 'full')})
        self.assertEqual(len(self.service.dispatch('probe_records', {})), 4)

    def test_revised_provider_retains_old_history_without_current_badge(self):
        self.response_gate = threading.Event()
        results, failures = [], []
        def run():
            try: results.append(self.service.test_provider(self.provider['id'], 'pro', 'full'))
            except Exception as error: failures.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(self.request_seen.wait(8))
        current = self.store.provider(self.provider['id'])
        self.service.save_provider(**{**current, 'api_key': 'replacement-secret'})
        self.response_gate.set(); thread.join(15)
        self.assertFalse(thread.is_alive()); self.assertEqual(failures, [])
        self.assertFalse(results[0]['current_revision'])
        self.assertNotIn('probe_evidence', self.store.provider(self.provider['id']))
        self.assertNotIn('replacement-secret', json.dumps(self.service.dispatch('probe_records', {})))

    def test_probe_usage_is_separate_frozen_and_missing_is_unknown(self):
        from codexrelay.usage import save_price
        provider = self.store.provider(self.provider['id'])
        save_price(self.store, {'input': '2', 'cached_input': '1', 'output': '3'},
                   provider_id=provider['id'], provider_revision=provider['revision'], model='strong')
        self.report_usage = True
        self.service.test_provider(provider['id'], 'pro', 'full')
        save_price(self.store, {'input': '100', 'cached_input': '50', 'output': '100'},
                   provider_id=provider['id'], provider_revision=provider['revision'], model='strong')
        self.report_usage = False
        self.service.test_provider(provider['id'], 'pro', 'quick')
        usage = self.service.dispatch('probe_usage_today', {})
        self.assertEqual(usage['requests'], 3)
        self.assertEqual(usage['reported_tokens'], 50)
        self.assertEqual(usage['missing_usage'], 1)
        from decimal import Decimal
        self.assertEqual(Decimal(usage['cost']['low']), Decimal('0.00009'))
        self.assertEqual(Decimal(usage['cost']['high']), Decimal('0.00009'))
        self.assertFalse(usage['cost_complete'])
        self.assertIsNone(usage['actual_bill'])
        self.assertEqual(self.service.dispatch('usage_today', {})['counts'], {})
        self.assertEqual(self.service.dispatch('probe_usage_today', {})['requests'], 3)

    def test_same_probe_id_is_rejected_before_paid_request(self):
        params = dict(id=self.provider['id'], slot='pro', verification='quick', probe_id='same-id')
        self.service.dispatch('test_provider', params)
        with self.assertRaisesRegex(ValueError, '拒绝重放'):
            self.service.dispatch('test_provider', params)
        self.assertEqual(len(self.requests), 1)

    def test_concurrent_probe_id_is_claimed_before_network(self):
        self.response_gate = threading.Event()
        results, failures = [], []
        def run():
            try: results.append(self.service.test_provider(self.provider['id'], verification='quick', probe_id='shared-id'))
            except Exception as error: failures.append(error)
        thread = threading.Thread(target=run); thread.start()
        self.assertTrue(self.request_seen.wait(8))
        with self.assertRaisesRegex(ValueError, '拒绝重放'):
            self.service.test_provider(self.provider['id'], verification='quick', probe_id='shared-id')
        self.assertEqual(self.service.dispatch('probe_usage_today', {})['unknown_requests'], 1)
        self.response_gate.set(); thread.join(10)
        self.assertFalse(thread.is_alive()); self.assertEqual(failures, [])
        self.assertEqual(len(results), 1); self.assertEqual(len(self.requests), 1)

    def test_backend_read_permission_rejected_before_network(self):
        self.service.save_provider(**{**self.store.provider(self.provider['id']), 'allowed_tools': ['Glob']})
        with self.assertRaisesRegex(ValueError, '不允许 Read'):
            self.service.test_backend('deepseek', self.provider['id'], verification='backend')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.store.snapshot()['tasks'], [])
        self.assertEqual(self.service.dispatch('probe_records', {})[0]['request_count'], 0)

    def test_cancellation_closes_inflight_socket_and_persists_gap(self):
        self.mode = 'stall'
        failures = []
        def run():
            try: self.service.test_provider(self.provider['id'], verification='quick', probe_id='cancel-me')
            except Exception as error: failures.append(error)
        thread = threading.Thread(target=run); thread.start()
        self.assertTrue(self.request_seen.wait(8))
        self.service.dispatch('cancel_probe', {'probe_id': 'cancel-me'})
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.disconnected.wait(2))
        self.assertIn('取消', str(failures[0]))
        record = self.service.dispatch('probe_records', {})[0]
        self.assertEqual(record['request_count'], 1)
        self.assertFalse(record['usage_events'][0]['available'])
        self.assertTrue(record['cleanup_complete'])

    def test_trickling_body_obeys_wall_deadline_without_replay(self):
        self.mode = 'trickle'
        with patch.object(connection_probe, 'QUICK_TEST_SECONDS', 5):
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, '5 秒内完成'):
                self.service.test_provider(self.provider['id'], verification='quick')
        self.assertLess(time.monotonic() - started, 7)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.disconnected.wait(2))

    def test_rate_limit_is_not_replayed_and_does_not_expose_error_body(self):
        self.mode = 'rate'
        with self.assertRaisesRegex(ValueError, '429') as error:
            self.service.test_provider(self.provider['id'], verification='quick')
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertEqual(len(self.requests), 1)

    def experiment(self, authorize=False, **conditions):
        from codexrelay.evaluation import Evaluation
        evaluation = Evaluation(self.store)
        price = dict(input='10', output='20', cached_input='2', cache_write='5',
                     currency='CNY', source='local fixture', observed_at='2026-10-15')
        value = evaluation.create_experiment('local probe gate fixture', dict(main_model='host-fixture',
            external_model='strong', external_provider_revision=self.store.provider(self.provider['id'])['revision'],
            external_slot='pro', external_reasoning='high',
            runtime_version='local fixture', policy_version='test-v1', environment='local HTTP fixture',
            main_price=price, external_price=price, currency='CNY') | conditions)
        experiment = evaluation.prepare_experiment(value['id'], str(Path(self.temp.name) / 'experiments'))
        if authorize:
            caps = dict(external_tokens=100000, trial_tokens=50000, run_tokens=30000, max_trials=3,
                        main_tokens=20000, external_cost='100')
            scope = dict(usage_directory=None, raw_evidence_directory=None)
            preview = evaluation.authorization_preview(value['id'], caps, scope)
            evaluation.authorize(value['id'], caps, scope, preview['preview_sha256'], confirmed=True)
        return evaluation, experiment

    def test_experiment_probe_requires_authorization_before_any_http(self):
        evaluation, experiment = self.experiment()
        for verification in ('quick', 'backend'):
            with self.assertRaisesRegex(ValueError, 'NOT_AUTHORIZED'):
                self.service.test_backend('deepseek', self.provider['id'], slot='pro',
                    verification=verification, experiment_id=experiment['id'])
        self.assertEqual(self.requests, [])
        report = evaluation.report(experiment['id'])
        self.assertEqual(report['ledger']['requests'], 0)
        self.assertTrue(all(record['request_count'] == 0 for record in self.service.dispatch('probe_records', {})))

    def test_authorized_probe_is_counted_once_in_experiment_preparation(self):
        evaluation, experiment = self.experiment(authorize=True)
        self.report_usage = True
        result = self.service.test_provider(self.provider['id'], slot='pro', verification='quick',
                                           experiment_id=experiment['id'], probe_id='authorized-local-probe')
        self.assertEqual(len(self.requests), 1)

        self.assertEqual(result['experiment_id'], experiment['id'])
        report = evaluation.report(experiment['id'])
        self.assertEqual(report['ledger']['requests'], 1)
        self.assertEqual(report['preparation']['requests'], 1)
        self.assertEqual(report['preparation']['charged_tokens'], 25)
        self.assertFalse(report['billing']['reference_estimate_is_actual_bill'])
        with self.store.connect() as db:
            ledger = json.loads(db.execute('SELECT payload FROM evaluation_requests').fetchone()[0])
        self.assertIsNone(ledger['actual_cost'])
        self.assertEqual(ledger['request_kind'], 'probe')
        with self.assertRaisesRegex(ValueError, '拒绝重放'):
            self.service.test_provider(self.provider['id'], slot='pro', verification='quick',
                                      experiment_id=experiment['id'], probe_id='authorized-local-probe')
        self.assertEqual(len(self.requests), 1)

    def test_authorized_backend_probe_uses_preparation_gate_for_each_turn(self):
        evaluation, experiment = self.experiment(authorize=True)
        self.report_usage = True
        result = self.service.test_backend('deepseek', self.provider['id'], slot='pro', verification='backend',
                                           experiment_id=experiment['id'])
        self.assertTrue(result['backend_verified'])
        report = evaluation.report(experiment['id'])
        self.assertEqual(report['preparation']['requests'], 2)
        self.assertEqual(report['ledger']['requests'], 2)
        self.assertEqual(report['ledger']['charged_tokens'], 50)

    def test_unknown_cache_write_is_not_invented_as_zero_cost(self):
        from codexrelay.usage import save_price
        provider = self.store.provider(self.provider['id'])
        save_price(self.store, {'input': '2', 'cached_input': '1', 'output': '3', 'cache_write': '5'},
                   provider_id=provider['id'], provider_revision=provider['revision'], model='strong')
        self.report_usage = True; self.cache_write_usage = None
        self.service.test_provider(provider['id'], 'pro', 'quick')
        usage = self.service.dispatch('probe_usage_today', {})
        self.assertEqual(usage['reported_tokens'], 25)
        self.assertEqual(usage['unknown_cache_write'], 1)
        self.assertIsNone(usage['cost'])
        self.assertFalse(usage['cost_complete'])

    def test_experiment_probe_frozen_identity_mismatches_stop_before_http(self):
        for changes, slot, expected in [({}, 'flash', 'external_model'),
                ({'external_model': 'strong', 'external_slot': 'flash'}, 'pro', 'external_slot'),
                ({'external_model': 'other-model'}, 'pro', 'external_model'),
                ({'external_provider_revision': 'old-revision'}, 'pro', 'external_provider_revision'),
                ({'external_model': 'unknown'}, 'pro', 'IDENTITY_UNKNOWN')]:
            with self.subTest(changes=changes, slot=slot):
                evaluation, experiment = self.experiment(authorize=True, **changes)
                with self.assertRaisesRegex(ValueError, expected):
                    self.service.test_provider(self.provider['id'], slot, 'quick', experiment_id=experiment['id'])
                self.assertEqual(evaluation.report(experiment['id'])['ledger']['requests'], 0)
        self.assertEqual(self.requests, [])

    def test_response_model_identity_retains_alias_mapping_and_redacts_echoed_key(self):
        self.response_model = 'canonical-strong'
        result = self.service.test_provider(self.provider['id'], 'pro', 'quick')
        self.assertEqual(result['model'], 'strong')
        self.assertEqual(result['actual_model'], 'canonical-strong')
        self.assertEqual(result['reported_models'], ['canonical-strong'])
        self.response_model = 'fixture-secret'
        result = self.service.test_provider(self.provider['id'], 'pro', 'quick')
        self.assertIsNone(result['actual_model'])
        self.assertNotIn('fixture-secret', json.dumps(self.service.dispatch('probe_records', {})))


if __name__ == '__main__':
    unittest.main()
