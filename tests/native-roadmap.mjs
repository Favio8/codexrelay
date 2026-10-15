// Actual Tauri/WebView2 + bundled Python/Pi, isolated state and deterministic local model.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const root=process.cwd(),area=fs.mkdtempSync(path.join(root,'.tmp','roadmap-native-'));
const version=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8')).version;
const staging=path.join(area,'app');fs.mkdirSync(staging);
fs.copyFileSync(path.join(root,'src-tauri/target/release/codexrelay.exe'),path.join(staging,'codexrelay.exe'));
fs.symlinkSync(path.join(root,'runtime'),path.join(staging,'runtime'),'junction');
let requests=0;const requestBodies=[],probeMatrix=[];
const server=http.createServer(async(req,res)=>{
 let input='';for await(const data of req)input+=data;
 const body=JSON.parse(input);requests++;requestBodies.push(body);
 const tool=body.messages.filter(m=>m.role==='tool').at(-1);
 const probeTool=body.tools?.find(t=>t.function.name==='relay_probe')?.function.name||'Read';
 const message=tool?{role:'assistant',content:tool.content}:!body.tools?{role:'assistant',content:'CONNECTED'}:{role:'assistant',content:null,tool_calls:[{id:'probe',type:'function',function:{name:probeTool,arguments:JSON.stringify(probeTool==='Read'?{path:'probe.txt'}:{})}}]};
 res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'native-probe',object:'chat.completion',created:1,model:body.model,choices:[{index:0,message,finish_reason:tool?'stop':'tool_calls'}],usage:{prompt_tokens:100,completion_tokens:10,prompt_tokens_details:{cached_tokens:20}}}));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9238',WEBVIEW2_USER_DATA_FOLDER:path.join(area,'webview')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
env.PATH=path.join(process.env.SystemRoot,'System32')+';'+process.env.SystemRoot;
const python=path.join(root,'runtime','python.exe');
const setup=`from codexrelay.service import Service\nfrom codexrelay.integration import install,codex_dir,BEGIN,END\ns=Service()\ns.save_provider(name='Native fixture',kind='custom',base_url='http://127.0.0.1:${server.address().port}/v1',flash_model='fixture',pro_model='fixture-pro')\ninstall(s.store)\np=codex_dir()/'AGENTS.md'\np.write_text('User rule: preserve me.\\n'+BEGIN+'\\nLegacy managed rules\\n'+END+'\\n',encoding='utf-8')\n`;
const initialized=spawnSync(python,['-I','-B','-X','utf8','-c',setup],{env,encoding:'utf8',windowsHide:true});assert.equal(initialized.status,0,initialized.stderr);
const logs=path.join(env.CODEX_HOME,'sessions','2020','01','01');fs.mkdirSync(logs,{recursive:true});
const sentinel='DO_NOT_CACHE_NATIVE_LOG_BODY';
const records=[{type:'session_meta',payload:{id:'native-log',model_provider:'custom'}},{type:'turn_context',payload:{model:'log-model'}},{type:'response_item',payload:{content:sentinel}},
 {type:'token_usage_record',timestamp:new Date().toISOString(),payload:{response_id:'native-usage',usage:{input_tokens:100,cached_input_tokens:20,output_tokens:10,reasoning_output_tokens:4}}}];
