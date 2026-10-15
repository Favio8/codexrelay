"""Real stdio MCP, Python tools and Pi SDK; deterministic local protocol, no paid model."""
import asyncio
import hashlib
import json
import os
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
from deepseek_mcp.execution_context import complete_exchange

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert complete_exchange(body['messages']), 'invalid assistant/tool exchange'
        self.requests.append(body)
        content = json.dumps(body['messages'])
        tools = [m for m in body['messages'] if m['role'] == 'tool']
        phase_two = 'PHASE_TWO' in content
        if not phase_two:
            tool = 'Write'; args = {'path': 'partial.txt', 'content': 'verified partial artifact'}
            tokens = {'prompt_tokens': 32000, 'completion_tokens': 500}
        elif not tools:
            tool = 'Read'; args = {'path': 'partial.txt'}; tokens = {'prompt_tokens': 100, 'completion_tokens': 20}
        elif len(tools) == 1:
            tool = 'Write'; args = {'path': 'remaining.txt', 'content': 'remaining artifact'}; tokens = {'prompt_tokens': 100, 'completion_tokens': 20}
        else:
            tool = None; args = None; tokens = {'prompt_tokens': 100, 'completion_tokens': 20}
        message = {'role': 'assistant', 'content': 'Candidate complete; parent must verify', 'reasoning_content': 'opaque fixture reasoning'}
        if tool: message['tool_calls'] = [{'id': 'call_' + str(len(self.requests)), 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(args)}}]
        response = {'id': 'fixture', 'object': 'chat.completion', 'created': int(time.time()), 'model': body['model'],
                    'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if tool else 'stop'}], 'usage': tokens}
        payload = json.dumps(response).encode(); self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)


