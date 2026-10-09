// Adapted from coding-mobile (MIT); see bridge/clients/LICENSE.coding-mobile.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto, createHmac} = require('node:crypto');
const config = {cwd:'D:/fixture', request:'request.json', response:'response.json', token:'test', generation:'run', reconnect:true,connectorRevision:5};
const pack = value => {
  const payload = JSON.stringify({generation:'run', ...value});
  return JSON.stringify({payload,signature:createHmac('sha256','test').update(payload).digest('hex')});
};
let request = pack({seq:0,type:'idle'}), response, calls = [];
let failWrites=0,catalogHung=false;
const api = {
  async readFileAtCwd() { return {contents:request}; },
  async writeSessionFile(sid,path,text) { if(failWrites>0){failWrites--;throw Error('temporary write failure');}response=JSON.parse(JSON.parse(text).payload); return {hash:'ok'}; },
  async getAll() { if(catalogHung)return new Promise(()=>{});return [{sessionId:'local_test',cwd:'D:/fixture',title:'Codex Bridge Connector',oauthToken:'SECRET'}]; },
  async getSupportedCommands(options) {assert.ok(options && typeof options==='object' && !Array.isArray(options));return options;},
  async getContextUsageSummary() {return {totalTokens:42000,rawMaxTokens:200000,categories:[],private:'SECRET'};},
  async getSession(id) { return {sessionId:id, oauthToken:'SECRET'}; },
  async sendMessage(...args) { calls.push(args); },
  async getTranscript() {return [
    {type:'user',isSynthetic:true,uuid:'retry',message:{role:'user',content:[{type:'text',text:'internal retry'}]}},
    {type:'assistant',uuid:'answer',message:{role:'assistant',content:[{type:'thinking',thinking:'PRIVATE_THINKING'},{type:'text',text:'visible'}]}},
    {type:'result',uuid:'native-result',subtype:'success',is_error:false,num_turns:2,parent_tool_use_id:null,result:'PRIVATE_RESULT',usage:{secret:'PRIVATE_USAGE'},apiKey:'SECRET'},
    {type:'result',uuid:'nested-result',subtype:'success',is_error:false,parent_tool_use_id:'tool-id',isSidechain:true},
    {type:'result',uuid:'synthetic-result',subtype:'success',is_error:false,isSyntheticResult:true},
    {type:'result',uuid:'failure',subtype:'error_during_execution',is_error:true,num_turns:0},
  ];},
};
const window = {'claude.web':{LocalSessions:api,LocalAgentModeSessions:api}};
const context = {window,TextEncoder,Uint8Array,crypto:webcrypto,console,
  clearTimeout,setTimeout:(fn,ms)=>setTimeout(fn,ms>=9000?100:Math.min(ms,10))};
