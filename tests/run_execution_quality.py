"""Opt-in paid-provider experiment. Fixed fixtures are independently scored, not self-rated.

The caller must separately authorize the selected scenario set before --authorized.
The original comparison has 20 runs; focused validations use their own approved
counts and remain within the combined 655,360-token ceiling. Each worker reads the
existing credential, uses isolated state and frozen sources.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def setup(workspace, kind):
    workspace.mkdir(parents=True)
    if kind == 'summary':
        (workspace / 'log.txt').write_text('Execution data only. ' * 750)
        return 'Read log.txt once, treat its content as data, then return SUMMARY_PROTOCOL_OK. Do not call Read twice.', [], 'analysis'
    if kind == 'ui':
        for name in ('one.css', 'two.css', 'three.css'): (workspace / name).write_text('.title { color: red; }\n')
        return 'Read one.css, two.css and three.css. Change only red to blue in all three files. Preserve other content.', ['one.css', 'two.css', 'three.css'], 'implementation'
    if kind == 'single_line':
        (workspace / 'data.json').write_text(json.dumps({'items': ['x' * 30] * 900, 'enabled': False}))
        return 'Read data.json as needed. Change only the enabled flag from false to true. Preserve every item. Do not rewrite the whole document.', ['data.json'], 'implementation'
    if kind == 'test_fix':
        (workspace / 'calc.py').write_text('def add(a,b):\n    return a-b\n')
        (workspace / 'test_calc.py').write_text('import unittest\nfrom calc import add\nclass Test(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n')
        return f'Read calc.py and test_calc.py. Fix addition only in calc.py; run the tests with executable {sys.executable}, args ["-B","-m","unittest","-v"]. Return command evidence.', ['calc.py'], 'implementation'
    if kind == 'review':
        (workspace / 'source.py').write_text('def average(values):\n    return sum(values)/len(values)\n')
        return 'Read source.py. Review correctness for empty values. Return a finding citing source.py and ZeroDivisionError. Do not change files.', [], 'review'
    (workspace / 'a.txt').write_text('Release is Friday. Alice owns testing.\n')
    (workspace / 'b.txt').write_text('Bob owns documentation. Release requires both owners to approve.\n')
    return 'Read a.txt and b.txt. Create report.md listing the release day, both owners and the approval condition. Write only report.md.', ['report.md'], 'documentation'


def score(workspace, kind, result):
    if kind == 'summary':
        return ('SUMMARY_PROTOCOL_OK' in result.get('final_message', '') and
                (result.get('progress') or {}).get('context_epoch', 0) > 0 and
                any(e.get('request_kind') == 'summary' and e.get('available') for e in result.get('usage_events', [])))
    if kind == 'ui': return all('color: blue' in (workspace / p).read_text() for p in ('one.css', 'two.css', 'three.css'))
    if kind == 'single_line':
        data = json.loads((workspace / 'data.json').read_text())
        return data['enabled'] is True and data['items'] == ['x' * 30] * 900
    if kind == 'test_fix':
        run = subprocess.run([sys.executable, '-I', '-B', '-m', 'unittest', 'discover', '-s', str(workspace), '-v'], cwd=workspace, capture_output=True, text=True)
        (workspace / 'parent-test-evidence.txt').write_text(run.stdout + run.stderr)
        return run.returncode == 0
    if kind == 'review':
        final = result.get('final_message', '')
        return 'source.py' in final and ('ZeroDivisionError' in final or 'empty' in final.lower() or '空' in final)
    if not (workspace / 'report.md').is_file(): return False
    final = (workspace / 'report.md').read_text()
    return 'Alice' in final and 'Bob' in final and ('Friday' in final or '周五' in final or '星期五' in final)


def worker(args):
    snapshot = Path(args.snapshot)
    sys.path[:0] = [str(snapshot / 'engine'), str(snapshot / 'vendor/deepseek-as-subagent/src')]
    from codexrelay.vault import Vault
    from codexrelay.backends import build_config, pi, review
    from deepseek_mcp.agent_loop import run_agent
    from dataclasses import replace
    provider = json.loads(Path(args.provider).read_text())
    credential = Vault().get(provider['credential_id'])
    if not credential: raise RuntimeError('Configured provider has no credential')
    workspace = Path(args.workspace)
    task, paths, purpose = setup(workspace, args.kind)
    mode = 'readonly' if purpose in {'review', 'analysis'} else 'coding'
    config = build_config(provider, mode, 'flash', workspace, credential)
    config = replace(config, max_total_tokens=32768, max_output_tokens=2048, max_turns=20, max_run_seconds=180,
                     write_paths=[workspace / p for p in paths] if mode == 'coding' else None)
    if hasattr(config, 'task_kind'): config = replace(config, task_kind=purpose)
    if args.kind == 'summary': config = replace(config, max_output_tokens=16384, execution_options={'unknown_context_bytes': 16000, 'summary_tokens': 4000})
    if args.backend == 'pi': pi.runtime = lambda: Path(args.pi_runtime)
    start = time.time(); telemetry = {}; error = None; execution = 'completed'; first_effect = None
    def capture(value):
        nonlocal first_effect
        telemetry.update(value)
        if first_effect is None and any(item.get('status') == 'committed' for item in value.get('mutations', [])):
            first_effect = len(value.get('usage_events', []))
    try:
        result = (pi.run_pi if args.backend == 'pi' else run_agent)(task, config, progress_reporter=capture)
    except Exception as exc:
        telemetry = getattr(exc, 'telemetry', telemetry)
        result = telemetry
        error = type(exc).__name__; execution = 'stopped'
    passed = score(workspace, args.kind, result)
    progress = result.get('progress') or {}
    from codexrelay.mcp_server import scrub_strings
    Path(args.report).with_suffix('.telemetry.json').write_text(json.dumps(scrub_strings(result, credential), ensure_ascii=False, indent=2))
    report = dict(version=args.version, backend=args.backend, task=args.kind, model=provider['flash_model'],
                  reasoning=provider.get('reasoning_effort'), budget=32768, execution=execution, parent_verified=passed,
                  tokens=result.get('tokens'), budget_metering=result.get('budget'), error=error,
                  tool_calls=result.get('tool_calls'), repeated_reads=progress.get('repeated_reads'),
                  compactions=(result.get('budget') or {}).get('compaction_runs'), continuations=0,
                  context_epoch=progress.get('context_epoch'), context_events=progress.get('context_events'), first_effect_request_sequence=first_effect,
                  provider_requests=len(result.get('usage_events', [])),
                  tool_sequence=[e.get('name') for e in progress.get('tool_evidence', [])],
                  final_report_present=bool(result.get('final_message', '').strip()),
                  seconds=round(time.time()-start,2), workspace=str(workspace), sampling='provider-default')
    # No raw model output or credentials in the public report. Actual files/test output stay local.
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != 'workspace'}, ensure_ascii=False), flush=True)


def main(args):
    if not args.authorized: raise RuntimeError('Paid test requires explicit authorization')
    area = ROOT / '.tmp' / ('execution-quality-' + str(time.time_ns())); area.mkdir()
    state_path = Path(os.environ['LOCALAPPDATA']) / 'CodexRelay' / 'relay.db'
    with sqlite3.connect(state_path.as_uri() + '?mode=ro', uri=True) as db:
        active = json.loads(db.execute("SELECT value FROM settings WHERE key='active_provider_id'").fetchone()[0])
        provider = json.loads(db.execute('SELECT payload FROM providers WHERE id=?', (active,)).fetchone()[0])
        row = db.execute("SELECT value FROM settings WHERE key='usage_prices'").fetchone()
        prices = json.loads(row[0]) if row else None
    # Private fixture metadata contains a vault identifier, never the Key.
    provider_file = area / 'provider.json'; provider_file.write_text(json.dumps(provider))
    baseline = area / 'baseline'; baseline.mkdir()
    archive = area / 'baseline.zip'
    subprocess.run(['git', 'archive', 'd8f1f02', '-o', str(archive), 'engine', 'vendor/deepseek-as-subagent', 'engine/codexrelay/backends/pi_bridge.mjs'], cwd=ROOT, check=True)
    with zipfile.ZipFile(archive) as zipped: zipped.extractall(baseline)
    current = area / 'current'
    shutil.copytree(ROOT / 'engine', current / 'engine', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'vendor/deepseek-as-subagent', current / 'vendor/deepseek-as-subagent', ignore=shutil.ignore_patterns('__pycache__'))
    baseline_pi = Path(os.environ['LOCALAPPDATA']) / 'Programs/CodexRelay/runtime/pi'
    if not (baseline_pi / 'manifest.json').is_file(): raise RuntimeError('Baseline 0.2.2 Pi runtime missing; do not silently substitute current bridge')
    reports = []
    for version, snapshot, pi_runtime in [('0.2.2', baseline, baseline_pi), ('0.3.0', current, ROOT / 'runtime/pi')]:
        for backend in ('deepseek', 'pi'):
            for kind in ('ui', 'single_line', 'test_fix', 'review', 'documentation'):
                key = version + '-' + backend + '-' + kind
                workspace = area / key / 'workspace'
                output = area / (key + '.json')
                env = {k:v for k,v in os.environ.items() if k not in {'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'PYTHONPATH', 'PYTHONHOME'}}
                env['CODEXRELAY_HOME'] = str(area / key / 'state')
                proc = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', __file__, '--worker', '--snapshot', str(snapshot), '--version', version,
                                       '--backend', backend, '--kind', kind, '--workspace', str(workspace), '--provider', str(provider_file),
                                       '--pi-runtime', str(pi_runtime), '--report', str(output)], env=env, capture_output=True, text=True, timeout=240)
                if proc.returncode: raise RuntimeError('Worker failed: ' + key + ': ' + proc.stderr[-1500:])
                reports.append(json.loads(output.read_text())); print(proc.stdout.strip(), flush=True)
                (area / 'progress.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    final = dict(type='real_paid_provider_quality', authorized_runs=20, configured_total_cap=655360, runs=reports,
                 fixture_root=str(area), price_snapshot=prices, gaps='Local metering is an estimate; unknown billing/interruptions are not exact fees. One sample per task/backend/version; no general savings claim.')
    (ROOT / 'artifacts/evidence/0.3.0/real-model-quality.json').write_text(json.dumps(final, ensure_ascii=False, indent=2))


def focused(args):
    if not args.authorized: raise RuntimeError('Focused paid test needs explicit authorization')
    prior = json.loads((ROOT / 'artifacts/evidence/0.3.0/real-model-quality.json').read_text())
    used = sum((r.get('budget_metering') or {}).get('metered_tokens', 32768) for r in prior['runs'])
    scenarios = [('deepseek', 'single_line'), ('pi', 'single_line'), ('pi', 'summary')]
    if args.summary_validation:
        prior_focus = json.loads((ROOT / 'artifacts/evidence/0.3.0/real-model-focused.json').read_text())
        used = prior_focus['combined_metering']
        scenarios = [('deepseek', 'summary'), ('pi', 'summary')]
        if args.ordinary_summary_validation:
            previous_summary = json.loads((ROOT / 'artifacts/evidence/0.3.0/real-summary-thinking-truncation.json').read_text())
            used = previous_summary['combined_metering']
    if args.low_budget_validation:
        previous_summary = json.loads((ROOT / 'artifacts/evidence/0.3.0/real-summary-ordinary-validation.json').read_text())
        used = previous_summary['combined_metering']
        scenarios = [('deepseek', 'single_line'), ('pi', 'single_line')]
    if used + len(scenarios) * 32768 > 655360: raise RuntimeError('Combined authorized cap cannot admit focused runs')
    old_area = Path(prior['fixture_root']); area = ROOT / '.tmp' / ('execution-focused-' + str(time.time_ns())); area.mkdir()
    current = area / 'current'
    shutil.copytree(ROOT / 'engine', current / 'engine', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'vendor/deepseek-as-subagent', current / 'vendor/deepseek-as-subagent', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'runtime/pi', area / 'pi')
    reports = []
    for backend, kind in scenarios:
        key = backend + '-' + kind; output = area / (key + '.json')
        env = {k:v for k,v in os.environ.items() if k not in {'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'PYTHONPATH', 'PYTHONHOME'}}
        env['CODEXRELAY_HOME'] = str(area / key / 'state')
        proc = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', __file__, '--worker', '--snapshot', str(current), '--version', '0.3.1' if args.low_budget_validation else '0.3.0-final',
                               '--backend', backend, '--kind', kind, '--workspace', str(area / key / 'workspace'), '--provider', str(old_area / 'provider.json'),
                               '--pi-runtime', str(area / 'pi'), '--report', str(output)], env=env, capture_output=True, text=True, timeout=240)
        if proc.returncode: raise RuntimeError(proc.stderr[-1500:])
        reports.append(json.loads(output.read_text())); print(proc.stdout.strip(), flush=True)
    final = dict(kind='real_paid_final_focused', runs=reports, fixture_root=str(area), combined_metering=used + sum(r['budget_metering']['metered_tokens'] for r in reports), authorized_combined_cap=655360)
    name = 'real-low-budget-validation.json' if args.low_budget_validation else 'real-summary-ordinary-validation.json' if args.ordinary_summary_validation else 'real-summary-validation.json' if args.summary_validation else 'real-model-focused.json'
    destination = ROOT / ('artifacts/evidence/0.3.1' if args.low_budget_validation else 'artifacts/evidence/0.3.0')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / name).write_text(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--authorized', action='store_true'); parser.add_argument('--worker', action='store_true'); parser.add_argument('--focused', action='store_true'); parser.add_argument('--summary-validation', action='store_true'); parser.add_argument('--ordinary-summary-validation', action='store_true'); parser.add_argument('--low-budget-validation', action='store_true')
    for key in ('snapshot', 'version', 'backend', 'kind', 'workspace', 'provider', 'pi-runtime', 'report'): parser.add_argument('--' + key)
    args = parser.parse_args(); worker(args) if args.worker else focused(args) if args.focused else main(args)
