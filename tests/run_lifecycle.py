"""Lifecycle checks with actual MCP processes, tools and a local HTTP fixture."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.service import Service
from codexrelay.state import Store

ROOT = Path(__file__).resolve().parents[1]
REQUESTS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        REQUESTS.append(body['model'])
        task = next((m['content'] for m in body['messages'] if m['role'] == 'user'), '')
        previous = [m for m in body['messages'] if m['role'] == 'tool']
        if 'SLOW' in task: time.sleep(3)
        if 'ENV_CHECK' in task and not previous:
            message = {'role':'assistant','content':None,'tool_calls':[{'id':'env','type':'function','function':{'name':'Bash','arguments':json.dumps({'command':str(Path(sys.executable))+' -X utf8 check_env.py'})}}]}
        else:
            message = {'role':'assistant','content':previous[-1]['content'] if previous else 'LIFECYCLE_OK'}
        payload = json.dumps({'id':'fixture','object':'chat.completion','created':1,'model':body['model'],'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}).encode()
        try:
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass


async def run():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='lifecycle-',dir=ROOT/'.tmp') as directory:
        base=Path(directory); workspace=base/'workspace';workspace.mkdir()
        (workspace/'check_env.py').write_text('import os\nassert not os.environ.get("DEEPSEEK_API_KEY")\nassert not os.environ.get("OPENAI_API_KEY")\nprint("CHILD_KEYS_ABSENT")\n')
        service=Service(Store(base/'state'))
        common=dict(kind='custom',base_url=f'http://127.0.0.1:{server.server_port}/v1',pro_model='pro')
        first=service.save_provider(name='First',flash_model='first-model',api_key='fixture-secret',**common)
        second=service.save_provider(name='Second',flash_model='second-model',**common)
        service.dispatch('set_enabled',{'enabled':True})
        env=dict(os.environ,CODEXRELAY_HOME=str(service.store.directory),DEEPSEEK_API_KEY='sentinel-never-in-tools',OPENAI_API_KEY='sentinel-never-in-tools')
        params=StdioServerParameters(command=sys.executable,args=['-I','-X','utf8','-m','codexrelay','mcp'],cwd=str(workspace),env=env)
        app=None
        try:
            async with stdio_client(params) as (r,w):
                async with ClientSession(r,w) as client:
                    await client.initialize()
                    async def call(name,**kwargs):
                        result=await client.call_tool(name,kwargs)
                        assert not result.isError, result
                        return json.loads(result.content[0].text)
                    async def done(identifier):
                        for _ in range(150):
                            outcome=await call('get_deepseek_result',job_id=identifier)
                            if outcome['ready']:return outcome
                            await asyncio.sleep(.1)
                        raise AssertionError('Task did not finish')
                    started=await call('start_deepseek_readonly',task='SLOW lifecycle')
                    assert started['ok'],started
                    # A real desktop process observes the same state, then exits during execution.
                    app=subprocess.Popen([str(ROOT/'src-tauri/target/release/codexrelay.exe')],env=env,creationflags=0x08000000)
                    await asyncio.sleep(.5)
                    assert app.poll() is None
                    service.dispatch('activate_provider',{'id':second['id']})
                    app.terminate();app.wait(timeout=10);app=None
                    outcome=await done(started['job_id'])
                    assert outcome['status']=='completed',outcome
                    assert outcome['model']=='first-model'
                    result=await call('delegate_to_deepseek_readonly',task='New provider lifecycle')
                    assert result['status']=='completed' and result['model']=='second-model',result
                    assert REQUESTS[:2]==['first-model','second-model'],REQUESTS
                    pending=asyncio.create_task(call('delegate_to_deepseek_readonly',task='SLOW cancel synchronous request'))
                    identifier=None
                    for _ in range(100):
                        active=[t for t in service.store.snapshot()['tasks'] if t['status'] in {'queued','running'}]
                        if active:identifier=active[0]['id'];break
                        await asyncio.sleep(.05)
                    assert identifier
                    service.dispatch('cancel_task',{'id':identifier})
                    cancelled=await asyncio.wait_for(pending,15)
                    assert cancelled['status']=='cancelled',cancelled
                    service.dispatch('activate_provider',{'id':first['id']})
                    clean=await call('delegate_to_deepseek',task='ENV_CHECK')
                    assert clean['status']=='completed' and 'CHILD_KEYS_ABSENT' in json.dumps(clean),clean
                    assert 'fixture-secret' not in json.dumps(service.snapshot())
                    assert not (await call('get_deepseek_recovery'))['pending']
            print(json.dumps({'ok':True,'provider':'local deterministic fixture','checks':['GUI process exits while MCP task completes','running task keeps provider snapshot; next task uses new provider','synchronous MCP request cancelled via desktop state','real Bash child receives neither API key environment variable']},indent=2))
        finally:
            if app is not None:app.terminate();app.wait(timeout=10)
            for provider in service.store.providers():service.vault.delete(provider['credential_id'])
    server.shutdown()


if __name__=='__main__':asyncio.run(run())
