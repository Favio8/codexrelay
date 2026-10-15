# A05/A06 执行可靠性修复

日期：2026-10-13。由主代理亲自实现；没有使用外部子代理，没有更改用户的总开关、供应商或 Codex 注册。

## 原因与实现

1. Windows CMD 的 `/c` 命令串使用 CMD 引号语义，不再经过 Python 参数列表的 CRT 转义。普通程序可使用 Bash 工具的 `executable`、`args`、`cwd`，按字面参数直接执行；既有 `command` 仍支持复合命令。
2. 同一输入状态下相同工具/参数/错误出现两次后，第三次执行前停止并返回 `no_progress`。文件内容重复两次循环也停止，包含A05里四个版本反复变化的情况；使用任务初始哈希和规范路径识别循环。单次试验回退、输入修复后的重试、成功的重复工作不被笼统禁止。
3. 状态独立保存 `telemetry`：供应商已返回用量、本地计量、80%预算提示、每个已返回命令的退出码、正在执行的工具、中断信息、耗时与文件变化。失败或取消仍保留这些字段；未取得供应商用量为 null，不伪报零。已有 result=null 的失败契约保持兼容。
4. 所有委托工具新增可选 `task_root`、`write_paths`、`token_budget`。任务目录必须在项目内；文件工具执行前校验目录和允许写入的具体路径；命令从任务目录启动，结束后发现范围外项目文件改动即停止并保留证据。任务预算范围为32768至供应商配置上限，冻结到该次任务，不更改全局配置。
5. 命令 HOME/USERPROFILE/TEMP/TMP 使用独立临时目录，不指向项目或真实用户HOME。经过工具子进程的命令目录由父进程持有，取消并关闭子进程后也会清理。
6. coding任务记录项目前后的观察差异，覆盖命令产生的新增、修改、删除。初次保存SHA-256，后续使用元数据检测候选变化并复核SHA-256；公开 `method`、排除目录与不可读取路径。该记录补充现有Write/Edit事务journal，不冒称命令具有原子事务或自动回滚。
7. recovery保持原有committed/uncertain字段，另提供 `current_sha256`、`exists`、`observed_state`（matches/changed/missing/unreadable）。历史哈希不同不再只有笼统的uncertain；不会未经证据推断历史事务已提交或自动确认。
8. completed表示执行结束，复验初始为pending。主代理独立核验后调用 `record_deepseek_verification(job_id, passed, evidence)` 保存结果；GUI分别显示执行状态、复验状态、已报告用量和本地计量，并显示最新错误。消息返回统一使用公开job_id，ping版本引用包版本；steering的排队与实际应用计数分开。凭据只在字符串中脱敏，不再替换序列化JSON导致数字计量损坏，未配置密钥时不会把local占位符误当真实凭据。

## 使用

对已列举的Python文件做格式化时，将任务目录设为目标目录，write_paths设为这些具体文件；使用结构化程序参数向格式化工具传入显式文件清单。MCP中的路径可以是绝对路径，或相对于task_root。

可选参数未提供时保留原工作区范围和供应商预算。权限在任务启动时冻结；后续消息不会升级readonly为coding。

## 边界

- Bash仍是受管理的可信主机命令执行器。cwd、临时HOME和写入清单不是操作系统文件沙箱；范围外项目改动是执行后检测，不保证阻止任意命令写入主机其他目录。
- 文件差异是任务期间观察到的净变化，不能证明每项变化的进程归属，也不记录中途创建后删除的文件。元数据被外部刻意恢复的同大小改动可能无法识别；明确排除VCS、Python和常见测试/格式缓存目录。
- 供应商未返回的请求、重试或中断账单不能被精确补算；本地预算不是实际收费金额。
- completed/pending分离不会自动证明模型分析正确，仍需主代理检查事实和测试。
- 已安装应用现已更新至0.1.4，管理窗口已重新启动。当前Codex会话中此前启动的MCP进程仍需重启Codex后才能加载新代码与接口。

