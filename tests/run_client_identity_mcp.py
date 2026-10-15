"""Real source stdio: group ownership cannot stand in for this call's chat.

No model requests, credentials, switch changes or daily configuration writes.
The two client identities below are explicit local fixture reports, not real
Codex Desktop/CLI or independently attested host identities.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.state import Store
from codexrelay.integration_evidence import CALLS_KEY


async def run():
    with tempfile.TemporaryDirectory(prefix='client-caller-stdio-',dir=ROOT/'.tmp') as directory:
        area=Path(directory);workspace=area/'workspace';workspace.mkdir();codex_home=area/'codex';codex_home.mkdir()
        env={k:v for k,v in os.environ.items() if k not in {'OPENAI_API_KEY','DEEPSEEK_API_KEY','PYTHONPATH','PYTHONHOME'}}
        env.update(CODEXRELAY_HOME=str(area/'state'),CODEX_HOME=str(codex_home))
        bootstrap=f"import sys;sys.path.insert(0,{str(ROOT/'engine')!r});from codexrelay.mcp_server import serve;serve()"
        parameters=StdioServerParameters(command=sys.executable,args=['-I','-B','-X','utf8','-c',bootstrap],cwd=str(workspace),env=env)
        async with stdio_client(parameters) as (reader,writer), ClientSession(reader,writer) as client:
            await client.initialize()
            async def call(name,**arguments):
                result=await client.call_tool(name,arguments)
                assert not result.isError,result
                body=json.loads(result.content[0].text)
                assert body.get('ok',True) is not False,body
                return body
            listing=await client.list_tools()
            for tool in listing.tools:
                if tool.name=='bind_client_session':continue
                assert 'client_binding_id' in tool.inputSchema['properties'],tool.name
                assert 'client_binding_id' not in tool.inputSchema.get('required',[]),tool.name
            a=await call('bind_client_session',session_id='fixture-chat-A',root_session_id='fixture-chat-A',
                         client_kind='local_stdio',client_version='fixture-A-version',confirmed=True)
            b=await call('bind_client_session',session_id='fixture-chat-B',root_session_id='fixture-chat-B',
                         client_kind='local_stdio',client_version='fixture-B-version',confirmed=True)
            assert not a['attested'] and not b['attested']
            group=await call('create_task_group',goal='Identity-only fixture, no model execution',task_kind='analysis',
                             capability='readonly',client_binding_id=a['id'])
            # Omitted caller is allowed to read, but earns no chat identity.
            await call('get_task_group',group_id=group['group_id'])
            # An explicitly different chat can read the same workspace group;
            # ownership remains A while this invocation belongs to B.
            await call('get_task_group',group_id=group['group_id'],client_binding_id=b['id'])
            chain=await call('get_client_evidence',group_id=group['group_id'],client_binding_id=b['id'])
            reads=[r for r in chain['calls'] if r['tool']=='get_task_group']
            assert len(reads)==2,reads
            assert reads[0]['success'] and reads[0]['owner_binding_id']==a['id'] and reads[0]['caller_binding_id'] is None
            assert reads[1]['success'] and reads[1]['owner_binding_id']==a['id'] and reads[1]['caller_binding_id']==b['id']
            records=Store(area/'state').setting(CALLS_KEY)
            observations=[r for r in records if r['tool']=='get_task_group']
            assert observations[0]['client_kind']=='unknown' and observations[0]['client_session_id'] is None
            assert observations[1]['client_kind']=='local_stdio_fixture' and observations[1]['client_session_id']=='fixture-chat-B'
            assert observations[1]['client_version']=='fixture-B-version'
            assert Store(area/'state').setting('enabled') is False
            report={'ok':True,'method':'real source stdio MCP; two explicitly reported local fixture chats on one instance',
                    'tool_count':len(listing.tools),'model_requests':0,'switches_modified':False,
                    'client_identity':'user/test reports; not independent host attestation',
                    'checks':['all legacy/neutral metadata controls accept optional caller binding',
                              'unbound query succeeds and keeps caller unknown',
                              'group owner A stays distinct from explicit reader B',
                              'B invocation never upgrades A integration call evidence',
                              'client kind/session/version come only from the explicit caller binding']}
            evidence=ROOT/'artifacts/evidence/implementation-review';evidence.mkdir(parents=True,exist_ok=True)
            (evidence/'client-identity-mcp.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(run())
