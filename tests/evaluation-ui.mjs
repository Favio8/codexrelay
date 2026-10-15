// Built frontend + actual per-request Python RPC, synthetic data only.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn,spawnSync} from 'node:child_process';
import {chromium,expect} from '@playwright/test';

const root=process.cwd(),area=fs.mkdtempSync(path.join(root,'.tmp','evaluation-ui-'));
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const python=path.join(root,'runtime','python.exe');
const bootstrap=`import sys;sys.path.insert(0,${JSON.stringify(path.join(root,'engine'))});`;
const calls=[],errors=[];let modelRequests=0,failNextCreate=true;
const model=http.createServer((req,res)=>{modelRequests++;res.writeHead(500).end();});
const dist=path.join(root,'dist');
const web=http.createServer((req,res)=>{
 const file=path.resolve(dist,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
 if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]||'application/octet-stream');
 res.end(fs.readFileSync(file));
});
function rpc(method,params={}){
 calls.push({method,params});
 if(method==='evaluation'&&params.action==='create_experiment'&&failNextCreate){failNextCreate=false;return Promise.resolve({ok:false,error:'评测 fixture 拒绝；没有自动重放',code:'database_busy',retryable:true});}
 return new Promise((resolve,reject)=>{
  const child=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+'from codexrelay.__main__ import main;main()','rpc'],{env,windowsHide:true});
  let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);
  child.on('error',reject);child.on('exit',code=>{try{assert.equal(code,0,err);resolve(JSON.parse(out));}catch(e){reject(e);}});
  child.stdin.end(JSON.stringify({method,params}));
 });
}
let browser;
try{
 await new Promise(r=>model.listen(0,'127.0.0.1',r));await new Promise(r=>web.listen(0,'127.0.0.1',r));
 const init=spawnSync(python,['-I','-B','-X','utf8','-c',bootstrap+`from codexrelay.service import Service;s=Service();s.save_provider(name='evaluation-fixture',kind='custom',base_url='http://127.0.0.1:${model.address().port}/v1',flash_model='fixture-worker',pro_model='fixture-pro')`],{env,windowsHide:true,encoding:'utf8'});
 assert.equal(init.status,0,init.stderr);
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:900,height:600}});page.setDefaultTimeout(15000);
 page.on('pageerror',e=>errors.push(e.message));await page.exposeFunction('testRelay',rpc);
 await page.addInitScript(()=>{window.isTauri=true;window.__TAURI_INTERNALS__={invoke:async(command,args)=>command==='relay_request'?window.testRelay(args.method,args.params):1,transformCallback:()=>1,unregisterCallback:()=>{}};window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};});
 await page.goto(`http://127.0.0.1:${web.address().port}`,{waitUntil:'domcontentloaded'});
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'收益评测',exact:true}).click();
 const panel=page.getByRole('region',{name:'受控收益评测'});
 await panel.getByRole('button',{name:'创建草稿',exact:true}).click();
 await expect(panel.getByRole('alert')).toContainText('没有自动重放');
 assert.equal(calls.filter(c=>c.method==='evaluation'&&c.params.action==='create_experiment').length,1);
 await panel.getByRole('textbox',{name:'主模型',exact:true}).fill('fixture-codex');
 await panel.getByRole('textbox',{name:'主模型供应商',exact:true}).fill('fixture-host-provider');
 await panel.getByRole('textbox',{name:'隔离工作区根目录',exact:true}).fill(path.join(area,'workspaces'));
 await panel.getByRole('combobox',{name:'外部模型档位',exact:true}).selectOption('pro');
 await panel.locator('summary').filter({hasText:'冻结设置与参考价格'}).click();
 await panel.getByRole('combobox',{name:'主模型计费方式',exact:true}).selectOption('subscription');
 await panel.getByRole('combobox',{name:'外部模型计费方式',exact:true}).selectOption('api');
 for(const label of ['主模型','外部模型']){
  await panel.getByRole('checkbox',{name:`填写${label}参考价格`,exact:true}).check();
  await panel.getByRole('textbox',{name:`${label}输入单价`,exact:true}).fill('1.2');
  await panel.getByRole('textbox',{name:`${label}输出单价`,exact:true}).fill('3.4');
  await panel.getByRole('textbox',{name:`${label}价格来源`,exact:true}).fill('isolated fixture; no real price claim');
  await panel.getByLabel(`${label}价格核对时间`,{exact:true}).fill('2026-10-15T12:00');
 }
 await panel.getByRole('button',{name:'创建草稿',exact:true}).click();
 await expect(panel.getByLabel('评测结果')).toContainText('"state": "draft"');
 const experimentId=await panel.getByRole('textbox',{name:'实验 ID',exact:true}).inputValue();assert.match(experimentId,/^[a-f0-9]{32}$/);
 await panel.getByRole('button',{name:'冻结并准备隔离材料',exact:true}).click();
 await expect(panel.getByLabel('评测结果')).toContainText('"state": "prepared"');
 await expect(panel.getByLabel('评测结果')).toContainText('"authorization": null');
 const authorization=panel.locator('details').filter({has:page.locator('summary').filter({hasText:'累计授权预览'})});
 await authorization.locator('summary').focus();await page.keyboard.press('Enter');
 await expect(authorization).toHaveAttribute('open','');
 await panel.getByRole('button',{name:'生成具体授权预览',exact:true}).click();
 await expect(panel.getByRole('button',{name:'确认上述范围并授权',exact:true})).toBeVisible();
 assert.equal(calls.filter(c=>c.method==='evaluation'&&c.params.action==='authorize').length,0);
 let actual=await rpc('evaluation',{action:'describe',params:{experiment_id:experimentId}});
 assert(actual.ok,JSON.stringify(actual));assert.equal(actual.data.authorization,null);assert.equal(actual.data.trials.length,15);
 assert.equal(actual.data.conditions.external_slot,'pro');assert.equal(actual.data.conditions.external_model,'fixture-pro');
 assert.equal(actual.data.conditions.main_billing,'subscription');assert.equal(actual.data.conditions.external_billing,'api');
 for(const price of [actual.data.conditions.main_price,actual.data.conditions.external_price]){
  assert.equal(price.input,'1.2');assert.equal(price.output,'3.4');assert.equal(price.cached_input,null);assert.equal(price.cache_write,null);
  assert.equal(price.source,'isolated fixture; no real price claim');assert.match(price.sha256,/^[a-f0-9]{64}$/);
 }
 await panel.locator('summary').filter({hasText:'真实聊天操作与独立评分'}).click();
 const selected=actual.data.trials.filter(t=>t.group==='B').slice(0,3);
 await panel.getByRole('combobox',{name:'试次',exact:true}).selectOption(selected[0].id);
 let describes=calls.filter(c=>c.method==='evaluation'&&c.params.action==='describe').length;
 await panel.getByRole('button',{name:'登记开始',exact:true}).click();
 await expect(panel.getByRole('alert')).toHaveText('EXPERIMENT_NOT_AUTHORIZED');
 assert.equal(calls.filter(c=>c.method==='evaluation'&&c.params.action==='describe').length,describes,'failed mutation must not refresh away its error');
 // This grant is confined to the fixture database. Zero external tokens means no model requests.
 await panel.getByRole('spinbutton',{name:'最多试次',exact:true}).fill('3');
 await panel.getByRole('spinbutton',{name:'主模型已观测 Token',exact:true}).fill('1000');
 await panel.getByRole('button',{name:'生成具体授权预览',exact:true}).click();
 await panel.getByRole('button',{name:'确认上述范围并授权',exact:true}).click();
 await expect(panel.getByLabel('评测结果')).toContainText('"state": "authorized"');
 const endings=['not_delegated','failed','completed_by_parent'];
 for(let i=0;i<selected.length;i++){
  await panel.getByRole('combobox',{name:'试次',exact:true}).selectOption(selected[i].id);
  await panel.getByRole('button',{name:'登记开始',exact:true}).click();
  await expect(panel.getByLabel('评测结果')).toContainText('"state": "running"');
  await expect(panel.getByRole('button',{name:'登记开始',exact:true})).toBeEnabled();
  await panel.getByRole('combobox',{name:'实际执行结局',exact:true}).selectOption(endings[i]);
  if(i===0)await panel.getByRole('combobox',{name:'未派工原因',exact:true}).selectOption('disabled');
  await panel.getByRole('button',{name:'登记执行结束',exact:true}).click();
  await expect(panel.getByLabel('评测结果')).toContainText(`"outcome": "${endings[i]}"`);
  await expect(panel.getByRole('button',{name:'登记执行结束',exact:true})).toBeEnabled();
 }
 actual=await rpc('evaluation',{action:'describe',params:{experiment_id:experimentId}});
 for(let i=0;i<selected.length;i++)assert.equal(actual.data.trials.find(t=>t.id===selected[i].id).outcome,endings[i]);
 assert.equal(actual.data.trials.find(t=>t.id===selected[0].id).not_delegated_reason,'disabled');
 for(const [width,height] of [[900,600],[800,520]]){
  await page.setViewportSize({width,height});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`evaluation overflow ${width}x${height}`);
  await page.screenshot({path:path.join(area,`evaluation-${width}.png`),fullPage:true});
 }
 await panel.getByRole('button',{name:'生成完整报告',exact:true}).click();
 await expect(panel.getByLabel('评测结果')).toContainText('"report_version": 1');
 await expect(panel.getByLabel('评测结果')).toContainText('"requests": 0');
 assert.equal(modelRequests,0);assert.deepEqual(errors,[]);
 const result={ok:true,evidence_kind:'browser_and_actual_python_rpc',area,modelRequests,paidRequests:0,checks:['busy mutation error has no auto replay','Pro model and sourced API prices frozen through UI/RPC','subscription and API billing distinct','unknown cache prices retained','freeze 15 isolated trial files','preview does not authorize','failed start error remains visible','fixture-only explicit token-zero grant','not delegated/failed/parent takeover outcomes','prepare/preview/report send no model request','900x600 and 800x520 layout','keyboard disclosure','counter-only report','no JavaScript errors']};
 const evidence=path.join(root,'artifacts','evidence','implementation-review','evaluation-ui.json');fs.mkdirSync(path.dirname(evidence),{recursive:true});fs.writeFileSync(evidence,JSON.stringify(result,null,2));
 console.log(JSON.stringify(result));
}finally{if(browser)await browser.close();await new Promise(r=>web.close(r));await new Promise(r=>model.close(r));}
