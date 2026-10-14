// Pi owns scheduling; the parent owns model transport, budgets and workspace tools.
// No credentials, filesystem tools, plugins or provider SDKs execute in this process.
import { Agent } from '@earendil-works/pi-agent-core';
import { AssistantMessageEventStream } from '@earendil-works/pi-ai';
import readline from 'node:readline';

const waiting = new Map();
let sequence = 0, agent, preparedResponse;
const write = value => process.stdout.write(JSON.stringify(value) + '\n');
const request = (op, payload) => new Promise((resolve, reject) => {
 const id = ++sequence; waiting.set(id, {resolve,reject}); write({id,op,...payload});
});
function text(content) {
 return typeof content === 'string' ? content : (content||[]).filter(c=>c.type==='text').map(c=>c.text).join('\n');
}
function messages(context) {
 return context.messages.filter(m=>m.role!=='system').map(m=>{
  if(m.role==='assistant') return m.relayRaw;
  if(m.role==='toolResult') return {role:'tool',tool_call_id:m.toolCallId,content:text(m.content)};
  return {role:'user',content:text(m.content)};
 });
}
function streamFn(model,context) {
 const stream = new AssistantMessageEventStream();
 void (async()=>{
  const usage={input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
  let result={role:'assistant',content:[],api:'openai-completions',provider:'relay',model:model.id,usage,timestamp:Date.now(),stopReason:'stop'};
  try {
   const response=preparedResponse;
   preparedResponse=undefined;
   const raw=response.message;
   result={...result,relayRaw:raw,content:[...(raw.content?[{type:'text',text:raw.content}]:[]),...(raw.tool_calls||[]).map(c=>({type:'toolCall',id:c.id,name:c.function.name,arguments:JSON.parse(c.function.arguments)}))],stopReason:raw.tool_calls?.length?'toolUse':'stop'};
   stream.push({type:'done',reason:result.stopReason,message:result});
  } catch(error) {
   result={...result,stopReason:'error',errorMessage:String(error)};
   stream.push({type:'error',reason:'error',error:result});
  }
  stream.end(result);
 })();
 return stream;
}
async function start(config) {
 const tools=config.tools.map(({function:f})=>({name:f.name,label:f.name,description:f.description,parameters:f.parameters,
  execute:async(id,args)=>({content:[{type:'text',text:(await request('tool',{tool_id:id,name:f.name,args})).text}],details:{}})}));
 const convertRaw = raw => {
  if(raw.role==='assistant')return {role:'assistant',relayRaw:raw,content:[...(raw.content?[{type:'text',text:raw.content}]:[]),...(raw.tool_calls||[]).map(c=>({type:'toolCall',id:c.id,name:c.function.name,arguments:JSON.parse(c.function.arguments)}))],api:'openai-completions',provider:'relay',model:config.model,usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},timestamp:Date.now(),stopReason:raw.tool_calls?.length?'toolUse':'stop'};
  if(raw.role==='tool')return {role:'toolResult',toolCallId:raw.tool_call_id,toolName:'relay',content:[{type:'text',text:raw.content}],isError:raw.content.startsWith('ERROR:'),timestamp:Date.now()};
  return {role:'user',content:[{type:'text',text:raw.content}],timestamp:Date.now()};
 };
 agent=new Agent({initialState:{systemPrompt:config.system,model:{id:config.model,api:'openai-completions',provider:'relay',contextWindow:config.context_window||0,maxTokens:config.max_output_tokens||16384},tools},streamFn,toolExecution:'sequential',steeringMode:'all',
  prepareRequest:async({context})=>{
   preparedResponse=await request('model',{messages:messages(context)});
   const effective=[...context.messages.filter(m=>m.role==='system'),...preparedResponse.context.filter(m=>m.role!=='system').map(convertRaw)];
   agent.state.messages=effective;
   return {context:{...context,messages:effective}};
  }});
 agent.subscribe(event=>{if(event.type==='message_start'&&event.message.role==='user')write({op:'user',text:text(event.message.content)});});
 await agent.prompt(config.task);
 while(true){
  const reply=await request('finalize',{});
  if(!reply.messages?.length)break;
  agent.clearAllQueues();
  await agent.prompt(reply.messages.map(value=>({role:'user',content:[{type:'text',text:value}],timestamp:Date.now()})));
 }
 const last=[...agent.state.messages].reverse().find(m=>m.role==='assistant');
 write({op:'done',text:text(last?.content),error:agent.state.errorMessage||last?.errorMessage});
}
const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
lines.on('line',line=>{
 const value=JSON.parse(line);
 if(value.op==='start')void start(value).catch(error=>write({op:'done',error:String(error)}));
 else if(value.op==='steer')agent?.steer({role:'user',content:[{type:'text',text:value.text}],timestamp:Date.now()});
 else if(value.op==='cancel'){agent?.clearAllQueues();agent?.abort();}
 else if(waiting.has(value.id)){const entry=waiting.get(value.id);waiting.delete(value.id);value.error?entry.reject(new Error(value.error)):entry.resolve(value);}
});
lines.on('close',()=>{agent?.abort();process.exit(0);});
