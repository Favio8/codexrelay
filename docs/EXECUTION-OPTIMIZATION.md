# 0.3.1 执行与核验后续作

本次提供 [0.3.1 Windows Beta](https://github.com/Favio8/codexrelay/releases/tag/v0.3.1)，发布操作保留本机日常安装。原始 PRD 保留不变。使用现有供应商、单个串行外部代理；Pi Agent Core 1.2.0 负责调度，Python 负责供应商调用、预算、工具、证据和恢复。

## 使用

每轮默认继承供应商预算，结果显示供应商默认、Codex 指定或用户单任务授权。压缩改变下一次请求的上下文，历史消耗不会减少；续作创建独立 Run，组累计和已用次数保留。初始执行加默认 3 次续作，最多 4 轮。

在执行设置调整上下文优化、自动续作和 0–10 次默认次数，仅影响新 TaskGroup。供应商高级设置可填写两个模型档位的真实上下文容量；留空显示未配置，不采用 Pi 的固定容量冒充供应商数据。

任务详情可查看 checkpoint、实际工具输出、文件版本、核验、轮次和授权。追加一次执行机会或调整后续预算需要 App 确认预览，记录政策版本、旧值、新值和操作来源。单轮范围仍为 32,768–20,000,000 Token；有效单任务授权可以高于供应商默认值。当前 Run 预算保持冻结。授权不会开启开关或启动模型。

Codex 的交接顺序：

1. `create_task_group` 提交目标、类型、能力、约束、验收和路径；不调用模型。也可通过既有入口隐式建立新组。
2. `start_agent` / `delegate_to_agent` 或 readonly 对应入口提交 `group_id`；旧 DeepSeek 命名入口始终使用原引擎。
3. Run 结束后读取 `get_task_checkpoint` 和实际文件、测试／引用。`completed` 仅表示执行结束，预算停止、取消或中断也可核验。
4. 查询 `get_task_recovery`，逐项核对事务路径、当前内容及哈希，再确认精确事务 ID；Pi 观察检查另外用当前状态哈希确认。摘要不能替代这些检查。
5. `record_task_verification` 提交 `disposition`、当前 checkpoint ID／SHA256、检查依据、完成项和 `remaining_items`。结果不充分时使用 `needs_more_evidence`，补充真实文件／来源后再核验。
6. `resume_agent` 提交核验 ID、checkpoint ID／SHA256、幂等键，以及与已核验 `remaining_items` 按换行连接完全一致的 `remaining_task`。该入口复用组，不接受扩大的能力或路径。
7. 全部完成记录 `verified_complete`；Codex 接手完成时增加 `parent_completed=true`，显示 `completed_by_parent`，不编造额外 Run。

追加授权仅由 App 产生，没有授予次数或预算的 MCP 工具。达到限制、关闭总开关、禁用项目、取消组或出现未处理恢复事项时，不能自动创建下一轮。取消后只有明确的手动恢复输入及新的有效核验才能续作。App 离线不启动执行链；重启查询持久化结果后仍由 Codex 核验。

## 任务合同

| 类型 | 能力与成果 |
| --- | --- |
| implementation | coding；修改受 task_root/write_paths 约束，返回实际变更及验证 |
| testing | coding 允许命令；禁用文件修改工具，默认 write_paths 为空；临时输出需指定授权路径，不默认改源码 |
| review | readonly；返回可定位发现、引用、影响与建议 |
| analysis | readonly；区分来源、事实、推断和不确定事项 |
| documentation | coding；仅写授权输出，不声称任意 Office/PDF 转换能力 |

命令仍在可信主机执行。目录与清单不是操作系统沙箱；命令范围违规属于执行后检测，变化保留给父代理检查。

## 数据、兼容和隐私

SQLite user_version 从 0.2.2 的 1 原子迁移到 4。新增 TaskGroup／Run 关联、checkpoint、幂等续作、完整结果、工具证据和上下文归档表。旧历史保留原状态，不伪造 TaskGroup、checkpoint 或核验。原供应商预算、项目、开关、价格和恢复记录不重置。

状态刷新读取轻量任务元数据，完整结果通过 `get_task` 按需读取。checkpoint 正文和完整工具输出独立存储，引用带 SHA256；旧上下文在压缩前和终止时归档。`get_task_checkpoint(include_context=true, context_epoch=N)` 可按需读取已封存上下文。摘要输入不会重复附带一份完整工具输出，也不会把不透明思考内容当作事实摘要；原协议历史及归档仍保留原字段。

执行、摘要和重试共用供应商调用与预算层。执行、摘要和缺口请求分别记录 request_kind；截断回复的已返回 usage 仍计量，部分文本不安装为有效摘要或可执行调用。失败降级保留机器证据和缺口，不能单凭摘要直接续作。

API Key 留在 Windows 凭据库及 Python 供应商边界，Node 无凭据和直连供应商能力。选定 Key 在持久化报告中脱敏。checkpoint 可能含项目内容，留在本机，不自动过期删除，不进入默认诊断导出。实际费用只按已知用量及冻结价格估算；缺价、缺 usage 和中断账单保持未知。

## 实施选择及来源

本轮用户确认：当前执行模型生成摘要；默认次数可设 0–10；预算沿用既有范围；追加授权仅由 App；checkpoint 不自动过期删除。默认 3 次、双后端和串行属于先前已确认决策。

其余是可调整的工程起点：最迟 80% 预算提示、90% 收尾、80% 已知窗口触发、未知窗口 128,000 请求字节、摘要输出上限 3,000 Token（含供应商输出计量）、Read 默认 200 行和至多 12,000 字符、读取反馈缓存 128 项。剩余额度不足约三次当前输入及短输出时提前给预算提示，为修改、检查及最终报告留空间。百分比结合实际输入／输出空间判断，不保证所有模型在固定预算内完成。摘要失败不无限重试。

0.3.1 对大单行文件默认返回 2,000 字符预览、精确 next_read 和搜索提示，显式 char_offset/char_limit/full 仍可读取需要的范围。对已知文件和局部修改，直接用 Grep 搜索目标；实际匹配片段即可作为 Edit 前的检查依据，无须再分页读完无关数据。修改后验证局部，需检查整份 JSON 时用解析器返回紧凑结果。预算在首个请求就告知执行模型，历史输入的重复计费也明确说明。这些选择同时适用于原引擎与 Pi，不增加授权额度或隐藏调用。

GitHub 参考固定到 [Pi compaction 9a100c7](https://github.com/earendil-works/pi/blob/9a100c7cc4707f013438b13969c6671bc0a33c4c/packages/coding-agent/src/core/compaction/compaction.ts) 和 [Aider history 19a7864](https://github.com/Aider-AI/aider/blob/19a786416846d3ce446973f6e9dabb859642a9d2/aider/history.py)。借鉴合法切点、近期交换保留、结构化索引和失败降级，未引入完整 coding-agent 或 Aider 依赖。已核对锁定的 Pi Agent Core 1.2.0 `prepareRequest`：它能替换当前及后续调度上下文；实现同步 Agent.state，保留原 relayRaw 思考与工具协议。参考仓库的新接口不直接套用于锁定 SDK。

升级继续采用 0.2.2 的 NSIS 原目录维护、活动任务拒绝和 Tauri 单实例。安装后更新托管规则并重启 Codex，以加载新增工具。验证及真实限制见 [逐项验收](EXECUTION-ACCEPTANCE.md)。

已知 DeepSeek 官方接口的摘要使用同一模型的普通模式，执行仍保持启动时的推理设置；其他供应商沿用配置并可失败降级。该实现选择根据截断实测和[官方模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)制定，未增加模型或供应商。摘要请求单独记录推理配置与真实消耗。

核验完成前仍须处理恢复事项。父代理完成剩余修改时可用 checked_files 绑定所有重新检查的当前版本，记录 completed_by_parent；不会覆写原 checkpoint 或编造新外部 Run。授权预览使用当前所选后续供应商的实际默认预算。引擎断开时，在途请求保留输入估算及账单缺口。
