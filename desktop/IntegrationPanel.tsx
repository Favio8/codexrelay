import { useEffect, useRef, useState } from 'react';
import { confirm, open } from '@tauri-apps/plugin-dialog';
import { ShieldCheck } from 'lucide-react';
import { rpc } from './api';
import DetailsSummary from './DetailsSummary';
import { useNotifications } from './Notifications';
import './integration-panel.css';

interface Capability {id:string;state:string;missing_tools:string[];legacy_engine_only?:boolean}
interface RepairPlan {plan_id:string;conflicts:string[];changes:{target:string;operation:string;tools?:string[];before?:unknown;after?:unknown}[];added_tools:string[];removed_denials:string[];retained_denials:string[];codex_home:string;selection:Record<string,unknown>}
export interface ClientSelection {client_report_id:string;instance:string;workspace:string;client_session_id:string}
interface ClientReport {id:string;instance:string;workspace:string;client_session_id:string|null;source:string;client_kind:string;client_version:string|null;reported_at:number;current:boolean;stale_reasons:string[]}
interface IntegrationStatus {
 codex_home:string;registered:boolean;state:string;delegation_enabled:boolean;
 server_declaration:{contract_version:string;runtime_version:string;fingerprint:string;tools:{name:string}[];capabilities:{id:string}[]};
 configuration_prediction:{server_enabled:boolean|null;registration_state:string;capabilities:Capability[];unknown_policies:string[];tool_states:Record<string,{state:string;reasons:string[]}>};
 managed_rules:{state:string;version:string|null;expected_version:string};
 client_evidence:{state:string;current_chat_bound:boolean;selection_current?:boolean;client_report_capabilities:Capability[];actual_call_capabilities:Capability[];client_reports:ClientReport[];actual_calls:unknown[]};
 verification_instructions:{prompt:string};pending_recovery?:{state:string;pending?:unknown[]};
}
interface Props {codexHome?:string;onHomeChanged?:(value:string)=>void;status?:IntegrationStatus|null;onChanged?:()=>void;disabled?:boolean;selection?:ClientSelection|null;onSelectionChange?:(value:ClientSelection|null)=>void}
const names:Record<string,string>={basic_readonly:'基础只读执行',selected_backend:'选定后端执行',recovery_review:'编码后的恢复核验',independent_verification:'独立验收记录',task_groups_continuation:'任务组与续作'};
const states:Record<string,string>={configuration_needs_repair:'配置待修复',capabilities_limited:'部分能力受限',waiting_client_verification:'配置检查通过，等待 Codex 验证',partially_verified:'当前聊天部分能力有调用证据',current_chat_verified:'当前聊天已验证'};
const capabilityText=(item?:Capability)=>!item||item.state==='unknown'?'未知':item.state==='available'?(item.legacy_engine_only?'旧入口可用（原引擎）':'具备证据'):'受限';

