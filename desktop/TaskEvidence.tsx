import type { Task } from './api';

const verificationText: Record<string, string> = {
  pending: '待 Codex 复验', passed: '复验通过', failed: '复验未通过', not_applicable: '执行未完成',
};

export default function TaskEvidence({ task }: { task: Task }) {
  const usage = task.telemetry;
  const change = usage?.workspace_changes;
  if (!usage && !task.verification) return null;
  return <>
    <dl>
      <dt>执行后端</dt><dd>{task.backend_id === "pi" ? "Pi Agent SDK" : "现有执行引擎"}{task.backend_version ? " · " + task.backend_version : ""}</dd>
      {task.verification && <><dt>复验状态</dt><dd>{verificationText[task.verification.status] || task.verification.status}</dd></>}
      {usage && <>
        <dt>供应商已报告用量</dt><dd>{usage.tokens?.total == null ? '未返回' : usage.tokens.total.toLocaleString() + ' tokens'}</dd>
        <dt>本地计量（含估算）</dt><dd>{usage.budget?.metered_tokens?.toLocaleString() ?? '未返回'} tokens</dd>
        <dt>已记录命令</dt><dd>{usage.bash?.calls ?? 0} 次，失败 {usage.bash?.failure_count ?? 0} 次</dd>
      </>}
      {change && <><dt>观察到的文件变化</dt><dd>新增 {change.added.length} · 修改 {change.modified.length} · 删除 {change.deleted.length}</dd></>}
    </dl>
    {usage?.workspace_review&&<p className="subtle">工作区检查事项：{usage.workspace_review.review_id}。执行结束后请在 Codex 查询 get_task_recovery，核对差异与测试，再确认工作区检查；此处为执行结束时记录，后续确认状态以恢复查询为准。</p>}
    {usage?.budget?.warning && <p className="subtle">本地计量已达到任务预算的 80%。</p>}
    {usage?.tokens?.interrupted_request && <p className="subtle">中断请求未返回完整用量，上方只显示已取得的数据。</p>}
    {change && <details><summary>查看文件变化</summary><pre className="diagnostic-output">{JSON.stringify(change, null, 2)}</pre></details>}
    {task.verification?.evidence && <p className="subtle">复验依据：{task.verification.evidence}</p>}
  </>;
}
