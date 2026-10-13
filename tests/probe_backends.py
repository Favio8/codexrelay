"""B1 spike: installed candidate runtimes against a local deterministic model.

Requires the pinned packages under .tmp/backend-probe; never uses real API keys.
This is a compatibility probe, not a production adapter or model-quality test.
"""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / '.tmp' / 'backend-probe'
WORK = PROBE / 'workspace with spaces'
WORK.mkdir(exist_ok=True)
(WORK / 'input.txt').write_text('BACKEND_PROBE_FILE', encoding='utf-8')
NODE = shutil.which('node')
CALLS = []


class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError):
            pass  # The cancellation probe deliberately closes an in-flight socket.

    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        tools = [tool.get('function', {}).get('name') for tool in request.get('tools', [])]
        CALLS.append({'path': self.path, 'tools': tools, 'model': request.get('model'), 'stream': request.get('stream')})
        messages = request.get('messages', [])
        delay = any('PROBE_DELAY' in str(m.get('content', '')) for m in messages)
        if delay:
            time.sleep(3)
        has_result = any(m.get('role') == 'tool' for m in messages)
        call_read = 'read' in tools and not has_result and not delay
        message = {'role': 'assistant', 'content': None if call_read else 'PROBE_DONE', 'reasoning_content': ''}
        if call_read:
            message['tool_calls'] = [{'id': 'call_probe', 'type': 'function', 'function':
                {'name': 'read', 'arguments': json.dumps({'path': str(WORK / 'input.txt')})}}]
        finish = 'tool_calls' if call_read else 'stop'
        usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110,
                 'prompt_tokens_details': {'cached_tokens': 20}, 'prompt_cache_hit_tokens': 20,
                 'prompt_cache_miss_tokens': 80}
        try:
            self.send_response(200)
            if request.get('stream'):
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                if call_read:
                    message['tool_calls'][0]['index'] = 0
                for delta, reason, count in [(message, None, None), ({}, finish, usage)]:
                    chunk = {'id': 'probe-response', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                             'model': request['model'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                    if count:
                        chunk['usage'] = count
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
            else:
                body = json.dumps({'id': 'probe-response', 'object': 'chat.completion', 'created': int(time.time()),
                    'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': usage}).encode()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def clean_environment(home):
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}}
    env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home), APPDATA=str(home),
               TEMP=str(home), TMP=str(home), PI_OFFLINE='1', PI_TELEMETRY='0', NO_COLOR='1')
    return env


