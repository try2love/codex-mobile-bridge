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
let failWrites=0,catalogHung=false,usageCalls=[],realContextBudget=false;
const api = {
  async readFileAtCwd() { return {contents:request}; },
  async writeSessionFile(sid,path,text) { if(failWrites>0){failWrites--;throw Error('temporary write failure');}response=JSON.parse(JSON.parse(text).payload); return {hash:'ok'}; },
  async getAll() { if(catalogHung)return new Promise(()=>{});return [{sessionId:'local_test',cwd:'D:/fixture',title:'Codex Bridge Connector',oauthToken:'SECRET'}]; },
  async getSupportedCommands(options) {assert.ok(options && typeof options==='object' && !Array.isArray(options));return options;},
  async getContextUsageSummary() {usageCalls.push('summary');return {totalTokens:42000,rawMaxTokens:200000,categories:[],private:'SECRET'};},
  async getContextUsage() {usageCalls.push('full');return null;},
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
let budgetNow=0;
class FixtureDate extends Date {static now(){return realContextBudget?budgetNow:Date.now();}}
const context = {window,Date:FixtureDate,TextEncoder,Uint8Array,crypto:webcrypto,console,
  clearTimeout,setTimeout:(fn,ms)=>realContextBudget&&ms>1000&&ms<2000
    // A native timer can fire just before the wall-clock deadline. Its timeout
    // still consumes the budget and must not start another desktop request.
    ? setTimeout(()=>{budgetNow+=ms-1;fn();},10)
    : setTimeout(fn,realContextBudget&&ms>1000?ms:ms>=9000?100:Math.min(ms,10))};
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
  assert.equal(response.result.contextUsageStatus,'available');
  assert.deepEqual(usageCalls,['summary'],'valid summary must not trigger the larger getter');
  assert.ok(!JSON.stringify(response).includes('SECRET'));
  api.getContextUsageSummary=async()=>({totalTokens:NaN,rawMaxTokens:200000});
  request=pack({generation:'next',type:'request',seq:++seq,surface:'code',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===seq&&response.done);assert.equal(response.result.contextUsage,null);
  assert.equal(response.result.contextUsageStatus,'unavailable');
  for(const summary of [async()=>null,async()=>{throw Error('older CLI does not support summary');}]){
    usageCalls=[];
    api.getContextUsageSummary=async id=>{assert.equal(id,'local_test');usageCalls.push('summary');return summary();};
    api.getContextUsage=async id=>{assert.equal(id,'local_test');usageCalls.push('full');return {totalTokens:76543,rawMaxTokens:200000,categories:[{name:'PRIVATE'}],token:'SECRET'};};
    request=pack({generation:'next',type:'request',seq:++seq,surface:'code',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
    await waitFor(()=>response.seq===seq&&response.done);
    assert.deepEqual(response.result.contextUsage,{usedTokens:76543,contextWindow:200000});
    assert.equal(response.result.contextUsageStatus,'available');
    assert.deepEqual(usageCalls,['summary','full']);
    assert.ok(!JSON.stringify(response.result.contextUsage).includes('PRIVATE')&&!JSON.stringify(response).includes('SECRET'));
  }
  for(const usage of [null,{totalTokens:12,rawMaxTokens:0},{totalTokens:-1,rawMaxTokens:200000},{totalTokens:12,rawMaxTokens:Infinity}]){
    api.getContextUsageSummary=async()=>null;api.getContextUsage=async()=>usage;
    request=pack({generation:'next',type:'request',seq:++seq,surface:'code',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
    await waitFor(()=>response.seq===seq&&response.done);
    assert.equal(response.result.contextUsage,null);assert.equal(response.result.contextUsageStatus,'unavailable');
  }
  usageCalls=[];budgetNow=Date.now();realContextBudget=true;
  api.getContextUsageSummary=()=>{usageCalls.push('summary');return new Promise(()=>{});};
  api.getContextUsage=async()=>{usageCalls.push('full');return {totalTokens:12,rawMaxTokens:200000};};
  request=pack({generation:'next',type:'request',seq:++seq,surface:'code',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===seq&&response.done);realContextBudget=false;
  assert.equal(response.result.contextUsage,null);assert.equal(response.result.contextUsageStatus,'unavailable');
  assert.deepEqual(usageCalls,['summary'],'a stalled summary exhausts the shared budget; do not start another native request');
  delete api.getContextUsageSummary;delete api.getContextUsage;
  request=pack({generation:'next',type:'request',seq:++seq,surface:'cowork',method:'mobileDetail',args:['local_test'],expires:Date.now()/1000+10});
  await waitFor(()=>response.seq===seq&&response.done);
  assert.equal(response.result.contextUsage,null);assert.equal(response.result.contextUsageStatus,'unsupported');
  window.__claudeMobileBridge.stop(); await running;
  assert.equal(response.connected,false);
  console.log('Desktop file connector: handshake, original session dispatch, deduplication, same-generation reinjection without replay, expiry, allowlist, redaction, restart, replay rejection, stop passed.');
})().catch(e=>{window.__claudeMobileBridge?.stop();console.error(e);process.exitCode=1;});
