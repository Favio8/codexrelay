"""Startup reads must tolerate another process writing the shared database."""
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.state import Store
from codexrelay.__main__ import rpc_error


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_fresh_rpc_can_read_initialized_state_while_a_writer_is_active(self):
        self.store.set_setting('active_backend', 'pi')
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            with self.store.connect() as writer:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute("UPDATE settings SET value='true' WHERE key='enabled'")
                future = pool.submit(lambda: Store(self.temp.name).snapshot())
                snapshot = future.result(timeout=2)
                self.assertFalse(snapshot['enabled'], 'reader must see the last committed state')
                self.assertEqual(snapshot['active_backend'], 'pi')
        finally:
            pool.shutdown(wait=True)
        self.assertTrue(self.store.snapshot()['enabled'])

    def test_upgrade_initializes_version_without_resetting_saved_values(self):
        self.store.set_setting('enabled', True)
        self.store.set_setting('active_backend', 'pi')
        with self.store.connect() as db:
            db.execute('PRAGMA user_version=0')
            db.execute('DROP TABLE usage_events')
        upgraded = Store(self.temp.name)
        self.assertTrue(upgraded.setting('enabled'))
        self.assertEqual(upgraded.setting('active_backend'), 'pi')
        with upgraded.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], Store.SCHEMA_VERSION)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='usage_events'").fetchone())

    def test_busy_snapshot_details_are_actionable_and_distinct_from_permissions(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            other = sqlite3.connect(self.store.path, timeout=0)
            try:
                with self.assertRaises(sqlite3.OperationalError) as raised:
                    other.execute('BEGIN IMMEDIATE')
            finally:
                other.close()
        error = rpc_error(raised.exception)
        self.assertEqual(error['code'], 'database_busy')
        self.assertTrue(error['retryable'])
        self.assertIn('SQLITE_BUSY', error['details'])
        self.assertNotIn('权限', error['error'])

    def test_readonly_database_is_not_retried_as_busy(self):
        db = sqlite3.connect(self.store.path.as_uri() + '?mode=ro', uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                db.execute("UPDATE settings SET value='true' WHERE key='enabled'")
        finally:
            db.close()
        error = rpc_error(raised.exception)
        self.assertEqual(error['code'], 'database_error')
        self.assertNotIn('retryable', error)
        self.assertIn('权限', error['error'])

    def test_extended_busy_code_is_retryable(self):
        exc = sqlite3.OperationalError('private text')
        exc.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
        exc.sqlite_errorname = 'SQLITE_BUSY_SNAPSHOT'
        self.assertTrue(rpc_error(exc)['retryable'])

    def test_details_never_return_arbitrary_exception_contents(self):
        for exc in (RuntimeError('secret-key-and-provider-response'), PermissionError('secret-key-and-provider-response')):
            error = rpc_error(exc)
            self.assertNotIn('secret-key', json.dumps(error))


if __name__ == '__main__':
    unittest.main()
