"""DeepSeek sub-agent loop with bounded tools, tokens, and wall-clock time."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from .bash_outcome import record_bash_result
from .config import Config
from .hard_deadline import Deadline, HardDeadline
from .mutation_outcome import (
    MutationAccumulator,
    MutationRecord,
    mutation_failure_message,
)
from .provider_process import MAX_OUTPUT_TOKENS_PER_REQUEST
from .provider_retry import (
    AgentBudgetExceeded,
    AgentLoopCancelled,
    AgentLoopError,
    CancellationSignal,
    call_with_retry as _call_with_retry,
    check_cancel as _check_cancel,
    remaining_seconds as _remaining_seconds,
    MutationOutcomeCancelled,
    MutationOutcomeError,
)
from .resource_budget import (
    MAX_TOOL_CALLS_PER_RUN,
    MAX_TOOL_CALLS_PER_TURN,
    MutationBudget,
    ResourceBudgetExceeded,
)
from .tools import build_tool_schemas, execute_tool
from .tool_process import execute_in_subprocess
from .history_compaction import compact_history
from .execution_scope import execution_root, scoped_arguments, scope_violations
from .safety import SandboxViolation, resolve_safe_path
from .progress_guard import ProgressGuard
from .workspace_changes import snapshot, changes, workspace_revision

logger = logging.getLogger(__name__)


class AgentIncompleteResponse(AgentLoopError):
    """A model stopped without producing a usable final answer."""


MAX_TOTAL_TOKENS_PER_RUN = 1_000_000
MAX_PROVIDER_HISTORY_BYTES = 12 * 1024 * 1024
ESTIMATED_BYTES_PER_TOKEN = 4

SYSTEM_PROMPT_TEMPLATE = """You are a coding sub-agent working for a parent coding agent.

You're given a focused task to complete autonomously within a workspace.
You have local tools: {tools}

Rules:
1. Stay strictly within the workspace: {workspace}
2. Read before editing. Don't guess file contents.
3. For batch tasks (translating, extracting, refactoring many files), iterate file-by-file.
4. When done, return a final message summarizing:
   - What you did (file paths affected)
   - Any issues / files you couldn't process
   - A brief summary the parent agent can use without re-reading everything
5. Don't ask clarifying questions back to the parent. Make reasonable assumptions
   and document them in your final message.
6. If a tool returns "ERROR: ...", read the error and decide: retry with fixed input,
   skip the file, or report and stop. Don't blindly loop on the same error.
