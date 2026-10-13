"""Upgrade handoff preserves business state and changes only owned MCP paths."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
import tomlkit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.state import Store
from codexrelay import upgrade, integration


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.store = Store(self.area / 'state')
        self.store.save_provider(dict(id='p',revision='1',name='Preserve me',credential_id='p',flash_model='fast',pro_model='strong'))
        self.store.set_setting('active_backend', 'pi')
        self.store.set_setting('enabled', True)
        self.home = self.area / 'codex'
        self.home.mkdir()
        self.old = self.area / 'old-python.exe'
        self.old.write_bytes(b'fixture interpreter path')
        self.config = self.home / 'config.toml'
        self.config.write_text('model="keep-main-model"\n[mcp_servers.other]\nurl="https://example.com/mcp"\n[mcp_servers.codexrelay]\ncommand='+json.dumps(str(self.old))+'\nargs=["-I","-X","utf8","-m","codexrelay","mcp"]\n',encoding='utf-8')
        self.store.set_setting('integration_registration:' + str(self.home), {'command': str(self.old)})
        self.rules = self.home / 'AGENTS.md'
        self.rules.write_text('User rules: preserve exact bytes.\n',encoding='utf-8')
        self.plan = dict(state_directory=str(self.store.directory),install_directory=str(self.area/'new'),roots=[str(self.old.parent)],prepared=False)
        self.path = self.area / 'plan.json'
        upgrade.save_plan(self.path,self.plan)

    def test_prepare_temporarily_blocks_admission_without_resetting_other_settings(self):
        providers = self.store.providers()
        upgrade.prepare(self.path,self.plan)
        self.assertFalse(self.store.setting('enabled'))
        self.assertEqual(self.store.setting('active_backend'),'pi')
        self.assertEqual(self.store.providers(),providers)
        with self.assertRaisesRegex(ValueError,'DELEGATION_DISABLED'):
            self.store.admit('Must not start',self.area,'readonly','flash')
        upgrade.restore(self.plan)
        self.assertTrue(self.store.setting('enabled'))

    def test_running_task_refuses_upgrade_before_switch_or_plan_changes(self):
        task = self.store.admit('Live task',self.area,'readonly','flash')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'正在执行'):
            upgrade.prepare(self.path,self.plan)
        self.assertTrue(self.store.setting('enabled'))
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.store.task(task['id'])['status'],'queued')

    def test_finish_relocates_only_owned_entry_and_preserves_rules_and_business_state(self):
        previous = tomlkit.parse(self.config.read_text(encoding='utf-8'))
        rules = self.rules.read_bytes()
        upgrade.prepare(self.path,self.plan)
        upgrade.finish(self.path,self.plan)
        current = tomlkit.parse(self.config.read_text(encoding='utf-8'))
        self.assertEqual(current['model'],previous['model'])
        self.assertEqual(current['mcp_servers']['other'],previous['mcp_servers']['other'])
        self.assertTrue(integration.same_executable(current['mcp_servers']['codexrelay']['command'],sys.executable))
        self.assertEqual(current['mcp_servers']['codexrelay']['args'],previous['mcp_servers']['codexrelay']['args'])
        self.assertEqual(self.rules.read_bytes(),rules)
        self.assertTrue(self.store.setting('enabled'))
        self.assertEqual(self.store.setting('active_backend'),'pi')

    def test_foreign_overridden_entry_is_not_rewritten(self):
        self.config.write_text(self.config.read_text(encoding='utf-8')+'cwd="foreign-override"\n',encoding='utf-8')
        before=self.config.read_bytes()
        upgrade.prepare(self.path,self.plan)
        upgrade.finish(self.path,self.plan)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(self.plan['updated_mcp_configs'],[])

    def test_same_runtime_does_not_reformat_existing_configuration(self):
        config=tomlkit.parse(self.config.read_text(encoding='utf-8'))
        config['mcp_servers']['codexrelay']['command']=sys.executable
        self.config.write_text(tomlkit.dumps(config),encoding='utf-8')
        before=self.config.read_bytes()
        upgrade.prepare(self.path,self.plan)
        upgrade.finish(self.path,self.plan)
        self.assertEqual(self.config.read_bytes(),before)

    def test_disabled_switch_stays_disabled_after_finish(self):
        self.store.set_setting('enabled',False)
        upgrade.prepare(self.path,self.plan)
        upgrade.finish(self.path,self.plan)
        self.assertFalse(self.store.setting('enabled'))

    def test_mixed_runtime_cannot_be_reported_as_a_successful_upgrade(self):
        self.plan['version']='99.0.0'
        before=self.config.read_bytes()
        upgrade.prepare(self.path,self.plan)
        with self.assertRaisesRegex(ValueError,'版本未更新'):
            upgrade.finish(self.path,self.plan)
        self.assertEqual(self.config.read_bytes(),before)
        upgrade.restore(self.plan)
        self.assertTrue(self.store.setting('enabled'))

    def test_abort_without_preparation_does_not_create_or_modify_state(self):
        upgrade.restore(dict(state_directory=str(self.area/'absent'),prepared=False))
        self.assertFalse((self.area/'absent').exists())


if __name__=='__main__':
    unittest.main()
