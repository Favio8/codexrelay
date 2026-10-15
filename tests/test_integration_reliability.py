"""Observable integration behavior in isolated files; never changes daily Codex."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tomlkit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from codexrelay import integration as subject
from codexrelay.capabilities import ALIASES, CANONICAL_TOOLS, LEGACY_EXECUTORS, TOOL_NAMES, assert_registered_tools, manifest
from codexrelay.integration_evidence import evidence_status, record_call, record_client_report
from codexrelay.service import Service
from codexrelay.state import Store


class IntegrationReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "codex"
        self.home.mkdir()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.store = Store(self.base / "state")
        self.config = self.home / "config.toml"
        self.rules = self.home / "AGENTS.md"

    def registration(self, **policy):
        doc = tomlkit.document()
        doc["model"] = "preserved-user-model"
        doc["mcp_servers"] = {"other": {"command": "other.exe"}, "codexrelay": {
            "command": str(Path(sys.executable).resolve()), "args": ["-I", "-X", "utf8", "-m", "codexrelay", "mcp"], **policy}}
        self.config.write_text("# keep this comment\n" + tomlkit.dumps(doc), encoding="utf-8")
        self.rules.write_text("User rule: retain this text.\n\n" + subject.RULES + "\n", encoding="utf-8")

    def status(self, **options):
        return subject.status(self.store, str(self.home), **options)

    def plan(self, **options):
        return subject.repair_preview(self.store, str(self.home), **options)

    def test_unset_empty_allow_deny_and_tool_restrictions(self):
        self.registration()
        prediction = self.status()["configuration_prediction"]
        self.assertEqual(prediction["allowlist_state"], "unset")
        self.assertEqual(set(prediction["effective_tools"]), TOOL_NAMES)
        self.registration(enabled_tools=[])
        self.assertEqual(self.status()["configuration_prediction"]["effective_tools"], [])
        self.registration(enabled_tools=["ping", "resume_agent"], disabled_tools=["resume_agent"], tools={"ping": {"enabled": False}})
        prediction = self.status()["configuration_prediction"]
        self.assertIn("explicitly_denied", prediction["tool_states"]["resume_agent"]["reasons"])
        self.assertIn("tool_disabled", prediction["tool_states"]["ping"]["reasons"])
        self.assertEqual(prediction["effective_tools"], [])

    def test_disabled_server_and_delegation_switch_are_separate(self):
        self.registration(enabled=False)
        report = self.status()
        self.assertTrue(report["registered"])
        self.assertFalse(report["configuration_prediction"]["server_enabled"])
        self.assertFalse(report["delegation_enabled"])
        plan = self.plan()
        self.assertFalse(plan["selection"]["enable_server"])
        self.assertFalse(plan["resulting_configuration"]["server_enabled"])
        self.assertTrue(self.plan(enable_server=True)["resulting_configuration"]["server_enabled"])

    def test_unknown_policy_is_unknown_and_bad_rules_never_replaced(self):
        self.registration(tools={"ping": {"future_policy": True}})
        prediction = self.status()["configuration_prediction"]
        self.assertEqual(prediction["tool_states"]["ping"]["state"], "unknown")
        self.assertTrue(prediction["unknown_policies"])
        self.rules.write_text("User content\n" + subject.BEGIN, encoding="utf-8")
        plan = self.plan()
        self.assertTrue(plan["conflicts"])
        with self.assertRaisesRegex(ValueError, "规则标记"):
            subject.apply_plan(self.store, plan)
        self.assertTrue(self.rules.read_text(encoding="utf-8").endswith(subject.BEGIN))

    def test_reversed_complete_rule_markers_are_malformed_and_never_duplicated(self):
        self.registration()
        self.rules.write_text("User content\n" + subject.END + "\ntext\n" + subject.BEGIN, encoding="utf-8")
        before = self.rules.read_bytes()
        self.assertEqual(self.status()["managed_rules"]["state"], "malformed")
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "规则标记"):
            subject.apply_plan(self.store, plan)
        self.assertEqual(self.rules.read_bytes(), before)

    def test_missing_executable_has_its_own_diagnostic_without_taking_ownership(self):
        self.registration()
        doc = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        doc["mcp_servers"]["codexrelay"]["command"] = str(self.base / "missing" / "python.exe")
        self.config.write_text(tomlkit.dumps(doc), encoding="utf-8")
        prediction = self.status()["configuration_prediction"]
        self.assertEqual(prediction["command_state"], "command_missing")
        self.assertFalse(prediction["command_exists"])
        self.assertEqual(prediction["registration_state"], "foreign")
        self.assertIn("command_missing", prediction["tool_states"]["ping"]["reasons"])
        self.assertTrue(self.plan()["conflicts"])

    def test_legacy_fixture_preserves_original_engine_readonly_but_finds_new_gaps(self):
        old_tools = ["ping", *LEGACY_EXECUTORS, *ALIASES.values()]
        old_tools.remove("record_deepseek_verification")  # reproduces restricted old entry list
        self.assertEqual(len(old_tools), 11)
        self.registration(enabled_tools=old_tools)
        by_id = {item["id"]: item for item in self.status()["configuration_prediction"]["capabilities"]}
        self.assertEqual(by_id["basic_readonly"]["state"], "available")
        self.assertTrue(by_id["basic_readonly"]["legacy_engine_only"])
        self.assertIn("start_agent", by_id["selected_backend"]["missing_tools"])
        self.assertIn("resume_agent", by_id["task_groups_continuation"]["missing_tools"])
        service = Service(self.store)
        result = service.dispatch("integration_repair_preview", {"codex_home": str(self.home)})
        self.assertIn("start_agent", result["added_tools"])
        self.assertIn("record_task_verification", result["added_tools"])
        self.assertIn("resume_agent", result["added_tools"])

    def test_contract_is_versioned_and_missing_or_extra_registration_fails(self):
        self.assertEqual(assert_registered_tools(TOOL_NAMES), manifest()["fingerprint"])
        self.assertEqual(manifest(), manifest())
        with self.assertRaisesRegex(RuntimeError, "missing"):
            assert_registered_tools(TOOL_NAMES - {"resume_agent"})
        with self.assertRaisesRegex(RuntimeError, "unexpected_tool"):
            assert_registered_tools(TOOL_NAMES | {"unexpected_tool"})

    def test_preview_is_readonly_and_selective_apply_preserves_comments_other_mcp_and_rules(self):
        self.registration(enabled_tools=["ping", "user-extra-tool"], disabled_tools=["resume_agent"], tools={"cancel_task": {"enabled": False, "approval_mode": "always"}})
        self.rules.write_text("User rule before.\n" + subject.BEGIN + "\nold\n" + subject.END + "\nUser rule after.\n", encoding="utf-8")
        before_config, before_rules = self.config.read_bytes(), self.rules.read_bytes()
        before_files = {str(path) for path in self.base.rglob("*")}
        plan = self.plan(capability_scope="basic")
        self.assertEqual({str(path) for path in self.base.rglob("*")}, before_files)
        self.assertEqual(self.config.read_bytes(), before_config)
        self.assertEqual(self.rules.read_bytes(), before_rules)
        self.assertIn("cancel_task", plan["retained_denials"])
        self.assertNotIn("resume_agent", plan["added_tools"])
        result = Service(self.store).dispatch("integration_apply_plan", {"plan": plan})
        doc = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        entry = doc["mcp_servers"]["codexrelay"]
        self.assertEqual(entry["enabled_tools"], ["ping", "user-extra-tool", "start_agent_readonly", "get_task_status", "get_task_result"])
        self.assertEqual(entry["disabled_tools"], ["resume_agent"])
        self.assertEqual(entry["tools"]["cancel_task"]["approval_mode"], "always")
        self.assertIn("# keep this comment", self.config.read_text(encoding="utf-8"))
        self.assertEqual(doc["model"], "preserved-user-model")
        self.assertEqual(doc["mcp_servers"]["other"]["command"], "other.exe")
        rules = self.rules.read_text(encoding="utf-8")
        self.assertIn("User rule before.", rules)
        self.assertIn("User rule after.", rules)
        self.assertIn(subject.RULES, rules)
        self.assertTrue(result["restart_required"])
        self.assertEqual(result["client_evidence"]["state"], "waiting_client_verification")
        self.assertNotIn(b"preserved-user-model", Path(result["operation_backup"]).read_bytes())

    def test_explicit_deny_removal_is_a_distinct_preview_selection(self):
        self.registration(enabled_tools=["ping"], disabled_tools=["resume_agent", "external-tool"], tools={"get_task_result": {"enabled": False, "approval_mode": "always"}})
        normal = self.plan()
        self.assertNotIn("resume_agent", normal["added_tools"])
        self.assertNotIn("get_task_result", normal["added_tools"])
        chosen = self.plan(remove_denials=["resume_agent", "get_task_result"])
        self.assertEqual(chosen["removed_denials"], ["get_task_result", "resume_agent"])
        self.assertNotEqual(normal["plan_id"], chosen["plan_id"])
        subject.apply_plan(self.store, chosen)
        doc = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        entry = doc["mcp_servers"]["codexrelay"]
        self.assertEqual(entry["disabled_tools"], ["external-tool"])
        self.assertTrue(entry["tools"]["get_task_result"]["enabled"])
        self.assertEqual(entry["tools"]["get_task_result"]["approval_mode"], "always")

    def test_preview_stale_on_concurrent_config_or_rules_edit_and_tampering(self):
        self.registration(enabled_tools=["ping"])
        plan = self.plan()
        self.config.write_bytes(self.config.read_bytes() + b"\n# concurrent user edit\n")
        concurrent = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, "预览已失效"):
            subject.apply_plan(self.store, plan)
        self.assertEqual(self.config.read_bytes(), concurrent)
        plan = self.plan()
        plan["selection"]["enable_server"] = True
        with self.assertRaisesRegex(ValueError, "预览已失效"):
            subject.apply_plan(self.store, plan)

    def test_second_step_failure_restores_only_own_write_and_retains_encrypted_backup(self):
        self.registration(enabled_tools=["ping"])
        self.rules.write_text("User rules\n", encoding="utf-8")
        before = self.config.read_bytes()
        plan, replace = self.plan(), subject.replace_checked
        def fail(path, previous, value):
            if path.name == "AGENTS.md":
                raise OSError("injected second-step failure")
            return replace(path, previous, value)
        with patch.object(subject, "replace_checked", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "已回退"):
                subject.apply_plan(self.store, plan)
        self.assertEqual(self.config.read_bytes(), before)
        recovery = self.store.setting("integration_recovery:" + str(self.home.resolve()))
        self.assertEqual(recovery["state"], "rolled_back")
        backup = Path(recovery["backup"])
        payload = json.loads(subject.crypt(backup.read_bytes(), decrypt=True))
        self.assertEqual(payload["plan_id"], plan["plan_id"])
        self.assertEqual(self.rules.read_text(encoding="utf-8"), "User rules\n")
        self.assertTrue(subject.restore_repair(self.store, self.home)["restored"])
        self.assertTrue(backup.is_file())

    def test_rollback_preserves_external_edit_and_recovery_does_not_overwrite_it(self):
        self.registration(enabled_tools=["ping"])
        self.rules.write_text("User rules\n", encoding="utf-8")
        plan, replace = self.plan(), subject.replace_checked
        outsider = b"# later external edit\nmodel='must-survive'\n"
        def conflict(path, previous, value):
            if path.name == "AGENTS.md":
                self.config.write_bytes(outsider)
                raise OSError("injected edit after first step")
            return replace(path, previous, value)
        with patch.object(subject, "replace_checked", side_effect=conflict):
            with self.assertRaisesRegex(ValueError, "外部修改"):
                subject.apply_plan(self.store, plan)
        self.assertEqual(self.config.read_bytes(), outsider)
        recovery = subject.restore_repair(self.store, self.home)
        self.assertFalse(recovery["restored"])
        self.assertEqual(recovery["pending"][0]["reason"], "external_edit_preserved")
        self.assertEqual(self.config.read_bytes(), outsider)

    def test_foreign_registration_cannot_be_taken_over(self):
        self.registration()
        doc = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        doc["mcp_servers"]["codexrelay"]["command"] = "foreign.exe"
        self.config.write_text(tomlkit.dumps(doc), encoding="utf-8")
        before = self.config.read_bytes()
        plan = self.plan()
        self.assertEqual(self.status()["configuration_prediction"]["registration_state"], "foreign")
        with self.assertRaisesRegex(ValueError, "不能自动接管"):
            subject.apply_plan(self.store, plan)
        self.assertEqual(self.config.read_bytes(), before)

    def test_report_ping_and_app_self_test_cannot_claim_full_client_verification(self):
        self.registration()
        self.store.heartbeat("instance-1", self.workspace)
        args = dict(instance="instance-1", workspace=str(self.workspace), client_session_id="chat-1", codex_home=self.home)
        report = record_client_report(self.store, **args, visible_tools=sorted(TOOL_NAMES), client_kind="codex_desktop", client_version="fixture")
        self.assertEqual(report["source"], "user_report")
        self.assertEqual(self.status(instance="instance-1", workspace=self.workspace, client_session_id="chat-1")["client_evidence"]["state"], "waiting_client_verification")
        record_call(self.store, **args, tool="ping", success=True, client_kind="codex_desktop")
        evidence = evidence_status(self.store, **{key: value for key, value in args.items() if key != "codex_home"}, codex_home=self.home)
        self.assertEqual(evidence["state"], "partially_verified")
        self.assertEqual(evidence["verified_tools"], ["ping"])
        self.assertEqual(self.status()["client_evidence"]["state"], "waiting_client_verification")
        for name in CANONICAL_TOOLS:
            record_call(self.store, **args, tool=name, success=True, client_kind="app_self_test")
        self.assertEqual(self.status(instance="instance-1", workspace=self.workspace, client_session_id="chat-1")["client_evidence"]["verified_tools"], ["ping"])

    def test_configuration_rules_instance_and_session_changes_expire_observations(self):
        self.registration()
        self.store.heartbeat("instance-1", self.workspace)
        args = dict(instance="instance-1", workspace=str(self.workspace), client_session_id="chat-1", codex_home=self.home)
        record_client_report(self.store, **args, visible_tools=["ping"], client_kind="codex_cli")
        record_call(self.store, **args, tool="ping", success=True, client_kind="codex_cli")
        current = evidence_status(self.store, **args)
        self.assertTrue(current["client_reports"][-1]["current"])
        different = evidence_status(self.store, **{**args, "client_session_id": "chat-2"})
        self.assertIn("different_client_session", different["client_reports"][-1]["stale_reasons"])
        different = evidence_status(self.store, **{**args, "instance": "instance-2"})
        self.assertIn("different_instance", different["client_reports"][-1]["stale_reasons"])
        self.config.write_bytes(self.config.read_bytes() + b"\n# changed\n")
        self.rules.write_bytes(self.rules.read_bytes() + b"\n# changed\n")
        stale = evidence_status(self.store, **args)
        reasons = stale["client_reports"][-1]["stale_reasons"]
        self.assertIn("config_sha256_changed", reasons)
        self.assertIn("rules_sha256_changed", reasons)
        self.assertEqual(stale["verified_tools"], [])


if __name__ == "__main__":
    unittest.main()
