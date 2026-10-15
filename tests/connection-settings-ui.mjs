// Built frontend, real Python RPC and local model responses; no real API charges.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn,spawnSync} from 'node:child_process';
import {chromium,expect} from '@playwright/test';

const root=process.cwd(),area=fs.mkdtempSync(path.join(root,'.tmp','connection-settings-'));
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const python=path.join(root,'runtime','python.exe');
const bootstrap=`import sys;sys.path.insert(0,${JSON.stringify(path.join(root,'engine'))});`;
let delay=0,status=200,simulateMissingPi=false;
const requests=[],calls=[],errors=[];
const modelServer=http.createServer(async(req,res)=>{
 let raw='';for await(const chunk of req)raw+=chunk;
 const body=JSON.parse(raw);requests.push(body);
 const responseStatus=status,responseDelay=delay;
 const hasResult=body.messages.some(m=>m.role==='tool');
 const toolName=body.tools?.[0]?.function.name;
 const toolResult=body.messages.filter(m=>m.role==='tool').at(-1)?.content||'';
 const marker=toolResult.split('\n').slice(1).join('\n').match(/\b[a-f0-9]{32}\b/)?.[0];
 const message=!body.tools||hasResult?{role:'assistant',content:marker||'CONNECTED'}:{role:'assistant',content:null,tool_calls:[{id:'probe',type:'function',function:{name:toolName,arguments:toolName==='Read'?JSON.stringify({path:'probe.txt'}):'{}'}}]};
 if(responseDelay)await new Promise(resolve=>setTimeout(resolve,responseDelay));
 res.writeHead(responseStatus,{'Content-Type':'application/json'});
 res.end(JSON.stringify(responseStatus===200?{id:'fixture',object:'chat.completion',created:1,model:body.model,usage:{prompt_tokens:20,completion_tokens:5,total_tokens:25},choices:[{index:0,message,finish_reason:message.tool_calls?'tool_calls':'stop'}]}:{error:{message:'fixture-secret'}}));
});
const dist=path.join(root,'dist');
const webServer=http.createServer((req,res)=>{
 const file=path.resolve(dist,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
 if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
function rpc(method,params={}){
 calls.push({method,params});
 return new Promise((resolve,reject)=>{
  const child=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+'from codexrelay.__main__ import main;main()','rpc'],{env,windowsHide:true});
  let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);
  child.on('error',reject);child.on('exit',code=>{try{assert.equal(code,0,err);const result=JSON.parse(out);if(method==='backend_catalog'&&simulateMissingPi&&result.ok)result.data=result.data.map(item=>item.id==='pi'?{...item,installed:false}:item);resolve(result);}catch(e){reject(e);}});
  child.stdin.end(JSON.stringify({method,params}));
 });
}
let browser;
try{
 await new Promise(resolve=>modelServer.listen(0,'127.0.0.1',resolve));
 await new Promise(resolve=>webServer.listen(0,'127.0.0.1',resolve));
 const init=spawnSync(python,['-I','-B','-X','utf8','-c',bootstrap+`from codexrelay.service import Service;s=Service();s.save_provider(name='连接测试演示',kind='custom',base_url='http://127.0.0.1:${modelServer.address().port}/v1',flash_model='fixture-fast',pro_model='fixture-pro',api_key='fixture-secret')`],{env,windowsHide:true,encoding:'utf8'});
 assert.equal(init.status,0,init.stderr);
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:900,height:600}});page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('testRelay',rpc);
 await page.addInitScript(()=>{
  window.isTauri=true;
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>command==='relay_request'?window.testRelay(args.method,args.params):1,transformCallback:()=>1,unregisterCallback:()=>{}};
  window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
 });
 await page.goto(`http://127.0.0.1:${webServer.address().port}`,{waitUntil:'domcontentloaded'});
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 const backend=page.locator('.backend-settings'),execution=page.locator('.execution-settings'),toast=page.locator('.toast');
 await expect(backend.getByRole('button',{name:'测试连接',exact:true})).toBeEnabled();
 for(const width of [900,800]){
  await page.setViewportSize({width,height:600});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
  const boxes=await execution.locator('.check-field').evaluateAll(nodes=>nodes.map(node=>{
   const input=node.querySelector('input').getBoundingClientRect(),label=node.querySelector('span').getBoundingClientRect();
   return {width:input.width,gap:label.left-input.right,centers:Math.abs(input.y+input.height/2-label.y-label.height/2)};
  }));
  assert(boxes.every(box=>box.width===16&&box.gap===8&&box.centers<1),JSON.stringify(boxes));
  const spacing=await execution.evaluate(node=>node.querySelector('.execution-footer').getBoundingClientRect().top-node.querySelector('.execution-advanced').getBoundingClientRect().bottom);
  assert(spacing>=24);
  assert.equal(await execution.locator('summary').evaluate(e=>getComputedStyle(e).listStyleType),'none');
 }
 await page.setViewportSize({width:900,height:600});
 await execution.screenshot({path:path.join(area,'execution-settings.png')});
 await execution.locator('summary').focus();await page.keyboard.press('Enter');
 await expect(execution.locator('details')).toHaveAttribute('open','');
 await execution.screenshot({path:path.join(area,'execution-expanded.png')});
 await page.keyboard.press('Enter');
 const save=execution.getByRole('button',{name:'保存执行默认设置',exact:true});
 await save.click();await toast.waitFor();await page.mouse.move(1,1);
 await page.waitForTimeout(1200);await save.click();await expect(toast).toHaveCount(1);
 await page.waitForTimeout(1200);assert(await toast.isVisible(),'identical notices must restart the 2s timer');
 await expect(toast).toBeHidden({timeout:1800});
 await save.click();await toast.waitFor();await toast.hover();await page.waitForTimeout(2300);
 assert(await toast.isVisible(),'hover pauses dismissal');await page.mouse.move(1,1);await expect(toast).toBeHidden({timeout:2500});
 // A delayed real request must show feedback immediately and never be replayed.
 delay=4000;const before=requests.length;
 await backend.getByRole('button',{name:'测试连接',exact:true}).click();
 await expect(backend.getByRole('status')).toContainText('正在测试连接');
 await expect(backend.getByRole('button',{name:'完整工具验证'})).toBeDisabled();
 await backend.getByRole('button',{name:'测试连接',exact:true}).evaluate(e=>e.dispatchEvent(new MouseEvent('click',{bubbles:true})));
 await expect(backend.getByRole('status')).toContainText('已等待 3 秒',{timeout:4500});
 await expect(toast).toContainText('工具能力尚未验证');assert.equal(requests.length-before,1);
 await expect(page.getByRole('region',{name:'连接测试用量'})).toContainText('缓存写入用量未知');
 assert.equal(calls.filter(c=>c.method==='test_backend').length,1);
 await expect(backend.getByRole('button',{name:'测试连接',exact:true})).toBeEnabled();
 await page.mouse.move(1,1);await expect(toast).toBeHidden({timeout:3000});
 delay=0;status=401;
 await backend.getByRole('button',{name:'测试连接',exact:true}).click();
 await expect(backend.getByRole('alert')).toContainText('鉴权失败');
 await page.mouse.move(1,1);await expect(toast).toBeHidden({timeout:7000});
 assert(await backend.getByRole('alert').isVisible(),'failure details remain after notification disappears');
 assert(!(await page.locator('body').innerText()).includes('fixture-secret'));
 status=200;await backend.getByRole('button',{name:'重试连接测试'}).click();
 await expect(toast).toContainText('连接正常');await expect(backend.getByRole('alert')).toHaveCount(0);
 for(const slot of ['flash','pro']){
  await backend.getByRole('combobox',{name:'测试模型'}).selectOption(slot);
  for(const [name,verification,count] of [['测试连接','quick',1],['完整工具验证','full',2],['执行后端验证','backend',2]]){
   const before=requests.length;
   await backend.getByRole('button',{name,exact:true}).click();
   await expect(toast).toContainText(verification==='quick'?'连接正常':verification==='full'?'模型工具协议验证通过':'此模型与执行后端验证通过',{timeout:50000});
   await expect(backend.getByRole('button',{name,exact:true})).toBeEnabled();
   assert.equal(requests.length-before,count,`${slot}/${verification} request count`);
   assert.equal(requests.at(-1).model,slot==='pro'?'fixture-pro':'fixture-fast');
   assert.equal(calls.filter(c=>c.method==='test_backend').at(-1).params.verification,verification);
  }
 }
 // Unavailable Pi may block backend execution; model probes still work.
 await rpc('activate_execution',{backend:'pi',provider_id:(await rpc('snapshot')).data.active_provider_id});
 simulateMissingPi=true;
 await page.getByRole('button',{name:'总览',exact:true}).click();
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 await expect(backend.getByRole('button',{name:'执行后端验证',exact:true})).toBeDisabled();
 for(const [name,count] of [['测试连接',1],['完整工具验证',2]]){
  const before=requests.length;await expect(backend.getByRole('button',{name,exact:true})).toBeEnabled();
  await backend.getByRole('button',{name,exact:true}).click();
  await expect(toast).toContainText(name==='测试连接'?'连接正常':'模型工具协议验证通过',{timeout:15000});
  await expect(backend.getByRole('button',{name,exact:true})).toBeEnabled();
  assert.equal(requests.length-before,count);
 }
 simulateMissingPi=false;
 await page.getByRole('button',{name:'供应商',exact:true}).click();
 for(const width of [900,800]){
  await page.setViewportSize({width,height:600});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`provider overflow at ${width}`);
 }
 const provider=page.locator('.provider-row');
 for(const slot of ['flash','pro']){
  await provider.getByRole('combobox',{name:'测试模型 连接测试演示',exact:true}).selectOption(slot);
  for(const [name,verification,count] of [['测试连接','quick',1],['完整工具验证','full',2]]){
   const before=requests.length;
   await provider.getByRole('button',{name,exact:true}).click();
   await expect(toast).toContainText(verification==='quick'?'连接正常':'模型工具协议验证通过');
   await expect(provider.getByRole('button',{name,exact:true})).toBeEnabled();
   assert.equal(requests.length-before,count);
   assert.equal(requests.at(-1).model,slot==='pro'?'fixture-pro':'fixture-fast');
   const call=calls.filter(c=>c.method==='test_provider').at(-1);
   assert.equal(call.params.slot,slot);assert.equal(call.params.verification,verification);
  }
 }
 // Opening and saving the same provider preserves validated evidence and identity.
 const prior=(await rpc('snapshot')).data.providers[0],savedRequests=requests.length;
 await provider.getByRole('button',{name:'编辑',exact:true}).click();
 await page.getByRole('button',{name:'保存供应商',exact:false}).click();
 await expect(page.getByRole('heading',{name:'编辑供应商',exact:true})).toHaveCount(0);
 const afterSave=(await rpc('snapshot')).data.providers[0];
 assert.equal(afterSave.revision,prior.revision);assert.deepEqual(afterSave.probe_evidence,prior.probe_evidence);
 assert.equal(requests.length,savedRequests);
 await provider.locator('.probe-evidence summary').click();
 await expect(provider.locator('.probe-evidence')).toContainText('工具协议：已验证');
 assert(requests.at(-1).messages.some(m=>m.role==='tool'));
 await page.mouse.move(1,1);await expect(toast).toBeHidden({timeout:3000});
 delay=4000;const leaveBefore=requests.length;
 await provider.getByRole('button',{name:'测试连接',exact:true}).click();
 await expect.poll(()=>requests.length).toBe(leaveBefore+1);
 await page.getByRole('button',{name:'总览',exact:true}).click();await page.waitForTimeout(1000);
 assert.equal(await toast.count(),0,'unmounted test must not publish a stale notification');
 assert(calls.some(c=>c.method==='cancel_probe'),'navigation must cancel the in-flight request');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,area,checks:['900/800 layout','16px checkbox and 8px aligned labels','24px footer separation','keyboard disclosure','2s dismissal and repeated notice reset','hover pause','immediate spinner and 3s elapsed feedback','one real request/no duplicate','persistent actionable errors/no credential exposure','Flash/Pro quick/full through provider-list RPC','Flash/Pro quick/full/backend through backend-panel RPC','real backend reads a random file marker','unavailable backend does not block model probes','unchanged provider save preserves revision and evidence without requests','unknown cache-write usage is visible','navigation cancels in-flight probe','no stale results after navigation','no JavaScript errors']}));
}finally{
 if(browser)await browser.close();
 await new Promise(resolve=>webServer.close(resolve));await new Promise(resolve=>modelServer.close(resolve));
}
