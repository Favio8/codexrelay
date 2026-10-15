"""Real FastMCP conversion: failed evidence cannot lose an accepted job or replay it."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from mcp.server.fastmcp import FastMCP
from codexrelay.call_audit import install_call_audit, decoded
from codexrelay.state import Store
from codexrelay import client_evidence, integration_evidence


class CallAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp');self.addCleanup(self.temp.cleanup)
        self.workspace=Path(self.temp.name);self.store=Store(self.workspace/'state')
        self.relay=SimpleNamespace(store=self.store,instance='fixture-instance',workspace=self.workspace)
        self.calls=0;self.mcp=FastMCP('audit fixture')
        @self.mcp.tool()
        def start_agent(task:str='fixture') -> str:
            self.calls+=1
            return json.dumps({'ok':True,'job_id':'already-accepted-job','status':'queued'})
        @self.mcp.tool()
        def primary_failure() -> str:
            raise ValueError('original tool failure')
        install_call_audit(self.mcp,self.relay)

    async def call(self,name='start_agent'):
        return await self.mcp._tool_manager.call_tool(name,{},convert_result=True)

    async def test_client_call_write_failure_preserves_converted_job_and_disallows_new_verification(self):
        with (patch.object(client_evidence,'record_call',side_effect=OSError('do-not-export-this-secret')),
              patch.object(integration_evidence,'record_call',return_value={}) as integration):
            result=await self.call()
        self.assertEqual(decoded(result)['job_id'],'already-accepted-job')
        combined=json.dumps([item.model_dump() if hasattr(item,'model_dump') else item for item in result],default=str)
        self.assertIn('AUDIT_RECORDING_FAILED',combined)
        self.assertNotIn('do-not-export-this-secret',combined)
        self.assertEqual(self.calls,1)
        self.assertFalse(integration.call_args.kwargs['success'])
        self.assertNotIn('client_session_id',integration.call_args.kwargs)

    async def test_integration_write_failure_preserves_converted_job_without_replay(self):
        with patch.object(integration_evidence,'record_call',side_effect=OSError('private-database-message')):
            result=await self.call()
        self.assertEqual(decoded(result)['job_id'],'already-accepted-job')
        text=str(result)
        self.assertIn('AUDIT_RECORDING_FAILED',text)
        self.assertIn('integration_ledger',text)
        self.assertNotIn('private-database-message',text)
        self.assertEqual(self.calls,1)

    async def test_tool_failure_is_preserved_when_both_audits_fail(self):
        with (patch.object(client_evidence,'record_call',side_effect=OSError('audit failure')),
              patch.object(integration_evidence,'record_call',side_effect=OSError('other audit failure'))):
            with self.assertRaisesRegex(Exception,'original tool failure'):
                await self.call('primary_failure')
        self.assertEqual(self.calls,0)

    async def test_success_keeps_original_converted_output(self):
        with patch.object(integration_evidence,'record_call',return_value={}):
            result=await self.call()
        self.assertEqual(decoded(result),{'ok':True,'job_id':'already-accepted-job','status':'queued'})
        self.assertNotIn('evidence_gap',str(result))
        self.assertEqual(self.calls,1)

    async def test_task_owner_does_not_upgrade_an_unknown_or_different_caller(self):
        owner=client_evidence.bind(self.store,instance=self.relay.instance,workspace=self.workspace,
            session_id='owner-chat',root_session_id='owner-chat',client_kind='desktop',confirmed=True)
        caller=client_evidence.bind(self.store,instance=self.relay.instance,workspace=self.workspace,
            session_id='reader-chat',root_session_id='reader-chat',client_kind='cli',confirmed=True)
        with self.store.connect() as db:
            db.execute('INSERT INTO task_groups VALUES (?,?)',('owned-group',json.dumps({'group_id':'owned-group','client_binding_id':owner['id']})))
        @self.mcp.tool()
        def get_task_group(group_id:str,client_binding_id:str|None=None) -> str:
            return json.dumps({'group_id':group_id,'client_binding_id':owner['id']})
        unbound=await self.mcp._tool_manager.call_tool('get_task_group',{'group_id':'owned-group'},convert_result=True)
        self.assertEqual(decoded(unbound)['group_id'],'owned-group')
        first=client_evidence.chain(self.store,workspace=self.workspace)['calls'][-1]
        self.assertEqual(first['owner_binding_id'],owner['id'])
        self.assertIsNone(first['caller_binding_id'])
        unbound_observation=self.store.setting(integration_evidence.CALLS_KEY)[-1]
        self.assertEqual(unbound_observation['client_kind'],'unknown')
        self.assertIsNone(unbound_observation['client_session_id'])
        bound=await self.mcp._tool_manager.call_tool('get_task_group',{'group_id':'owned-group','client_binding_id':caller['id']},convert_result=True)
        self.assertEqual(decoded(bound)['group_id'],'owned-group')
        second=client_evidence.chain(self.store,workspace=self.workspace)['calls'][-1]
        self.assertEqual(second['owner_binding_id'],owner['id'])
        self.assertEqual(second['caller_binding_id'],caller['id'])
        bound_observation=self.store.setting(integration_evidence.CALLS_KEY)[-1]
        self.assertEqual(bound_observation['client_kind'],'codex_cli')
        self.assertEqual(bound_observation['client_session_id'],'reader-chat')


if __name__=='__main__':
    unittest.main()
