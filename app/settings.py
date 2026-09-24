"""Portable runtime configuration; no credentials, network calls or downloads.

The launcher loads the local .env before importing runtime modules. Configuration
is deliberately read at construction time so importing the offline tests does not
require a dataset, installed models, or a running Docker daemon.
"""
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def _local_url(value, name, *, docker_host=False, optional=False):
    if optional and not value:
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        allowed = host == 'localhost' or docker_host and host == 'host.docker.internal'
        if not allowed:
            try:
                allowed = ipaddress.ip_address(host or '').is_loopback
            except ValueError:
                allowed = False
        if (parsed.scheme not in ('http', 'https') or not allowed
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
    except ValueError:
        raise ValueError(f'{name} must be a credential-free local HTTP(S) URL') from None
    return value.rstrip('/')


def _identifier(value, name, required=False):
    if not value:
        if required:
            raise ValueError(f'{name} is required')
        return ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError(f'{name} must be a valid non-secret identifier')
    return value


@dataclass(frozen=True)
class Settings:
    dataset_id: str
    chat_model_id: str
    container: str
    container_workdir: str
    container_python: str
    kimi_proxy: str
    ollama_url: str
    ollama_model: str
    ollama_model_digest: str
    qwen_model_path: Path
    qwen_device: str
    cache_path: Path


def load_settings(*, require_dataset=False, require_cloud=False,
                  require_digest=False, environ=None, root=None):
    env = os.environ if environ is None else environ
    root = Path(root or ROOT).resolve()
    value = lambda key, default='': str(env.get(key, default)).strip()
    dataset = _identifier(value('RAGFLOW_DATASET_ID'), 'RAGFLOW_DATASET_ID', require_dataset)
    chat = _identifier(value('RAGFLOW_CHAT_MODEL_ID'), 'RAGFLOW_CHAT_MODEL_ID', require_cloud)
    container = value('RAGFLOW_CONTAINER', 'ragflow-server')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', container):
        raise ValueError('RAGFLOW_CONTAINER must be a Docker container name')
    workdir = value('RAGFLOW_WORKDIR', '/ragflow')
    python = value('RAGFLOW_PYTHON', '/ragflow/.venv/bin/python')
    if any(not p.startswith('/') or '\x00' in p or '\n' in p or '\r' in p for p in (workdir, python)):
        raise ValueError('RAGFLOW_WORKDIR and RAGFLOW_PYTHON must be absolute container paths')
    proxy = _local_url(value('KIMI_PROXY'), 'KIMI_PROXY', docker_host=True, optional=True)
    ollama = _local_url(value('OLLAMA_URL', 'http://127.0.0.1:11434'), 'OLLAMA_URL')
    model = value('OLLAMA_MODEL', 'bge-m3:latest')
    if model != 'bge-m3:latest':
        raise ValueError('OLLAMA_MODEL must be bge-m3:latest (1024 dimensions)')
    digest = value('OLLAMA_MODEL_DIGEST')
    if require_digest and not digest:
        raise ValueError('OLLAMA_MODEL_DIGEST is required; pin an already installed model')
    if digest and not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', digest):
        raise ValueError('OLLAMA_MODEL_DIGEST must be the model SHA-256 digest')
    model_path = Path(value('QWEN_MODEL_PATH', 'models/qwen3-reranker-0.6b')).expanduser()
    model_path = (root / model_path).resolve() if not model_path.is_absolute() else model_path.resolve()
    device = value('QWEN_DEVICE', 'cpu')
    if device not in ('cpu', 'mps'):
        raise ValueError('QWEN_DEVICE must be cpu or mps')
    cache = Path(value('VECTOR_CACHE_PATH', 'runtime/article_vectors.sqlite3'))
    if cache.is_absolute():
        raise ValueError('VECTOR_CACHE_PATH must be relative to this package under runtime/')
    resolved_cache = (root / cache).resolve()
    # Reject both traversal and symlinks that escape the package runtime directory.
    if not resolved_cache.is_relative_to(root / 'runtime') or resolved_cache == root / 'runtime':
        raise ValueError('VECTOR_CACHE_PATH must stay inside runtime/')
    return Settings(dataset, chat, container, workdir, python, proxy, ollama,
                    model, digest, model_path, device, resolved_cache)


def docker_python_command(settings, code, *, cloud=False):
    """Pass only public routing configuration, never host credentials to Docker."""
    _identifier(settings.dataset_id, 'RAGFLOW_DATASET_ID', True)
    public_env = {
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': settings.container_workdir,
        'RAGFLOW_DATASET_ID': settings.dataset_id,
    }
    if cloud:
        _identifier(settings.chat_model_id, 'RAGFLOW_CHAT_MODEL_ID', True)
        public_env.update(RAGFLOW_CHAT_MODEL_ID=settings.chat_model_id,
                          KIMI_PROXY=settings.kimi_proxy)
    args = ['docker', 'exec', '-i', '-w', settings.container_workdir]
    for key, value in public_env.items():
        args.extend(['-e', f'{key}={value}'])
    return args + [settings.container, settings.container_python, '-u', '-c', code]
