"""Run the frozen PARALLEL pilot on Llama 3.1 8B using one Modal GPU."""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID, uuid4

import modal

import parallel_agent
from parallel_agent.llm import LLMConfig
from parallel_agent.oversight_collect import MAX_TOKENS, RUN_TIMEOUT, collect, frozen_requests, make_plan
from parallel_agent.oversight_pilot import read_responses, report, request_jsonl, review_cases


MODEL = "unsloth/Llama-3.1-8B-Instruct"
MODEL_REVISION = "4699cc75b550f9c6f3173fb80f4703b62d946aa5"
VLLM = "0.11.0"
TRANSFORMERS = "4.57.3"
GPU_CHOICES = ["L4", "A10"]
STARTUP_TIMEOUT = 600
MAX_CONTEXT = 2048
PORT = 8000
SERVER = f"http://127.0.0.1:{PORT}"
BASE_URL = f"{SERVER}/v1"
LOCAL_KEY = "loopback-only"
STORE = Path("/oversight")
VOLUME_NAME = "parallel-oversight-llama31-8b"
MODEL_CACHE = STORE / "hub"
SNAPSHOT = MODEL_CACHE / f"models--{MODEL.replace('/', '--')}" / "snapshots" / MODEL_REVISION
MODEL_FILES = (
    "chat_template.jinja", "config.json", "generation_config.json", "model.safetensors.index.json",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "*.safetensors",
)
COLLECTION_FILES = ("plan.json", "journal.jsonl", "responses.jsonl")
SOURCE_HASHES = {
    "llm.py": "50f5232e2cfc9be36733dc750f3aeef4f05138362185330e23b26167a07eff57",
    "oversight_collect.py": "59cb868651e1452623f7819873b7bd28d63d29906c7b5aa767e9d413bf57f0e9",
    "oversight_pilot.py": "1129b67a2fe04375ab6010bc61629cbe1d29a39b623b04e8bd80338c24cea6f5",
}

app = modal.App("parallel-oversight-llama31-8b")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
prefetch_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface-hub==0.35.3")
    .add_local_python_source("parallel_agent")
)
inference_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(f"vllm=={VLLM}", f"transformers=={TRANSFORMERS}")
    .add_local_python_source("parallel_agent")
)


@app.function(image=prefetch_image, cpu=2, memory=4096, volumes={str(STORE): volume},
              max_containers=1, scaledown_window=30, timeout=1800, retries=0)
def prefetch():
    from huggingface_hub import snapshot_download

    volume.reload()
    downloaded = snapshot_download(
        MODEL, revision=MODEL_REVISION, cache_dir=str(MODEL_CACHE),
        allow_patterns=MODEL_FILES, max_workers=2,
    )
    if Path(downloaded) != SNAPSHOT:
        raise RuntimeError("Downloaded model snapshot differs from the pinned revision")
    volume.commit()


def server_command() -> list[str]:
    return [
        "vllm", "serve", str(SNAPSHOT),
        "--tokenizer", str(SNAPSHOT), "--chat-template", str(SNAPSHOT / "chat_template.jinja"),
        "--served-model-name", MODEL, "--host", "127.0.0.1", "--port", str(PORT),
        "--load-format", "safetensors", "--dtype", "half", "--max-model-len", str(MAX_CONTEXT),
        "--max-num-seqs", "1", "--gpu-memory-utilization", "0.90", "--enforce-eager",
        "--generation-config", "vllm", "--disable-log-requests", "--disable-fastapi-docs",
    ]


def wait_for_server(server: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("vLLM exited before becoming ready. See the Modal logs.")
        try:
            with urlopen(f"{SERVER}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError("vLLM did not become ready before the startup deadline")


@app.function(image=inference_image, gpu=GPU_CHOICES, cpu=2, memory=16384,
              volumes={str(STORE): volume}, max_containers=1, scaledown_window=30,
              timeout=STARTUP_TIMEOUT + int(RUN_TIMEOUT) + 60, retries=0)
def replicate(run_id: str, requests: bytes, plan: dict) -> tuple[dict, dict[str, bytes]]:
    source_dir = Path(parallel_agent.__file__).parent
    hashes = {name: sha256((source_dir / name).read_bytes()).hexdigest() for name in SOURCE_HASHES}
    if hashes != SOURCE_HASHES or plan["source_sha256"] != hashes:
        raise RuntimeError("Worker source differs from the frozen collection and scoring code")

    volume.reload()
    run_root = STORE / "runs" / UUID(run_id).hex
    run_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    request_file = run_root / "requests.jsonl"
    request_file.write_bytes(requests)
    rows = frozen_requests(request_file)
    # Reserve the run before inference. A Modal worker restart must not replay it.
    volume.commit()

    os.environ["no_proxy"] = "127.0.0.1"
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    directory = run_root / "collection"
    try:
        server = subprocess.Popen(server_command(), env=env)
        try:
            wait_for_server(server)
            config = LLMConfig(BASE_URL, MODEL, LOCAL_KEY, max_tokens=MAX_TOKENS)
            result = collect(rows, plan, config, directory)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    finally:
        volume.commit()
    return result, {name: (directory / name).read_bytes() for name in COLLECTION_FILES}


@app.local_entrypoint()
def main(execute: bool = False, output_dir: str = "artifacts/oversight-run-llama31-8b"):
    if not execute:
        raise SystemExit("Pass --execute to start one capped Modal replication")
    os.environ.update(PARALLEL_LLM_BASE_URL=BASE_URL, PARALLEL_LLM_MODEL=MODEL, PARALLEL_LLM_API_KEY=LOCAL_KEY)
    plan, _ = make_plan(False)
    if plan["source_sha256"] != SOURCE_HASHES:
        raise SystemExit("Checkout differs from the frozen collection and scoring code")

    directory = Path(output_dir)
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    cases = review_cases()
    requests = request_jsonl(cases).encode("utf-8")
    request_file = directory / "requests.jsonl"
    request_file.write_bytes(requests)
    frozen_requests(request_file)
    run_id = uuid4().hex
    metadata = {
        "run_id": run_id, "provider": "Modal", "model": MODEL, "model_revision": MODEL_REVISION,
        "vllm": VLLM, "transformers": TRANSFORMERS, "dtype": "float16", "max_context": MAX_CONTEXT,
        "gpu_choices": GPU_CHOICES, "collector_commit": plan["collector_commit"],
        "request_sha256": plan["request_sha256"], "modal_volume": VOLUME_NAME,
        "modal_run_path": f"/runs/{run_id}",
    }
    (directory / "replication.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Run {run_id}. Preparing pinned weights for {MODEL}.", flush=True)
    prefetch.remote()
    print("Collecting up to 120 frozen requests.", flush=True)
    result, files = replicate.remote(run_id, requests, plan)
    for name in COLLECTION_FILES:
        (directory / name).write_bytes(files[name])
    responses_path = directory / "responses.jsonl"
    result["response_path"] = str(responses_path)
    result["reviewer_model"] = MODEL
    result["model_revision"] = MODEL_REVISION
    result["request_sha256"] = plan["request_sha256"]
    if result["unattempted"] == 0:
        score = report(cases, read_responses(responses_path, cases))
        (directory / "score.json").write_text(json.dumps(score, indent=2) + "\n", encoding="utf-8")
        result["conditions"] = score["conditions"]
    print(json.dumps(result, indent=2, sort_keys=True))
