import { useEffect, useState } from 'react';
import { confirm } from '@tauri-apps/plugin-dialog';
import { rpc, type Task } from './api';

const states:Record<string,string>={executing:'正在执行',awaiting_verification:'等待 Codex 核验',ready_to_continue:'已核验部分成果，可续作',awaiting_authorization:'等待追加授权',completed:'已验收完成',completed_by_parent:'Codex 已接手完成',cancelled:'任务组已取消',needs_parent_decision:'等待主代理判断',ready:'等待首轮',delegation_disabled:'新委派已关闭'};
const sources:Record<string,string>={provider_default:'供应商默认',codex_override:'Codex 指定',user_task_authorization:'用户单任务授权'};
const kinds:Record<string,string>={implementation:'编程',testing:'测试',review:'审查',analysis:'分析',documentation:'文档'};
const verdicts:Record<string,string>={verified_complete:'全部目标已验收',verified_partial:'部分成果已核验',rejected:'核验未通过',needs_more_evidence:'需要补充证据'};
export default function TaskGroupDetail({task,onChanged}:{task:Task;onChanged:()=>void}) {
 const [group,setGroup]=useState<any>(null),[checkpoint,setCheckpoint]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[budget,setBudget]=useState(task.token_budget??1000000);
 const reload=async()=>{try{setGroup(await rpc('get_task_group',{group_id:task.group_id}));}catch(e){setError(String(e));}};
 useEffect(()=>{void reload();},[task.group_id,task.status,task.checkpoint?.checkpoint_id,task.verification?.status,task.verification?.evidence]);
 useEffect(()=>{if(group?.next_effective_budget!=null)setBudget(group.next_effective_budget);},[group?.next_effective_budget]);
 if(!task.group_id)return <p className="subtle">旧版记录，无续作 checkpoint。</p>;
 if(!group)return <p className="subtle">{error||'正在读取任务进度…'}</p>;
 const extend=async(add:number,next?:number)=>{
  const nextBudget=next??group.next_effective_budget;
  if(nextBudget==null){setError('请先选择后续供应商，或明确设置后续预算。');return;}
  const remaining=group.remaining_continuations;
  const increment=add*nextBudget+(next?remaining*Math.max(0,next-(group.next_effective_budget??next)):0);
  const text=`剩余续作次数：${remaining} → ${remaining+add}\n已报告消耗：${group.usage_summary.reported_tokens.toLocaleString()} Token\n本地计量：${group.usage_summary.metered_tokens.toLocaleString()} Token\n下一轮预算：${nextBudget.toLocaleString()} Token\n潜在新增额度：${increment.toLocaleString()} Token\n仅调整后续执行。授权不会开启委托或跳过 Codex 核验。`;
  if(!await confirm(text,{title:'确认当前任务授权',kind:'info'}))return;
  setBusy(true);setError('');try{await rpc('extend_task_authorization',{group_id:group.group_id,expected_version:group.version,idempotency_key:crypto.randomUUID(),add_continuations:add,next_round_budget:next??null});await reload();onChanged();}catch(e){setError(String(e));}finally{setBusy(false);}
 };
 return <section className="group-detail"><p><strong>{states[group.status]||group.status}</strong> · {kinds[group.task_kind]||group.task_kind} · 第 {group.run_ids.length} / {group.max_auto_continuations+1} 轮</p>
  <p className="subtle">范围：{group.task_contract.task_root}<br/>{group.task_contract.capability==='readonly'?'仅读取':group.task_contract.write_paths?`允许写入：${group.task_contract.write_paths.join('、')||'无文件写入'}`:'按任务合同在此目录执行'}</p>
  <p className="subtle">{group.task_contract.original_goal}</p><dl><dt>本轮预算</dt><dd>{task.token_budget?.toLocaleString()} Token · {sources[task.budget_source||'provider_default']}</dd><dt>任务组累计已报告</dt><dd>{group.usage_summary.reported_tokens.toLocaleString()} Token{group.usage_summary.has_gaps?'（存在用量缺口）':''}</dd><dt>累计费用估算</dt><dd>{group.usage_summary.cost?`¥${group.usage_summary.cost.low}–${group.usage_summary.cost.high}`:'价格或用量未知'}{group.usage_summary.unpriced_runs?` · ${group.usage_summary.unpriced_runs} 轮未计价`:''}</dd><dt>剩余续作次数</dt><dd>{group.remaining_continuations}</dd></dl>
  {group.verification_head&&<><p>核验：{verdicts[group.verification_head.disposition]||group.verification_head.disposition}</p><p className="subtle">{group.verification_head.evidence}</p>{group.verification_head.completed_items?.length>0&&<p>已核验完成：{group.verification_head.completed_items.join('；')}</p>}{group.verification_head.remaining_items?.length>0&&<p>剩余事项：{group.verification_head.remaining_items.join('；')}</p>}</>}
  <div className="card-actions">{group.latest_checkpoint_id&&<button disabled={busy} onClick={()=>void rpc('get_task_checkpoint',{checkpoint_id:group.latest_checkpoint_id}).then(setCheckpoint).catch(e=>setError(String(e)))}>查看进度</button>}{!['completed','completed_by_parent'].includes(group.status)&&<button disabled={busy} onClick={()=>void extend(1)}>追加一次执行机会</button>}</div>
  {!['completed','completed_by_parent'].includes(group.status)&&<details><summary>调整后续轮次预算</summary><label className="field">后续预算<input aria-label="后续轮次预算" type="number" min={32768} max={20000000} value={budget} onChange={e=>setBudget(Number(e.target.value))}/></label><button disabled={busy} onClick={()=>void extend(0,budget)}>确认调整后续预算</button></details>}
  {checkpoint&&<><p className="subtle">摘要是进度索引，仍需核验文件、测试与恢复记录。</p><pre className="diagnostic-output">{JSON.stringify(checkpoint.progress.summary,null,2)}</pre><details><summary>技术详情与证据</summary><pre className="diagnostic-output">{JSON.stringify(checkpoint,null,2)}</pre></details></>}
  <details><summary>轮次时间线与授权记录</summary><pre className="diagnostic-output">{JSON.stringify({runs:group.runs,authorizations:group.authorization_extensions},null,2)}</pre></details>{error&&<p role="alert" className="alert error">{error}</p>}
 </section>;
}
