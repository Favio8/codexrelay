"""Release notices fail closed when full text or reviewed provenance is missing."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_notices', ROOT / 'scripts/generate-notices.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class LicenseNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def fixture(self):
        text = 'Copyright (c) Example upstream.\n' + 'This is the reviewed exact license text.\n' * 5
        license_file = self.directory / 'LICENSE'
        license_file.write_text(text, encoding='utf-8')
        metadata = {'components': [{'name': 'fixture', 'version': '1.0', 'license': 'MIT',
                     'crate_checksum': 'crate-sha', 'upstream_commit': 'commit-sha', 'documents': ['LICENSE']}],
                    'documents': {'LICENSE': {'path': 'LICENSE', 'sha256': hashlib.sha256(license_file.read_bytes()).hexdigest(),
                     'source_url': 'https://example.test/upstream/exact-commit/LICENSE'}}, 'source_archives': []}
        return metadata

    def test_unknown_missing_license_text_or_spdx_only_fails(self):
        with self.assertRaisesRegex(ValueError, 'Missing license text'):
            subject.render_component('unreviewed', '1.0', 'MIT', self.directory)
        (self.directory / 'LICENSE.spdx').write_text('SPDX-License-Identifier: MIT\n' * 6)
        with self.assertRaisesRegex(ValueError, 'Missing license text'):
            subject.render_component('unreviewed', '1.0', 'MIT', self.directory)

    def test_reviewed_text_keeps_exact_attribution_and_source(self):
        metadata = self.fixture()
        documents = subject.supplemental_documents('fixture', '1.0', 'MIT', metadata, self.directory,
                                                  checksum='crate-sha', vcs={'git': {'sha1': 'commit-sha'}})
        self.assertIn('Copyright (c) Example upstream.', documents[0][1])
        self.assertEqual(documents[0][2], metadata['documents']['LICENSE']['source_url'])

    def test_version_license_checksum_and_source_revision_must_match_review(self):
        metadata = self.fixture()
        for name, version, license_id, options in [
            ('fixture', '2.0', 'MIT', {}), ('fixture', '1.0', 'BSD-3-Clause', {}),
            ('fixture', '1.0', 'MIT', {'checksum': 'changed'}),
            ('fixture', '1.0', 'MIT', {'vcs': {'git': {'sha1': 'changed'}}}),
        ]:
            with self.subTest(version=version, license=license_id, options=options), self.assertRaises(ValueError):
                subject.supplemental_documents(name, version, license_id, metadata, self.directory, **options)

    def test_tampering_or_path_escape_is_not_silently_included(self):
        metadata = self.fixture()
        (self.directory / 'LICENSE').write_text('altered upstream copyright')
        with self.assertRaisesRegex(ValueError, 'changed reviewed'):
            subject.supplemental_documents('fixture', '1.0', 'MIT', metadata, self.directory)
        with self.assertRaisesRegex(ValueError, 'reviewed license/source'):
            subject.verified_file(self.directory, '../outside.txt', 'unknown')

    def test_mpl_text_without_source_availability_refuses_release(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError, 'MPL source availability'):
            subject.render_component('fixture', '1.0', 'MPL-2.0', self.directory,
                                     supplements={'source_archives': []}, supplement_directory=self.directory)

    def test_runtime_scope_excludes_build_dependencies_and_proc_macro_subtrees(self):
        packages = [{'id': name, 'targets': [{'kind': ['proc-macro'] if name == 'macro' else ['lib']}]} for name in ['root', 'runtime', 'build', 'macro', 'macro-helper']]
        edge = lambda name, kind: {'pkg': name, 'dep_kinds': [{'kind': kind}]}
        cargo = {'packages': packages, 'resolve': {'root': 'root', 'nodes': [
            {'id': 'root', 'deps': [edge('runtime', None), edge('build', 'build'), edge('macro', None)]},
            {'id': 'runtime', 'deps': []}, {'id': 'build', 'deps': []},
            {'id': 'macro', 'deps': [edge('macro-helper', None)]}, {'id': 'macro-helper', 'deps': []}]}}
        self.assertEqual(subject.runtime_dependency_ids(cargo), {'root', 'runtime'})

    def test_all_reviewed_documents_and_unmodified_mpl_archives_match_manifest(self):
        manifest = subject.load_supplements()
        self.assertEqual(len([item for item in manifest['components'] if item.get('package_manager') != 'npm']), 7)
        self.assertEqual(len([item for item in manifest['components'] if item.get('package_manager') == 'npm']), 1)
        self.assertEqual(len(manifest['source_archives']), 5)
        for item in [*manifest['documents'].values(), *manifest['source_archives']]:
            with self.subTest(file=item['path']):
                self.assertTrue(subject.verified_file(subject.SUPPLEMENTS, item['path'], item['sha256']).is_file())

    def test_reviewed_npm_package_integrity_cannot_drift(self):
        manifest = subject.load_supplements()
        with self.assertRaisesRegex(ValueError, 'npm integrity differs'):
            subject.supplemental_documents('@tauri-apps/plugin-dialog', '2.8.2', 'MIT OR Apache-2.0', manifest,
                                           npm_integrity='different-lock-value')


if __name__ == '__main__':
    unittest.main()
