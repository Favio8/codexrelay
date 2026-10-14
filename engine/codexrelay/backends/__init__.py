"""Small adapter registry; both schedulers share the same controlled tool harness."""
from pathlib import Path
from deepseek_mcp.config import Config, DEFAULT_ALLOWED_TOOLS
from deepseek_mcp.job_manager import DeepSeekJobManager, validate_delegation_input
from deepseek_mcp.host_instructions import HOST_INSTRUCTIONS as LEGACY_HOST_INSTRUCTIONS
from .. import __version__
from deepseek_mcp.transaction_recovery import query_with_lease, acknowledge_with_lease
from . import pi, review

HOST_INSTRUCTIONS = LEGACY_HOST_INSTRUCTIONS + '\nCodexRelay routing: prefer start_agent/delegate_to_agent and readonly variants for the selected backend. Legacy DeepSeek-named start/delegate tools always use the original engine. Use get_task_recovery and acknowledge_workspace_review for separate observed-workspace checks; a backend switch never clears pending checks.'


def capabilities(basis):
    """Verified by the regression suite unless the entry says otherwise; never optimistic."""
    return dict(strict_readonly=True, scope_enforcement='files_before_commands_after',
                budget_enforcement=True, live_usage=True, steering=True,
                cancel_and_join=True, mutation_journal=True, workspace_diff=True,
                credential_isolation=True, basis=basis)


def catalog():
    return [dict(id='deepseek', name='现有执行引擎', version='7e540263+relay-'+__version__, installed=True,
                 capabilities=capabilities('verified_by_test')),
            dict(id='pi', name='Pi Agent SDK', version='1.2.0', installed=pi.available(),
                 capabilities=capabilities('verified_by_test'))]


def create_manager(backend):
    if backend == 'deepseek':
        return DeepSeekJobManager(ready_check=review.require_clear)
    if backend == 'pi':
        if not pi.available(verify=True):
            raise ValueError('Pi 运行时不可用，请安装包含 Pi 的 CodexRelay 版本')
        return DeepSeekJobManager(runner=pi.run_pi, ready_check=review.require_clear)
    raise ValueError('未知执行后端')


def recovery_config(workspace):
    return Config(api_key='', workspace=Path(workspace), allowed_tools=[])


def recovery(workspace):
    config = recovery_config(workspace)
    return {'pending': query_with_lease(config), 'checks': review.query(config),
            'journal_supported': True, 'workspace_checks_supported': True}


def ensure_removable(workspace):
    status = recovery(workspace)
    if status['pending'] or status['checks']:
        raise ValueError('仍有待核验修改，请先在 Codex 中完成恢复检查')


def build_config(provider, mode, slot, workspace, credential):
    key = credential
    from urllib.parse import urlsplit
    if not key and urlsplit(provider["base_url"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("供应商未配置 API Key")
    return Config(api_key=key or "local", workspace=workspace,
                  model=provider[slot + "_model"], base_url=provider["base_url"],
                  allowed_tools=[tool for tool in provider.get("allowed_tools", DEFAULT_ALLOWED_TOOLS) if mode != "readonly" or tool in {"Read", "Glob", "Grep"}],
                  delegation_capability=mode, flash_model=provider["flash_model"], pro_model=provider["pro_model"],
                  reasoning_effort=provider.get("pro_reasoning_effort", provider["reasoning_effort"]) if slot == "pro" else provider["reasoning_effort"],
                  flash_reasoning_effort=provider["reasoning_effort"], pro_reasoning_effort=provider.get("pro_reasoning_effort", provider["reasoning_effort"]),
                  max_total_tokens=provider.get("max_total_tokens", 1_000_000), max_output_tokens=provider.get("max_output_tokens", 16384),
                  context_window=provider.get(slot + "_context_window"), max_turns=provider.get("max_turns", 50), max_run_seconds=provider.get("max_run_seconds", 18000))

HOST_INSTRUCTIONS += '\nUse create_task_group, get_task_checkpoint and record_task_verification for each ended run. Resume only verified remaining_items with resume_agent in the same group and current checkpoint ID/hash. Default is 3 continuations, 4 runs. Do not reset groups to bypass limits or routinely override provider budgets with lower token_budget. Only App grants budget/round extensions. Summary is an index, not proof; inspect actual files, commands and exact recovery records.'
