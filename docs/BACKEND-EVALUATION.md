# 第二执行后端：DSH / Pi 初步实测

> 后续进展：已完成 Pi Agent SDK 正式适配及 0.2.0 本地构建。最终采用 SDK 调度 + CodexRelay 受控工具/模型传输，而非本页初探的完整 Pi CLI。实现及验收边界见 [0.2.0 交付记录](ROADMAP-0.2.0.md)；以下保留选型时的原始实验。

日期：2026-10-13（Asia/Shanghai）。阶段：B1 的发布包、基础协议与控制探针；尚未接入生产任务路径。

## 建议

**优先将 Pi 作为第二后端适配对象。** 当前最有价值的依据是：它已在本机完成仅读工具循环、结构化用量、steering、清队列和取消，接口与 CodexRelay 任务控制接近。DSH 的 Windows 原生运行时与 Python SDK 也可用，但 sdk-minimal 在本次请求中只暴露 pwsh，不能直接满足本产品的严格仅读工具语义。

这是工程适配优先级，不是模型质量或实际成本排名。两个后端尚未通过完整准入矩阵，不建议现在将任一项直接放进用户可选列表。

## 制品与隔离范围

| 项目 | 本次固定版本及制品 | 本机观察 |
| --- | --- | --- |
| Pi | `@earendil-works/pi-coding-agent@1.2.0`，npm，使用独立 package-lock | 使用现有 Node v24.15.0；node_modules 约 111.4 MB，不含 Node 本体 |
| DSH SDK | `deepseek-harness-sdk==0.1.5rc1`，PyPI wheel | 元数据声明 Python >=3.10、Pydantic >=2.12,<3、MIT；使用项目现有 Python 3.12 |
| DSH runtime | `deepseek-harness-runtime-bin==0.1.5rc1`，Windows x64 wheel | 压缩包 72,045,733 bytes；SDK + runtime 解包约 246.0 MB；PATH 不含系统 Node 时运行成功 |

DSH wheel 的 SHA-256 与 PyPI 元数据一致：

- SDK：`8d9897e97e8a95fd738cb2c3e2c563951d824e43847ec6f3b0fb172c0729b4d8`
- runtime：`d031a5ab4919c572fd7e48c7ef19ec8b1896e93d93c44b0937bb958c528f8acf`

上述是发布渠道实际取得的版本，不将 GitHub 默认分支的版本或行为直接套给制品。体积不是最终安装包增量，两者分发方案也不同，不能简单据此判定用户下载成本。

依赖放在项目 `.tmp/backend-probe`，未写入项目正式依赖、日常 agent 配置或系统全局安装。Pi 安装禁用 lifecycle scripts，启动禁用扩展发现、MCP、skills、上下文文件、启动联网及遥测；DSH 使用独立 home 和 sdk-minimal。模型地址均指向本机 HTTP 替身，只使用假 Key。没有做整个进程网络行为审计或干净 Windows 验收。

