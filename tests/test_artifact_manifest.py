from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_artifact_manifest import verify_manifest


class ArtifactManifestTest(unittest.TestCase):
    def test_line_ending_conversion_is_an_integrity_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = b'<!doctype html>\r\n<p>packed player</p>\r\n'
            (root / 'player.html').write_bytes(raw)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'files': {'player.html': {
                'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}), encoding='utf-8')
            self.assertEqual(verify_manifest(manifest)['filesVerified'], 1)
            (root / 'unlisted.html').write_bytes(b'not in delivery manifest')
            with self.assertRaisesRegex(ValueError, 'unlisted'):
                verify_manifest(manifest)
            (root / 'unlisted.html').unlink()
            (root / 'player.html').write_bytes(raw.replace(b'\r\n', b'\n'))
            with self.assertRaisesRegex(ValueError, 'bytes or SHA256 changed'):
                verify_manifest(manifest)

    def test_manifest_cannot_read_outside_the_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'delivery').mkdir()
            (root / 'outside.txt').write_bytes(b'outside')
            manifest = root / 'delivery/manifest.json'
            manifest.write_text(json.dumps({'files': {'../outside.txt': {
                'bytes': 7, 'sha256': hashlib.sha256(b'outside').hexdigest()}}}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'outside the delivery'):
                verify_manifest(manifest)
