"""Cross real stdio, frozen trial admission, shared authorization and actual tools.

Uses only fixed local HTTP responses. It is not a Codex chat or model-quality test.
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.state import Store
from codexrelay.service import Service
from codexrelay.evaluation import Evaluation
from codexrelay.evaluation_cases import builtin_cases

ROOT = Path(__file__).resolve().parents[1]
GOOD = 'def clamp(value, lower, upper):\n    if lower > upper: raise ValueError("bounds")\n    return min(upper, max(lower, value))\n'


class Handler(BaseHTTPRequestHandler):
    calls = 0
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).calls += 1
        message = {'role':'assistant','content':'fixed local candidate'}
        previous=[m for m in body['messages'] if m['role']=='tool']
        if not previous:
            message['tool_calls']=[{'id':'read','type':'function','function':{'name':'Read','arguments':json.dumps({'path':'clamp.py'})}}]
        elif len(previous)==1:
            message['tool_calls']=[{'id':'edit','type':'function','function':{'name':'Edit','arguments':json.dumps({'path':'clamp.py',
                'old_string':'def clamp(value, lower, upper):\n    return max(lower, value)\n','new_string':GOOD})}}]
        data = json.dumps({'id':'local','model':body['model'],'object':'chat.completion','created':1,
            'choices':[{'index':0,'finish_reason':'tool_calls' if 'tool_calls' in message else 'stop','message':message}],
            'usage':{'prompt_tokens':10,'completion_tokens':2}}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)


async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    reports=[]
    try:
        for backend,arm in [('deepseek','B'),('pi','C')]:
            area=Path(tempfile.mkdtemp(prefix='reliability-mcp-',dir=ROOT/'.tmp'))
            service=Service(Store(area/'state'))
            saved=service.save_provider(name='local only',base_url=f'http://127.0.0.1:{server.server_port}/v1',flash_model='fixture',pro_model='fixture',max_output_tokens=512)
            provider=service.store.provider(saved['id']);service.store.set_setting('enabled',True);service.store.set_setting('active_backend',backend)
            ev=Evaluation(service.store)
            exp=ev.create_experiment('local stdio evidence',dict(main_model='unknown',external_model='fixture',external_provider_revision=provider['revision'],external_reasoning='provider-default',runtime_version='0.4.0',policy_version='test',environment='deterministic local substitute'),builtin_cases()[:1])
            exp=ev.prepare_experiment(exp['id'],str(area/'workspaces'))
            trial=next(t for t in exp['trials'] if t['group']==arm)
            caps=dict(external_tokens=100000,trial_tokens=100000,run_tokens=100000,max_trials=1,main_tokens=0,external_cost=None)
            scope=dict(usage_directory=None,raw_evidence_directory=None);preview=ev.authorization_preview(exp['id'],caps,scope)
            ev.authorize(exp['id'],caps,scope,preview['preview_sha256'],confirmed=True);ev.start_trial(trial['id'])
            ev.bind_session(trial['id'],'s-'+backend,'s-'+backend,'local_substitute',confirmed=True)
            env={k:v for k,v in os.environ.items() if k not in {'OPENAI_API_KEY','DEEPSEEK_API_KEY','PYTHONPATH','PYTHONHOME'}}
            env['CODEXRELAY_HOME']=str(service.store.directory)
            params=StdioServerParameters(command=sys.executable,args=['-I','-B','-X','utf8','-m','codexrelay','mcp'],cwd=trial['workspace'],env=env)
            before=Handler.calls
            async with stdio_client(params) as (reader,writer),ClientSession(reader,writer) as client:
                await client.initialize()
                async def call(name,**args):
                    response=await client.call_tool(name,args);assert not response.isError,response
                    return json.loads(response.content[0].text)
                binding=await call('bind_client_session',session_id='s-'+backend,root_session_id='s-'+backend,client_kind='local_stdio',confirmed=True)
                ping=await call('ping',client_binding_id=binding['id']);assert ping['evidence_source']=='server_declaration'
                blocked=await call('start_agent',task='unbound external execution')
                assert 'EXPERIMENT_BINDING_REQUIRED' in blocked['error'] and Handler.calls==before,blocked
                instruction=ev.trial_instruction(trial['id'])
                group_params=instruction['client_binding_steps'][1]['params'];group_params['client_binding_id']=binding['id']
                group=await call('create_task_group',**group_params);assert 'group_id' in group,group
                blocked=await call('start_agent',task='changed frozen task',group_id=group['group_id'])
                assert 'EXPERIMENT_INPUT_CHANGED' in blocked['error'] and Handler.calls==before,blocked
                job=await call('start_agent',task=instruction['task'],group_id=group['group_id']);assert job.get('ok'),job
                for _ in range(500):
                    task=await call('get_task_result',job_id=job['job_id'])
                    if task['ready']:break
                    await asyncio.sleep(.04)
                assert task['status']=='completed',task
                pending=await call('get_task_recovery',client_binding_id=binding['id'])
                for record in pending['pending']:
                    target=Path(trial['workspace'])/record['path']
                    assert hashlib.sha256(target.read_bytes()).hexdigest()==record['sha256']
                if pending['pending']:
                    result=await call('acknowledge_task_mutations',transaction_ids=[r['transaction_id'] for r in pending['pending']],client_binding_id=binding['id']);assert not result['pending']
                for record in pending['checks']:
                    result=await call('acknowledge_workspace_review',review_id=record['review_id'],state_sha256=record['state_sha256'],evidence='Parent checked exact fixed candidate bytes',client_binding_id=binding['id']);assert result['ok'],result
                cp=await call('get_task_checkpoint',checkpoint_id=task['checkpoint']['checkpoint_id'])
                assert cp['environment']['experiment_id']==exp['id'] and cp['environment']['trial_id']==trial['id']
                assert all(not item['result'].startswith('ERROR:') for item in cp['tool_evidence_records']),cp['tool_evidence_records']
                assert (Path(trial['workspace'])/'clamp.py').read_text()==GOOD
                ev.finish_trial(trial['id'],'completed')
                score=ev.score_trial(trial['id']);assert score['passed'],score
                accepted=await call('record_task_verification',job_id=task['id'],passed=True,disposition='verified_complete',checkpoint_id=cp['checkpoint_id'],checkpoint_sha256=cp['sha256'],evidence='Parent exact-byte inspection and frozen boundary grader passed',completed_items=[instruction['task']]);assert accepted['ok'],accepted
                report=ev.report(exp['id']);row=next(r for r in report['trials'] if r['trial_id']==trial['id'])
                assert row['delegated'] and row['quality_passed'],row
                assert report['ledger']['requests']==3 and report['ledger']['charged_tokens']==36,report['ledger']
                assert not row['coverage']['usage_complete'],'main usage was never supplied'
                reports.append(dict(backend=backend,experiment_id=exp['id'],trial_id=trial['id'],group_id=group['group_id'],run_id=task['id'],checkpoint_id=cp['checkpoint_id'],fixture=str(area),http_requests=Handler.calls-before,ledger=report['ledger'],quality='frozen grader passed deterministic candidate; no real model',main_usage='unknown'))
        output=ROOT/'artifacts/evidence/0.4.0/reliability-mcp.json'
        result=dict(ok=True,source='local_stdio_and_fixed_http_not_real_codex_client',backends=reports)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:server.shutdown();server.server_close()


if __name__=='__main__':asyncio.run(main())
