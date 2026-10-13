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
let requests=0;
const server=http.createServer(async(req,res)=>{
 let input='';for await(const data of req)input+=data;
 const body=JSON.parse(input);requests++;
 const tool=body.messages.filter(m=>m.role==='tool').at(-1);
 const message=tool?{role:'assistant',content:tool.content}:{role:'assistant',content:null,tool_calls:[{id:'probe',type:'function',function:{name:'Read',arguments:JSON.stringify({path:'probe.txt'})}}]};
 res.setHeader('Content-Type','application/json');res.end(JSON.stringify({id:'native-probe',object:'chat.completion',created:1,model:body.model,choices:[{index:0,message,finish_reason:tool?'stop':'tool_calls'}],usage:{prompt_tokens:100,completion_tokens:10,prompt_tokens_details:{cached_tokens:20}}}));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9238',WEBVIEW2_USER_DATA_FOLDER:path.join(area,'webview')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
env.PATH=path.join(process.env.SystemRoot,'System32')+';'+process.env.SystemRoot;
const python=path.join(root,'runtime','python.exe');
const setup=`from codexrelay.service import Service\nfrom codexrelay.integration import install,codex_dir,BEGIN,END\ns=Service()\ns.save_provider(name='Native fixture',kind='custom',base_url='http://127.0.0.1:${server.address().port}/v1',flash_model='fixture',pro_model='fixture')\ninstall(s.store)\np=codex_dir()/'AGENTS.md'\np.write_text('User rule: preserve me.\\n'+BEGIN+'\\nLegacy managed rules\\n'+END+'\\n',encoding='utf-8')\n`;
const initialized=spawnSync(python,['-I','-B','-X','utf8','-c',setup],{env,encoding:'utf8',windowsHide:true});assert.equal(initialized.status,0,initialized.stderr);
const logs=path.join(env.CODEX_HOME,'sessions','2020','01','01');fs.mkdirSync(logs,{recursive:true});
const sentinel='DO_NOT_CACHE_NATIVE_LOG_BODY';
const records=[{type:'session_meta',payload:{id:'native-log',model_provider:'custom'}},{type:'turn_context',payload:{model:'log-model'}},{type:'response_item',payload:{content:sentinel}},
 {type:'token_usage_record',timestamp:new Date().toISOString(),payload:{response_id:'native-usage',usage:{input_tokens:100,cached_input_tokens:20,output_tokens:10,reasoning_output_tokens:4}}}];
fs.writeFileSync(path.join(logs,'rollout-native.jsonl'),records.map(r=>JSON.stringify(r)).join('\n')+'\n');
let app,browser,page;
const rpc=(method,params={})=>page.evaluate(async({method,params})=>window.__TAURI_INTERNALS__.invoke('relay_request',{method,params}),{method,params});
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
 await page.getByRole('button',{name:'测试连接',exact:true}).click();
 await page.locator('.alert.success,.alert.error').filter({hasText:/连接测试通过|失败|操作|runtime|workspace/}).first().waitFor({timeout:45000});
 assert((await page.locator('body').innerText()).includes('连接测试通过。'),await page.locator('body').innerText());
 assert.equal(requests,2);
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
 const result={ok:true,target:'actual Tauri/WebView2 and bundled Python/Pi',requests,checks:['atomic backend/provider activation preserves disabled switch','Pi real local model+Read loop from GUI','opt-in Codex log import from old session directory','per-model cached-input prices','no message body in DB or DOM','stop scanning and clear cache without deleting logs','managed-rule upgrade preserves user rules','900x600 layout'],fixture:area};
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
