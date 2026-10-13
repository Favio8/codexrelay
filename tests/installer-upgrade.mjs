// Real NSIS upgrade, legacy GUI/MCP processes, and single-instance activation.
// All data, registrations and shortcuts are scoped to this fixture.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {chromium} from '@playwright/test';

const root=process.cwd(),resume=process.argv[2]==='--resume';
const area=resume?path.resolve(process.argv[3]):fs.mkdtempSync(path.join(root,'.tmp','installer-升级 with spaces-'));
const local=path.join(area,'Local'),canonical=path.join(local,'Programs','CodexRelay'),legacy=path.join(local,'CodexRelay');
const version=JSON.parse(fs.readFileSync('package.json','utf8')).version;
const installer=path.join(root,'src-tauri/target/release/bundle/nsis',`CodexRelay_${version}_x64-setup.exe`);
const env={...process.env,CODEXRELAY_INSTALL_TEST_ROOT:area,CODEXRELAY_HOME:legacy,CODEX_HOME:path.join(area,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const ps=path.join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe');
const quote=s=>"'"+s.replaceAll("'","''")+"'";
function powershell(code){code='[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);'+code;const r=spawnSync(ps,['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(code,'utf16le').toString('base64')],{env,windowsHide:true,encoding:'utf8'});assert.equal(r.status,0,r.stderr||r.stdout);return r.stdout;}
function python(code,exe=path.join(canonical,'runtime/python.exe')){const r=spawnSync(exe,['-I','-B','-X','utf8','-c',code],{env,windowsHide:true,encoding:'utf8'});assert.equal(r.status,0,r.stderr);return r.stdout;}
function exited(child,timeout=90000){return new Promise((resolve,reject)=>{if(child.exitCode!==null)return resolve(child.exitCode);const timer=setTimeout(()=>{child.kill();reject(new Error('process timeout: '+child.spawnfile));},timeout);child.once('error',reject);child.once('exit',code=>{clearTimeout(timer);resolve(code);});});}
function launchInstaller(){return spawn(installer,['/S','/UPDATE'],{env,windowsHide:true,stdio:'ignore'});}
async function ready(port){const until=Date.now()+25000;while(true){try{await fetch(`http://127.0.0.1:${port}/json/version`);return;}catch(e){if(Date.now()>until)throw e;await new Promise(r=>setTimeout(r,150));}}}
function launchGui(directory,port){return spawn(path.join(directory,'codexrelay.exe'),[],{env:{...env,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`,WEBVIEW2_USER_DATA_FOLDER:path.join(area,'webview-'+port)},windowsHide:true,stdio:'ignore'});}
async function launchMcp(directory){
 const child=spawn(path.join(directory,'runtime/python.exe'),['-I','-B','-X','utf8','-m','codexrelay','mcp'],{env,cwd:area,windowsHide:true});
 let buffer='',id=0;const pending=new Map();child.stderr.resume();
 child.stdout.on('data',chunk=>{buffer+=chunk;let n;while((n=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,n);buffer=buffer.slice(n+1);if(!line.trim())continue;const item=JSON.parse(line);const callback=pending.get(item.id);if(callback){pending.delete(item.id);callback(item);}}});
 const call=(method,params)=>new Promise(resolve=>{const key=++id;pending.set(key,resolve);child.stdin.write(JSON.stringify({jsonrpc:'2.0',id:key,method,params})+'\n');});
 await call('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'isolated-upgrade-test',version:'1'}});
 child.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');
 const response=await call('tools/call',{name:'ping',arguments:{}});assert(!response.error);
 return {child,ping:JSON.parse(response.result.content[0].text)};
}
const children=[],browsers=[];
try{
 if(!resume){
 for(const [directory,oldVersion] of [[canonical,'0.2.0'],[legacy,'0.2.1']]){
  fs.mkdirSync(directory,{recursive:true});
  fs.copyFileSync(path.join(root,'.tmp/upgrade-baseline',oldVersion+'.exe'),path.join(directory,'codexrelay.exe'));
  fs.cpSync(path.join(root,'runtime'),path.join(directory,'runtime'),{recursive:true});
  const cached=path.join(root,'.tmp/upgrade-baseline',oldVersion+'-python');
  const actual=fs.existsSync(cached)?cached:(oldVersion==='0.2.0'?path.join(process.env.LOCALAPPDATA,'Programs/CodexRelay/runtime/Lib/site-packages/codexrelay'):path.join(process.env.LOCALAPPDATA,'CodexRelay/runtime/Lib/site-packages/codexrelay'));
  fs.cpSync(actual,path.join(directory,'runtime/Lib/site-packages/codexrelay'),{recursive:true});
 }
 const key='Software\\CodexRelay\\InstallerTests\\'+path.basename(area);
 powershell(`$ErrorActionPreference='Stop';$r=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryView]::Registry64);$k=$r.CreateSubKey(${quote(key+'\\Product')});$k.SetValue('',${quote(canonical)});$k.Dispose();$k=$r.CreateSubKey(${quote(key+'\\Uninstall')});$k.SetValue('DisplayVersion','0.2.0');$k.SetValue('InstallLocation',${quote('"'+canonical+'"')});$k.Dispose();$r.Dispose();$w=New-Object -ComObject WScript.Shell;foreach($folder in @(${quote(path.join(area,'StartMenu'))},${quote(path.join(area,'Desktop'))})){New-Item -ItemType Directory -Force -Path $folder|Out-Null;$l=$w.CreateShortcut((Join-Path $folder 'CodexRelay.lnk'));$l.TargetPath=${quote(legacy+'\\codexrelay.exe')};$l.Save()}`);
 python(`from codexrelay.service import Service\nfrom codexrelay.integration import install\ns=Service();s.save_provider(name='保留供应商',kind='custom',base_url='http://localhost:1',flash_model='fixture',pro_model='fixture');s.store.set_setting('active_backend','pi');s.store.set_setting('enabled',True);install(s.store)\n`);
 const guiOld=launchGui(canonical,9256),guiSide=launchGui(legacy,9257);children.push(guiOld,guiSide);
 await Promise.all([ready(9256),ready(9257)]);
 const mcpOld=await launchMcp(canonical),mcpSide=await launchMcp(legacy);children.push(mcpOld.child,mcpSide.child);
 assert.equal(mcpOld.ping.version,'0.2.0');assert.equal(mcpSide.ping.version,'0.2.1');
 // Upgrade refusal must leave the live job, old GUIs and switch alone.
 const owner=spawn(path.join(canonical,'runtime/python.exe'),['-I','-B','-X','utf8','-c',`import sys,json;from codexrelay.state import Store;s=Store();t=s.admit('live upgrade guard',${JSON.stringify(area)},'readonly','flash');print(json.dumps(t),flush=True);sys.stdin.read()`],{env,windowsHide:true});children.push(owner);owner.stderr.resume();
 await new Promise(resolve=>owner.stdout.once('data',resolve));
 const blocked=launchInstaller();children.push(blocked);assert.notEqual(await exited(blocked),0);
 assert.equal(owner.exitCode,null);assert.equal(guiOld.exitCode,null);assert.equal(guiSide.exitCode,null);
 assert.equal(JSON.parse(python('from codexrelay.state import Store;import json;print(json.dumps(Store().snapshot()["enabled"]))')),true);
 owner.stdin.end();await exited(owner);
 const businessCode='from codexrelay.state import Store;import json;s=Store();s.snapshot();print(json.dumps({"providers":s.providers(),"enabled":s.setting("enabled"),"backend":s.setting("active_backend")},sort_keys=True,ensure_ascii=False))';
 const before=python(businessCode);const config=path.join(env.CODEX_HOME,'config.toml'),savedConfig=fs.readFileSync(config);const rules=fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md'));
 const setup=launchInstaller();children.push(setup);assert.equal(await exited(setup),0);
 for(const child of [guiOld,guiSide,mcpOld.child,mcpSide.child])await exited(child,10000);
 assert(!fs.existsSync(path.join(legacy,'codexrelay.exe')),'old side installation must no longer be launchable');
 assert(fs.existsSync(path.join(legacy,'relay.db')),'business data must remain');
 assert.equal(python(businessCode),before);assert.deepEqual(fs.readFileSync(config),savedConfig);assert.deepEqual(fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md')),rules);
 const report=JSON.parse(fs.readFileSync(path.join(legacy,'upgrades/last-upgrade.json'),'utf8'));assert(report.finished);assert.equal(report.version,version);assert.equal(report.stopped_processes.length,4);
 }
 const links=JSON.parse(powershell(`$w=New-Object -ComObject WScript.Shell;@($w.CreateShortcut(${quote(path.join(area,'StartMenu/CodexRelay.lnk'))}).TargetPath,$w.CreateShortcut(${quote(path.join(area,'Desktop/CodexRelay.lnk'))}).TargetPath)|ConvertTo-Json -Compress`));assert(links.every(s=>s.toLowerCase()===path.join(canonical,'codexrelay.exe').toLowerCase()));
 const newMcp=await launchMcp(canonical);children.push(newMcp.child);assert.equal(newMcp.ping.version,version);newMcp.child.stdin.end();await exited(newMcp.child);
 const primary=launchGui(canonical,9258);children.push(primary);await ready(9258);
 const browser=await chromium.connectOverCDP('http://127.0.0.1:9258');browsers.push(browser);const page=browser.contexts()[0].pages()[0];await page.getByText('管理服务已连接',{exact:true}).waitFor();
 powershell(`Add-Type -Name Window -Namespace RelayTest -MemberDefinition '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd,int nCmdShow);';[RelayTest.Window]::ShowWindow((Get-Process -Id ${primary.pid}).MainWindowHandle,0)|Out-Null`);
 const duplicateRoot=path.join(area,'another-copy');fs.mkdirSync(duplicateRoot);fs.copyFileSync(path.join(canonical,'codexrelay.exe'),path.join(duplicateRoot,'codexrelay.exe'));
 const secondary=launchGui(duplicateRoot,9259);children.push(secondary);assert.equal(await exited(secondary,15000),0);
 await page.waitForFunction(()=>document.visibilityState==='visible');assert.equal(primary.exitCode,null);
 const result={ok:true,version,area,checks:['real NSIS refuses live delegation without stopping it','upgrade 0.2.0 and stray 0.2.1 GUI/MCP processes','old directory reused by default','old side executable/runtime retired with data preserved','both shortcuts unified','MCP config and user rules preserved','provider/backend/enabled state preserved','new MCP reports current version','second GUI copy exits and shows first window']};
 fs.writeFileSync(path.join(root,'artifacts/evidence',version+'-installer-upgrade.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{
 for(const browser of browsers)await browser.close();
 for(const child of children)if(child.exitCode===null)child.kill();
}
