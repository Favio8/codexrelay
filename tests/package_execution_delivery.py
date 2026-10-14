"""Package the reviewed local delivery and its 54-case acceptance map; never publish/install."""
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'artifacts/evidence/0.3.0'
DEST = ROOT / 'artifacts/releases/0.3.0'

CASES = [
('AC01','默认预算来源','U','both','provider_default=1000000','test_default_override_budget_sources_both_backends','其他供应商价格和模型窗口未逐一实测'),
('AC02','显式低预算','U','both','token_budget=40000','test_default_override_budget_sources_both_backends','无'),
('AC03','下一请求空间不足','P/B','both thinking','32768','真实 stdio 预算停止及已知消耗保留','供应商精确 Token 预测不可得'),
('AC04','同轮上下文段','U/R','both','same Run','test_epoch_preserves_reasoning_protocol_budget_and_deadline；真实 API epoch=1','极端 50 轮内存峰值未测'),
('AC05','摘要纳入计量','U/R','both','summary request_kind','共享计量、请求序号及真实摘要 usage','金额缺价未知'),
('AC06','摘要超预算降级','U','shared control','32760/32768 used','test_summary_over_budget_does_not_send_request','無真实账户超额实验'),
('AC07','大单行有界分页','U/Q','both','12000 chars','test_read_single_line_exact_character_pagination；JSON 实验','低预算真实样本仍有未完成'),
('AC08','长多行有界','U','shared tools','200 lines','test_read_multiline_bounds_and_explicit_full_remain_bounded','超出既有文本文件硬限不支持'),
('AC09','重复成功读取反馈','U','shared tools','same version/range/epoch','test_repeat_read_feedback_is_not_no_progress','不保证模型遵循效率建议'),
('AC10','修改后新读取','U','shared tools','purpose=verify','test_changed_file_and_fresh_purpose_do_not_reuse_stale_content','可信主机并发改动不能完全隔离'),
('AC11','压缩后原文恢复','U','both','epoch cache cleared','test_two_backends_sync_semantic_epochs_and_use_same_usage_pipeline','按需重读，不提供无损记忆保证'),
('AC12','只读进展','U/Q','both','review/analysis','只读无文件修改仍执行完成；引用判据','复杂审查结论的通用正确率未验证'),
('AC13','重复失败保护','U','shared tools','two equivalent failures','test_execution_reliability ProgressGuard cases','不识别所有语义等价命令'),
('AC14','思考协议合法','U/P/R','both thinking','high execution / ordinary summary','完整交换与真实 API 重建后成功请求','仅实测官方 deepseek-flash'),
('AC15','普通模式语义摘要','U','both fixed response','structured JSON','test_epoch_preserves_reasoning_protocol_budget_and_deadline','其他普通模式真实模型未测'),
('AC16','未完成批次不重建','U','shared control','unpaired calls','test_incomplete_tool_batch_prevents_rebuild','无'),
('AC17','Pi 状态同步','U/R','Pi 1.2.0','prepareRequest','实际 SDK 上下文消息数有界及真实重建','未做最大轮数峰值内存基准'),
('AC18','双后端临界预算一致','P/U','both','32768 fixed sequence','同流程预算停止、checkpoint 和续作','不保证所有模型响应序列相同'),
('AC19','执行完成不等于验收','U/P','shared control','verification=pending','test_completed_is_pending_not_accepted','语义验收依赖父代理独立检查'),
('AC20','部分修改预算停止','P/B','both thinking','partial.txt / journal','真实文件、checkpoint 与精确事务对应','命令变化不冒称原子 journal'),
('AC21','未确认 journal 阻止','P','both','pending transaction_ids','resume_agent 被 recovery 门禁拒绝','无'),
('AC22','Pi 检查独立确认','P/L/U','Pi','review_id/state_sha256','只确认 journal 后工作区检查仍阻止执行','可信主机外部操作无法强隔离'),
('AC23','摘要不能充当证据','U','shared control','no tool evidence','test_summary_without_actual_evidence_cannot_authorize_continuation','父代理提交的检查说明非密码学证明'),
('AC24','仅核验后剩余合同','P','both','PHASE_TWO','verified remaining_items 精确匹配后执行','任意自然语言同义目标不程序化识别'),
('AC25','不重放完成写入','P','both','partial SHA unchanged','第二轮读现状并只创建 remaining.txt','不保证模型永不尝试重复操作'),
('AC26','不无理由复跑成功测试','U/P','shared contract','preserved evidence','提示词／交接保留成功测试与输入版本；实际续作无旧命令重放','复杂模型策略需要父代理判断'),
('AC27','第2/3/4轮计数','U/P','shared + both integration','3 continuations','test_four_rounds_persist_usage_and_fifth_requires_authorization','四轮 Pi 专项质量任务集未测'),
('AC28','第五轮需授权','U','shared control','4 runs','test_four_rounds_persist_usage_and_fifth_requires_authorization','无'),
('AC29','App 单组追加','U/N','App / shared','add=1','原生按钮、Windows 确认及实际 RPC','无'),
('AC30','默认仅影响新组','U/N','App / shared','defaults version','test_defaults_are_frozen_and_app_extensions_are_versioned_idempotent','无'),
('AC31','后续预算不回写','U/N','App / shared','next=65536/2000000','原生确认、后续预算及已冻结当前 Run 核验','后续选择供应商可能改变未授权默认预算'),
('AC32','MCP 不能自加额度','U','shared control','provider ceiling','test_authorized_budget_applies_only_to_new_round_and_cannot_exceed_it','可信主机命令不是操作系统安全沙箱'),
('AC33','续作幂等','U/P','both integration','same key','重复请求返回同一 Run','无'),
('AC34','授权并发／旧版本','U/N','App / shared','expected_version','test_two_concurrent_grants_only_apply_once；原生 RPC 重放','无'),
('AC35','总开关关闭','P/L','both','disabled','查询核验可用，新 Run 拒绝，次数不消耗','无'),
('AC36','项目禁用','U','shared control','disabled project','test_switch_project_cancel_and_wrong_remaining_contract_block_resume','无'),
('AC37','取消链门禁','U','shared control','cancelled','无自动续作；显式手动恢复参数及核验','无'),
('AC38','退出 App 生命周期','C','existing lifecycle','isolated GUI/RPC exit','run_lifecycle GUI 退出时引擎继续完成','无干净机器断电实验'),
('AC39','退出引擎持久恢复','U/P','shared control','dead PID / persisted state','test_offline_interruption_preserves_checkpoint_without_new_run；stdio 后新 Store 查询','不跨进程原样恢复模型会话'),
('AC40','相关文件变化失效','U','shared control','changed SHA','test_related_change_invalidates_unrelated_change_does_not','不强锁 IDE／外部文件写入'),
('AC41','无关改动不丢进度','U','shared control','unrelated.txt','test_related_change_invalidates_unrelated_change_does_not','无'),
('AC42','后续切模型／后端','U/L','DeepSeek -> Pi','pro slot','test_switch_backend_model_preserves_group_and_contract','未知供应商兼容性未逐一验证'),
('AC43','review/analysis 合同','U/Q','both','readonly','类型／能力拒绝及真实引用关键词判据','复杂结论语义正确率未测；旧样本正文未全部保留'),
('AC44','testing 默认不写源码','U','shared tools','write_paths=[]','test_testing_cannot_mutate_source_even_with_coding_label','命令违规是执行后检测'),
('AC45','documentation 范围','U/Q','both','report.md','test_documentation_rejects_unauthorized_outputs；实际文档内容复核','Office/PDF 任意格式转换不在范围'),
('AC46','认证／余额不反复','B/U','shared provider','insufficient_quota','真实 HTTP 错误分类无重试；续作需配置修订','恢复余额后需明确配置／策略处理'),
('AC47','父代理完成不造 Run','U','shared control','parent_completed','实际父代理改变文件后绑定当前哈希；外部用量不变','父代理自身用量不并入外部硬限'),
('AC48','缺失／中断用量','U','shared control','usage gap / disconnect','test_disconnected_request_retains_estimate_and_usage_gap；截断 usage 保留','无法补齐未返回的实际账单'),
('AC49','组／日用量不重复','U/P','shared usage','job_id/sequence','usage events 主键与两后端组累计断言','未知价格不报精确金额'),
('AC50','凭据不进入结果／Node','U/C/N','both / App','sanitized env','实际命令环境、Node 白名单、字符串脱敏与诊断','没有做全主机进程内存取证'),
('AC51','旧接口／历史','L/U/I','legacy + new','schema 1 -> 4','29 个工具兼容查询；旧记录无伪造组/checkpoint','旧客户端不识别新状态需按错误／ready 字段处理'),
('AC52','0.2.2 安装升级','I/G','Windows x64','actual NSIS','实际基线安装器、旧进程、原目录、源文件及确定 bundle 标记核验','新机器／Windows10／ARM64 未测'),
('AC53','中文空格 namespace','U/P/I','Windows x64','drive namespace','文件读取、scope／证据规范化与项目禁用；中文安装路径','网络 UNC 文件系统不宣称支持'),
('AC54','真实模型效率报告','Q/R','both real DeepSeek','27 runs / bounded quota','版本对照、真实摘要重建、完成与用量分别记录','小样本；未证明固定 Token 降幅／四轮必完成；部分基线效率指标未知'),
]


