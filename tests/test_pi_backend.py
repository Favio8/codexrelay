import json
import sys
import hashlib
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from codexrelay.backends import pi, review, create_manager
from codexrelay.backends.pi import run_pi, available
from codexrelay.mcp_server import Relay
from codexrelay.state import Store
from codexrelay.service import Service
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.agent_loop import _build_result
from deepseek_mcp.config import Config
from deepseek_mcp.job_manager import JobBusy
from deepseek_mcp.transaction_recovery import query_with_lease, acknowledge_with_lease
from test_control import MemoryVault
from test_execution_reliability import response


@unittest.skipUnless(available(),'Build the pinned Pi runtime first')
class PiBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pi-tests-',dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/'project with spaces';self.workspace.mkdir()
        self.config=Config(api_key='not-a-real-key',workspace=self.workspace,allowed_tools=['Read'],delegation_capability='readonly')
        change=patch.object(review,'DIRECTORY',self.root/'reviews');change.start();self.addCleanup(change.stop)

    def test_real_pi_scheduler_reads_file_and_normalizes_telemetry(self):
        (self.workspace/'input.txt').write_text('PI_REAL_FILE')
        def provider(config,messages,*args,**kwargs):
            if messages[-1]['role']=='tool':
                self.assertIn('PI_REAL_FILE',messages[-1]['content'])
                return response()
            return response('Read',{'path':'input.txt'})
        with patch.object(loop,'_call_with_retry',side_effect=provider):
            result=run_pi('Read input',self.config)
        self.assertEqual(result['tokens']['total'],240)
        self.assertEqual(result['tool_calls'],1)
        self.assertEqual(len(result['usage_events']),2)

    def test_windows_namespace_interpreter_path(self):
        script='''from pathlib import Path
import sys
sys.path.insert(0,sys.argv[2])
from unittest.mock import patch
from codexrelay.backends.pi import run_pi,runtime
from deepseek_mcp.config import Config
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.provider_response import ProviderResponse
reply=ProviderResponse.from_payload({'choices':[{'message':{'role':'assistant','content':'NAMESPACE_OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':10}})
with patch.object(loop,'_call_with_retry',return_value=reply):
 print(run_pi('Finish',Config(api_key='fixture',workspace=Path(sys.argv[1]),allowed_tools=['Read'],delegation_capability='readonly'))['final_message'])
'''
        executable='\\\\?\\'+str(Path(sys.executable).resolve())
        result=subprocess.run([executable,'-I','-B','-X','utf8','-c',script,str(self.workspace),str(ROOT/'engine')],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('NAMESPACE_OK',result.stdout)

    def test_coding_journal_and_review_survive_backend_change(self):
        config=replace(self.config,allowed_tools=['Write'],delegation_capability='coding',write_paths=[Path('allowed.txt')])
        manager=create_manager('pi')
        with patch.object(loop,'_call_with_retry',side_effect=[response('Write',{'path':'allowed.txt','content':'written'}),response()]):
            job=manager.start('Write','',config)
            self.assertTrue(manager.wait_for_terminal(job['job_id'],15))
            self.assertEqual(manager.result(job['job_id'])['status'],'completed',manager.result(job['job_id']))
        self.assertEqual((self.workspace/'allowed.txt').read_text(),'written')
        records=query_with_lease(config)
        self.assertTrue(records)
        acknowledge_with_lease(config,[r['transaction_id'] for r in records])
        with self.assertRaisesRegex(Exception,'WORKSPACE_REVIEW_REQUIRED'):
            create_manager('deepseek').start('Read','',self.config)
        item=review.query(config)[0]
        review.acknowledge(config,item['review_id'],item['state_sha256'],'Read actual file and validated content')
        self.assertEqual(review.query(config),[])

    def test_budget_stops_before_second_request_and_keeps_first_usage(self):
        config=replace(self.config,max_total_tokens=32768)
        with patch.object(loop,'_call_with_retry',return_value=response('Read',{'path':'input.txt'},prompt_tokens=32768)) as provider:
            with self.assertRaises(loop.AgentBudgetExceeded) as caught:
                run_pi('Read',config)
        self.assertEqual(provider.call_count,1)
        self.assertEqual(caught.exception.telemetry['tokens']['total'],32788)
        self.assertEqual(len(caught.exception.telemetry['usage_events']),1)

    def test_cancellation_interrupts_provider_and_joins_pi(self):
        manager=create_manager('pi')
        entered=threading.Event()
        def wait(*args,**kwargs):
            entered.set()
            while not kwargs['cancel_signal'].is_set():time.sleep(.01)
            raise loop.AgentLoopCancelled('test cancel')
        with patch.object(loop,'_call_with_retry',side_effect=wait):
            job=manager.start('Wait','',self.config)
            self.assertTrue(entered.wait(10))
            manager.cancel(job['job_id'])
            self.assertTrue(manager.wait_for_terminal(job['job_id'],10))
            result=manager.result(job['job_id'])
        self.assertEqual(result['status'],'cancelled')
        self.assertTrue(result['telemetry']['tokens']['interrupted_request'])

    def test_relay_persists_response_events_and_backend_identity(self):
        store=Store(self.root/'state');service=Service(store,MemoryVault())
        provider=service.save_provider(name='Local',base_url='http://localhost:1',flash_model='model',pro_model='model')
        service.dispatch('activate_execution',{'backend':'pi','provider_id':provider['id']})
        store.set_setting('enabled',True)
        relay=Relay(store,service.vault,self.workspace)
        with patch.object(loop,'_call_with_retry',return_value=response()):
            job=relay.start('Finish','','readonly','flash')
            until=time.monotonic()+15
            while store.task(job['job_id'])['status'] in {'queued','running'} and time.monotonic()<until:time.sleep(.05)
        task=store.task(job['job_id'])
        self.assertEqual(task['status'],'completed',task)
        self.assertEqual(task['backend_id'],'pi')
        self.assertTrue(task['backend_capabilities']['strict_readonly'])
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM usage_events').fetchone()[0],1)

    def test_steering_accepted_at_finalize_is_applied(self):
        pending=['New requirement']
        seen=[]
        def provider(config,messages,*args,**kwargs):
            seen.append(messages)
            return response()
        def finalize():
            return [pending.pop()] if pending else []
        with patch.object(loop,'_call_with_retry',side_effect=provider):
            result=run_pi('Initial task',self.config,control_finalize=finalize)
        self.assertEqual(len(seen),2)
        self.assertIn('New requirement',json.dumps(seen[-1]))
        self.assertEqual(result['applied_steering_messages'],1)

    def test_write_scope_and_command_credentials(self):
        config=replace(self.config,allowed_tools=['Write','Bash'],delegation_capability='coding',write_paths=[Path('allowed.txt')])
        code='import os; assert not os.environ.get("DEEPSEEK_API_KEY"); assert not os.environ.get("OPENAI_API_KEY"); print("NO_KEYS")'
        replies=[response('Write',{'path':'forbidden.txt','content':'blocked'}),response('Bash',{'executable':sys.executable,'args':['-I','-c',code]}),response()]
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'secret-sentinel','OPENAI_API_KEY':'secret-sentinel'}),patch.object(loop,'_call_with_retry',side_effect=replies):
            result=run_pi('Probe tools',config)
        self.assertFalse((self.workspace/'forbidden.txt').exists())
        self.assertEqual(result['bash']['results'][0]['exit_code'],0)
        self.assertEqual(review.query(config),[])

    def test_real_command_cancel_keeps_workspace_check(self):
        (self.workspace/'wait.py').write_text('from pathlib import Path\nimport time\nPath("started.txt").write_text("started")\ntime.sleep(30)')
        config=replace(self.config,allowed_tools=['Bash'],delegation_capability='coding')
        manager=create_manager('pi')
        with patch.object(loop,'_call_with_retry',return_value=response('Bash',{'executable':sys.executable,'args':['-B','wait.py']})):
            job=manager.start('Wait','',config)
            try:
                until=time.monotonic()+15
                while not (self.workspace/'started.txt').exists() and time.monotonic()<until:time.sleep(.02)
                self.assertTrue((self.workspace/'started.txt').exists())
                manager.cancel(job['job_id'])
                self.assertTrue(manager.wait_for_terminal(job['job_id'],10))
                self.assertEqual(manager.result(job['job_id'])['status'],'cancelled')
            finally:
                manager.cancel(job['job_id']);manager.wait_for_terminal(job['job_id'],10)
        check=review.query(config)[0]
        self.assertIn('started.txt',check['observed']['added'])

    def test_cross_backend_workspace_lease(self):
        entered=threading.Event();manager=create_manager('pi')
        def wait(*args,**kwargs):
            entered.set()
            while not kwargs['cancel_signal'].is_set():time.sleep(.01)
            raise loop.AgentLoopCancelled('cancel lease fixture')
        with patch.object(loop,'_call_with_retry',side_effect=wait):
            job=manager.start('Hold lease','',self.config)
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises(JobBusy):
                    create_manager('deepseek').start('Competing manager','',self.config)
            finally:
                manager.cancel(job['job_id']);self.assertTrue(manager.wait_for_terminal(job['job_id'],10))

    def test_parent_crash_preserves_usage_journal_and_workspace_check(self):
        script=self.root/'crash.py'
        script.write_text('''import sys,os,time,json
from pathlib import Path
sys.path.insert(0,sys.argv[4])
from codexrelay.backends import create_manager,review
from codexrelay.state import Store
from deepseek_mcp.config import Config
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.provider_response import ProviderResponse
workspace=Path(sys.argv[1]);review.DIRECTORY=Path(sys.argv[2]);store=Store(Path(sys.argv[3]))
store.save_provider(dict(id='fixture',name='fixture',flash_model='model',pro_model='model'))
store.set_setting('enabled',True);task=store.admit('Crash fixture',workspace,'coding','flash',backend='pi')
calls=0
def provider(*args,**kwargs):
 global calls
 calls+=1
 message={'role':'assistant','content':'done'}
 if calls==1:message={'role':'assistant','content':None,'tool_calls':[{'id':'write','type':'function','function':{'name':'Write','arguments':json.dumps({'path':'crash.txt','content':'preserved'})}}]}
 return ProviderResponse.from_payload({'choices':[{'message':message,'finish_reason':'tool_calls' if calls==1 else 'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':10}})
loop._call_with_retry=provider
def report(value):
 store.update_task(task['id'],telemetry=value)
 if len(value.get('usage_events',[]))==2:os._exit(99)
manager=create_manager('pi')
manager.start('Write crash.txt','',Config(api_key='fixture',workspace=workspace,allowed_tools=['Write']),progress_reporter=report)
time.sleep(30)
''',encoding='utf-8')
        result=subprocess.run([sys.executable,'-I','-B',str(script),str(self.workspace),str(review.DIRECTORY),str(self.root/'state'),str(ROOT/'engine')],timeout=20,capture_output=True)
        self.assertEqual(result.returncode,99,result.stderr.decode(errors='replace'))
        self.assertEqual((self.workspace/'crash.txt').read_text(),'preserved')
        store=Store(self.root/'state')
        with store.connect() as db:identifier=db.execute('SELECT id FROM tasks').fetchone()[0]
        reconnected=Relay(store,MemoryVault(),self.workspace)
        task=reconnected.owned(identifier)
        self.assertEqual(task['status'],'interrupted')
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM usage_events').fetchone()[0],2)
        record=review.query(self.config)[0]
        self.assertEqual(record['status'],'interrupted')
        pending=query_with_lease(self.config)
        self.assertEqual(len(pending),1)
        acknowledge_with_lease(self.config,[pending[0]['transaction_id']])
        review.acknowledge(self.config,record['review_id'],record['state_sha256'],'Verified crash.txt remains preserved and both responses persisted')

    def test_node_crash_still_persists_the_workspace_check(self):
        crashed=self._runtime_with_bridge('''
import fs from 'node:fs'
const line=await new Promise(resolve=>process.stdin.once('data',resolve))
fs.writeFileSync('crashed.txt','written before the runtime died')
process.exit(7)
''')
        config=replace(self.config,allowed_tools=['Write'],delegation_capability='coding')
        with patch.object(pi,'runtime',lambda:crashed),patch.object(loop,'_call_with_retry',return_value=response('Write',{'path':'crashed.txt','content':'written before the runtime died'})):
            with self.assertRaises(Exception):run_pi('Write crashed.txt',config)
        self.assertEqual((self.workspace/'crashed.txt').read_text(),'written before the runtime died')
        # The gate must stay closed even though the scheduler itself died mid-task.
        record=review.query(self.config)[0]
        self.assertEqual(record['status'],'pending')
        self.assertIn('crashed.txt',record['observed']['added'])
        with self.assertRaisesRegex(Exception,'WORKSPACE_REVIEW_REQUIRED'):
            create_manager('deepseek').start('Read','',self.config)
        review.acknowledge(self.config,record['review_id'],record['state_sha256'],'Read crashed.txt after the runtime exited')

    def test_stop_closes_the_job_object_even_when_termination_fails(self):
        class Broken:
            stdin=None;stdout=None
            def __init__(self):self.terminated=False;self.waited=False
            def poll(self):return None
            def terminate(self):self.terminated=True;raise OSError('already gone')
            def wait(self,timeout=None):self.waited=True
        class Job:
            def __init__(self):self.closed=False
            def close(self):self.closed=True
        process,job=Broken(),Job()
        errors=pi._stop(process,job)
        self.assertTrue(any('terminate' in item for item in errors))
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertTrue(job.closed)

    def test_required_vendored_bindings_are_present(self):
        pi.verify_bindings()
        with patch.object(loop,'_call_with_retry'):
            del loop._build_result
            try:
                with self.assertRaisesRegex(loop.AgentLoopError,'_build_result'):pi.verify_bindings()
            finally:
                import deepseek_mcp.agent_loop as module
                module._build_result=_build_result
        self.assertTrue(callable(_build_result))

    def _runtime_with_bridge(self,source):
        directory=self.root/f'runtime-{uuid.uuid4().hex}'
        directory.mkdir()
        shutil.copy2(pi.runtime()/'node.exe',directory/'node.exe')
        (directory/'bridge.mjs').write_text(source,encoding='utf-8')
        manifest={'version':'1.2.0','files':{name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('node.exe','bridge.mjs')}}
        (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        return directory


if __name__=='__main__':unittest.main()
