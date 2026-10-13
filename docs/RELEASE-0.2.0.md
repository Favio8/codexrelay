# 0.2.0 交付与收尾

日期：2026-10-13（Asia/Shanghai）。本机从 0.1.4 升级到 0.2.0，源码包含委托用量、可选 Codex 日志统计、Pi Agent SDK 后端及执行可靠性修复。功能范围和环境验收边界见 [ROADMAP-0.2.0.md](ROADMAP-0.2.0.md)。[GitHub v0.2.0 Windows Beta](https://github.com/Favio8/codexrelay/releases/tag/v0.2.0) 提供安装包、SHA256SUMS.txt 及第三方许可声明，README 下载链接已同步。

## 最终复查修复

- Pi 收尾记账失败时保留工作区基线；恢复查询仍显示未完成的观察证据，同时返回当前文件哈希，允许主代理检查后正常确认。确认审计保留覆盖不完整的标记。
- 后端连接测试原先只检查一次任务快照，存在与正式委托同时进入的竞态。现在原子占用同一个数据库任务位，成功或失败均释放；进程退出后可回收。测试不计入委托用量。
- 缺少输出单价在保存前拒绝，避免后续费用计算异常。非有限响应时间跳过入库，不影响任务终态保存。
- 修正只读工具白名单测试的 mock 位置，实际调用被测入口，并确认未启动执行器、未接收任务。
- 本机维护升级同步 Pi 文件及当前许可声明，使用原有备份与回退流程。

## 验证

- 全量单元测试：113 项通过，未跳过 Pi SDK 测试；10,001 条任务聚合约 0.13 秒。
- 真实 stdio MCP、预算中断和生命周期测试通过；使用确定性本地模型响应，真实执行文件工具与 Windows 进程。
- 浏览器前端与实际 Python RPC 通过；真实 Tauri/WebView2 检查 Pi 模型/文件往返、日志启停和价格、隐私、托管规则与窗口布局，应用 PATH 仅含系统目录。
- TypeScript、Vite、Rust release 与 NSIS x64 构建通过；最后重新打包完整运行时。
- 本机安装程序、65 个 Python 文件、Pi 运行时及许可文件与构建输入一致；安装后独立 stdio MCP 返回 0.2.0，共 24 个工具，验证未发起模型请求。
- 供应商、项目和业务设置哈希保持一致，总开关仍为关闭。升级应用时 Codex 配置文件保持不变；随后更新托管规则，独立确认 Codex 配置语义、用户自定义规则和业务设置保持原值。重启 Codex 后加载新接口。

本地证据保存在忽略目录 `artifacts/evidence/`：`0.2.0-final-unit.log`、`0.2.0-final-mcp.log`、`0.2.0-final-budget.log`、`0.2.0-final-lifecycle.log`、`0.2.0-final-build.log`、`0.2.0-final-package.log`、`0.2.0-installed-update.json`、`0.2.0-installed-verification.json`、`0.2.0-managed-rules-verification.json` 和 `0.2.0-release.json`。

安装包在 `src-tauri/target/release/bundle/nsis/CodexRelay_0.2.0_x64-setup.exe`，相邻 `.sha256` 文件记录最终哈希。旧安装文件备份保留在 `.tmp/app-update-backup-a4cf7b57364f4156bd8a1ac1db4e81b5/`。

干净 Windows 虚拟机安装/卸载、安装器升级失败注入，以及重启后新 Codex 聊天的付费供应商自动派工仍未验收；本次未将本地模型替身测试当成这些环境的通过证据。
