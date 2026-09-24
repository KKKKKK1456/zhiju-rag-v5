import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.package_release import problems
from tools.run import load_env


class PackagingTests(unittest.TestCase):
    def test_secret_patterns(self):
        samples = [('sk-' + 'a' * 30, 'provider_token'),
                   ('/' + 'Users/' + 'someone/file', 'personal_home'),
                   ('api_key="' + 'x' * 30 + '"', 'literal_credential'),
                   ('a' * 32, 'original_private_id')]
        for text, rule in samples:
            self.assertIn(rule, problems('test.py', text.encode()))

    def test_public_model_hash_allowed(self):
        self.assertEqual(problems('hashes.json', ('a' * 64).encode()), [])

    def test_env_is_not_shell(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / '.env'
            path.write_text('RAGFLOW_CONTAINER="plain-name"\nKIMI_PROXY=\n')
            load_env(path)
            self.assertEqual(os.environ['RAGFLOW_CONTAINER'], 'plain-name')
            path.write_text('UNKNOWN_SENSITIVE_SETTING=never-loaded\n')
            with self.assertRaises(ValueError):
                load_env(path)
            self.assertNotIn('UNKNOWN_SENSITIVE_SETTING', os.environ)

    def test_exported_env_wins(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'RAGFLOW_CONTAINER': 'already-set'}):
            path = Path(temp) / '.env'
            path.write_text('RAGFLOW_CONTAINER=file-value\n')
            load_env(path)
            self.assertEqual(os.environ['RAGFLOW_CONTAINER'], 'already-set')

    def test_launcher_and_downloader_share_canonical_model_path(self):
        # Both entry points must honor Settings' ~ expansion and root resolution.
        import inspect
        from tools import run, prepare_model
        self.assertIn('model_path = config.qwen_model_path', inspect.getsource(run.check))
        self.assertIn('target = load_settings().qwen_model_path', inspect.getsource(prepare_model.main))
