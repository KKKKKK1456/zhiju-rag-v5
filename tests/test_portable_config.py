"""Configuration/transport tests: fake subprocesses and HTTP, no model requests."""
import io
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.settings import ROOT, docker_python_command, load_settings
from app.transport_preflight import ModelProxyUnavailableError, require_model_proxy
from app.local_coarse_candidate import BoundedLocalVectors, _NoLocalRedirect


class PortableConfigTests(unittest.TestCase):
    def env(self, **overrides):
        return {'RAGFLOW_DATASET_ID': 'dataset-demo', 'OLLAMA_MODEL_DIGEST': 'a' * 64,
                **overrides}

    def test_defaults_are_portable_without_import_side_effects(self):
        cfg = load_settings(environ={})
        self.assertEqual(cfg.container, 'ragflow-server')
        self.assertEqual(cfg.container_python, '/ragflow/.venv/bin/python')
        self.assertEqual(cfg.kimi_proxy, '')
        self.assertEqual(cfg.qwen_device, 'cpu')
        self.assertEqual(cfg.qwen_model_path, ROOT / 'models/qwen3-reranker-0.6b')
        self.assertEqual(cfg.cache_path, ROOT / 'runtime/article_vectors.sqlite3')

    def test_required_fields_are_stage_specific(self):
        with self.assertRaisesRegex(ValueError, 'RAGFLOW_DATASET_ID is required'):
            load_settings(environ={}, require_dataset=True)
        with self.assertRaisesRegex(ValueError, 'OLLAMA_MODEL_DIGEST is required'):
            load_settings(environ={'RAGFLOW_DATASET_ID': 'dataset-demo'}, require_digest=True)
        cfg = load_settings(environ=self.env(), require_dataset=True, require_digest=True)
        self.assertEqual(cfg.chat_model_id, '')
        with self.assertRaisesRegex(ValueError, 'RAGFLOW_CHAT_MODEL_ID is required'):
            load_settings(environ=self.env(), require_cloud=True)

    def test_proxy_and_ollama_are_local_and_credential_free(self):
        for name in ('KIMI_PROXY', 'OLLAMA_URL'):
            for bad in ('https://example.com:8123', 'http://' + 'test:test' + '@localhost:8123',
                        'http://localhost:8123/a', 'http://localhost:99999',
                        'http://localhost:8123?x=1', 'http://localhost:8123#fragment'):
                with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                    load_settings(environ=self.env(**{name: bad}))
        cfg = load_settings(environ=self.env(KIMI_PROXY='http://host.docker.internal:8123',
                                             OLLAMA_URL='http://[::1]:11555/'))
        self.assertEqual(cfg.ollama_url, 'http://[::1]:11555')
        with self.assertRaises(ValueError):
            load_settings(environ=self.env(OLLAMA_URL='http://host.docker.internal:11434'))

    def test_cache_containment_and_model_constraints(self):
        for key, value in (('VECTOR_CACHE_PATH', '../outside.sqlite3'),
                           ('VECTOR_CACHE_PATH', '/tmp/outside.sqlite3'),
                           ('VECTOR_CACHE_PATH', 'runtime'),
                           ('OLLAMA_MODEL', 'different-model'),
                           ('OLLAMA_MODEL_DIGEST', 'not-a-digest'),
                           ('QWEN_DEVICE', 'cuda'), ('RAGFLOW_CONTAINER', '--privileged')):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                load_settings(environ=self.env(**{key: value}))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'package'
            root.mkdir()
            (root / 'runtime').symlink_to(Path(tmp), target_is_directory=True)
            with self.assertRaises(ValueError):
                load_settings(environ=self.env(), root=root)

    def test_docker_environment_is_explicit_and_never_forwards_secrets(self):
        env = self.env(RAGFLOW_CHAT_MODEL_ID='chat-demo', KIMI_PROXY='http://localhost:8123',
                       OPENAI_API_KEY='TEST_' + 'SENTINEL_MUST_NOT_BE_FORWARDED',
                       HTTP_PROXY='TEST_SENTINEL_MUST_NOT_BE_FORWARDED',
                       RAGFLOW_CONTAINER='custom-ragflow', RAGFLOW_WORKDIR='/custom',
                       RAGFLOW_PYTHON='/custom/venv/bin/python')
        cfg = load_settings(environ=env)
        for cloud in (False, True):
            command = docker_python_command(cfg, 'pass', cloud=cloud)
            forwarded = {command[i + 1].split('=', 1)[0] for i, x in enumerate(command) if x == '-e'}
            expected = {'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'RAGFLOW_DATASET_ID'}
            if cloud:
                expected.update(('RAGFLOW_CHAT_MODEL_ID', 'KIMI_PROXY'))
            self.assertEqual(forwarded, expected)
            self.assertNotIn('TEST_SENTINEL_MUST_NOT_BE_FORWARDED', repr(command))
            self.assertEqual(command[-5:], ['custom-ragflow', '/custom/venv/bin/python', '-u', '-c', 'pass'])

    def test_direct_preflight_makes_no_socket_or_provider_request(self):
        with patch.dict(os.environ, {}, clear=True), patch('app.transport_preflight.socket.create_connection') as connect:
            self.assertFalse(require_model_proxy())
            connect.assert_not_called()

    def test_configured_proxy_preflight_uses_its_actual_port(self):
        with patch.dict(os.environ, {'KIMI_PROXY': 'http://host.docker.internal:8123'}, clear=True):
            with patch('app.transport_preflight.socket.create_connection') as connect:
                self.assertTrue(require_model_proxy())
                connect.assert_called_once_with(('host.docker.internal', 8123), timeout=2)
            with patch('app.transport_preflight.socket.create_connection', side_effect=OSError):
                with self.assertRaises(ModelProxyUnavailableError):
                    require_model_proxy()
        with patch.dict(os.environ, {'KIMI_PROXY': 'http://example.com:8123'}, clear=True):
            with patch('app.transport_preflight.socket.create_connection') as connect:
                with self.assertRaises(ValueError):
                    require_model_proxy()
                connect.assert_not_called()

    def test_bundled_runtime_compiles_without_host_app_imports(self):
        from app.preview_runtime import Planner
        from tools.integrated_local_worker import LocalRecallWorker
        env = self.env(RAGFLOW_CHAT_MODEL_ID='chat-demo')
        for cls, path in ((Planner, 'app.preview_runtime.subprocess.Popen'),
                          (LocalRecallWorker, 'tools.integrated_local_worker.subprocess.Popen')):
            with self.subTest(worker=cls.__name__), patch.dict(os.environ, env, clear=True):
                with patch(path) as popen, patch.object(cls, 'read', return_value={'type': 'ready', 'pid': 123}):
                    instance = cls()
                    code = popen.call_args.args[0][-1]
                    compile(code, '<bundled-worker>', 'exec')
                    self.assertNotIn('from app.', code)
                    self.assertEqual(instance.settings.dataset_id, 'dataset-demo')

    def test_cloud_client_keeps_tls_and_does_not_use_ambient_proxy(self):
        for proxy in ('', 'http://host.docker.internal:8123'):
            with self.subTest(proxy=proxy), patch.dict(os.environ, {'KIMI_PROXY': proxy}, clear=True):
                namespace = {'retrieve': lambda *args: None, 'json': json, 'time': time}
                exec((ROOT / 'app/enhanced.py').read_text(), namespace)
                httpx = types.SimpleNamespace(AsyncClient=MagicMock(), Limits=MagicMock())
                with patch.dict('sys.modules', {'httpx': httpx}):
                    namespace['model_http_client']()
                kwargs = httpx.AsyncClient.call_args.kwargs
                self.assertTrue(kwargs['verify'])
                self.assertFalse(kwargs['follow_redirects'])
                self.assertFalse(kwargs['trust_env'])
                self.assertEqual(kwargs['proxy'], proxy or None)
                namespace['validate_model_endpoint']('https://api.moonshot.cn/v1')
                for bad in ('http://api.moonshot.cn/v1', 'https://example.com/v1',
                            'https://api.moonshot.cn:8443/v1'):
                    with self.assertRaises(ValueError):
                        namespace['validate_model_endpoint'](bad)
                with self.assertRaisesRegex(ValueError, 'RAGFLOW_CHAT_MODEL_ID'):
                    namespace['normalizer_config']('tenant-demo')

    def test_local_vectors_use_configured_loopback_and_fresh_cache(self):
        digest = 'a' * 64
        tags = {'models': [{'name': 'bge-m3:latest', 'digest': digest}]}
        opener = MagicMock()
        opener.open.side_effect = [io.StringIO(json.dumps(tags)),
                                  io.StringIO(json.dumps({'embeddings': [[1] + [0] * 1023]}))]
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener', return_value=opener) as build:
            path = Path(tmp) / 'runtime/vectors.sqlite3'
            vectors = BoundedLocalVectors(path, digest, base_url='http://localhost:11555')
            try:
                self.assertTrue(path.is_file())
                self.assertEqual(vectors.embed(['fixture text']), [[1] + [0] * 1023])
                self.assertEqual(vectors.embed(['fixture text']), [[1] + [0] * 1023])
                self.assertEqual(opener.open.call_count, 2)
                self.assertEqual(opener.open.call_args_list[0].args[0], 'http://localhost:11555/api/tags')
                request = opener.open.call_args_list[1].args[0]
                self.assertEqual(request.full_url, 'http://localhost:11555/api/embed')
                self.assertEqual(json.loads(request.data)['model'], 'bge-m3:latest')
                self.assertIn(_NoLocalRedirect, build.call_args.args)
            finally:
                vectors.close()


if __name__ == '__main__':
    unittest.main()
