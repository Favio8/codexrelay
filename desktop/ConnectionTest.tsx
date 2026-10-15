import { useEffect, useRef, useState, type ReactNode } from 'react';
import { LoaderCircle, PlugZap, ShieldCheck } from 'lucide-react';
import { rpc, RpcError } from './api';
import DetailsSummary from './DetailsSummary';
import { useNotifications } from './Notifications';

interface TestResult {ok:boolean;model:string;verification:'quick'|'full'|'backend';tool_roundtrip:boolean;elapsed_ms:number;backend_verified?:boolean;current_revision?:boolean}
interface Props {method:'test_provider'|'test_backend';params:Record<string,unknown>;identityKey?:string;disabled?:boolean;backendDisabled?:boolean;compact?:boolean;onChanged?:()=>void;onBusyChange?:(busy:boolean)=>void;children?:ReactNode}
export default function ConnectionTest({method,params,identityKey,disabled=false,backendDisabled=false,compact=false,onChanged,onBusyChange,children}:Props) {
 const [mode,setMode]=useState<'quick'|'full'|'backend'|null>(null),[elapsed,setElapsed]=useState(0),[error,setError]=useState(''),[details,setDetails]=useState('');
 const mounted=useRef(false),inFlight=useRef(false),started=useRef(0),{notify,dismiss}=useNotifications();
 const identity=JSON.stringify({params,identityKey});
 const latestIdentity=useRef(identity),activeProbe=useRef('');latestIdentity.current=identity;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;if(activeProbe.current)void rpc('cancel_probe',{probe_id:activeProbe.current}).catch(()=>{});};},[]);
 useEffect(()=>{setError('');setDetails('');},[identity]);
 useEffect(()=>{if(!mode)return;const timer=setInterval(()=>setElapsed(Math.floor((performance.now()-started.current)/1000)),250);return()=>clearInterval(timer);},[mode]);
 const run=async(verification:'quick'|'full'|'backend')=>{
  if(disabled||inFlight.current||(verification==='backend'&&backendDisabled))return;
  inFlight.current=true;dismiss();started.current=performance.now();setMode(verification);setElapsed(0);setError('');setDetails('');onBusyChange?.(true);
  const requestIdentity=identity,probeId=crypto.randomUUID();activeProbe.current=probeId;
  try {
   const result=await rpc<TestResult>(method,{...params,verification,probe_id:probeId});
   if(!result.ok)throw new Error('连接测试未通过');
   if(mounted.current&&latestIdentity.current===requestIdentity){
    const scope=result.current_revision===false?'配置已变化，结果已作为旧版本证据保存；当前配置等待验证。':verification==='quick'?'连接正常，模型已响应；工具能力尚未验证。':result.backend_verified?'此模型与执行后端验证通过。':'模型工具协议验证通过；执行后端尚未验证。';
    notify(`${scope} ${(result.elapsed_ms/1000).toFixed(1)} 秒`);onChanged?.();
   }
  } catch(e) {
   if(mounted.current&&latestIdentity.current===requestIdentity){setError(e instanceof Error?e.message:String(e));setDetails(e instanceof RpcError?e.details:'');notify('测试未完成，请查看测试位置的原因并重试。','error');}
  } finally {
   activeProbe.current='';inFlight.current=false;if(mounted.current){setMode(null);onBusyChange?.(false);}
  }
 };
 return <div className={'connection-test'+(compact?' compact':'')}>
  <div className="test-buttons">
   <button className={compact?'icon-btn':''} aria-label="测试连接" title="快速连接测试：一次真实模型请求" disabled={disabled||!!mode} onClick={()=>void run('quick')}>
    {mode==='quick'?<LoaderCircle size={16} className="spin"/>:<PlugZap size={16}/>}{!compact&&(mode==='quick'?'正在测试…':'测试连接')}
   </button>
   <button className={compact?'icon-btn':'text-btn'} aria-label="完整工具验证" title="模型工具协议验证：合成工具往返，最多 45 秒，不验证执行后端" disabled={disabled||!!mode} onClick={()=>void run('full')}>
    {mode==='full'?<LoaderCircle size={16} className="spin"/>:<ShieldCheck size={16}/>}{!compact&&(mode==='full'?'正在验证…':'完整工具验证')}
   </button>
   {method==='test_backend'&&<button className="text-btn" aria-label="执行后端验证" title={backendDisabled?'所选执行后端未安装，仍可测试模型连接和工具协议':'所选执行后端实际读取隔离文件中的随机标记，最多 45 秒'} disabled={disabled||backendDisabled||!!mode} onClick={()=>void run('backend')}>
    {mode==='backend'?<LoaderCircle size={16} className="spin"/>:<ShieldCheck size={16}/>}{mode==='backend'?'正在验证…':'执行后端验证'}
   </button>}
   {children}
  </div>
  <div className="test-progress">{mode&&<p role="status">{mode==='quick'?'正在测试连接':mode==='backend'?'正在验证后端文件读取':'正在验证工具调用'}{elapsed>=3?` · 已等待 ${elapsed} 秒`:'…'}</p>}</div>
  {error&&<div role="alert" className="alert error test-error"><div>{error}{details&&<details className="error-details"><DetailsSummary>查看详情</DetailsSummary><p>{details}</p></details>}<button className="text-btn" disabled={disabled||!!mode} onClick={()=>void run('quick')}>重试连接测试</button></div></div>}
 </div>;
}
