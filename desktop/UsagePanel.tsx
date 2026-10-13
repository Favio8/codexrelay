import { useEffect, useRef, useState } from 'react';
import { isTauri } from '@tauri-apps/api/core';
import { ArrowUpRight, Coins, RefreshCw, X } from 'lucide-react';
import { rpc, type MoneyRange, type Snapshot, type UsagePrices, type UsageToday } from './api';
import './usage.css';
import { UsageActivity } from './UsageSources';

let cached: { key:string; at:number; value:UsageToday } | undefined;
const pending = new Map<string,Promise<UsageToday>>();
function cacheKey(snapshot:Snapshot) {
 const date = new Date();
 return [snapshot.usage_source, snapshot.usage_revision, date.toDateString(), date.getTimezoneOffset()].join('|');
}
async function queryUsage(key:string) {
 if (cached?.key === key && Date.now() - cached.at < 60000) return cached.value;
 let request = pending.get(key);
 if (!request) {
  request = rpc<UsageToday>('usage_today').then(value => {
   cached = {key, value, at:Date.now()}; return value;
  }).finally(() => pending.delete(key));
  pending.set(key, request);
 }
 return request;
}
const money = (value:string) => Number(value).toLocaleString('zh-CN', {style:'currency',currency:'CNY',minimumFractionDigits:2,maximumFractionDigits:6});
export const moneyRange = (value:MoneyRange|null) => value ? Number(value.low) === Number(value.high) ? money(value.low) : `${money(value.low)} ～ ${money(value.high)}` : '待配置价格';
const statusName:Record<string,string> = {completed:'执行完成',failed:'失败',cancelled:'已停止',interrupted:'执行中断'};
const coverageName:Record<string,string> = {reported:'已报告',partial:'部分用量',legacy:'历史覆盖未知',missing:'用量未返回'};