来源：[Pi npm 元数据](https://registry.npmjs.org/@earendil-works/pi-coding-agent/1.2.0)、[DSH SDK 元数据](https://pypi.org/pypi/deepseek-harness-sdk/0.1.5rc1/json)、[DSH runtime 元数据](https://pypi.org/pypi/deepseek-harness-runtime-bin/0.1.5rc1/json)。开源架构资料见 [Pi RPC](https://github.com/earendil-works/pi/blob/7d75a0e3eb273dff753d49c066ea29fbe7b3b6f6/packages/coding-agent/docs/rpc.md) 和 [DSH Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/python/sdk/README.md)。

## 已得到的实际结果

| 场景 | Pi 1.2.0 | DSH 0.1.5rc1 |
| --- | --- | --- |
| 启动及握手 | 成功，约 0.48 秒 | 成功，约 3.27 秒；不依赖系统 Node |
| 本地兼容 Chat Completions | 两次流式请求，完成真正的 read 工具调用 | 一次流式请求，返回 PROBE_DONE / completed |
| 读取未放进提示词的标记 | 工具读取真实 input.txt，事件含 BACKEND_PROBE_FILE | 本次未执行文件读取场景 |
| 仅读工具列表 | 请求只含 read/grep/find/ls，无 write/edit/bash/powershell | sdk-minimal 请求只含 pwsh，不满足严格 readonly |
| 用量 | 总量 220；未缓存输入 160、缓存输入 40、输出 20 | 总量 110；未缓存输入 80、缓存输入 20、输出 10，assistant/message 含 usage |
| 中途指令 | steer 返回 queued | 本次未验证 |
| 清空排队消息 | 返回实际清除的 QUEUED_PROBE | 本次未验证 |
| 在途模型请求取消 | abort 返回成功，约 0.002 秒；此场景共一次模型请求 | 本次未验证 |
| 正常退出 | stdin 关闭后退出码 0，无强杀 | SDK close 后进程已退出 |

启动和取消耗时均是单次本机观察，不是性能基准。取消场景没有运行 OS 子命令，所以不能据此宣称整棵工具进程树取消已通过。工具列表检查与成功读取不等于已完成恶意工具名/目录越界攻击测试。

两者的已报告 input 都**不包含**另列 cacheRead，适配到本项目 prompt 总输入时要加回；本次替身返回 prompt=100、cached=20，事件输入为80。不能直接把不同后端同名 input 字段相加。

## 正式适配前仍需做的工作

1. **权限与作用域**：Pi 工具白名单有现成入口，但 task_root/write_paths 的逐操作限制、扩展工具及命令环境仍需适配。DSH 需找到可读文件且无任意 shell 的受控 profile，或定制工具组成。
2. **累计预算**：当前 probe 得到运行用量，但没有实施下一次模型请求前的累计预算拦截。需证明异步事件不会在主代理检查前自动发出下一请求；仅收到 usage 后发送 abort 不等于硬预算。
3. **coding 与恢复**：固定文件修改并测试、非 Git 工作区快照、进程崩溃持久检查事项及工作区租约尚未验证。两者都不能仅凭“无 journal 返回空”解除恢复阻塞。
4. **凭据隔离**：独立 home 与假 Key 仅证明探针不会使用日常凭据；不能证明后端 shell 子进程不会继承模型 Key。生产适配要验证实际传播链。
5. **停止与失败**：确认 command 子进程树退出、取消期间排队消息不继续执行、重试/压缩用量覆盖、协议失败及断开后的接管。
6. **实际收益与分发**：真实供应商任务质量/总成本、干净 Windows 运行时交付、许可证依赖清单及升级回退，留到选定适配对象后验收。

推荐实现路径是 Python 持有 Pi 的 JSONL RPC 子进程，以现有 Relay/SQLite 作为唯一接收入口；限制发现与自动启动行为，在适配层补预算、作用域和持久恢复，再对外提供中性任务工具。暂不整体引入 Pi UI，也不直接把旧 DeepSeek 工具改路由。

## 复现与证据

脚本：`tests/probe_backends.py`。先将上述固定 DSH wheels 解压至 `.tmp/backend-probe/dsh-site` 并保存下载元数据为 `dsh-packages.json`，Pi 安装至 `.tmp/backend-probe/pi`：

```powershell
npm install --prefix .tmp/backend-probe/pi --ignore-scripts --no-audit --no-fund --save-exact @earendil-works/pi-coding-agent@1.2.0
runtime/python.exe -I -B -X utf8 tests/probe_backends.py
```

脚本不下载或调用真实模型。`artifacts/evidence/backend-probe.json` 保存本次实际请求工具列表、控制响应、用量事件、制品哈希和 Pi lock 哈希；依赖及证据目录均默认忽略。探针是评估程序，发生错误会写入结果的 error 字段，退出码本身不代表全部能力通过。
