# 委托可靠性实验：本地工具与人工客户端操作

本地实现提供五类冻结案例、独立 workspace、试次身份、显式会话绑定、请求累计授权、独立评分和成本报告。准备、预览、评分和生成报告都不发送模型请求。当前真实 Codex Desktop／CLI 聊天和付费模型对照尚未执行；下列确定性记录验证机制，不是模型质量或收益结论。

发布操作不自动修改使用者的日常 Codex 配置、总开关或安装；真实模型费用仍需具体授权。45 试次仅已准备材料，没有真实实验授权。对真实实验，先审阅具体的隔离配置、模型、限额和日志范围，再确认新的授权预览。

## 本地入口

App 的“收益评测”页面可以创建草稿、冻结材料、预览累计额度、读取试次指令、绑定新聊天、导入主动选择的日志、登记执行结局、运行评分和查看报告。界面可选择当前供应商 Flash／Pro 档位、主模型推理设置及环境，分别记录订阅／API 计费方式，并填写主模型和外部模型的参考价格、来源及核对时间。单价单位为每百万 Token，缓存价格可留空保持未知；授权金额上限显示实验冻结币种。创建后条件冻结，页面继续改设置只影响新草稿，后台改供应商不改变既有清单。主模型名称或供应商未知时，不产生完整计量或完整参考价收益。

