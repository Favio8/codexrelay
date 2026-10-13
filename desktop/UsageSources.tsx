import { useEffect, useState } from 'react';
import { rpc, type MoneyRange } from './api';

const cost=(value:MoneyRange|null)=>value?Number(value.low)===Number(value.high)?`¥${Number(value.low).toFixed(4)}`:`¥${Number(value.low).toFixed(4)} ～ ¥${Number(value.high).toFixed(4)}`:'未计价';
interface Activity {date:string;tokens:{total:number;cached_input:number}|null;counts:Record<string,number>;cost:MoneyRange|null}
export function UsageActivity({revision}:{revision?:number}) {
 const [data,setData]=useState<Activity|null>(null),[error,setError]=useState('');
 useEffect(()=>{let disposed=false;void rpc<Activity>('usage_requests_today').then(value=>{if(!disposed){setData(value);setError('');}}).catch(e=>{if(!disposed)setError(String(e));});return()=>{disposed=true;};},[revision]);
 return <section className="usage-backfill"><h3>今日请求用量</h3>{error?<p className="subtle">{error}</p>:data?<><p className="usage-comparison">{data.tokens?.total.toLocaleString()??'未返回'} tokens · {cost(data.cost)}</p><p className="subtle">按响应发生日统计，包含运行中任务已返回的请求，与上方“任务结束日”口径独立。已记录 {data.counts.responses||0} 次响应；未返回用量 {data.counts.missing||0} 次；未计价 {data.counts.unpriced||0} 次。已知缓存命中 {data.tokens?.cached_input.toLocaleString()??'—'} tokens。</p><p className="subtle">今日结束但无请求级记录 {data.counts.without_events||0} 个任务；中断或可能缺失末次用量 {data.counts.interrupted||0} 个任务。旧任务不会伪造请求时间。</p></>:<p className="subtle">正在读取请求记录…</p>}</section>;
}

