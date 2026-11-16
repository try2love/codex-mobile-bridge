'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHmac,createHash}=require('node:crypto');
const template=fs.readFileSync('bridge/clients/claude/connector.js','utf8');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const waitFor=async predicate=>{const end=Date.now()+1000;while(!predicate()){if(Date.now()>end)throw Error('timeout');await delay(5);}};
function runtime(t,{save,existing=false,trusted=true}={}){
 const cfg={cwd:'/fixture',request:'request.json',response:'response.json',token:'staging-fixture',generation:'first',reconnect:true,connectorRevision:16};
 const packet=value=>{const payload=JSON.stringify({generation:cfg.generation,...value});return {contents:JSON.stringify({payload,signature:createHmac('sha256',cfg.token).update(payload).digest('hex')})};};
 const stats={saves:0,starts:0,sends:0,writes:0,reads:0},saved=[];let ack=null;
 const api={getAll:async()=>existing?[{sessionId:'known',cwd:cfg.cwd}]:[],checkTrust:async()=>({trusted}),
  readFileAtCwd:async(cwd,name)=>{stats.reads++;if(name==='staged-ack.json'){if(!ack)throw Error('no ack');return packet(ack);}return packet({type:'idle',seq:0});},
  writeSessionFile:async()=>{stats.writes++;return {hash:'ok'};},
  start:async()=>{stats.starts++;throw Error('unexpected model turn');},sendMessage:async()=>{stats.sends++;throw Error('unexpected model turn');}};
 const window={'claude.web':{LocalSessions:api,FileSystem:{savePastedFile:async(...args)=>{const index=++stats.saves;saved.push(args);return save?save(index,...args):{path:'/native/reply'};}}}};
 const context={window,crypto:webcrypto,btoa,TextEncoder,Uint8Array,console:{info(){},error(){}},setTimeout:(fn,ms)=>setTimeout(fn,ms===4000?25:Math.min(ms,5)),clearTimeout};
 const running=vm.runInNewContext(template.replace('__BRIDGE_CONFIG__',JSON.stringify(cfg)),context);
 // Attach immediately: an initial trust failure must not become unhandled.
 const completed=running.then(()=>({}),error=>({error}));
 t.after(async()=>{window.__claudeMobileBridge?.stop();await completed;assert.equal(stats.starts,0);assert.equal(stats.sends,0);});
 return {stats,window,saved,completed,setAck(value){ack=value;},
  async receipt(index=0){const bytes=Buffer.from(saved[index][0],'base64'),key=await webcrypto.subtle.importKey('raw',createHash('sha256').update('cmb-reply-key:'+cfg.token).digest(),{name:'AES-GCM'},false,['decrypt']);return JSON.parse(JSON.parse(Buffer.from(await webcrypto.subtle.decrypt({name:'AES-GCM',iv:bytes.subarray(0,12)},key,bytes.subarray(12))).toString()).payload);}};
}
for(const failure of ['reject','missing path'])test('first staging '+failure+' recovers without a session or model turn',async t=>{
 const ui=runtime(t,{save:async n=>{if(n===1){if(failure==='reject')throw Error('Private dir leaf redirects');return {};}return {path:'/native/reply'};}});
 await waitFor(()=>ui.stats.saves===2);await delay(60);
 assert.equal(ui.stats.saves,2,'successful retry waits for its own acknowledgement');
 assert.equal((await ui.receipt(1)).connected,true);
});
test('outer timeout does not replay a pending save; late failure permits one new attempt',async t=>{
 let reject;
 const ui=runtime(t,{save:n=>n===1?new Promise((resolve,fail)=>{reject=fail;}):Promise.resolve({path:'/native/reply'})});
 await waitFor(()=>!!reject);await delay(90);
 assert.equal(ui.stats.saves,1);assert.equal(ui.window.__claudeMobileBridge.status().writing,true);
 reject(Error('save rejected'));await waitFor(()=>ui.stats.saves===2);await delay(40);assert.equal(ui.stats.saves,2);
});
test('late successful save waits for a matching signed acknowledgement',async t=>{
 let resolve;
 const ui=runtime(t,{save:n=>n===1?new Promise(done=>{resolve=done;}):Promise.resolve({path:'/native/reply'})});
 await waitFor(()=>!!resolve);await delay(70);assert.equal(ui.stats.saves,1);
 resolve({path:'/native/reply'});await waitFor(()=>!ui.window.__claudeMobileBridge.status().writing);
 ui.setAck({receipt:'wrong'});await delay(50);assert.equal(ui.stats.saves,1);
 const row=await ui.receipt();ui.setAck({generation:row.generation,receipt:row.receipt});
 await waitFor(()=>ui.stats.saves===2);await delay(40);assert.equal(ui.stats.saves,2);
});
test('late failure from an acknowledged save cannot clear a newer receipt',async t=>{
 let reject;
 const ui=runtime(t,{save:n=>n===1?new Promise((resolve,fail)=>{reject=fail;}):Promise.resolve({path:'/native/reply'})});
 await waitFor(()=>!!reject);const row=await ui.receipt();ui.setAck({generation:row.generation,receipt:row.receipt});
 await waitFor(()=>ui.stats.saves===2);reject(Error('late native failure'));await delay(80);
 assert.equal(ui.stats.saves,2,'late completion must not clear the replacement pending object');
});
test('an existing connector session keeps its original receipt path',async t=>{
 const ui=runtime(t,{existing:true});await waitFor(()=>ui.stats.writes>0);assert.equal(ui.stats.saves,0);
});
test('an untrusted empty workspace still refuses initialization',async t=>{
 const ui=runtime(t,{trusted:false});const result=await ui.completed;assert.match(result.error.message,/尚未信任目录/);assert.equal(ui.stats.saves,0);
});
