"""A terminal command owns no live Windows descendants or cwd handles."""
import ctypes
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from deepseek_mcp import agent_loop as loop, bash_tool, tool_process
from deepseek_mcp.config import Config
from deepseek_mcp.job_manager import DeepSeekJobManager
from deepseek_mcp.provider_retry import AgentLoopError
from test_execution_reliability import response


class WindowsJobCleanupTests(unittest.TestCase):
    def test_job_closes_only_after_accounting_confirms_zero_descendants(self):
        events, active = [], iter([2, 1, 0])
        def terminate(handle, code):
            events.append(('terminate', handle))
            return True
        def query(handle):
            value = next(active)
            events.append(('active', value))
            return value
        def close(handle):
            events.append(('close', handle))
            return True
        job = bash_tool._WindowsJob(123, close, terminate, query)
        self.assertTrue(job.close())
        self.assertEqual(events, [('terminate', 123), ('active', 2), ('active', 1), ('active', 0), ('close', 123)])
        self.assertTrue(job.close())
        self.assertEqual(len(events), 5)

    def test_accounting_timeout_is_bounded_and_never_reported_as_success(self):
        closed = []
        job = bash_tool._WindowsJob(123, lambda handle: closed.append(handle) or True, lambda *args: True, lambda handle: 1)
        with patch.object(bash_tool.time, 'monotonic', side_effect=[0, .4, 1.01]), patch.object(bash_tool.time, 'sleep'):
            self.assertFalse(job.close())
        self.assertEqual(closed, [123])
        self.assertFalse(job.close())

    def test_query_or_termination_failure_still_closes_job_without_claiming_cleanup(self):
        for terminate, query in [(False, 0), (True, None)]:
            with self.subTest(terminate=terminate, query=query):
                closed = []
                job = bash_tool._WindowsJob(456, lambda handle: closed.append(handle) or True,
                                           lambda *args: terminate, lambda handle: query)
                self.assertFalse(job.close())
                self.assertEqual(closed, [456])

    def test_tool_parent_checks_descendant_job_before_direct_process_and_artifacts(self):
        events = []
        process = SimpleNamespace(_deepseek_tool_job=SimpleNamespace(close=lambda: events.append('job') or True))
        with patch.object(tool_process, '_stop_process', side_effect=lambda process: events.append('process') or True), \
             patch.object(tool_process, 'close_parent_liveness'), patch.object(tool_process, '_close_process_pipes'), \
             patch.object(tool_process, 'cleanup_tool_artifacts', side_effect=lambda *args: events.append('artifacts')):
            tool_process._cleanup_tool_process(process, None, False, None, 'Bash', {}, 'fixture')
        self.assertEqual(events, ['job', 'process', 'artifacts'])

    def test_failed_descendant_cleanup_stops_direct_process_and_fails_closed(self):
        process = SimpleNamespace(_deepseek_tool_job=SimpleNamespace(close=lambda: False))
        with patch.object(tool_process, '_stop_process', return_value=True) as stop, \
             patch.object(tool_process, 'close_parent_liveness'), patch.object(tool_process, '_close_process_pipes'), \
             patch.object(tool_process, 'cleanup_tool_artifacts') as artifacts:
            with self.assertRaisesRegex(AgentLoopError, 'cleanup failed'):
                tool_process._cleanup_tool_process(process, None, False, None, 'Bash', {}, 'fixture')
        stop.assert_called_once_with(process)
        artifacts.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows process job/cwd contract')
    def test_real_cancel_waits_for_grandchild_exit_before_terminal_and_releases_cwd(self):
        with tempfile.TemporaryDirectory(prefix='job-cleanup-', dir=ROOT / '.tmp') as directory:
            workspace = Path(directory) / 'workspace with spaces'
            workspace.mkdir()
            locked = workspace / 'grandchild cwd'
            locked.mkdir()
            script = workspace / 'spawn.py'
            script.write_text('import os,sys,json,time,subprocess\nfrom pathlib import Path\n'
                'child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(30)"],cwd="grandchild cwd")\n'
                'Path("started.json").write_text(json.dumps({"home":os.environ["HOME"],"pid":child.pid}))\n'
                'time.sleep(30)\n', encoding='utf-8')
            manager = DeepSeekJobManager(lock_directory=Path(directory) / 'locks')
            config = Config(api_key='local-only', workspace=workspace, allowed_tools=['Bash'])
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.WaitForSingleObject.restype = ctypes.c_ulong
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = None
            with patch.object(loop, '_call_with_retry', return_value=response('Bash', {'executable': sys.executable, 'args': ['-B', str(script)]})):
                started = manager.start('Spawn child, then cancel', '', config)
                try:
                    marker = workspace / 'started.json'
                    deadline = time.monotonic() + 10
                    while not marker.exists() or marker.stat().st_size == 0:
                        self.assertLess(time.monotonic(), deadline, 'command did not spawn descendant')
                        time.sleep(.02)
                    observed = json.loads(marker.read_text())
                    handle = kernel.OpenProcess(0x00100000, False, observed['pid'])
                    self.assertTrue(handle, 'hold the actual grandchild process identity before cancelling')
                    self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0x102)
                    manager.cancel(started['job_id'])
                    self.assertTrue(manager.wait_for_terminal(started['job_id'], 10))
                    self.assertEqual(manager.result(started['job_id'])['status'], 'cancelled')
                    self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0, 'terminal task still owns a live grandchild')
                    self.assertFalse(Path(observed['home']).exists())
                    locked.rmdir()  # The inherited command cwd must be released immediately.
                    self.assertTrue(marker.exists())
                finally:
                    manager.cancel(started['job_id'])
                    manager.wait_for_terminal(started['job_id'], 10)
                    if handle:
                        kernel.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
