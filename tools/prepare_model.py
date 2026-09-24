"""Download a pinned public model only on explicit invocation; verify every byte."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Explicitly download about 1.2 GB from Hugging Face')
    args = parser.parse_args()
    from tools.run import load_env
    load_env()
    manifest = json.loads((ROOT / 'tools/qwen_manifest.json').read_text())
    from app.settings import load_settings
    target = load_settings().qwen_model_path
    target.mkdir(parents=True, exist_ok=True)
    for name, record in manifest['files'].items():
        if Path(name).name != name:
            raise ValueError('unsafe manifest path')
        path = target / name
        if path.is_file() and path.stat().st_size == record['bytes'] and digest(path) == record['sha256']:
            continue
        if not args.download:
            raise ValueError('Local model missing or invalid. Explicit download requires --download.')
        from huggingface_hub import hf_hub_download
        downloaded = Path(hf_hub_download(repo_id=manifest['repo'], revision=manifest['revision'],
                                         filename=name, local_dir=target, token=False))
        if downloaded.stat().st_size != record['bytes'] or digest(downloaded) != record['sha256']:
            raise ValueError('model checksum mismatch')
    (target / 'verified_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print('Local Qwen snapshot verified. No question or knowledge-base content was sent.')


if __name__ == '__main__':
    main()
