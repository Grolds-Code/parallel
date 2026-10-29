"""Offline deployment contract: fake Modal SDK, fake HTTP, no GPU or downloads."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from parallel_agent import DatabaseState, LLMConfig, LLMConnector, LLMTransportError, Proposal


@pytest.fixture
def deployment(monkeypatch, tmp_path):
    captured = {}

    def decorator(name):
        def configure(*args, **kwargs):
            captured[name] = (args, kwargs)
            def register(function):
                captured[function.__name__ + ':' + name] = (args, kwargs)
                return function
            return register
        return configure

    class Image:
        @classmethod
        def debian_slim(cls, **kwargs):
            captured['image'] = kwargs
            return cls()

        def pip_install(self, *packages):
            self.packages = getattr(self, 'packages', ()) + packages
            return self

    def app(name):
        captured['name'] = name
        return SimpleNamespace(function=decorator('function'))

    events = []
    volume = SimpleNamespace(commit=lambda: events.append('commit'), reload=lambda: events.append('reload'))
    def from_name(name, **kwargs):
        captured['volume'] = (name, kwargs)
        return volume

    monkeypatch.setitem(sys.modules, 'modal' , SimpleNamespace(
        Volume=SimpleNamespace(from_name=from_name), Image=Image, App=app, concurrent=decorator('concurrent'),
        web_server=decorator('web_server'),
    ))
    launches = []
    monkeypatch.setattr('subprocess.Popen', lambda command: launches.append(command))
    path = Path(__file__).resolve().parents[1] / 'deployment/modal/app.py'
    spec = importlib.util.spec_from_file_location('modal_deployment_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert launches == []  # Import must not start inference.
    monkeypatch.setattr(module, 'CACHE_ROOT', tmp_path)
    monkeypatch.setattr(module, 'SNAPSHOT', tmp_path / 'snapshot')
    monkeypatch.setattr(module, 'READY', tmp_path / 'ready.json')
    captured['events'] = events
    for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE'):
        monkeypatch.setenv(key, '')
    return module, captured, launches


def test_deployment_dependency_pins(deployment):
    module, captured, _ = deployment
    serving_image = captured['serve:function'][1]['image']
    assert serving_image is module.image
    # Exact pins prevent vLLM's open-ended requirement from selecting 5.x.
    assert serving_image.packages == ('vllm==0.11.0', 'transformers==4.57.3')
    prefetch_image = captured['prefetch:function'][1]['image']
    assert prefetch_image is module.prefetch_image
    assert prefetch_image.packages == ('huggingface-hub==0.35.3',)


def test_cost_and_authentication_limits(deployment, monkeypatch):
    module, captured, launches = deployment
    settings = captured['serve:function'][1]
    assert settings['gpu'] == 'L4'
    assert settings['min_containers'] == settings['buffer_containers'] == 0
    assert settings['max_containers'] == 1
    assert 0 < settings['scaledown_window'] <= 60
    assert 0 < settings['timeout'] <= 120
    assert 0 < settings['startup_timeout'] <= 600
    assert 'retries' not in settings  # Modal Web Functions reject even retries=0.
    assert captured['concurrent'][1] == {'max_inputs': 1}
    assert captured['web_server'] == ((8000,), {
        'startup_timeout': 600, 'requires_proxy_auth': True,
    })
    monkeypatch.setattr(module, "verify_cache", lambda: None)
    module.serve()  # Popen is mocked; no process or SDK calls.
    command = launches[0]
    assert command[:3] == ['vllm', 'serve', str(module.SNAPSHOT)]
    assert command[command.index('--chat-template') + 1] == str(module.SNAPSHOT / 'chat_template.jinja')
    assert command[command.index('--served-model-name') + 1] == module.MODEL
    assert command[command.index('--max-model-len') + 1] == '8192'
    assert command[command.index('--max-num-seqs') + 1] == '1'
    assert command[command.index('--revision') + 1] == module.MODEL_REVISION
    assert command[command.index('--tokenizer-revision') + 1] == module.MODEL_REVISION
    assert '--trust-remote-code' not in command
    assert '--disable-log-requests' in command


@pytest.mark.parametrize('status', [200, 401, 403, 408, 429, 504])
def test_existing_connector_modal_contract(deployment, monkeypatch, status):
    module, _, _ = deployment
    monkeypatch.setenv('PARALLEL_LLM_BASE_URL', 'https://workspace--parallel-llama-serve.modal.run/v1')
    monkeypatch.setenv('PARALLEL_LLM_MODEL', module.MODEL)
    monkeypatch.setenv('PARALLEL_LLM_API_KEY', 'wk-test.ws-test')
    config = LLMConfig.from_env()

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == config.base_url + '/chat/completions'
            assert request.get_header('Authorization') == 'Bearer wk-test.ws-test'
            body = json.loads(request.data)
            assert body['model'] == module.MODEL
            assert body['stream'] is False
            assert body['response_format'] == {'type': 'json_object'}
            assert body['max_tokens'] == 4096
            assert [m['role'] for m in body['messages']] == ['system', 'user']
            assert 0 < timeout <= 30
            if status != 200:
                raise HTTPError(request.full_url, status, 'redacted', {}, None)
            return Response(json.dumps({'choices': [{
                'finish_reason': 'stop', 'message': {
                    'role': 'assistant', 'content': '{"operations": []}',
                },
            }]}).encode())

    monkeypatch.setattr('parallel_agent.llm.build_opener', lambda *args: Opener())
    connector = LLMConnector(config)
    if status == 200:
        assert connector.generate('Return a candidate', DatabaseState()) == Proposal(())
    else:
        with pytest.raises(LLMTransportError):
            connector.generate('Return a candidate', DatabaseState())


def fake_download(module, monkeypatch, *, failure=False, incomplete=False):
    calls = []
    def download(**kwargs):
        calls.append(kwargs)
        module.SNAPSHOT.mkdir(exist_ok=True)
        for name in module.JSON_METADATA:
            (module.SNAPSHOT / name).write_text('{"ok": true}')
        (module.SNAPSHOT / 'chat_template.jinja').write_text(
            '{% for message in messages %}{{ message.content }}{% endfor %}', encoding='utf-8')
        (module.SNAPSHOT / 'model.safetensors.index.json').write_text(
            '{"weight_map": {"a": "model-00001.safetensors", "b": "model-00002.safetensors"}}')
        (module.SNAPSHOT / 'model-00001.safetensors').write_bytes(b'fake weights')
        if failure:
            raise OSError('download interrupted')
        if not incomplete:
            (module.SNAPSHOT / 'model-00002.safetensors').write_bytes(b'other weights')
    def info(*args, **kwargs):
        assert args == (module.MODEL,)
        assert kwargs == {'revision': module.MODEL_REVISION, 'files_metadata': True}
        return SimpleNamespace(sha=module.MODEL_REVISION, siblings=[
            SimpleNamespace(rfilename=p.name, size=p.stat().st_size)
            for p in module.SNAPSHOT.iterdir()])
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(
        snapshot_download=download, HfApi=lambda: SimpleNamespace(model_info=info)))
    return calls


def test_cpu_prefetch_and_shared_volume(deployment, monkeypatch):
    module, captured, launches = deployment
    cpu = captured['prefetch:function'][1]
    gpu = captured['serve:function'][1]
    assert captured['volume'] == ('parallel-llama-hf-cache', {'create_if_missing': True})
    assert cpu['volumes'] == gpu['volumes'] == {'/hf-cache': module.cache_volume}
    assert cpu['gpu'] is None
    assert cpu['cpu'] == 2 and cpu['memory'] == 4096
    assert cpu['min_containers'] == cpu['buffer_containers'] == cpu['retries'] == 0
    assert 'prefetch:web_server' not in captured
    assert cpu['max_containers'] == 1 and cpu['timeout'] == 1800
    assert captured['prefetch:concurrent'][1] == {'max_inputs': 1}
    calls = fake_download(module, monkeypatch)
    snapshots = []
    monkeypatch.setattr(module.cache_volume, 'commit', lambda: snapshots.append(module.READY.exists()))
    module.prefetch()
    assert snapshots == [False, True]
    assert calls[0]['revision'] == module.MODEL_REVISION
    assert calls[0]['repo_id'] == module.MODEL
    assert calls[0]['cache_dir'] == str(module.CACHE_ROOT / 'hub')
    assert calls[0]['max_workers'] == 2
    assert calls[0]['allow_patterns'] == [*module.JSON_METADATA, 'chat_template.jinja', '*.safetensors']
    template = module.SNAPSHOT / 'chat_template.jinja'
    manifest = json.loads(module.READY.read_text())
    assert manifest['files']['chat_template.jinja'] == {
        'size': template.stat().st_size, 'sha256': module.file_hash(template),
    }
    module.verify_cache()
    module.prefetch()
    assert len(calls) == 1
    module.serve()
    assert len(launches) == 1
    assert __import__('os').environ['HF_HUB_OFFLINE'] == '1'
    assert launches[0][launches[0].index('--chat-template') + 1] == str(template)


@pytest.mark.parametrize('damage', ['missing', 'empty', 'whitespace', 'corrupt', 'invalid_utf8', 'old_manifest'])
def test_template_damage_blocks_inference(deployment, monkeypatch, damage):
    module, _, launches = deployment
    fake_download(module, monkeypatch)
    module.prefetch()
    template = module.SNAPSHOT / 'chat_template.jinja'
    if damage == 'missing':
        template.unlink()
    elif damage == 'empty':
        template.write_text('')
    elif damage == 'whitespace':
        template.write_text(' \n\t')
    elif damage == 'corrupt':
        template.write_text(template.read_text().replace('content', 'CONTENT'))
    elif damage == 'invalid_utf8':
        template.write_bytes(b'\xff')
    else:
        manifest = json.loads(module.READY.read_text())
        del manifest['files']['chat_template.jinja']
        module.READY.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='CPU prefetch'):
        module.serve()
    assert launches == []


@pytest.mark.parametrize('name,content', [
    ('chat_template.jinja', ''),
    ('chat_template.jinja', ' \n\t'),
    ('config.json', 'not JSON'),
    ('config.json', '{}'),
])
def test_invalid_metadata_not_published(deployment, monkeypatch, name, content):
    module, captured, _ = deployment
    fake_download(module, monkeypatch)
    hub = sys.modules['huggingface_hub']
    original = hub.snapshot_download
    def download(**kwargs):
        original(**kwargs)
        (module.SNAPSHOT / name).write_text(content)
    monkeypatch.setattr(hub, 'snapshot_download', download)
    with pytest.raises(ValueError):
        module.prefetch()
    assert not module.READY.exists()
    assert 'commit' not in captured['events']


@pytest.mark.parametrize('failure,incomplete', [(True, False), (False, True)])
def test_failed_download_not_ready(deployment, monkeypatch, failure, incomplete):
    module, captured, launches = deployment
    fake_download(module, monkeypatch, failure=failure, incomplete=incomplete)
    with pytest.raises((OSError, ValueError)):
        module.prefetch()
    assert not module.READY.exists()
    assert 'commit' not in captured['events']
    with pytest.raises(RuntimeError):
        module.serve()
    assert launches == []


@pytest.mark.parametrize('commit_number', [1, 2])
def test_failed_commit_not_ready(deployment, monkeypatch, commit_number):
    module, _, launches = deployment
    fake_download(module, monkeypatch)
    count = 0
    def commit():
        nonlocal count
        count += 1
        if count == commit_number:
            raise OSError('commit failed')
    monkeypatch.setattr(module.cache_volume, 'commit', commit)
    with pytest.raises(OSError):
        module.prefetch()
    assert not module.READY.exists()
    with pytest.raises(RuntimeError):
        module.serve()
    assert launches == []


@pytest.mark.parametrize('damage', ['missing', 'empty', 'corrupt', 'revision', 'marker'])
def test_cache_damage_blocks_inference(deployment, monkeypatch, damage):
    module, _, launches = deployment
    fake_download(module, monkeypatch)
    module.prefetch()
    shard = module.SNAPSHOT / 'model-00001.safetensors'
    if damage == 'missing':
        shard.unlink()
    elif damage == 'empty':
        shard.write_bytes(b'')
    elif damage == 'corrupt':
        shard.write_bytes(b'FAKE weights')  # Same size, different hash.
    elif damage == 'revision':
        data = json.loads(module.READY.read_text())
        data['revision'] = 'wrong'
        module.READY.write_text(json.dumps(data))
    else:
        module.READY.write_text('invalid json')
    with pytest.raises(RuntimeError):
        module.serve()
    assert launches == []


def test_truncated_download_rejected_by_remote_sizes(deployment, monkeypatch):
    module, captured, launches = deployment
    fake_download(module, monkeypatch)
    hub = sys.modules['huggingface_hub']
    original = hub.HfApi
    def info(*args, **kwargs):
        result = original().model_info(*args, **kwargs)
        for file in result.siblings:
            if file.rfilename.endswith('.safetensors'):
                file.size += 1
        return result
    monkeypatch.setattr(hub, 'HfApi', lambda: SimpleNamespace(model_info=info))
    with pytest.raises(ValueError, match='Incomplete model file'):
        module.prefetch()
    assert not module.READY.exists()
    assert 'commit' not in captured['events']
    assert launches == []


def test_unpublished_cache_blocks_startup(deployment):
    module, _, launches = deployment
    with pytest.raises(RuntimeError, match='CPU prefetch'):
        module.serve()
    assert launches == []
