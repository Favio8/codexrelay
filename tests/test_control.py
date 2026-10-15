import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from codexrelay.state import Store
from codexrelay.service import Service
from codexrelay.mcp_server import Relay
from codexrelay.backends import catalog
from codexrelay import integration

ROOT = Path(__file__).resolve().parents[1]
(ROOT / ".tmp").mkdir(exist_ok=True)


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "项目 with spaces"
        self.workspace.mkdir()
        self.store = Store(self.base / "state")
        self.vault = MemoryVault()
        self.service = Service(self.store, self.vault)
        self.provider = self.service.save_provider(name="Test", kind="custom", base_url="https://example.com/v1", flash_model="fast", pro_model="strong", reasoning_effort="provider-default", api_key="test-secret-never-persist")

    def enable(self):
        self.service.dispatch("set_enabled", {"enabled": True})

    def admit(self):
        return self.store.admit("Task", self.workspace, "readonly", "flash")

    def test_disabled_rejects_without_task(self):
        with self.assertRaisesRegex(ValueError, "DELEGATION_DISABLED"):
            self.admit()
        self.assertEqual(self.store.snapshot()["tasks"], [])

    def test_backend_test_refuses_while_a_task_is_active(self):
        self.enable()
        self.admit()
        with self.assertRaisesRegex(ValueError, "已有委托任务执行中"):
            self.service.dispatch("test_backend", {"backend": "deepseek", "provider_id": self.provider["id"]})
        self.store.update_task(self.store.snapshot()["tasks"][0]["id"], status="cancelled", finished_at=time.time())
        from codexrelay.connection_probe import ProbeFailure
        with patch("codexrelay.connection_probe.probe_backend", side_effect=ProbeFailure("no provider")) as runner:
            with self.assertRaisesRegex(ValueError, "no provider"):
                self.service.dispatch("test_backend", {"backend": "deepseek", "provider_id": self.provider["id"]})
        self.assertEqual(runner.call_args.args[2:4], ('flash', 'deepseek'))

    def test_readonly_without_read_tools_is_refused_before_any_call(self):
        self.enable()
        record = self.store.provider(self.provider["id"])
        self.service.save_provider(**{**record, "allowed_tools": ["Bash"]})
        relay = Relay(self.store, self.vault, self.workspace)
        with patch.object(relay.manager, "start") as start:
            with self.assertRaisesRegex(ValueError, "严格只读"):
                relay.start("Read only", "", "readonly", "flash")
            self.assertFalse(start.called)
        self.assertEqual(self.store.snapshot()["tasks"], [])

    def test_backend_probe_reserves_the_shared_slot_and_releases_after_failure(self):
        self.enable()
        other = Store(self.store.directory)
        def probe(*args, **kwargs):
            with self.assertRaisesRegex(ValueError, 'BUSY'):
                other.admit('Concurrent task', self.workspace, 'readonly', 'flash')
            with self.assertRaisesRegex(ValueError, '已有委托任务执行中'):
                with other.backend_probe(other.provider(self.provider['id']), 'pi', 'flash', self.workspace):
                    self.fail('a second probe entered')
            self.assertEqual(self.service.dispatch('usage_today', {})['counts'], {})
            from codexrelay.connection_probe import ProbeFailure
            raise ProbeFailure('fixture probe failed')
        with patch('codexrelay.connection_probe.probe_backend', side_effect=probe):
            with self.assertRaisesRegex(ValueError, 'fixture probe failed'):
                self.service.test_backend('deepseek', self.provider['id'])
        self.assertEqual(other.snapshot()['tasks'], [])
        self.admit()

    def test_unsupported_capability_is_refused_before_admission(self):
        self.enable()
        relay = Relay(self.store, self.vault, self.workspace)
        limited = [{"id": "deepseek", "installed": True,
                    "capabilities": {"strict_readonly": False, "budget_enforcement": False, "steering": True}}]
        with patch("codexrelay.mcp_server.catalog", return_value=limited):
            with self.assertRaisesRegex(ValueError, "严格只读"):
                relay.start("Read only", "", "readonly", "flash")
            with self.assertRaisesRegex(ValueError, "任务预算"):
                relay.start("Work", "", "coding", "flash", token_budget=40000)
        self.assertEqual(self.store.snapshot()["tasks"], [])

    def test_capability_entries_are_independent_and_labelled(self):
        entries = catalog()
        self.assertEqual(len({id(item["capabilities"]) for item in entries}), len(entries))
        for item in entries:
            self.assertEqual(item["capabilities"]["basis"], "verified_by_test")

    def test_hot_disable_preserves_running_task(self):
        self.enable()
        task = self.admit()
        other = Store(self.store.directory)
        other.set_setting("enabled", False)
        self.assertFalse(self.store.task(task["id"])["cancel_requested"])
        with self.assertRaisesRegex(ValueError, "DELEGATION_DISABLED"):
            self.admit()
        self.store.update_task(task["id"], status="completed", result={"answer": "done"})
        self.assertEqual(other.task(task["id"])["status"], "completed")

    def test_project_override(self):
        self.enable()
        self.store.set_project(self.workspace, True)
        with self.assertRaisesRegex(ValueError, "PROJECT_DISABLED"):
            self.admit()
        self.store.set_project(self.workspace, False)
        self.assertEqual(self.admit()["status"], "queued")

    def test_one_admission_across_connections(self):
        self.enable()
        barrier = threading.Barrier(2)
        results = []
        def submit():
            store = Store(self.store.directory)
            barrier.wait()
            try:
                results.append(store.admit("Race", self.workspace, "readonly", "flash")["id"])
            except ValueError as exc:
                results.append(str(exc))
        workers = [threading.Thread(target=submit) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(sum(r.startswith("BUSY") for r in results), 1)
        self.assertEqual(len(self.store.snapshot()["tasks"]), 1)

    def test_close_linearizes_with_admission(self):
        self.enable()
        Store(self.store.directory).set_setting("enabled", False)
        with self.assertRaisesRegex(ValueError, "DELEGATION_DISABLED"):
            Store(self.store.directory).admit("After off", self.workspace, "coding", "pro")

    def test_no_key_in_db_or_snapshot(self):
        self.assertNotIn(b"test-secret-never-persist", self.store.path.read_bytes())
        snapshot = self.service.snapshot()
        self.assertTrue(snapshot["providers"][0]["has_key"])
        self.assertNotIn("credential_id", snapshot["providers"][0])
        self.assertNotIn("test-secret-never-persist", json.dumps(snapshot))

    def test_cancel_does_not_report_completed_or_undo(self):
        self.enable()
        task = self.admit()
        changed = self.workspace / "changed.txt"
        changed.write_text("keep me")
        result = self.store.cancel(task["id"])
        self.assertEqual(result["status"], "cancelling")
        self.assertTrue(changed.exists())
        self.store.update_task(task["id"], status="cancelled")
        self.assertEqual(self.store.cancel(task["id"])["status"], "cancelled")

    def test_config_changed_before_admission(self):
        self.enable()
        before = self.store.provider()
        self.service.save_provider(**{**before, "api_key": "new-secret", "flash_model": "new"})
        with self.assertRaisesRegex(ValueError, "配置已变化"):
            self.store.admit("Race", self.workspace, "readonly", "flash", expected_provider=before)

    def test_running_task_keeps_model_identity(self):
        self.enable()
        task = self.admit()
        provider = self.store.provider()
        self.service.save_provider(**{**provider, "api_key": "rotated", "flash_model": "replacement"})
        self.assertEqual(self.store.task(task["id"])["model"], "fast")
        with self.assertRaisesRegex(ValueError, "运行中的任务"):
            self.service.delete_provider(provider["id"])

    def test_dead_owner_is_interrupted(self):
        self.enable()
        task = self.admit()
        with patch("codexrelay.state.process_identity", return_value=None):
            snapshot = self.store.snapshot()
        self.assertEqual(snapshot["tasks"][0]["status"], "interrupted")
        self.assertIsNone(snapshot["tasks"][0]["result"])

    def test_selective_install_uninstall_preserves_later_edits(self):
        directory = self.base / "codex"
        directory.mkdir()
        config = directory / "config.toml"
        config.write_text('# preserve me\nmodel = "original"\n[mcp_servers.other]\ncommand="other.exe"\n', encoding="utf-8")
        (directory / "AGENTS.md").write_text("User rules\n", encoding="utf-8")
        result = integration.install(self.store, str(directory))
        self.assertTrue(result["registered"])
        self.assertNotIn(b"original", Path(result["backup"]).read_bytes())
        config.write_text(config.read_text(encoding="utf-8") + '\n[custom]\nvalue=7\n', encoding="utf-8")
        integration.uninstall(self.store, str(directory))
        content = config.read_text(encoding="utf-8")
        self.assertIn('model = "original"', content)
        self.assertIn('value=7', content)
        self.assertIn('mcp_servers.other', content)
        self.assertNotIn('mcp_servers.codexrelay', content)
        self.assertEqual((directory / "AGENTS.md").read_text(encoding="utf-8"), "User rules\n")

    def test_migration_restores_previous_registration(self):
        directory = self.base / "codex"
        directory.mkdir()
        config = directory / "config.toml"
        config.write_text('[mcp_servers.deepseek]\ncommand="old.exe"\n', encoding="utf-8")
        (directory / "AGENTS.md").write_text("Before\n" + integration.OLD_BEGIN + "\nlegacy\n" + integration.OLD_END, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "迁移"):
            integration.install(self.store, str(directory))
        integration.install(self.store, str(directory), migrate=True)
        self.assertNotIn('old.exe', config.read_text(encoding="utf-8"))
        integration.uninstall(self.store, str(directory))
        self.assertIn('old.exe', config.read_text(encoding="utf-8"))
        self.assertIn('legacy', (directory / "AGENTS.md").read_text(encoding="utf-8"))

    def test_install_rolls_back_config_when_rules_write_fails(self):
        directory = self.base / "codex"
        directory.mkdir()
        config = directory / "config.toml"
        original = b'# user config\nmodel="keep-me"\n'
        config.write_bytes(original)
        replace = integration.replace_checked
        def fail_rules(path, before, after):
            if path.name == "AGENTS.md":
                raise ValueError("simulated rules write conflict")
            return replace(path, before, after)
        with patch("codexrelay.integration.replace_checked", side_effect=fail_rules):
            with self.assertRaisesRegex(ValueError, "conflict"):
                integration.install(self.store, str(directory))
        self.assertEqual(config.read_bytes(), original)

    def test_foreign_same_name_registration_is_preserved(self):
        directory = self.base / "codex"
        directory.mkdir()
        config = directory / "config.toml"
        original = b'[mcp_servers.codexrelay]\ncommand="foreign.exe"\nargs=["-I","-X","utf8","-m","codexrelay","mcp"]\n'
        config.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "同名"):
            integration.install(self.store, str(directory))
        self.assertEqual(config.read_bytes(), original)

    def test_managed_registration_survives_different_desktop_interpreter(self):
        directory = self.base / "codex"
        directory.mkdir()
        integration.install(self.store, str(directory))
        with patch("codexrelay.integration.sys.executable", str(self.base / "other-runtime" / "python.exe")):
            result = integration.preview(self.store, str(directory))
        self.assertTrue(result["registered"])
        self.assertNotIn("conflict", result)

    def test_same_runtime_with_windows_namespace_after_config_reformat(self):
        directory = self.base / "codex"
        directory.mkdir()
        integration.install(self.store, str(directory))
        config = directory / "config.toml"
        config.write_text(config.read_text(encoding="utf-8").replace("# Managed by CodexRelay\n", ""), encoding="utf-8")
        self.store.set_setting("integration_registration:" + str(directory.resolve()), None)
        executable = str(Path(sys.executable).resolve())
        if not executable.startswith("\\\\?\\"):
            executable = "\\\\?\\" + executable
        with patch("codexrelay.integration.sys.executable", executable):
            result = integration.preview(self.store, str(directory))
        self.assertTrue(result["registered"])
        self.assertNotIn("conflict", result)

    def test_previous_release_is_recognized_from_protected_backup(self):
        directory = self.base / "codex"
        directory.mkdir()
        integration.install(self.store, str(directory))
        self.store.set_setting("integration_registration:" + str(directory.resolve()), None)
        with patch("codexrelay.integration.sys.executable", str(self.base / "other-runtime" / "python.exe")):
            result = integration.preview(self.store, str(directory))
        self.assertTrue(result["registered"])
        self.assertNotIn("conflict", result)


if __name__ == "__main__":
    unittest.main()
