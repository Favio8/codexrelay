// Regression: real Tauri launches namespace-prefixed Python after host reformat.
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

const root=process.cwd();
const area=fs.mkdtempSync(path.join(root,'.tmp','registration-lifecycle-'));
const staging=path.join(area,'app');fs.mkdirSync(staging);
fs.copyFileSync(path.join(root,'src-tauri/target/release/codexrelay.exe'),path.join(staging,'codexrelay.exe'));
fs.symlinkSync(path.join(root,'runtime'),path.join(staging,'runtime'),'junction');
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9236',WEBVIEW2_USER_DATA_FOLDER:path.join(area,'webview')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const setup=`from codexrelay.service import Service\nfrom codexrelay.integration import install,codex_dir\ns=Service()\ns.save_provider(name='Fixture',kind='custom',base_url='http://127.0.0.1:9000/v1',flash_model='fixture',pro_model='fixture')\ns.dispatch('set_enabled',{'enabled':True})\ninstall(s.store)\np=codex_dir()/'config.toml'\np.write_text(p.read_text(encoding='utf-8').replace('# Managed by CodexRelay\\n',''),encoding='utf-8')\ns.store.set_setting('integration_registration:'+str(codex_dir()),None)\n`;
const initialized=spawnSync(path.join(root,'runtime/python.exe'),['-I','-X','utf8','-c',setup],{env,encoding:'utf8',windowsHide:true});
assert.equal(initialized.status,0,initialized.stderr);
const config=path.join(env.CODEX_HOME,'config.toml');const saved=fs.readFileSync(config);
const hash=()=>createHash('sha256').update(fs.readFileSync(config)).digest('hex');const originalHash=hash();
let app,browser,page;
const rpc=(method)=>page.evaluate(async method=>window.__TAURI_INTERNALS__.invoke('relay_request',{method,params:{}}),method);
async function openApp(){
 app=spawn(path.join(staging,'codexrelay.exe'),[],{env,windowsHide:true,stdio:'ignore'});
 const deadline=Date.now()+20000;
 while(true){try{await fetch('http://127.0.0.1:9236/json/version');break;}catch(error){if(Date.now()>deadline)throw error;await new Promise(resolve=>setTimeout(resolve,200));}}
 browser=await chromium.connectOverCDP('http://127.0.0.1:9236');page=browser.contexts()[0].pages()[0];
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 const connected=page.getByRole('button',{name:'已接入 Codex',exact:true});await connected.waitFor();assert(await connected.isDisabled());
 assert.equal((await rpc('integration_preview')).data.registered,true);
 assert.equal((await rpc('snapshot')).data.enabled,true);
 assert.equal(hash(),originalHash);
}
async function closeApp(){
 if(browser){await browser.close();browser=undefined;}
 if(app&&app.exitCode===null){const exited=new Promise(resolve=>app.once('exit',resolve));app.kill();await exited;}app=undefined;
}
try{
 await openApp();await closeApp();await openApp();
 // A foreign execution override remains a conflict, detected without a click.
 const changed=saved.toString('utf8').replace(/(\[mcp_servers\.codexrelay\][\r\n]+)/,'$1cwd="'+area.replaceAll('\\','/')+'"\n');
 assert.notEqual(changed,saved.toString('utf8'));fs.writeFileSync(config,changed);
 await page.locator('.integration-status').getByText('需要处理接入配置',{exact:true}).waitFor({timeout:10000});
 assert.equal((await rpc('integration_preview')).data.registered,false);
 fs.writeFileSync(config,saved);
 await page.getByRole('button',{name:'已接入 Codex',exact:true}).waitFor({timeout:10000});assert.equal(hash(),originalHash);
 console.log(JSON.stringify({ok:true,checks:['actual namespace-prefixed runtime recognizes re-formatted registration','registration and enabled switch survive desktop exit/restart','no host configuration rewrite','settings refresh detects external changes and preserves foreign conflict']},null,2));
}finally{await closeApp();}