export default function UsagePanel({snapshot, ready, onTask, onChanged, settings=false}:{snapshot:Snapshot;ready:boolean;onTask:(id:string)=>void;onChanged:()=>void;settings?:boolean}) {
 const [usage,setUsage] = useState<UsageToday|null>(null), [error,setError] = useState(''), [loading,setLoading] = useState(false);
 const [view,setView] = useState<'details'|'prices'|null>(null), [refresh,setRefresh] = useState(0);
 const [prices,setPrices] = useState<UsagePrices>({models:{},reference:null});
 const modal = useRef<HTMLElement>(null);
 useEffect(()=>{if(modal.current)modal.current.scrollTop=0;},[view]);
 const forceRefresh = () => { cached = undefined; setRefresh(v=>v+1); };
 const key = cacheKey(snapshot);
 useEffect(()=>{
  if (!ready || !isTauri()) return;
  let disposed = false;
  const load = async () => {
   setLoading(true);
   try { const value=await queryUsage(cacheKey(snapshot)); if(!disposed){setUsage(value);setError('');} }
   catch(e){if(!disposed)setError(e instanceof Error?e.message:String(e));}
   finally{if(!disposed)setLoading(false);}
  };
  // Statistics have their own cadence; snapshot's two-second updates do not scan tasks.
  void load();
  const timer=window.setInterval(()=>void load(),60000);
  const focus=()=>void load();
  const visibility=()=>{if(document.visibilityState==='visible')void load();};
  window.addEventListener('focus',focus); document.addEventListener('visibilitychange',visibility);
  const midnight=new Date();midnight.setHours(24,0,0,50);
  const dayTimer=window.setTimeout(forceRefresh,midnight.getTime()-Date.now());
  return()=>{disposed=true;clearInterval(timer);clearTimeout(dayTimer);window.removeEventListener('focus',focus);document.removeEventListener('visibilitychange',visibility);};
 },[key,ready,refresh]);
 useEffect(()=>{
  if(view!=='prices')return;
  let disposed=false;
  void rpc<UsagePrices>('get_usage_prices').then(value=>{if(!disposed)setPrices(value);}).catch(e=>{if(!disposed)setError(String(e));});
  return()=>{disposed=true;};
 },[view]);
 useEffect(()=>{
  if(!view)return;
  const close=(event:KeyboardEvent)=>{if(event.key==='Escape')setView(null);};
  window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);
 },[view]);
 const incomplete = usage && ['partial','legacy','missing','invalid_records','undated_records'].some(k=>usage.coverage[k]>0);
 const costLabel = usage?.cost ? `${usage.unpriced_tasks || incomplete ? '已报告部分估算' : '费用估算'} ${moneyRange(usage.cost)}` : '待配置价格';
 return <>
  {settings ? <section className="panel"><div className="section-heading"><div><h2>用量与价格</h2><p className="subtle">按实际模型填写人民币单价，新任务保存价格快照。</p></div><Coins size={22}/></div><button disabled={!ready} onClick={()=>setView('prices')}>设置模型价格</button></section> :
   <button className="stat usage-stat" aria-label="查看今日委托用量" disabled={!ready} onClick={()=>setView('details')}>
    <span>今日委托用量 <Coins size={16}/></span>
    <strong>{usage?.tokens ? usage.tokens.total.toLocaleString() : usage ? usage.counts.settled ? '用量未返回' : '暂无已结算任务' : loading ? '正在统计…' : '—'}</strong>
    <small>{usage?.tokens ? costLabel : '本机全部项目 · 按任务结束日'}</small>
    <small>{error ? '刷新失败 · 点击查看' : usage ? `${usage.counts.settled||0} 个已结算任务${incomplete?' · 数据不完整':''}` : '等待本地数据'}</small>
   </button>}
  {view&&<div className="modal-shade" onMouseDown={e=>{if(e.target===e.currentTarget)setView(null);}}><section ref={modal} className="modal usage-modal" role="dialog" aria-modal="true" aria-label={view==='prices'?'模型价格':'今日委托用量'}>
   <div className="section-heading"><h2>{view==='prices'?'模型价格':'今日委托用量'}</h2><button className="icon-btn" aria-label="关闭用量详情" onClick={()=>setView(null)}><X size={20}/></button></div>
   {error&&<p role="alert" className="alert error">{error}</p>}
   <div className="usage-toolbar"><button onClick={()=>setView(view==='prices'?'details':'prices')}>{view==='prices'?'返回用量明细':'设置模型价格'}</button><button aria-label="刷新用量" disabled={loading} onClick={forceRefresh}><RefreshCw size={14} className={loading?'spin':''}/>刷新</button></div>
   {view==='prices'?<PriceEditor snapshot={snapshot} usage={usage} prices={prices} onSaved={value=>{setPrices(value);forceRefresh();onChanged();}}/>:usage?<>
    <p className="subtle">{usage.date} · {usage.timezone}（UTC{usage.utc_offset}）<br/>本机全部项目 · 按任务结束日汇总；跨日任务全部计入结束当天。</p>
    <div className="usage-summary"><div><span>供应商已报告用量</span><strong>{usage.tokens?.total.toLocaleString()??'未返回'}</strong><small>输入 {usage.tokens?.prompt.toLocaleString()??'—'} / 输出 {usage.tokens?.completion.toLocaleString()??'—'}</small></div><div><span>{usage.unpriced_tasks || incomplete ? '已报告部分费用估算':'费用估算'}</span><strong>{moneyRange(usage.cost)}</strong><small>{usage.priced_tasks} 个已计价 · {usage.unpriced_tasks} 个已知用量待计价</small></div></div>
    <h3>任务结果与数据覆盖</h3><dl className="usage-counts"><dt>执行完成 / 复验通过</dt><dd>{usage.counts.completed||0} / {usage.counts.verified||0}</dd><dt>失败 / 停止 / 中断</dt><dd>{usage.counts.failed||0} / {usage.counts.cancelled||0} / {usage.counts.interrupted||0}</dd><dt>运行中，尚未结算</dt><dd>{usage.counts.unsettled||0}</dd><dt>部分 / 历史覆盖未知</dt><dd>{usage.coverage.partial||0} / {usage.coverage.legacy||0}</dd><dt>未返回用量</dt><dd>{usage.coverage.missing||0}</dd></dl>
    {incomplete&&<p className="usage-note">仅累计已取得的数据，失败及停止任务的已知消耗也包含在内。缺失部分不按零计算，本地预算估算不参与计费。</p>}
    {!!(usage.coverage.invalid_records||usage.coverage.undated_records||usage.coverage.approximate_date)&&<p className="usage-note">无法解析记录 {usage.coverage.invalid_records||0}；历史无法归日 {usage.coverage.undated_records||0}；今日中断按发现时间归属 {usage.coverage.approximate_date||0}。</p>}
    <h3>等量 Token 价格差（估算）</h3><p className="usage-comparison">{usage.comparison?moneyRange(usage.comparison):'设置委托价格与参考模型价格后显示'}</p>
    {usage.reference&&<p className="subtle">参考：{usage.reference.label||'自定义参考模型'} · 输入 {usage.reference.input} / 缓存 {usage.reference.cached_input??usage.reference.input} / 输出 {usage.reference.output} 元／百万 Token</p>}
    <p className="usage-note">用同样的输入、输出数量换算参考模型价格，减去已计价委托费用。负值表示参考模型更便宜。缓存未知时显示区间；不包含主代理监工和返工，不代表实际省下的钱。</p>
    <UsageActivity revision={snapshot.usage_revision}/><h3>最近结算任务 <span className="subtle">最多显示 50 条，汇总包含全部</span></h3>
    <div className="usage-tasks">{usage.recent.map(task=><button key={task.id} onClick={()=>{setView(null);onTask(task.id);}}><span><strong>{task.provider_name||'历史供应商'} · {task.model}</strong><small>{statusName[task.status]} · {coverageName[task.coverage]} · {task.tokens?.total.toLocaleString()??'—'} tokens</small><small>{task.price?`${task.price.source==='backfill'?'回填估算':'任务价格快照'} · 输入 ${task.price.input} / 缓存 ${task.price.cached_input??task.price.input} / 输出 ${task.price.output}`:'未配置任务价格'}</small></span><span>{task.cost?moneyRange(task.cost):'—'} <ArrowUpRight size={13}/></span></button>)}</div>
    <p className="subtle">仅覆盖保留的委托记录；不含连接测试或供应商账户的其他消费。{usage.retained_since?`最早保留任务：${new Date(usage.retained_since*1000).toLocaleDateString()}`:''}<br/>最近更新：{new Date(usage.generated_at*1000).toLocaleString()}；金额为人民币估算，并非账单。</p>
   </>:<p className="subtle">{loading?'正在读取任务用量…':'暂无可用数据，请刷新重试。'}</p>}
  </section></div>}
 </>;
}

