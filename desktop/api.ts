import { invoke, isTauri } from '@tauri-apps/api/core';
export class RpcError extends Error {
 constructor(message:string, public details='', public code='') {super(message);this.name='RpcError';}
}
const readMethods=new Set(['snapshot','integration_preview','backend_catalog','get_task','get_usage_prices','codex_usage_settings']);
export async function rpc<T = any>(method: string, params: Record<string, unknown> = {}): Promise<T> {
 if (!isTauri()) throw new Error('请在 CodexRelay 桌面应用中使用此功能。浏览器仅用于界面预览。');
 for(let attempt=0;;attempt++) {
  const result=await invoke<{ok:boolean;data:T;error?:string;details?:string;code?:string;retryable?:boolean}>('relay_request',{method,params});
  if(result.ok)return result.data;
  // Only retry busy reads. Applying settings, writes and model tests are never replayed.
  if(result.code==='database_busy'&&result.retryable&&readMethods.has(method)&&attempt<2) {
   await new Promise(resolve=>setTimeout(resolve,250*(attempt+1)));continue;
  }
  throw new RpcError(result.error||'操作未完成',result.details,result.code);
 }
}
export interface Provider { id:string; revision?:string; name:string; kind:string; base_url:string; flash_model:string; pro_model:string; reasoning_effort:string; pro_reasoning_effort?:string; max_total_tokens?:number; max_output_tokens?:number; max_turns?:number; max_run_seconds?:number; has_key:boolean; }
export interface Project { path:string; disabled:boolean; }
export interface Task { id:string; title:string; workspace:string; model:string; provider?:string; provider_name?:string; mode:string; status:string; backend_id?:string; backend_version?:string; started_at?:number; created_at?:number; finished_at?:number; result?:unknown; error?:string; failure?:{code:string;title:string;hint:string;metrics?:Record<string,number>}; cancel_requested?:boolean; verification?:{status:string;evidence?:string}; telemetry?:{workspace_review?:{review_id:string;status:string}|null;tokens?:{total:number|null;interrupted_request?:boolean};budget?:{metered_tokens:number;warning:boolean};bash?:{calls:number;failure_count:number};workspace_changes?:{added:string[];modified:string[];deleted:string[];complete:boolean;out_of_scope:string[]}}; }
export interface Snapshot { enabled:boolean; providers:Provider[]; active_provider_id:string|null; projects:Project[]; tasks:Task[]; active_backend?:string; usage_source?:string; usage_revision?:number; }
export const empty:Snapshot = {enabled:false,providers:[],active_provider_id:null,projects:[],tasks:[]};

export interface UsagePrice { input:string; cached_input:string|null; cache_write?:string|null; output:string; currency:'CNY'; label:string; version:string; created_at:number; source:string; }
export interface UsagePrices { models:Record<string,UsagePrice>; reference:UsagePrice|null; }
export interface MoneyRange { low:string; high:string; }
export interface UsageGroup { key:string; provider_id:string; provider_revision:string|null; provider_name:string; model:string; tasks:number; unpriced:number; reported_tokens:number; }
export interface UsageToday {
 date:string; timezone:string; utc_offset:string; day_start:number; day_end:number; generated_at:number;
 source:string; revision:number; retained_since:number|null; counts:Record<string,number>; coverage:Record<string,number>;
 tokens:{prompt:number;completion:number;total:number}|null; priced_tasks:number; unpriced_tasks:number;
 cost:MoneyRange|null; comparison:MoneyRange|null; reference:UsagePrice|null; currency:'CNY'; groups:UsageGroup[];
 recent:{id:string;model:string;provider_name:string;status:string;finished_at:number;coverage:string;tokens:{total:number}|null;cost:MoneyRange|null;price:UsagePrice|null;verification?:string}[];
}