fs.writeFileSync(path.join(logs,'rollout-native.jsonl'),records.map(r=>JSON.stringify(r)).join('\n')+'\n');
let app,browser,page;
const rpc=(method,params={})=>page.evaluate(async({method,params})=>window.__TAURI_INTERNALS__.invoke('relay_request',{method,params}),{method,params});
function approveAuthorizationDialog(){
 const code=`$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);Add-Type -TypeDefinition @'
using System;using System.Text;using System.Runtime.InteropServices;
public static class RelayDialogFixture {
 public delegate bool EnumProc(IntPtr h,IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback,IntPtr p);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint id);
 [DllImport("user32.dll",EntryPoint="GetClassLongW")] public static extern uint GetClassLong(IntPtr h,int index);
 [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h,int id);
 [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h,uint message,IntPtr w,IntPtr l);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint message,IntPtr w,IntPtr l);
 public static IntPtr FindDialog(uint expected){IntPtr result=IntPtr.Zero;EnumWindows((h,p)=>{uint owner;GetWindowThreadProcessId(h,out owner);if(owner==expected&&GetClassLong(h,-32)==32770){result=h;return false;}return true;},IntPtr.Zero);return result;}

}
'@
$taskUntil=[DateTime]::UtcNow.AddSeconds(15)
while([DateTime]::UtcNow -lt $taskUntil){
 $taskWindow=[RelayDialogFixture]::FindDialog(${app.pid})
 if($taskWindow.ToInt64() -ne 0){
  $taskDefault=[RelayDialogFixture]::SendMessage($taskWindow,0x400,[IntPtr]::Zero,[IntPtr]::Zero).ToInt64()
  $taskButtonId=[int]($taskDefault -band 65535)
  if($taskButtonId -gt 0){
   [RelayDialogFixture]::PostMessage($taskWindow,0x111,[IntPtr]$taskButtonId,[IntPtr]::Zero)|Out-Null;Write-Output 'NATIVE_AUTHORIZATION_DIALOG_ACCEPTED';exit 0
  }
 }
 Start-Sleep -Milliseconds 100
}
$taskSeen=@('dialog='+$taskWindow+' default='+$taskButtonId)
throw ('Isolated native authorization dialog not found; owned windows: '+($taskSeen -join '; '))
`;
 const ps=path.join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe');
 const helper=spawn(ps,['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(code,'utf16le').toString('base64')],{env,windowsHide:true});let stdout='',stderr='';helper.stdout.on('data',c=>stdout+=c);helper.stderr.on('data',c=>stderr+=c);
 return new Promise((resolve,reject)=>{helper.once('error',reject);helper.once('exit',exit=>exit===0?resolve(stdout):reject(new Error(stderr||stdout)));});
}
try{
 app=spawn(path.join(staging,'codexrelay.exe'),[],{env,windowsHide:true,stdio:'ignore'});
 const until=Date.now()+25000;
 while(true){try{await fetch('http://127.0.0.1:9238/json/version');break;}catch(e){if(Date.now()>until)throw e;await new Promise(resolve=>setTimeout(resolve,200));}}
 browser=await chromium.connectOverCDP('http://127.0.0.1:9238');page=browser.contexts()[0].pages()[0];page.setDefaultTimeout(20000);
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 await page.getByRole('combobox',{name:'执行引擎',exact:true}).selectOption('pi');
 await page.getByRole('button',{name:'应用设置',exact:true}).click();
 await page.getByText('设置已应用，新任务使用此组合。',{exact:true}).waitFor();
 assert.equal((await rpc('snapshot')).data.active_backend,'pi');
 assert.equal((await rpc('snapshot')).data.enabled,false);
 await page.getByLabel('最多自动续作次数',{exact:true}).fill('2');
 await page.getByRole('button',{name:'保存执行默认设置',exact:true}).click();
 await page.getByText('执行默认设置已保存，仅影响新任务。',{exact:true}).waitFor();
 assert.equal((await rpc('snapshot')).data.execution_defaults.max_auto_continuations,2);
 await page.getByRole('button',{name:'执行后端验证',exact:true}).click();
 await page.locator('.toast.success,.backend-settings .alert.error').filter({hasText:/此模型与执行后端验证通过|失败|操作|runtime|workspace/}).first().waitFor({timeout:45000});
 assert((await page.locator('body').innerText()).includes('此模型与执行后端验证通过。'),await page.locator('body').innerText());
 assert.equal(requests,2);
 const provider=(await rpc('snapshot')).data.providers[0];
 for(const method of ['test_provider','test_backend'])for(const slot of ['flash','pro'])for(const verification of ['quick','full']){
  const prior=requests;
  const params={slot,verification,...(method==='test_provider'?{id:provider.id}:{backend:'pi',provider_id:provider.id})};
  const reply=await rpc(method,params);assert(reply.ok,JSON.stringify(reply));
  const expectedModel=slot==='flash'?'fixture':'fixture-pro';
  assert.equal(reply.data.model,expectedModel);assert.equal(reply.data.verification,verification);
  assert.equal(requests-prior,verification==='quick'?1:2);
  assert(requestBodies.slice(prior).every(body=>body.model===expectedModel));
  assert.equal(reply.data.backend_verified,false);
  probeMatrix.push({method,slot,verification,requests:requests-prior,model:expectedModel});
 }
 const proBackend=await rpc('test_backend',{backend:'pi',provider_id:provider.id,slot:'pro',verification:'backend'});
 assert(proBackend.ok&&proBackend.data.backend_verified,JSON.stringify(proBackend));
 assert.equal(proBackend.data.model,'fixture-pro');
 probeMatrix.push({method:'test_backend',slot:'pro',verification:'backend',requests:proBackend.data.request_count,model:proBackend.data.model});
 // Seed a real Pi Read run in this isolated state; the host process stays independent of GUI lifetime.
 const workspace=path.join(area,'execution workspace');fs.mkdirSync(workspace);fs.writeFileSync(path.join(workspace,'probe.txt'),'NATIVE_EXECUTION_EVIDENCE');
 const seed=`import time\nfrom pathlib import Path\nfrom codexrelay.service import Service\nfrom codexrelay.mcp_server import Relay\ns=Service();s.store.set_setting('enabled',True)\nr=Relay(s.store,s.vault,Path(${JSON.stringify(workspace)}));j=r.start('Read probe.txt for native task-group verification','','readonly','flash',task_kind='analysis')\nwhile s.store.task(j['job_id'])['status'] in {'queued','running','compacting','wrapping_up','cancelling'}: time.sleep(.05)\ns.store.set_setting('enabled',False)\nprint(j['job_id'])\n`;
 const worker=spawn(python,['-I','-B','-X','utf8','-c',seed],{env,windowsHide:true});let seedOut='',seedErr='';worker.stdout.on('data',c=>seedOut+=c);worker.stderr.on('data',c=>seedErr+=c);
 assert.equal(await new Promise(resolve=>worker.once('exit',resolve)),0,seedErr);
 const jobId=seedOut.trim(),run=(await rpc('get_task',{id:jobId})).data;
 assert.equal(run.status,'completed');assert(run.checkpoint);assert.equal(run.verification.status,'pending');
 await page.getByRole('button',{name:'任务记录',exact:true}).click();
 await page.getByText('Read probe.txt for native task-group verification',{exact:true}).click();
 await page.getByText('等待 Codex 核验',{exact:true}).first().waitFor();
 await page.getByRole('button',{name:'查看进度',exact:true}).click();
 await page.getByText('摘要是进度索引，仍需核验文件、测试与恢复记录。',{exact:true}).waitFor();
 const grant=await rpc('get_task_group',{group_id:run.group_id});assert.equal(grant.data.max_auto_continuations,2);
 // Exercise real App RPC with optimistic versions; no new model call is authorized by this operation.
 const priorRequests=requests;
 let dialog=approveAuthorizationDialog();await page.getByRole('button',{name:'追加一次执行机会',exact:true}).click();await dialog;
 let current;
 for(let attempt=0;attempt<40;attempt++){current=(await rpc('get_task_group',{group_id:run.group_id})).data;if(current.max_auto_continuations===3)break;await new Promise(resolve=>setTimeout(resolve,100));}
 assert.equal(current.max_auto_continuations,3);
 await page.getByText('调整后续轮次预算',{exact:true}).click();await page.getByLabel('后续轮次预算',{exact:true}).fill('65536');
 dialog=approveAuthorizationDialog();await page.getByRole('button',{name:'确认调整后续预算',exact:true}).click();await dialog;
 for(let attempt=0;attempt<40;attempt++){current=(await rpc('get_task_group',{group_id:run.group_id})).data;if(current.next_round_budget===65536)break;await new Promise(resolve=>setTimeout(resolve,100));}
 assert.equal(current.next_round_budget,65536);
 const extension={group_id:run.group_id,expected_version:current.version,idempotency_key:'native-single-grant',add_continuations:0,next_round_budget:65536};
 assert((await rpc('extend_task_authorization',extension)).ok);
 assert((await rpc('extend_task_authorization',extension)).ok);
 const extended=(await rpc('get_task_group',{group_id:run.group_id})).data;assert.equal(extended.max_auto_continuations,3);assert.equal(extended.next_round_budget,65536);
 assert.equal((await rpc('get_task',{id:jobId})).data.token_budget,1000000);assert.equal(requests,priorRequests);
 assert.equal((await rpc('snapshot')).data.enabled,false);
 await page.getByRole('button',{name:'关闭详情',exact:true}).click();
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 assert.equal((await rpc('codex_usage_settings')).data.enabled,false);
 await page.getByRole('button',{name:'开启本机日志统计',exact:true}).click();
 const group=page.getByRole('button',{name:/custom · log-model/});await group.waitFor();
 assert.equal((await rpc('codex_usage_today')).data.tokens.total,110);
 await group.click();
 await page.getByLabel('日志输入单价',{exact:true}).fill('10');await page.getByLabel('日志输出单价',{exact:true}).fill('20');await page.getByLabel('日志缓存命中单价',{exact:true}).fill('2');
 await page.getByRole('button',{name:'保存日志参考价格',exact:true}).click();
 await group.filter({hasText:'0.0010'}).waitFor();
 assert(!fs.readFileSync(path.join(env.CODEXRELAY_HOME,'relay.db')).includes(Buffer.from(sentinel)));
 assert(!(await page.locator('body').innerText()).includes(sentinel));
 await page.getByRole('button',{name:'停止日志统计',exact:true}).click();
 await page.getByRole('button',{name:'清除统计缓存',exact:true}).click();
 await page.getByText('派生统计缓存已清除，原日志未改变。',{exact:true}).waitFor();
 assert(fs.readFileSync(path.join(logs,'rollout-native.jsonl'),'utf8').includes(sentinel));
 await page.getByRole('button',{name:'更新托管规则',exact:true}).click();
 await page.getByText('托管规则已更新，请重启 Codex 加载多后端工具。',{exact:true}).waitFor();
 const rules=fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md'),'utf8');
 assert(rules.includes('User rule: preserve me.'));assert(rules.includes('delegate_to_agent'));assert(!rules.includes('Legacy managed rules'));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.getByRole('heading',{name:'执行后端',exact:true}).scrollIntoViewIfNeeded();
 const output=path.join(root,'artifacts','evidence');fs.mkdirSync(output,{recursive:true});
 await page.screenshot({path:path.join(output,version+'-native-settings.png')});
 const result={ok:true,target:'actual Tauri/WebView2 and bundled Python/Pi',requests,probeMatrix,checks:['atomic backend/provider activation preserves disabled switch','Pi real local model+Read loop from GUI','new execution defaults through UI','real Pi task group displayed with pending verification and on-demand checkpoint','native App grant and budget buttons with real Windows confirmation dialogs','App RPC grant idempotency and frozen current budget without model launch','opt-in Codex log import from old session directory','per-model cached-input prices','no message body in DB or DOM','stop scanning and clear cache without deleting logs','managed-rule upgrade preserves user rules','900x600 layout'],uncovered:[],fixture:area};
 fs.writeFileSync(path.join(output,version+'-native.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}catch(error){
 if(page)console.error('Native failure state:',await page.locator('body').innerText());
 throw error;
}finally{
 if(browser)await browser.close();
 if(app&&app.exitCode===null){const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill();await stopped;}
 server.close();
 // Retain the isolated native fixture and screenshot for inspection; no live profile touched.
}
