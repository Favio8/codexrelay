"""Recovery-gate hardening: private storage, unreadable records and the acknowledgement audit."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.backends import review
from deepseek_mcp.config import Config
from deepseek_mcp.execution_lock import _secure_directory
from deepseek_mcp.transaction_recovery import TransactionRecoveryRequired
from deepseek_mcp.workspace_changes import snapshot


class ReviewHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='review-tests-', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / 'project'
        self.workspace.mkdir()
        self.config = Config(api_key='fixture', workspace=self.workspace, allowed_tools=['Read'],
                             delegation_capability='readonly')
        change = patch.object(review, 'DIRECTORY', Path(self.temp.name) / 'reviews')
        change.start()
        self.addCleanup(change.stop)

    def baseline(self):
        return snapshot(self.workspace)

    def test_pending_review_storage_is_private(self):
        value = review.begin(self.config, self.baseline())
        self.assertTrue(value['review_id'])
        # Raises when another local principal could read the workspace hash list or forge the check.
        _secure_directory(review.DIRECTORY)
        review.append_audit(self.config, {'review_id': value['review_id']})
        self.assertTrue(review.location(self.config).exists())
        self.assertTrue(review.audit_location(self.config).exists())

    def test_unreadable_record_keeps_the_gate_closed(self):
        review.begin(self.config, self.baseline())
        review.location(self.config).write_text('{oops', encoding='utf-8')
        item = review.query(self.config)[0]
        self.assertTrue(item['corrupt'])
        self.assertEqual(item['status'], 'pending')
        with self.assertRaises(TransactionRecoveryRequired):
            review.require_clear(self.config)

    def test_acknowledgement_keeps_evidence_before_clearing(self):
        value = review.begin(self.config, self.baseline())
        (self.workspace / 'written.txt').write_text('changed', encoding='utf-8')
        pending = review.finish(self.config, value)
        self.assertEqual(pending['observed']['added'], ['written.txt'])
        item = review.query(self.config)[0]
        result = review.acknowledge(self.config, item['review_id'], item['state_sha256'],
                                    'Read written.txt and ran the test')
        self.assertTrue(result['ok'])
        self.assertEqual(review.query(self.config), [])
        record = json.loads(review.audit_location(self.config).read_text(encoding='utf-8').strip().split('\n')[-1])
        self.assertEqual(record['review_id'], pending['review_id'])
        self.assertEqual(record['evidence'], 'Read written.txt and ran the test')
        self.assertEqual(record['observed']['added'], 1)
        self.assertTrue(record['observed_complete'])

    def test_acknowledgement_refuses_a_changed_workspace(self):
        review.begin(self.config, self.baseline())
        (self.workspace / 'written.txt').write_text('changed', encoding='utf-8')
        item = review.query(self.config)[0]
        (self.workspace / 'written.txt').write_text('changed again', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '状态已变化'):
            review.acknowledge(self.config, item['review_id'], item['state_sha256'], 'evidence')
        self.assertTrue(review.query(self.config))

    def test_blocked_review_survives_failed_bookkeeping(self):
        value = review.begin(self.config, self.baseline())
        blocked = review.block(self.config, value, 'OSError')
        self.assertEqual(blocked['status'], 'pending')
        self.assertEqual(blocked['observed']['added'], [])
        with self.assertRaises(TransactionRecoveryRequired):
            review.require_clear(self.config)
        self.assertEqual(review.query(self.config)[0]['observed']['unreadable'], ['workspace_changes_failed'])

    def test_failed_bookkeeping_can_be_reviewed_and_acknowledged(self):
        value = review.begin(self.config, self.baseline())
        (self.workspace / 'written.txt').write_text('preserved change', encoding='utf-8')
        review.block(self.config, value, 'OSError')
        item = review.query(self.config)[0]
        self.assertNotIn('corrupt', item)
        self.assertEqual(item['observed']['added'], ['written.txt'])
        self.assertFalse(item['observed']['complete'])
        self.assertEqual(item['current_hashes'], self.baseline()[0])
        review.acknowledge(self.config, item['review_id'], item['state_sha256'],
                           'Inspected the complete workspace and written.txt after bookkeeping failed')
        self.assertEqual(review.query(self.config), [])
        audit = json.loads(review.audit_location(self.config).read_text(encoding='utf-8'))
        self.assertFalse(audit['observed_complete'])
