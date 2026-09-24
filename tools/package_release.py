"""Allowlisted source export with secret-pattern checks and a SHA-256 manifest.

Not a guarantee that arbitrary source is non-sensitive; review changes manually.
Never bundles .env, models, data, logs, runtime output, Git history or symlinks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'.gitignore', '.env.example', 'README.md', 'requirements.txt',
              'THIRD_PARTY_NOTICES.md', 'SECURITY.md'}
ALLOWED = {
    'app': {'.py'}, 'tools': {'.py'}, 'tests': {'.py', '.cjs'},
    'preview': {'.html', '.css', '.js'}, 'docs': {'.md'}, 'backend': {'.patch'},
}
EXACT = {'tools/qwen_manifest.json', 'backend/LICENSE-APACHE-2.0'}
PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'provider_token': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b'),
    'personal_home': re.compile(r'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'credential_url': re.compile(r'https?://[^\s/]+:[^\s/@]+@'),
    'literal_credential': re.compile(r'''(?ix)(?:api[_-]?key|password|access[_-]?token|secret[_-]?key)\s*["']?\s*[:=]\s*["']([A-Za-z0-9_./+=-]{20,})["']'''),
    'original_private_id': re.compile(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])'),
}


def problems(relative, data):
    if len(data) > 2 * 1024 * 1024:
        return ['oversized_source']
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return ['binary_file']
    return [label for label, pattern in PATTERNS.items() if pattern.search(text)]


def source_files(root=ROOT):
    files = []
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        # All unlisted directories (including .git and runtime) are excluded.
        if rel.parts[0] not in ALLOWED and rel.as_posix() not in ROOT_FILES and rel.as_posix() not in EXACT:
            continue
        if path.is_dir():
            continue
        if '__pycache__' in rel.parts:
            continue
        if path.is_symlink():
            raise ValueError('Symlink refused: ' + rel.as_posix())
        allowed = rel.as_posix() in ROOT_FILES or rel.as_posix() in EXACT
        allowed = allowed or (len(rel.parts) == 2 and path.suffix in ALLOWED.get(rel.parts[0], set()))
        if not allowed:
            raise ValueError('Unexpected export path: ' + rel.as_posix())
        files.append((rel.as_posix(), path.read_bytes()))
    for required in ('README.md', '.env.example', 'app/preview_server.py', 'tools/run.py', 'backend/ragflow-exact-anchor.patch'):
        if required not in {name for name, _ in files}:
            raise ValueError('Required source missing: ' + required)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, help='Create sanitized ZIP at this new path')
    args = parser.parse_args()
    files = source_files()
    findings = [{'path': name, 'rules': labels} for name, data in files if (labels := problems(name, data))]
    if findings:
        print(json.dumps({'passed': False, 'findings': findings}, ensure_ascii=False, indent=2))
        return 1
    manifest = {'release': 'v5-experience-20260924.1-share', 'contains_private_corpus': False,
                'contains_model_weights': False, 'cloud_e2e_verified': False,
                'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in files}}
    report = {'passed': True, 'source_files': len(files), 'source_bytes': sum(len(d) for _, d in files),
              'secret_pattern_findings': 0, 'note': 'Pattern scan plus reviewed allowlist, not universal secret detection.'}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                archive.writestr('zhiju-rag-v5/' + name, data)
            archive.writestr('zhiju-rag-v5/PACKAGE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(args.output) as archive:
            for name, data in files:
                if archive.read('zhiju-rag-v5/' + name) != data:
                    raise ValueError('Archive readback mismatch')
        report.update(archive_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(), archive_bytes=args.output.stat().st_size)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

