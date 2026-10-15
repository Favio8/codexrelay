import { useEffect, useState } from 'react';
import { rpc, type Snapshot } from './api';
import DetailsSummary from './DetailsSummary';
import { useNotifications } from './Notifications';

export default function ExecutionSettings({snapshot,onChanged}:{snapshot:Snapshot;onChanged:()=>void}) {
 const defaults=snapshot.execution_defaults;
 const [form,setForm]=useState({...defaults}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const {notify}=useNotifications();
 useEffect(()=>setForm({...defaults}),[defaults?.version]);
 const save=async()=>{setBusy(true);setError('');try{const {version,...values}=form;await rpc('set_execution_defaults',values);notify('执行默认设置已保存，仅影响新任务。');onChanged();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 return <section className="panel execution-settings"><h2>执行与续作</h2><p className="subtle">仅影响新任务；调整当前任务请进入任务详情。每轮预算来自供应商配置。</p>
  <div className="execution-options"><label className="check-field"><input type="checkbox" aria-label="上下文优化" disabled={busy} checked={form.context_optimization??true} onChange={e=>setForm({...form,context_optimization:e.target.checked})}/><span>上下文优化</span></label>
  <label className="check-field"><input type="checkbox" aria-label="自动续作" disabled={busy} checked={form.auto_continue??true} onChange={e=>setForm({...form,auto_continue:e.target.checked})}/><span>核验后自动续作</span></label></div>
  <label className="field continuation-count">最多自动续作次数<input aria-label="最多自动续作次数" disabled={busy} type="number" min={0} max={10} value={form.max_auto_continuations??3} onChange={e=>setForm({...form,max_auto_continuations:Number(e.target.value)})}/><small>初始执行加续作；默认共 4 轮。Codex 每轮核验，App 不独立启动执行链。</small></label>
  <details className="execution-advanced"><DetailsSummary>高级设置</DetailsSummary><div className="form-grid">{(['warning_ratio','wrap_ratio','context_ratio'] as const).map((key,i)=><label className="field" key={key}>{['预算提醒比例','优先收尾比例','上下文压缩比例'][i]}<input aria-label={key} disabled={busy} type="number" min={.5} max={.95} step={.05} value={form[key]??[.8,.9,.8][i]} onChange={e=>setForm({...form,[key]:Number(e.target.value)})}/></label>)}<label className="field">摘要输出上限<input disabled={busy} type="number" min={256} max={4000} value={form.summary_tokens??3000} onChange={e=>setForm({...form,summary_tokens:Number(e.target.value)})}/></label></div><p className="subtle">摘要使用当前执行模型，消耗计入本轮。模型上下文容量在供应商高级设置配置；未知时显示未配置。checkpoint 保留在本机，不自动过期删除。</p></details>
  <div className="execution-footer"><button disabled={busy} className="primary" onClick={()=>void save()}>{busy?'正在保存…':'保存执行默认设置'}</button></div>
  {error&&<p role="alert" className="alert error">{error}</p>}
 </section>;
}
