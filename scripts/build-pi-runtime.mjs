// Build a pinned Pi SDK bridge. Runtime and licenses are bundled for end users.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { build } from 'esbuild';
const root=process.cwd(), output=path.join(root,'runtime','pi');
const nodeVersion=JSON.parse(fs.readFileSync(path.join(root,'vendor/pi-agent/node-distribution.json'),'utf8')).version;
fs.mkdirSync(output,{recursive:true});
const result=await build({entryPoints:['engine/codexrelay/backends/pi_bridge.mjs'],outfile:path.join(output,'bridge.mjs'),
 bundle:true,format:'esm',platform:'node',target:'node24',metafile:true,
 nodePaths:[path.join(root,'vendor/pi-agent/node_modules')],
 banner:{js:"import { createRequire as relayCreateRequire } from 'node:module'; const require = relayCreateRequire(import.meta.url);"}});
const nodeLicense=path.join(output,'LICENSE');
if(!fs.existsSync(nodeLicense))throw new Error('Node LICENSE must accompany the build interpreter');
const packages=new Set();
for(const input of Object.keys(result.metafile.inputs)){
 let directory=path.dirname(path.resolve(input));
 while(directory!==path.dirname(directory)){
  if(fs.existsSync(path.join(directory,'package.json'))){packages.add(directory);break;}
  directory=path.dirname(directory);
 }
}
let notices='Node '+nodeVersion+'\n'+fs.readFileSync(nodeLicense,'utf8');
const dependencies=[];
for(const directory of packages){
 const pkg=JSON.parse(fs.readFileSync(path.join(directory,'package.json'),'utf8'));
 dependencies.push({name:pkg.name,version:pkg.version,license:pkg.license});
 const license=fs.readdirSync(directory).find(name=>/^licen[cs]e(?:\.|$)/i.test(name));
 const licensePath=license?path.join(directory,license):pkg.name?.startsWith('@earendil-works/pi-')?path.join(root,'vendor/pi-agent/LICENSE'):null;
 if(!licensePath)throw new Error('Missing bundled license: '+pkg.name);
 notices+='\n\n'+pkg.name+' '+pkg.version+'\n'+fs.readFileSync(licensePath,'utf8');
}
fs.writeFileSync(path.join(output,'THIRD_PARTY_LICENSES.txt'),notices);
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(path.join(output,file))).digest('hex');
fs.writeFileSync(path.join(output,'manifest.json'),JSON.stringify({backend:'pi',version:'1.2.0',node:nodeVersion,
 files:Object.fromEntries(['node.exe','bridge.mjs','THIRD_PARTY_LICENSES.txt'].map(file=>[file,digest(file)])),dependencies},null,2));
console.log(JSON.stringify({output,dependencies,bridge_bytes:fs.statSync(path.join(output,'bridge.mjs')).size}));