## 调研取舍

通过GitHub专用工具核对了两个未归档的公开项目：

- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python/blob/125efa029b4bfd84238bd2c4fd69c3406f802663/docs/release.md)：参考运行用量快照与显式终止的思路；其模型/运行框架不替换当前DeepSeek兼容执行器。
- [LangGraph](https://github.com/langchain-ai/langgraph/blob/10f1fabb3b2f2d550b8133fba1217391c27694c4/libs/langgraph/langgraph/errors.py)：参考显式步数终止；本次实际新增的是工具失败和文件循环检测，保留既有总轮数限制。

采用Python标准库小模块，复用现有Config、项目租约、工具子进程与SQLite任务记录，不引入SDK或图框架依赖。此次调研没有足够的发布日期数据，不对项目更新频率作额外判断。

## 验证

执行记录以实际工具输出为准。回归包含真实Windows引号/字面参数、私有HOME及真实命令取消清理、第三次失败前停止、允许有效重试、文件循环、作用域跨工具进程传递、允许写入清单、失败/取消计量、命令文件差异、恢复哈希状态与任务预算冻结。

本地MCP测试使用确定性HTTP模型响应，但真实运行stdio、文件工具、Windows进程与恢复journal；没有付费模型调用，不将这些测试当成新的真实Codex自动派工验收。

验证命令：

```text
runtime\python.exe -I -B -X utf8 -m unittest discover -s tests -p test_*.py -v
runtime\python.exe -I -B -X utf8 tests\run_mcp.py
runtime\python.exe -I -B -X utf8 tests\run_budget_mcp.py
runtime\python.exe -I -B -X utf8 tests\run_lifecycle.py
node tests/task-evidence-ui.mjs
npm run build
```

代码修复阶段完成：55项全量单元测试通过；MCP协议、预算中断、生命周期回归全部退出0；TypeScript及生产构建退出0；UI渲染回归退出0；git diff --check通过。测试临时目录已自动清理。复核了实际变更；没有更改用户的全局开关、真实供应商配置或Codex注册，没有进行付费模型实测。此阶段仅验证项目运行时，后续安装更新记录见下节。机器可读结果见artifacts/evidence/execution-reliability-fix.json。

## 0.1.4 安装更新

用户确认后，于2026-10-13完成本机安装更新：

- 使用项目现有便携MSVC工具链执行scripts/build.ps1，TypeScript、Vite、Rust release与NSIS安装包构建均退出0。安装包为src-tauri/target/release/bundle/nsis/CodexRelay_0.1.4_x64-setup.exe。
- scripts/update-installed.ps1退出0，将19个变更文件写入%LOCALAPPDATA%\Programs\CodexRelay；替换前确认无活动任务，并备份旧文件。管理窗口已重新启动，进程对应安装目录，文件版本与注册版本均为0.1.4。
- 独立核对安装程序及codexrelay/deepseek_mcp全部60个Python文件：与新构建源码哈希一致。安装包SHA-256为37aefa75f5d8f30386bef207071449a98f3e6a2af195491982248902a3eb7d5d。
- 使用安装后的Python启动真实stdio MCP进程，隔离状态目录与工作区，仅执行工具枚举和ping：返回0.1.4，12个工具齐全，新增复验接口与四个委托工具的作用域/预算参数存在。没有提交委托、没有模型请求；该检查不是新的自动派工验收。
- 更新前后settings、providers、projects表的内容哈希一致；总开关仍为关闭。Codex的config.toml与AGENTS.md哈希一致，接入仍被识别。当前会话既有MCP进程没有被终止；重启Codex后加载更新。
- 更新验证临时目录及脚本已清理，旧安装文件备份保留在.tmp/app-update-backup-0a58f2ada8574f959a2cb9cff26eaeb9。

本次证据：artifacts/evidence/0.1.4-update-baseline.json、0.1.4-installed-update.json、0.1.4-installed-verification.json。