const source=fs.readFileSync(process.env.CONNECTOR_TEMPLATE || 'bridge/clients/claude/connector.js','utf8').replace('__BRIDGE_CONFIG__',JSON.stringify(config));
const waitFor = async predicate => {
  const deadline=Date.now()+3000;
  while(!predicate()) { if(Date.now()>deadline)throw Error('timeout'); await new Promise(r=>setTimeout(r,10)); }
};
(async()=>{
  let running=vm.runInNewContext(source,context,{codeGeneration:{strings:false,wasm:false}});
  await waitFor(()=>response?.connected);
  assert.equal(response.connectorRevision,config.connectorRevision);
  request=pack({type:'request',seq:1,surface:'code',method:'sendMessage',args:['local_test','hello'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===1 && response.done);
  assert.deepEqual(calls,[['local_test','hello']]);
  await new Promise(r=>setTimeout(r,100));
  assert.equal(calls.length,1,'same sequence must not execute again');
  window.__claudeMobileBridge.stop();await running;
  running=vm.runInNewContext(source,context,{codeGeneration:{strings:false,wasm:false}});
  await waitFor(()=>response?.connected);await new Promise(r=>setTimeout(r,100));
  assert.equal(calls.length,1,'same-generation reinjection must not replay the completed unexpired send');
  request=pack({type:'request',seq:2,surface:'code',method:'sendMessage',args:['local_test','expired'],expires:0});
  await waitFor(()=>response.seq===2 && response.done);
  assert.ok(response.error); assert.equal(calls.length,1);
  request=pack({type:'request',seq:3,surface:'code',method:'readFileAtCwd',args:[],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===3 && response.done); assert.ok(response.error);
  request=pack({type:'request',seq:4,surface:'code',method:'getAll',args:[],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===4 && response.done);
  assert.ok(!JSON.stringify(response).includes('SECRET'));
  request=pack({type:'request',seq:5,surface:'code',method:'getTranscript',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===5 && response.done);
  assert.equal(response.result[0].isSynthetic,true);
  assert.ok(!JSON.stringify(response).includes('PRIVATE_THINKING'));
  assert.equal(response.result[1].message.content[0].text,'visible');
  assert.deepEqual(response.result.slice(2),[
    {type:'result',uuid:'native-result',subtype:'success',is_error:false,num_turns:2,parent_tool_use_id:null},
    {type:'result',uuid:'nested-result',subtype:'success',is_error:false,parent_tool_use_id:'tool-id',isSidechain:true},
    {type:'result',uuid:'synthetic-result',subtype:'success',is_error:false,isSyntheticResult:true},
    {type:'result',uuid:'failure',subtype:'error_during_execution',is_error:true,num_turns:0},
  ]);
  assert.ok(!JSON.stringify(response).includes('PRIVATE_RESULT')&&!JSON.stringify(response).includes('PRIVATE_USAGE')&&!JSON.stringify(response).includes('SECRET'));
  failWrites=1;catalogHung=true;
  request=pack({type:'request',seq:6,surface:'code',method:'mobileCatalog',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===6 && response.done);
  assert.ok(response.connected && Array.isArray(response.result.models),'slow catalog and transient write must not stop bridge');
  catalogHung=false;
  request=pack({type:'request',seq:7,surface:'code',method:'getSession',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===7 && response.done);assert.equal(response.result.sessionId,'local_test');
  request=pack({type:'stop',seq:8});
  await waitFor(()=>response.connected===false);
  request=pack({generation:'next',type:'request',seq:1,surface:'code',method:'sendMessage',args:['local_test','must not replay'],expires:Date.now()/1000+10});
  await new Promise(r=>setTimeout(r,80));assert.equal(calls.length,1);
  request=pack({generation:'next',type:'idle',seq:0});
  await waitFor(()=>response.generation==='next' && response.connected);
  assert.equal(response.connectorRevision,config.connectorRevision,'handoff must retain the actually loaded renderer revision');
  request=pack({generation:'next',type:'request',seq:1,surface:'code',method:'sendMessage',args:['local_test','after restart'],expires:Date.now()/1000+10});
  await waitFor(()=>response.generation==='next' && response.seq===1 && response.done);
  assert.equal(calls.length,2);
  request=pack({type:'idle',seq:0});
  await new Promise(r=>setTimeout(r,80));assert.equal(response.generation,'next','old generations cannot return');
  let seq=1;
  for(const surface of ['code','cowork'])for(const args of [[],['local_test'],[{sessionId:'local_test',cwd:'D:/fixture'}]]){
    const current=++seq;request=pack({generation:'next',type:'request',seq:current,surface,method:'getSupportedCommands',args,expires:Date.now()/1000+10});
    await waitFor(()=>response.seq===current && response.done);assert.equal(response.error,undefined);assert.equal(response.result.sessionId,args.length?'local_test':undefined);
  }
  request=pack({generation:'next',type:'request',seq:++seq,surface:'code',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===seq&&response.done);
  assert.deepEqual(response.result.contextUsage,{usedTokens:42000,contextWindow:200000});
  assert.ok(!JSON.stringify(response).includes('SECRET'));
  api.getContextUsageSummary=async()=>({totalTokens:NaN,rawMaxTokens:200000});
  request=pack({generation:'next',type:'request',seq:++seq,surface:'code',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===seq&&response.done);assert.equal(response.result.contextUsage,null);
  window.__claudeMobileBridge.stop(); await running;
  assert.equal(response.connected,false);
  console.log('Desktop file connector: handshake, original session dispatch, deduplication, same-generation reinjection without replay, expiry, allowlist, redaction, restart, replay rejection, stop passed.');
})().catch(e=>{window.__claudeMobileBridge?.stop();console.error(e);process.exitCode=1;});
