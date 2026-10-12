import { invoke, isTauri } from '@tauri-apps/api/core';
export async function rpc<T = any>(method: string, params: Record<string, unknown> = {}): Promise<T> {
 if (!isTauri()) throw new Error('请在 CodexRelay 桌面应用中使用此功能。浏览器仅用于界面预览。');
 const result = await invoke<{ok:boolean;data:T;error?:string}>('relay_request', {method, params});
 if (!result.ok) throw new Error(result.error || '操作未完成');
 return result.data;
}
export interface Provider { id:string; name:string; kind:string; base_url:string; flash_model:string; pro_model:string; reasoning_effort:string; pro_reasoning_effort?:string; max_total_tokens?:number; max_output_tokens?:number; max_turns?:number; max_run_seconds?:number; has_key:boolean; }
export interface Project { path:string; disabled:boolean; }
export interface Task { id:string; title:string; workspace:string; model:string; provider?:string; provider_name?:string; mode:string; status:string; started_at?:number; created_at?:number; finished_at?:number; result?:unknown; error?:string; failure?:{code:string;title:string;hint:string;metrics?:Record<string,number>}; cancel_requested?:boolean; }
export interface Snapshot { enabled:boolean; providers:Provider[]; active_provider_id:string|null; projects:Project[]; tasks:Task[]; }
export const empty:Snapshot = {enabled:false,providers:[],active_provider_id:null,projects:[],tasks:[]};
