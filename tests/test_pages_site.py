from pathlib import Path
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_pages_site import DELIVERIES, build_site, safe_name, validate_link


class PagesSiteTest(unittest.TestCase):
    def setUp(self):
        # These small fixtures isolate the filesystem/link publishing boundary;
        # PublishedPagesIntegrityTest below exercises the real semantic gate.
        validator = patch('build_pages_site.validate_publication')
        self.publication_validation = validator.start()
        self.addCleanup(validator.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.output = self.repo / '_site'
        for version in DELIVERIES:
            self.delivery(version, {'index.html': '<a href="player.html">Replay</a>',
                                    'player.html': '<img src="data:image/png;base64,AA==">'})

    def delivery(self, version, files):
        folder = self.repo / 'artifacts' / version
        folder.mkdir(parents=True, exist_ok=True)
        records = {}
        for name, content in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = content.encode('utf-8')
            target.write_bytes(raw)
            records[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        (folder / 'artifact-manifest.json').write_text(json.dumps({'files': records}), encoding='utf-8')

    def test_only_manifested_allowlisted_deliveries_are_published(self):
        for name in ('private/original.png', 'generated/cache.json', 'reports/status.html',
                     'pose_backend.py', 'artifacts/future/secret.html'):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('never publish', encoding='utf-8')
        result = build_site(self.repo, self.output)
        actual = {file.relative_to(self.output).as_posix() for file in self.output.rglob('*') if file.is_file()}
        expected = {'index.html'} | {f'artifacts/{version}/{name}' for version in DELIVERIES
                                     for name in ('index.html', 'player.html', 'artifact-manifest.json')}
        self.assertEqual(actual, expected)
        self.assertEqual(result['files'], 7)
        self.publication_validation.assert_called_once_with(self.repo.resolve() / 'artifacts/v8.1')
        root = (self.output / 'index.html').read_text(encoding='utf-8')
        self.assertIn('href="artifacts/v8.1/index.html"', root)
        self.assertIn('href="artifacts/v7.2/index.html"', root)
        self.assertNotIn('http-equiv="refresh"', root)
        self.assertIn('aria-label="交付版本"', root)
        self.assertEqual((self.output / 'artifacts/v8.1/player.html').read_bytes(),
                         (self.repo / 'artifacts/v8.1/player.html').read_bytes())

    def test_missing_latest_never_falls_back_to_history(self):
        (self.repo / 'artifacts/v8.1/artifact-manifest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Required delivery manifest'):
            build_site(self.repo, self.output)
        self.assertFalse(self.output.exists())

    def test_unlisted_or_modified_delivery_fails_before_output(self):
        (self.repo / 'artifacts/v8.1/private.png').write_bytes(b'private')
        with self.assertRaisesRegex(ValueError, 'unlisted'):
            build_site(self.repo, self.output)
        (self.repo / 'artifacts/v8.1/private.png').unlink()
        (self.repo / 'artifacts/v8.1/player.html').write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'bytes or SHA256'):
            build_site(self.repo, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_and_absolute_links_fail_closed(self):
        for link in ('missing.html', '../../../private/original.png', '/index.html',
                     'file:///C:/Users/private.png', 'C:/Users/private.png',
                     r'C:\Users\private.png', '%2Fprivate.png', '%5cprivate.png',
                     'javascript:alert(1)'):
            with self.subTest(link=link), self.assertRaises(ValueError):
                validate_link('artifacts/v8.1/index.html', link,
                              {'index.html', 'artifacts/v8.1/index.html'})

    def test_cross_version_relative_links_are_valid(self):
        validate_link('artifacts/v8.1/index.html', '../v7.2/index.html#history',
                      {'artifacts/v7.2/index.html'})

    def test_manifest_traversal_and_noncanonical_paths_are_rejected(self):
        for name in ('../outside.html', '/absolute.html', r'..\outside.html',
                     'C:/private.html', 'sub/../index.html', 'sub//index.html',
                     './index.html', '.private', '%2e%2e/private.html'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe artifact path'):
                safe_name(name)

    def test_actual_broken_html_link_is_detected(self):
        self.delivery('v8.1', {'index.html': '<a href="missing.html">missing</a>',
                               'player.html': '<p>Replay</p>'})
        with self.assertRaisesRegex(ValueError, 'Broken or unpublished'):
            build_site(self.repo, self.output)
        self.assertFalse(self.output.exists())

    def test_css_urls_are_checked_but_embedded_source_is_not_executed(self):
        self.delivery('v8.1', {'index.html': '<script>const example = "url(source_example)";</script>',
                               'player.html': '<p>Replay</p>'})
        build_site(self.repo, self.output)
        self.delivery('v8.1', {'index.html': '<style>body {background: url(missing.png)}</style>',
                               'player.html': '<p>Replay</p>'})
        with self.assertRaisesRegex(ValueError, 'Broken or unpublished'):
            build_site(self.repo, self.repo / '_other_site')

    def test_site_and_file_size_limits_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'file size limit'):
            build_site(self.repo, self.output, max_file_bytes=5)
        with self.assertRaisesRegex(ValueError, 'total size limit'):
            build_site(self.repo, self.output, max_site_bytes=5)
        self.assertFalse(self.output.exists())

    def test_old_output_is_not_mixed_or_deleted(self):
        self.output.mkdir()
        private = self.output / 'private.txt'
        private.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            build_site(self.repo, self.output)
        self.assertEqual(private.read_bytes(), b'keep')

    def test_hard_links_are_not_published(self):
        target = self.repo / 'artifacts/v8.1/player.html'
        outside = self.repo / 'same-file.html'
        try:
            os.link(target, outside)
        except OSError as error:
            self.skipTest('Hard links unavailable: ' + str(error))
        with self.assertRaisesRegex(ValueError, 'Hard links'):
            build_site(self.repo, self.output)


class PublishedPagesIntegrityTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.temp = tempfile.TemporaryDirectory(prefix='.pages-integrity-', dir=root / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        shutil.copytree(root / 'artifacts/v8.1', self.repo / 'artifacts/v8.1')
        history = self.repo / 'artifacts/v7.2'
        history.mkdir()
        raw = b'<p>Historical delivery</p>'
        (history / 'index.html').write_bytes(raw)
        (history / 'artifact-manifest.json').write_text(json.dumps({'files': {'index.html': {
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}), encoding='utf-8')

    def rehash(self, name):
        folder = self.repo / 'artifacts/v8.1'
        manifest = folder / 'artifact-manifest.json'
        records = json.loads(manifest.read_text(encoding='utf-8'))
        raw = (folder / name).read_bytes()
        records['files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        manifest.write_text(json.dumps(records), encoding='utf-8')

    def test_rehashed_but_wrong_embedded_source_zip_is_not_publishable(self):
        from package_review_artifacts import _PayloadScripts
        page = self.repo / 'artifacts/v8.1/replay-v8.1-64.html'
        text = page.read_text(encoding='utf-8')
        parser = _PayloadScripts(); parser.feed(text)
        body = parser.payloads['generator-source'][0]
        source = json.loads(body)
        source['payload'] = base64.b64encode(b'wrong source archive').decode()
        page.write_text(text.replace(body, json.dumps(source), 1), encoding='utf-8')
        self.rehash(page.name)
        with self.assertRaisesRegex(ValueError, 'exact complete source ZIP'):
            build_site(self.repo, self.repo / '_site')
        self.assertFalse((self.repo / '_site').exists())

    def test_rehashed_but_different_public_json_is_not_publishable(self):
        data_file = self.repo / 'artifacts/v8.1/painting-data-v8.1-64.json'
        data = json.loads(data_file.read_bytes())
        data['totalDuration'] += 1
        data_file.write_text(json.dumps(data), encoding='utf-8')
        self.rehash(data_file.name)
        with self.assertRaisesRegex(ValueError, 'HTML and public JSON data differ'):
            build_site(self.repo, self.repo / '_site')
        self.assertFalse((self.repo / '_site').exists())


if __name__ == '__main__':
    unittest.main()
