"""Regression tests for reconstruction, fail-closed integrity, and import isolation."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location(
    'historical_validator', Path(__file__).resolve().parents[1] / 'tools/validate_heldout_v1.py')
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


@pytest.fixture
def frozen(tmp_path):
    tree = tmp_path / 'historical'
    tree.mkdir()
    v.reconstruct(v.ROOT, v.DEFAULT_ARCHIVE, tree)
    return tree


def test_missing_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        v.reconstruct(v.ROOT, tmp_path / 'missing.tar.gz', tmp_path / 'tree')


def test_missing_git_revision(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path / 'empty')], check=True)
    with pytest.raises(ValueError, match='Missing or mismatching frozen Git revision'):
        v.reconstruct(tmp_path / 'empty', v.DEFAULT_ARCHIVE, tmp_path / 'tree')


def test_corrupt_archive(tmp_path):
    archive = tmp_path / 'corrupt.tar.gz'
    archive.write_bytes(v.DEFAULT_ARCHIVE.read_bytes() + b'corrupt')
    with pytest.raises(ValueError, match='Archive SHA-256 mismatch'):
        v.reconstruct(v.ROOT, archive, tmp_path / 'tree')


@pytest.mark.parametrize('name', ['manifest.json', 'manifest.sha256'])
def test_altered_manifest(frozen, name):
    path = frozen / 'benchmarks/heldout_v1' / name
    path.write_bytes(b'altered')
    with pytest.raises(ValueError, match='Manifest'):
        v.verify_tree(frozen)


def test_altered_benchmark_case_is_the_actual_failure(frozen):
    # Establish a clean baseline, including the historical README, before tampering.
    assert len(v.verify_tree(frozen)['file_sha256']) == 58
    path = frozen / 'benchmarks/heldout_v1/cases.json'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError) as error:
        v.verify_tree(frozen)
    assert str(error.value) == 'Frozen file checksum mismatch: benchmarks/heldout_v1/cases.json'
    # Also exercise the ORIGINAL validator, independently of our preflight.
    result = subprocess.run([v.sys.executable, '-I', '-S', '-c',
        "import sys; sys.path.insert(0, 'src'); from parallel_agent.heldout import validate_freeze; validate_freeze()"],
        cwd=frozen, capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stderr.rstrip().endswith(
        'ValueError: Frozen file checksum mismatch: benchmarks/heldout_v1/cases.json')
    assert 'README' not in result.stderr


def test_mismatching_historical_source(frozen):
    path = frozen / 'src/parallel_agent/pilot_v3.py'
    path.write_bytes(path.read_bytes() + b'\n# drift\n')
    with pytest.raises(ValueError, match='src/parallel_agent/pilot_v3.py'):
        v.verify_tree(frozen)


def test_import_isolation(frozen, tmp_path, monkeypatch):
    poison = tmp_path / 'poison'
    package = poison / 'parallel_agent'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text("raise RuntimeError('CURRENT TREE IMPORTED')")
    (poison / 'sitecustomize.py').write_text("raise RuntimeError('SITE CUSTOMIZATION IMPORTED')")
    monkeypatch.chdir(poison)
    monkeypatch.setenv('PYTHONPATH', str(poison))
    monkeypatch.setenv('PYTEST_ADDOPTS', '--invalid-option')
    monkeypatch.setenv('PYTEST_PLUGINS', 'nonexistent_plugin')
    assert v.run_tests(frozen) == 0
