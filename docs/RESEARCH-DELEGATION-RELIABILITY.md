# CodexRelay 委托可靠性与收益评测：GitHub 调研

> 本文是实施前调研快照；末尾的操作范围说明仅指调研阶段。后续 0.4.0 的实现、验证与公开交付见 [发布记录](RELEASE-0.4.0.md)。

调研整理日期：2026-10-15，Asia/Shanghai。查询跨越 10 月 14 日深夜至 15 日凌晨。本文先于本期需求稿写入；本轮范围是研究与设计。

## 1. 问题与筛选方式

围绕三个已收敛的问题调研：配置存在但关键 MCP 工具不可见；执行与父代理验收之间的交接；委托带来的总成本、质量和耗时如何测量。

使用 GitHub 专用工具搜索 codex subagent MCP external model、coding agent benchmark evaluation、cc-switch，随后读取候选仓库元数据、README、依赖文件及默认分支最近提交。额外核对 OpenAI Codex 的 MCP 配置定义，作为宿主兼容语义依据。

仓库 README 描述属于候选项目的公开说明；本文没有安装或执行候选软件，也没有验证其全部行为。设计取舍属于结合 CodexRelay 本地实现所作的判断。

## 2. 候选项目与维护快照

| 项目 | 解决的问题与架构 | 技术与依赖 | 查询时的维护快照 | 本期取舍 |
| --- | --- | --- | --- | --- |
| [PsChina/deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent) | Python stdio MCP；外部模型拥有文件、命令工具循环；父代理保留规划和验收 | Python、MCP、OpenAI 兼容 Chat Completions；依赖见锁文件 | 未归档；最近默认分支提交 2026-10-10；MIT | 延续已固定的引擎与恢复机制，不引入第二套文件执行层 |
| [PaysNatal/external-subagents-mcp](https://github.com/PaysNatal/external-subagents-mcp) | Codex 主导的只读 worker pool；角色路由、doctor、smoke、可定位报告和用量遥测 | Node >=20.3、TypeScript、MCP SDK、Zod、jsonrepair、minimatch；包版本 0.3.2 | 未归档；最近默认分支提交 2026-06-18；MIT；更新间隔较长 | 借鉴诊断、较早评估委托机会、可定位证据；其只读产品边界不能覆盖本产品编码与续作 |
| [farion1231/cc-switch](https://github.com/farion1231/cc-switch) | 桌面集中管理供应商、MCP 和提示；配置预览、备份、原子写入；也有本地路由 | Tauri 2、Rust、React 18、TypeScript、SQLite；读取的 package.json 为 4.0.12 | 未归档；最近默认分支提交 2026-10-14；MIT | 借鉴修复预览与配置归属管理；本期继续沿用 CodexRelay 的外部委托接入路径 |
| [openai/codex](https://github.com/openai/codex) | 宿主代理及 MCP 接入实现；决定工具筛选和调用语义 | Rust；MCP 配置定义有 enabled、enabled_tools、disabled_tools 等字段 | 未归档；最近默认分支提交 2026-10-14；Apache-2.0 | 作为兼容行为的主要依据；读取源码不代表已经验证每个 Desktop 构建 |
| [SWE-bench/SWE-bench](https://github.com/SWE-bench/SWE-bench) | 代码问题、补丁生成与独立测试评判分离；固定实例和结果记录 | Python >=3.10、Docker、datasets、GitPython 等；完整评测环境较重 | 未归档；查询时 pushed_at 为 2026-09-18；最近默认分支提交为 2026-09-02；MIT | 借鉴固定实例、独立评分与完整失败记录；本期设计较小的 Windows 原生任务集 |

维护日期来自 GitHub API；pushed_at 与默认分支最新提交时间是不同指标。它们只说明查询时的活动，不构成质量、成本或长期维护保证。

## 3. 固定提交与可复核来源

| 项目 | 默认分支查询快照 |
| --- | --- |
| deepseek-as-subagent | [7e54026392f85ccfc796f947faf9ffac3968a49e](https://github.com/PsChina/deepseek-as-subagent/commit/7e54026392f85ccfc796f947faf9ffac3968a49e) |
| external-subagents-mcp | [1b324861d4d1a1af34c3a636bc7d913b05575406](https://github.com/PaysNatal/external-subagents-mcp/commit/1b324861d4d1a1af34c3a636bc7d913b05575406) |
| cc-switch | [51ac86f1c59abfefabadebd05ec5a5415b8ad72c](https://github.com/farion1231/cc-switch/commit/51ac86f1c59abfefabadebd05ec5a5415b8ad72c) |
| Codex | [76ed3b484d62ccceb9fa512ff4dd139f421814c3](https://github.com/openai/codex/commit/76ed3b484d62ccceb9fa512ff4dd139f421814c3) |
| SWE-bench | [02e7a74ffd0b707aab73d203fe87bdc7c76afc8e](https://github.com/SWE-bench/SWE-bench/commit/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e) |

以下文件进行了直接读取：

- deepseek-as-subagent 的 [README](https://github.com/PsChina/deepseek-as-subagent/blob/main/README.md)，结合本地固定源码与 UPSTREAM.md。
- external-subagents-mcp 的 [README](https://github.com/PaysNatal/external-subagents-mcp/blob/main/README.md) 与 [package.json](https://github.com/PaysNatal/external-subagents-mcp/blob/main/package.json)。
- cc-switch 的 [README](https://github.com/farion1231/cc-switch/blob/main/README.md) 与[固定提交 package.json](https://github.com/farion1231/cc-switch/blob/51ac86f1c59abfefabadebd05ec5a5415b8ad72c/package.json)。
- Codex 的[固定提交 MCP 配置定义](https://github.com/openai/codex/blob/76ed3b484d62ccceb9fa512ff4dd139f421814c3/codex-rs/config/src/mcp_types.rs)。
- SWE-bench 的 [README](https://github.com/SWE-bench/SWE-bench/blob/main/README.md) 与[固定提交 pyproject.toml](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/pyproject.toml)。

main 链接代表查询时读取的默认分支文件；固定提交链接代表确实按该提交读取的文件。实施前重新检查相关变化，并固定需要复用的版本。

## 4. 对本期设计的直接影响

### 4.1 配置诊断必须解释有效工具集

Codex 的 MCP 配置定义明确：设置 enabled_tools 时仅注册该列表中的工具，随后再应用 disabled_tools。这能解释本机只暴露 11 个旧入口的现象。参考[官方配置源码](https://github.com/openai/codex/blob/76ed3b484d62ccceb9fa512ff4dd139f421814c3/codex-rs/config/src/mcp_types.rs)。

本期需要分别记录服务端声明工具、配置推算工具、客户端报告工具和实际调用证据。服务端 ping 成功无法证明客户端看到了全部工具；修复文件后也需要确认客户端重新加载。

未配置名单、空名单、显式拒绝、服务器禁用及宿主附加策略应分别处理；存在版本差异时显示未知，不能直接把不确定状态当作就绪。

### 4.2 诊断与模型探针分开

external-subagents-mcp 将 doctor 与 smoke 分开，前者检查路由及凭据准备，后者发起小请求。本期采用相同的职责划分：接入静态检查默认不调用收费模型；模型快速测试、工具验证、执行后端验证分别呈现结果。[项目说明](https://github.com/PaysNatal/external-subagents-mcp/blob/main/README.md)

### 4.3 修复以具体变更预览为单位

cc-switch 的公开说明提供配置预览、备份和原子写入思路。本期在已有 tomlkit、DPAPI 备份及并发检查上增加修复计划，而非导入其整套供应商切换或路由系统。[项目说明](https://github.com/farion1231/cc-switch/blob/main/README.md)

本产品需要补充自己的约束：用户明确拒绝的工具不默认解除；只修改本软件拥有的注册与托管规则；保留配置中的其他内容；修复失败不丢失备份与原状态。

### 4.4 产物、验收和收益分别保留证据

SWE-bench 将预测补丁交给独立评测环境，并保留运行及实例身份；它也提示同一运行 ID 的缓存可能复用旧结果。本期因此要求试次身份包含输入、产物与评判版本，禁止将旧产物的评分套给新修改。[评测说明](https://github.com/SWE-bench/SWE-bench/blob/main/README.md)

external-subagents-mcp 的公开说明明确区分供应商用量与无法可靠直接测得的 Codex 逐工具节省。本期采用任务／聊天级对照，并显式报告日志覆盖、金额估算与无法归属的记录。[用量边界](https://github.com/PaysNatal/external-subagents-mcp/blob/main/README.md)

## 5. 技术选型结论

继续使用现有 Tauri、React、Python、SQLite、Windows Credential Manager 和固定执行引擎。新增接入诊断、修复计划、客户端观测和评测记录，不增加常驻代理服务、主模型网络代理或完整第三方评测平台。

当前同类参考足以支持本期设计，技术路线不需要另起执行框架。PRD 与开发顺序见 [委托可靠性与收益评测 PRD](PRD-DELEGATION-RELIABILITY.md)。

本轮没有付费调用、候选项目安装、宿主配置修改、实现代码修改或远端发布。
