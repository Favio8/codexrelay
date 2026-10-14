"""Independent local artifact/recovery audit after the authorized paid fixtures."""
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
    source = ROOT / 'artifacts/evidence/0.3.0'
    reports = []
    for name in ('real-model-quality.json', 'real-model-focused.json', 'real-summary-thinking-truncation.json', 'real-summary-ordinary-validation.json'):
        data = json.loads((source / name).read_text())
        for run in data['runs']:
            workspace = Path(run['workspace']).resolve()
            if not workspace.is_relative_to((ROOT / '.tmp').resolve()): raise ValueError('fixture path outside approved temporary directory')
            files = {}
            for path in workspace.iterdir():
                if path.is_file(): files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            status = recovery(workspace)
            checked = []
            for item in status['pending']:
                path = (workspace / item['path']).resolve()
                if not path.is_relative_to(workspace): raise ValueError('unexpected journal path')
                actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                # Historical hashes may differ after a later reviewed edit. Preserve both.
                checked.append(dict(transaction_id=item['transaction_id'], path=item['path'], intended_sha256=item.get('sha256'), current_sha256=actual,
                                    observed_state=item.get('observed_state'), status=item.get('status')))
            config = Config(api_key='', workspace=workspace, allowed_tools=[])
            if checked: acknowledge_with_lease(config, [item['transaction_id'] for item in checked])
            observed = []
            for item in status['checks']:
                current = review.query(config)[0]
                review.acknowledge(config, current['review_id'], current['state_sha256'],
                                  'Parent independently inspected fixture artifacts and actual current hashes; this clears recovery only, not a model-quality verdict')
                observed.append(dict(review_id=current['review_id'], state_sha256=current['state_sha256']))
            if recovery(workspace)['pending'] or recovery(workspace)['checks']: raise ValueError('reviewed fixture recovery remains pending')
            reports.append(dict(workspace=str(workspace), version=run['version'], backend=run['backend'], task=run['task'],
                                files=files, reviewed_transactions=checked, reviewed_workspace_checks=observed,
                                quality_rubric_passed=run['parent_verified'], execution_status=run['execution']))
    result = dict(kind='parent_actual_artifact_and_recovery_audit', records=reports,
                  note='Recovery acknowledgement certifies inspection of current artifacts, not fulfillment of every natural-language objective. Model quality follows the declared experiment rubric.')
    (source / 'paid-fixture-recovery-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(dict(fixtures=len(reports), transactions=sum(len(r['reviewed_transactions']) for r in reports), checks=sum(len(r['reviewed_workspace_checks']) for r in reports))))


if __name__ == '__main__': main()