def probe_pi(base_url):
    home = PROBE / 'pi-home'
    env = clean_environment(home)
    env['PI_CODING_AGENT_DIR'] = str(home)
    (home / 'models.json').write_text(json.dumps({'providers': {'probe': {'baseUrl': base_url,
        'api': 'openai-completions', 'apiKey': 'probe-not-a-real-key', 'models': [{'id': 'probe', 'contextWindow': 32768, 'maxTokens': 1024}]}}}), encoding='utf-8')
    cli = PROBE / 'pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js'
    args = [NODE, str(cli), '--mode', 'rpc', '--no-session', '--offline', '--no-extensions', '--no-mcp',
            '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-themes',
            '--provider', 'probe', '--model', 'probe', '--thinking', 'off', '--tools', 'read,grep,find,ls']
    started = time.perf_counter()
    process = subprocess.Popen(args, cwd=WORK, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    events, errors = queue.Queue(), []
    def read():
        for line in process.stdout:
            try:
                events.put(json.loads(line))
            except json.JSONDecodeError:
                errors.append(line[:200])
    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=lambda: errors.extend(process.stderr.readlines()), daemon=True).start()
    def send(kind, **values):
        process.stdin.write(json.dumps({'id': kind, 'type': kind, **values}) + '\n')
        process.stdin.flush()
    seen = []
    def wait(predicate, timeout=20):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            event = events.get(timeout=max(.01, until-time.monotonic()))
            seen.append(event)
            if predicate(event):
                return event
        raise TimeoutError('No matching Pi event')
    result = {'version': '1.2.0'}
    try:
        send('get_state')
        state = wait(lambda e: e.get('command') == 'get_state')
        result['startup_seconds'] = round(time.perf_counter()-started, 3)
        result['state_ok'] = state.get('success')
        before = len(CALLS)
        send('prompt', message='Read input.txt, then reply PROBE_DONE.')
        wait(lambda e: e.get('type') == 'agent_settled')
        result['model_requests'] = CALLS[before:]
        result['read_result_seen'] = 'BACKEND_PROBE_FILE' in json.dumps(seen)
        result['write_tools_excluded'] = not any(set(c['tools']) & {'write','edit','bash','powershell'} for c in CALLS[before:])
        send('get_session_stats')
        result['stats'] = wait(lambda e: e.get('command') == 'get_session_stats').get('data')
        before = len(CALLS)
        send('prompt', message='PROBE_DELAY')
        wait(lambda e: e.get('type') == 'agent_start')
        send('steer', message='QUEUED_PROBE')
        result['steer'] = wait(lambda e: e.get('command') == 'steer')
        send('clear_queue')
        result['clear_queue'] = wait(lambda e: e.get('command') == 'clear_queue')
        cancelled = time.perf_counter()
        send('abort')
        result['abort'] = wait(lambda e: e.get('command') == 'abort')
        result['abort_seconds'] = round(time.perf_counter()-cancelled,3)
        result['cancel_requests'] = len(CALLS)-before
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
        result['event_tail'] = seen[-6:]
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate(); process.wait(timeout=5)
            result['forced_shutdown'] = True
        result['exit_code'] = process.returncode
        result['stderr_tail'] = ''.join(errors)[-1800:]
    return result


def probe_dsh(base_url):
    sys.path.insert(0, str(PROBE / 'dsh-site'))
    from deepseek_harness import DeepSeekHarness
    home = PROBE / 'dsh-home'
    clean = clean_environment(home)
    # SDK merges with the parent environment; only this probe process is replaced.
    previous = dict(os.environ)
    os.environ.clear(); os.environ.update(clean)
    os.environ['PATH'] = str(Path(clean['SYSTEMROOT']) / 'System32')
    result = {'version': '0.1.5rc1', 'profile': 'sdk-minimal', 'system_node_on_path': False}
    harness = DeepSeekHarness(profile='sdk-minimal', dsh_home=str(home), cwd=str(WORK),
        model='deepseek-v4-flash', base_url=base_url, api_key='probe-not-a-real-key',
        initialize_timeout_seconds=20, request_timeout_seconds=20)
    started = time.perf_counter()
    before = len(CALLS)
    try:
        harness.start()
        result['startup_seconds'] = round(time.perf_counter()-started,3)
        run = harness.run('Reply PROBE_DONE only.', session_id='probe-session')
        result.update(final_response=run.final_response, finish_reason=run.finish_reason,
                      event_types=sorted({e.get('type','') for e in run.events}), model_requests=CALLS[before:])
        result['usage_events'] = [e for e in run.events if 'usage' in str(e.get('type','')) or 'usage' in e.get('data',{})]
        result['notification_methods'] = sorted({n.method for n in run.notifications})
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        proc = harness.client._proc
        harness.close()
        result['process_exited'] = proc is None or proc.poll() is not None
        result['stderr_tail'] = '\n'.join(harness.client._stderr_lines)[-1800:]
        os.environ.clear(); os.environ.update(previous)
    return result


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/v1'
    try:
        pi = probe_pi(url)
        print('Pi:',json.dumps(pi,ensure_ascii=False),flush=True)
        dsh = probe_dsh(url)
        print('DSH:',json.dumps(dsh,ensure_ascii=False),flush=True)
        lock = PROBE / 'pi/package-lock.json'
        evidence = dict(kind='local deterministic endpoint; no paid provider', pi=pi, dsh=dsh,
            dsh_packages=json.loads((PROBE/'dsh-packages.json').read_text()),
            pi_lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(), node=subprocess.check_output([NODE,'--version'],text=True).strip())
        output = ROOT/'artifacts/evidence/backend-probe.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        server.shutdown(); server.server_close()
