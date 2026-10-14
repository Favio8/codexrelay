"""Pi Agent SDK scheduler using the existing isolated provider/tool boundaries."""
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from deepseek_mcp import agent_loop as loop
from deepseek_mcp.provider_response import ProviderToolCall, ProviderFunction
from deepseek_mcp.bash_tool import PROCESS_STOP_SECONDS, _create_windows_job, _stop_windows_process_tree
from . import review

# The adapter reuses the vendored engine's controlled provider/tool boundaries instead of
# forking the loop. Those helpers are private, so an upstream snapshot swap fails at runtime
# with an opaque task error unless the bindings are checked explicitly.
REQUIRED_LOOP_SYMBOLS = ('_create_agent_state', '_AgentControls', '_check_cancel', '_remaining_seconds',
                         '_execution_request', '_provider_request',
                         '_compact_history', '_enforce_history_budget', '_encoded_size', '_ensure_request_budget',
                         '_ensure_token_budget_available', '_raise_max_turns', '_validate_tool_batch',
                         '_execute_and_record_tool', '_raise_with_mutation_records', '_build_result',
                         '_call_with_retry', '_record_response', '_publish_telemetry', '_build_telemetry')
REQUIRED_STATE_FIELDS = ('messages', 'tools', 'turns_used', 'applied_steering_messages', 'progress_reporter',
                         'pi_context_messages',
                         'workspace_before', 'workspace_review', 'deadline', 'request_in_flight', 'mutations')


def verify_bindings():
    missing = [name for name in REQUIRED_LOOP_SYMBOLS if not hasattr(loop, name)]
    if not missing:
        fields = getattr(loop, '_AgentState', None)
        if fields is not None and hasattr(fields, '__dataclass_fields__'):
            missing = [name for name in REQUIRED_STATE_FIELDS if name not in fields.__dataclass_fields__]
        else:
            missing = ['_AgentState']
    if missing:
        raise loop.AgentLoopError('vendored engine API changed, missing: ' + ', '.join(missing))


