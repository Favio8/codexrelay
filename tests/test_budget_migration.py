import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from test_control import MemoryVault
from codexrelay.service import Service
from codexrelay.state import Store
from codexrelay import legacy
from codexrelay.failures import classify_error
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.config import Config
from deepseek_mcp.history_compaction import compact_history
from deepseek_mcp.provider_response import ProviderResponse
from deepseek_mcp.provider_retry import AgentBudgetExceeded


def exchange(index, content='x' * 24000):
    return [{'role':'assistant','content':'inspect','tool_calls':[{'id':f't{index}','type':'function','function':{'name':'Read','arguments':json.dumps({'path':f'file-{index}.txt'})}}]},
            {'role':'tool','tool_call_id':f't{index}','content':content}]


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.config = Config(api_key='fixture', workspace=self.workspace, allowed_tools=['Read'], max_total_tokens=1_000_000)

    def state(self):
        return loop._create_agent_state('Do the requested work', self.config, [], loop._AgentControls(None,None,None), None)

    def test_uses_remaining_output_instead_of_reserving_full_maximum(self):
        state=self.state();state.budget_tokens=990000
        allowed=loop._ensure_request_budget(state,4000)
        self.assertEqual(allowed,9000)
        self.assertLess(allowed,self.config.max_output_tokens)

    def test_input_reservation_shrinks_after_compaction(self):
        state=self.state();state.budget_tokens=960000
        state.last_prompt_tokens=80000;state.last_request_bytes=320000
        self.assertEqual(loop._ensure_request_budget(state,80000),16384)

    def test_real_exhaustion_still_stops_with_local_metrics(self):
        state=self.state();state.budget_tokens=999800
        with self.assertRaisesRegex(AgentBudgetExceeded,'LOCAL_TOKEN_BUDGET used=999800 limit=1000000'):
            loop._ensure_request_budget(state,4000)

    def test_preserves_instructions_recent_tools_and_incomplete_exchanges(self):
        initial=[{'role':'system','content':'Do not modify secrets'},{'role':'user','content':'Original acceptance criteria'}]
        messages=initial+sum((exchange(i) for i in range(9)),[])
        messages.insert(6,{'role':'user','content':'Steering: modify only calculator.py'})
        messages+=exchange(10)[:1]
        compacted,count=compact_history(messages)
        self.assertEqual(count,5)
        self.assertEqual([m for m in compacted if m['role'] in {'system','user'}],[m for m in messages if m['role'] in {'system','user'}])
        self.assertEqual(compacted[-9:],messages[-9:])
        self.assertLess(len(json.dumps(compacted)),len(json.dumps(messages)))

    def test_complete_multiturn_work_that_exhausted_old_history(self):
        def provider(config,messages,tools,turn,**kwargs):
            prompt=loop._estimate_tokens(loop._encoded_size({'messages':messages,'tools':tools},ensure_ascii=False))
            message=exchange(turn)[0] if turn<24 else {'role':'assistant','content':'FINISHED_ALL_24_READS'}
            return ProviderResponse.from_payload({'choices':[{'message':message,'finish_reason':'tool_calls' if turn<24 else 'stop'}],
                                                 'usage':{'prompt_tokens':prompt,'completion_tokens':40}})
        with patch.object(loop,'_call_with_retry',side_effect=provider),patch.object(loop,'_execute_one_tool',return_value='result\n'+'x'*24000):
            with patch.object(loop,'_compact_history',return_value=None):
                with self.assertRaises(AgentBudgetExceeded):loop.run_agent('Read 24 reports',self.config)
            result=loop.run_agent('Read 24 reports',self.config)
        self.assertEqual(result['final_message'],'FINISHED_ALL_24_READS')
        self.assertEqual(result['tool_calls'],24)
        self.assertGreater(result['budget']['history_compactions'],0)
        self.assertLess(result['budget']['metered_tokens'],1_000_000)
        print('Long-run regression:',json.dumps(result['budget']))

    def test_local_and_provider_errors_are_not_conflated(self):
        self.assertEqual(classify_error('run token budget exhausted')['code'],'local_token_budget')
        self.assertEqual(classify_error('category=quota status=402')['code'],'provider_quota')
        self.assertEqual(classify_error('category=rate_limit status=429')['code'],'provider_rate_limit')

    def test_config_rejects_unbounded_budget(self):
        with self.assertRaises(RuntimeError):Config(api_key='x',workspace=self.workspace,allowed_tools=[],max_total_tokens=0)

    def test_native_windows_shell_is_explicit(self):
        self.assertIn('cmd.exe',self.state().messages[0]['content'])
        self.assertIn('not POSIX Bash or PowerShell',self.state().messages[0]['content'])

    def test_thinking_history_keeps_every_assistant_protocol_field(self):
        messages=[{'role':'system','content':'Constraint'},{'role':'user','content':'Task'}]
        for index in range(9):
            group=exchange(index)
            group[0]['reasoning_content']='opaque-reasoning-'+str(index)
            messages.extend(group)
        compacted,count=compact_history(messages)
        self.assertEqual(count,5)
        self.assertEqual([m for m in compacted if m['role']=='assistant'],[m for m in messages if m['role']=='assistant'])
        self.assertEqual([m['tool_call_id'] for m in compacted if m['role']=='tool'],[m['tool_call_id'] for m in messages if m['role']=='tool'])
        self.assertEqual(compacted[-8:],messages[-8:])
        self.assertLess(len(json.dumps(compacted)),len(json.dumps(messages)))
        again,changed=compact_history(compacted)
        self.assertEqual(changed,0)
        self.assertEqual(again,compacted)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp');self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.codex=root/'codex';self.codex.mkdir()
        (self.codex/'config.toml').write_text('model="preserve"\n[mcp_servers.deepseek]\ncommand="C:\\\\old\\\\deepseek-mcp.exe"\n',encoding='utf-8')
        self.config=root/'legacy.json'
        self.config.write_text(json.dumps({'flash':'old-fast','pro':'old-pro','flash_reasoning_effort':'low','pro_reasoning_effort':'high','max_turns':30}),encoding='utf-8')
        self.service=Service(Store(root/'state'),MemoryVault())
        self.addCleanup(patch.stopall)
        patch('codexrelay.legacy.configuration_path',return_value=self.config).start()
        patch('codexrelay.legacy.credential_environment',return_value='existing-fake-key').start()

    def test_preview_never_exposes_key(self):
        info=legacy.preview(str(self.codex))
        self.assertTrue(info['credential_available'])
        self.assertNotIn('existing-fake-key',json.dumps(info))

    def test_import_preserves_models_limits_and_moves_key_to_vault(self):
        result=legacy.adopt(self.service,str(self.codex))
        provider=self.service.store.provider()
        self.assertEqual(provider['flash_model'],'old-fast');self.assertEqual(provider['pro_model'],'old-pro')
        self.assertEqual(provider['max_turns'],30);self.assertEqual(provider['max_total_tokens'],1000000)
        self.assertEqual(provider['pro_reasoning_effort'],'high');self.assertEqual(provider['reasoning_effort'],'low')
        self.assertEqual(self.service.vault.get(provider['credential_id']),'existing-fake-key')
        self.assertNotIn(b'existing-fake-key',self.service.store.path.read_bytes())
        config=(self.codex/'config.toml').read_text(encoding='utf-8')
        self.assertIn('model="preserve"',config);self.assertNotIn('mcp_servers.deepseek]',config);self.assertIn('mcp_servers.codexrelay',config)
        self.assertTrue(result['restart_required'])
        self.assertTrue(legacy.adopt(self.service,str(self.codex))['already_imported'])
        self.assertEqual(len(self.service.store.providers()),1)

    def test_import_rolls_back_new_provider_on_registration_failure(self):
        original=(self.codex/'config.toml').read_bytes()
        with patch('codexrelay.legacy.install',side_effect=ValueError('conflict')):
            with self.assertRaisesRegex(ValueError,'conflict'):legacy.adopt(self.service,str(self.codex))
        self.assertEqual(self.service.store.providers(),[]);self.assertEqual(self.service.vault.values,{})
        self.assertEqual((self.codex/'config.toml').read_bytes(),original)

    def test_custom_provider_requests_only_its_own_key_variable(self):
        self.config.write_text(json.dumps({'base_url':'https://custom.example/v1'}))
        with patch('codexrelay.legacy.credential_environment',return_value='custom-key') as reader:
            legacy.preview(str(self.codex))
        reader.assert_called_once_with('OPENAI_API_KEY')

    def test_migration_preserves_host_tool_restrictions(self):
        config=self.codex/'config.toml'
        config.write_text(config.read_text(encoding='utf-8')+'disabled_tools=["delegate_to_deepseek"]\ndefault_tools_approval_mode="prompt"\n',encoding='utf-8')
        legacy.adopt(self.service,str(self.codex))
        import tomlkit
        migrated=tomlkit.parse(config.read_text(encoding='utf-8'))['mcp_servers']['codexrelay']
        self.assertEqual(list(migrated['disabled_tools']),['delegate_to_deepseek'])
        self.assertEqual(migrated['default_tools_approval_mode'],'prompt')


if __name__=='__main__':unittest.main()
