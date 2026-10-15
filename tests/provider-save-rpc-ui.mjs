// Adversarial stale editor + actual Python RPC. No provider requests or secrets.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn,spawnSync} from 'node:child_process';
import {chromium,expect} from '@playwright/test';

const root=process.cwd(),area=fs.mkdtempSync(path.join(root,'.tmp','provider-save-rpc-'));
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const python=path.join(root,'runtime','python.exe');
const bootstrap=`import sys;sys.path.insert(0,${JSON.stringify(path.join(root,'engine'))});`;
const calls=[],errors=[];
function rpc(method,params={}){
 calls.push({method,params});
 return new Promise((resolve,reject)=>{
  const child=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+'from codexrelay.__main__ import main;main()','rpc'],{env,windowsHide:true});
  let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);
  child.on('error',reject);child.on('exit',code=>{try{assert.equal(code,0,err);resolve(JSON.parse(out));}catch(e){reject(e);}});
  child.stdin.end(JSON.stringify({method,params}));
 });
}
const dist=path.join(root,'dist');
const web=http.createServer((req,res)=>{
 const file=path.resolve(dist,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
 if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
let browser;
try{
 const init=spawnSync(python,['-I','-B','-X','utf8','-c',bootstrap+"from codexrelay.service import Service;Service().save_provider(name='stale-editor-fixture',kind='custom',base_url='http://127.0.0.1:1/v1',flash_model='original-model',pro_model='strong')"],{env,windowsHide:true,encoding:'utf8'});
 assert.equal(init.status,0,init.stderr);
 await new Promise(resolve=>web.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:900,height:600}});page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));await page.exposeFunction('testRelay',rpc);
 await page.addInitScript(()=>{
  window.isTauri=true;
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>command==='relay_request'?window.testRelay(args.method,args.params):1,transformCallback:()=>1,unregisterCallback:()=>{}};
  window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
 });
 await page.goto(`http://127.0.0.1:${web.address().port}`,{waitUntil:'domcontentloaded'});
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'供应商',exact:true}).click();
 const original=(await rpc('snapshot')).data.providers[0];
 await page.getByRole('button',{name:'编辑',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'普通模型',exact:true})).toHaveValue('original-model');
 const changed=await rpc('save_provider',{...original,flash_model:'externally-changed-model',api_key:''});assert(changed.ok);
 const current=(await rpc('snapshot')).data.providers[0];assert.notEqual(current.revision,original.revision);
 await page.getByRole('button',{name:'保存供应商',exact:false}).click();
 await expect(page.getByRole('alert')).toContainText('供应商配置已变化');
 await expect(page.getByRole('heading',{name:'编辑供应商',exact:true})).toBeVisible();
 const after=(await rpc('snapshot')).data.providers[0];
 assert.equal(after.revision,current.revision);assert.equal(after.flash_model,'externally-changed-model');
 const staleCalls=calls.filter(c=>c.method==='save_provider'&&c.params.revision===original.revision);
 assert.equal(staleCalls.length,2,'one explicit external edit and one user save; no automatic replay');
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await expect(page.locator('.provider-row .meta-value').filter({hasText:'externally-changed-model'})).toBeVisible();
 await page.getByRole('button',{name:'编辑',exact:true}).click();
 await page.getByRole('textbox',{name:'名称',exact:true}).fill('renamed-after-refresh');
 await page.getByRole('button',{name:'保存供应商',exact:false}).click();
 await expect(page.getByRole('heading',{name:'编辑供应商',exact:true})).toHaveCount(0);
 const renamed=(await rpc('snapshot')).data.providers[0];
 assert.equal(renamed.revision,current.revision);assert.equal(renamed.name,'renamed-after-refresh');
 assert.equal(calls.filter(c=>c.method==='test_provider'||c.method==='test_backend').length,0);
 assert.deepEqual(errors,[]);
 const result={ok:true,evidence_kind:'browser_and_actual_python_rpc',area,modelRequests:0,paidRequests:0,checks:['stale provider editor rejects a changed revision','stale no-op cannot undo a real model change','failed save keeps editor and actionable error','save is not automatically replayed','refresh then metadata edit preserves current revision','no model probe or credential writes','no JavaScript errors']};
 const file=path.join(root,'artifacts','evidence','implementation-review','provider-save-rpc-ui.json');fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,JSON.stringify(result,null,2));
 console.log(JSON.stringify(result));
}finally{if(browser)await browser.close();await new Promise(resolve=>web.close(resolve));}
