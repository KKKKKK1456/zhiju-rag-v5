"""Explicit local launcher. No key loading, shell expansion, or automatic download."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ENV_KEYS = frozenset('RAGFLOW_CONTAINER RAGFLOW_DATASET_ID RAGFLOW_CHAT_MODEL_ID RAGFLOW_WORKDIR RAGFLOW_PYTHON KIMI_PROXY OLLAMA_URL OLLAMA_MODEL OLLAMA_MODEL_DIGEST QWEN_MODEL_PATH QWEN_DEVICE VECTOR_CACHE_PATH'.split())


def load_env(path=ROOT / '.env'):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError('Invalid .env line; use KEY=value, not shell commands')
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if name not in ENV_KEYS:
            raise ValueError('Unknown .env setting; API keys belong in RAGFlow, not this file')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('Unclosed quote in .env')
            value = value[1:-1]
        if '\x00' in value or '\n' in value:
            raise ValueError('Invalid .env value')
        os.environ.setdefault(name, value)


def check(cloud=False):
    """Safe local prerequisite checks only; no questions, model calls or DB exports."""
    from app.settings import load_settings
    config = load_settings(require_dataset=True, require_cloud=cloud, require_digest=True)
    checks = []
    for module in ('torch', 'transformers', 'tokenizers', 'huggingface-hub'):
        try:
            checks.append({'check': module, 'ok': True, 'version': importlib.metadata.version(module)})
        except importlib.metadata.PackageNotFoundError:
            checks.append({'check': module, 'ok': False})
    docker = shutil.which('docker')
    if not docker:
        checks.append({'check': 'docker_container', 'ok': False})
    else:
        probe = subprocess.run([docker, 'inspect', '--format', '{{.State.Running}}',
                                config.container],
                               capture_output=True, text=True, timeout=12)
        checks.append({'check': 'docker_container', 'ok': probe.returncode == 0 and probe.stdout.strip() == 'true'})
    # Never print dataset IDs, credentials, local paths, response bodies or user content.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = config.ollama_url
        with opener.open(url + '/api/tags', timeout=5) as response:
            tags = json.load(response)
        good = any(m.get('name') == config.ollama_model and m.get('digest') == config.ollama_model_digest
                   for m in tags.get('models', []))
        checks.append({'check': 'ollama_model_digest', 'ok': good})
    except Exception:
        checks.append({'check': 'ollama_model_digest', 'ok': False})
    model_path = config.qwen_model_path
    checks.append({'check': 'qwen_local_manifest', 'ok': (model_path / 'verified_manifest.json').is_file()})
    print(json.dumps({'checks': checks, 'cloud_calls': 0,
                      'note': 'Prerequisites only; backend API compatibility and answer accuracy are not certified.'}, indent=2))
    return all(item['ok'] for item in checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'check'])
    parser.add_argument('--cloud', action='store_true', help='Enable per-request UI consent for Kimi; does not submit a question')
    parser.add_argument('--port', type=int, default=8772)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    load_env()
    from app.settings import load_settings
    load_settings(require_dataset=True, require_cloud=args.cloud, require_digest=True)
    if args.action == 'check':
        return 0 if check(args.cloud) else 1
    os.chdir(ROOT)
    args_out = [str(ROOT / 'app/preview_server.py'), '--port', str(args.port),
                '--review-protocol', 'separate_support', '--review-thinking',
                '--evidence-policy', 'document_round_robin']
    if args.cloud:
        args_out += ['--cloud-approval-reference', 'operator-enabled-per-request-ui-consent',
                     '--answer-approval-reference', 'operator-enabled-per-request-ui-consent']
    # Foreground process: Ctrl-C stops it. Do not silently install an OS service.
    from app.preview_server import main as serve
    sys.argv = args_out
    serve()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(130)
    except Exception as exc:
        # Avoid emitting config, cloud responses, keys or raw backend logs.
        print('启动未完成：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
