'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHmac}=require('node:crypto');
const template=fs.readFileSync('bridge/clients/claude/connector.js','utf8');
const config=(cwd,generation)=>({cwd,request:'request.json',response:'response.json',token:generation,generation,reconnect:true,connectorRevision:8,inspectionMarker:generation});
const packet=(cfg,value)=>{const payload=JSON.stringify({generation:cfg.generation,...value});return JSON.stringify({payload,signature:createHmac('sha256',cfg.token).update(payload).digest('hex')});};
const waitFor=async predicate=>{const until=Date.now()+3000;while(!predicate()){if(Date.now()>until)throw Error('timeout');await new Promise(resolve=>setTimeout(resolve,10));}};
function runtime(configs,{fastTimeouts=false}={}){
 const requests=new Map(configs.map(cfg=>[cfg.cwd,packet(cfg,{type:'idle',seq:0})]));
 const writes=[],calls=[],errors=[];let nativeStops=0,delayRead=false,readHeld=false,releaseRead,delayWrite=false,releaseWrite,delaySign=false,signHeld=false,releaseSign;
 const original=function(models){return models;};
 const api={setAvailableCodeModels:original,
  async getAll(){return configs.map((cfg,i)=>({sessionId:'session-'+i,cwd:cfg.cwd,title:'Codex Bridge Connector'}));},
  async readFileAtCwd(cwd){if(delayRead){delayRead=false;readHeld=true;return new Promise(resolve=>{releaseRead=contents=>resolve({contents:contents||requests.get(cwd)});});}return {contents:requests.get(cwd)};},
  async writeSessionFile(id,path,text){if(delayWrite)return new Promise(resolve=>{releaseWrite=()=>{writes.push(JSON.parse(JSON.parse(text).payload));resolve({hash:'ok'});};});writes.push(JSON.parse(JSON.parse(text).payload));return {hash:'ok'};},
  async sendMessage(...args){calls.push(args);},
  async stop(){nativeStops++;}
 };
 const window={'claude.web':{LocalSessions:api}};
 const crypto={subtle:{importKey:(...args)=>webcrypto.subtle.importKey(...args),verify:(...args)=>webcrypto.subtle.verify(...args),
  sign:(...args)=>delaySign?(delaySign=false,signHeld=true,new Promise(resolve=>{releaseSign=()=>webcrypto.subtle.sign(...args).then(resolve);})):webcrypto.subtle.sign(...args)}};
 const context={window,crypto,TextEncoder,Uint8Array,Date,
  setTimeout:(fn,ms)=>setTimeout(fn,fastTimeouts&&ms===4000?30:ms),clearTimeout,console:{info(){},error(message){errors.push(message);}}};
 const inject=cfg=>vm.runInNewContext(template.replace('__BRIDGE_CONFIG__',JSON.stringify(cfg)),context);
 return {window,api,writes,calls,errors,requests,original,inject,nativeStops:()=>nativeStops,delayNextRead(){delayRead=true;},readPending(){return readHeld;},releaseRead(contents){releaseRead(contents);},delayNextSign(){delaySign=true;},signPending(){return signHeld;},releaseSign(){releaseSign();},delayNextWrite(){delayWrite=true;},writePending(){return !!releaseWrite;},releaseWrite(){delayWrite=false;releaseWrite();}};
}
test('signed stop detaches one gateway and permits a different workspace to connect',async()=>{
 const first=config('/gateway-a','first'),second=config('/gateway-b','second'),ui=runtime([first,second]);
 const oldRunning=ui.inject(first);await waitFor(()=>ui.writes.some(row=>row.generation==='first'&&row.connected));
 const old=ui.window.__claudeMobileBridge,oldObserver=ui.api.setAvailableCodeModels;
 assert.notEqual(oldObserver,ui.original);
 ui.requests.set(first.cwd,packet(first,{type:'stop',seq:1}));
 await waitFor(()=>old.status().suspended&&!old.status().writing);
 assert.equal(ui.writes.at(-1).connected,false);
 const newRunning=ui.inject(second);await waitFor(()=>ui.window.__claudeMobileBridge?.owns(second.cwd));
 assert.equal(old.status().stopped,true);assert.notEqual(ui.api.setAvailableCodeModels,oldObserver);
 ui.window.__claudeMobileBridge.stop();await Promise.all([oldRunning,newRunning]);
 assert.equal(ui.api.setAvailableCodeModels,ui.original,'observer restores Desktop original after transfer');
});
test('foreign gateway takes over only after the old writer acknowledges the idle offer',async()=>{
 const first=config('/gateway-a','first'),second=config('/gateway-b','second'),ui=runtime([first,second]);
 const oldRunning=ui.inject(first);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge,observer=ui.api.setAvailableCodeModels;
 const newRunning=ui.inject(second);
 await waitFor(()=>ui.writes.some(row=>row.generation==='first'&&row.handoffId));
 assert.equal(ui.window.__claudeMobileBridge,old);assert.equal(ui.api.setAvailableCodeModels,observer);
 const offer=ui.writes.findLast(row=>row.generation==='first'&&row.handoffId);
 ui.requests.set(first.cwd,packet(first,{type:'handoff-ack',seq:0,handoffId:offer.handoffId}));
 await waitFor(()=>ui.window.__claudeMobileBridge?.owns(second.cwd));
 assert.equal(old.status().stopped,true);
 assert.equal(ui.nativeStops(),0,'renderer handoff never stops a native Claude chat');
 assert.equal(ui.writes.some(row=>row.generation==='first'&&row.connected===false),true);
 ui.window.__claudeMobileBridge.stop();await Promise.all([oldRunning,newRunning]);
 assert.equal(ui.api.setAvailableCodeModels,ui.original);
});
for(const revision of [14,15])test('staging recovery revision accepts the verified idle handoff from '+revision,async()=>{
 const first={...config('/gateway-a','first'),connectorRevision:revision},second={...config('/gateway-b','second'),connectorRevision:16},ui=runtime([first,second]);
 const oldRunning=ui.inject(first);await waitFor(()=>ui.writes.length>0);
 const newRunning=ui.inject(second);await waitFor(()=>ui.writes.some(row=>row.handoffId));
 const offer=ui.writes.findLast(row=>row.handoffId);
 ui.requests.set(first.cwd,packet(first,{type:'handoff-ack',seq:0,handoffId:offer.handoffId}));
 await waitFor(()=>ui.window.__claudeMobileBridge?.owns(second.cwd));
 ui.window.__claudeMobileBridge.stop();await Promise.all([oldRunning,newRunning]);assert.equal(ui.nativeStops(),0);
});
test('two injectors awaiting one handoff cannot both own the renderer',async()=>{
 const oldCfg=config('/gateway-a','first'),b=config('/gateway-b','second'),c=config('/gateway-c','third');
 const ui=runtime([oldCfg,b,c]);
 const oldRunning=ui.inject(oldCfg);await waitFor(()=>ui.writes.length>0);
 const attempts=[ui.inject(b),ui.inject(c)].map(p=>p.then(()=>({ok:true}),error=>({ok:false,error})));
 await waitFor(()=>ui.writes.some(row=>row.generation==='first'&&row.handoffId));
 const offer=ui.writes.findLast(row=>row.generation==='first'&&row.handoffId);
 ui.requests.set(oldCfg.cwd,packet(oldCfg,{type:'handoff-ack',seq:0,handoffId:offer.handoffId}));
 await waitFor(()=>ui.window.__claudeMobileBridge?.owns(b.cwd)||ui.window.__claudeMobileBridge?.owns(c.cwd));
 ui.window.__claudeMobileBridge.stop();
 const results=await Promise.all(attempts);await oldRunning;
 assert.equal(results.filter(row=>row.ok).length,1);
 assert.match(results.find(row=>!row.ok).error.message,/状态已变化/);
 assert.match(ui.errors.at(-1),/BRIDGE_OWNER_UNCONFIRMED.*\[(second|third)\]/);
 assert.equal(ui.nativeStops(),0);
});
test('an unread signed request prevents foreign handoff',async()=>{
 const first=config('/gateway-a','first'),second=config('/gateway-b','second'),ui=runtime([first,second]);
 const oldRunning=ui.inject(first);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge;
 ui.requests.set(first.cwd,packet(first,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:0}));
 await assert.rejects(ui.inject(second),/未确认请求/);
 assert.match(ui.errors.at(-1),/BRIDGE_OWNER_UNCONFIRMED.*\[second\]/);
 assert.equal(ui.window.__claudeMobileBridge,old);
 assert.equal(old.status().stopped,false);
 old.stop();await oldRunning;
});
test('a request arriving during an offer cancels handoff without replay',async()=>{
 const first=config('/gateway-a','first'),second=config('/gateway-b','second'),ui=runtime([first,second]);
 const oldRunning=ui.inject(first);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge,newRunning=ui.inject(second);
 await waitFor(()=>ui.writes.some(row=>row.generation==='first'&&row.handoffId));
 ui.requests.set(first.cwd,packet(first,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:0}));
 await assert.rejects(newRunning,/未确认请求/);
 assert.equal(ui.window.__claudeMobileBridge,old);
 assert.equal(old.status().stopped,false);
 assert.equal(ui.calls.length,0);
 old.stop();await oldRunning;
});
test('foreign handoff waits for a pending response write',async()=>{
 const first=config('/gateway-a','first'),second=config('/gateway-b','second'),ui=runtime([first,second]);
 const oldRunning=ui.inject(first);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge;
 ui.delayNextWrite();
 ui.requests.set(first.cwd,packet(first,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:0}));
 await waitFor(()=>old.status().writing&&ui.writePending());
 await assert.rejects(ui.inject(second),/请求进行中/);
 assert.match(ui.errors.at(-1),/BRIDGE_OWNER_BUSY.*\[second\]/);
 assert.equal(ui.window.__claudeMobileBridge,old);
 ui.releaseWrite();old.stop();await oldRunning;
});
test('replacement while old read is pending cannot dispatch its late request',async()=>{
 const cfg=config('/same','same'),ui=runtime([cfg]);
 const oldRunning=ui.inject(cfg);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge;
 ui.delayNextRead();await waitFor(()=>ui.readPending());
 const newRunning=ui.inject(cfg);await waitFor(()=>ui.window.__claudeMobileBridge!==old);
 ui.releaseRead(packet(cfg,{type:'request',seq:1,surface:'code',method:'sendMessage',args:['session-0','late'],expires:Date.now()/1000+10}));await oldRunning;await new Promise(resolve=>setTimeout(resolve,30));
 assert.equal(ui.calls.length,0);
 ui.window.__claudeMobileBridge.stop();await newRunning;
});
test('a response write in progress blocks replacement and direct stop prevents a later write',async()=>{
 const cfg=config('/same','same'),ui=runtime([cfg]);
 const oldRunning=ui.inject(cfg);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge,baseline=ui.writes.length;
 ui.delayNextWrite();ui.requests.set(cfg.cwd,packet(cfg,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:0}));
 await waitFor(()=>old.status().writing&&ui.writePending());
 await assert.rejects(ui.inject(cfg),/请求进行中/);
 assert.equal(ui.window.__claudeMobileBridge,old);
 old.stop();ui.releaseWrite();await oldRunning;
 assert.equal(ui.writes.length,baseline+1,'an already submitted write completes, with no follow-up publish');
});
test('response signing is covered by the write guard and direct stop drops its result',async()=>{
 const cfg=config('/same','same'),ui=runtime([cfg]);
 const oldRunning=ui.inject(cfg);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge,baseline=ui.writes.length;
 ui.delayNextSign();ui.requests.set(cfg.cwd,packet(cfg,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:0}));
 await waitFor(()=>ui.signPending()&&old.status().writing);
 await assert.rejects(ui.inject(cfg),/请求进行中/);
 old.stop();ui.releaseSign();await oldRunning;
 assert.equal(ui.writes.length,baseline,'stopped connector must not submit a response after signing');
});
test('CDP invocation advertises pending work and blocks same-owner replacement',async()=>{
 const cfg={...config('/same','same'),transport:'cdp'},ui=runtime([cfg]);
 await ui.inject(cfg);const old=ui.window.__claudeMobileBridge;
 const finishes=[];ui.api.getAll=()=>new Promise(resolve=>{finishes.push(()=>resolve([]));});
 const message=seq=>({token:cfg.token,generation:cfg.generation,seq,expires:Date.now()/1000+10,surface:'code',method:'getAll',args:[]});
 const first=old.invoke(message(1)),second=old.invoke(message(2));
 await waitFor(()=>old.status().busy);
 await assert.rejects(ui.inject(cfg),/请求进行中/);
 assert.equal(ui.window.__claudeMobileBridge,old);
 await waitFor(()=>finishes.length===2);finishes[0]();await first;
 assert.equal(old.status().busy,true,'one completed invoke cannot clear the other');
 finishes[1]();await second;assert.equal(old.status().busy,false);old.stop();
});
test('file operation remains owned after its display deadline until Desktop settles',async()=>{
 const cfg=config('/same','same'),ui=runtime([cfg]);
 const running=ui.inject(cfg);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge;let finish;
 ui.api.getAll=()=>new Promise(resolve=>{finish=()=>resolve([]);});
 ui.requests.set(cfg.cwd,packet(cfg,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:Date.now()/1000+.8}));
 await waitFor(()=>ui.writes.some(row=>row.seq===1&&row.done&&/超时/.test(row.error)));
 assert.equal(old.status().busy,true,'timeout must not hide an unsettled native call');
 await assert.rejects(ui.inject(cfg),/请求进行中/);
 finish();await waitFor(()=>old.status().busy===false);
 old.stop();await running;
});
test('overlapping timed-out publishes keep the write guard until both settle',async()=>{
 const cfg=config('/same','same'),ui=runtime([cfg],{fastTimeouts:true});
 const running=ui.inject(cfg);await waitFor(()=>ui.writes.length>0);
 const old=ui.window.__claudeMobileBridge,finishes=[];
 ui.api.writeSessionFile=()=>new Promise(resolve=>{finishes.push(()=>resolve({hash:'ok'}));});
 ui.requests.set(cfg.cwd,packet(cfg,{type:'request',seq:1,surface:'code',method:'getAll',args:[],expires:0}));
 await waitFor(()=>finishes.length===2);
 finishes[0]();await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(old.status().writing,true,'first completion cannot clear a later pending write');
 await assert.rejects(ui.inject(cfg),/请求进行中/);
 old.stop();finishes[1]();await running;
});
