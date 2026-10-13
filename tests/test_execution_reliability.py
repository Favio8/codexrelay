"""A05/A06 regressions: real Windows commands and deterministic model responses."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.failures import classify_error
from codexrelay.mcp_server import Relay, scrub_strings
from codexrelay.service import Service
from codexrelay.state import Store
from test_control import MemoryVault
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.bash_tool import execute_bash
from deepseek_mcp.config import Config
from deepseek_mcp.execution_scope import scoped_arguments
from deepseek_mcp.job_manager import DeepSeekJobManager
from deepseek_mcp.progress_guard import ProgressGuard
from deepseek_mcp.provider_response import ProviderResponse
from deepseek_mcp.provider_retry import AgentBudgetExceeded, AgentLoopCancelled, AgentLoopError
from deepseek_mcp.transaction_journal import acknowledge, pending_records, record_intent
from deepseek_mcp.tools import execute_tool
from deepseek_mcp.workspace_changes import snapshot, changes


def response(name=None, arguments=None, *, usage=True, prompt_tokens=100):
    message = {'role': 'assistant', 'content': 'Complete' if name is None else ''}
    if name:
        message['tool_calls'] = [{'id': 'call', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments or {})}}]
    payload = {'choices': [{'message': message, 'finish_reason': 'tool_calls' if name else 'stop'}]}
    if usage:
        payload['usage'] = {'prompt_tokens': prompt_tokens, 'completion_tokens': 20}
    return ProviderResponse.from_payload(payload)


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reliability-', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / '项目 with spaces'
        self.workspace.mkdir()
        self.config = Config(api_key='not-an-external-key', workspace=self.workspace, allowed_tools=['Read', 'Bash'])

    def test_redaction_preserves_numeric_usage_and_does_not_mask_placeholder(self):
        value = {'tokens': {'total': 1234}, 'message': 'credential=1234', 'path': 'local-project'}
        scrubbed = scrub_strings(value, '1234')
        self.assertEqual(scrubbed['tokens']['total'], 1234)
        self.assertEqual(scrubbed['message'], 'credential=[REDACTED]')
        self.assertEqual(scrub_strings(value, '')['path'], 'local-project')

    @unittest.skipUnless(os.name == 'nt', 'Windows cmd.exe regression')
    def test_cmd_preserves_quoted_path_and_compound_command(self):
        result = execute_bash({'command': f'cd /d "{self.workspace}" && echo QUOTE_OK'}, self.config)
        self.assertTrue(result.startswith('[exit 0]'), result)
        self.assertIn('QUOTE_OK', result)

    def test_direct_program_preserves_literal_arguments(self):
        script = self.workspace / 'print_args.py'
        script.write_text('import sys,json;print(json.dumps(sys.argv[1:]))', encoding='utf-8')
        values = ['with spaces', 'quote"inside', 'a&b', '中文']
        result = execute_bash({'executable': sys.executable, 'args': ['-B', str(script), *values]}, self.config)
        self.assertTrue(result.startswith('[exit 0]'), result)
        self.assertIn(json.dumps(values), result)

    def test_command_home_is_private_and_removed_after_command(self):
        script = self.workspace / 'home.py'
        script.write_text('import os,json;print(json.dumps({k:os.environ.get(k) for k in ["HOME","USERPROFILE","OPENAI_API_KEY","TEMP","TMP"]}))', encoding='utf-8')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'must-not-leak'}):
            result = execute_bash({'executable': sys.executable, 'args': ['-B', str(script)]}, self.config)
        values = json.loads(result.split('--- stdout ---\n')[1])
        home = Path(values['HOME'])
        self.assertFalse(home.is_relative_to(self.workspace))
        self.assertFalse(home.exists())
        self.assertIsNone(values['OPENAI_API_KEY'])
        self.assertEqual(values['TEMP'], str(home))
        self.assertEqual(values['TMP'], str(home))
        if os.name == 'nt':
            self.assertEqual(values['USERPROFILE'], str(home))

    def test_scope_blocks_sibling_and_roots_relative_paths_in_task(self):
        scope = self.workspace / 'task'
        scope.mkdir()
        config = replace(self.config, task_root=scope)
        (scope / 'inside.txt').write_text('inside')
        (self.workspace / 'outside.txt').write_text('outside')
        self.assertIn('inside', execute_tool('Read', {'path': 'inside.txt'}, config))
        self.assertIn('ERROR:', execute_tool('Read', {'path': '../outside.txt'}, config))
        self.assertEqual(scoped_arguments('Read', {'path': 'inside.txt'}, config)['path'], str(scope / 'inside.txt'))

    def test_scope_rejects_control_directories(self):
        control = self.workspace / '.git'
        control.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'task_root'):
            replace(self.config, task_root=control)

    def test_glob_scope_cannot_traverse_siblings(self):
        scope = self.workspace / 'task'
        scope.mkdir()
        (scope / 'inside.txt').write_text('inside')
        (self.workspace / 'outside.txt').write_text('outside')
        config = replace(self.config, task_root=scope, allowed_tools=['Glob'])
        self.assertIn('inside.txt', execute_tool('Glob', {'pattern': '*.txt'}, config))
        self.assertNotIn('outside.txt', execute_tool('Glob', {'pattern': '**/*.txt'}, config))
        self.assertIn('ERROR:', execute_tool('Glob', {'pattern': '../*.txt'}, config))

    def test_scope_is_preserved_across_real_tool_subprocess(self):
        scope = self.workspace / 'task'
        scope.mkdir()
        (scope / 'inside.txt').write_text('SUBPROCESS_SCOPE')
        config = replace(self.config, task_root=scope)
        state = loop._create_agent_state('read', config, [], loop._AgentControls(None, None, None), None)
        call = response('Read', {'path': 'inside.txt'}).choices[0].message.tool_calls[0]
        result = loop._execute_one_tool(config, call, 0, deadline=state.deadline)
        self.assertIn('SUBPROCESS_SCOPE', result)

    def test_write_allowlist_rejects_other_files(self):
        config = replace(self.config, allowed_tools=['Write'], write_paths=[Path('allowed.py')])
        denied = execute_tool('Write', {'path': 'report.md', 'content': 'unwanted'}, config)
        self.assertIn('ERROR:', denied)
        self.assertFalse((self.workspace / 'report.md').exists())
        self.assertNotIn('ERROR:', execute_tool('Write', {'path': 'allowed.py', 'content': 'value=1'}, config))

    def test_command_cwd_does_not_narrow_the_authorized_file_scope(self):
        subdirectory = self.workspace / 'sub'
        subdirectory.mkdir()
        config = replace(self.config, write_paths=[Path('allowed.py')])
        result = execute_bash({'executable': sys.executable, 'args': ['--version'], 'cwd': 'sub'}, config)
        self.assertTrue(result.startswith('[exit 0]'), result)
        self.assertEqual(config.write_paths, [self.workspace / 'allowed.py'])

    def test_third_equivalent_failed_command_is_not_executed(self):
        with patch.object(loop, '_call_with_retry', return_value=response('Bash', {'command': 'bad'})), patch.object(loop, '_execute_one_tool', return_value='[exit 1]\nunchanged failure') as tool:
            with self.assertRaises(AgentLoopError) as caught:
                loop.run_agent('Do work', self.config)
        self.assertEqual(tool.call_count, 2)
        self.assertEqual(classify_error(str(caught.exception))['code'], 'no_progress')
        self.assertEqual(caught.exception.telemetry['bash']['failure_count'], 2)
        self.assertGreater(caught.exception.telemetry['tokens']['total'], 0)

    def test_guard_allows_repair_and_repeated_success(self):
        guard = ProgressGuard()
        arguments = {'command': 'check'}
        for _ in range(2):
            guard.check('Bash', arguments, 'before')
            guard.record('Bash', arguments, 'before', '[exit 1]\nsame')
        guard.check('Bash', arguments, 'repaired')
        for _ in range(4):
            guard.record('Bash', arguments, 'repaired', '[exit 0]\nOK')
            guard.check('Bash', arguments, 'repaired')

    def test_one_revert_is_allowed_but_repeated_cycle_stops(self):
        guard = ProgressGuard()
        for digest in ('a', 'b', 'a', 'b'):
            guard.mutation('file.py', digest)
        with self.assertRaisesRegex(RuntimeError, 'NO_PROGRESS:'):
            guard.mutation('file.py', 'a')

    def test_four_version_cycle_from_a05_stops(self):
        guard = ProgressGuard()
        for digest in ('baseline', 'variant1', 'variant2', 'variant3', 'baseline', 'variant1', 'variant2', 'variant3'):
            guard.mutation('report.md', digest)
        with self.assertRaisesRegex(RuntimeError, 'NO_PROGRESS:'):
            guard.mutation('report.md', 'baseline')

    def test_budget_failure_preserves_provider_usage(self):
        huge = response(prompt_tokens=40000)
        with patch.object(loop, '_call_with_retry', return_value=huge):
            with self.assertRaises(AgentBudgetExceeded) as caught:
                loop.run_agent('Work', replace(self.config, max_total_tokens=32768))
        self.assertEqual(caught.exception.telemetry['tokens']['prompt'], 40000)
        self.assertGreaterEqual(caught.exception.telemetry['budget']['metered_tokens'], 40020)

    def test_cancelled_job_retains_known_usage_and_incomplete_request(self):
        manager = DeepSeekJobManager(lock_directory=Path(self.temp.name) / 'locks')
        with patch.object(loop, '_call_with_retry', side_effect=[response('Read', {'path': 'missing'}), AgentLoopCancelled('cancel fixture')]):
            started = manager.start('Inspect', '', self.config)
            self.assertTrue(manager.wait_for_terminal(started['job_id'], 10))
        result = manager.result(started['job_id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['result'])
        self.assertEqual(result['telemetry']['tokens']['total'], 120)
        self.assertTrue(result['telemetry']['tokens']['interrupted_request'])
        self.assertIsNotNone(manager.claim_usage_record(started['job_id']))
        manager.finish_usage_record(started['job_id'], True)
        self.assertIsNone(manager.claim_usage_record(started['job_id']))

    def test_real_command_cancel_preserves_file_and_cleans_private_home(self):
        script = self.workspace / 'wait.py'
        script.write_text('import os,json,time\nfrom pathlib import Path\nPath("started.json").write_text(json.dumps({"home":os.environ["HOME"]}))\ntime.sleep(30)', encoding='utf-8')
        manager = DeepSeekJobManager(lock_directory=Path(self.temp.name) / 'cancel-locks')
        with patch.object(loop, '_call_with_retry', return_value=response('Bash', {'executable': sys.executable, 'args': ['-B', str(script)]})):
            started = manager.start('Wait for cancellation', '', self.config)
            try:
                deadline = time.monotonic() + 10
                marker = self.workspace / 'started.json'
                while not marker.exists() or marker.stat().st_size == 0:
                    self.assertLess(time.monotonic(), deadline, 'command did not start')
                    time.sleep(.02)
                home = Path(json.loads(marker.read_text())['home'])
                self.assertTrue(home.exists())
                manager.cancel(started['job_id'])
                self.assertTrue(manager.wait_for_terminal(started['job_id'], 10))
                result = manager.result(started['job_id'])
                self.assertEqual(result['status'], 'cancelled')
                self.assertTrue(marker.exists())
                self.assertFalse(home.exists())
                self.assertEqual(result['telemetry']['active_tool']['name'], 'Bash')
                self.assertEqual(result['telemetry']['active_tool']['status'], 'interrupted')
                self.assertIn('started.json', result['telemetry']['workspace_changes']['added'])
            finally:
                manager.cancel(started['job_id'])
                manager.wait_for_terminal(started['job_id'], 10)

    def test_mutation_cycle_stops_the_actual_loop_with_current_file_preserved(self):
        target = self.workspace / 'cycle.txt'
        target.write_text('original')
        values = iter(['one', 'two', 'one', 'two', 'one', 'two'])
        config = replace(self.config, allowed_tools=['Edit'])
        def edit(*args, **kwargs):
            target.write_text(next(values))
            return 'OK: edited cycle.txt'
        with patch.object(loop, '_call_with_retry', return_value=response('Edit', {'path': 'cycle.txt'})), patch.object(loop, '_execute_one_tool', side_effect=edit) as tool:
            with self.assertRaisesRegex(AgentLoopError, 'NO_PROGRESS:') as caught:
                loop.run_agent('Change text', config)
        self.assertEqual(tool.call_count, 5)
        self.assertEqual(target.read_text(), 'one')
        self.assertIn('cycle.txt', caught.exception.telemetry['workspace_changes']['modified'])

    def test_absent_provider_usage_is_not_reported_as_zero(self):
        with patch.object(loop, '_call_with_retry', return_value=response(usage=False)):
            result = loop.run_agent('Work', self.config)
        self.assertIsNone(result['tokens']['total'])
        self.assertGreater(result['budget']['metered_tokens'], 0)
        self.assertEqual(result['verification']['status'], 'pending')

    def test_shell_file_changes_are_reported_even_after_cancel(self):
        script = self.workspace / 'mutate.py'
        script.write_text('from pathlib import Path\nPath("created.txt").write_text("new")\nPath("modified.txt").write_text("changed")\nPath("deleted.txt").unlink()')
        (self.workspace / 'modified.txt').write_text('old')
        (self.workspace / 'deleted.txt').write_text('old')
        requests = [response('Bash', {'executable': sys.executable, 'args': ['-B', str(script)]}), AgentLoopCancelled('cancel')]
        with patch.object(loop, '_call_with_retry', side_effect=requests):
            with self.assertRaises(AgentLoopCancelled) as caught:
                loop.run_agent('Change fixture', self.config)
        change = caught.exception.telemetry['workspace_changes']
        self.assertEqual(change['added'], ['created.txt'])
        self.assertEqual(change['modified'], ['modified.txt'])
        self.assertEqual(change['deleted'], ['deleted.txt'])
        self.assertEqual(caught.exception.telemetry['mutations'], [])
        self.assertEqual(caught.exception.telemetry['bash']['results'][0]['exit_code'], 0)

    def test_unlisted_shell_write_stops_with_preserved_diff(self):
        script = self.workspace / 'write.py'
        script.write_text('from pathlib import Path;Path("unlisted.md").write_text("keep for review")')
        config = replace(self.config, write_paths=[Path('allowed.py')])
        with patch.object(loop, '_call_with_retry', return_value=response('Bash', {'executable': sys.executable, 'args': ['-B', str(script)]})):
            with self.assertRaisesRegex(AgentLoopError, 'TASK_SCOPE_VIOLATION:') as caught:
                loop.run_agent('Format Python', config)
        self.assertEqual(caught.exception.telemetry['workspace_changes']['out_of_scope'], ['unlisted.md'])
        self.assertTrue((self.workspace / 'unlisted.md').exists())

    def test_unreadable_folder_is_not_falsely_reported_as_deleted(self):
        folder = self.workspace / 'locked'
        folder.mkdir()
        (folder / 'existing.txt').write_text('existing')
        before = snapshot(self.workspace)
        with patch('deepseek_mcp.workspace_changes.file_metadata', return_value=({}, ['locked'])):
            observed = changes(self.workspace, before)
        self.assertFalse(observed['complete'])
        self.assertEqual(observed['deleted'], [])
        self.assertEqual(observed['unreadable'], ['locked'])

    def test_recovery_distinguishes_changed_and_missing(self):
        path = self.workspace / 'record.txt'
        path.write_text('first')
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        record = record_intent(self.config, 'a' * 32, 'Write', {'path': 'record.txt'}, digest)
        self.addCleanup(lambda: acknowledge(self.config, [record['transaction_id']]))
        self.assertEqual(pending_records(self.config)[0]['observed_state'], 'matches')
        path.write_text('second')
        changed = pending_records(self.config)[0]
        self.assertEqual(changed['observed_state'], 'changed')
        self.assertNotEqual(changed['current_sha256'], changed['sha256'])
        path.unlink()
        self.assertEqual(pending_records(self.config)[0]['observed_state'], 'missing')

    def test_task_budget_and_root_are_frozen_at_admission(self):
        scope = self.workspace / 'task'
        scope.mkdir()
        store = Store(Path(self.temp.name) / 'state')
        vault = MemoryVault()
        service = Service(store, vault)
        service.save_provider(name='fixture', kind='custom', base_url='http://127.0.0.1:1234/v1', flash_model='fixture', pro_model='fixture')
        store.set_setting('enabled', True)
        captured = []
        class Manager:
            def start(self, task, context, config, *, progress_reporter=None):
                captured.append(config)
                return {'job_id': 'internal'}
        relay = Relay(store, vault, self.workspace, Manager())
        with patch.object(threading.Thread, 'start'):
            started = relay.start('Work', '', 'coding', 'flash', 32768, 'task', ['allowed.py'])
        self.assertEqual(captured[0].max_total_tokens, 32768)
        self.assertEqual(captured[0].task_root, scope)
        self.assertEqual(captured[0].write_paths, [scope / 'allowed.py'])
        self.assertEqual(store.task(started['job_id'])['token_budget'], 32768)
