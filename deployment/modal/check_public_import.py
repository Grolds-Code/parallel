"""Run with the real Modal 1.6.3 SDK; no cloud credentials or network required."""
from pathlib import Path
import runpy
import sys


def deny_external(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "subprocess.Popen", "os.system"}:
        raise RuntimeError(f"Offline SDK check blocked {event}")


sys.addaudithook(deny_external)
import modal  # noqa: E402

if modal.__version__ != "1.6.3":
    raise RuntimeError("This check requires modal==1.6.3")
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
namespace = runpy.run_path(str(root / "deployment/modal/public_demo.py"), run_name="public_demo")
if namespace["app"].name != "parallel-public-demo":
    raise RuntimeError("Unexpected application")
# Local factory only: no .remote(), app.run(), hydration, image build, or deploy.
from parallel_agent.public_demo import application  # noqa: E402
if namespace["serve"].local() is not application:
    raise RuntimeError("Unexpected ASGI application")
print("Modal 1.6.3 public deployment import and ASGI factory passed offline.")
