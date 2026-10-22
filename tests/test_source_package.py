"""Regression tests for the standalone, read-only source-integrity checker."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_verify_source_test', ROOT / 'tools/verify_source.py')
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'app').mkdir()
        (self.root / 'app/test.py').write_bytes(b'print("ok")\n')
        self.hash = hashlib.sha256((self.root / 'app/test.py').read_bytes()).hexdigest()

    def manifest(self, text=None):
        text = text if text is not None else f'{self.hash}  app/test.py\n'
        (self.root / 'SHA256SUMS.txt').write_text(text, encoding='utf-8')

    def test_valid_file(self):
        self.manifest()
        self.assertEqual(VERIFY.verify_manifest(self.root), (1, []))

    def test_modified_file(self):
        self.manifest()
        (self.root / 'app/test.py').write_text('changed')
        count, errors = VERIFY.verify_manifest(self.root)
        self.assertEqual(count, 1)
        self.assertTrue(any('mismatch' in s for s in errors))

    def test_missing_file(self):
        self.manifest()
        (self.root / 'app/test.py').unlink()
        self.assertTrue(VERIFY.verify_manifest(self.root)[1])

    def test_missing_manifest(self):
        self.assertTrue(VERIFY.verify_manifest(self.root)[1])

    def test_empty_manifest_is_not_success(self):
        self.manifest('')
        self.assertTrue(VERIFY.verify_manifest(self.root)[1])

    def test_bad_manifest_is_not_success(self):
        self.manifest('not a hash  app/test.py\n')
        self.assertTrue(VERIFY.verify_manifest(self.root)[1])

    def test_traversal_is_rejected(self):
        for name in ('../external.py', '/tmp/external.py', 'C:/external.py', 'app\\test.py'):
            with self.subTest(name=name):
                self.manifest(f'{self.hash}  {name}\n')
                self.assertTrue(VERIFY.verify_manifest(self.root)[1])

    def test_duplicates_are_rejected(self):
        self.manifest(f'{self.hash}  app/test.py\n' * 2)
        self.assertTrue(VERIFY.verify_manifest(self.root)[1])

    def test_generated_extras_are_not_modified(self):
        self.manifest()
        extra = self.root / 'config.json'
        extra.write_bytes(b'{"personal":true}\n')
        self.assertEqual(VERIFY.verify_manifest(self.root), (1, []))
        self.assertEqual(extra.read_bytes(), b'{"personal":true}\n')


if __name__ == '__main__':
    unittest.main()
