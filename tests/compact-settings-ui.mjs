// Built frontend + real Python RPC in isolated state; inject only named IPC failures.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn,spawnSync} from 'node:child_process';
import {chromium,expect} from '@playwright/test';

const root=process.cwd(),area=fs.mkdtempSync(path.join(root,'.tmp','compact-settings-'));
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const python=path.join(root,'runtime','python.exe');
const bootstrap=`import sys;sys.path.insert(0,${JSON.stringify(path.join(root,'engine'))});`;
const initialized=spawnSync(python,['-I','-B','-X','utf8','-c',bootstrap+`from codexrelay.service import Service;s=Service();s.save_provider(name='DeepSeek（已导入）',kind='custom',base_url='http://localhost:1',flash_model='normal-model',pro_model='strong-model')`],{env,windowsHide:true,encoding:'utf8'});
assert.equal(initialized.status,0,initialized.stderr);
const failures={snapshot:2},calls=[],errors=[];
const busy={ok:false,error:'本地数据暂时被占用，请稍后重试。',code:'database_busy',retryable:true,details:'OperationalError: SQLITE_BUSY (5)'};
function rpc(method,params={}){
 calls.push(method);
 if(failures[method]>0){failures[method]--;return busy;}
 return new Promise((resolve,reject)=>{
  const child=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+'from codexrelay.__main__ import main;main()','rpc'],{env,windowsHide:true});
  let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);
  child.on('error',reject);child.on('exit',code=>{try{assert.equal(code,0,err);resolve(JSON.parse(out));}catch(e){reject(e);}});
  child.stdin.end(JSON.stringify({method,params}));
 });
}
const dist=path.join(root,'dist');
const server=http.createServer((req,res)=>{
 const file=path.resolve(dist,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
 if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:900,height:600}});page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('testRelay',(method,params)=>rpc(method,params));
 await page.addInitScript(()=>{
  window.isTauri=true;
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>command==='relay_request'?window.testRelay(args.method,args.params):1,transformCallback:()=>1,unregisterCallback:()=>{}};
  window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
 });
 await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'domcontentloaded'});
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 assert(calls.filter(c=>c==='snapshot').length>=3,'busy reads should recover automatically');
 assert.equal(await page.getByRole('alert').count(),0);
 assert.equal(await page.locator('.stat-name strong').innerText(),'DeepSeek');
 assert.equal(await page.locator('.import-badge').innerText(),'已导入');
 assert.equal(await page.locator('.stat-name strong').evaluate(e=>getComputedStyle(e).fontSize),'18px');
 await page.waitForFunction(()=>!/正在统计/.test(document.body.innerText));
 await page.screenshot({path:path.join(area,'overview.png'),fullPage:true});
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 const panel=page.locator('.backend-settings');
 await expect(panel.getByRole('button',{name:'应用设置'})).toBeEnabled();
 assert.equal(await panel.locator('.advanced-help').getAttribute('open'),null);
 assert(!(await panel.innerText()).includes('操作系统沙箱'),'technical explanations should start collapsed');
 for(const width of [900,800]){
  await page.setViewportSize({width,height:width===900?600:520});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const heights=await panel.locator('select, .backend-actions button').evaluateAll(nodes=>nodes.map(e=>Math.round(e.getBoundingClientRect().height)));
  assert(heights.every(h=>h===38),JSON.stringify(heights));
 }
 await page.setViewportSize({width:900,height:600});
 await page.screenshot({path:path.join(area,'settings.png'),fullPage:true});
 await panel.locator('.advanced-help summary').click();
 assert((await panel.innerText()).includes('操作系统沙箱'));await panel.locator('.advanced-help summary').click();
 // A settings mutation must never be replayed when its result says "busy".
 failures.activate_execution=1;let before=calls.filter(c=>c==='activate_execution').length;
 await panel.getByRole('button',{name:'应用设置'}).click();await panel.getByRole('alert').waitFor();
 await page.waitForTimeout(1200);
 assert.equal(calls.filter(c=>c==='activate_execution').length-before,1);
 assert.equal(await panel.locator('.error-details').getAttribute('open'),null);
 await panel.locator('.error-details summary').click();assert((await panel.innerText()).includes('SQLITE_BUSY'));
 await panel.getByRole('combobox',{name:'执行引擎',exact:true}).selectOption('pi');
 await panel.getByRole('button',{name:'应用设置'}).click();await page.getByText('设置已应用，新任务使用此组合。',{exact:true}).waitFor();
 assert.equal((await rpc('snapshot')).data.active_backend,'pi');
 assert.equal((await rpc('snapshot')).data.providers[0].name,'DeepSeek（已导入）');
 // Paid connection probes also must not be repeated after an uncertain result.
 failures.test_backend=1;before=calls.filter(c=>c==='test_backend').length;
 await panel.getByRole('button',{name:'测试连接'}).click();await panel.getByRole('alert').waitFor();await page.waitForTimeout(1000);
 assert.equal(calls.filter(c=>c==='test_backend').length-before,1);
 await page.getByRole('button',{name:'总览',exact:true}).click();
 failures.snapshot=Infinity;
 const globalAlert=page.locator('.content > .alert.error');await globalAlert.waitFor();
 await globalAlert.locator('.error-details summary').click();assert((await globalAlert.innerText()).includes('SQLITE_BUSY'));
 await page.getByRole('button',{name:'关闭错误提示'}).click();await page.waitForTimeout(3000);
 assert.equal(await globalAlert.count(),0,'dismissed identical polling errors should stay dismissed');
 failures.snapshot=0;await page.getByText('管理服务已连接',{exact:true}).waitFor();assert.equal(await globalAlert.count(),0);
 failures.snapshot=Infinity;await globalAlert.waitFor();
 failures.snapshot=0;await globalAlert.waitFor({state:'hidden'});
 // Recovery of one request must not hide another request's unresolved error.
 failures.integration_preview=Infinity;
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();await globalAlert.waitFor();
 await page.waitForTimeout(2500);assert(await globalAlert.isVisible());
 failures.integration_preview=0;await globalAlert.waitFor({state:'hidden'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,area,checks:['18px provider name and imported badge','collapsed explanations','900/800 layout and consistent control height','busy read retries','settings and model tests never retried','actual Pi selection persisted','saved name preserved','poll errors dismiss/recover independently','no JavaScript errors']}));
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