interface LogConfig {enabled:boolean;directory:string;prices:Record<string,unknown>}
const coverageLabels:Record<string,string>={identity_inferred:"响应身份按记录推定",identity_unknown:"缺少响应身份",cache_unknown:"缓存用量未知",invalid:"格式异常",resets:"累计计数重置",fork_baselines:"分支基线无法归日",fork_replayed:"分支历史已跳过",skipped_cumulative:"混合格式累计记录已跳过",conflicting:"同一响应存在冲突记录"};
interface LogUsage {generated_at?:number;enabled:boolean;date?:string;tokens?:{total:number};scan?:Record<string,number|boolean>;coverage?:Record<string,number>;groups?:{provider:string;model:string;requests:number;prompt:number;completion:number;cached_input:number;cost:MoneyRange|null;unpriced:number}[]}
export default function CodexUsagePanel(){
 const [config,setConfig]=useState<LogConfig|null>(null),[directory,setDirectory]=useState(''),[data,setData]=useState<LogUsage|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const [model,setModel]=useState(''),[provider,setProvider]=useState('unknown'),[input,setInput]=useState(''),[cached,setCached]=useState(''),[output,setOutput]=useState('');
 const refresh=async()=>{setBusy(true);setError('');try{setData(await rpc<LogUsage>('codex_usage_today'));}catch(e){setError(String(e));}finally{setBusy(false);}};
 useEffect(()=>{let disposed=false;void rpc<LogConfig>('codex_usage_settings').then(value=>{if(!disposed){setConfig(value);setDirectory(value.directory);}}).catch(e=>{if(!disposed)setError(String(e));});return()=>{disposed=true;};},[]);
 useEffect(()=>{if(!config?.enabled)return;void refresh();const timer=setInterval(()=>void refresh(),60000);return()=>clearInterval(timer);},[config?.enabled,config?.directory]);
 useEffect(()=>{if(config?.enabled&&Number(data?.scan?.pending_files)>0&&Number(data?.scan?.consumed_bytes)>0){const timer=setTimeout(()=>void refresh(),500);return()=>clearTimeout(timer);}},[config?.enabled,data?.generated_at]);
 const configure=async()=>{setBusy(true);setError('');try{const value=await rpc<LogConfig>('codex_usage_configure',{enabled:!config?.enabled,directory});setConfig(value);if(!value.enabled)setData(null);}catch(e){setError(String(e));}finally{setBusy(false);}};
 return <section className="panel"><div className="section-heading"><div><h2>Codex 日志用量对照</h2><p className="subtle">默认关闭。只在你开启后扫描本机日志，不改变主模型请求路径。</p></div></div>
  <label className="field">统计用的 Codex 目录<input value={directory} onChange={e=>setDirectory(e.target.value)} disabled={config?.enabled} placeholder="包含 sessions 的 Codex 配置目录"/></label>
  <p className="subtle">扫描会经过混有消息正文的日志文件，只保存用量、模型来源、时间及解析游标，不保存、展示或上传正文。范围可能包含其他项目和内置子代理，不能直接当作委托监工成本。</p>
  <div className="card-actions"><button disabled={busy||!config} onClick={()=>void configure()}>{config?.enabled?'停止日志统计':'开启本机日志统计'}</button><button disabled={busy||!config?.enabled} onClick={()=>void refresh()}>刷新日志用量</button><button disabled={busy||config?.enabled} onClick={()=>void rpc('codex_usage_clear').then(()=>{setData(null);setMessage('派生统计缓存已清除，原日志未改变。');}).catch(e=>setError(String(e)))}>清除统计缓存</button></div>
  {error&&<p role="alert" className="alert error">{error}</p>}{message&&<p role="status" className="alert success">{message}</p>}
  {config?.enabled&&data?.enabled&&<><p className="usage-comparison">{data.date} · {data.tokens?.total?data.tokens.total.toLocaleString()+' tokens':'未找到可识别的用量记录'}</p><p className="subtle">{data.scan?.unavailable?'日志目录不可读或不存在。':`本次待继续扫描 ${data.scan?.pending_files||0} 个文件，不可读 ${data.scan?.unreadable_files||0} 个文件。`}累计格式缺口/跳过记录：{Object.entries(data.coverage||{}).filter(([key,value])=>value&& !['reported','cumulative_delta'].includes(key)).map(([key,value])=>`${coverageLabels[key]||"其他覆盖提示"}: ${value}`).join('，')||'无'}。数字仅覆盖已取得的记录。</p>
   <div className="usage-tasks">{data.groups?.map(group=><button key={JSON.stringify([group.provider,group.model])} onClick={()=>{setProvider(group.provider);setModel(group.model);setInput('');setCached('');setOutput('');}}><span><strong>{group.provider} · {group.model}</strong><small>{group.requests} 次响应 · 输入 {group.prompt} / 缓存 {group.cached_input} / 输出 {group.completion}</small></span><span>{cost(group.cost)}<small>{group.unpriced} 次未计价 · 点击设置价格</small></span></button>)}</div>
   {model&&<form onSubmit={e=>{e.preventDefault();setBusy(true);setError('');void rpc<LogConfig>('codex_usage_save_price',{provider,model,price:{input,cached_input:cached||null,output,currency:'CNY'}}).then(value=>{setConfig(value);setModel('');void refresh();}).catch(e=>setError(String(e))).finally(()=>setBusy(false));}}><h3>{provider} · {model} 的对照价格</h3><p className="subtle">人民币／百万 Token；仅重算日志费用估算，不改委托任务的价格快照。</p><div className="form-grid"><label className="field">日志输入单价<input required type="number" min="0" step="any" value={input} onChange={e=>setInput(e.target.value)}/></label><label className="field">日志输出单价<input required type="number" min="0" step="any" value={output} onChange={e=>setOutput(e.target.value)}/></label></div><label className="field">日志缓存命中单价<input type="number" min="0" step="any" value={cached} onChange={e=>setCached(e.target.value)}/></label><button className="primary" disabled={busy}>保存日志参考价格</button></form>}
  </>}
 </section>;
}
