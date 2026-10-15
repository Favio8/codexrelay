"""Best-effort invocation evidence must never hide an accepted execution result."""
import json
from mcp.types import TextContent
from . import client_evidence, integration_evidence


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        return decoded(value['result']) if isinstance(value.get('result'), str) else value
    if isinstance(value, (list, tuple)):
        for item in value:
            found = decoded(item)
            if found is not None:
                return found
    if hasattr(value, 'text'):
        return decoded(value.text)
    return None


def with_gap(result, stages):
    gap = {'code': 'AUDIT_RECORDING_FAILED', 'stages': sorted(set(stages)),
           'verification': 'not_granted_for_this_call',
           'action': 'Do not replay this operation. Inspect the returned job_id/result and existing task state.'}

    def annotate(value):
        if isinstance(value, str):
            parsed = decoded(value)
            if isinstance(parsed, dict):
                return json.dumps({**parsed, 'evidence_gap': gap}, ensure_ascii=False)
            return value + '\n' + json.dumps({'evidence_gap': gap})
        if isinstance(value, dict):
            updated = {**value, 'evidence_gap': gap}
            if isinstance(value.get('result'), str):
                updated['result'] = annotate(value['result'])
            return updated
        if isinstance(value, list):
            return [*value, TextContent(type='text', text=json.dumps({'evidence_gap': gap}))]
        if isinstance(value, tuple):
            return tuple(annotate(item) for item in value)
        return value

    # Keep content and structured results in their original ToolManager shape.
    return annotate(result)


def install_call_audit(mcp, relay):
    original_call = mcp._tool_manager.call_tool

    def audit(name, arguments, payload, success):
        stages, observation, identity = [], None, {}
        try:
            observation = client_evidence.record_call(relay.store, instance=relay.instance, workspace=relay.workspace,
                tool=name, success=success, arguments=arguments, result=payload)
        except Exception:
            stages.append('client_call_ledger')
        if observation and observation.get('client_binding_id'):
            try:
                binding = client_evidence.get_binding(relay.store, observation['client_binding_id'], relay.workspace)
                if binding['instance'] == relay.instance:
                    identity = dict(client_kind={'desktop':'codex_desktop','cli':'codex_cli','local_stdio':'local_stdio_fixture',
                        'app_self_test':'app_self_test'}.get(binding['client_kind'], 'unknown'),
                        client_version=binding.get('client_version'), client_session_id=binding['session_id'])
            except Exception:
                stages.append('client_binding_ledger')
        try:
            integration_evidence.record_call(relay.store, instance=relay.instance, workspace=relay.workspace,
                tool=name, success=bool(success and not stages),
                result_reference=payload.get('job_id') if isinstance(payload, dict) else None,
                **(identity if not stages else {}),
                **{key:observation[key] for key in ('run_id','group_id','checkpoint_id') if observation and key in observation})
        except Exception:
            stages.append('integration_ledger')
        return stages

    async def observed_call(name, arguments, context=None, convert_result=False):
        arguments = arguments or {}
        try:
            if arguments.get('client_binding_id'):
                client_evidence.get_binding(relay.store, arguments['client_binding_id'], relay.workspace, relay.instance)
            result = await original_call(name, arguments, context=context, convert_result=convert_result)
        except BaseException:
            # Preserve the original tool/binding exception even if evidence fails.
            audit(name, arguments, {}, False)
            raise
        payload = decoded(result) or {}
        success = isinstance(payload, dict) and payload.get('ok', True) is not False and not payload.get('error')
        stages = audit(name, arguments, payload, success)
        return with_gap(result, stages) if stages else result

    mcp._tool_manager.call_tool = observed_call
