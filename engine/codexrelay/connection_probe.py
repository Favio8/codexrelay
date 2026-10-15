"""Killable, non-replaying probes with response-level metering.

Only the private worker receives credentials. Its JSON events exclude model
text, tool contents and provider error bodies.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QUICK_TEST_SECONDS = 12
FULL_TEST_SECONDS = 45
CLEANUP_SECONDS = 3
PROBE_PROTOCOL_VERSION = 1


class ProbeFailure(ValueError):
    """Safe error carrying metering evidence from a failed probe."""
    def __init__(self, message, record=None):
        super().__init__(message)
        self.record = record or {}


def validate_probe(slot, verification, *, allow_backend=False):
    if slot not in {'flash', 'pro'}:
        raise ValueError('未知模型档位')
    if verification not in ({'quick', 'full', 'backend'} if allow_backend else {'quick', 'full'}):
        raise ValueError('未知连接测试类型')


def _environment():
    names = ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'HTTP_PROXY',
             'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy',
             'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE')
    result = {key: os.environ[key] for key in names if key in os.environ}
    result['PATH'] = os.defpath
    return result


def _events(output):
    result = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get('event') in {'request', 'usage', 'model', 'done'}:
            result.append(value)
    return result


def _run_worker(payload, limit, *, started=None, cancel=None):
    started = time.monotonic() if started is None else started
    deadline = started + limit
    bootstrap = f'import sys;sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r});from codexrelay.connection_probe import worker;worker()'
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    isolated_home = tempfile.TemporaryDirectory(prefix='codexrelay-probe-home-')
    environment = _environment()
    environment.update(HOME=isolated_home.name, USERPROFILE=isolated_home.name, TEMP=isolated_home.name, TMP=isolated_home.name)
    process = subprocess.Popen([sys.executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=environment, **options)
    output, job, failure, cleanup_ok = b'', None, None, True
    operation_ms = 0
    try:
        if os.name == 'nt':
            from deepseek_mcp.bash_tool import _create_windows_job
            job = _create_windows_job(process)
        payload['timeout'] = max(.01, deadline - time.monotonic())
        incoming = json.dumps(payload).encode('utf-8')
        while True:
            if cancel is not None and cancel.is_set():
                failure = '测试已取消；已结束在途请求。'
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = f'测试未在 {limit} 秒内完成，请检查网络或重试。'
                break
            try:
                output, _ = process.communicate(incoming, timeout=min(.1, remaining) if cancel is not None else remaining)
                break
            except subprocess.TimeoutExpired:
                incoming = None
                if cancel is None:
                    failure = f'测试未在 {limit} 秒内完成，请检查网络或重试。'
                    break
        operation_ms = round((time.monotonic() - started) * 1000)
    finally:
        cleanup_started = time.monotonic()
        if job is not None:
            cleanup_ok = bool(job.close())
        elif os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        try:
            output, _ = process.communicate(timeout=CLEANUP_SECONDS)
        except subprocess.TimeoutExpired:
            cleanup_ok = False
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        isolated_home.cleanup()
        cleanup_ms = round((time.monotonic() - cleanup_started) * 1000)
    events = _events(output)
    usage = [event['usage'] for event in events if event['event'] == 'usage']
    requests = sum(event['event'] == 'request' for event in events)
    reported_models = sorted({event['value'] for event in events if event['event'] == 'model' and event.get('value')})
    for _ in range(max(0, requests - len(usage))):
        usage.append({'available': False, 'prompt': None, 'completion': None, 'gap': 'response_not_received'})
    done = next((event for event in reversed(events) if event['event'] == 'done'), {})
    record = dict(ok=bool(done.get('ok')) and failure is None and cleanup_ok,
                  elapsed_ms=operation_ms, cleanup_ms=cleanup_ms, cleanup_complete=cleanup_ok,
                  deadline_seconds=limit, timed_out=bool(failure and '秒内完成' in failure) or done.get('error_code') == 'probe_timeout',
                  request_count=requests, usage_events=usage, protocol='openai-chat-completions',
                  tool_roundtrip=bool(done.get('tool_roundtrip')), backend_verified=bool(done.get('backend_verified')),
                  tool_evidence=done.get('tool_evidence', []), automatic_retries=0)
    record.update(reported_models=reported_models, actual_model=reported_models[0] if len(reported_models) == 1 else None,
                  actual_model_basis='provider_response' if len(reported_models) == 1 else 'unknown_or_mixed')
    if done.get('error_type'):
        record['error_type'] = done['error_type']
    if done.get('error_code'):
        record.update(error_code=done['error_code'], http_status=done.get('http_status'))
    if not record['ok']:
        message = failure or (done.get('error') if done else '连接测试进程未返回有效结果，请运行诊断。')
        if not cleanup_ok:
            message = '连接测试清理未完成，请运行诊断。'
        raise ProbeFailure(message, record)
    return record


def probe_model(provider, secret, slot, verification, *, started=None, cancel=None, experiment_id=None, evaluation_state_directory=None):
    validate_probe(slot, verification)
    from deepseek_mcp.provider_settings import is_deepseek_endpoint
    limit = QUICK_TEST_SECONDS if verification == 'quick' else FULL_TEST_SECONDS
    result = _run_worker(dict(base_url=provider['base_url'], model=provider[slot + '_model'],
                              secret=secret or 'local', verification=verification, experiment_id=experiment_id,
                              evaluation_state_directory=evaluation_state_directory), limit, started=started, cancel=cancel)
    return {**result, 'model': provider[slot + '_model'], 'verification': verification,
            'reasoning_mode': 'none' if is_deepseek_endpoint(provider['base_url']) else 'provider-default'}


def probe_backend(provider, secret, slot, backend, workspace, marker, *, started=None, cancel=None, experiment_id=None, evaluation_state_directory=None):
    validate_probe(slot, 'backend', allow_backend=True)
    frozen = {key: value for key, value in provider.items() if key not in {'credential_id', 'api_key'}}
    return _run_worker(dict(provider=frozen, secret=secret or 'local', verification='backend',
                            slot=slot, backend=backend, workspace=str(workspace), marker=marker,
                            experiment_id=experiment_id, evaluation_state_directory=evaluation_state_directory),
                       FULL_TEST_SECONDS, started=started, cancel=cancel)


def _emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


def _reported_model(value, secret):
    # Response identities are untrusted too: never persist an echoed credential.
    if not isinstance(value, str) or not value or len(value) > 500 or any(c in value for c in '\r\n\x00'):
        return None
    return None if secret and secret in value else value


def _usage(response):
    usage = getattr(response, 'usage', None)
    prompt, completion = getattr(usage, 'prompt_tokens', None), getattr(usage, 'completion_tokens', None)
    valid = lambda value: isinstance(value, int) and not isinstance(value, bool) and value >= 0
    available = valid(prompt) and valid(completion)
    cached = getattr(usage, 'prompt_cache_hit_tokens', getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None))
    cache_write = getattr(usage, 'cache_creation_input_tokens', getattr(usage, 'cache_write_input_tokens', None))
    return dict(available=available, prompt=prompt if available else None,
                completion=completion if available else None, cached_input=cached if valid(cached) and available and cached <= prompt else None,
                cache_write_input=cache_write if valid(cache_write) else None,
                gap=None if available else 'provider_omitted_usage')


def _request(payload):
    import httpx
    from openai import OpenAI
    from urllib.parse import urlsplit
    from deepseek_mcp.provider_settings import is_deepseek_endpoint
    from types import SimpleNamespace
    guard = _experiment_guard(payload)
    extra = {'extra_body': {'thinking': {'type': 'disabled'}}} if is_deepseek_endpoint(payload['base_url']) else {}
    local = urlsplit(payload['base_url']).hostname in {'localhost', '127.0.0.1', '::1'}
    with httpx.Client(timeout=payload['timeout'], trust_env=not local) as http_client:
        with OpenAI(api_key=payload['secret'], base_url=payload['base_url'], http_client=http_client, max_retries=0) as client:
            def create(**kwargs):
                reservation = guard.reserve(SimpleNamespace(max_output_tokens=kwargs['max_tokens'], request_kind='probe'),
                                            kwargs['messages'], kwargs.get('tools', [])) if guard else None
                _emit('request')
                response = None
                try:
                    response = client.chat.completions.create(model=payload['model'], **kwargs, **extra)
                finally:
                    usage = _usage(response) if response is not None else None
                    if usage is not None:
                        _emit('usage', usage=usage)
                        _emit('model', value=_reported_model(getattr(response, 'model', None), payload['secret']))
                    if guard:
                        adapted = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=usage['prompt'],
                            completion_tokens=usage['completion'], cached_input_tokens=usage['cached_input'],
                            cache_write_input_tokens=usage['cache_write_input'])) if usage and usage['available'] else None
                        guard.settle(reservation, adapted)
                return response
            if payload['verification'] == 'quick':
                response = create(messages=[{'role': 'user', 'content': 'Reply with exactly CONNECTED.'}], max_tokens=64)
                if not response.choices or not response.choices[0].message.content:
                    raise ProbeFailure('模型未返回有效回复，请重试或运行模型工具验证。')
                return dict(tool_roundtrip=False, backend_verified=False)
            tools = [{'type': 'function', 'function': {'name': 'relay_probe',
                      'description': 'Verify the tool connection',
                      'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
            messages = [{'role': 'user', 'content': 'Call relay_probe once, then reply CONNECTED after its result.'}]
            first = create(messages=messages, tools=tools, max_tokens=512)
            if not first.choices:
                raise ProbeFailure('模型未返回有效回复')
            message = first.choices[0].message
            if not message.tool_calls or len(message.tool_calls) != 1 or message.tool_calls[0].function.name != 'relay_probe':
                raise ProbeFailure('模型未返回要求的结构化工具调用，请检查模型兼容性')
            try:
                if json.loads(message.tool_calls[0].function.arguments) != {}:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ProbeFailure('模型工具参数格式无效，请检查模型兼容性') from None
            messages.append(message.model_dump(exclude_none=True))
            messages.append({'role': 'tool', 'tool_call_id': message.tool_calls[0].id, 'content': 'CONNECTED'})
            final = create(messages=messages, max_tokens=512)
            if not final.choices or not final.choices[0].message.content or final.choices[0].message.tool_calls:
                raise ProbeFailure('模型收到工具结果后未返回有效回复')
            return dict(tool_roundtrip=True, backend_verified=False)


def _backend_request(payload):
    from dataclasses import replace
    from deepseek_mcp import agent_loop as loop, provider_retry
    from .backends import build_config, create_manager
    provider_retry.API_RETRY_ATTEMPTS = 0
    original_request = provider_retry.request_in_subprocess
    def request_once(*args, **kwargs):
        _emit('request')
        result = original_request(*args, **kwargs)
        if result[0] is not None:
            _emit('model', value=_reported_model(result[0].raw.get('model'), payload['secret']))
        return result
    provider_retry.request_in_subprocess = request_once
    original_publish, seen = loop._publish_telemetry, set()
    def publish(state, value):
        for event in value.get('usage_events', []):
            sequence = event.get('sequence')
            if sequence not in seen:
                seen.add(sequence)
                _emit('usage', usage={key: event.get(key) for key in
                      ('available', 'prompt', 'completion', 'cached_input', 'cache_write_input', 'gap')})
        original_publish(state, value)
    loop._publish_telemetry = publish
    config = replace(build_config(payload['provider'], 'readonly', payload['slot'], Path(payload['workspace']), payload['secret']),
                     allowed_tools=['Read'], max_output_tokens=512, max_turns=4,
                     max_run_seconds=max(1, int(payload['timeout'])), max_total_tokens=32768,
                     execution_options={'context_optimization': False},
                     request_guard=_experiment_guard(payload), request_kind='probe')
    result = create_manager(payload['backend']).run_sync('Read probe.txt with the Read tool and return its exact contents.', config)
    marker, valid = payload['marker'], []
    for event in result.get('progress', {}).get('tool_evidence', []):
        if event.get('name') != 'Read' or marker not in event.get('result', ''):
            continue
        supplied = event.get('arguments', {}).get('path', '')
        target = (Path(payload['workspace']) / supplied).resolve()
        if target == (Path(payload['workspace']) / 'probe.txt').resolve() and not event.get('result', '').startswith('ERROR:'):
            valid.append({'tool_id': _reported_model(event.get('tool_id'), payload['secret']) or '[REDACTED_OR_UNKNOWN]', 'name': 'Read', 'path': 'probe.txt',
                          'marker_matched': True, 'turn': event.get('turn')})
    if not valid or marker not in result.get('final_message', ''):
        raise ProbeFailure('后端未完成实际文件读取，请检查模型工具兼容性')
    return dict(tool_roundtrip=True, backend_verified=True, tool_evidence=valid)


def _safe_error(error, payload):
    if isinstance(error, ProbeFailure):
        return str(error)
    if 'NOT_AUTHORIZED' in str(error):
        return '实验未授权或当前不可执行（NOT_AUTHORIZED）；尚未发送模型请求。'
    if any(code in str(error) for code in ('TOKEN_LIMIT', 'COST_LIMIT', 'COST_RESERVATION_UNKNOWN')):
        return '实验累计授权额度不足，已在发送前停止请求。'
    code = getattr(error, 'status_code', None)
    match = re.search(r'\bstatus=(\d{3})\b', str(error))
    code = int(match.group(1)) if code is None and match else code
    if code in {401, 403}:
        return f'鉴权失败（HTTP {code}），请检查 API Key 与访问权限。'
    if code == 404:
        return '接口或模型不存在（HTTP 404），请检查地址和模型名称。'
    if code == 429:
        return '供应商限流或额度不足（HTTP 429），请稍后重试。'
    if code in {400, 422}:
        return f'供应商拒绝请求参数（HTTP {code}），请核对模型名称、推理设置与接口协议。'
    if code == 413:
        return '供应商拒绝过大的请求（HTTP 413），请检查接口请求大小限制。'
    if isinstance(code, int) and 500 <= code <= 599:
        return f'供应商服务暂时异常（HTTP {code}），请稍后手动重试。'
    if type(error).__name__ in {'APITimeoutError', 'ProviderRequestDeadline'} or 'run time budget exceeded' in str(error):
        return '连接测试达到截止时间，请检查网络或重试。'
    if type(error).__name__ == 'APIConnectionError' or 'category=connection' in str(error):
        return '无法连接供应商接口，请检查地址、网络或代理设置。'
    if isinstance(code, int) and 400 <= code <= 599:
        return f'供应商拒绝连接测试（HTTP {code}），请检查供应商设置。'
    return '连接测试失败，请检查网络、模型与执行后端设置；未自动重放。'


def _error_metadata(error):
    code = getattr(error, 'status_code', None)
    match = re.search(r'\bstatus=(\d{3})\b', str(error))
    code = int(match.group(1)) if code is None and match else code
    if not isinstance(code, int) or not 100 <= code <= 599:
        code = None
    if type(error).__name__ in {'APITimeoutError', 'ProviderRequestDeadline'} or 'run time budget exceeded' in str(error):
        category = 'probe_timeout'
    elif type(error).__name__ == 'APIConnectionError' or 'category=connection' in str(error):
        category = 'connection_unreachable'
    elif code is not None:
        category = 'provider_http_error'
    elif isinstance(error, ProbeFailure):
        category = 'protocol_validation_failed'
    else:
        category = 'probe_failed'
    return dict(error_code=category, http_status=code)


def _experiment_guard(payload):
    if not payload.get('experiment_id'):
        return None
    from .state import Store
    from .request_gate import ExperimentRequestGate
    return ExperimentRequestGate(Store(payload['evaluation_state_directory']),
        dict(experiment_id=payload['experiment_id'], trial_id=None, group_id=None, id=None, purpose='probe'))


def worker():
    payload = json.load(sys.stdin)
    try:
        result = _backend_request(payload) if payload['verification'] == 'backend' else _request(payload)
        _emit('done', ok=True, **result)
    except Exception as error:
        _emit('done', ok=False, error=_safe_error(error, payload), error_type=type(error).__name__, **_error_metadata(error))
