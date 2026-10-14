"""M1-M3 contracts with real file tools/Pi scheduling and fixed provider responses."""
import concurrent.futures
import hashlib
import json
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
from codexrelay import task_groups as groups
from codexrelay.backends import pi, review, recovery
from codexrelay.mcp_server import Relay
from codexrelay.service import Service
from codexrelay.state import Store
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.config import Config
from deepseek_mcp.execution_context import complete_exchange, summarize, SUMMARY_FIELDS
from deepseek_mcp.provider_response import ProviderResponse
from deepseek_mcp.tools import execute_tool
from test_control import MemoryVault
from test_execution_reliability import response


def summary_response():
    result = {key: [] for key in SUMMARY_FIELDS}
    result.update(goal='focused goal', facts=['Observed tools; verify independently'], remaining=['remaining step'])
    return ProviderResponse.from_payload({'choices': [{'message': {'role': 'assistant', 'content': json.dumps(result)}, 'finish_reason': 'stop'}],
                                          'usage': {'prompt_tokens': 500, 'completion_tokens': 100}})


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='optimization-', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / '项目 with spaces'; self.workspace.mkdir()
        (self.workspace / 'input.txt').write_text('input evidence', encoding='utf-8')
        self.config = Config(api_key='fixture', workspace=self.workspace, allowed_tools=['Read'], delegation_capability='readonly')
        self.store = Store(self.root / 'state'); self.service = Service(self.store, MemoryVault())
        self.provider = self.service.save_provider(name='fixture', base_url='http://localhost:1', flash_model='fixture', pro_model='fixture-pro')
        self.store.set_setting('enabled', True)
        self.relay = Relay(self.store, self.service.vault, self.workspace)
        guard = patch.object(review, 'DIRECTORY', self.root / 'reviews'); guard.start(); self.addCleanup(guard.stop)

    def wait(self, job):
        until = time.monotonic() + 20
        while time.monotonic() < until:
            task = self.store.task(job['job_id'])
            if task['status'] not in groups.ACTIVE: return task
            time.sleep(.02)
        self.fail('run did not exit')

    def start(self, backend='deepseek', **values):
        self.store.set_setting('active_backend', backend)
        with patch.object(loop, '_call_with_retry', side_effect=[response('Read', {'path': 'input.txt'}), response()]):
            return self.wait(self.relay.start('Read input', '', 'readonly', 'flash', **values))

    def verify(self, task, remaining=('remaining step',), **values):
        meta = task['checkpoint']
        return groups.verify(self.store, task, True, 'Parent read actual input and checked tool evidence',
            'verified_partial' if remaining else 'verified_complete', meta['checkpoint_id'], meta['sha256'],
            completed_items=['input inspected'], remaining_items=list(remaining), checked_files=['input.txt'], **values)['verification']

    def resume(self, task, verification, key='once', **values):
        return self.relay.resume(task['group_id'], task['checkpoint']['checkpoint_id'], task['checkpoint']['sha256'],
                                 verification['id'], '\n'.join(verification['remaining_items']), key, **values)

    def test_read_single_line_exact_character_pagination(self):
        text = '{"payload":"' + '中x' * 22000 + '"}'
        (self.workspace / 'large.json').write_text(text, encoding='utf-8')
        args = {'path': 'large.json'}; pages = []
        while True:
            output = execute_tool('Read', args, self.config)
            header, _, page = output.partition('\n')
            meta = json.loads(header[len('[Read range '):-1]); pages.append(page)
            self.assertLessEqual(meta['returned_chars'], 12000)
            self.assertEqual(meta['total_bytes'], len(text.encode()))
            if not meta['next_read']: break
            args = meta['next_read']
        self.assertEqual(''.join(pages), text)

    def test_read_multiline_bounds_and_explicit_full_remain_bounded(self):
        text = 'a\n' * 600
        (self.workspace / 'lines.txt').write_bytes(text.encode())
        output = execute_tool('Read', {'path': 'lines.txt'}, self.config)
        meta = json.loads(output.partition('\n')[0][len('[Read range '):-1])
        self.assertEqual(meta['next_read']['offset'], 200)
        self.assertEqual(meta['total_lines'], 600)
        output = execute_tool('Read', {'path': 'lines.txt', 'full': True}, self.config)
        self.assertEqual(output.partition('\n')[2], text)

    def test_long_line_preview_points_to_search_and_respects_explicit_range(self):
        text = 'x' * 30000 + 'target_flag=false'
        (self.workspace / 'minified.txt').write_text(text)
        output = execute_tool('Read', {'path': 'minified.txt'}, self.config)
        meta = json.loads(output.partition('\n')[0][len('[Read range '):-1])
        self.assertEqual(meta['returned_chars'], 2000)
        self.assertIn('Grep', meta['search_hint'])
        output = execute_tool('Read', {'path': 'minified.txt', 'char_offset': 29995, 'char_limit': 100}, self.config)
        self.assertEqual(output.partition('\n')[2], text[29995:])

    def test_minified_edit_and_focused_verification_fit_low_budget_both_backends(self):
        for backend, runner in (('deepseek', loop.run_agent), ('pi', pi.run_pi)):
            workspace = self.workspace / backend; workspace.mkdir()
            original = json.dumps({'items': ['x' * 30] * 900, 'enabled': False})
            (workspace / 'data.json').write_text(original)
            config = replace(self.config, workspace=workspace, expected_workspace_identity=None, delegation_capability='coding',
                             allowed_tools=['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep', 'NotebookEdit'],
                             max_total_tokens=32768, max_output_tokens=2048, reasoning_effort='high',
                             write_paths=[workspace / 'data.json'])
            seen = []
            def provider(profile, messages, tools, *args, **kwargs):
                self.assertTrue(complete_exchange(messages))
                seen.append(messages[-1].get('content', ''))
                steps = [response('Read', {'path': 'data.json'}),
                         response('Grep', {'path': 'data.json', 'pattern': '"enabled"'}),
                         response('Edit', {'path': 'data.json', 'old_string': '"enabled": false', 'new_string': '"enabled": true'}),
                         response('Grep', {'path': 'data.json', 'pattern': '"enabled"'}), response()]
                raw = steps[len(seen) - 1].model_dump(exclude_none=True)
                raw['choices'][0]['message']['reasoning_content'] = 'opaque thinking trace'
                raw['usage'] = {'prompt_tokens': loop._estimate_tokens(loop._encoded_size({'model': profile.model, 'messages': messages, 'tools': tools}, ensure_ascii=False)), 'completion_tokens': 150}
                return ProviderResponse.from_payload(raw)
            with patch.object(loop, '_call_with_retry', side_effect=provider):
                result = runner('Change only enabled from false to true in data.json; preserve every item.', config)
            self.assertEqual((workspace / 'data.json').read_text(), original.replace('"enabled": false', '"enabled": true'))
            self.assertIn('search_hint', seen[1]); self.assertIn('"enabled": false', seen[2])
            self.assertIn('"enabled": true', seen[4])
            self.assertLess(result['budget']['metered_tokens'], 32768)
            self.assertEqual(len(result['mutations']), 1)
            self.assertTrue(result['final_message'])

    def test_budget_feedback_reserves_followup_requests_before_eighty_percent(self):
        from deepseek_mcp.execution_context import prepare
        config = replace(self.config, max_total_tokens=32768, max_output_tokens=2048)
        state = loop._create_agent_state('focused edit', config, [], loop._AgentControls(None, None, None), None)
        state.messages += [{'role': 'assistant', 'tool_calls': [{'id': 'read', 'function': {'name': 'Read', 'arguments': '{}'}}]},
                           {'role': 'tool', 'tool_call_id': 'read', 'content': 'data ' * 5000}]
        state.budget_tokens = 16000
        prepare(state); prepare(state)
        notices = [m for m in state.messages if m.get('content', '').startswith('Budget feedback:')]
        self.assertEqual(len(notices), 1)
        self.assertIn('history is charged again', notices[0]['content'])
        self.assertEqual(state.budget_tokens, 16000)
        self.assertEqual(state.config.max_total_tokens, 32768)

    def test_glob_pages_report_known_remaining(self):
        for index in range(105): (self.workspace / f'f{index:03}.txt').write_text('')
        config = replace(self.config, allowed_tools=['Glob'])
        first = execute_tool('Glob', {'pattern': '*.txt'}, config)
        self.assertIn('remaining_known=6', first)
        second = execute_tool('Glob', {'pattern': '*.txt', 'offset': 100}, config)
        self.assertIn('remaining_known=0', second)

    def test_repeat_read_feedback_is_not_no_progress(self):
        with patch.object(loop, '_call_with_retry', side_effect=[response('Read', {'path': 'input.txt'}), response('Read', {'path': 'input.txt'}), response()]):
            result = loop.run_agent('Inspect twice', self.config)
        self.assertEqual(result['progress']['repeated_reads'], 1)
        self.assertEqual(result['tool_calls'], 2)

    def test_changed_file_and_fresh_purpose_do_not_reuse_stale_content(self):
        seen = []
        def provider(config, messages, *args, **kwargs):
            seen.append(messages[-1].get('content', ''))
            if len(seen) == 2: (self.workspace / 'input.txt').write_text('changed')
            return response('Read', {'path': 'input.txt', 'purpose': 'verify'}) if len(seen) < 3 else response()
        with patch.object(loop, '_call_with_retry', side_effect=provider): result = loop.run_agent('Verify', self.config)
        self.assertIn('changed', seen[-1]); self.assertEqual(result['progress']['repeated_reads'], 0)

    def test_epoch_preserves_reasoning_protocol_budget_and_deadline(self):
        state = loop._create_agent_state('fixed goal', self.config, [], loop._AgentControls(None, None, None), None)
        state.messages += [{'role': 'assistant', 'content': '', 'reasoning_content': 'opaque' * 9000,
                            'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'Read', 'arguments': '{}'}}]},
                           {'role': 'tool', 'tool_call_id': 'a', 'content': 'read data'}]
        state.budget_tokens = 700; state.tool_calls = 3
        deadline = state.deadline
        with patch.object(loop, '_call_with_retry', return_value=summary_response()): self.assertTrue(summarize(state, 'test'))
        self.assertTrue(complete_exchange(state.messages)); self.assertEqual(state.context_epoch, 1)
        self.assertEqual(state.budget_tokens, 700 + max(500, loop._estimate_tokens(state.last_request_bytes)) + 100)
        self.assertIs(state.deadline, deadline); self.assertEqual(state.tool_calls, 3)
        self.assertEqual(state.usage_events[-1]['request_kind'], 'summary')
        self.assertIn('fixed goal', json.dumps(state.messages))

    def test_summary_failure_keeps_original_context_and_machine_evidence(self):
        state = loop._create_agent_state('goal', self.config, [], loop._AgentControls(None, None, None), None)
        original = list(state.messages)
        with patch.object(loop, '_call_with_retry', return_value=response()): self.assertFalse(summarize(state, 'test'))
        self.assertEqual(state.messages, original); self.assertIn('semantic_summary_unavailable', state.summary_gaps)

    def test_summary_over_budget_does_not_send_request(self):
        state = loop._create_agent_state('goal', replace(self.config, max_total_tokens=32768), [], loop._AgentControls(None, None, None), None)
        state.budget_tokens = 32760
        with patch.object(loop, '_call_with_retry') as provider: self.assertFalse(summarize(state, 'test'))
        provider.assert_not_called()

    def test_incomplete_tool_batch_prevents_rebuild(self):
        state = loop._create_agent_state('goal', self.config, [], loop._AgentControls(None, None, None), None)
        state.messages.append({'role': 'assistant', 'tool_calls': [{'id': 'a'}]})
        with patch.object(loop, '_call_with_retry') as provider: self.assertFalse(summarize(state, 'test'))
        provider.assert_not_called()

    def test_two_backends_sync_semantic_epochs_and_use_same_usage_pipeline(self):
        (self.workspace / 'long.txt').write_text('x' * 22000)
        for runner in (loop.run_agent, pi.run_pi):
            calls = []; summary_calls = []
            def provider(config, messages, tools, *args, **kwargs):
                self.assertTrue(complete_exchange(messages))
                if not tools:
                    summary_calls.append(1); return summary_response()
                calls.append(len(json.dumps(messages)))
                return response('Read', {'path': 'long.txt', 'char_limit': 12000}) if len(calls) < 6 else response()
            config = replace(self.config, execution_options={'unknown_context_bytes': 16000})
            with patch.object(loop, '_call_with_retry', side_effect=provider): result = runner('Read long file repeatedly', config)
            self.assertTrue(summary_calls); self.assertGreater(result['progress']['context_epoch'], 0)
            self.assertEqual(sum(e['request_kind'] == 'summary' for e in result['usage_events']), len(summary_calls))
            if runner is pi.run_pi:
                self.assertLessEqual(max(result['progress']['pi_context_messages']), 9, 'Pi scheduler itself must discard archived messages')

    def test_default_override_budget_sources_both_backends(self):
        for backend in ('deepseek', 'pi'):
            task = self.start(backend)
            self.assertEqual(task['token_budget'], 1000000); self.assertEqual(task['budget_source'], 'provider_default')
            task = self.start(backend, token_budget=40000)
            self.assertEqual(task['token_budget'], 40000); self.assertEqual(task['budget_source'], 'codex_override')

    def test_completed_is_pending_not_accepted(self):
        task = self.start()
        self.assertEqual(task['verification']['status'], 'pending')
        self.assertEqual(groups.get(self.store, task['group_id'])['status'], 'awaiting_verification')

    def test_partial_verification_stale_version_and_real_evidence(self):
        task = self.start(); meta = task['checkpoint']
        with self.assertRaisesRegex(ValueError, 'VERSION_STALE'):
            groups.verify(self.store, task, True, 'checked', 'verified_partial', meta['checkpoint_id'], 'wrong', remaining_items=['remaining'])
        self.verify(task)
        self.assertEqual(groups.get(self.store, task['group_id'])['status'], 'ready_to_continue')

    def test_related_change_invalidates_unrelated_change_does_not(self):
        task = self.start(); verification = self.verify(task)
        (self.workspace / 'unrelated.txt').write_text('unrelated')
        body = groups.read_checkpoint(self.store, task['checkpoint']['checkpoint_id'])
        self.assertEqual(groups.stale_files(body), [])
        (self.workspace / 'input.txt').write_text('external edit')
        with self.assertRaisesRegex(ValueError, 'EVIDENCE_STALE'): self.resume(task, verification)

    def test_four_rounds_persist_usage_and_fifth_requires_authorization(self):
        task = self.start(); group_id = task['group_id']
        for index in range(3):
            verification = self.verify(task)
            with patch.object(loop, '_call_with_retry', return_value=response()): task = self.wait(self.resume(task, verification, str(index)))
            self.assertEqual(task['round_index'], index + 2)
        verification = self.verify(task)
        group = groups.get(Store(self.store.directory), group_id)
        self.assertEqual(group['used_continuations'], 3); self.assertEqual(len(group['runs']), 4)
        self.assertEqual(group['usage_summary']['reported_tokens'], 600)
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_REQUIRED'): self.resume(task, verification, 'fifth')

    def test_resume_idempotency_and_request_conflict(self):
        task = self.start(); verification = self.verify(task)
        with patch.object(loop, '_call_with_retry', return_value=response()): first = self.wait(self.resume(task, verification))
        second = self.resume(task, verification)
        self.assertEqual(first['id'], second['job_id'])
        with self.assertRaisesRegex(ValueError, 'conflict'): self.resume(task, verification, model='pro')

    def test_defaults_are_frozen_and_app_extensions_are_versioned_idempotent(self):
        task = self.start(); original = groups.get(self.store, task['group_id'])
        groups.set_defaults(self.store, max_auto_continuations=1)
        self.assertEqual(groups.get(self.store, task['group_id'])['max_auto_continuations'], 3)
        next_task = self.start(); self.assertEqual(groups.get(self.store, next_task['group_id'])['max_auto_continuations'], 1)
        extended = groups.extend(self.store, original['group_id'], original['version'], 'grant', 1, 2000000)
        self.assertEqual(extended['max_auto_continuations'], 4)
        self.assertEqual(groups.extend(self.store, original['group_id'], original['version'], 'grant', 1, 2000000), extended)
        with self.assertRaisesRegex(ValueError, 'STALE'): groups.extend(self.store, original['group_id'], original['version'], 'different', 1)
        self.assertEqual(self.store.task(task['id'])['token_budget'], 1000000)

    def test_authorized_budget_applies_only_to_new_round_and_cannot_exceed_it(self):
        task = self.start(); verification = self.verify(task); group = groups.get(self.store, task['group_id'])
        with self.assertRaisesRegex(ValueError, 'provider task limit'):
            with patch.object(loop, '_call_with_retry'): self.resume(task, verification, 'high', token_budget=2000000)
        groups.extend(self.store, group['group_id'], group['version'], 'budget', 0, 2000000)
        with patch.object(loop, '_call_with_retry', return_value=response()): next_task = self.wait(self.resume(task, verification))
        self.assertEqual(next_task['token_budget'], 2000000); self.assertEqual(next_task['budget_source'], 'user_task_authorization')
        self.assertEqual(self.store.task(task['id'])['token_budget'], 1000000)

    def test_authorization_preview_uses_selected_future_provider_budget(self):
        task = self.start()
        self.service.save_provider(id=self.provider['id'], name='fixture', base_url='http://localhost:1', flash_model='fixture', pro_model='fixture', max_total_tokens=2000000)
        group = groups.get(self.store, task['group_id'])
        self.assertEqual(group['next_effective_budget'], 2000000)
        self.assertEqual(self.store.task(task['id'])['token_budget'], 1000000)

    def test_switch_project_cancel_and_wrong_remaining_contract_block_resume(self):
        task = self.start(); verification = self.verify(task)
        self.store.set_setting('enabled', False)
        with self.assertRaisesRegex(ValueError, 'DISABLED'): self.resume(task, verification)
        self.store.set_setting('enabled', True); self.store.set_project(self.workspace, True)
        with self.assertRaisesRegex(ValueError, 'PROJECT_DISABLED'): self.resume(task, verification)
        self.store.set_project(self.workspace, False); self.relay.cancel_group(task['group_id'])
        with self.assertRaisesRegex(ValueError, 'CANCELLED'): self.resume(task, verification)
        self.assertEqual(groups.get(self.store, task['group_id'])['used_continuations'], 0)

    def test_switch_backend_model_preserves_group_and_contract(self):
        task = self.start(); verification = self.verify(task)
        self.store.set_setting('active_backend', 'pi')
        with patch.object(loop, '_call_with_retry', return_value=response()): next_task = self.wait(self.resume(task, verification, model='pro'))
        self.assertEqual(next_task['backend_id'], 'pi'); self.assertEqual(next_task['model'], 'fixture-pro')
        self.assertEqual(next_task['group_id'], task['group_id']); self.assertEqual(next_task['round_index'], 2)

    def test_task_kind_contracts_and_parent_completion(self):
        with self.assertRaisesRegex(ValueError, 'readonly'): self.relay.create_group('Review', 'review', 'coding')
        task = self.start(task_kind='review'); self.assertEqual(task['task_kind'], 'review')
        self.verify(task, remaining=(), parent_completed=True)
        group = groups.get(self.store, task['group_id']); self.assertEqual(group['status'], 'completed_by_parent')
        self.assertEqual(len(group['runs']), 1)

    def test_parent_completion_can_bind_rechecked_changed_files_without_new_run(self):
        with patch.object(loop, '_call_with_retry', side_effect=[response('Read', {'path': 'input.txt'}), response()]):
            task = self.wait(self.relay.start('Finish scoped input', '', 'coding', 'flash', write_paths=['input.txt']))
        before = groups.get(self.store, task['group_id'])['usage_summary']['reported_tokens']
        (self.workspace / 'input.txt').write_text('finished by parent')
        verdict = self.verify(task, remaining=(), parent_completed=True)
        self.assertTrue(verdict['parent_rechecked_changes'])
        group = groups.get(self.store, task['group_id']); self.assertEqual(group['status'], 'completed_by_parent')
        self.assertEqual(len(group['run_ids']), 1); self.assertEqual(group['usage_summary']['reported_tokens'], before)

    def test_snapshot_is_lightweight_and_checkpoint_body_is_on_demand(self):
        task = self.start()
        snapshot = self.store.snapshot()['tasks'][0]
        self.assertIsNone(snapshot['result']); self.assertNotIn('tool_evidence', snapshot['telemetry']['progress'])
        body = groups.read_checkpoint(Store(self.store.directory), task['checkpoint']['checkpoint_id'])
        self.assertTrue(body['progress']['tool_evidence']); self.assertTrue(body['files'])
        archive = groups.read_checkpoint(self.store, task['checkpoint']['checkpoint_id'], include_context=True)
        self.assertTrue(archive['context_archive_messages'])
        self.assertTrue(complete_exchange(archive['context_archive_messages']))

    def test_testing_cannot_mutate_source_even_with_coding_label(self):
        with patch.object(loop, '_call_with_retry', side_effect=[response('Edit', {'path': 'input.txt', 'old_string': 'input', 'new_string': 'bad'}), response()]):
            task = self.wait(self.relay.start('Test only', '', 'coding', 'flash', task_kind='testing'))
        self.assertEqual((self.workspace / 'input.txt').read_text(), 'input evidence')
        self.assertEqual(task['write_paths'], [])

    def test_read_grep_single_line_snippet_includes_late_match(self):
        (self.workspace / 'tail.json').write_text('x' * 20000 + 'target_flag=false')
        result = execute_tool('Grep', {'path': 'tail.json', 'pattern': 'target_flag'}, replace(self.config, allowed_tools=['Grep']))
        self.assertIn('target_flag=false', result); self.assertLess(len(result), 2500)

    def test_retry_failures_are_metered_before_next_attempt(self):
        from deepseek_mcp import provider_retry
        state = loop._create_agent_state('Read', self.config, [], loop._AgentControls(None, None, None), None)
        with patch.object(provider_retry, '_request_once', side_effect=[(None, 'category=connection', True), (response(), '', False)]), patch.object(provider_retry, '_backoff'):
            loop._provider_request(state, 0)
        self.assertEqual(len(state.usage_events), 2)
        self.assertFalse(state.usage_events[0]['available'])
        self.assertGreater(state.budget_tokens, 120)

    def test_truncated_summary_response_keeps_known_usage_without_installing_partial_context(self):
        from deepseek_mcp.provider_process import _decode_response
        raw = {'kind': 'ok', 'response': {'choices': [{'message': {'role': 'assistant', 'content': 'partial'}, 'finish_reason': 'length'}],
                                          'usage': {'prompt_tokens': 700, 'completion_tokens': 300}}}
        parsed, _, _ = _decode_response(json.dumps(raw).encode())
        state = loop._create_agent_state('goal', self.config, [], loop._AgentControls(None, None, None), None)
        original = list(state.messages)
        with patch.object(loop, '_call_with_retry', return_value=parsed): self.assertFalse(summarize(state, 'truncated'))
        self.assertEqual(state.prompt_tokens + state.completion_tokens, 1000)
        self.assertEqual(state.messages, original); self.assertTrue(state.usage_events[0]['available'])

    def test_summary_ordinary_profile_keeps_execution_model_and_budget_frozen(self):
        config = replace(self.config, reasoning_effort='high')
        state = loop._create_agent_state('goal', config, [], loop._AgentControls(None, None, None), None)
        with patch.object(loop, '_call_with_retry', return_value=summary_response()) as provider:
            loop._provider_request(state, 0, messages=state.messages, tools=[], request_kind='summary')
        request = provider.call_args.args[0]
        self.assertEqual(request.model, config.model); self.assertEqual(request.reasoning_effort, 'none')
        self.assertEqual(state.config.reasoning_effort, 'high'); self.assertEqual(request.max_total_tokens, config.max_total_tokens)
        self.assertEqual(state.usage_events[0]['reasoning_effort'], 'none')

    def test_provider_context_rejection_category_survives_envelope(self):
        from deepseek_mcp.provider_process import _decode_response
        _, summary, retryable = _decode_response(json.dumps({'kind': 'error', 'summary': 'category=context_length status=400', 'retryable': False}).encode())
        self.assertIn('context_length', summary); self.assertFalse(retryable)

    def test_two_concurrent_grants_only_apply_once(self):
        task = self.start(); group = groups.get(self.store, task['group_id'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(groups.extend, self.store, group['group_id'], group['version'], 'same-grant', 1) for _ in range(2)]
            results = [f.result() for f in futures]
        self.assertEqual(results[0]['max_auto_continuations'], 4); self.assertEqual(results[0], results[1])

    def test_documentation_rejects_unauthorized_outputs(self):
        with patch.object(loop, '_call_with_retry', side_effect=[response('Write', {'path': 'wrong.md', 'content': 'wrong'}), response()]):
            task = self.wait(self.relay.start('Write report', '', 'coding', 'flash', task_kind='documentation', write_paths=['report.md']))
        self.assertFalse((self.workspace / 'wrong.md').exists()); self.assertEqual(task['task_kind'], 'documentation')

    def test_summary_without_actual_evidence_cannot_authorize_continuation(self):
        with patch.object(loop, '_call_with_retry', return_value=response()): task = self.wait(self.relay.start('Claim complete', '', 'readonly', 'flash'))
        meta = task['checkpoint']
        with self.assertRaisesRegex(ValueError, 'NEEDS_MORE_EVIDENCE'):
            groups.verify(self.store, task, True, 'Model claimed completion', 'verified_partial', meta['checkpoint_id'], meta['sha256'], remaining_items=['remaining'])

    def test_context_model_window_is_frozen_and_unknown_is_not_fabricated(self):
        self.assertIsNone(self.config.context_window)
        self.service.save_provider(id=self.provider['id'], name='fixture', base_url='http://localhost:1', flash_model='fixture', pro_model='fixture', flash_context_window=64000)
        task = self.start(); self.assertEqual(task['context_window'], 64000)
        self.service.save_provider(id=self.provider['id'], name='fixture', base_url='http://localhost:1', flash_model='fixture', pro_model='fixture', flash_context_window=128000)
        self.assertEqual(self.store.task(task['id'])['context_window'], 64000)

    def test_legacy_record_remains_single_history_without_checkpoint(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,0)', ('legacy', json.dumps(dict(id='legacy', title='legacy', status='failed', workspace=str(self.workspace), created_at=1, result=None)),))
        item = self.store.task('legacy'); self.assertNotIn('group_id', item); self.assertNotIn('checkpoint', item)

    def test_offline_interruption_preserves_checkpoint_without_new_run(self):
        task = self.start(); record = self.store.task(task['id'])
        with self.store.connect() as db:
            record.update(status='running', pid=99999999, process_identity='gone')
            db.execute('UPDATE tasks SET payload=?,active=1 WHERE id=?', (json.dumps(record), task['id']))
        self.store.reap(); recovered = self.store.task(task['id'])
        self.assertEqual(recovered['status'], 'interrupted'); self.assertIn('checkpoint', recovered)
        group = groups.get(self.store, task['group_id']); self.assertEqual(len(group['run_ids']), 1)

    def test_disconnected_request_retains_estimate_and_usage_gap(self):
        task = self.start(); record = self.store.task(task['id']); prior = record['telemetry']['budget']['metered_tokens']
        record['telemetry']['request'] = dict(in_flight=True, input_estimate=900, kind='summary')
        with self.store.connect() as db:
            record.update(status='compacting', pid=99999999, process_identity='gone')
            db.execute('UPDATE tasks SET payload=?,active=1 WHERE id=?', (json.dumps(record), task['id']))
        self.store.reap(); actual = self.store.task(task['id'])
        self.assertEqual(actual['telemetry']['budget']['metered_tokens'], prior + 900)
        self.assertTrue(actual['telemetry']['tokens']['interrupted_request'])
        self.assertEqual(actual['telemetry']['usage_events'][-1]['gap'], 'engine_disconnected')

    def test_active_compaction_keeps_integration_and_upgrade_gates_closed(self):
        from codexrelay import integration, upgrade
        task = self.store.admit('active compaction', self.workspace, 'readonly', 'flash')
        for status in ('compacting', 'wrapping_up'):
            self.store.update_task(task['id'], status=status)
            with self.assertRaisesRegex(ValueError, '正在执行'):
                upgrade.prepare(self.root / 'not-created-plan.json', dict(state_directory=str(self.store.directory)))
            self.assertTrue(self.store.setting('enabled'))

    def test_checkpoint_evidence_corruption_is_detected(self):
        task = self.start()
        with self.store.connect() as db:
            row = db.execute('SELECT payload FROM execution_evidence WHERE run_id=? AND sequence=1', (task['id'],)).fetchone()
            item = json.loads(row[0]); item['result'] = 'forged successful tool output'
            db.execute('UPDATE execution_evidence SET payload=? WHERE run_id=? AND sequence=1', (json.dumps(item), task['id']))
        with self.assertRaisesRegex(ValueError, 'integrity'): groups.read_checkpoint(self.store, task['checkpoint']['checkpoint_id'])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows namespace aliases')
    def test_namespace_workspace_identity_and_project_disable_are_consistent(self):
        from codexrelay.state import canonical
        alias = Path('\\\\?\\' + str(self.workspace))
        self.assertEqual(canonical(alias), canonical(self.workspace))
        named_file = '\\\\?\\' + str(self.workspace / 'input.txt')
        result = execute_tool('Read', {'path': named_file}, self.config)
        self.assertIn('input evidence', result); self.assertNotIn('ERROR:', result)
        scoped = replace(self.config, task_root=alias)
        self.assertIn('input evidence', execute_tool('Read', {'path': named_file}, scoped))
        self.store.set_project(alias, True)
        with self.assertRaisesRegex(ValueError, 'PROJECT_DISABLED'): self.start()


if __name__ == '__main__': unittest.main()