界面可登记失败、取消、预算停止、中断、超时、未派工及主代理接手；未派工需选择实际原因。关联子聊天和分阶段耗时通过本地 CLI 或同一 RPC 合同登记。操作失败保留错误，不自动重放或用刷新掩盖失败。CLI 参数为 JSON 文件，路径和数据不会通过 shell 字符串拼装。所有调用明确指定隔离状态目录，例如：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'engine')
.\runtime\python.exe -X utf8 -m codexrelay.evaluation --state 'D:\chosen\isolated-state' create --params 'D:\chosen\create.json'
.\runtime\python.exe -X utf8 -m codexrelay.evaluation --state 'D:\chosen\isolated-state' prepare --params 'D:\chosen\prepare.json' --output 'D:\chosen\manifest.json'
```

`create.json` 的字段是 `name` 和 `conditions`。需冻结实际 `main_model`、`main_provider`、`main_reasoning`、`external_provider_revision`、`external_model`、`external_slot`（flash/pro）、`external_reasoning`、`runtime_version`、`policy_version`、`dependency_version`、`environment`、`currency`、`main_billing` 和 `external_billing`。未知项明确写 `unknown`；API 价格 `main_price`／`external_price` 可以为 null。给定价格时使用十进制字符串，记录 input/output/cached_input/cache_write、currency、source 和 observed_at；整个实验使用同一冻结币种。换汇在准备前显式完成，不能把不同币种直接相加。

`prepare.json` 包含 `experiment_id`、绝对 `workspace_root` 和 `repetitions`。默认一次生成 5 案例 × A/B/C = 15 个独立 workspace。三次生成 45 试次，每轮轮换 A/B/C、B/C/A、C/A/B 的顺序。初始文件、任务、验收器、条件和清单均有 hash。已存在的实验目录和已开始的试次不被重用；中断重做需新试次身份，不重置旧消耗。

RPC 是 `evaluation`，参数为 `{action, params}`。`action` 使用 Python 方法名，包括 `create_experiment`、`prepare_experiment`、`describe`、`authorization_preview`、`authorize`、`start_trial`、`bind_session`、`add_related_sessions`、`trial_instruction`、`import_main_log`、`record_main_usage`、`finish_trial`、`score_trial`、`report` 和 `set_state`。该 API 不暴露任意 SQL、任意方法、手工 request reservation 或伪造 Run 入口。

任务开始后创建了新的主模型子聊天时，先调用 `add_related_sessions(trial_id, related_sessions=[实际子聊天 ID], confirmed=true)`，再导入该子聊天日志。CLI 对应 `add-related --params 文件.json`。操作只追加明确确认的会话，不替换 root、主 session 或客户端；重复追加幂等，其他试次已占用的 session 会被拒绝。普通主聊天绑定仍不能改成别的聊天，历史未归属／冲突记录不会借此自动清空。

## 冻结案例与独立判据

案例是本仓库原创的小型合成材料，随包存放于 `engine/codexrelay/evaluation_fixtures/cases.json`；评分实现 `evaluation_cases.py` 在候选 workspace 外。每类仅一个案例是机制试运行，不代表所有同类开发工作。

| 案例 | 执行合同 | 独立判据 |
| --- | --- | --- |
| implementation-clamp-v1 | 只改 clamp.py | 固定功能、包含边界、非法上下界；冻结评分进程完成随机哨兵；退出 0 本身不能通过 |
| testing-inclusive-v1 | 执行现有 unittest，不改源码 | 评分器再次实际运行固定命令、核对退出码／失败测试；结构化答案定位 exclusive_upper_bound |
| review-auth-v1 | 只读 auth.py／utils.py | 精确缺陷路径、行号、影响与正常文件；额外误报不通过 |
| analysis-timeouts-v1 | 只读配置和 JSONL | 精确计数、来源行、配置、median；根因未知不得改作事实 |
| documentation-runbook-v1 | 只新增 RUNBOOK.md | 必需事实／来源、输出范围和禁止的相反保证；目前为固定事实检查器，未声称自由文本语义评分 |

testing／review／analysis 的答案由主代理或操作者保存为工作区外的 JSON 文件，`score_trial(trial_id, answer_file=绝对路径)` 引用并绑定其 hash。该文件正文不进入默认报告。其 schema 和稳定词汇写在试次 task 内。implementation／documentation 直接检查真实文件。评分不会接受执行代理的“完成”声明，也不使用它改写的测试。源码、答案、输入或验收器变化使旧分数失效。

评分进程使用固定参数和截止时间，源码仍在可信主机权限下运行；这是本地测试，不能当作操作系统沙箱。评分报告只保留 checks、退出码、命令／输出 hash、文件 hash 和证据引用。

## 累计授权与请求合同

`authorization_preview` 需要明确的 `caps` 和 `data_scope`。caps 必须同时提供 `external_tokens`、`trial_tokens`、`run_tokens`、`max_trials`、`main_tokens`、`external_cost`。零上限禁止后续请求；external_cost 为 null 表示没有金额门禁，仍保留三层 Token 限额。使用金额门禁而估算未知时拒绝请求，不能补造零价。data_scope 明确 `usage_directory` 和 `raw_evidence_directory`，null 表示不读取。预览包含清单指纹、授权版本、当前累计 Token／金额、已开始试次、在途请求和缺口；新请求或结算改变累计值后旧预览失效。`authorize` 要求同一预览 hash 及明确 confirmed=true。追加授权保留所有历史请求和消耗，不是新实验重置，也不能低于已计消耗。

所有实际外部请求在 Python 共同供应商边界先登记 reservation，再发送；执行、summary、retry、continuation 共用同一账本。已预留的 request_id 禁止自动重放。结算明确区分供应商报告用量、参考价估算和实际账单；没有 usage 时保留 reservation 和缺口，实验进入 needs_review，绝不把中断清零。预留同时记录进程身份和授权版本；进程退出或 Run 已终态而结算未落库时，下一请求拒绝并要求审阅含该缺口的新授权。明确新授权可允许有界续作，既有未知消耗与预留仍保留。准备探针可以使用同一累计账本的 purpose=probe 无 trial 入口；没有明确 experiment 绑定的普通探针不冒称本实验成本。

金额 reservation 是发送前保守估计，不能保证供应商实际账单；未知用量、未知价格和不可界定的计费行为需要停止并核查。真实模型可能报告超出预留的用量，已发生消耗仍保留，并禁止后续超限请求。

Codex 主模型不经过本产品网络边界。产品只能统计明确绑定的已观测用量、限制试次数和显示停止提醒，不能停止宿主在途请求。订阅额度不能从 API 参考估价换算为固定节省比例。

## 单个真实试次的最少人工操作

1. 在隔离安装／配置中确认模型、推理设置、后端、预算、日志目录及停止条件，取得具体真实实验授权。在 App 或 CLI 预览并确认累计额度，然后 `start_trial`；一次只运行一条聊天。
2. 用试次 workspace 打开一个新的 Codex 聊天。复制 `trial_instruction` 返回的任务与调用参数；记录实际 Desktop／CLI 版本和新 chat root/session ID。在评测侧 `bind_session` 确认这一对 ID；有主模型子聊天时显式列入 related_sessions。
3. A 组在已确认的隔离配置里关闭外部委托；由 Codex 直接完成。B/C 组先 `ping` 取得实例，再 `bind_client_session` 报告相同 root/session，保存返回的 client binding ID，再 `ping(client_binding_id=...)` 形成该聊天的调用证据。B 选 deepseek，C 选 pi，供应商／真实模型／推理设置保持相同。
4. B/C 调用 `create_task_group` 时必传 `experiment_id`、`trial_id`、`client_binding_id`，以及完整目标、类型、绝对 task_root、write_paths 和 acceptance。再把返回的 group_id 传给 `start_agent`／`start_agent_readonly`。工作区、真实模型、修订、档位和后端必须与冻结条件相同；实验 workspace 的未关联普通任务会被拒绝，不能规避累计账本。
5. 执行结束后 Codex 读取真实文件／测试与 checkpoint；核对事务及 Pi 工作区检查的当前 hash，再用精确 ID 确认恢复事项。只在独立依据充分时记录 verified_complete 或 verified_partial；summary 本身不授予通过。get_task_recovery／ack 调用带同一 client_binding_id。续作保留同一组、当前 checkpoint、verification ID 及幂等键，只传已核验 remaining_items。
6. 通过 `finish_trial` 登记实际结局。失败、取消、超时、budget_stopped、interrupted、not_delegated 和 completed_by_parent 均保留。未派工原因写 decision／disabled／integration／policy／error，不编造外部 Run。结构化答案保存于用户选择的本地证据目录，评分实际文件和答案。
7. 明确选择本条日志并 `import_main_log(trial_id,path,confirmed_session_id)`；原始 JSONL 保留在所选目录。仅统计与已绑定 session/root 相符的计数，保存实际 model/provider 与来源指纹。同文件重读和正常追加幂等，不重复计数，也不永久降低覆盖。明确绑定的单调累计计数无需供应商 response ID；逐请求与累计来源按会话协调，不能把同一批 Token 加两次。无法证明重叠时保留来源统计和已知下界，不计算完整净收益。真实冲突、fork、截断、无 usage、混入其他聊天、未知／不同实际模型或供应商仍降低覆盖。未知主模型不能套用冻结参考价。没有宿主日志时报告计量不足。
8. `score_trial` 后生成完整 report。每条 A 与同案例／重复轮次的 B/C 配对，查看质量、覆盖、成本和耗时；没有完整计量的配对不输出完整净差。人工原始聊天证据与统计报告分别保存，不把聊天自述当独立客户端验收。

不需要操作者手写每个 MCP 参数：试次指令已包含精确 experiment/trial/workspace、group creation 模板和后续 ID 占位；只需把实际客户端与返回的 ID 补齐。没有可以控制真实 Desktop 的 API 时，上述开新聊天、确认身份及提交目标是必须保留的人工步骤。

日志导入使用流式读取和哈希，不再按 16 MiB 整文件大小拒绝。每次核对读取开始时的固定前缀，正常追加可在下次导入；读取过的前缀被改写或截断仍记缺口。未写完的末尾记录暂缓解析，补完后重新导入；超长单行按有界块跳过并保留异常，不阻止后面的正常用量记录。历史版本已留下的冲突／来源元数据缺失不自动改判，需保留原记录并建立新的可复核试次。

## 六场景真实客户端验收清单

每条分别记录 `client_kind`（desktop／cli）、客户端版本、工具可见性报告、MCP 实例、实验／试次、workspace、binding、group、各 Run、checkpoint、恢复／核验记录与产物 hash。App 自测、local_stdio、本地 HTTP 替身和真实外部模型分开标记。当前六条均待真实客户端及付费实验授权。

| 场景 | 后端与任务 | 预先固定的动作／停止条件 | 通过依据 |
| --- | --- | --- | --- |
| D1 | 原引擎，analysis 只读 | 完整提交 analysis-timeouts-v1；不允许写；完整正常结束 | 实际 Read/Grep 链、来源答案与固定独立评分；客户端调用和实例相符 |
| D2 | Pi，analysis 只读 | 与 D1 同输入／模型／推理 | 同 D1，并证明实际 backend=pi |
| D3 | 原引擎，implementation | 完整 clamp 案例；只改 clamp.py；独立边界测试 | 实际变更、恢复清除、checkpoint 和 verified_complete；冻结评分通过 |
| D4 | Pi，implementation | 与 D3 相同；另核对 Pi 工作区检查 | 同 D3，并检查 review state hash 和实际 backend=pi |
| D5 | 原引擎，部分成果核验后续作 | 先授权一个固定早停：观察到首个有效文件变更后明确 cancel；若没有部分变更则记录场景未满足，不机械重跑。核验已做项后，明确手动恢复剩余事项 | 保留原 Run；verified_partial、准确 remaining_items、新 Run 与同一组；取消链不会自行启动；最终冻结评分 |
| D6 | Pi，部分成果核验后续作 | 与 D5 相同，同时核对 Pi workspace review；不假设特定 Token 阈值一定产生部分成果 | 同 D5，加当前工作区 hash 和实际 backend=pi |

取消、引擎中断、steering、App 退出、开关／项目禁用、恢复阻塞与重复幂等先由本地确定性测试覆盖。真实场景只声明实际经过的环境，不能把一种客户端结果推广给另一种客户端。

## 报告口径

端到端完成率分母为全部已开始试次；有效委托／接手／未派工分母为预定 B 或 C 试次。首轮通过分母为确有外部请求的试次，计入首轮通过需其单 Run 成果经独立评分且未由主代理接手。Run 引用必须与真实 tasks/group_runs/workspace/backend 记录匹配；仅有人工字符串不能算外部执行。

主模型规划、查询、验收、返工和接手均在绑定的主聊天及相关子聊天计数内，不能再加一次。外部执行、摘要、重试和续作分别入账再加总；失败消耗不删除。报告同时呈现成功试次成本和“所有已开始试次总成本／验收成功数”；后者包含失败。准备成本仅计一次，提供原始不摊销视图和按实际开始试次数平均分配的独立视图，不能把两视图再次相加。

所有金额使用 Decimal。净参考估算区间为 `[直接下界－委托上界, 直接上界－委托下界]`，负数原样保留，跨零标记方向不确定。外部模型、供应商修订或推理配置未知时不生成完整收益估算；未知 cache_write／价格使费用未知，未知 cache hit 可保留计价区间。实际账单字段没有账单来源时为 null。报告给中位数、范围和原始分布；五案例小样本不提供普遍节省率或显著性结论。

## 已执行的本地验证与证据

31 个 `tests/test_evaluation.py` 合同测试覆盖冻结／隔离、实际评分／范围、scorer／答案 hash 失效、明确绑定和混入、fork／重复／缺失、实际日志 model/provider、真实 Run 档位、三层 Token 上限、金额上限、未知结算、无自动重放、授权预览累计值并发失效、硬终止预留门禁与新授权、负收益、缓存和订阅区分。其测试用量与授权均为隔离 fixture 数据，不是真实用户额度。额外共享供应商边界的真实本地 HTTP 回归由 `test_request_authorization.py` 验证，结果以本轮总验收记录为准。

`node tests/evaluation-ui.mjs` 已通过生产 frontend ＋真实 Python RPC：创建草稿、15 试次隔离材料、授权预览无授权／无模型请求、完整报告、busy mutation 错误不自动重放、键盘展开与 900×600／800×520 布局，无 JavaScript 错误。此记录是浏览器／RPC 测试，不冒充原生 Tauri 或真实 Codex 聊天。

可复核产物：

- [公开脱敏证据包](https://github.com/Favio8/codexrelay/releases/download/v0.4.0/CodexRelay_0.4.0_evidence.zip) 中的 `evaluation-deterministic-report.json`：明确 `deterministic_local_fixture`，含失败、未派工、主代理接手、负估算差、缺价和缺计量。合成计数仅验证汇总机制，真实模型请求为 0。
- 同包 `evaluation-ui.json`：生产前端及真实 Python RPC 验证记录；私有路径已泛化，截图不随公开证据导出。
- 同包 `prepared-45-manifest.json`、`prepared-45-instructions.json`：45 个试次的 prepared-only 清单和指令，authorization=null，requests=0。公开路径是占位标识，不能直接运行；请用本地工具重新准备自己的隔离目录并冻结真实设置。公开副本哈希和原始本地哈希分开记录。
- `tests/run_evaluation_fixture.py`：可在新的 `.tmp/` 目录重建上述合成报告及未授权材料；不发模型请求。

待覆盖：真实 Desktop／CLI 新聊天、真实供应商账单／usage、真实主模型日志和相关子聊天的完整归属、六个真实客户端场景。启动它们需要对具体模型、轮次、累计限额、隔离设置和数据目录另行授权；不能沿用历史付费实验授权。
