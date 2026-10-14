# 0.3.1 逐项验收与发布

基线为已发布的 0.2.2 Windows Beta；0.3.0 的 M1—M4 实现保留，本次修复低预算下的大单行文件执行。原始 PRD 未修改。完整修复说明见 [0.3.1 交付报告](RELEASE-0.3.1.md)。

170 项单元回归通过；新旧 stdio MCP、实际 Tauri/WebView2、实际 0.2.2 原目录安装升级均通过。当前证据位于 `artifacts/evidence/0.3.1/`；未改变的机制保留表中明确链接的 0.3.0 证据，不声称所有历史场景都重新做过付费实验。安装器中的 Python 源文件逐一与当前源码核对，单实例和原目录升级修复保留。

**两个具体问题的状态：**单行 JSON 真实低预算补测两后端均执行完成，Codex 随后检查精确字节、全部 900 个 items、最终报告、工具证据及恢复记录，验收通过。原引擎本地计量 16,000、供应商报告 15,969 Token；Pi 两项均为 13,695 Token，单轮硬上限仍为 32,768，没有续作或增加预算。思考模式摘要沿用已成功实测的同模型普通模式摘要策略；双后端真实 ContextEpoch 重建及后续请求证据见下表 AC14/17，不再将旧截断样本作为最终实现状态。

原 20 次版本对照和 7 次补测原样保留，包括失败样本。新增 2 次真实实验后，共 29 次，本地累计 418,636 / 655,360 Token；未配置价格，金额未知。真实实验每后端只有一个本次场景样本，不声称任意任务都能在低预算下完成或达到固定降幅。

固定本地响应验证预算、协议、恢复和续作机制；真实模型样本验证该配置下的实际行为。执行完成与父代理验收分别记录；源码构建与实际安装升级分别验证。54 项矩阵继续列出方法、证据和未覆盖条件。

