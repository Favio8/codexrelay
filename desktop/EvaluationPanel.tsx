import {useState} from 'react';
import {rpc, type Snapshot} from './api';

type Trial={id:string;case_id:string;group:string;state:string;workspace:string;outcome?:string};
type Experiment={id:string;state:string;manifest_sha256:string;trials:Trial[]};
type Caps={external_tokens:number;trial_tokens:number;run_tokens:number;max_trials:number;main_tokens:number;external_cost:string|null};

export default function EvaluationPanel({snapshot}:{snapshot:Snapshot}) {
 const [experiment,setExperiment]=useState<Experiment|null>(null),[identifier,setIdentifier]=useState('');
 const [root,setRoot]=useState(''),[mainModel,setMainModel]=useState('unknown'),[mainProvider,setMainProvider]=useState('unknown'),[repetitions,setRepetitions]=useState(1);
 const [trialId,setTrialId]=useState(''),[session,setSession]=useState(''),[client,setClient]=useState('desktop');
 const [answer,setAnswer]=useState(''),[log,setLog]=useState(''),[usageDirectory,setUsageDirectory]=useState('');
 const [caps,setCaps]=useState<Caps>({external_tokens:0,trial_tokens:0,run_tokens:0,max_trials:0,main_tokens:0,external_cost:'0'});
 const [preview,setPreview]=useState<{preview_sha256:string}|null>(null),[output,setOutput]=useState<unknown>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const provider=snapshot.providers.find(p=>p.id===snapshot.active_provider_id);
 const call=async(action:string,params:Record<string,unknown>)=>{
  setBusy(true);setError('');
  try{const value=await rpc<any>('evaluation',{action,params});setOutput(value);
   if(value.id&&Array.isArray(value.trials)){setExperiment(value);setIdentifier(value.id);}
   return value;
  }catch(e){setError(e instanceof Error?e.message:String(e));return null;}finally{setBusy(false);}
 };
 const id=experiment?.id||identifier;
 const scope={usage_directory:usageDirectory||null,raw_evidence_directory:null};
 const authorizationParams={experiment_id:id,caps,data_scope:scope};
 const refresh=()=>void call('describe',{experiment_id:id});
 const create=()=>void call('create_experiment',{name:'五类冻结案例试运行',conditions:{main_model:mainModel||'unknown',main_provider:mainProvider||'unknown',main_reasoning:'unknown',external_model:provider?.flash_model||'unknown',external_slot:'flash',external_provider_revision:provider?.revision||'unknown',external_reasoning:provider?.reasoning_effort||'unknown',runtime_version:'0.4.0-local',policy_version:'delegation-reliability-v1',dependency_version:'bundled',environment:'local; client and host settings must be confirmed before authorization',currency:'CNY',main_billing:'unknown',external_billing:'unknown'}});
 const invalidatePreview=()=>setPreview(null);
 return <section className="panel evaluation-panel" aria-label="受控收益评测">
  <h2>受控收益评测</h2><p className="subtle">准备与评分均在本机完成。固定案例比较直接执行、原引擎委托和 Pi 委托。创建清单不会授权付费请求；真实聊天需要在 Codex 中启动。</p>
  <div className="form-grid"><label className="field">主模型<input value={mainModel} onChange={e=>setMainModel(e.target.value)} disabled={busy}/></label>
   <label className="field">主模型供应商<input value={mainProvider} onChange={e=>setMainProvider(e.target.value)} disabled={busy}/></label>
   <label className="field">隔离工作区根目录<input value={root} onChange={e=>setRoot(e.target.value)} placeholder="绝对路径" disabled={busy}/></label>
   <label className="field">每组每案例重复次数<input type="number" min={1} max={10} value={repetitions} onChange={e=>setRepetitions(Number(e.target.value))} disabled={busy}/></label></div>
  <p className="subtle">外部模型固定为当前供应商的 Flash 档位：{provider?.flash_model||'未配置'}。主模型或供应商未知时，报告保留计量缺口。</p>
  <div className="row"><button disabled={busy} onClick={create}>创建草稿</button><input aria-label="实验 ID" value={identifier} onChange={e=>{setIdentifier(e.target.value);setExperiment(null);invalidatePreview();}} placeholder="实验 ID"/>
   <button disabled={busy||!id} onClick={refresh}>读取实验</button><button disabled={busy||!id||!root||experiment?.state!=='draft'} onClick={()=>void call('prepare_experiment',{experiment_id:id,workspace_root:root,repetitions})}>冻结并准备隔离材料</button></div>
  {experiment&&<p>状态：{experiment.state} · {experiment.trials.length} 个预定试次 · 清单 {experiment.manifest_sha256.slice(0,12)}</p>}
  <details><summary>累计授权预览</summary><p className="subtle">先填写具体限额和允许读取的本地日志目录。零额度不允许新请求。追加授权保留既有消耗。Codex 主模型仅能观测和提醒，无法拦截宿主在途请求。</p>
   <div className="form-grid">{(['external_tokens','trial_tokens','run_tokens','max_trials','main_tokens'] as const).map((key,i)=><label key={key} className="field">{['外部累计 Token','单试次 Token','单 Run Token','最多试次','主模型已观测 Token'][i]}<input type="number" min={0} value={caps[key]} onChange={e=>{setCaps({...caps,[key]:Number(e.target.value)});invalidatePreview();}} disabled={busy}/></label>)}
    <label className="field">外部估算金额上限<input value={caps.external_cost??''} onChange={e=>{setCaps({...caps,external_cost:e.target.value||null});invalidatePreview();}} disabled={busy}/><small>缺价时金额门禁拒绝请求；留空只使用 Token 限额。</small></label>
    <label className="field">主动选择的用量日志目录<input value={usageDirectory} onChange={e=>{setUsageDirectory(e.target.value);invalidatePreview();}} disabled={busy}/><small>留空不允许导入日志，默认报告不含聊天正文。</small></label></div>
   <button disabled={busy||!id} onClick={()=>void call('authorization_preview',authorizationParams).then(v=>setPreview(v))}>生成具体授权预览</button>
   {preview&&<><pre style={{maxHeight:180,overflow:'auto'}}>{JSON.stringify(preview,null,2)}</pre><button disabled={busy} onClick={()=>void call('authorize',{...authorizationParams,preview_sha256:preview.preview_sha256,confirmed:true}).then(()=>setPreview(null))}>确认上述范围并授权</button></>}
  </details>
  <details><summary>真实聊天操作与独立评分</summary>
   <label className="field">试次<select value={trialId} onChange={e=>setTrialId(e.target.value)}><option value="">选择试次</option>{experiment?.trials.map(t=><option key={t.id} value={t.id}>{t.group} · {t.case_id} · {t.state}</option>)}</select></label>
   <div className="row"><button disabled={busy||!trialId} onClick={()=>void call('trial_instruction',{trial_id:trialId})}>查看聊天指令</button><button disabled={busy||!trialId} onClick={()=>void call('start_trial',{trial_id:trialId}).then(refresh)}>登记开始</button></div>
   <div className="form-grid"><label className="field">新聊天 root/session ID<input value={session} onChange={e=>setSession(e.target.value)}/></label><label className="field">客户端<select value={client} onChange={e=>setClient(e.target.value)}><option value="desktop">Codex Desktop</option><option value="cli">Codex CLI</option><option value="local_substitute">本地替身</option></select></label></div>
   <button disabled={busy||!trialId||!session} onClick={()=>void call('bind_session',{trial_id:trialId,session_id:session,root_session_id:session,client_kind:client,confirmed:true})}>确认绑定独立新聊天</button>
   <label className="field">已选择目录中的用量 JSONL 文件<input value={log} onChange={e=>setLog(e.target.value)}/></label><button disabled={busy||!log||!session||!trialId} onClick={()=>void call('import_main_log',{trial_id:trialId,path:log,confirmed_session_id:session})}>导入绑定会话的统计</button>
   <label className="field">结构化答案文件（只读案例保存于工作区外）<input value={answer} onChange={e=>setAnswer(e.target.value)}/></label>
   <div className="row"><button disabled={busy||!trialId} onClick={()=>void call('finish_trial',{trial_id:trialId,outcome:'completed'}).then(refresh)}>登记执行结束</button>
    <button disabled={busy||!trialId} onClick={()=>void call('score_trial',{trial_id:trialId,answer_file:answer||null})}>运行冻结独立评分</button></div>
   <p className="subtle">执行结束后由独立评分决定质量。失败、未派工、取消及接手可通过本地 CLI 完整登记；六场景操作清单见开发文档。</p>
  </details>
  <div className="row"><button disabled={busy||!id} onClick={()=>void call('report',{experiment_id:id})}>生成完整报告</button><button disabled={busy||output===null} onClick={()=>void navigator.clipboard.writeText(JSON.stringify(output,null,2)).catch(e=>setError(String(e)))}>复制当前结果</button></div>
  {error&&<p role="alert" className="alert error">{error}</p>}{output!==null&&<pre aria-label="评测结果" style={{maxHeight:320,overflow:'auto',whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(output,null,2)}</pre>}
 </section>;
}
