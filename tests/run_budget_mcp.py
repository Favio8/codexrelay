"""Real tool/journal behavior when a local budget or remote quota stops a run."""
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
from codexrelay.service import Service
from codexrelay.state import Store

ROOT=Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    mode='budget'
    calls=0
    def log_message(self,*args):pass
    def do_POST(self):
        type(self).calls+=1
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.mode=='quota':
            self.send_response(429);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(b'{"error":{"code":"insufficient_quota","message":"fixture quota error"}}');return
        previous=any(m['role']=='tool' for m in body['messages'])
        message={'role':'assistant','content':'Done'} if previous else {'role':'assistant','content':None,'tool_calls':[{'id':'write','type':'function','function':{'name':'Write','arguments':json.dumps({'path':'budget-output.txt','content':'preserve completed mutation\n'})}}]}
        payload={'choices':[{'index':0,'message':message,'finish_reason':'stop' if previous else 'tool_calls'}],'usage':{'prompt_tokens':40000 if previous else 1000,'completion_tokens':20},'id':'fixture','model':body['model'],'object':'chat.completion','created':1}
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(payload).encode())


async def run():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='budget-mcp-',dir=ROOT/'.tmp') as folder:
        base=Path(folder);workspace=base/'workspace';workspace.mkdir()
        service=Service(Store(base/'state'))
        service.save_provider(name='Budget fixture',kind='custom',base_url=f'http://127.0.0.1:{server.server_port}/v1',flash_model='fixture',pro_model='fixture',max_total_tokens=32768)
        service.dispatch('set_enabled',{'enabled':True})
        env={k:v for k,v in os.environ.items() if k not in {'DEEPSEEK_API_KEY','OPENAI_API_KEY','PYTHONPATH','PYTHONHOME'}}
        env['CODEXRELAY_HOME']=str(service.store.directory)
        params=StdioServerParameters(command=sys.executable,args=['-I','-X','utf8','-m','codexrelay','mcp'],cwd=str(workspace),env=env)
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as client:
                await client.initialize()
                async def call(name,**kwargs):
                    reply=await client.call_tool(name,kwargs);assert not reply.isError,reply
                    return json.loads(reply.content[0].text)
                outcome=await call('delegate_to_deepseek',task='Write the fixture file')
                assert outcome['status']=='budget_stopped',outcome
                assert outcome['failure']['code']=='local_token_budget',outcome
                assert outcome['failure']['metrics']['limit']==32768,outcome
                assert outcome['telemetry']['tokens']['prompt'] == 41000, outcome
                assert outcome['telemetry']['workspace_changes']['added'] == ['budget-output.txt'], outcome
                output=workspace/'budget-output.txt'
                assert output.read_text()=='preserve completed mutation\n'
                pending=(await call('get_deepseek_recovery'))['pending']
                assert len(pending)==1 and pending[0]['path']=='budget-output.txt'
                assert pending[0]['sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
                result=await call('acknowledge_deepseek_mutations',transaction_ids=[pending[0]['transaction_id']])
                assert not result['pending']
                Handler.mode='quota';before=Handler.calls
                quota=await call('delegate_to_deepseek_readonly',task='Check fixture')
                assert quota['failure']['code']=='provider_quota',quota
                assert Handler.calls-before==1, 'Quota failure must not be retried as rate limiting'
                print(json.dumps({'ok':True,'checks':['local budget classified with counters','completed Write preserved through budget stop','journal hash verified and exactly acknowledged','provider insufficient_quota distinct from local budget and not retried']},indent=2))
    server.shutdown()


if __name__=='__main__':asyncio.run(run())