export default function IntegrationPanel({codexHome='',onHomeChanged,status,onChanged,disabled=false,selection,onSelectionChange}:Props) {
 const [directory,setDirectory]=useState(codexHome),[data,setData]=useState<IntegrationStatus|null>(status||null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [scope,setScope]=useState('full'),[registration,setRegistration]=useState(true),[rules,setRules]=useState(true),[enableServer,setEnableServer]=useState(false),[removeDenials,setRemoveDenials]=useState<string[]>([]),[plan,setPlan]=useState<RepairPlan|null>(null),[report,setReport]=useState('');
 const mounted=useRef(true),{notify}=useNotifications();
 const [localSelection,setLocalSelection]=useState<ClientSelection|null>(null);
 const selected=selection===undefined?localSelection:selection;
 const selectClient=(value:ClientSelection|null)=>{setLocalSelection(value);onSelectionChange?.(value);};
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{setDirectory(codexHome);setPlan(null);},[codexHome]);
 useEffect(()=>{if(status)setData(status);},[status]);
 const params={codex_home:directory||undefined,...selected};
 const run=async(action:()=>Promise<void>)=>{if(busy||disabled)return;setBusy(true);setError('');try{await action();}catch(e){if(mounted.current)setError(e instanceof Error?e.message:String(e));}finally{if(mounted.current)setBusy(false);}};
 const inspect=async()=>{const result=await rpc<IntegrationStatus>('integration_status',params);if(mounted.current)setData(result);};
 useEffect(()=>{let stale=false;void rpc<IntegrationStatus>('integration_status',{codex_home:directory||undefined,...selected}).then(result=>{if(!stale)setData(result);}).catch(e=>{if(!stale)setError(e instanceof Error?e.message:String(e));});return()=>{stale=true;};},[directory,selected?.client_report_id]);
 const changeDirectory=(value:string)=>{setDirectory(value);onHomeChanged?.(value);selectClient(null);setData(null);setPlan(null);setRemoveDenials([]);setEnableServer(false);};
 const invalidate=()=>setPlan(null);
 const denied=Object.entries(data?.configuration_prediction.tool_states||{}).filter(([,value])=>value.reasons.some(reason=>['explicitly_denied','tool_disabled'].includes(reason))).map(([name])=>name);
 const preview=()=>run(async()=>{const result=await rpc<RepairPlan>('integration_repair_preview',{codex_home:directory||undefined,capability_scope:scope,update_registration:registration,update_rules:rules,enable_server:enableServer,remove_denials:removeDenials});if(mounted.current)setPlan(result);});
 const apply=()=>run(async()=>{if(!plan)return;const chosen=plan;const accepted=await confirm(`应用预览中的 ${chosen.changes.length} 项变更。\n新增工具：${chosen.added_tools.join('、')||'无'}\n解除拒绝：${chosen.removed_denials.join('、')||'无'}\n文件将先用 Windows DPAPI 备份。应用后仍需 Codex 重新连接验证。`,{title:'应用 Codex 接入修复',kind:'info'});if(!accepted)return;const result=await rpc<IntegrationStatus>('integration_apply_plan',{codex_home:directory||undefined,plan:chosen});if(mounted.current){setData(result);setPlan(null);selectClient(null);notify('配置已更新，等待客户端重新连接并重新验证。');onChanged?.();}});
 return <section className="panel integration-reliability" aria-label="Codex 接入完整性">
  <div className="section-heading"><div><h2>Codex 接入完整性</h2><p className="subtle">只读检查配置、工具名单和规则；客户端报告与实际调用分别保留。</p></div><ShieldCheck size={22}/></div>
  <label className="field">Codex 配置目录（留空自动检测）<div className="input-row"><input aria-label="Codex 配置目录" value={directory} disabled={busy||disabled} onChange={e=>changeDirectory(e.target.value)} placeholder="CODEX_HOME 或用户 .codex 目录"/><button disabled={busy||disabled} onClick={()=>void open({directory:true}).then(value=>{if(typeof value==='string')changeDirectory(value);})}>选择目录</button></div></label>
  <div className="card-actions"><button disabled={busy||disabled} onClick={()=>void run(inspect)}>只读检查接入</button><span className="subtle">{data?.delegation_enabled?'新委托已开启':'新委托已关闭'}</span></div>
  {data&&<>
   {!!data.client_evidence.client_reports.length&&<label className="field">核对的聊天<select aria-label="核对的聊天" disabled={busy||disabled} value={selected?.client_report_id||''} onChange={event=>{const item=data.client_evidence.client_reports.find(report=>report.id===event.target.value);selectClient(item?.client_session_id?{client_report_id:item.id,instance:item.instance,workspace:item.workspace,client_session_id:item.client_session_id}:null);}}><option value="">未选择聊天</option>{data.client_evidence.client_reports.map(item=><option key={item.id} value={item.id} disabled={!item.client_session_id}>{item.client_kind} · {item.client_session_id||'缺会话标识'}{item.current?'':' · 已过期'}</option>)}</select><small>{selected?(data.client_evidence.selection_current===false?'所选报告已过期或实例已断开，请重新连接并导入当前报告。':'仅核对所选聊天；导入报告与实际调用分别计证。'):'请选择要核对的聊天；重新打开 App 后需要重新选择。'}</small></label>}
   <div className="integration-status"><ShieldCheck size={19}/><div><strong>{states[data.state]||data.state}</strong><p>注册：{data.configuration_prediction.registration_state} · 托管规则：{data.managed_rules.state} · 合同 {data.server_declaration.contract_version}</p><small>{data.codex_home}</small></div></div>
   <div className="integration-evidence-scroll"><table data-testid="integration-capabilities"><thead><tr><th>能力</th><th>服务端声明</th><th>配置推算</th><th>客户端报告</th><th>实际调用</th></tr></thead><tbody>{data.server_declaration.capabilities.map(capability=>{
    const configured=data.configuration_prediction.capabilities.find(item=>item.id===capability.id),reported=data.client_evidence.client_report_capabilities.find(item=>item.id===capability.id),called=data.client_evidence.actual_call_capabilities.find(item=>item.id===capability.id);
    return <tr key={capability.id}><th>{names[capability.id]||capability.id}</th><td>已声明</td><td title={configured?.missing_tools.join('、')}>{configured?.state==='available'?(configured.legacy_engine_only?'旧入口（原引擎）':'预计可用'):capabilityText(configured)}</td><td title={reported?.missing_tools.join('、')}>{selected?capabilityText(reported):'请选择聊天'}</td><td title={called?.missing_tools.join('、')}>{!selected?'请选择聊天':called?.state==='available'?'成功调用已记录':data.client_evidence.actual_calls.length?'证据不足':'尚无证据'}</td></tr>;
   })}</tbody></table></div>
   <p className="subtle">服务声明 {data.server_declaration.tools.length} 个入口；ping、自测及配置写入均不证明当前聊天已加载全部能力。组织策略不可由本机配置推算。</p>
   {!!data.configuration_prediction.unknown_policies.length&&<p role="status" className="alert">{data.configuration_prediction.unknown_policies.join('；')}</p>}
   <details><DetailsSummary>选择修复范围并预览</DetailsSummary>
    <div className="integration-repair-options"><label className="field">启用能力<select aria-label="接入能力范围" value={scope} disabled={busy||disabled} onChange={e=>{setScope(e.target.value);invalidate();}}><option value="basic">基础只读</option><option value="full">完整合同</option></select></label>
     <label className="check-field"><input type="checkbox" checked={registration} disabled={busy||disabled} onChange={e=>{setRegistration(e.target.checked);invalidate();}}/>修复本软件注册与允许名单</label>
     <label className="check-field"><input type="checkbox" checked={rules} disabled={busy||disabled} onChange={e=>{setRules(e.target.checked);invalidate();}}/>更新托管规则</label>
     {data.configuration_prediction.server_enabled===false&&<label className="check-field"><input type="checkbox" checked={enableServer} disabled={busy||disabled} onChange={e=>{setEnableServer(e.target.checked);invalidate();}}/>明确解除服务器禁用（默认保留）</label>}
    </div>
    {!!denied.length&&<details><DetailsSummary>显式拒绝的工具（默认保留）</DetailsSummary>{denied.map(name=><label className="check-field" key={name}><input type="checkbox" aria-label={'解除拒绝 '+name} checked={removeDenials.includes(name)} disabled={busy||disabled} onChange={e=>{setRemoveDenials(e.target.checked?[...removeDenials,name]:removeDenials.filter(item=>item!==name));invalidate();}}/>解除拒绝 {name}</label>)}</details>}
    <div className="card-actions"><button disabled={busy||disabled} onClick={()=>void preview()}>预览所选修复</button></div>
    {plan&&<div className="integration-repair-preview" data-testid="integration-repair-preview"><p><strong>具体修复预览</strong> · {plan.changes.length} 项变更</p><ul>{plan.changes.map((change,index)=><li key={index}>{change.target}：{change.operation}{change.tools?.length?' · '+change.tools.join('、'):''}{change.operation==='update_owned_runtime'?` · ${String(change.before)} → ${String(change.after)}`:''}</li>)}</ul>{!!plan.retained_denials.length&&<p>保持限制：{plan.retained_denials.join('、')}</p>}{plan.conflicts.map(conflict=><p role="alert" className="alert error" key={conflict}>{conflict}</p>)}<p className="subtle">仅应用所选改动；保留其他 MCP、额外名单和用户规则。并发编辑会使预览失效。</p><button className="primary" disabled={busy||disabled||!!plan.conflicts.length||!plan.changes.length} onClick={()=>void apply()}>应用此修复预览</button></div>}
   </details>
   {!!data.pending_recovery&&<div className="alert"><p>接入修复恢复事项：{data.pending_recovery.state}。备份已保留；外部编辑不会被覆盖。</p><button disabled={busy||disabled} onClick={()=>void run(async()=>{if(!await confirm('仅恢复本次修复留下且未被外部编辑的文件；冲突将继续保留。',{title:'恢复接入修复',kind:'info'}))return;await rpc('integration_restore_repair',{codex_home:directory||undefined});await inspect();})}>检查并恢复本次变更</button></div>}
   <details><DetailsSummary>验证当前 Codex 聊天</DetailsSummary><p className="subtle">重新连接后，从实际聊天取得工具可见性与实例。人工导入保留为用户报告，再与引擎实际调用核对；App 不自动发起模型实验。</p><textarea readOnly aria-label="Codex 客户端验证指令" rows={5} value={data.verification_instructions.prompt}/><label className="field">导入客户端报告（JSON）<textarea aria-label="客户端报告 JSON" rows={5} value={report} disabled={busy||disabled} onChange={e=>setReport(e.target.value)} placeholder={'{"instance":"ping 返回值","workspace":"绝对路径","client_kind":"codex_desktop","client_session_id":"聊天标识","client_version":null,"visible_tools":["ping"]}'}/></label><button disabled={busy||disabled||!report.trim()} onClick={()=>void run(async()=>{await rpc('integration_client_report',{...JSON.parse(report),codex_home:directory||undefined});await inspect();notify('已保存用户报告；请选择核对的聊天。');})}>保存为用户报告</button>{!!data.client_evidence.client_reports.length&&<p className="subtle">保留 {data.client_evidence.client_reports.length} 条近期报告；过期原因：{data.client_evidence.client_reports.at(-1)?.stale_reasons.join('、')||'当前文件指纹一致，当前聊天仍需显式绑定'}。</p>}</details>
  </>}
  {error&&<p className="alert error" role="alert">{error}</p>}
 </section>;
}