async def run():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    reports = []
    try:
        for backend in ('deepseek', 'pi'):
            area = Path(tempfile.mkdtemp(prefix='execution-mcp-', dir=ROOT / '.tmp'))
            workspace = area / '中文 with spaces'; workspace.mkdir()
            service = Service(Store(area / 'state'))
            service.save_provider(name='Protocol fixture', base_url=f'http://127.0.0.1:{server.server_port}/v1', flash_model='fixture-thinking', pro_model='fixture-thinking', max_total_tokens=32768)
            service.store.set_setting('enabled', True); service.store.set_setting('active_backend', backend)
            env = {k:v for k,v in os.environ.items() if k not in {'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'PYTHONPATH', 'PYTHONHOME'}}
            env['CODEXRELAY_HOME'] = str(service.store.directory)
            params = StdioServerParameters(command=sys.executable, args=['-I', '-B', '-X', 'utf8', '-m', 'codexrelay', 'mcp'], cwd=str(workspace), env=env)
            async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
                await client.initialize()
                async def call(name, **arguments):
                    reply = await client.call_tool(name, arguments); assert not reply.isError, reply
                    return json.loads(reply.content[0].text)
                async def finished(job):
                    for _ in range(250):
                        result = await call('get_task_result', job_id=job['job_id'])
                        if result['ready']: return result
                        await asyncio.sleep(.04)
                    raise AssertionError('worker did not exit')
                async def acknowledge():
                    status = await call('get_task_recovery')
                    for item in status['pending']:
                        target = workspace / item['path']; assert target.is_file()
                        assert hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256']
                    if status['pending']:
                        reply = await call('acknowledge_task_mutations', transaction_ids=[r['transaction_id'] for r in status['pending']]); assert not reply['pending']
                    for item in status['checks']:
                        reply = await call('acknowledge_workspace_review', review_id=item['review_id'], state_sha256=item['state_sha256'], evidence='Parent independently read artifacts and verified exact current state')
                        assert reply['ok'], reply
                ping = await call('ping')
                listing = await client.list_tools()
                assert {t.name for t in listing.tools} == {t['name'] for t in ping['server_capabilities']['tools']}
                binding = await call('bind_client_session', session_id='fixture-'+backend, root_session_id='fixture-root-'+backend,
                                     client_kind='local_stdio', confirmed=True, client_version='deterministic-fixture')
                group = await call('create_task_group', goal='PHASE_ONE then PHASE_TWO', task_kind='implementation', capability='coding', write_paths=['partial.txt', 'remaining.txt'], constraints='Preserve first artifact; write only remaining work on continuation', acceptance='Both artifacts have exact expected text',client_binding_id=binding['id'])
                job = await call('start_agent', task='PHASE_ONE', group_id=group['group_id'])
                task = await finished(job); assert task['status'] == 'budget_stopped', task
                cp = await call('get_task_checkpoint', checkpoint_id=task['checkpoint']['checkpoint_id'])
                assert cp['environment']['client_binding_id'] == binding['id']
                assert cp['environment']['mcp_instance'] == ping['instance']
                assert cp['mutations'] and cp['files']; assert task['verification']['status'] == 'pending'
                verification = await call('record_task_verification', job_id=task['id'], passed=True, evidence='Read partial.txt and verified exact contents', disposition='verified_partial', checkpoint_id=cp['checkpoint_id'], checkpoint_sha256=cp['sha256'], completed_items=['PHASE_ONE'], remaining_items=['PHASE_TWO'])
                assert verification['ok'], verification
                arguments = dict(group_id=group['group_id'], checkpoint_id=cp['checkpoint_id'], checkpoint_sha256=cp['sha256'], verification_id=verification['verification']['id'], remaining_task='PHASE_TWO', idempotency_key='continue-once')
                blocked = await call('resume_agent', **arguments); assert 'RECOVERY_REQUIRED' in blocked['error'], blocked
                await acknowledge()
                service.store.set_setting('enabled', False)
                blocked = await call('resume_agent', **arguments); assert 'DISABLED' in blocked['error'], blocked
                service.store.set_setting('enabled', True)
                before_hash = hashlib.sha256((workspace / 'partial.txt').read_bytes()).hexdigest()
                next_job = await call('resume_agent', **arguments); next_task = await finished(next_job)
                assert next_task['status'] == 'completed', next_task
                replay = await call('resume_agent', **arguments); assert replay['job_id'] == next_job['job_id'], replay
                assert hashlib.sha256((workspace / 'partial.txt').read_bytes()).hexdigest() == before_hash
                assert (workspace / 'remaining.txt').read_text() == 'remaining artifact'
                await acknowledge()
                latest = await call('get_task_checkpoint', checkpoint_id=next_task['checkpoint']['checkpoint_id'])
                done = await call('record_task_verification', job_id=next_task['id'], passed=True, evidence='Parent verified both exact artifacts; no writes replayed', disposition='verified_complete', checkpoint_id=latest['checkpoint_id'], checkpoint_sha256=latest['sha256'], completed_items=['PHASE_ONE', 'PHASE_TWO'])
                assert done['ok'], done
                chain = await call('get_task_group', group_id=group['group_id'])
                assert chain['status'] == 'completed' and chain['used_continuations'] == 1
                assert chain['usage_summary']['reported_tokens'] == 32860, chain
                observed = await call('get_client_evidence',group_id=group['group_id'])
                assert any(r['tool']=='resume_agent' and r['success'] for r in observed['calls']), observed
                assert {r['client_binding_id'] for r in observed['calls'] if 'client_binding_id' in r} == {binding['id']}
                reports.append(dict(backend=backend, protocol='thinking', ok=True, group_id=group['group_id'],
                                    fixture=str(area), rounds=2, usage=chain['usage_summary'], checks=['budget checkpoint', 'journal/workspace gate', 'switch gate', 'partial verification', 'remaining-only continuation', 'idempotency', 'parent completion']))
            reopened = Store(area / 'state'); assert reopened.task(next_task['id'])['status'] == 'completed'
        report = dict(kind='fixed_local_response_real_stdio_and_tools', ok=True, backends=reports)
        from codexrelay import __version__
        evidence = ROOT / 'artifacts/evidence' / __version__; evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'execution-mcp.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally: server.shutdown()


if __name__ == '__main__': asyncio.run(run())
