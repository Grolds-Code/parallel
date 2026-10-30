"""Separate CPU-only public lab. Import defines resources; it never contacts Modal."""
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "parallel-public-demo"
# Explicit file manifest: never upload the repository, environment, or model cache.
SOURCE_FILES = (
    "__init__.py", "actions.py", "models.py", "simulator.py", "verifier.py",
    "search.py", "repair.py", "llm.py", "web_demo.py", "public_demo.py",
    "web_assets/index.html", "web_assets/app.js", "web_assets/style.css",
)
image = modal.Image.debian_slim(python_version="3.12").env({
    "PYTHONPATH": "/opt/parallel/src:/opt/parallel/deployment/modal",
})
for name in SOURCE_FILES:
    image = image.add_local_file(
        ROOT / "src/parallel_agent" / name,
        f"/opt/parallel/src/parallel_agent/{name}",
    )
image = image.add_local_file(
    Path(__file__).resolve(), "/opt/parallel/deployment/modal/public_demo.py",
)
app = modal.App(APP_NAME, include_source=False)


@app.function(
    image=image, gpu=None, cpu=(0.25, 0.5), memory=(256, 512),
    min_containers=0, max_containers=1, buffer_containers=0,
    scaledown_window=10, timeout=15, startup_timeout=60,
    secrets=[], volumes={}, block_network=True, restrict_modal_access=True,
)
@modal.concurrent(max_inputs=1)
@modal.asgi_app(requires_proxy_auth=False)
def serve():
    from parallel_agent.public_demo import application
    return application
