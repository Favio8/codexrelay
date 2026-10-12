import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const deadline=Date.now()+20000;
while(true){try{await fetch('http://127.0.0.1:9224/json/version');break;}catch(error){if(Date.now()>deadline)throw error;await new Promise(resolve=>setTimeout(resolve,200));}}
const browser=await chromium.connectOverCDP('http://127.0.0.1:9224');
try{
 const page=browser.contexts()[0].pages()[0];
 await page.getByText('管理服务已连接',{exact:true}).waitFor();
 assert(await page.getByRole('heading',{name:'让合适的任务，交给合适的智能体。'}).isVisible());
 const snapshot=await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('relay_request',{method:'snapshot',params:{}}));
 assert(snapshot.ok);
 assert.equal(snapshot.data.enabled,false);
 await page.screenshot({path:'docs/desktop-preview.png',fullPage:true});
 console.log('Installed native desktop window and bundled Python IPC: PASS');
}finally{await browser.close();}