[下载完整验收证据包](https://github.com/Favio8/codexrelay/releases/download/v0.3.1/CodexRelay_0.3.1_evidence.zip)。下表证据位置是 ZIP 内的实际路径，包含本次和明确引用的历史验证；本地原始记录位于 artifacts/evidence/。公开安装器、源码及校验文件见 [Release](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1)。

| ID / 用例 | 已实现 | 已验证 | 后端 / 配置 | 方法 | 证据位置 | 未覆盖条件 |
| --- | --- | --- | --- | --- | --- | --- |
| AC01 默认预算来源 | 是 | 所列方法通过 | both / provider_default=1000000 | test_default_override_budget_sources_both_backends | `evidence/0.3.1/full-unit.log` | 其他供应商价格和模型窗口未逐一实测 |
| AC02 显式低预算 | 是 | 所列方法通过 | both / token_budget=40000 | test_default_override_budget_sources_both_backends | `evidence/0.3.1/full-unit.log` | 无 |
| AC03 下一请求空间不足 | 是 | 所列方法通过 | both thinking / 32768 | 真实 stdio 预算停止及已知消耗保留 | `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.0/budget-mcp-final.log` | 供应商精确 Token 预测不可得 |
| AC04 同轮上下文段 | 是 | 所列方法通过 | both / same Run | test_epoch_preserves_reasoning_protocol_budget_and_deadline；真实 API epoch=1 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-summary-ordinary-validation.json` | 极端 50 轮内存峰值未测 |
| AC05 摘要纳入计量 | 是 | 所列方法通过 | both / summary request_kind | 共享计量、请求序号及真实摘要 usage | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-summary-ordinary-validation.json` | 金额缺价未知 |
| AC06 摘要超预算降级 | 是 | 所列方法通过 | shared control / 32760/32768 used | test_summary_over_budget_does_not_send_request | `evidence/0.3.1/full-unit.log` | 無真实账户超额实验 |
| AC07 大单行有界分页 | 是 | 所列方法通过 | both / 2000-character long-line preview; explicit ranges; 32768 per run | Exact pagination and target-search tests; both real backends completed, exact flag-only bytes and all 900 items checked by parent | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-model-quality.json`, `evidence/0.3.1/real-low-budget-validation.json`, `evidence/0.3.1/parent-low-budget-audit.json` | One real sample per backend; arbitrary JSON/CSS structures not exhausted |
| AC08 长多行有界 | 是 | 所列方法通过 | shared tools / 200 lines | test_read_multiline_bounds_and_explicit_full_remain_bounded | `evidence/0.3.1/full-unit.log` | 超出既有文本文件硬限不支持 |
| AC09 重复成功读取反馈 | 是 | 所列方法通过 | shared tools / same version/range/epoch | test_repeat_read_feedback_is_not_no_progress | `evidence/0.3.1/full-unit.log` | 不保证模型遵循效率建议 |
| AC10 修改后新读取 | 是 | 所列方法通过 | shared tools / purpose=verify | test_changed_file_and_fresh_purpose_do_not_reuse_stale_content | `evidence/0.3.1/full-unit.log` | 可信主机并发改动不能完全隔离 |
| AC11 压缩后原文恢复 | 是 | 所列方法通过 | both / epoch cache cleared | test_two_backends_sync_semantic_epochs_and_use_same_usage_pipeline | `evidence/0.3.1/full-unit.log` | 按需重读，不提供无损记忆保证 |
| AC12 只读进展 | 是 | 所列方法通过 | both / review/analysis | 只读无文件修改仍执行完成；引用判据 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-model-quality.json` | 复杂审查结论的通用正确率未验证 |
| AC13 重复失败保护 | 是 | 所列方法通过 | shared tools / two equivalent failures | test_execution_reliability ProgressGuard cases | `evidence/0.3.1/full-unit.log` | 不识别所有语义等价命令 |
| AC14 思考协议合法 | 是 | 所列方法通过 | both thinking / high execution / ordinary summary | 完整交换与真实 API 重建后成功请求 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.0/real-summary-ordinary-validation.json` | 仅实测官方 deepseek-flash |
| AC15 普通模式语义摘要 | 是 | 所列方法通过 | both fixed response / structured JSON | test_epoch_preserves_reasoning_protocol_budget_and_deadline | `evidence/0.3.1/full-unit.log` | 其他普通模式真实模型未测 |
| AC16 未完成批次不重建 | 是 | 所列方法通过 | shared control / unpaired calls | test_incomplete_tool_batch_prevents_rebuild | `evidence/0.3.1/full-unit.log` | 无 |
| AC17 Pi 状态同步 | 是 | 所列方法通过 | Pi 1.2.0 / prepareRequest | 实际 SDK 上下文消息数有界及真实重建 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-summary-ordinary-validation.json` | 未做最大轮数峰值内存基准 |
| AC18 双后端临界预算一致 | 是 | 所列方法通过 | both / 32768 fixed sequence | 同流程预算停止、checkpoint 和续作 | `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.1/full-unit.log` | 不保证所有模型响应序列相同 |
| AC19 执行完成不等于验收 | 是 | 所列方法通过 | shared control / verification=pending | test_completed_is_pending_not_accepted | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log` | 语义验收依赖父代理独立检查 |
| AC20 部分修改预算停止 | 是 | 所列方法通过 | both thinking / partial.txt / journal | 真实文件、checkpoint 与精确事务对应 | `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.0/budget-mcp-final.log` | 命令变化不冒称原子 journal |
| AC21 未确认 journal 阻止 | 是 | 所列方法通过 | both / pending transaction_ids | resume_agent 被 recovery 门禁拒绝 | `evidence/0.3.1/execution-mcp.log` | 无 |
| AC22 Pi 检查独立确认 | 是 | 所列方法通过 | Pi / review_id/state_sha256 | 只确认 journal 后工作区检查仍阻止执行 | `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.1/legacy-mcp.log`, `evidence/0.3.1/full-unit.log` | 可信主机外部操作无法强隔离 |
| AC23 摘要不能充当证据 | 是 | 所列方法通过 | shared control / no tool evidence | test_summary_without_actual_evidence_cannot_authorize_continuation | `evidence/0.3.1/full-unit.log` | 父代理提交的检查说明非密码学证明 |
| AC24 仅核验后剩余合同 | 是 | 所列方法通过 | both / PHASE_TWO | verified remaining_items 精确匹配后执行 | `evidence/0.3.1/execution-mcp.log` | 任意自然语言同义目标不程序化识别 |
| AC25 不重放完成写入 | 是 | 所列方法通过 | both / partial SHA unchanged | 第二轮读现状并只创建 remaining.txt | `evidence/0.3.1/execution-mcp.log` | 不保证模型永不尝试重复操作 |
| AC26 不无理由复跑成功测试 | 是 | 所列方法通过 | shared contract / preserved evidence | 提示词／交接保留成功测试与输入版本；实际续作无旧命令重放 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log` | 复杂模型策略需要父代理判断 |
| AC27 第2/3/4轮计数 | 是 | 所列方法通过 | shared + both integration / 3 continuations | test_four_rounds_persist_usage_and_fifth_requires_authorization | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log` | 四轮 Pi 专项质量任务集未测 |
| AC28 第五轮需授权 | 是 | 所列方法通过 | shared control / 4 runs | test_four_rounds_persist_usage_and_fifth_requires_authorization | `evidence/0.3.1/full-unit.log` | 无 |
| AC29 App 单组追加 | 是 | 所列方法通过 | App / shared / add=1 | 原生按钮、Windows 确认及实际 RPC | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1-native.json` | 无 |
| AC30 默认仅影响新组 | 是 | 所列方法通过 | App / shared / defaults version | test_defaults_are_frozen_and_app_extensions_are_versioned_idempotent | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1-native.json` | 无 |
| AC31 后续预算不回写 | 是 | 所列方法通过 | App / shared / next=65536/2000000 | 原生确认、后续预算及已冻结当前 Run 核验 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1-native.json` | 后续选择供应商可能改变未授权默认预算 |
| AC32 MCP 不能自加额度 | 是 | 所列方法通过 | shared control / provider ceiling | test_authorized_budget_applies_only_to_new_round_and_cannot_exceed_it | `evidence/0.3.1/full-unit.log` | 可信主机命令不是操作系统安全沙箱 |
| AC33 续作幂等 | 是 | 所列方法通过 | both integration / same key | 重复请求返回同一 Run | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log` | 无 |
| AC34 授权并发／旧版本 | 是 | 所列方法通过 | App / shared / expected_version | test_two_concurrent_grants_only_apply_once；原生 RPC 重放 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1-native.json` | 无 |
| AC35 总开关关闭 | 是 | 所列方法通过 | both / disabled | 查询核验可用，新 Run 拒绝，次数不消耗 | `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.1/legacy-mcp.log` | 无 |
| AC36 项目禁用 | 是 | 所列方法通过 | shared control / disabled project | test_switch_project_cancel_and_wrong_remaining_contract_block_resume | `evidence/0.3.1/full-unit.log` | 无 |
| AC37 取消链门禁 | 是 | 所列方法通过 | shared control / cancelled | 无自动续作；显式手动恢复参数及核验 | `evidence/0.3.1/full-unit.log` | 无 |
| AC38 退出 App 生命周期 | 是 | 所列方法通过 | existing lifecycle / isolated GUI/RPC exit | run_lifecycle GUI 退出时引擎继续完成 | `evidence/0.3.0/lifecycle.log` | 无干净机器断电实验 |
| AC39 退出引擎持久恢复 | 是 | 所列方法通过 | shared control / dead PID / persisted state | test_offline_interruption_preserves_checkpoint_without_new_run；stdio 后新 Store 查询 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log` | 不跨进程原样恢复模型会话 |
| AC40 相关文件变化失效 | 是 | 所列方法通过 | shared control / changed SHA | test_related_change_invalidates_unrelated_change_does_not | `evidence/0.3.1/full-unit.log` | 不强锁 IDE／外部文件写入 |
| AC41 无关改动不丢进度 | 是 | 所列方法通过 | shared control / unrelated.txt | test_related_change_invalidates_unrelated_change_does_not | `evidence/0.3.1/full-unit.log` | 无 |
| AC42 后续切模型／后端 | 是 | 所列方法通过 | DeepSeek -> Pi / pro slot | test_switch_backend_model_preserves_group_and_contract | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/legacy-mcp.log` | 未知供应商兼容性未逐一验证 |
| AC43 review/analysis 合同 | 是 | 所列方法通过 | both / readonly | 类型／能力拒绝及真实引用关键词判据 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-model-quality.json` | 复杂结论语义正确率未测；旧样本正文未全部保留 |
| AC44 testing 默认不写源码 | 是 | 所列方法通过 | shared tools / write_paths=[] | test_testing_cannot_mutate_source_even_with_coding_label | `evidence/0.3.1/full-unit.log` | 命令违规是执行后检测 |
| AC45 documentation 范围 | 是 | 所列方法通过 | both / report.md | test_documentation_rejects_unauthorized_outputs；实际文档内容复核 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/real-model-quality.json` | Office/PDF 任意格式转换不在范围 |
| AC46 认证／余额不反复 | 是 | 所列方法通过 | shared provider / insufficient_quota | 真实 HTTP 错误分类无重试；续作需配置修订 | `evidence/0.3.0/budget-mcp-final.log`, `evidence/0.3.1/full-unit.log` | 恢复余额后需明确配置／策略处理 |
| AC47 父代理完成不造 Run | 是 | 所列方法通过 | shared control / parent_completed | 实际父代理改变文件后绑定当前哈希；外部用量不变 | `evidence/0.3.1/full-unit.log` | 父代理自身用量不并入外部硬限 |
| AC48 缺失／中断用量 | 是 | 所列方法通过 | shared control / usage gap / disconnect | test_disconnected_request_retains_estimate_and_usage_gap；截断 usage 保留 | `evidence/0.3.1/full-unit.log` | 无法补齐未返回的实际账单 |
| AC49 组／日用量不重复 | 是 | 所列方法通过 | shared usage / job_id/sequence | usage events 主键与两后端组累计断言 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log` | 未知价格不报精确金额 |
| AC50 凭据不进入结果／Node | 是 | 所列方法通过 | both / App / sanitized env | 实际命令环境、Node 白名单、字符串脱敏与诊断 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.0/lifecycle.log`, `evidence/0.3.1-native.json` | 没有做全主机进程内存取证 |
| AC51 旧接口／历史 | 是 | 所列方法通过 | legacy + new / schema 1 -> 4 | 29 个工具兼容查询；旧记录无伪造组/checkpoint | `evidence/0.3.1/legacy-mcp.log`, `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/installer.log` | 旧客户端不识别新状态需按错误／ready 字段处理 |
| AC52 0.2.2 安装升级 | 是 | 所列方法通过 | Windows x64 / actual NSIS | 实际基线安装器、旧进程、原目录、源文件及确定 bundle 标记核验 | `evidence/0.3.1/installer.log`, `evidence/0.3.0/installer-legacy-single-instance.log` | 新机器／Windows10／ARM64 未测 |
| AC53 中文空格 namespace | 是 | 所列方法通过 | Windows x64 / drive namespace | 文件读取、scope／证据规范化与项目禁用；中文安装路径 | `evidence/0.3.1/full-unit.log`, `evidence/0.3.1/execution-mcp.log`, `evidence/0.3.1/installer.log` | 网络 UNC 文件系统不宣称支持 |
| AC54 真实模型效率报告 | 是 | 所列方法通过 | both real DeepSeek / 29 paid runs / combined metering 418636 <= 655360 | Original comparison and failed samples retained; real summary epochs succeeded; 0.3.1 low-budget tasks 2/2 execution complete and parent accepted | `evidence/0.3.0/real-model-quality.json`, `evidence/0.3.0/real-summary-ordinary-validation.json`, `evidence/0.3.1/real-low-budget-validation.json`, `evidence/0.3.1/parent-low-budget-audit.json` | Small sample; no general fixed savings/completion-rate claim; old baseline lacks some efficiency metrics |