function PriceEditor({snapshot,usage,prices,onSaved}:{snapshot:Snapshot;usage:UsageToday|null;prices:UsagePrices;onSaved:(value:UsagePrices)=>void}) {
 const options=new Map<string,string>();
 for(const provider of snapshot.providers)for(const model of new Set([provider.flash_model,provider.pro_model]))options.set(JSON.stringify([provider.id,provider.revision??null,model]),`${provider.name} · ${model}（当前配置）`);
 for(const group of usage?.groups??[])if(!options.has(JSON.stringify([group.provider_id,group.provider_revision,group.model])))options.set(JSON.stringify([group.provider_id,group.provider_revision,group.model]),`${group.provider_name} · ${group.model}（历史配置）`);
 const [target,setTarget]=useState(()=>options.keys().next().value??'reference');
 const [input,setInput]=useState(''),[output,setOutput]=useState(''),[cachedInput,setCachedInput]=useState(''),[cacheWrite,setCacheWrite]=useState(''),[label,setLabel]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const identity=target==='reference'?null:JSON.parse(target) as [string,string|null,string];
 // Python's JSON key includes spaces; compare the identity, not serializer whitespace.
 const savedPrice=target==='reference'?prices.reference:Object.entries(prices.models).find(([key])=>JSON.stringify(JSON.parse(key))===target)?.[1];
 useEffect(()=>{setInput(savedPrice?.input??'');setOutput(savedPrice?.output??'');setCachedInput(savedPrice?.cached_input??'');setCacheWrite(savedPrice?.cache_write??'');setLabel(savedPrice?.label??'');},[target,savedPrice]);
 const group=usage?.groups.find(g=>JSON.stringify([g.provider_id,g.provider_revision,g.model])===target);
 const perform=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 return <form onSubmit={event=>{event.preventDefault();void perform(async()=>{
  const result=await rpc<UsagePrices>('save_usage_price',{price:{input,output,cached_input:cachedInput||null,cache_write:cacheWrite||null,label,currency:'CNY'},...(identity?{provider_id:identity[0],provider_revision:identity[1],model:identity[2]}:{reference:true})});
  onSaved(result);setMessage(identity?'已保存，新任务使用此价格；历史任务保持原值。':'已保存参考价格。');
 });}}>
  <label className="field">计价模型<select value={target} onChange={event=>setTarget(event.target.value)}>{[...options].map(([key,name])=><option value={key} key={key}>{name}</option>)}<option value="reference">参考模型（用于等量价格对比）</option></select></label>
  {target==='reference'&&<label className="field">参考模型名称<input value={label} onChange={event=>setLabel(event.target.value)} maxLength={100}/></label>}
  <p className="subtle">人民币／百万 Token。缓存输入留空表示明确采用统一输入单价。请填写你的实际计费价格，零价格有效。</p>
  <div className="form-grid"><label className="field">输入单价<input required type="number" min="0" max="1000000" step="any" value={input} onChange={e=>setInput(e.target.value)}/></label><label className="field">输出单价<input required type="number" min="0" max="1000000" step="any" value={output} onChange={e=>setOutput(e.target.value)}/></label></div>
  <label className="field">缓存命中输入单价（可选）<input type="number" min="0" max="1000000" step="any" value={cachedInput} onChange={e=>setCachedInput(e.target.value)}/><small>当前任务未记录缓存拆分；配置差价后按缓存占比未知给出费用区间。</small></label>
  <label className="field">缓存写入输入单价（可选）<input type="number" min="0" max="1000000" step="any" value={cacheWrite} onChange={e=>setCacheWrite(e.target.value)}/><small>留空表示该模型不计缓存写入费；供应商返回缓存写入且此处留空时，这些任务只显示已报告用量，不给出费用估算。</small></label>
  {!!usage?.coverage?.cache_write_unpriced&&<p className="subtle">今日有 {usage.coverage.cache_write_unpriced} 个任务返回了缓存写入 token，但未配置缓存写入单价，因此没有给出费用估算。</p>}
  {error&&<p role="alert" className="alert error">{error}</p>}{message&&<p role="status" className="alert success">{message}</p>}
  <div className="card-actions"><button className="primary" disabled={busy} type="submit">保存价格</button>{target==='reference'&&prices.reference&&<button type="button" disabled={busy} onClick={()=>void perform(async()=>{onSaved(await rpc('save_usage_price',{reference:true,price:null}));setMessage('已关闭价格对比。');})}>关闭价格对比</button>}</div>
  {identity&&!!group?.unpriced&&<div className="usage-backfill"><p className="subtle">此配置今日有 {group.unpriced} 个已知用量任务尚未计价。保存价格不会自动改变它们；可以明确应用已保存的价格，结果标为“回填估算”。</p><button type="button" disabled={busy||!savedPrice} onClick={()=>void perform(async()=>{
   const result=await rpc<{updated:number}>('backfill_usage_price',{date:usage!.date,provider_id:identity[0],provider_revision:identity[1],model:identity[2]});
   onSaved(prices);setMessage(`已为 ${result.updated} 个任务应用已保存价格。`);
  })}>为今日 {group.unpriced} 个未计价任务应用已保存价格</button></div>}
 </form>;
}
