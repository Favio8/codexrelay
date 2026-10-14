"""Inspect real 0.3.1 artifacts and recovery; never call a provider or daily controls."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from codexrelay.backends import recovery, review
from deepseek_mcp.config import Config
from deepseek_mcp.transaction_recovery import acknowledge_with_lease


def main():
    evidence = ROOT / 'artifacts/evidence/0.3.1'
    result = json.loads((evidence / 'real-low-budget-validation.json').read_text())
    records = []
    original = json.dumps({'items': ['x' * 30] * 900, 'enabled': False})
    expected = original.replace('"enabled": false', '"enabled": true')
    for run in result['runs']:
        workspace = Path(run['workspace']).resolve()
        assert workspace.is_relative_to((ROOT / '.tmp').resolve())
        actual = (workspace / 'data.json').read_text()
        assert actual == expected, 'unexpected changes outside the flag'
        telemetry = json.loads((Path(result['fixture_root']) / (run['backend'] + '-single_line.telemetry.json')).read_text())
        assert run['execution'] == 'completed' and run['parent_verified'] and telemetry['final_message'].strip()
        assert run['budget_metering']['limit'] == 32768 and run['budget_metering']['metered_tokens'] <= 32768
        assert not telemetry['tokens']['interrupted_request']
        assert not telemetry.get('bash_failures')
        tools = telemetry['progress']['tool_evidence']
        edits = [e for e in tools if e['name'] in {'Edit', 'Write', 'NotebookEdit'}]
        assert len(edits) == 1 and edits[0]['name'] == 'Edit'
        edit = edits[0]['arguments']
        assert original.count(edit['old_string']) == 1
        assert original.replace(edit['old_string'], edit['new_string'], 1) == expected
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.iterdir() if p.is_file()}
        assert set(hashes) == {'data.json'}, 'unexpected output files'
        status = recovery(workspace)
        transactions = []
        for item in status['pending']:
            assert (workspace / item['path']).resolve() == workspace / 'data.json'
            assert item['status'] == 'committed'
            assert item['sha256'] == hashes['data.json']
            transactions.append({'transaction_id': item['transaction_id'], 'path': item['path'], 'sha256': item['sha256']})
        config = Config(api_key='', workspace=workspace, allowed_tools=[])
        if transactions:
            acknowledge_with_lease(config, [item['transaction_id'] for item in transactions])
        checks = []
        for item in status['checks']:
            current = next(c for c in review.query(config) if c['review_id'] == item['review_id'])
            review.acknowledge(config, current['review_id'], current['state_sha256'],
                               'Parent checked exact flag-only bytes, JSON items, tool outputs, final report and current file hashes')
            checks.append({'review_id': current['review_id'], 'state_sha256': current['state_sha256']})
        assert not recovery(workspace)['pending'] and not recovery(workspace)['checks']
        records.append({'backend': run['backend'], 'execution_completed': True, 'parent_accepted': True,
                        'workspace': str(workspace), 'files': hashes, 'reviewed_transactions': transactions,
                        'reviewed_workspace_checks': checks,
                        'final_report': telemetry['final_message'],
                        'tool_sequence': [e['name'] for e in tools]})
    assert result['combined_metering'] <= 655360
    report = {'ok': True, 'kind': 'independent_actual_artifact_and_recovery_audit',
              'combined_metering': result['combined_metering'], 'records': records}
    (evidence / 'parent-low-budget-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
