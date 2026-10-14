"""Assemble the local 0.3.1 fix with current and explicitly inherited evidence."""
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.3.1'
EVIDENCE = ROOT / 'artifacts/evidence' / VERSION
DEST = ROOT / 'artifacts/releases' / VERSION


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def package():
    assert '\nOK\n' in (EVIDENCE / 'full-unit.log').read_text(encoding='utf-8')
    for name in ('execution-mcp.json', 'installer-0.2.2.json', 'parent-low-budget-audit.json'):
        assert json.loads((EVIDENCE / name).read_text())['ok'], name
    native = ROOT / 'artifacts/evidence/0.3.1-native.json'
    assert json.loads(native.read_text())['ok'] and not json.loads(native.read_text())['uncovered']
    paid = json.loads((EVIDENCE / 'real-low-budget-validation.json').read_text())
    assert all(r['execution'] == 'completed' and r['parent_verified'] and r['final_report_present'] for r in paid['runs'])
    assert paid['combined_metering'] <= 655360
    summary = json.loads((ROOT / 'artifacts/evidence/0.3.0/real-summary-ordinary-validation.json').read_text())
    assert all(r['parent_verified'] and r['context_epoch'] > 0 for r in summary['runs'])
    # Preserve the exact original requirement and all previous release artifacts.
    prd_hash = digest(ROOT / 'docs/PRD-EXECUTION-OPTIMIZATION.md')
    assert prd_hash.upper() == '8DC1F22AD02BE40B0B9FE1B214040C26D32178AEA9970AA9CFCFC5B137591001'
    previous = ROOT / 'artifacts/releases/0.3.0'
    for line in (previous / 'SHA256SUMS.txt').read_text().splitlines():
        expected, name = line.split('  ', 1)
        assert digest(previous / name) == expected, name
    (EVIDENCE / 'preservation.json').write_text(json.dumps({'original_prd_sha256': prd_hash, 'previous_release_checksums_valid': True}, indent=2))
    for run in paid['runs']:
        name = run['backend'] + '-single_line.telemetry.json'
        shutil.copyfile(Path(paid['fixture_root']) / name, EVIDENCE / name)
    shutil.copyfile(Path(paid['runs'][0]['workspace']) / 'data.json', EVIDENCE / 'actual-final-data.json')
    matrix = json.loads((ROOT / 'artifacts/evidence/0.3.0/acceptance-matrix.json').read_text())
    replacements = {'full-unit-final.log': 'full-unit.log', 'execution-mcp-final.log': 'execution-mcp.log',
                    'mcp-legacy-final.log': 'legacy-mcp.log', 'installer-0.2.2-final.log': 'installer.log'}
    for row in matrix:
        updated = []
        for item in row['evidence']:
            name = Path(item).name
            if name in replacements:
                item = f'../artifacts/evidence/{VERSION}/{replacements[name]}'
            elif name == '0.3.0-native.json':
                item = '../artifacts/evidence/0.3.1-native.json'
            updated.append(item)
        row['evidence'] = updated
        row['public_evidence_archive'] = 'https://github.com/Favio8/codexrelay/releases/download/v0.3.1/CodexRelay_0.3.1_evidence.zip'
        row['evidence_policy'] = 'Current checks where listed; unchanged mechanisms also retain explicitly linked 0.3.0 evidence.'
        if row['id'] in {'AC07', 'AC54'}:
            row['evidence'] += [f'../artifacts/evidence/{VERSION}/real-low-budget-validation.json',
                                f'../artifacts/evidence/{VERSION}/parent-low-budget-audit.json']
        if row['id'] == 'AC07':
            row.update(configuration='2000-character long-line preview; explicit ranges; 32768 per run',
                       verification_method='Exact pagination and target-search tests; both real backends completed, exact flag-only bytes and all 900 items checked by parent',
                       uncovered='One real sample per backend; arbitrary JSON/CSS structures not exhausted')
        elif row['id'] == 'AC54':
            row.update(configuration='29 paid runs / combined metering 418636 <= 655360',
                       verification_method='Original comparison and failed samples retained; real summary epochs succeeded; 0.3.1 low-budget tasks 2/2 execution complete and parent accepted',
                       uncovered='Small sample; no general fixed savings/completion-rate claim; old baseline lacks some efficiency metrics')
    assert len(matrix) == 54 and len({r['id'] for r in matrix}) == 54
    (EVIDENCE / 'acceptance-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2))
    intro = '''# 0.3.1 逐项验收与发布

基线为已发布的 0.2.2 Windows Beta；0.3.0 的 M1—M4 实现保留，本次修复低预算下的大单行文件执行。原始 PRD 未修改。完整修复说明见 [0.3.1 交付报告](RELEASE-0.3.1.md)。

170 项单元回归通过；新旧 stdio MCP、实际 Tauri/WebView2、实际 0.2.2 原目录安装升级均通过。当前证据位于 `artifacts/evidence/0.3.1/`；未改变的机制保留表中明确链接的 0.3.0 证据，不声称所有历史场景都重新做过付费实验。安装器中的 Python 源文件逐一与当前源码核对，单实例和原目录升级修复保留。

**两个具体问题的状态：**单行 JSON 真实低预算补测两后端均执行完成，Codex 随后检查精确字节、全部 900 个 items、最终报告、工具证据及恢复记录，验收通过。原引擎本地计量 16,000、供应商报告 15,969 Token；Pi 两项均为 13,695 Token，单轮硬上限仍为 32,768，没有续作或增加预算。思考模式摘要沿用已成功实测的同模型普通模式摘要策略；双后端真实 ContextEpoch 重建及后续请求证据见下表 AC14/17，不再将旧截断样本作为最终实现状态。

原 20 次版本对照和 7 次补测原样保留，包括失败样本。新增 2 次真实实验后，共 29 次，本地累计 418,636 / 655,360 Token；未配置价格，金额未知。真实实验每后端只有一个本次场景样本，不声称任意任务都能在低预算下完成或达到固定降幅。

固定本地响应验证预算、协议、恢复和续作机制；真实模型样本验证该配置下的实际行为。执行完成与父代理验收分别记录；源码构建与实际安装升级分别验证。54 项矩阵继续列出方法、证据和未覆盖条件。

[下载完整验收证据包](https://github.com/Favio8/codexrelay/releases/download/v0.3.1/CodexRelay_0.3.1_evidence.zip)。下表证据位置是 ZIP 内的实际路径，包含本次和明确引用的历史验证；本地原始记录位于 artifacts/evidence/。公开安装器、源码及校验文件见 [Release](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1)。

| ID / 用例 | 已实现 | 已验证 | 后端 / 配置 | 方法 | 证据位置 | 未覆盖条件 |
| --- | --- | --- | --- | --- | --- | --- |
'''
    rows = []
    for row in matrix:
        links = ', '.join('`' + item.removeprefix('../artifacts/') + '`' for item in row['evidence'])
        values = [f"{row['id']} {row['case']}", '是', '所列方法通过', row['backend'] + ' / ' + row['configuration'],
                  row['verification_method'], links, row['uncovered']]
        rows.append('| ' + ' | '.join(value.replace('|', '\\|') for value in values) + ' |')
    (ROOT / 'docs/EXECUTION-ACCEPTANCE.md').write_text(intro + '\n'.join(rows) + '\n', encoding='utf-8')
    DEST.mkdir(parents=True, exist_ok=True)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT / name, DEST / name)
    for source, name in (('runtime/THIRD-PARTY-NOTICES.txt', 'THIRD-PARTY-NOTICES.txt'),
                         ('runtime/pi/THIRD_PARTY_LICENSES.txt', 'PI-THIRD-PARTY-LICENSES.txt'),
                         ('runtime/pi/LICENSE', 'NODE-LICENSE.txt'),
                         ('runtime/LICENSE.txt', 'PYTHON-LICENSE.txt')):
        shutil.copyfile(ROOT / source, DEST / name)
    for name in ('EXECUTION-ACCEPTANCE.md', 'EXECUTION-OPTIMIZATION.md', 'RELEASE-0.3.1.md'):
        shutil.copyfile(ROOT / 'docs' / name, DEST / name)
    installer = f'CodexRelay_{VERSION}_x64-setup.exe'
    shutil.copyfile(ROOT / 'src-tauri/target/release/bundle/nsis' / installer, DEST / installer)
    paths = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    manifest = []
    with zipfile.ZipFile(DEST / f'CodexRelay_{VERSION}_source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(paths)):
            file = ROOT / name
            if file.is_file():
                archive.write(file, name)
                manifest.append({'path': name, 'sha256': digest(file)})
    (DEST / 'SOURCE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(previous / 'CodexRelay_0.3.0_source.zip') as old:
        old_hashes = {name: hashlib.sha256(old.read(name)).hexdigest() for name in old.namelist()}
    changes = [{'path': item['path'], 'previous_sha256': old_hashes.get(item['path']), 'sha256': item['sha256']}
               for item in manifest if old_hashes.get(item['path']) != item['sha256']]
    (EVIDENCE / 'source-changes-since-0.3.0.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(DEST / f'CodexRelay_{VERSION}_evidence.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for directory in (ROOT / 'artifacts/evidence/0.3.0', EVIDENCE):
            for file in directory.iterdir():
                if file.is_file(): archive.write(file, f'evidence/{directory.name}/{file.name}')
        for file in (ROOT / 'artifacts/evidence').glob('0.3.*-native.json'):
            archive.write(file, 'evidence/' + file.name)
    (DEST / 'SHA256SUMS.txt').write_text(''.join(digest(file) + '  ' + file.name + '\n' for file in sorted(DEST.iterdir())
                                              if file.is_file() and file.name != 'SHA256SUMS.txt'))
    print(json.dumps({'version': VERSION, 'acceptance_cases': len(matrix), 'source_files': len(manifest),
                      'changed_or_new_since_0_3_0': len(changes), 'installer_sha256': digest(DEST / installer), 'output': str(DEST)}))


if __name__ == '__main__': package()
