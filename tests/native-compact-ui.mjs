// Actual packaged Tauri/WebView, isolated state, no paid API or real config writes.
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const root=process.cwd();
const area=fs.mkdtempSync(path.join(root,'.tmp','native-ui-'));
const staging=path.join(area,'app');fs.mkdirSync(staging);
fs.copyFileSync(path.join(root,'src-tauri/target/release/codexrelay.exe'),path.join(staging,'codexrelay.exe'));
fs.symlinkSync(path.join(root,'runtime'),path.join(staging,'runtime'),'junction');
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9235',WEBVIEW2_USER_DATA_FOLDER:path.join(area,'webview')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const setup=`from codexrelay.service import Service\nfrom codexrelay.integration import install,codex_dir\nfrom pathlib import Path\nimport os,tomlkit\ns=Service()\nfor i,name in enumerate(['DeepSeek（界面验收）','较长名称的兼容供应商用于省略检查','第三个供应商']):\n s.save_provider(name=name,kind='custom',base_url='http://127.0.0.1:9000/v1',flash_model='normal-model' if i!=1 else 'normal-model-with-a-long-identifier',pro_model='enhanced-model-with-a-long-identifier',max_total_tokens=1000000)\ninstall(s.store)\nold=Path(os.environ['LOCALAPPDATA'])/'Programs'/'CodexRelay'/'runtime'/'python.exe'\nif old.exists():\n config_path=codex_dir()/'config.toml'\n config=tomlkit.parse(config_path.read_text(encoding='utf-8'))\n config['mcp_servers']['codexrelay']['command']=str(old)\n config_path.write_text(tomlkit.dumps(config),encoding='utf-8')\n s.store.set_setting('integration_registration:'+str(codex_dir()),None)\nprint('Isolated fixture ready')\n`;
const initialized=spawnSync(path.join(root,'runtime/python.exe'),['-I','-X','utf8','-c',setup],{env,encoding:'utf8',windowsHide:true});
assert.equal(initialized.status,0,initialized.stderr);
const app=spawn(path.join(staging,'codexrelay.exe'),[],{env,windowsHide:true,stdio:'ignore'});
let browser;
try{
 const deadline=Date.now()+20000;
 while(true){try{await fetch('http://127.0.0.1:9235/json/version');break;}catch(error){if(Date.now()>deadline)throw error;await new Promise(resolve=>setTimeout(resolve,200));}}
 browser=await chromium.connectOverCDP('http://127.0.0.1:9235');
 const page=browser.contexts()[0].pages()[0];const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 assert.deepEqual(await page.evaluate(()=>[innerWidth,innerHeight]),[900,600]);
 await page.getByRole('button',{name:'供应商',exact:true}).click();
 const rows=await page.locator('.provider-row').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
 assert.equal(rows.length,3);
 assert(rows.every(r=>r.x===rows[0].x&&r.width===rows[0].width));
 assert(rows.every((r,i)=>i===0||r.y>=rows[i-1].y+rows[i-1].height));
 assert(rows.every(r=>r.height<100));
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('.provider-row').nth(1).getByRole('button',{name:'使用',exact:true}).click();
 await page.locator('.provider-row').nth(1).getByRole('button',{name:'使用中',exact:true}).waitFor();
 await page.locator('.provider-row').nth(1).getByRole('button',{name:'编辑',exact:true}).click();
 await page.getByRole('heading',{name:'编辑供应商',exact:true}).waitFor();
 await page.getByRole('button',{name:'取消',exact:true}).click();
 await page.setViewportSize({width:800,height:520});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 const connected=page.getByRole('button',{name:'已接入 Codex',exact:true});await connected.waitFor();assert(await connected.isDisabled());
 assert(await page.locator('.integration-status').getByText('Codex 已接入',{exact:true}).isVisible());
 assert.equal(await page.locator('[role="alert"]').count(),0);
 await page.setViewportSize({width:900,height:600});await page.getByRole('button',{name:'供应商',exact:true}).click();
 await page.screenshot({path:'docs/provider-row-preview.png',fullPage:true});
 assert.deepEqual(errors,[]);
 const report={ok:true,mode:'actual Tauri desktop with isolated real SQLite/RPC',defaultSize:[900,600],smallSize:[800,520],rows:rows.length,checks:['native default window dimensions','one row per provider','no horizontal overflow','actual activation/edit callbacks','owned integration recognized and repeat button disabled','no JavaScript errors']};
 fs.writeFileSync('.tmp/v012-native-ui.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}finally{
 if(browser)await browser.close();
 app.kill();
 await new Promise(resolve=>app.once('exit',resolve));
}
