import type { Provider } from './api';
import packageInfo from '../package.json';

interface Evidence {probe_id:string;ok:boolean;provider_revision:string;slot:string;model:string;actual_model?:string|null;verification:string;backend?:string;backend_version?:string;adapter_version?:string;reasoning_mode:string;occurred_at:number;request_count:number;elapsed_ms:number}
export default function ProbeEvidence({provider,slot,backend,backendVersion}:{provider?:Provider;slot:string;backend?:string;backendVersion?:string}) {
 const values=Object.values((provider as (Provider & {probe_evidence?:Record<string,Evidence>})|undefined)?.probe_evidence||{});
 const reasoning=slot==='pro'?(provider?.pro_reasoning_effort||provider?.reasoning_effort):provider?.reasoning_effort;
 const current=values.filter(item=>item.ok&&item.provider_revision===provider?.revision&&item.slot===slot&&item.model===(slot==='pro'?provider?.pro_model:provider?.flash_model));
 const latest=(level:string)=>current.filter(item=>item.verification===level&&(level!=='backend'||(item.backend===backend&&item.backend_version===backendVersion&&item.adapter_version===packageInfo.version&&item.reasoning_mode===reasoning))).sort((a,b)=>b.occurred_at-a.occurred_at)[0];
 const levels=[['quick','模型响应'],['full','工具协议'],...(backend?[['backend','后端读取']]:[])];
 return <details className="probe-evidence subtle"><summary>当前档位验证记录</summary><div>
  {levels.map(([level,label])=>{const record=latest(level);return <p key={level}>{label}：{record?'已验证':'待验证'}{record&&<small title={'探针 '+record.probe_id}> · 响应模型：{record.actual_model||'供应商未明确报告'} · {record.request_count} 次请求 · {(record.elapsed_ms/1000).toFixed(1)} 秒 · {new Date(record.occurred_at*1000).toLocaleString()}</small>}</p>;})}
 </div></details>;
}
