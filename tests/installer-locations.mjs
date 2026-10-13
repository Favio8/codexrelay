// Real NSIS first install and recovery of a registration pointing to the data folder.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
const root=process.cwd(),version=JSON.parse(fs.readFileSync('package.json','utf8')).version;
const installer=path.join(root,'src-tauri/target/release/bundle/nsis',`CodexRelay_${version}_x64-setup.exe`);
const results=[];
for(const mode of ['fresh','data-folder']){
 const area=fs.mkdtempSync(path.join(root,'.tmp','installer-'+mode+'-')),state=path.join(area,'Local/CodexRelay'),target=path.join(area,'Local/Programs/CodexRelay');
 const env={...process.env,CODEXRELAY_INSTALL_TEST_ROOT:area,CODEXRELAY_HOME:state,CODEX_HOME:path.join(area,'codex')};delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
 let originalConfig,originalRules;
 if(mode==='data-folder'){
  fs.mkdirSync(state,{recursive:true});fs.copyFileSync(path.join(root,'.tmp/upgrade-baseline/0.2.1.exe'),path.join(state,'codexrelay.exe'));fs.cpSync(path.join(root,'runtime'),path.join(state,'runtime'),{recursive:true});
  fs.cpSync(path.join(root,'.tmp/upgrade-baseline/0.2.1-python'),path.join(state,'runtime/Lib/site-packages/codexrelay'),{recursive:true});
  const setup=spawnSync(path.join(state,'runtime/python.exe'),['-I','-B','-X','utf8','-c',`from codexrelay.service import Service;from codexrelay.integration import install;s=Service();s.save_provider(name='keep',kind='custom',base_url='http://localhost:1',flash_model='fixture',pro_model='fixture');install(s.store)`],{env,windowsHide:true,encoding:'utf8'});assert.equal(setup.status,0,setup.stderr);
  originalConfig=fs.readFileSync(path.join(env.CODEX_HOME,'config.toml'),'utf8');originalRules=fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md'));
  const key='Software\\CodexRelay\\InstallerTests\\'+path.basename(area),q=s=>"'"+s.replaceAll("'","''")+"'";
  const code=`$r=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryView]::Registry64);$k=$r.CreateSubKey(${q(key+'\\Product')});$k.SetValue('',${q(state)});$k.Dispose();$k=$r.CreateSubKey(${q(key+'\\Uninstall')});$k.SetValue('DisplayVersion','0.2.1');$k.SetValue('InstallLocation',${q('"'+state+'"')});$k.Dispose();$r.Dispose()`;
  const register=spawnSync(path.join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe'),['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(code,'utf16le').toString('base64')],{env,windowsHide:true,encoding:'utf8'});assert.equal(register.status,0,register.stderr);
 }
 const setup=spawn(installer,['/S','/UPDATE'],{env,windowsHide:true,stdio:'ignore'});
 const exit=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{setup.kill();reject(new Error('installer timeout'));},90000);setup.once('exit',code=>{clearTimeout(timer);resolve(code);});setup.once('error',reject);});assert.equal(exit,0);
 assert(fs.existsSync(path.join(target,'codexrelay.exe')));assert(!fs.existsSync(path.join(state,'codexrelay.exe')));assert(fs.existsSync(path.join(state,'relay.db')));
 const report=JSON.parse(fs.readFileSync(path.join(state,'upgrades/last-upgrade.json'),'utf8'));assert(report.finished);assert.equal(report.install_directory,target);
 if(mode==='data-folder'){
  assert.equal(report.updated_mcp_configs.length,1);
  const config=fs.readFileSync(path.join(env.CODEX_HOME,'config.toml'),'utf8');assert(config.includes(target.replaceAll('\\','\\\\')));assert(!config.includes(state.replaceAll('\\','\\\\')+'\\\\runtime'));
  assert.deepEqual(fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md')),originalRules);
  assert(originalConfig.includes(state.replaceAll('\\','\\\\')));
 }else assert(!fs.existsSync(path.join(env.CODEX_HOME,'config.toml')),'fresh install must not create unsolicited Codex registration');
 results.push({mode,area,ok:true});
}
const report={ok:true,version,checks:['fresh default uses Programs and keeps state separate','data-folder installation migrated with owned MCP path and user rules preserved'],results};
fs.writeFileSync(path.join(root,'artifacts/evidence',version+'-installer-locations.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
