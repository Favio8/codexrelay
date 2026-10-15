"""Observe HTTP counts through the real shared retry boundary and durable gate."""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
sys.path.insert(0, str(ROOT / 'vendor/deepseek-as-subagent/src'))
from codexrelay.state import Store
from codexrelay.evaluation import Evaluation
from codexrelay.request_gate import ExperimentRequestGate
from deepseek_mcp.config import Config
from deepseek_mcp.provider_retry import call_with_retry


class Handler(BaseHTTPRequestHandler):
    requests = 0
    status = 200
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests += 1
        response = {'id':'local', 'object':'chat.completion', 'model':body['model'], 'created':1,
            'choices':[{'index':0, 'finish_reason':'stop', 'message':{'role':'assistant','content':'ok'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2,'prompt_cache_hit_tokens':0}}
        if self.status != 200: response = {'error':{'message':'local failure'}}
        data = json.dumps(response).encode()
        self.send_response(self.status); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)


class RequestAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp'); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'state'); self.eval = Evaluation(self.store)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        Handler.requests=0; Handler.status=200

    def config(self, run_tokens=5000):
        exp = self.eval.create_experiment('local HTTP boundary',dict(main_model='fixture',external_model='fixture',
            runtime_version='test',policy_version='test',environment='local-only'))
        exp = self.eval.prepare_experiment(exp['id'],str(Path(self.temp.name)/'trials'))
        trial = next(t for t in exp['trials'] if t['group']=='B')
        caps = dict(external_tokens=100000,trial_tokens=10000,run_tokens=run_tokens,max_trials=1,main_tokens=10000,external_cost=None)
        scope = dict(usage_directory=None,raw_evidence_directory=None)
        preview = self.eval.authorization_preview(exp['id'],caps,scope)
        self.eval.authorize(exp['id'],caps,scope,preview['preview_sha256'],confirmed=True)
        self.eval.start_trial(trial['id'])
        with self.store.connect() as db:
            task = dict(id='r',group_id='g',workspace=trial['workspace'],backend_id='deepseek',model='fixture',
                        provider_revision='fixture-r1',reasoning_effort='provider-default',model_slot='flash')
            db.execute('INSERT INTO tasks VALUES (?,?,0)',('r',json.dumps(task)))
            db.execute('INSERT INTO group_runs VALUES (?,?)',('g','r'))
        self.eval.attach_run(trial['id'],'g','r','deepseek',workspace=trial['workspace'],model='fixture')
        task = dict(experiment_id=exp['id'],trial_id=trial['id'],group_id='g',id='r',usage_price=None)
        config = Config(api_key='local',workspace=Path(trial['workspace']),model='fixture',allowed_tools=[],
            base_url=f'http://127.0.0.1:{self.server.server_port}/v1',max_output_tokens=256,
            request_guard=ExperimentRequestGate(self.store,task))
        return config,exp['id']

    def test_run_cap_denies_before_real_http(self):
        config,identifier = self.config(run_tokens=1)
        with self.assertRaisesRegex(ValueError,'RUN_TOKEN_LIMIT'):
            call_with_retry(config,[{'role':'user','content':'local'}],[],1)
        self.assertEqual(Handler.requests,0)
        self.assertEqual(self.eval.report(identifier)['ledger']['requests'],0)

    def test_real_response_settles_once_and_reference_is_not_a_bill(self):
        config,identifier = self.config()
        reply = call_with_retry(config,[{'role':'user','content':'local'}],[],1)
        self.assertEqual(reply.usage.prompt_tokens,10)
        self.assertEqual(Handler.requests,1)
        report = self.eval.report(identifier)
        self.assertEqual(report['ledger']['charged_tokens'],12)
        self.assertEqual(report['ledger']['usage_gaps'],0)
        self.assertFalse(report['billing']['reference_estimate_is_actual_bill'])

    def test_failed_attempt_retains_reservation_and_blocks_next_http(self):
        config,identifier = self.config(); Handler.status=429
        from unittest.mock import patch
        with patch('deepseek_mcp.provider_retry._backoff'):
            with self.assertRaisesRegex(ValueError,'EXPERIMENT_NOT_AUTHORIZED'):
                call_with_retry(config,[{'role':'user','content':'local'}],[],1)
        self.assertEqual(Handler.requests,1)
        report = self.eval.report(identifier)
        self.assertEqual(report['ledger']['usage_gaps'],1)
        self.assertGreater(report['ledger']['charged_tokens'],0)