def _stop(process, job):
    """End the runtime and its job object; a failing step never skips the next one."""
    errors = []
    if process.poll() is None:
        try:
            process.terminate()
        except OSError as error:
            errors.append(f'terminate: {error}')
    try:
        process.wait(timeout=PROCESS_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        _stop_windows_process_tree(process)
        try:
            process.wait(timeout=PROCESS_STOP_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f'wait: {error}')
    except OSError as error:
        errors.append(f'wait: {error}')
    for stream in (process.stdin, process.stdout):
        try:
            if stream:
                stream.close()
        except OSError as error:
            errors.append(f'close: {error}')
    if job is not None:
        try:
            job.close()
        except OSError as error:
            errors.append(f'job: {error}')
    return errors


def runtime():
    installed = Path(sys.executable).parent / 'pi'
    selected = installed if installed.is_dir() else Path(__file__).resolve().parents[3] / 'runtime' / 'pi'
    # Tauri supplies an extended Windows path; Node's ESM entrypoint rejects it.
    value = selected.as_posix()
    if value.startswith('//?/UNC/'):
        value = '//' + value[8:]
    elif value.startswith('//?/'):
        value = value[4:]
    return Path(value)


def available(verify=False):
    directory = runtime()
    if not (directory / 'manifest.json').is_file():
        return False
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != '1.2.0':
        return False
    for name, expected in manifest['files'].items():
        file = directory / name
        if not file.is_file() or (verify and hashlib.sha256(file.read_bytes()).hexdigest() != expected):
            return False
    return True


def run_pi(task, config, *, control_poll=None, control_finalize=None, cancel_signal=None,
           execution_lease_fd=None, progress_reporter=None):
    if not available(verify=True):
        raise loop.AgentLoopError('Pi 运行时缺失或校验失败，请重新安装 CodexRelay')
    verify_bindings()
    state = loop._create_agent_state(task, config, loop.build_tool_schemas(config.allowed_tools),
        loop._AgentControls(control_poll, control_finalize, cancel_signal), execution_lease_fd)
    state.progress_reporter = progress_reporter
    check = review.begin(config, state.workspace_before) if state.workspace_before is not None else None
    state.workspace_review = {'review_id':check['review_id'],'status':'running'} if check else None
    process = None
    job = None
    failure = None
    final_text = None
    try:
        environment = {key: os.environ[key] for key in ('SYSTEMROOT','WINDIR','COMSPEC') if key in os.environ}
        environment['PATH'] = os.defpath
        process = subprocess.Popen([str(runtime()/'node.exe'), str(runtime()/'bridge.mjs')],
            cwd=config.workspace, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        job = _create_windows_job(process)
        events = queue.Queue()
        def read():
            try:
                for line in process.stdout:
                    try:
                        events.put(json.loads(line))
                    except ValueError:
                        continue  # One unreadable line must not discard the events behind it.
            except Exception:
                pass
            finally:
                events.put({'op':'closed'})
        threading.Thread(target=read,daemon=True).start()
        def send(value):
            process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n')
            process.stdin.flush()
        send({'op':'start','system':state.messages[0]['content'],'task':task,'tools':state.tools,'model':config.model,'context_window':config.context_window,'max_output_tokens':config.max_output_tokens})
        pending_steers = []
        while True:
            loop._check_cancel(cancel_signal)
            loop._remaining_seconds(state.deadline)
            for message in control_poll() if control_poll else []:
                pending_steers.append(message)
                send({'op':'steer','text':message})
            try:
                event = events.get(timeout=.05)
            except queue.Empty:
                continue
            operation = event['op']
            if operation == 'user':
                if pending_steers and event['text'] in pending_steers:
                    pending_steers.remove(event['text'])
                    state.applied_steering_messages += 1
            elif operation == 'model':
                if state.turns_used >= config.max_turns:
                    loop._raise_max_turns(state)
                loop._ensure_token_budget_available(state)
                state.turns_used += 1
                state.pi_context_messages.append(len(event['messages']))
                state.messages = [state.messages[0], *event['messages']]
                from deepseek_mcp.execution_context import prepare
                size, allowance = prepare(state)
                response = loop._execution_request(state, state.turns_used-1, allowance)
                raw = response.raw['choices'][0]['message']
                if response.choices[0].message.tool_calls:
                    loop._validate_tool_batch(state,response.choices[0].message.tool_calls)
                send({'id':event['id'],'message':raw, 'context': state.messages[:-1], 'epoch': state.context_epoch})
            elif operation == 'tool':
                tool = ProviderToolCall(event['tool_id'],ProviderFunction(event['name'],json.dumps(event['args'])))
                loop._execute_and_record_tool(state,tool,state.turns_used-1)
                send({'id':event['id'],'text':state.messages[-1]['content']})
            elif operation == 'finalize':
                # Queue acknowledgement and application remain separate, as in the original backend.
                extra = list(pending_steers)
                if not extra:
                    extra = control_finalize() if control_finalize else []
                    pending_steers.extend(extra)
                send({'id':event['id'],'messages':extra})
            elif operation == 'done':
                if event.get('error'):
                    raise loop.AgentLoopError(event['error'])
                final_text = event.get('text')
                break
            elif operation == 'closed':
                raise loop.AgentLoopError('Pi runtime disconnected before completion')
    except Exception as error:
        failure = error
    finally:
        cleanup = _stop(process, job) if process else []
        if check:
            try:
                state.workspace_review = review.finish(config, check)
            except Exception as error:
                # Losing the observed check would silently release the recovery gate.
                cleanup.append(f'review: {type(error).__name__}')
                state.workspace_review = review.block(config, check, type(error).__name__)
        if cleanup:
            if isinstance(state.workspace_review, dict):
                # Recorded in the final telemetry so a secondary cleanup failure stays visible.
                state.workspace_review['cleanup_errors'] = cleanup
            cleanup_error = loop.AgentLoopError('Pi runtime cleanup failed: ' + '; '.join(cleanup))
            failure = failure or cleanup_error
    if failure:
        loop._raise_with_mutation_records(state,failure)
    try:
        return loop._build_result(state,final_text,state.turns_used-1)
    except Exception as error:
        loop._raise_with_mutation_records(state,error)
