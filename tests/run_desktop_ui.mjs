// Own the native fixture lifecycle; never attach this mutation test to an everyday app.
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
const root=process.cwd();
try{await fetch('http://127.0.0.1:9223/json/version');throw new Error('Port 9223 is already in use; refusing to attach to an unowned WebView.');}
catch(error){if(!String(error).includes('fetch failed'))throw error;}
const area=fs.mkdtempSync(path.join(root,'.tmp','desktop-reliability-'));
const staging=path.join(area,'app');fs.mkdirSync(staging);
fs.copyFileSync(path.join(root,'src-tauri/target/release/codexrelay.exe'),path.join(staging,'codexrelay.exe'));
fs.symlinkSync(path.join(root,'runtime'),path.join(staging,'runtime'),'junction');
const env={...process.env,CODEXRELAY_HOME:path.join(area,'state'),CODEX_HOME:path.join(area,'codex'),
 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=9223',WEBVIEW2_USER_DATA_FOLDER:path.join(area,'webview')};
delete env.OPENAI_API_KEY;delete env.DEEPSEEK_API_KEY;
process.env.CODEXRELAY_TEST_AREA=area;
const app=spawn(path.join(staging,'codexrelay.exe'),[],{env,windowsHide:true,stdio:'ignore'});
try{await import('./desktop-ui.mjs');}
finally{
 if(app.exitCode===null){const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill();await stopped;}
}
