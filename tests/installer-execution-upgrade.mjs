// Actual 0.2.2 or 0.3.1 -> current in-place NSIS upgrade, isolated registry/state/Codex config.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import crypto from 'node:crypto';
const root=process.cwd(),version=JSON.parse(fs.readFileSync('package.json','utf8')).version;
const baseline=process.env.CODEXRELAY_UPGRADE_FROM||'0.2.2';assert(['0.2.2','0.3.1','0.4.0'].includes(baseline));
const area=fs.mkdtempSync(path.join(root,'.tmp',`upgrade-${baseline}-中文 spaces-`));
const target=path.join(area,'Local/Programs/CodexRelay'),state=path.join(area,'Local/CodexRelay');
const env={...process.env,CODEXRELAY_INSTALL_TEST_ROOT:area,CODEXRELAY_HOME:state,CODEX_HOME:path.join(area,'codex')};delete env.OPENAI_API_KEY;delete env.DEEPSEEK_API_KEY;
const installer=path.join(root,'src-tauri/target/release/bundle/nsis',`CodexRelay_${version}_x64-setup.exe`);
const baselineInstaller=path.join(root,'src-tauri/target/release/bundle/nsis',`CodexRelay_${baseline}_x64-setup.exe`);
const wait=child=>new Promise((resolve,reject)=>{const timer=setTimeout(()=>{child.kill();reject(new Error('installer timeout'));},120000);child.once('exit',code=>{clearTimeout(timer);resolve(code);});child.once('error',reject);});
const install=(executable=installer)=>wait(spawn(executable,['/S','/UPDATE'],{env,windowsHide:true,stdio:'ignore'}));
// Bootstrap with the actual published-baseline installer, without swapping a runtime tree.
assert.equal(await install(baselineInstaller),0);
const python=path.join(target,'runtime/python.exe');
const run=code=>{const result=spawnSync(python,['-I','-B','-X','utf8','-c',code],{env,windowsHide:true,encoding:'utf8'});assert.equal(result.status,0,result.stderr);return result.stdout;};
assert.equal(run('import codexrelay;print(codexrelay.__version__)').trim(),baseline);
run(`from codexrelay.service import Service\nfrom codexrelay.integration import install\ns=Service();s.save_provider(name='Preserved ${baseline} provider',base_url='http://localhost:1',flash_model='fixture',pro_model='fixture');s.store.set_setting('enabled',True)\nt=s.store.admit('legacy single run',${JSON.stringify(area)},'readonly','flash');s.store.update_task(t['id'],status='failed',finished_at=1,error='historical fixture');s.store.set_setting('enabled',False);s.store.set_setting('saved_custom','preserve-me');install(s.store)\n${baseline==='0.2.2'?"with s.store.connect() as db: db.execute('PRAGMA user_version=1')":''}\n`);
const historical=JSON.parse(run("import json;from codexrelay.state import Store;print(json.dumps([dict(status=t['status'],group=t.get('group_id'),checkpoint=t.get('checkpoint')) for t in Store().snapshot()['tasks']]))"));
const originalRules=fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md'));
const gui=spawn(path.join(target,'codexrelay.exe'),[],{env,windowsHide:true,stdio:'ignore'});
const engine=spawn(python,['-I','-B','-X','utf8','-m','codexrelay','mcp'],{env,cwd:area,windowsHide:true});engine.stderr.resume();engine.stdout.resume();
engine.stdin.write(JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'upgrade-test',version:'1'}}})+'\n');
await new Promise(resolve=>setTimeout(resolve,1500));
assert.equal(gui.exitCode,null,'0.2.2 GUI must still be alive before upgrade; run GUI fixtures serially');
assert.equal(engine.exitCode,null,'0.2.2 MCP must still be alive before upgrade');
assert.equal(await install(),0);
await new Promise(resolve=>setTimeout(resolve,300));
assert.notEqual(gui.exitCode,null,'old GUI must exit');assert.notEqual(engine.exitCode,null,'idle old MCP must exit');
const check=JSON.parse(run(`import json\nfrom codexrelay import __version__\nfrom codexrelay.state import Store\ns=Store();d=s.snapshot()\nprint(json.dumps(dict(version=__version__,enabled=d['enabled'],providers=[p['name'] for p in d['providers']],custom=s.setting('saved_custom'),legacy=[dict(status=t['status'],group=t.get('group_id'),checkpoint=t.get('checkpoint')) for t in d['tasks']],defaults=d['execution_defaults'])))`));
assert.equal(check.version,version);assert.equal(check.enabled,false);assert.equal(check.custom,'preserve-me');
const installedProtocol=JSON.parse(run(`import asyncio,json,os,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from codexrelay.state import Store
async def verify():
 workspace=Path(os.environ['CODEXRELAY_INSTALL_TEST_ROOT'])/'installed-client-workspace';workspace.mkdir()
 params=StdioServerParameters(command=sys.executable,args=['-I','-B','-X','utf8','-m','codexrelay','mcp'],cwd=str(workspace),env=dict(os.environ))
 async with stdio_client(params) as (reader,writer),ClientSession(reader,writer) as client:
  await client.initialize();listing=await client.list_tools();reply=await client.call_tool('ping',{})
  ping=json.loads(reply.content[0].text)
  assert {t.name for t in listing.tools}=={t['name'] for t in ping['server_capabilities']['tools']}
  with Store().connect() as db: schema=db.execute('PRAGMA user_version').fetchone()[0]
  print(json.dumps(dict(version=ping['version'],enabled=ping['enabled'],tools=len(listing.tools),schema=schema,source='actual installed stdio, no model requests')))
asyncio.run(verify())`));
assert.equal(installedProtocol.version,version);assert.equal(installedProtocol.enabled,false);assert.equal(installedProtocol.schema,5);assert.equal(installedProtocol.tools,31);
assert.deepEqual(check.providers,[`Preserved ${baseline} provider`]);assert.deepEqual(check.legacy,historical);
if(baseline==='0.2.2'){assert.equal(check.legacy[0].group,null);assert.equal(check.legacy[0].checkpoint,null);}
else{assert(check.legacy[0].group);assert(check.legacy[0].checkpoint);}
assert.equal(check.defaults.max_auto_continuations,3);assert.deepEqual(fs.readFileSync(path.join(env.CODEX_HOME,'AGENTS.md')),originalRules);
const upgrade=JSON.parse(fs.readFileSync(path.join(state,'upgrades/last-upgrade.json')));assert(upgrade.finished);assert.equal(upgrade.install_directory,target);
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const verified=[];
function verifySources(source,destination){for(const entry of fs.readdirSync(source,{withFileTypes:true})){if(entry.isDirectory()&&entry.name!=='__pycache__')verifySources(path.join(source,entry.name),path.join(destination,entry.name));else if(entry.isFile()&&/\.(py|mjs|json)$/.test(entry.name)){const a=path.join(source,entry.name),b=path.join(destination,entry.name);assert.equal(hash(a),hash(b),b);verified.push({path:b,sha256:hash(b)});}}}
verifySources(path.join(root,'engine/codexrelay'),path.join(target,'runtime/Lib/site-packages/codexrelay'));
verifySources(path.join(root,'vendor/deepseek-as-subagent/src/deepseek_mcp'),path.join(target,'runtime/Lib/site-packages/deepseek_mcp'));
const builtImage=fs.readFileSync(path.join(root,'src-tauri/target/release/codexrelay.exe'));
const marker=Buffer.from('TAURI_BUNDLE_TYPE_VAR_UNK'),patch=Buffer.from('TAURI_BUNDLE_TYPE_VAR_NSS');
const markerIndex=builtImage.indexOf(marker);assert(markerIndex>=0&&builtImage.indexOf(marker,markerIndex+1)===-1,'one official Tauri bundle marker expected');
const packagedImage=Buffer.from(builtImage);patch.copy(packagedImage,markerIndex);
assert.deepEqual(fs.readFileSync(path.join(target,'codexrelay.exe')),packagedImage,'installed image must equal compiled image with only the NSIS bundle marker patch');
assert.equal(hash(path.join(target,'runtime/pi/bridge.mjs')),hash(path.join(root,'runtime/pi/bridge.mjs')));
const report={ok:true,from:baseline,to:version,actualNsis:true,fixture:area,installedProtocol,verified_source_files:verified,native_bundle_patch:{offset:markerIndex,from:'TAURI_BUNDLE_TYPE_VAR_UNK',to:'TAURI_BUNDLE_TYPE_VAR_NSS',compiled_sha256:hash(path.join(root,'src-tauri/target/release/codexrelay.exe')),installed_sha256:hash(path.join(target,'codexrelay.exe'))},checks:['idle old GUI and MCP exited','same-directory upgrade','disabled switch/provider/custom settings/history preserved','schema migration and new defaults','legacy groups/checkpoints preserved without invented identity','Codex managed rules preserved','all installed Python/bridge/case JSON sources match delivery source hashes','installed native image matches build with the exact official Tauri NSIS marker patch','bundled Pi bridge matches build','actual installed stdio capability enumeration and schema 5']};
const evidence=path.join(root,'artifacts/evidence',version);fs.mkdirSync(evidence,{recursive:true});
fs.writeFileSync(path.join(evidence,`installer-${baseline}.json`),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
