"""Client identity is explicit; an MCP process is not one chat."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.state import Store
from codexrelay import client_evidence as evidence


class ClientEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.store = Store(self.workspace / 'state')

    def bind(self, **updates):
        values = dict(instance='i', workspace=self.workspace, session_id='s', root_session_id='root',
                      client_kind='local_stdio', confirmed=True)
        return evidence.bind(self.store, **(values | updates))

    def test_confirmation_and_workspace_instance_are_required(self):
        with self.assertRaisesRegex(ValueError, 'confirmation'):
            self.bind(confirmed=False)
        binding = self.bind()
        self.assertFalse(binding['attested'])
        self.assertEqual(evidence.get_binding(self.store, binding['id'], self.workspace, 'i')['id'], binding['id'])
        with self.assertRaisesRegex(ValueError, 'earlier MCP'):
            evidence.get_binding(self.store, binding['id'], self.workspace, 'new')
        other = self.workspace / 'other'; other.mkdir()
        with self.assertRaisesRegex(ValueError, 'another workspace'):
            evidence.get_binding(self.store, binding['id'], other)

    def test_one_instance_can_have_multiple_explicit_chat_bindings(self):
        first = self.bind()
        second = self.bind(session_id='other-chat', root_session_id='other-root')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(evidence.get_binding(self.store, first['id'], self.workspace)['session_id'], 's')

    def test_calls_join_run_group_and_do_not_store_prompt_or_output(self):
        binding = self.bind()
        task = {'id':'r', 'group_id':'g', 'client_binding_id':binding['id'], 'trial_id':'t'}
        with self.store.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,0)', ('r', json.dumps(task)))
        record = evidence.record_call(self.store, instance='i', workspace=self.workspace,
                  tool='get_task_result', success=True, arguments={'job_id':'r','secret':'sensitive-key'},
                  result={'final_message':'private project body'})
        self.assertEqual(record['group_id'], 'g')
        self.assertEqual(record['client_binding_id'], binding['id'])
        chain = evidence.chain(self.store, workspace=self.workspace, group_id='g')
        self.assertEqual(len(chain['calls']), 1)
        self.assertNotIn('sensitive-key', json.dumps(chain))
        self.assertNotIn('private project body', json.dumps(chain))
        self.assertEqual(evidence.chain(self.store, workspace=self.workspace, group_id='absent')['calls'], [])

    def test_snapshot_excludes_observation_ledgers(self):
        self.store.set_setting('integration_client_reports_v1', [{'private':'report'}])
        self.store.set_setting('integration_call_evidence_v1', [{'private':'call'}])
        snapshot = self.store.snapshot()
        self.assertNotIn('integration_client_reports_v1', snapshot)
        self.assertNotIn('integration_call_evidence_v1', snapshot)
