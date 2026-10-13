# 0.2.2 升级与单实例修复

日期：2026-10-13（Asia/Shanghai）。[GitHub v0.2.2 Windows Beta](https://github.com/Favio8/codexrelay/releases/tag/v0.2.2) 提供安装包、SHA256SUMS.txt 和许可附件。

## 行为

- 管理程序接入 Tauri 官方 single-instance 插件，重复启动（包括另一目录的副本）唤起现有窗口，第二个进程退出。MCP 执行引擎仍由 Codex 按聊天/工作区管理。
- 安装器优先恢复原安装位置，缺少厂商位置登记时读取卸载登记；首次安装默认使用 `%LOCALAPPDATA%\Programs\CodexRelay`。误装在应用数据目录的 0.2.1 迁移到 Programs，保留数据目录。
- 覆盖升级不调用完整卸载。先原子检查活动委托并临时阻止新委派，再退出已确认安装路径的旧管理进程和空闲 MCP 引擎。存在活动任务时拒绝升级，原任务和开关保持原状。
- 安装结束核对程序、执行引擎、安装登记，更新现有快捷方式与已托管的 MCP 路径，恢复原开关。供应商、项目、价格和用户规则保留。旧副本的程序和运行时移入数据目录下的升级备份，仅迁移已确认组件。
- `scripts/update-installed.ps1` 使用同一安装器，避免开发维护更新与用户安装走不同升级流程。失败不会报告升级完成。

## 验证

- 127 项单元测试通过，包括活动任务拒绝、开关恢复、混合运行时拒绝、只修改已托管 MCP、其他配置及用户规则保留。
- `tests/installer-upgrade.mjs` 使用实际 NSIS、0.2.0/0.2.1 管理程序及真实 MCP 引擎，隔离数据、登记与快捷方式。验证升级前拒绝活跃任务，空闲时退出 4 个旧进程、原目录覆盖、旧副本归档、业务状态保留、新 MCP 返回 0.2.2，以及另一副本启动后退出并显示原窗口。中文路径的测试输出已明确使用 UTF-8。
- `tests/installer-locations.mjs` 验证首次安装默认位置，以及只存在数据目录旧安装时的迁移与托管 MCP 路径更新。首次安装不会擅自创建 Codex 接入。
- Windows PowerShell preflight 验证活动任务提示返回具体原因，并保留任务。
- TypeScript/Vite、Rust release 和 NSIS x64 安装包构建通过。测试无真实模型 API 消耗。

## 安装器来源

`src-tauri/installer.nsi` 基于 [Tauri CLI 2.12.2 官方模板](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.2/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi)，原始 blob 为 `f66b011e6b8acdd3e8e4f463c185e5c347b8f18c`，按 MIT/Apache-2.0 许可复用。定制点是安装位置恢复、原目录升级、交接钩子和隔离测试路径；其余资源打包和卸载由上游模板保留。升级 Tauri CLI 时应复核模板兼容性。

安装包在 `src-tauri/target/release/bundle/nsis/CodexRelay_0.2.2_x64-setup.exe`。升级退出旧 MCP 引擎后，重新启动 Codex 加载新版工具；不会强制关闭 Codex 主程序。

本机已通过同一安装器升级到 0.2.2。旧管理窗口及 4 个旧引擎退出，数据目录中的旧程序/运行时归档；当前只有一个 0.2.2 管理实例，两个快捷方式均指向 Programs。供应商、项目、业务设置及 Codex config.toml/AGENTS.md 的升级前后哈希一致，开关保持关闭。记录位于忽略目录 `artifacts/evidence/0.2.2-installed-verification.json`。
