"""Deployment only. Importing defines resources; never invokes inference."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import modal

APP_NAME = "parallel-llama"
MODEL = "unsloth/Llama-3.2-3B-Instruct"
MODEL_REVISION = "006f5dcd1393c3add266de40994ba96225e9689d"
PORT = 8000
STARTUP_TIMEOUT = 600
REQUEST_TIMEOUT = 120

CACHE_VOLUME_NAME = "parallel-llama-hf-cache"
CACHE_ROOT = Path("/hf-cache")
SNAPSHOT = CACHE_ROOT / "hub" / "models--unsloth--Llama-3.2-3B-Instruct" / "snapshots" / MODEL_REVISION
READY = CACHE_ROOT / f"ready-{MODEL_REVISION}.json"
JSON_METADATA = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "model.safetensors.index.json")
CHAT_TEMPLATE = "chat_template.jinja"
REQUIRED = (*JSON_METADATA, CHAT_TEMPLATE)
cache_volume = modal.Volume.from_name(CACHE_VOLUME_NAME, create_if_missing=True)

# Deployment dependencies stay outside parallel-agent and its lockfile.
# vLLM 0.11.0's tokenizer initialization is incompatible with Transformers 5.x.
image = modal.Image.debian_slim(python_version="3.12").pip_install("vllm==0.11.0", "transformers==4.57.3")
prefetch_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface-hub==0.35.3")
app = modal.App(APP_NAME)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def model_manifest():
    """Fail closed on absent/empty metadata or any shard named by the index."""
    for name in JSON_METADATA:
        data = json.loads((SNAPSHOT / name).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise ValueError(f"Invalid model metadata: {name}")
    if not (SNAPSHOT / CHAT_TEMPLATE).read_text(encoding="utf-8").strip():
        raise ValueError("Missing or empty chat template")
    index = json.loads((SNAPSHOT / "model.safetensors.index.json").read_text())
    shards = set(index["weight_map"].values())
    if not shards:
        raise ValueError("No model shards")
    files = {}
    for name in sorted(set(REQUIRED) | shards):
        if Path(name).name != name:
            raise ValueError("Invalid model file path")
        path = SNAPSHOT / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty model file: {name}")
        files[name] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    return {"model": MODEL, "revision": MODEL_REVISION, "files": files}


def verify_cache():
    try:
        expected = json.loads(READY.read_text())
        if expected != model_manifest():
            raise ValueError("Model cache does not match readiness manifest")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Model cache is not ready; run the CPU prefetch first") from exc


@app.function(
    image=prefetch_image, gpu=None, cpu=2, memory=4096,
    volumes={str(CACHE_ROOT): cache_volume},
    min_containers=0, max_containers=1, buffer_containers=0,
    scaledown_window=30, timeout=1800, startup_timeout=600, retries=0,
)
@modal.concurrent(max_inputs=1)
def prefetch():
    """Explicit, potentially billable CPU operation; never called by serve."""
    from huggingface_hub import HfApi, snapshot_download

    cache_volume.reload()
    if READY.exists():
        verify_cache()
        return
    snapshot_download(
        repo_id=MODEL, revision=MODEL_REVISION,
        cache_dir=str(CACHE_ROOT / "hub"),
        allow_patterns=[*REQUIRED, "*.safetensors"], max_workers=2,
    )
    manifest = model_manifest()
    info = HfApi().model_info(MODEL, revision=MODEL_REVISION, files_metadata=True)
    if info.sha != MODEL_REVISION:
        raise ValueError("Unexpected model revision")
    sizes = {file.rfilename: file.size for file in info.siblings}
    for name, details in manifest["files"].items():
        if sizes.get(name) != details["size"]:
            raise ValueError(f"Incomplete model file: {name}")
    cache_volume.commit()  # Persist complete data BEFORE publishing readiness.
    temporary = READY.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, sort_keys=True))
    temporary.replace(READY)
    try:
        cache_volume.commit()
    except Exception:
        READY.unlink(missing_ok=True)
        raise



def server_command():
    return [
        "vllm", "serve", str(SNAPSHOT),
        "--tokenizer", str(SNAPSHOT),
        "--chat-template", str(SNAPSHOT / CHAT_TEMPLATE),
        "--served-model-name", MODEL,
        "--revision", MODEL_REVISION, "--tokenizer-revision", MODEL_REVISION,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--dtype", "half", "--max-model-len", "8192",
        "--max-num-seqs", "1", "--gpu-memory-utilization", "0.80",
        "--enforce-eager", "--generation-config", "vllm",
        "--disable-log-requests", "--disable-fastapi-docs",
    ]


@app.function(
    image=image,
    gpu="L4",
    volumes={str(CACHE_ROOT): cache_volume},
    cpu=2,
    memory=8192,
    min_containers=0,
    max_containers=1,
    buffer_containers=0,
    scaledown_window=30,
    timeout=REQUEST_TIMEOUT,
    startup_timeout=STARTUP_TIMEOUT,
)
@modal.concurrent(max_inputs=1)
@modal.web_server(PORT, startup_timeout=STARTUP_TIMEOUT, requires_proxy_auth=True)
def serve():
    # Modal's authenticated proxy is the ONLY exposed ingress, protecting every
    # vLLM route (including non-/v1 routes). No tunnel or public side port.
    # No shell, embedded credential, remote-code trust, or local weight download.
    cache_volume.reload()
    verify_cache()
    os.environ.update({
        "HF_HOME": str(CACHE_ROOT), "HF_HUB_CACHE": str(CACHE_ROOT / "hub"),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    })
    subprocess.Popen(server_command())
