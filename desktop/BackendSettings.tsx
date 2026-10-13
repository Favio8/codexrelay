import { useEffect, useState } from 'react';
import { rpc, RpcError, type Snapshot } from './api';

interface Backend {id:string;name:string;version:string;installed:boolean;capabilities:Record<string,unknown>}
export default function BackendSettings({snapshot,onChanged}:{snapshot:Snapshot;onChanged:()=>void}) {
 const [backends,setBackends]=useState<Backend[]>([]),[backend,setBackend]=useState(snapshot.active_backend||'deepseek');
 const [provider,setProvider]=useState(snapshot.active_provider_id||''),[slot,setSlot]=useState('flash');
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(''),[details,setDetails]=useState('');
 useEffect(()=>{let disposed=false;void rpc<Backend[]>('backend_catalog').then(value=>{if(!disposed)setBackends(value);}).catch(e=>{if(!disposed){setError(e instanceof Error?e.message:String(e));setDetails(e instanceof RpcError?e.details:'');}});return()=>{disposed=true;};},[]);
 const action=async(method:string)=>{setBusy(true);setError('');setDetails('');setMessage('');try{
  await rpc(method,{backend,provider_id:provider,...(method==='test_backend'?{slot}:{})});
  onChanged();setMessage(method==='test_backend'?'连接测试通过。':'设置已应用，新任务使用此组合。');
 }catch(e){setError(e instanceof Error?e.message:String(e));setDetails(e instanceof RpcError?e.details:'');}finally{setBusy(false);}};
 return <section className="panel backend-settings"><div className="section-heading"><div><h2>执行后端</h2><p className="subtle">当前组合：{snapshot.active_backend==='pi'?'Pi Agent SDK':'现有执行引擎'} · {snapshot.providers.find(p=>p.id===snapshot.active_provider_id)?.name||'未选择供应商'}</p></div></div>
  <div className="form-grid"><label className="field">执行引擎<select aria-label="执行引擎" disabled={busy} value={backend} onChange={e=>setBackend(e.target.value)}>{backends.map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.name}{item.installed?'':'（运行时未安装）'}</option>)}</select></label>
  <label className="field">模型供应商<select aria-label="后端模型供应商" disabled={busy} value={provider} onChange={e=>setProvider(e.target.value)}><option value="">请选择</option>{snapshot.providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label></div>
  <div className="backend-actions"><button className="primary" disabled={busy||!provider||!backends.some(item=>item.id===backend&&item.installed)} onClick={()=>void action('activate_execution')}>应用设置</button><div className="backend-test"><label className="field">测试模型<select aria-label="测试模型" disabled={busy} value={slot} onChange={e=>setSlot(e.target.value)}><option value="flash">普通模型</option><option value="pro">增强模型</option></select></label><button disabled={busy||!provider||!backends.some(item=>item.id===backend&&item.installed)} onClick={()=>void action('test_backend')}>测试连接</button><small className="subtle">消耗少量额度</small></div></div>
  {error&&<div role="alert" className="alert error"><div>{error}{details&&<details className="error-details"><summary>查看详情</summary><p>{details}</p></details>}</div></div>}{message&&<p role="status" className="alert success">{message}</p>}
  <p className="subtle backend-hint">切换仅影响新任务。</p>
  <details className="advanced-help"><summary>高级说明</summary><div>
   <p>运行中的任务保持原后端。两种引擎均使用受控工具、任务预算和修改日志；Pi 的命令修改另保留工作区检查事项。</p>
   <p>命令以当前用户权限执行，这些控制不是操作系统沙箱。</p>
   <p>能力来源：{backends.find(item=>item.id===backend)?.capabilities?.basis==='verified_by_test'?'已由本机回归验证':'未验证'}。能力缺失的任务会在调用模型前被拒绝。</p>
   <p>Codex 使用 start_agent / delegate_to_agent 跟随所选后端；旧 DeepSeek 工具始终使用原引擎。升级后如未发现新增工具，请更新托管规则并重启 Codex。</p>
  </div></details>
 </section>;
}
