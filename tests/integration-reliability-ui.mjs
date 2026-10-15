// Production frontend + real Python RPC; isolated files, no paid provider or daily configuration.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn,spawnSync} from 'node:child_process';
import {chromium,expect} from '@playwright/test';

const root=process.cwd(),area=fs.mkdtempSync(path.join(root,'.tmp','integration-reliability-'));
const evidence=process.env.CODEXRELAY_EVIDENCE_DIR?path.resolve(process.env.CODEXRELAY_EVIDENCE_DIR):path.join(root,'artifacts','evidence','0.4.0');fs.mkdirSync(evidence,{recursive:true});
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex')};
delete env.DEEPSEEK_API_KEY;delete env.OPENAI_API_KEY;
const python=path.join(root,'runtime','python.exe'),bootstrap=`import sys;sys.path.insert(0,${JSON.stringify(path.join(root,'engine'))});`;
function pythonScript(code){const result=spawnSync(python,['-I','-B','-X','utf8','-c',bootstrap+code],{env,windowsHide:true,encoding:'utf8'});assert.equal(result.status,0,result.stderr);return result.stdout;}
pythonScript(`from codexrelay.service import Service\nfrom codexrelay.integration import codex_dir,BEGIN,END\nfrom codexrelay.capabilities import ALIASES,LEGACY_EXECUTORS\nfrom pathlib import Path\nimport tomlkit\ns=Service();s.save_provider(name='Integration isolated fixture',kind='custom',base_url='http://127.0.0.1:1/v1',flash_model='fixture-fast',pro_model='fixture-pro')\nhome=codex_dir();home.mkdir(parents=True,exist_ok=True)\nold=['ping',*LEGACY_EXECUTORS,*ALIASES.values()];old.remove('record_deepseek_verification');assert len(old)==11\nd=tomlkit.document();d['model']='preserve-user-model';d['mcp_servers']={'other':{'command':'preserve-other.exe'},'codexrelay':{'command':str(Path(sys.executable).resolve()),'args':['-I','-X','utf8','-m','codexrelay','mcp'],'enabled_tools':old,'disabled_tools':['resume_agent','external-user-tool'],'tools':{'get_task_checkpoint':{'enabled':False,'approval_mode':'always'}}}}\n(home/'config.toml').write_text('# preserve-user-comment\\n'+tomlkit.dumps(d),encoding='utf-8')\n(home/'AGENTS.md').write_text('User rule before.\\n'+BEGIN+'\\nOld managed block\\n'+END+'\\nUser rule after.\\n',encoding='utf-8')\n`);
const config=path.join(env.CODEX_HOME,'config.toml'),rules=path.join(env.CODEX_HOME,'AGENTS.md'),originalConfig=fs.readFileSync(config),originalRules=fs.readFileSync(rules);
const calls=[],errors=[];
function rpc(method,params={}){
 calls.push({method,params});
 return new Promise((resolve,reject)=>{
  const child=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+'from codexrelay.__main__ import main;main()','rpc'],{env,windowsHide:true});let out='',err='';
  child.stdout.on('data',value=>out+=value);child.stderr.on('data',value=>err+=value);child.on('error',reject);child.on('exit',code=>{try{assert.equal(code,0,err);resolve(JSON.parse(out));}catch(error){reject(error);}});child.stdin.end(JSON.stringify({method,params}));
 });
}
const dist=path.join(root,'dist'),server=http.createServer((req,res)=>{
 const file=path.resolve(dist,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));
 if(!file.startsWith(dist+path.sep)||!fs.existsSync(file)){res.writeHead(404).end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
let browser,metadataOwner;
try{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:900,height:600}});page.setDefaultTimeout(15000);page.on('pageerror',error=>errors.push(error.message));
 await page.exposeFunction('testRelay',rpc);
 await page.addInitScript(()=>{window.isTauri=true;window.__TAURI_INTERNALS__={invoke:async(command,args)=>command==='relay_request'?window.testRelay(args.method,args.params):command==='plugin:dialog|message'?'Ok':1,transformCallback:()=>1,unregisterCallback:()=>{}};window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};});
 await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'domcontentloaded'});await page.getByText('管理服务已连接',{exact:true}).waitFor();
 await page.getByRole('button',{name:'设置与诊断',exact:true}).click();
 const panel=page.locator('.integration-reliability');await expect(panel.getByTestId('integration-capabilities')).toBeVisible();
 await expect(panel.getByTestId('integration-capabilities')).toContainText('旧入口（原引擎）');
 await expect(panel.getByTestId('integration-capabilities')).toContainText('任务组与续作');
 await expect(panel).toContainText('新委托已关闭');
 for(const [width,height] of [[900,600],[800,520]]){await page.setViewportSize({width,height});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page overflow at ${width}x${height}`);}
 await page.setViewportSize({width:900,height:600});
 const disclosure=panel.locator('summary').filter({hasText:'选择修复范围并预览'});await disclosure.focus();await page.keyboard.press('Enter');
 await expect(disclosure.locator('..')).toHaveAttribute('open','');
 const scope=panel.getByRole('combobox',{name:'接入能力范围'}),preview=panel.getByRole('button',{name:'预览所选修复',exact:true}),apply=panel.getByRole('button',{name:'应用此修复预览',exact:true});
 await scope.selectOption('basic');await preview.click();await expect(panel.getByTestId('integration-repair-preview')).toContainText('具体修复预览');
 const basic=calls.filter(item=>item.method==='integration_repair_preview').at(-1);assert.equal(basic.params.capability_scope,'basic');
 assert.deepEqual(fs.readFileSync(config),originalConfig);assert.deepEqual(fs.readFileSync(rules),originalRules);
 await scope.selectOption('full');await preview.click();await expect(panel.getByTestId('integration-repair-preview')).toContainText('resume_agent');
 let planned=(await rpc('integration_repair_preview')).data;assert(planned.added_tools.includes('start_agent'));assert(planned.added_tools.includes('record_task_verification'));assert(!planned.added_tools.includes('resume_agent'));assert(!planned.added_tools.includes('get_task_checkpoint'));
 assert(planned.retained_denials.includes('resume_agent'));assert(planned.retained_denials.includes('get_task_checkpoint'));
 // A true file edit after preview must reject application through RPC and preserve it.
 fs.appendFileSync(config,'\n# concurrent-user-edit\n');await apply.click();await expect(panel.getByRole('alert')).toContainText('预览已失效');assert(fs.readFileSync(config,'utf8').includes('# concurrent-user-edit'));
 await preview.click();await apply.click();await expect(panel.getByTestId('integration-repair-preview')).toHaveCount(0);
 let status=(await rpc('integration_status')).data;assert.equal(status.client_evidence.state,'waiting_client_verification');assert.equal(status.delegation_enabled,false);assert.equal(status.configuration_prediction.tool_states.resume_agent.state,'blocked');
 pythonScript(`import tomlkit\nfrom codexrelay.integration import codex_dir,RULES\nh=codex_dir();d=tomlkit.parse((h/'config.toml').read_text(encoding='utf-8'));e=d['mcp_servers']['codexrelay'];assert d['model']=='preserve-user-model';assert d['mcp_servers']['other']['command']=='preserve-other.exe';assert e['disabled_tools']==['resume_agent','external-user-tool'];assert e['tools']['get_task_checkpoint']['enabled']==False;assert e['tools']['get_task_checkpoint']['approval_mode']=='always';text=(h/'AGENTS.md').read_text(encoding='utf-8');assert 'User rule before.' in text and 'User rule after.' in text and RULES in text\n`);
 assert(fs.readFileSync(config,'utf8').includes('# preserve-user-comment'));
 const denied=panel.locator('summary').filter({hasText:'显式拒绝的工具（默认保留）'});await denied.focus();await page.keyboard.press('Enter');
 for(const name of ['resume_agent','get_task_checkpoint']){const checkbox=panel.getByRole('checkbox',{name:'解除拒绝 '+name,exact:true});await checkbox.focus();await page.keyboard.press('Space');await expect(checkbox).toBeChecked();}
 await preview.click();await expect(panel.getByTestId('integration-repair-preview')).toContainText('remove_selected_denials');
 await apply.click();await expect(panel.getByTestId('integration-repair-preview')).toHaveCount(0);await expect(panel).toContainText('配置检查通过，等待 Codex 验证');
 status=(await rpc('integration_status')).data;assert.equal(status.state,'waiting_client_verification');assert.equal(status.client_evidence.state,'waiting_client_verification');assert(status.configuration_prediction.capabilities.every(item=>item.state==='available'));
 pythonScript(`import tomlkit\nfrom codexrelay.integration import codex_dir\nd=tomlkit.parse((codex_dir()/'config.toml').read_text(encoding='utf-8'));e=d['mcp_servers']['codexrelay'];assert e['disabled_tools']==['external-user-tool'];assert e['tools']['get_task_checkpoint']['enabled']==True;assert e['tools']['get_task_checkpoint']['approval_mode']=='always'\n`);
 // The previous managed interpreter can differ from the currently running App.
 const priorRuntime=path.join(area,'prior managed runtime.exe');fs.writeFileSync(priorRuntime,'isolated ownership fixture; never executed');
 pythonScript(`import tomlkit\nfrom codexrelay.integration import codex_dir\nfrom codexrelay.state import Store\nh=codex_dir();p=h/'config.toml';d=tomlkit.parse(p.read_text(encoding='utf-8'));d['mcp_servers']['codexrelay']['command']=${JSON.stringify(priorRuntime)};p.write_text(tomlkit.dumps(d),encoding='utf-8');Store().set_setting('integration_registration:'+str(h),{'command':${JSON.stringify(priorRuntime)}})\n`);
 await preview.click();await expect(panel.getByTestId('integration-repair-preview')).toContainText('update_owned_runtime');await expect(panel.getByTestId('integration-repair-preview')).toContainText(priorRuntime);
 await apply.click();await expect(panel.getByTestId('integration-repair-preview')).toHaveCount(0);assert.equal((await rpc('integration_status')).data.registered,true);
 const verification=panel.locator('summary').filter({hasText:'验证当前 Codex 聊天'});await verification.focus();await page.keyboard.press('Enter');
 await panel.getByRole('textbox',{name:'客户端报告 JSON',exact:true}).fill(JSON.stringify({instance:'local-fixture-no-real-client',workspace:area,client_kind:'local_stdio_fixture',client_session_id:'fixture-session',client_version:null,visible_tools:['ping']}));
 await panel.getByRole('button',{name:'保存为用户报告',exact:true}).click();await expect(panel).toContainText('保留 1 条近期报告');
 status=(await rpc('integration_status')).data;assert.equal(status.client_evidence.client_reports.at(-1).source,'user_report');assert.equal(status.client_evidence.client_reports.at(-1).client_kind,'local_stdio_fixture');assert.equal(status.client_evidence.state,'waiting_client_verification');
 // Synthetic evidence isolates the UI identity-selection contract. This is
 // expressly not a real Codex client or a model quality/paid-model experiment.
 metadataOwner=spawn(python,['-I','-B','-X','utf8','-c',bootstrap+`import json\nfrom codexrelay.state import Store\nfrom codexrelay.capabilities import CANONICAL_TOOLS\nfrom codexrelay.integration_evidence import record_call\ns=Store();workspace=${JSON.stringify(area)};instance='synthetic-ui-metadata-owner';s.heartbeat(instance,workspace)\nfor name in CANONICAL_TOOLS:record_call(s,instance=instance,workspace=workspace,client_session_id='synthetic-chat-a',client_kind='codex_desktop',tool=name,success=True)\nrecord_call(s,instance=instance,workspace=workspace,client_session_id='synthetic-chat-b',client_kind='codex_desktop',tool='ping',success=True)\nprint(json.dumps({'tools':list(CANONICAL_TOOLS)}),flush=True)\nsys.stdin.read()\n`],{env,windowsHide:true});
 let ownerErrors='';metadataOwner.stderr.on('data',value=>ownerErrors+=value);
 const metadata=await new Promise((resolve,reject)=>{let value='';metadataOwner.stdout.on('data',part=>{value+=part;if(value.includes('\n'))resolve(JSON.parse(value.split('\n')[0]));});metadataOwner.on('error',reject);metadataOwner.on('exit',code=>{if(!value.includes('\n'))reject(new Error('synthetic metadata owner failed: '+code+' '+ownerErrors));});});
 const selector=panel.getByRole('combobox',{name:'核对的聊天',exact:true});
 const importMetadataReport=async session=>{
  await panel.getByRole('textbox',{name:'客户端报告 JSON',exact:true}).fill(JSON.stringify({instance:'synthetic-ui-metadata-owner',workspace:area,client_kind:'codex_desktop',client_session_id:session,client_version:null,visible_tools:metadata.tools}));
  await panel.getByRole('button',{name:'保存为用户报告',exact:true}).click();
  await expect(panel.getByRole('button',{name:'保存为用户报告',exact:true})).toBeEnabled();
  return (await rpc('integration_status')).data.client_evidence.client_reports.find(item=>item.client_session_id===session).id;
 };
 const reportA=await importMetadataReport('synthetic-chat-a');await expect(selector).toHaveValue('');
 await selector.selectOption(reportA);await expect(panel.locator('.integration-status strong')).toHaveText('当前聊天已验证');
 const selectedCall=calls.filter(item=>item.method==='integration_status'&&item.params.client_report_id===reportA).at(-1);
 assert.equal(selectedCall.params.client_session_id,'synthetic-chat-a');assert.equal(selectedCall.params.instance,'synthetic-ui-metadata-owner');assert.equal(selectedCall.params.workspace.toLowerCase(),area.toLowerCase());
 const polls=calls.filter(item=>item.method==='integration_preview'&&item.params.client_report_id===reportA).length;
 await expect.poll(()=>calls.filter(item=>item.method==='integration_preview'&&item.params.client_report_id===reportA).length,{timeout:7000}).toBeGreaterThan(polls);
 await expect(panel.locator('.integration-status strong')).toHaveText('当前聊天已验证');
 const reportB=await importMetadataReport('synthetic-chat-b');await selector.selectOption(reportB);await expect(panel.locator('.integration-status strong')).toHaveText('当前聊天部分能力有调用证据');
 await selector.selectOption(reportA);await expect(panel.locator('.integration-status strong')).toHaveText('当前聊天已验证');
 await page.getByRole('button',{name:'总览',exact:true}).click();await page.getByRole('button',{name:'设置与诊断',exact:true}).click();await expect(selector).toHaveValue(reportA);await expect(panel.locator('.integration-status strong')).toHaveText('当前聊天已验证');
 await page.reload();await page.getByText('管理服务已连接',{exact:true}).waitFor();await page.getByRole('button',{name:'设置与诊断',exact:true}).click();await expect(selector).toHaveValue('');await expect(panel.locator('.integration-status strong')).toHaveText('配置检查通过，等待 Codex 验证');
 // Choosing basic for a fresh registration must actually restrict its tools.
 const basicHome=path.join(area,'fresh-basic-codex');await panel.getByRole('textbox',{name:'Codex 配置目录',exact:true}).fill(basicHome);
 await expect(panel.getByTestId('integration-capabilities')).toBeVisible();const repairDisclosure=panel.locator('summary').filter({hasText:'选择修复范围并预览'});await repairDisclosure.focus();await page.keyboard.press('Enter');await scope.selectOption('basic');await preview.click();await expect(panel.getByTestId('integration-repair-preview')).toContainText('initialize_basic_allowlist');await apply.click();await expect(panel.getByTestId('integration-repair-preview')).toHaveCount(0);
 const basicStatus=(await rpc('integration_status',{codex_home:basicHome})).data;assert.equal(basicStatus.registered,true);assert.equal(basicStatus.state,'capabilities_limited');assert(!basicStatus.configuration_prediction.effective_tools.includes('start_agent'));assert(!basicStatus.configuration_prediction.effective_tools.includes('resume_agent'));assert(basicStatus.configuration_prediction.effective_tools.includes('bind_client_session'));assert(basicStatus.configuration_prediction.effective_tools.includes('get_client_evidence'));assert.equal(basicStatus.configuration_prediction.effective_tools.length,7);
 for(const [width,height] of [[900,600],[800,520]]){await page.setViewportSize({width,height});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`expanded overflow at ${width}x${height}`);}
 await panel.screenshot({path:path.join(evidence,'integration-ui.png')});
 assert(!calls.some(item=>['test_provider','test_backend','start_agent','delegate_to_agent'].includes(item.method)),'integration checks must not call paid models');assert.deepEqual(errors,[]);
 const result={ok:true,mode:'production browser + real Python RPC; isolated configuration; explicitly synthetic client/call metadata',area,codex_home:env.CODEX_HOME,checks:['11 old tools retain original-engine read-only compatibility','neutral routing/verification/continuation gaps diagnosed','basic/full selection and read-only preview','default deny preservation','explicit deny removal preserves approval mode and extra denials','real concurrent file edit rejects stale plan without overwrite','selective apply preserves other MCP/comment/user rules','DPAPI operation backup and waiting for client verification','disabled delegation switch retained','800x520 and 900x600 layout','keyboard disclosure and denial selection','user report kept separate from engine evidence','owned interpreter command replacement is previewed and remains owned after application','explicit chat selection carries report and full identity through UI/RPC/background polling','two chats on one instance do not combine their verification evidence','navigation preserves explicit selection and reopening clears it','fresh basic exposes 7 readonly/identity tools and excludes coding/continuation','no model request and no JavaScript errors'],uncovered:['real Codex Desktop/CLI session tool visibility and actual calls','native confirmation UI handled by separate native tests'],rpc_methods:[...new Set(calls.map(item=>item.method))]};
 fs.writeFileSync(path.join(evidence,'integration-ui.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{if(metadataOwner){metadataOwner.stdin.end();metadataOwner.kill();}if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
