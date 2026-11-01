"""Offline reconstruction and fail-closed validation of the original held-out v1."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '058eda7bda5aacffbd859a9fbe8ac174a65c37dd'
ARCHIVE_SHA256 = '5704a00ddc1cc7f2cea83870c73786cdf1f045563af8b062c4c2326f7f33f065'
MANIFEST_SHA256 = '122fd160537ac8e79b82f3766aeef2dd69ca82bfaeee14539ba7fa2c47502403'
DEFAULT_ARCHIVE = ROOT / 'tests/fixtures/parallel-heldout-v1-freeze.tar.gz'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(data, destination):
    """Extract only regular files/directories, never links or traversal paths."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive member: {member.name}')
            target = destination / path
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())


def verify_tree(tree):
    directory = tree / 'benchmarks/heldout_v1'
    raw = (directory / 'manifest.json').read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError('Manifest SHA-256 mismatch')
    if (directory / 'manifest.sha256').read_text().strip() != MANIFEST_SHA256:
        raise ValueError('Manifest seal mismatch')
    manifest = json.loads(raw)
    if manifest['source_commit'] != COMMIT or len(manifest['file_sha256']) != 58:
        raise ValueError('Frozen commit or manifest inventory mismatch')
    mismatches = []
    for name, expected in manifest['file_sha256'].items():
        path = tree / name
        if not path.is_file() or digest(path.read_bytes()) != expected:
            mismatches.append(name)
    if mismatches:
        raise ValueError('Frozen file checksum mismatch: ' + ', '.join(mismatches))
    return manifest


def reconstruct(repository, archive, destination):
    raw = archive.read_bytes()  # No fallback for missing or corrupted inputs.
    if digest(raw) != ARCHIVE_SHA256:
        raise ValueError('Archive SHA-256 mismatch')
    revision = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--verify', COMMIT + '^{commit}'],
                              capture_output=True, text=True)
    if revision.returncode or revision.stdout.strip() != COMMIT:
        raise ValueError('Missing or mismatching frozen Git revision: ' + COMMIT)
    source = subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar', COMMIT],
                            capture_output=True, check=True).stdout
    unpack(source, destination)
    unpack(raw, destination)
    verify_tree(destination)


# -I ignores PYTHONPATH and the caller's directory. -S prevents editable-install
# .pth hooks/sitecustomize from running. Only dependency directories are restored;
# the frozen package is imported first and its origin is checked before pytest.
BOOTSTRAP = r'''
import importlib, pathlib, sys
root = pathlib.Path.cwd().resolve()
sys.path[:0] = [str(root / 'src')] + sys.argv[1:]
import parallel_agent
assert pathlib.Path(parallel_agent.__file__).resolve().is_relative_to(root / 'src')
import pytest
class Isolation:
    def pytest_sessionfinish(self, session, exitstatus):
        for name, module in list(sys.modules.items()):
            if name == 'parallel_agent' or name.startswith('parallel_agent.'):
                assert pathlib.Path(module.__file__).resolve().is_relative_to(root / 'src'), name
raise SystemExit(pytest.main(['-q', '-o', 'addopts=', 'tests/test_heldout.py'], plugins=[Isolation()]))
'''


def run_tests(tree):
    import sysconfig
    dependencies = sorted({sysconfig.get_path('purelib'), sysconfig.get_path('platlib')})
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('PYTHON', 'PYTEST', 'PARALLEL_', 'MODAL_', 'HF_', 'HUGGING_FACE'))}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return subprocess.run([sys.executable, '-I', '-S', '-c', BOOTSTRAP, *dependencies],
                          cwd=tree, env=env).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='parallel-heldout-v1-') as temporary:
            tree = Path(temporary)
            reconstruct(ROOT, args.archive, tree)
            print(f'Verified commit {COMMIT}; archive and manifest SHA-256; 58/58 files, zero mismatches.', flush=True)
            return run_tests(tree)
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'Historical validation BLOCKED: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
