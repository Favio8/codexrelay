// This test controls the actual Tauri WebView over CDP, not a mocked web UI.
import { chromium } from 'playwright';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const root=process.cwd();
let requests=0;
const server=http.createServer(async(req,res)=>{
 let raw='';for await(const chunk of req)raw+=chunk;
 const body=JSON.parse(raw);requests++;
 const hasResult=body.messages.some(m=>m.role==='tool');
 const message=hasResult?{role:'assistant',content:'CONNECTED'}:{role:'assistant',content:null,tool_calls:[{id:'probe',type:'function',function:{name:'relay_probe',arguments:'{}'}}]};
 res.writeHead(200,{'Content-Type':'application/json'});
 res.end(JSON.stringify({id:'fixture',object:'chat.completion',created:1,model:body.model,choices:[{index:0,message,finish_reason:hasResult?'stop':'tool_calls'}],usage:{prompt_tokens:5,completion_tokens:5,total_tokens:10}}));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const readyDeadline=Date.now()+15000;
while(true){try{await fetch('http://127.0.0.1:9223/json/version');break;}catch(error){if(Date.now()>readyDeadline)throw error;await new Promise(resolve=>setTimeout(resolve,200));}}
const browser=await chromium.connectOverCDP('http://127.0.0.1:9223');
const page=browser.contexts()[0].pages()[0];
const rpc=(method,params={})=>page.evaluate(async({method,params})=>window.__TAURI_INTERNALS__.invoke('relay_request',{method,params}),{method,params});
let provider;
try{
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'供应商',exact:true}).click();
 await page.getByRole('button',{name:'添加供应商',exact:true}).first().click();
 await page.getByRole('button',{name:'OpenAI 兼容服务',exact:true}).click();
 await page.getByLabel('名称',{exact:true}).fill('桌面验收 · 本地测试接口');
 await page.getByLabel('API 基础地址',{exact:true}).fill('http://127.0.0.1:'+server.address().port+'/v1');
 await page.getByPlaceholder('输入供应商 API Key').fill('fixture-only-not-a-real-key');
 await page.getByLabel('普通模型',{exact:true}).fill('fixture-fast');
 await page.getByLabel('增强模型',{exact:true}).fill('fixture-pro');
 await page.getByRole('button',{name:'保存供应商',exact:false}).click();
 await page.getByRole('heading',{name:'桌面验收 · 本地测试接口',exact:true}).waitFor();
 provider=(await rpc('snapshot')).data.providers.find(p=>p.name==='桌面验收 · 本地测试接口');
 assert(provider.has_key);
 assert(!JSON.stringify(await rpc('snapshot')).includes('fixture-only-not-a-real-key'));
 await page.getByRole('button',{name:'测试连接',exact:true}).click();
 await page.getByText('真实模型与工具调用测试通过',{exact:true}).waitFor({timeout:30000});
 assert.equal(requests,2);
 await page.getByRole('button',{name:'总览',exact:true}).click();
 await page.getByRole('switch',{name:'全局子代理开关'}).click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="全局子代理开关"]')?.getAttribute('aria-checked')==='true');
 assert.equal((await rpc('snapshot')).data.enabled,true);
 const workspace=path.join(root,'.tmp','desktop-test-project');fs.mkdirSync(workspace,{recursive:true});
 assert((await rpc('set_project',{path:workspace,disabled:false})).ok);
 await page.getByRole('button',{name:'项目',exact:true}).click();
 await page.getByRole('switch').waitFor();
 await page.getByRole('switch').click();
 await page.getByText('此项目已禁用',{exact:true}).waitFor();
 assert.equal((await rpc('snapshot')).data.projects.find(p=>p.path.toLowerCase()===workspace.toLowerCase()).disabled,true);
 const configHome=path.join(root,'.tmp','desktop-test-codex');fs.mkdirSync(configHome,{recursive:true});
 fs.writeFileSync(path.join(configHome,'config.toml'),'# existing\nmodel="preserved"\n');
 const installation=await rpc('integration_install',{codex_home:configHome});assert(installation.ok,JSON.stringify(installation));
 assert(fs.readFileSync(path.join(configHome,'config.toml'),'utf8').includes('model="preserved"'));
 assert((await rpc('integration_uninstall',{codex_home:configHome})).ok);
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 await page.getByPlaceholder('自动使用 CODEX_HOME 或用户 .codex 目录').fill(configHome);
 await page.getByRole('button',{name:'运行检查',exact:true}).click();
 await page.locator('.diagnostic-output').first().waitFor();
 assert(!(await page.locator('body').innerText()).includes('fixture-only-not-a-real-key'));
 await rpc('set_enabled',{enabled:false});
 await page.getByRole('button',{name:'总览',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="全局子代理开关"]')?.getAttribute('aria-checked')==='false');
 await page.screenshot({path:path.join(root,'.tmp','desktop-verified.png'),fullPage:true});
 console.log(JSON.stringify({ok:true,target:'actual Tauri desktop WebView',checks:['provider form and Windows credential storage','live HTTP tool roundtrip from GUI','persisted global switch','project switch','native IPC install/uninstall in isolated Codex home','diagnostics UI','no Key in returned state or DOM'],requests},null,2));
}finally{
 if(provider){await rpc('set_enabled',{enabled:false});await rpc('delete_provider',{id:provider.id});}
 await browser.close();server.close();
}
