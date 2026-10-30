"""Capture the complete public deployment contract without importing a cloud SDK."""
import importlib.util
from pathlib import Path
import sys
import shutil
import subprocess
from types import SimpleNamespace

from parallel_agent.public_demo import application

ROOT = Path(__file__).resolve().parents[1]


def test_public_deployment_contract(monkeypatch, tmp_path):
    captured = {}
    files = []
    class Image:
        @classmethod
        def debian_slim(cls, **kwargs):
            captured["base"] = kwargs
            return cls()
        def env(self, values):
            captured["env"] = values
            return self
        def add_local_file(self, local, remote):
            files.append((local, remote))
            return self
    def decorator(name):
        def configure(**kwargs):
            captured[name] = kwargs
            def register(function):
                captured.setdefault("registrations", []).append((name, function.__name__))
                return function
            return register
        return configure
    def app(name, **kwargs):
        captured["app"] = name, kwargs
        return SimpleNamespace(function=decorator("function"))
    # No Secret, Volume, remote call, pip install, run, or deploy capabilities.
    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        Image=Image, App=app, concurrent=decorator("concurrent"), asgi_app=decorator("asgi"),
    ))
    spec = importlib.util.spec_from_file_location("public_modal_contract", ROOT / "deployment/modal/public_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert captured["app"] == ("parallel-public-demo", {"include_source": False})
    assert captured["base"] == {"python_version": "3.12"}
    assert captured["env"] == {"PYTHONPATH": "/opt/parallel/src:/opt/parallel/deployment/modal"}
    assert captured["function"] == dict(
        image=module.image, gpu=None, cpu=(0.25, 0.5), memory=(256, 512),
        min_containers=0, max_containers=1, buffer_containers=0,
        scaledown_window=10, timeout=15, startup_timeout=60,
        secrets=[], volumes={}, block_network=True, restrict_modal_access=True,
    )
    assert captured["concurrent"] == {"max_inputs": 1}
    assert captured["asgi"] == {"requires_proxy_auth": False}
    assert captured["registrations"] == [("asgi", "serve"), ("concurrent", "serve"), ("function", "serve")]
    expected = {"__init__.py", "actions.py", "models.py", "simulator.py", "verifier.py",
                "search.py", "repair.py", "llm.py", "web_demo.py", "public_demo.py",
                "web_assets/index.html", "web_assets/app.js", "web_assets/style.css"}
    assert set(module.SOURCE_FILES) == expected
    assert files == [(ROOT / "src/parallel_agent" / name, f"/opt/parallel/src/parallel_agent/{name}")
                     for name in module.SOURCE_FILES] + [
                         (ROOT / "deployment/modal/public_demo.py", "/opt/parallel/deployment/modal/public_demo.py")]
    assert all(local.is_file() and not local.is_symlink() for local, _ in files)
    assert module.serve() is application
    assert (ROOT / "deployment/modal/requirements.txt").read_text().splitlines()[-1] == "modal==1.6.3"

    # Reproduce the source manifest outside the checkout; no editable install or
    # ambient package path can hide a missing module/asset in the image.
    package_root = tmp_path / "src"
    for local, remote in files[:-1]:
        target = tmp_path / Path(remote).relative_to("/opt/parallel")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
    script = r"""
import sys
from pathlib import Path

def block(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
        raise RuntimeError("External access forbidden")
sys.addaudithook(block)
sys.path.insert(0, sys.argv[1])
from parallel_agent import public_demo
assert Path(public_demo.__file__).is_relative_to(Path(sys.argv[1]))
for path in ("/", "/app.js", "/style.css"):
    assert public_demo.response({"path": path})[0] == 200
for scenario in ("delete", "replacement", "create"):
    assert public_demo.response({"path": "/api/evidence", "query_string":
        ("scenario=" + scenario).encode()})[0] == 200
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(package_root)],
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
