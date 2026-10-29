# PARALLEL inference

**Built with Llama.** Model: public
[unsloth/Llama-3.2-3B-Instruct](https://huggingface.co/unsloth/Llama-3.2-3B-Instruct),
pinned to revision `006f5dcd1393c3add266de40994ba96225e9689d` for weights and
tokenizer and `chat_template.jinja`. This is Meta Llama, subject to the
[Llama 3.2 Community License](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE)
and [Acceptable Use Policy](https://www.llama.com/llama3_2/use-policy).
Retain [Notice](Notice); include the full license with redistributed materials.
No weights are included in this repository.

## Service contract and limits

`app.py` defines vLLM's `POST /v1/chat/completions` endpoint. It accepts the
existing connector's model, messages, temperature, non-streaming response,
`max_tokens`, and `response_format={"type":"json_object"}`. Its standard choices /
message / content / finish_reason envelope is parsed by `LLMConnector` unchanged.
JSON mode does not establish proposal correctness; local validation still applies.

[Modal proxy authentication](https://modal.com/docs/guide/webhook-proxy-auth)
is required on **all routes**, before container startup. Use a workspace proxy
Token ID and Token Secret joined with a period as the bearer API key. No API key
is embedded in the image or command. Modal account/deployment tokens are different
credentials. There is no public bypass port, tunnel, or unauthenticated endpoint.
Do not remove `requires_proxy_auth=True` or rely on vLLM's API-key flag alone.

One GPU (L4 preferred, A10 fallback), 2 CPU cores, 8 GiB host RAM; minimum containers 0, maximum 1, buffer 0,
30-second idle scale-down, one concurrent HTTP input and one model sequence.
The model context is capped at 8192 tokens (prompt plus output), GPU memory use
at 80%, with eager execution to reduce cold-start compilation. The default local
4096 output-token budget leaves at most 4096 prompt tokens; large repair histories
can exceed this. vLLM should reject overlength requests rather than truncate them.
There are no automatic retries or warmers.

Both container startup and web-server readiness are bounded at 600 seconds;
Modal function requests at 120 seconds. These are not a combined end-to-end
queue/startup deadline. The connector defaults to a 30-second socket timeout,
which can expire during cold starts; disconnects are not guaranteed to immediately
stop GPU work. Modal's separate
[HTTP timeout/redirect behavior](https://modal.com/docs/guide/webhook-timeouts)
also applies. Redirects remain disabled by default. The explicit Modal result
redirect option below resumes an existing request without submitting inference again.

One dedicated named Volume, `parallel-llama-hf-cache`, is mounted at `/hf-cache`
in both functions. Existing project volumes are neither mounted nor modified.
The explicit `prefetch` function uses CPU only (2 cores, 4 GiB, one container,
one input, no retries, 1800-second execution timeout). Its separate image pins
`huggingface-hub==0.35.3`. It downloads only metadata/tokenizer files and safetensors
and the exact `chat_template.jinja` file from the pinned revision, with two download workers.

Prefetch parses required JSON files as JSON, verifies `chat_template.jinja` as
nonempty UTF-8 text (not JSON), checks every weight shard in the index, and checks sizes
against Hugging Face revision metadata. It commits the data before atomically
publishing and committing a model/revision/size/SHA-256 readiness manifest.
Partial files may persist through Modal background commits, but are not ready
without that manifest. Serving reloads the Volume and checks all required files
and hashes before launching vLLM from the local snapshot with Hugging Face and
Transformers offline mode enabled. Serving never invokes prefetch or downloads.
The template is included in the size/SHA-256 manifest and passed explicitly to
vLLM with `--chat-template` pointing to the verified local snapshot file.
Missing, empty, altered, or invalid UTF-8 templates block startup, as do older
readiness manifests that omit the template.
This check happens before inference, but after a GPU container is allocated;
failed validation and hashing/loading time can therefore incur GPU charges.

Run only one prefetch job at a time in the chosen environment; container limits
are not a distributed lock across separate app runs/deployments. Published caches
are immutable: repeat prefetch validates and returns, while corrupt ready caches
fail closed. Stop consumers and explicitly delete only this dedicated cache before
recreating a corrupt cache. No automatic cleanup is implemented. Request logging
is disabled in vLLM; review platform/error logs before sending sensitive data.

## Ordered GPU fallback and compatibility

Repeated live requests waited over 20 minutes for L4 scheduling. Serving now uses
`gpu=["L4", "A10"]`, Modal's documented [ordered GPU fallback](https://modal.com/docs/examples/gpu_fallbacks):
at scale-up Modal tries the GPU types in preference order. This requests one GPU,
not both, and keeps L4 preferred. Fallback does not guarantee availability or a
maximum scheduling wait; both pools can be busy. It does not retry inference or
switch GPUs inside a running container.

Both choices have 24 GB GPU memory (NVIDIA specifications for
[L4](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/l4/PB-11316-001_v01.pdf)
and [A10](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a10/pdf/A10-Product-Brief.pdf)).
[NVIDIA lists](https://developer.nvidia.com/cuda/gpus) L4 at compute capability 8.9
and A10 at 8.6, above [vLLM 0.11.0's requirement](https://docs.vllm.ai/en/v0.11.0/getting_started/installation/gpu.html)
of 7.0. Both support float16. The same Python 3.12 image, `vllm==0.11.0`,
`transformers==4.57.3`, and `--dtype half` apply to either GPU; the tokenizer
compatibility rationale below is unchanged. Neither choice requires quantization
or a different model/cache revision. The approximately 6.44 GB of weights and
single 8192-token sequence are expected to fit either GPU with the existing 80%
GPU-memory budget and eager execution. This is a hardware-capability and memory
estimate, not a GPU runtime test: actual usable memory, CUDA kernels, image
resolution, and startup latency still need live validation on each GPU.

## Observed live startup failure and corrective dependency pin

Live inference logs reported vLLM 0.11.0 failing during tokenizer initialization
with `AttributeError: TokenizersBackend has no attribute all_special_tokens_extended`.
This is a Transformers 5.x compatibility failure: vLLM expects the older tokenizer
API, while its unbounded Transformers dependency permits installation of 5.x.
The serving image now installs `vllm==0.11.0` and `transformers==4.57.3` together
to retain the compatible tokenizer API and prevent selection of Transformers 5.x.

Declared compatibility was checked against the tagged
[vLLM 0.11.0 requirements](https://github.com/vllm-project/vllm/blob/v0.11.0/requirements/common.txt)
and [Transformers 4.57.3 setup](https://github.com/huggingface/transformers/blob/v4.57.3/setup.py):
4.57.3 satisfies vLLM's `transformers>=4.55.2`; Transformers' required
`tokenizers>=0.22.0,<=0.23.0` also satisfies vLLM's `tokenizers>=0.21.1`.
Transformers requires Python >=3.9, compatible with the image's Python 3.12.
Its `huggingface-hub>=0.34.0,<1.0` range includes 0.35.3, although the prefetch
image and its Hub pin remain separate from the serving image.

This correction preserves the cached model revision, GPU configuration,
authentication, scaling limits, and application interfaces. It has only been
checked offline; the corrected image has not been built or deployed and inference
has not been retried by this change. Full transitive dependency resolution and
GPU runtime compatibility remain unverified; these two pins are not a complete
image lockfile.

## Offline checks (safe now)

From the repository root, with the existing development environment provisioned:

```sh
uv run pytest -q
git diff --check
```

Tests replace Modal with a recording stub and HTTP with fake responses. They
verify exact serving and prefetch dependency pins (including exclusion of
Transformers 5.x with vLLM 0.11.0), ordered L4/A10 allocation, float16, 8192-token context, memory budget, eager execution,
deployment limits, mandatory proxy authentication, subprocess arguments,
existing environment configuration, request/envelope compatibility, and failure
handling, cache publication ordering, interrupted downloads, failed commits, and
missing/corrupt cache rejection, including template integrity, JSON/text validation,
and the explicit local chat-template argument. They do not install Modal/vLLM, contact services, or run inference.
They cannot validate actual SDK behavior, GPU compatibility, or edge enforcement.

## Preflight before any future deployment

1. Obtain explicit approval for deployment, remote downloads, and compute spend.
   Review Meta's terms and retain attribution; public availability is not a waiver.
2. Review pinned `modal==1.6.3`, remote `vllm==0.11.0`, and
   `transformers==4.57.3` for supported APIs,
   security fixes, dependency resolution, CUDA/L4/A10 support, and JSON-object mode.
   Pins are a starting configuration, not a fully locked deployment image.
   Check model revision and chat template compatibility. None were GPU-tested here.
3. Confirm the intended Modal workspace/environment, L4 and A10 quotas, current pricing,
   billing alerts, and proxy-token scope. Prefer an isolated environment; proxy
   tokens can authorize other endpoints in their scope. Keep credentials in a
   secret manager and rotate/revoke them when no longer needed.
4. In a separate deployment virtualenv, install `requirements.txt`, authenticate
   the Modal CLI, and create a proxy token in Modal workspace settings. These are
   future operator actions, not steps performed by this change. Do not install
   deployment dependencies into parallel-agent or change its lockfile.
5. **CPU prefetch (potentially billable):** after separate approval for remote
   download/CPU/storage spend, run `modal run deployment/modal/app.py::prefetch`.
   Wait for successful completion before deploying or sending traffic. This can
   build remote images and creates/reuses only `parallel-llama-hf-cache` in the
   selected environment. It downloads approximately 6.44 GB remotely, commits it,
   and does not invoke GPU inference. Do not send requests to ephemeral endpoints.
6. **GPU service deployment:** after deployment approval, run
   `modal deploy deployment/modal/app.py` in the same workspace/environment.
   This registers the L4-preferred/A10-fallback service and may build images; it does not run prefetch.
   With minimum containers zero, inference is a separate action. `modal serve`
   and `modal run` are remote operations, not offline checks.
7. **Model inference (GPU billable):** Copy the actual URL emitted by Modal. Before real use, verify missing/invalid
   proxy credentials are denied on chat, health, docs, and alternate inference
   routes, without starting GPU containers. Then authorize one small synthetic
   request to check JSON mode, finish reason, latency, memory use on each GPU, and timeout
   cancellation. Confirm concurrency stays at one and containers return to zero.
   Stop the deployment if any check fails. These live checks remain unperformed.

## Existing LLMConfig configuration

After deployment, inject these values without committing secrets:

```sh
export PARALLEL_LLM_BASE_URL='https://ACTUAL-MODAL-HOST.modal.run/v1'
export PARALLEL_LLM_MODEL='unsloth/Llama-3.2-3B-Instruct'
read -r -s -p 'Modal proxy Token ID.Token Secret: ' PARALLEL_LLM_API_KEY
export PARALLEL_LLM_API_KEY
```

`LLMConfig.from_env()` uses these three variables directly. The URL includes
`/v1` but excludes `/chat/completions`. Redirects are not enabled by environment
variables. For this Modal endpoint, configure the connector explicitly:

```python
from dataclasses import replace
from parallel_agent import LLMConfig, LLMConnector

config = replace(
    LLMConfig.from_env(),
    modal_result_redirects=True,
    timeout_seconds=180,
    total_timeout_seconds=2400,
    max_result_redirects=20,
)
connector = LLMConnector(config)
```

This only constructs a connector; calling it can incur GPU charges. The explicit
40-minute total budget allows scheduling longer than the observed 26 minutes.
The socket timeout remains independently bounded at 180 seconds, allowing Modal's
150-second HTTP interval; increasing the total budget never increases the socket
timeout. When `total_timeout_seconds` is omitted (`None`), the total budget inherits
`timeout_seconds`; both effective budgets default to 30 seconds.

### Observed live repair transport failure

In the reported live repair incident, GPU scheduling took about 26 minutes and
vLLM initialized, but the original client rejected HTTP 303. Initialization did
not establish successful completion of the repair request. Modal documents that
a Web Function can return 303 after 150 seconds with the original URL plus a
special result query parameter. GET of that URL resumes the original request;
resubmitting the POST would create a new inference request.

The opt-in mechanism accepts only absolute HTTPS 303 result URLs matching the
original scheme, hostname, effective port and exact path, with a nonempty query.
It rejects credentials, fragments (including empty fragments), alternate paths,
cross-origin destinations, duplicate Location headers and ordinary redirects.
It treats Modal's result query as opaque rather than depending on an undocumented
parameter name. Authentication is sent only after origin validation. Follow-up
requests use GET with no POST body. No general redirect handling or retries are
enabled. Redirect bodies are closed without being read; the JSON response retains
its byte cap. A hop cap and monotonic deadline span all result retrievals; each
socket operation is capped by the remaining budget and the socket timeout.

Offline tests cover this transport protocol; no live inference was performed for
this fix. Python/OS DNS resolution and urllib response-header processing cannot
be forcibly interrupted by the monotonic checks; they can delay reporting a
deadline failure. Body reads check the deadline between bounded `read1` calls.
A client timeout does not guarantee cancellation of remote GPU work or billing.
Deployment scaling, startup limits, and function execution limits are unchanged.

## Compute cost and cleanup

As checked on 2026-10-30, [Modal's price list](https://modal.com/pricing) lists:

| GPU | GPU-second | GPU-hour | Estimated container-hour (2 cores, 8 GiB) |
| --- | --- | --- | --- |
| L4 (preferred) | $0.000222 | $0.7992 | $0.957456 |
| A10 (fallback) | $0.000306 | $1.1016 | $1.259856 |

A10 adds $0.000084/second ($0.3024/hour), about 37.8% more GPU cost or
31.6% more estimated container cost for equal runtime. Performance can differ,
so cost per completed request is not established. CPU is $0.0000131/core-second
and memory $0.00000222/GiB-second. Approximate L4/A10 totals are $0.016/$0.021
for 60 seconds and $0.160/$0.210 for 10 minutes. Actual CPU/memory billing can vary.
Include cache validation/loading, active requests, and idle time: the 30-second
tail alone is about $0.008/$0.0105 for L4/A10.
CPU prefetch is billed separately: at those illustrative rates, 2 CPU cores
and 4 GiB are about $0.126/hour (about $0.063 for the 30-minute execution limit),
excluding builds, network, and storage. Persistent cache storage can remain
billable after scale-to-zero or app stop. Builds and other account usage may add charges. Verify current rates
and billing behavior before deployment. No credits or free allowance are assumed.

Scale-to-zero removes ongoing GPU compute after idle shutdown; it is not a
spending cap. Continuous authenticated traffic could keep the one container
running all month (roughly $689 on L4 or $907 on A10 for 30 days at these estimates). Set account alerts
and stop the app when finished. Maximum containers 1 is a concurrency limit,
not a total-dollar budget or protection against a stolen valid token.

For cleanup after a future trial: use `modal app list` in the deployment
environment, identify the exact `parallel-llama` app ID, then
`modal app stop <APP_ID>`. Confirm stopped status and zero running containers in
the dashboard, revoke its proxy token in workspace settings, unset the three
`PARALLEL_LLM_*` variables, and inspect billing for remaining activity. This recipe
leaves the named cache intact. **Eventual cache deletion is a separate,
destructive operator action:** after confirming all consumers and prefetch jobs
are stopped and the environment is correct, delete only this deployment's
`parallel-llama-hf-cache` with `modal volume delete parallel-llama-hf-cache`.
Confirm its identity/ownership first; never delete other project volumes, apps,
or shared tokens. Deletion requires a new CPU prefetch before later inference.
Modal notes storage billing may persist for up to four days after deletion.
Remove the separate local deployment virtualenv if no longer needed.

## SDK review and remaining validation

The deployment SDK pin was updated from 1.2.1 to 1.6.3, the current release in
[Modal's release notes](https://modal.com/docs/sdk/py/releases) on 2026-10-28.
The documented [function options](https://modal.com/docs/sdk/py/latest/App),
[authenticated web server](https://modal.com/docs/sdk/py/latest/web_server), and
[Volume API](https://modal.com/docs/sdk/py/latest/Volume) support the APIs used here.
See [Volume persistence semantics](https://modal.com/docs/guide/volumes) for
commit/reload behavior and background persistence. A real Modal 1.6.3 SDK import
was also checked offline with network connections and subprocess launches blocked;
both functions registered without invoking them. This validates local SDK acceptance,
not scheduling, image builds, GPU execution, or cloud integration. The inspection
of `App.registered_functions` emitted a deprecation warning (removal in SDK 1.7.0);
the deployment does not use that property. The project dependencies/lockfile stay unchanged.

Remaining risks: image dependency resolution and vLLM/CUDA compatibility on both L4 and A10,
model/JSON-mode compatibility, Hugging Face availability, download timeout,
Volume throughput and full-file hash time within the startup budget, actual proxy
enforcement, and cancellation/billing behavior need an authorized live trial.
Hashes detect changes after prefetch; they are not independent publisher signatures.
Do not modify a published snapshot while serving. Resource limits bound concurrency
and per-invocation duration, not total spend or persistent storage growth.
