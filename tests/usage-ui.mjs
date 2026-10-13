// Browser UI + real per-request Python RPC, with isolated local state and no model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import http from 'node:http';
import { chromium } from '@playwright/test';

const root=process.cwd(), temp=fs.mkdtempSync(path.join(root,'.tmp','usage-ui-'));
const env={...process.env,CODEXRELAY_HOME:path.join(temp,'state'),CODEX_HOME:path.join(temp,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const python=path.join(root,'runtime','python.exe');
const bootstrap=`import sys; sys.path.insert(0, ${JSON.stringify(path.join(root,'engine'))}); `;
const calls=[];
function runPython(code){
 const r=spawnSync(python,['-I','-B','-X','utf8','-c',bootstrap+code],{env,encoding:'utf8',windowsHide:true});
 assert.equal(r.status,0,r.stderr);return r.stdout;
}
runPython(`from codexrelay.state import Store; import time; s=Store(); s.save_provider(dict(id='fixture',revision='v1',name='测试供应商',kind='custom',base_url='http://localhost:1',flash_model='fixture-model',pro_model='fixture-model',reasoning_effort='provider-default',credential_id='usage-ui-not-a-real-key')); s.set_setting('enabled',True); t=s.admit('用量界面测试',${JSON.stringify(temp)},'readonly','flash'); s.update_task(t['id'],status='failed',finished_at=time.time(),telemetry={'tokens':{'prompt':1000000,'completion':100000,'total':1100000,'available':True,'responses_with_usage':1,'responses_without_usage':0,'interrupted_request':False}}); s.set_setting('enabled',False)`);
async function rpc(method,params={}){
 calls.push(method);
 return await new Promise((resolve,reject)=>{
  const child=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+'from codexrelay.__main__ import main; main()','rpc'],{env,windowsHide:true});
  let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);
  child.on('error',reject);child.on('exit',code=>{try{if(code!==0)throw new Error(err);resolve(JSON.parse(out));}catch(e){reject(e);}});
  child.stdin.end(JSON.stringify({method,params}));
 });
}
const dist=path.join(root,'dist');
const server=http.createServer((req,res)=>{
 const file=path.resolve(dist,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
 if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
 const types={'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'};
 res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');
 res.end(fs.readFileSync(file));
});
let browser;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:900,height:600}});
 page.setDefaultTimeout(15000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('testRelay',async(method,params)=>rpc(method,params));
 await page.addInitScript(()=>{
  window.isTauri=true;
  window.__TAURI_INTERNALS__={invoke:async(command,args)=>command==='relay_request'?window.testRelay(args.method,args.params):1,transformCallback:()=>1,unregisterCallback:()=>{}};
  window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
 });
 await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'domcontentloaded'});
 const card=page.getByRole('button',{name:'查看今日委托用量'});
 await card.getByText('1,100,000',{exact:true}).waitFor();
 const initialCount=calls.filter(c=>c==='usage_today').length;
 await page.waitForTimeout(4500);
 assert.equal(calls.filter(c=>c==='usage_today').length,initialCount,'usage must not be in snapshot polling');
 await card.click();
 await page.getByRole('button',{name:'设置模型价格',exact:true}).click();
 await page.getByLabel('输入单价',{exact:true}).fill('2');await page.getByLabel('输出单价',{exact:true}).fill('3');
 await page.getByRole('button',{name:'保存价格',exact:true}).click();
 const backfill=page.getByRole('button',{name:'为今日 1 个未计价任务应用已保存价格'});
 await backfill.waitFor();await backfill.click();
 await page.getByText('已为 1 个任务应用已保存价格。',{exact:true}).waitFor();
 await page.getByRole('combobox').selectOption('reference');
 await page.getByLabel('参考模型名称',{exact:true}).fill('参考模型');
 await page.getByLabel('输入单价',{exact:true}).fill('10');await page.getByLabel('输出单价',{exact:true}).fill('20');
 await page.getByRole('button',{name:'保存价格',exact:true}).click();
 await page.getByRole('button',{name:'返回用量明细',exact:true}).click();
 await page.locator('.usage-comparison').filter({hasText:'9.70'}).waitFor();
 assert.equal(await page.locator('.usage-modal').evaluate(el=>el.scrollTop),0,'switching views returns to the title');
 assert.equal(await page.locator('.usage-modal').evaluate(el=>el.clientWidth),620);
 assert((await page.locator('.usage-summary').innerText()).includes('2.30'));
 assert((await page.locator('.usage-tasks').innerText()).includes('回填估算'));
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.equal(await page.locator('.usage-modal').evaluate(el=>el.scrollWidth>el.clientWidth),false);
 fs.mkdirSync(path.join(root,'artifacts','evidence'),{recursive:true});
 await page.screenshot({path:path.join(root,'artifacts','evidence','usage-details.png')});
 await page.getByRole('button',{name:'关闭用量详情'}).click();
 await page.evaluate(()=>window.scrollTo(0,0));
 await page.screenshot({path:path.join(root,'artifacts','evidence','usage-overview.png'),fullPage:true});
 const before=calls.filter(c=>c==='usage_today').length;
 await page.getByRole('button',{name:'任务记录',exact:true}).click();
 await page.getByRole('button',{name:'总览',exact:true}).click();
 await card.getByText('1,100,000',{exact:true}).waitFor();
 assert.equal(calls.filter(c=>c==='usage_today').length,before,'page remount should use cache');
 runPython(`from codexrelay.state import Store; import time; s=Store(); s.set_setting('enabled',True); t=s.admit('新增取消任务',${JSON.stringify(temp)},'readonly','flash'); s.update_task(t['id'],status='cancelled',finished_at=time.time(),telemetry={'tokens':{'prompt':100,'completion':20,'available':True,'responses_with_usage':1,'responses_without_usage':0,'interrupted_request':False}}); s.set_setting('enabled',False)`);
 await card.getByText('1,100,120',{exact:true}).waitFor({timeout:10000});
 assert.equal(calls.filter(c=>c==='usage_today').length,before+1,'terminal revision should invalidate once');
 await card.click();await page.locator('.usage-tasks button').first().click();
 await page.getByText('任务详情',{exact:true}).waitFor();
 assert.equal(errors.length,0,errors.join('\n'));
 const evidence={ok:true,target:'900x600 browser UI + real isolated Python RPC',checks:['price setup and explicit backfill','reference comparison 9.70','failed task counted','two-second polling does not recompute usage','page remount cache','new terminal task invalidates cache','historical task detail RPC','no horizontal overflow'],calls:calls.reduce((a,k)=>(a[k]=(a[k]||0)+1,a),{})};
 fs.writeFileSync(path.join(root,'artifacts','evidence','usage-ui.json'),JSON.stringify(evidence,null,2));
 console.log(JSON.stringify(evidence,null,2));
}finally{
 if(browser)await browser.close();server.close();
 // All writes are inside this test-created directory, never a user's state directory.
 fs.rmSync(temp,{recursive:true,force:true});
}
