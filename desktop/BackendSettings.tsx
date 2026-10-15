import { useEffect, useState } from 'react';
import { rpc, RpcError, type Snapshot } from './api';
import ConnectionTest from './ConnectionTest';
import ProbeEvidence from './ProbeEvidence';
import DetailsSummary from './DetailsSummary';
import { useNotifications } from './Notifications';

interface Backend {id:string;name:string;version:string;installed:boolean;capabilities:Record<string,unknown>}
export default function BackendSettings({snapshot,onChanged}:{snapshot:Snapshot;onChanged:()=>void}) {
 const [backends,setBackends]=useState<Backend[]>([]),[backend,setBackend]=useState(snapshot.active_backend||'deepseek');
 const [provider,setProvider]=useState(snapshot.active_provider_id||''),[slot,setSlot]=useState('flash');
 const [busy,setBusy]=useState(false),[testing,setTesting]=useState(false),[error,setError]=useState(''),[details,setDetails]=useState('');
 const {notify}=useNotifications(),locked=busy||testing;
 useEffect(()=>{let disposed=false;void rpc<Backend[]>('backend_catalog').then(value=>{if(!disposed)setBackends(value);}).catch(e=>{if(!disposed){setError(e instanceof Error?e.message:String(e));setDetails(e instanceof RpcError?e.details:'');}});return()=>{disposed=true;};},[]);
 const apply=async()=>{setBusy(true);setError('');setDetails('');try{
  await rpc('activate_execution',{backend,provider_id:provider});
  notify('设置已应用，新任务使用此组合。');onChanged();
 }catch(e){setError(e instanceof Error?e.message:String(e));setDetails(e instanceof RpcError?e.details:'');}finally{setBusy(false);}};
 const available=!!provider&&backends.some(item=>item.id===backend&&item.installed);
 return <section className="panel backend-settings"><div className="section-heading"><div><h2>执行后端</h2><p className="subtle">当前组合：{snapshot.active_backend==='pi'?'Pi Agent SDK':'现有执行引擎'} · {snapshot.providers.find(p=>p.id===snapshot.active_provider_id)?.name||'未选择供应商'}</p></div></div>
  <div className="form-grid"><label className="field">执行引擎<select aria-label="执行引擎" disabled={locked} value={backend} onChange={e=>setBackend(e.target.value)}>{backends.map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.name}{item.installed?'':'（运行时未安装）'}</option>)}</select></label>
  <label className="field">模型供应商<select aria-label="后端模型供应商" disabled={locked} value={provider} onChange={e=>setProvider(e.target.value)}><option value="">请选择</option>{snapshot.providers.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label></div>
  <div className="backend-actions"><button className="primary" disabled={locked||!available} onClick={()=>void apply()}>{busy?'正在应用…':'应用设置'}</button><div className="backend-test"><label className="field">测试模型<select aria-label="测试模型" disabled={locked} value={slot} onChange={e=>setSlot(e.target.value)}><option value="flash">普通模型</option><option value="pro">增强模型</option></select></label><ConnectionTest method="test_backend" params={{backend,provider_id:provider,slot}} identityKey={snapshot.providers.find(p=>p.id===provider)?.revision} disabled={busy||!available} onBusyChange={setTesting} onChanged={onChanged}/></div></div>
  <ProbeEvidence provider={snapshot.providers.find(p=>p.id===provider)} slot={slot} backend={backend} backendVersion={backends.find(item=>item.id===backend)?.version}/>
  {error&&<div role="alert" className="alert error"><div className="alert-message">{error}{details&&<details className="error-details"><DetailsSummary>查看详情</DetailsSummary><p>{details}</p></details>}</div><button aria-label="关闭设置错误提示" onClick={()=>setError('')}>关闭</button></div>}
  <p className="subtle backend-hint">快速测试检查模型响应；完整工具验证检查模型工具协议；执行后端验证实际读取隔离文件。各项独立记录，均消耗少量额度。</p>
  <p className="subtle backend-hint">切换影响后续新轮次；当前轮保持原配置。</p>
  <details className="advanced-help"><DetailsSummary>高级说明</DetailsSummary><div>
   <p>运行中的任务保持原后端。两种引擎均使用受控工具、任务预算和修改日志；Pi 的命令修改另保留工作区检查事项。</p>
   <p>命令以当前用户权限执行，这些控制不是操作系统沙箱。</p>
   <p>能力来源：{backends.find(item=>item.id===backend)?.capabilities?.basis==='verified_by_test'?'已由本机回归验证':'未验证'}。能力缺失的任务会在调用模型前被拒绝。</p>
   <p>Codex 使用 start_agent / delegate_to_agent 跟随所选后端；旧 DeepSeek 工具始终使用原引擎。升级后如未发现新增工具，请更新托管规则并重启 Codex。</p>
  </div></details>
 </section>;
}
