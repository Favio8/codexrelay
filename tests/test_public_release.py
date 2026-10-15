"""Public copies preserve counters and hash references without exposing local state."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from package_public_release import prefixes, redact_json, public_evidence, private_matches

_spec = importlib.util.spec_from_file_location('prepare_public_runtime', Path(__file__).resolve().parents[1] / 'scripts/prepare-public-runtime.py')
_runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runtime)


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.replacements = prefixes('D:\\private-workspace', 'C:\\Users\\private-person')

    def write(self, name, value):
        (self.area / name).write_text(json.dumps(value), encoding='utf-8')

    def test_json_keys_values_and_escaped_prefixes_are_scrubbed_without_changing_usage(self):
        value = {'D:\\private-workspace\\file.py': {'path':'C:\\Users\\private-person\\state',
                 'request_count':3, 'reported_tokens':25, 'cost':None, 'ok':True},
                 'logged':'C:\\\\Users\\\\private-person\\\\state'}
        transformed = redact_json(value, self.replacements)
        row = transformed['<workspace>\\file.py']
        self.assertEqual(row, {'path':'<user-home>\\state','request_count':3,'reported_tokens':25,'cost':None,'ok':True})
        self.assertFalse(private_matches(json.dumps(transformed).encode(), self.replacements))

    def test_private_host_records_and_screenshot_pixels_are_never_exported(self):
        self.write('daily-repair-preview.json', {'private_policy':'must not leave local storage'})
        (self.area/'integration-ui.png').write_bytes(b'private screenshot pixels')
        self.write('raw-task.json', {'body':'unreviewed private task'})
        self.write('native-roadmap.json', {'ok':True,'calls':2,'fixture':'D:\\private-workspace\\fixture'})
        original = (self.area/'daily-repair-preview.json').read_bytes()
        exported = public_evidence(self.area, self.replacements)
        self.assertNotIn('daily-repair-preview.json', exported)
        self.assertNotIn('integration-ui.png', exported)
        self.assertNotIn('raw-task.json',exported)
        self.assertEqual(json.loads(exported['native-roadmap.json'])['calls'],2)
        self.assertEqual((self.area/'daily-repair-preview.json').read_bytes(),original)

    def test_redacted_prepared_file_references_match_the_actual_exported_bytes(self):
        self.write('prepared-45-manifest.json', {'workspace':'D:\\private-workspace\\trials','authorization':None})
        self.write('prepared-45-instructions.json', [{'workspace':'D:\\private-workspace\\trials'}])
        self.write('evaluation-deterministic-report.json', {'prepared_45': {
            'manifest':'D:\\private-workspace\\prepared-45-manifest.json','manifest_file_sha256':'original-manifest-hash',
            'instructions':'D:\\private-workspace\\prepared-45-instructions.json','instructions_sha256':'original-instruction-hash'}})
        exported = public_evidence(self.area,self.replacements)
        prepared = json.loads(exported['evaluation-deterministic-report.json'])['prepared_45']
        self.assertEqual(prepared['manifest'],'prepared-45-manifest.json')
        self.assertEqual(prepared['original_local_manifest_file_sha256'],'original-manifest-hash')
        self.assertEqual(prepared['manifest_file_sha256'],hashlib.sha256(exported['prepared-45-manifest.json']).hexdigest())
        self.assertEqual(prepared['instructions_sha256'],hashlib.sha256(exported['prepared-45-instructions.json']).hexdigest())

    def test_public_receipt_omits_host_state_and_index_checks_every_actual_output(self):
        self.write('delivery-verification.json', {'local_delivery_verified':True,'daily_state':{'policy':'private'},
                   'daily_repair':'private.json','remaining_authorizations':['private host choices'],
                   'checks':[{'tests':256,'exit_code':0}]})
        (self.area/'unit-tests-final.log').write_text('C:\\Users\\private-person; D:/private-workspace; passed 256',encoding='utf-8')
        exported = public_evidence(self.area,self.replacements)
        value = json.loads(exported['delivery-verification.json'])
        self.assertNotIn('daily_state',value)
        self.assertEqual(value['checks'][0]['tests'],256)
        index = json.loads(exported['evidence-index.json'])
        self.assertEqual(set(index),set(exported)-{'evidence-index.json'})
        for name, record in index.items():
            self.assertEqual(record['sha256'],hashlib.sha256(exported[name]).hexdigest())
            self.assertEqual(record['bytes'],len(exported[name]))

    def test_binary_scan_catches_utf16_and_escaped_build_paths(self):
        for data in ['C:\\Users\\private-person\\build'.encode('utf-16-le'),
                     b'D:\\\\private-workspace\\\\build',b'd:/private-workspace/build']:
            with self.subTest(data=data):
                self.assertTrue(private_matches(data,self.replacements))

    def test_runtime_preparation_preserves_source_and_removes_only_generated_metadata(self):
        workspace = self.area / 'workspace'; runtime = workspace / 'runtime'; runtime.mkdir(parents=True)
        module = runtime / 'module.py'; module.write_text('value = 1',encoding='utf-8')
        cache = runtime / '__pycache__'; cache.mkdir(); (cache/'module.pyc').write_bytes(b'compiled cache')
        launchers = runtime / 'Lib/site-packages/bin'; launchers.mkdir(parents=True)
        (launchers/'unused.exe').write_bytes(str(workspace).encode())
        profile = self.area / 'profile'
        result = _runtime.prepare(workspace,profile=profile)
        self.assertEqual(module.read_text(),'value = 1')
        self.assertFalse(cache.exists())
        self.assertFalse((launchers/'unused.exe').exists())
        self.assertEqual(result['bytecode_files_remaining'],0)
        self.assertEqual(result['private_prefix_files'],[])

    def test_runtime_preparation_refuses_to_delete_business_files_to_hide_a_prefix(self):
        workspace = self.area / 'workspace'; runtime = workspace / 'runtime'; runtime.mkdir(parents=True)
        module = runtime / 'module.py'; module.write_text(str(workspace),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'still contains local build metadata'):
            _runtime.prepare(workspace,profile=self.area/'profile')
        self.assertTrue(module.exists())


if __name__=='__main__':unittest.main()