def package():
    DEST.mkdir(parents=True, exist_ok=True)
    unit = (EVIDENCE / 'full-unit-final.log').read_text(encoding='utf-8')
    if '\nOK\n' not in unit: raise ValueError('full unit result is not passing')
    for report in ('installer-0.2.2.json', 'execution-mcp.json'):
        if not json.loads((EVIDENCE / report).read_text())['ok']: raise ValueError(report + ' not passing')
    native = json.loads((ROOT / 'artifacts/evidence/0.3.0-native.json').read_text())
    if not native['ok'] or native.get('uncovered'): raise ValueError('native confirmation coverage incomplete')
    final_real = json.loads((EVIDENCE / 'real-summary-ordinary-validation.json').read_text())
    if not all(row['parent_verified'] for row in final_real['runs']): raise ValueError('real summary reconstruction not verified')
    if final_real['combined_metering'] > 655360: raise ValueError('authorized test cap exceeded')
    mapping = {key: f'../artifacts/evidence/0.3.0/{file}' for key, file in {
        'U':'full-unit-final.log','P':'execution-mcp-final.log','L':'mcp-legacy-final.log','B':'budget-mcp-final.log',
        'C':'lifecycle.log','I':'installer-0.2.2-final.log','G':'installer-legacy-single-instance.log',
        'Q':'real-model-quality.json','R':'real-summary-ordinary-validation.json'}.items()}
    mapping['N'] = '../artifacts/evidence/0.3.0-native.json'
    intro = '''# 0.3.0 逐项验收与本地交付

基线：0.2.2 Windows Beta。交付环境：Windows 11 x64 / Tauri / WebView2 / Python 3.12 / Pi Agent Core 1.2.0。日期：2026-10-14（Asia/Shanghai）。原始 PRD 未修改。

下表“已验证”指所列环境与方法，不扩展为任意模型／主机保证。固定本地响应测试验证机制；真实模型实验验证特定样本；执行完成、程序化判据通过和 Codex 的业务验收分开。源构建通过不替代实际安装。完整日志与机器可读矩阵一并提供。

本地回归、stdio MCP、原生界面及实际 0.2.2 安装升级均通过。原生追加次数和调整预算使用真实 Windows 确认框。安装源码逐文件核验，原生程序仅允许 Tauri 官方 `UNK -> NSS` 标记的确定 3 字节变换，其他字节完全一致。

真实对照首轮：0.2.2 为 10/10 执行完成、10/10 程序化判据通过，已报告 110,062、本地计量 120,069 Token；迭代早期 0.3.0 为 8/10 执行完成、9/10 判据通过，已报告 134,342、本地计量 141,200 Token。相同 32,768 的低预算没有证明首轮效率提高。JSON 补测原引擎产出了正确修改但执行停止，Pi 仍未完成。

发现思考模式摘要截断后，改用同一模型的普通模式生成结构化索引，执行配置保持 high。两后端真实上下文重建成功：原引擎 17,119 -> 7,122 字节，Pi 17,184 -> 8,073 字节；这些是单次上下文变化，不是消耗退款。共 27 次授权实测，本地计量 388,941 / 655,360 Token。价格未配置，金额未知；原始缺口账单不伪补齐。

实测配置和版本快照保留在 `.tmp/execution-*`；最终两后端摘要有完整本地 telemetry。27 个工作区的实际文件和恢复记录已独立检查，24 个精确事务、7 个工作区检查已确认。确认恢复不代表每项任务的自然语言目标都已合格。审查实验采用引用／异常关键词判据，复杂语义质量不作一般化结论；首次有效修改前请求数等未完整采集的旧基线指标标为未知。

## 54 项矩阵

证据缩写：U 单元；P 新版真实 stdio＋固定响应；L 兼容接口；B 预算/供应商错误；N 实际 Tauri；C 生命周期；I 实际 0.2.2 升级；G 旧目录/单实例；Q 真实质量对照；R 真实摘要。共享控制层用例不声称另做了每种真实模型的相同实验。

| ID / 用例 | 已实现 | 已验证 | 后端 / 配置 | 方法 | 证据位置 | 未覆盖条件 |
| --- | --- | --- | --- | --- | --- | --- |
'''
    lines = []
    structured = []
    for identifier, title, refs, backend, config, method, gap in CASES:
        links = ', '.join(f'[{ref}]({mapping[ref]})' for ref in refs.split('/'))
        lines.append(f'| {identifier} {title} | 是 | 所列方法通过 | {backend} / {config} | {method} | {links} | {gap} |')
        structured.append(dict(id=identifier, case=title, implemented=True, verified=True, backend=backend, configuration=config,
                               verification_method=method, evidence=[mapping[r] for r in refs.split('/')], uncovered=gap))
    if len(structured) != 54 or len({r['id'] for r in structured}) != 54: raise ValueError('54 unique acceptance rows required')
    (ROOT / 'docs/EXECUTION-ACCEPTANCE.md').write_text(intro + '\n'.join(lines) + '\n\n本次不自动推送远端、发布 Release 或替换日常安装。安装后新增工具需要更新托管规则并重启 Codex。\n', encoding='utf-8')
    (EVIDENCE / 'acceptance-matrix.json').write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT / name, DEST / name)
    for name in ('EXECUTION-ACCEPTANCE.md', 'EXECUTION-OPTIMIZATION.md', 'RELEASE-0.3.0.md'):
        shutil.copyfile(ROOT / 'docs' / name, DEST / name)
    shutil.copyfile(ROOT / 'src-tauri/target/release/bundle/nsis/CodexRelay_0.3.0_x64-setup.exe', DEST / 'CodexRelay_0.3.0_x64-setup.exe')
    paths = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    manifest = []
    with zipfile.ZipFile(DEST / 'CodexRelay_0.3.0_source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(paths)):
            file = ROOT / name
            if not file.is_file(): continue
            archive.write(file, name)
            manifest.append(dict(path=name, sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    (DEST / 'SOURCE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(DEST / 'CodexRelay_0.3.0_evidence.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in EVIDENCE.iterdir():
            if file.is_file(): archive.write(file, 'evidence/' + file.name)
        for file in (ROOT / 'artifacts/evidence').glob('0.3.0-*'):
            if file.is_file(): archive.write(file, 'evidence/' + file.name)
    entries = []
    for file in sorted(DEST.iterdir()):
        if file.is_file() and file.name != 'SHA256SUMS.txt': entries.append(hashlib.sha256(file.read_bytes()).hexdigest() + '  ' + file.name)
    (DEST / 'SHA256SUMS.txt').write_text('\n'.join(entries) + '\n', encoding='utf-8')
    print(json.dumps(dict(acceptance_cases=len(structured), source_files=len(manifest), output=str(DEST))))


if __name__ == '__main__': package()