"""


@dataclass(frozen=True)
class _AgentControls:
    poll: Callable[[], list[str]] | None
    finalize: Callable[[], list[str]] | None
    cancel: CancellationSignal | None


@dataclass
class _AgentState:
    config: Config
    tools: list[dict]
    controls: _AgentControls
    messages: list[dict]
    started: float
    deadline: Deadline
    execution_lease_fd: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    budget_tokens: int = 0
    last_prompt_tokens: int = 0
    last_request_bytes: int = 0
    history_compactions: int = 0
    tool_calls: int = 0
    empty_final_attempts: int = 0
    bash_calls: int = 0
    last_bash_status: str | None = None
    bash_failures: list[dict] = field(default_factory=list)
    bash_results: list[dict] = field(default_factory=list)
    active_tool: dict | None = None
    mutation_budget: MutationBudget = field(default_factory=MutationBudget)
    mutations: MutationAccumulator = field(default_factory=MutationAccumulator)
    progress: ProgressGuard = field(default_factory=ProgressGuard)
    workspace_before: tuple | None = None
    responses_with_usage: int = 0
    responses_without_usage: int = 0
    request_in_flight: bool = False
    turns_used: int = 0
    history_compaction_runs: int = 0
    applied_steering_messages: int = 0
    progress_reporter: Callable[[dict], None] | None = None
    usage_events: list[dict] = field(default_factory=list)
    cached_input_tokens: int = 0
    cache_known_responses: int = 0
    workspace_review: dict | None = None


def run_agent(
    task: str,
    config: Config,
    *,
    control_poll: Callable[[], list[str]] | None = None,
    control_finalize: Callable[[], list[str]] | None = None,
    cancel_signal: CancellationSignal | None = None,
    execution_lease_fd: int | None = None,
    progress_reporter: Callable[[dict], None] | None = None,
) -> dict:
    """Run until a final response or a configured safety limit is reached."""
    controls = _AgentControls(control_poll, control_finalize, cancel_signal)
    state = _create_agent_state(
        task,
        config,
        build_tool_schemas(config.allowed_tools),
        controls,
        execution_lease_fd,
    )
    state.progress_reporter = progress_reporter
    try:
        for turn in range(config.max_turns):
            state.turns_used = turn + 1
            result = _run_turn(state, turn)
            if result is not None:
                return result
        _raise_max_turns(state)
    except (AgentLoopCancelled, AgentLoopError) as error:
        _raise_with_mutation_records(state, error)
    except Exception as error:
        _raise_with_mutation_records(state, error)


def _raise_with_mutation_records(
    state: _AgentState, error: BaseException,
) -> None:
    telemetry = _build_telemetry(state, final=True)
    error.telemetry = telemetry
    _publish_telemetry(state, telemetry)
    if not state.mutations.records:
        raise error
    reason = error if isinstance(error, AgentLoopError) else "unexpected internal failure"
    message = mutation_failure_message(state.mutations.records, reason)
    error_type = (
        MutationOutcomeCancelled
        if isinstance(error, AgentLoopCancelled)
        else MutationOutcomeError
    )
    wrapped = error_type(message, tuple(state.mutations.records))
    wrapped.telemetry = telemetry
    raise wrapped from None


def _create_agent_state(
    task: str,
    config: Config,
    tools: list[dict],
    controls: _AgentControls,
    execution_lease_fd: int | None,
) -> _AgentState:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        tools=", ".join(config.allowed_tools),
        workspace=config.workspace,
    )
    if config.task_root is not None:
        system_prompt += f'\nTask file scope and command working directory: {config.task_root}. File paths may be absolute or relative to this task root. The project lease remains {config.workspace}.\n'
    if config.write_paths is not None:
        system_prompt += '\nAllowed file writes: ' + ', '.join(str(path) for path in config.write_paths) + '. Do not format an entire directory when only these files are authorized.\n'
    system_prompt += '\nPrefer structured Bash executable/args/cwd for ordinary programs. Shell commands use cmd.exe on Windows. Return evidence for claims; completion is pending parent verification. Do not rerun a successful validation when its inputs have not changed.\n'
    if os.name == "nt":
        system_prompt += (
            "\nEnvironment: native Windows. The tool named Bash executes cmd.exe, "
            "not POSIX Bash or PowerShell. Prefer Read/Glob/Grep for inspection. "
            "Use Windows command syntax, not shell heredocs or PowerShell cmdlets. "
            f"A working Python interpreter is {sys.executable}. "
            "After two equivalent command failures, change the approach instead "
            "of repeating environment or quoting probes.\n"
        )
    started = time.time()
    return _AgentState(
        config=config,
        tools=tools,
        controls=controls,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ],
        started=started,
        deadline=HardDeadline.after(config.max_run_seconds),
        execution_lease_fd=execution_lease_fd,
        workspace_before=snapshot(config.workspace) if config.delegation_capability == 'coding' else None,
    )


def _run_turn(state: _AgentState, turn: int) -> dict | None:
    _remaining_seconds(state.deadline)
    _ensure_token_budget_available(state)
    _check_cancel(state.controls.cancel)
    state.applied_steering_messages += _append_control_messages(state.messages, state.controls.poll)
    _compact_history(state)
    _enforce_history_budget(state.messages)
    request_bytes = _encoded_size(
        {"messages": state.messages, "tools": state.tools}, ensure_ascii=False,
    )
    try:
        output_allowance = _ensure_request_budget(state, request_bytes)
    except AgentBudgetExceeded:
        _compact_history(state, force=True)
        request_bytes = _encoded_size({"messages": state.messages, "tools": state.tools}, ensure_ascii=False)
        output_allowance = _ensure_request_budget(state, request_bytes)
    request_config = state.config
    if output_allowance != state.config.max_output_tokens:
        request_config = replace(state.config, max_output_tokens=output_allowance)
    state.request_in_flight = True
    _publish_telemetry(state, _build_telemetry(state))
    response = _call_with_retry(
        request_config,
        state.messages,
        state.tools,
        turn,
        cancel_signal=state.controls.cancel,
        deadline=state.deadline,
    )
    state.request_in_flight = False
    message = _record_response(state, response, request_bytes)
    _publish_telemetry(state, _build_telemetry(state))
    _check_cancel(state.controls.cancel)
    if not message.tool_calls:
        return _finalize_or_steer(state, message, turn)
    _execute_planned_tools(state, message.tool_calls, turn)
    return None


def _record_response(state: _AgentState, response, request_bytes: int = 0):
    usage = response.usage
    message = response.choices[0].message
    raw = response.model_dump(exclude_none=True)
    assistant_message = raw["choices"][0]["message"]
    if assistant_message.get("content") is None:
        assistant_message["content"] = ""
    response_bytes = _encoded_size(assistant_message, ensure_ascii=False)
    prompt = _estimate_tokens(request_bytes)
    completion = _estimate_tokens(response_bytes)
    if usage is not None:
        state.responses_with_usage += 1
        prompt = max(prompt, usage.prompt_tokens)
        completion = max(completion, usage.completion_tokens)
        state.prompt_tokens += usage.prompt_tokens
        state.completion_tokens += usage.completion_tokens
        if usage.cached_input_tokens is not None:
            state.cached_input_tokens += usage.cached_input_tokens
            state.cache_known_responses += 1
    else:
        state.responses_without_usage += 1
    state.usage_events.append({
        'sequence': len(state.usage_events) + 1, 'occurred_at': time.time(),
        'prompt': usage.prompt_tokens if usage else None,
        'completion': usage.completion_tokens if usage else None,
        'cached_input': usage.cached_input_tokens if usage else None,
        'cache_write_input': usage.cache_write_input_tokens if usage else None,
        'available': usage is not None,
    })
    # Persist the returned response even if the next budget check terminates this task.
    _publish_telemetry(state, _build_telemetry(state))
    # Meter every request, including re-sent history. Byte-derived floors are
    # estimates, not exact token counts, and remain active without provider usage.
    state.budget_tokens = getattr(state, "budget_tokens", 0) + prompt + completion
    state.last_prompt_tokens = prompt
    state.last_request_bytes = request_bytes
    if max(
        state.prompt_tokens + state.completion_tokens, state.budget_tokens
    ) > _token_limit(state):
        raise AgentBudgetExceeded(_budget_error(state, "run token budget exceeded"))
    state.messages.append(assistant_message)
    _enforce_history_budget(state.messages)
    return message


def _ensure_token_budget_available(state: _AgentState) -> None:
    metered = max(
        state.prompt_tokens + state.completion_tokens,
        getattr(state, "budget_tokens", 0),
    )
    if metered >= _token_limit(state):
        raise AgentBudgetExceeded(_budget_error(state, "run token budget exhausted"))


def _ensure_request_budget(state: _AgentState, request_bytes: int) -> int:
    used = max(
        state.prompt_tokens + state.completion_tokens,
        getattr(state, "budget_tokens", 0),
    )
    growth = max(0, request_bytes - getattr(state, "last_request_bytes", 0))
    prior_bytes = getattr(state, "last_request_bytes", 0)
    previous_prompt = getattr(state, "last_prompt_tokens", 0)
    if prior_bytes and request_bytes < prior_bytes:
        previous_prompt = (previous_prompt * request_bytes + prior_bytes - 1) // prior_bytes
    prompt = max(
        _estimate_tokens(request_bytes),
        previous_prompt + _estimate_tokens(growth),
    )
    configured_output = getattr(state.config, "max_output_tokens", MAX_OUTPUT_TOKENS_PER_REQUEST)
    available_output = _token_limit(state) - used - prompt
    if available_output < min(256, configured_output):
        raise AgentBudgetExceeded(_budget_error(state, "run token budget cannot cover another provider request", prompt))
    return min(configured_output, available_output)


def _token_limit(state: _AgentState) -> int:
    return getattr(state.config, "max_total_tokens", MAX_TOTAL_TOKENS_PER_RUN)


def _budget_error(state: _AgentState, reason: str, next_prompt: int = 0) -> str:
    used = max(state.prompt_tokens + state.completion_tokens, state.budget_tokens)
    return (f"{reason}; LOCAL_TOKEN_BUDGET used={used} limit={_token_limit(state)} "
            f"next_input_estimate={next_prompt} history_compactions={state.history_compactions}. "
            "This is a local task limit, not an account balance error. Review existing changes, "
            "then continue a smaller remaining task or explicitly raise the provider's task budget.")


def _compact_history(state: _AgentState, force: bool = False) -> None:
    if not force and _encoded_size(state.messages, ensure_ascii=False) < 128_000:
        return
    messages, count = compact_history(state.messages, keep_exchanges=2 if force else 4)
    if count and _encoded_size(messages, ensure_ascii=False) < _encoded_size(state.messages, ensure_ascii=False):
        state.messages = messages
        state.history_compactions += count
        state.history_compaction_runs += 1


def _estimate_tokens(byte_count: int) -> int:
    return (byte_count + ESTIMATED_BYTES_PER_TOKEN - 1) // ESTIMATED_BYTES_PER_TOKEN


def _encoded_size(value: object, *, ensure_ascii: bool = True) -> int:
    encoder = json.JSONEncoder(separators=(",", ":"), ensure_ascii=ensure_ascii)
    # Provider JSON can contain lone surrogates; count their escaped form.
    return sum(
        len(chunk.encode("utf-8", errors="backslashreplace"))
        for chunk in encoder.iterencode(value)
    )


def _enforce_history_budget(messages: list[dict]) -> int:
    size = _encoded_size(messages)
    if size > MAX_PROVIDER_HISTORY_BYTES:
        raise AgentBudgetExceeded("provider conversation history budget exceeded")
    return size


def _finalize_or_steer(state: _AgentState, message, turn: int) -> dict | None:
    if not message.content or not message.content.strip():
        # An empty reply is not final: keep background steering available.
        if state.empty_final_attempts or turn + 1 >= state.config.max_turns:
            raise AgentIncompleteResponse(
                "model returned an empty final response; delegation is incomplete"
            )
        state.empty_final_attempts += 1
        state.messages.append({
            "role": "user",
            "content": (
                "Your last final response was empty. Continue the original task "
                "and provide a non-empty final result, or explain what remains "
                "unfinished. Do not repeat already executed file mutations or "
                "other side-effectful tool calls without checking their effects."
            ),
        })
        _enforce_history_budget(state.messages)
        return None
    updates = _poll_control_messages(state.controls.finalize or state.controls.poll)
    if updates:
        _append_steering_update(state.messages, updates)
        state.applied_steering_messages += len(updates)
        _enforce_history_budget(state.messages)
        return None
    return _build_result(state, message.content, turn)


def _build_result(state: _AgentState, content: str | None, turn: int) -> dict:
    if not content or not content.strip():
        raise AgentIncompleteResponse("model returned an empty final response")
    final_message = content
    notices = filter(
        None,
        (state.mutations.recovery_notice(), state.mutations.warning_notice()),
    )
    final_message = "\n\n".join((*notices, final_message))
    telemetry = _build_telemetry(state, final=True)
    _publish_telemetry(state, telemetry)
    return {
        **telemetry,
        "final_message": final_message,
        "turns_used": turn + 1,
        "verification": {"status": "pending", "owner": "parent"},
    }


def _build_telemetry(state: _AgentState, final: bool = False) -> dict:
    available = state.responses_with_usage > 0
    total = state.prompt_tokens + state.completion_tokens if available else None
    result = {
        'turns_used': state.turns_used,
        'tokens': {'prompt': state.prompt_tokens if available else None,
                   'completion': state.completion_tokens if available else None, 'total': total,
                   'available': available, 'responses_with_usage': state.responses_with_usage,
                   'responses_without_usage': state.responses_without_usage,
                   'interrupted_request': final and state.request_in_flight},
        'usage_events': list(state.usage_events),
        'workspace_review': state.workspace_review,
        'budget': {'limit': _token_limit(state), 'metered_tokens': state.budget_tokens,
                   'reported_tokens': total, 'history_compactions': state.history_compactions,
                   'compaction_runs': state.history_compaction_runs,
                   'warning': state.budget_tokens >= _token_limit(state) * 0.8},
        'tool_calls': state.tool_calls,
        'bash': {'calls': state.bash_calls, 'failure_count': len(state.bash_failures),
                 'last_status': state.last_bash_status, 'failures': list(state.bash_failures),
                 'results': list(state.bash_results)},
        'active_tool': {**state.active_tool, 'status': 'interrupted' if final else 'running'} if state.active_tool else None,
        'duration_seconds': round(max(0.0, time.time() - state.started), 2),
        'mutations': state.mutations.payload(),
        'applied_steering_messages': state.applied_steering_messages,
    }
    if state.cache_known_responses and state.cache_known_responses == state.responses_with_usage:
        result['tokens']['cached_input'] = state.cached_input_tokens
    if final and state.workspace_before is not None:
        result['workspace_changes'] = changes(state.config.workspace, state.workspace_before)
        result['workspace_changes']['out_of_scope'] = scope_violations(state.config, result['workspace_changes'])
    return result


def _publish_telemetry(state: _AgentState, value: dict) -> None:
    if state.progress_reporter is not None:
        state.progress_reporter(value)


def _execute_planned_tools(state: _AgentState, tool_calls, turn: int) -> None:
    _check_cancel(state.controls.cancel)
    _validate_tool_batch(state, tool_calls)
    updates = _poll_control_messages(state.controls.poll)
    if updates:
        _steer_before_tools(state, tool_calls, updates)
        return
    for index, tool_call in enumerate(tool_calls):
        _check_cancel(state.controls.cancel)
        updates = _poll_control_messages(state.controls.poll)
        if updates:
            _steer_before_tools(state, tool_calls[index:], updates)
            return
        _execute_and_record_tool(state, tool_call, turn)


def _steer_before_tools(state: _AgentState, tool_calls, updates: list[str]) -> None:
    _append_skipped_tool_responses(state.messages, tool_calls)
    _append_steering_update(state.messages, updates)
    state.applied_steering_messages += len(updates)
    _enforce_history_budget(state.messages)


def _execute_and_record_tool(state: _AgentState, tool_call, turn: int) -> None:
    remaining = _remaining_seconds(state.deadline)
    name = tool_call.function.name
    try:
        arguments = json.loads(tool_call.function.arguments)
    except (ValueError, TypeError):
        arguments = {}
    revision = workspace_revision(execution_root(state.config))
    if name in {'Write', 'Edit', 'NotebookEdit'} and isinstance(arguments, dict) and arguments.get('path'):
        try:
            target = resolve_safe_path(scoped_arguments(name, arguments, state.config)['path'], state.config.workspace)
            key = os.path.normcase(str(target))
            if state.workspace_before is not None and key not in state.progress.file_versions:
                original = state.workspace_before[0].get(str(target.relative_to(state.config.workspace)))
                if original is not None:
                    state.progress.mutation(key, original)
        except (OSError, ValueError, SandboxViolation):
            pass
    try:
        state.progress.check(name, arguments, revision)
    except RuntimeError as error:
        raise AgentLoopError(str(error)) from None
    state.tool_calls += 1
    state.active_tool = {'name': name, 'tool_call_id': tool_call.id, 'turn': turn + 1}
    _publish_telemetry(state, _build_telemetry(state))
    result = _execute_one_tool(
        state.config,
        tool_call,
        turn,
        execution_lease_fd=state.execution_lease_fd,
        mutation_budget=state.mutation_budget,
        max_bash_timeout=max(1, int(remaining)),
        cancel_signal=state.controls.cancel,
        deadline=state.deadline,
        outcome_reporter=state.mutations.add,
    )
    state.active_tool = None
    if tool_call.function.name == "Bash":
        record_bash_result(state, result, turn, tool_call.id)
        if state.workspace_before is not None and (state.config.task_root is not None or state.config.write_paths is not None):
            observed = changes(state.config.workspace, state.workspace_before)
            if scope_violations(state.config, observed):
                raise AgentLoopError('TASK_SCOPE_VIOLATION: command changed files outside the authorized task scope; inspect observed workspace changes')
    state.progress.record(name, arguments, revision, result)
    if name in {'Write', 'Edit', 'NotebookEdit'} and isinstance(arguments, dict):
        try:
            scoped = scoped_arguments(name, arguments, state.config)
            target = scoped.get('path')
            if target:
                target = resolve_safe_path(target, state.config.workspace)
                from .file_io import read_workspace_text
                _, identity = read_workspace_text(state.config.workspace, str(target))
                state.progress.mutation(os.path.normcase(str(target)), identity.digest.hex())
        except RuntimeError as error:
            if str(error).startswith('NO_PROGRESS:'):
                raise AgentLoopError(str(error)) from None
        except (OSError, ValueError, SandboxViolation):
            pass
    state.messages.append(
        {"role": "tool", "tool_call_id": tool_call.id, "content": result}
    )
    _enforce_history_budget(state.messages)
    _publish_telemetry(state, _build_telemetry(state))
    _check_cancel(state.controls.cancel)


def _execute_one_tool(
    config: Config,
    tool_call,
    turn: int,
    *,
    execution_lease_fd: int | None = None,
    mutation_budget: MutationBudget | None = None,
    max_bash_timeout: int | None = None,
    cancel_signal: CancellationSignal | None = None,
    deadline: Deadline | None = None,
    outcome_reporter: Callable[[MutationRecord], None] | None = None,
) -> str:
    tool_name = tool_call.function.name
    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError as error:
        return f"ERROR: invalid JSON in tool arguments: {error}"
    if not isinstance(args, dict):
        return "ERROR: tool arguments must be a JSON object"
    try:
        args = scoped_arguments(tool_name, args, config)
    except SandboxViolation as error:
        return f'ERROR: {error}'
    logged_name = tool_name if tool_name in config.allowed_tools else "<unknown>"
    logger.info("Turn %d tool_call: %s arg_count=%d", turn, logged_name, len(args))
    kwargs = _tool_execution_options(
        execution_lease_fd, mutation_budget, max_bash_timeout
    )
    try:
        if deadline is not None:
            budget = mutation_budget or MutationBudget()
            return execute_in_subprocess(
                config,
                tool_name,
                args,
                budget,
                max_bash_timeout or max(1, int(_remaining_seconds(deadline))),
                execution_lease_fd,
                cancel_signal,
                deadline,
                outcome_reporter,
            )
        return execute_tool(tool_name, args, config, **kwargs)
    except ResourceBudgetExceeded as exc:
        raise AgentLoopError(str(exc)) from None


def _tool_execution_options(execution_lease_fd, mutation_budget, max_bash_timeout):
    options = {"execution_lease_fd": execution_lease_fd}
    if mutation_budget is not None:
        options["mutation_budget"] = mutation_budget
    if max_bash_timeout is not None:
        options["max_bash_timeout"] = max_bash_timeout
    return options


def _validate_tool_batch(state: _AgentState, tool_calls) -> None:
    planned = len(tool_calls)
    if planned > MAX_TOOL_CALLS_PER_TURN:
        raise AgentLoopError(
            f"tool call batch exceeds {MAX_TOOL_CALLS_PER_TURN} per turn"
        )
    if state.tool_calls + planned > MAX_TOOL_CALLS_PER_RUN:
        raise AgentLoopError(
            f"tool call budget exceeds {MAX_TOOL_CALLS_PER_RUN} per run"
        )


def _raise_max_turns(state: _AgentState) -> None:
    raise AgentLoopError(f"Agent loop exceeded max_turns ({state.config.max_turns})")


def _poll_control_messages(
    control_poll: Callable[[], list[str]] | None,
) -> list[str]:
    if control_poll is None:
        return []
    try:
        return [message.strip() for message in control_poll() if message.strip()]
    except Exception as error:
        raise AgentLoopError("control channel failed") from error


def _append_steering_update(messages: list[dict], updates: list[str]) -> int:
    if not updates:
        return 0
    body = "\n\n".join(
        f"Update {index + 1}:\n{text}" for index, text in enumerate(updates)
    )
    messages.append(
        {
            "role": "user",
            "content": (
                "# Parent agent steering update\n"
                "The parent sent newer instructions. Apply them from this point; "
                "they override conflicting earlier task details.\n\n"
                f"{body}"
            ),
        }
    )
    logger.info("Applied %d parent steering message(s)", len(updates))
    return len(updates)


def _append_control_messages(
    messages: list[dict],
    control_poll: Callable[[], list[str]] | None,
) -> int:
    return _append_steering_update(messages, _poll_control_messages(control_poll))


def _append_skipped_tool_responses(messages: list[dict], tool_calls) -> None:
    for tool_call in tool_calls:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": (
                    "SKIPPED: the parent sent a newer steering instruction before "
                    "this tool ran. Re-plan using the latest instruction."
                ),
            }
        )
    logger.info("Skipped %d stale tool call(s) due to parent steering", len(tool_calls))
