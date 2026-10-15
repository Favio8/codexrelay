import {useEffect, useState} from 'react';
import {rpc, type Snapshot, type MoneyRange} from './api';
import {moneyRange} from './UsagePanel';

type ProbeUsage={records:number;requests:number;reported_tokens:number;missing_usage:number;unknown_price:number;unknown_requests:number;cost:MoneyRange|null;cost_complete:boolean};
export default function ProbeUsage({snapshot}:{snapshot:Snapshot}) {
 const [value,setValue]=useState<ProbeUsage|null>(null),[error,setError]=useState('');
 useEffect(()=>{
  let cancelled=false;
  void rpc<ProbeUsage>('probe_usage_today').then(result=>{if(!cancelled){setValue(result);setError('');}}).catch(e=>{if(!cancelled)setError(String(e));});
  return()=>{cancelled=true;};
 },[snapshot.usage_source,snapshot.usage_revision]);
 return <section className="panel" aria-label="连接测试用量"><h2>连接测试用量</h2>
  <p className="subtle">任务用量沿用不含探针的历史口径。这里独立统计本机今天的连接、工具与后端验证。</p>
  {value&&<><p>{value.records} 次测试 · {value.requests} 次请求 · 已报告 {value.reported_tokens.toLocaleString()} Token</p>
   <p>API 参考价格估算：{value.cost?moneyRange(value.cost):'未知'}{value.cost&&!value.cost_complete?'（已知部分）':''}；实际账单未知。</p>
   {(value.missing_usage>0||value.unknown_price>0||value.unknown_requests>0)&&<p className="subtle">缺少用量 {value.missing_usage}，缺少价格 {value.unknown_price}，请求次数未知 {value.unknown_requests}。缺口未按零费用处理。</p>}</>}
  {error&&<p role="alert">{error}</p>}
 </section>;
}
