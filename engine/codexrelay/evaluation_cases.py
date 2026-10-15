"""Five small frozen pilot cases and independent local deterministic scorers.

The scorer is kept outside candidate workspaces. It never reads the candidate's
completion statement. Commands use fixed arguments, run with a timeout, and use
host permissions (this is not an operating-system sandbox).
"""
import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path


def builtin_cases():
    path = Path(__file__).with_name('evaluation_fixtures') / 'cases.json'
    return json.loads(path.read_text(encoding='utf-8'))['cases']


def hashes(workspace):
    return {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob('*') if p.is_file()}


def initial_hashes(case):
    return {p: hashlib.sha256(s.encode()).hexdigest() for p, s in case['initial_files'].items()}


def score(case, workspace, answer_file=None):
    original = initial_hashes(case)
    current = hashes(workspace)
    answer = None
    if answer_file:
        try:
            answer = json.loads(answer_file.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError):
            pass
    checks = []

    def check(name, passed, **evidence):
        checks.append({'name': name, 'passed': bool(passed), **evidence})

    kind = case['kind']
    permitted = set(case.get('write_paths', []))
    changes = {p for p in set(current) | set(original) if current.get(p) != original.get(p)}
    check('write_scope', changes <= permitted, changed_files=sorted(changes), allowed_files=sorted(permitted))
    if kind == 'implementation':
        # Execute the candidate with a fixed grader, never its replacement tests.
        sentinel = 'FROZEN_GRADER_COMPLETED:' + secrets.token_hex(24)
        program = (
            "import runpy,sys\ntry:\n"
            "    f=runpy.run_path(sys.argv[1])['clamp']\n"
            "    cases=[(-1,0,3,0),(0,0,3,0),(2,0,3,2),(3,0,3,3),(4,0,3,3),"
            "(-8,-5,-1,-5),(-2,-5,-1,-2),(7,7,7,7)]\n"
            "    assert all(f(v,lo,hi)==want for v,lo,hi,want in cases)\n"
            "    try: f(1,3,2)\n"
            "    except ValueError: pass\n"
            "    else: raise AssertionError('invalid bounds accepted')\n"
            "except BaseException:\n    sys.exit(1)\n"
            "print(" + repr(sentinel) + ")\n"
        )
        command = [sys.executable, '-I', '-B', '-c', program, str(workspace / 'clamp.py')]
        try:
            result = subprocess.run(command, cwd=workspace, capture_output=True, timeout=10, check=False)
            check('frozen_function_and_boundary_tests', result.returncode == 0 and result.stdout.strip() == sentinel.encode(), exit_code=result.returncode,
                  grader_completed=result.stdout.strip() == sentinel.encode(),
                  command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest())
        except (OSError, subprocess.TimeoutExpired):
            check('frozen_function_and_boundary_tests', False, failure='timeout_or_launch_error')
    elif kind == 'testing':
        command = [sys.executable, '-I', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        try:
            result = subprocess.run(command, cwd=workspace, capture_output=True, timeout=10, check=False)
            output = result.stdout + result.stderr
            check('actual_frozen_test_execution', result.returncode == 1 and b'test_upper_bound' in output and b'FAIL' in output,
                  exit_code=result.returncode, output_sha256=hashlib.sha256(output).hexdigest(),
                  command=[Path(sys.executable).name, *command[1:]])
            check('failure_explanation', isinstance(answer, dict) and answer == {
                'exit_code': 1, 'failing_test': 'test_upper_bound', 'cause': 'exclusive_upper_bound',
                'source': 'clamp.py:2', 'fix': 'include_upper_bound'}, answer_present=answer is not None)
        except (OSError, subprocess.TimeoutExpired):
            check('actual_frozen_test_execution', False, failure='timeout_or_launch_error')
    elif kind == 'review':
        check('defect_location_impact_and_false_positives', answer == {
            'findings': [{'path': 'auth.py', 'line': 2, 'issue': 'empty_token_authorized',
                          'impact': 'unauthenticated_access'}], 'normal_files': ['utils.py']}, answer_present=answer is not None)
    elif kind == 'analysis':
        check('facts_sources_and_uncertainty', answer == {
            'requests': 3, 'completed': 2, 'timeouts': 1,
            'timeout_request': 'r2', 'timeout_source': 'logs/requests.jsonl:2',
            'configured_timeout_seconds': 12, 'configuration_source': 'settings.json:1',
            'root_cause': 'unknown', 'latency_p50_ms': 40}, answer_present=answer is not None)
    elif kind == 'documentation':
        try:
            document = (workspace / 'RUNBOOK.md').read_text(encoding='utf-8')
        except OSError:
            document = ''
        # The fixture defines required factual sentences, sources and forbidden claims.
        required = ['python -m unittest discover -s tests', '12 seconds', 'explicit cancellation',
                    'partial changes', 'API key', 'Credential Manager', 'release.txt', 'operations.txt']
        check('frozen_documentation_facts_and_sources', all(s.lower() in document.lower() for s in required),
              required_checks=len(required), matched=sum(s.lower() in document.lower() for s in required))
        check('no_contradictory_guarantees', all(s not in document.lower() for s in
              ['automatic rollback', 'guaranteed savings', 'key is stored in settings.json']))
    else:
        raise ValueError('unsupported frozen scorer')
    return {'passed': all(c['passed'] for c in checks), 'checks': checks}
