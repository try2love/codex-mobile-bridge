const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');

async function fixture(){
 const stored=new Map(),context=vm.createContext({window:{addEventListener(){},dispatchEvent(){}},
   sessionStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},
   setTimeout,clearTimeout,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},URLSearchParams,TextEncoder,crypto:require('node:crypto').webcrypto,BridgeI18n:{t:value=>value}});
 for(const file of ['sync-cache.js','sync-transport.js'])vm.runInContext(fs.readFileSync('web/features/chat/'+file,'utf8'),context);
 const cache=context.window.BridgeChatSyncCache;
 await cache.bind({sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)},listCacheIdentity:'fixture'});
 const scope=cache.scope('claude','local','chat');
 const row={key:'answer',version:'content',turnId:'turn',role:'assistant',text:'Open [report](/audit/report.md)',order:0};
 const files=[{id:'file',reference:'/audit/report.md',references:[],scope:'host',path:'/audit/report.md'}];
 const page={version:'page',rows:[row],index:[row],files,meta:{},turns:[]};
 await cache.write(scope,'recent',{},page);
 return {window:context.window,cache,scope,row,files,page,read:context.window.BridgeChatSync.read};
}

test('a pending cache persistence job does not block the next online validation',async()=>{
 const f=await fixture();let release;
 f.cache.pending.set(f.scope,new Promise(resolve=>{release=resolve;}));
 try{
  const result=await Promise.race([f.cache.known(f.scope,'recent',{}),new Promise(resolve=>setTimeout(()=>resolve(null),80))]);
  assert.ok(result,'online read must use committed cache state without waiting for persistence');
 }finally{release();}
});

test('phone paints verified fresh rows before a slow native cache write completes',async()=>{
 const f=await fixture();
 f.window.CodexBridgeNativeOffline={request:async()=>({turns:[]})};
 f.cache.state=async()=>({clearRevision:0,allowPrefetch:true});
 const known=await f.cache.known(f.scope,'recent',{});
 const change=f.cache.disk.change.bind(f.cache.disk);let release;
 const gate=new Promise(resolve=>{release=resolve;});
 f.cache.disk.change=async(...args)=>{await gate;return change(...args);};
 const pending=f.cache.write(f.scope,'recent',{},f.page,{source:'live',validation:known});
 try{
  const result=await Promise.race([pending,new Promise(resolve=>setTimeout(()=>resolve(null),80))]);
  assert.equal(result?.rows?.[0]?.text,f.row.text);
 }finally{release();await pending;await f.cache.pending.get(f.scope);}
});

test('authorized metadata refresh restores file actions without downloading the cached message again',async()=>{
 const f=await fixture(),known=await f.cache.known(f.scope,'recent',{});
 assert.equal((await f.cache.read(f.scope,'recent',{})).files.length,0);
 const result=await f.cache.write(f.scope,'recent',{}, {...f.page,rows:[],notModified:true},{validation:known});
 assert.equal(result.rows[0].text,f.row.text);
 assert.equal(result.files[0].path,'/audit/report.md');
});

test('body reuse retains file metadata and upgrades older cached chunks once',async()=>{
 const f=await fixture(),body={key:f.row.key,version:f.row.version,offset:0,text:f.row.text,next:null};
 await f.cache.write(f.scope,'body',{key:body.key,offset:0},body);
 let requests=0,authorized=true;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>authorized,request:async()=>{requests++;return {...body,files:f.files};}};
 const params={key:body.key,version:body.version,offset:0};
 const first=await f.read(target,'body',params,'manual',()=>true);
 const next=await f.read(target,'body',params,'manual',()=>true);
 assert.equal(requests,1);
 assert.equal(first.files[0].path,next.files[0].path);
 authorized=false;
 assert.equal(await f.read(target,'body',params,'manual',()=>true),null);
 assert.equal(requests,1);
});

test('a changed body version cannot persist new chunks with the old cached row preview',async()=>{
 const f=await fixture();let writes=0;
 f.cache.write=async()=>{writes++;};
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async()=>({key:f.row.key,version:'edited',offset:0,text:'Edited full text',next:null,files:[]})};
 const result=await f.read(target,'body',{key:f.row.key,version:f.row.version,offset:100},'manual',()=>true);
 assert.equal(result.version,'edited');assert.equal(writes,0);
 assert.equal(f.cache.resident.get(f.scope)?.pages.size||0,0);
});

test('a fresh online page can paint while an earlier cache commit is still pending',async()=>{
 const f=await fixture(),known=await f.cache.known(f.scope,'recent',{});let release;
 const gate=new Promise(resolve=>{release=resolve;});f.cache.pending.set(f.scope,gate);
 const pending=f.cache.write(f.scope,'recent',{},f.page,{source:'live',validation:known});
 try{
  const result=await Promise.race([pending,new Promise(resolve=>setTimeout(()=>resolve(null),80))]);
  assert.equal(result?.rows?.[0]?.text,f.row.text);
 }finally{release();await pending;await f.cache.pending.get(f.scope);}
});


test('native imports, state queries and writes cannot hold an authenticated online response',async()=>{
 const f=await fixture(),calls=[];
 f.window.CodexBridgeNativeOffline={request:command=>{calls.push(command.type);return new Promise(()=>{});}};
 f.cache.known=()=>{throw Error('foreground must not import the archive');};
 const target={scope:f.scope,url:'/fixture/sync',request:async()=>f.page,authorized:()=>true};
 const page=await Promise.race([f.read(target,'recent',{},'manual',()=>true),new Promise(resolve=>setTimeout(()=>resolve(null),100))]);
 assert.equal(page?.rows?.[0]?.text,f.row.text);
 assert(calls.every(type=>type==='state'));
});

test('native foreground keeps resident versions for incremental refresh while persistence is pending',async()=>{
 const f=await fixture();f.window.CodexBridgeNativeOffline={request:()=>new Promise(()=>{})};
 let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async url=>{
  if(requests++===0)return f.page;
  assert(new URL(url,'https://fixture').searchParams.getAll('known').includes('answer:content'));
  return {...f.page,rows:[],notModified:true};
 }};
 await f.read(target,'recent',{},'manual',()=>true);
 const page=await f.read(target,'recent',{},'live',()=>true);
 assert.equal(page.rows[0].text,f.row.text);assert.equal(page.files[0].path,'/audit/report.md');
});

test('clearing cached turns invalidates an in-flight native foreground response',async()=>{
 const f=await fixture();f.window.CodexBridgeNativeOffline={request:async()=>({})};
 let reply;const pending=new Promise(resolve=>{reply=resolve;});
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:()=>pending};
 const read=f.read(target,'recent',{},'live',()=>true);
 f.cache.cleared({[f.scope]:['turn']},false);reply(f.page);
 assert.equal(await read,null);
});


test('a prior native deletion revision does not invalidate a newly authorized manual first screen',async()=>{
 const f=await fixture(),events=[];
 f.window.dispatchEvent=event=>events.push(event.type);
 f.window.CodexBridgeNativeOffline={request:async()=>({clearRevision:'2',tombstones:{[f.cache.scopes.get(f.scope).sessionId]:['turn']}})};
 f.cache.nativeState={clearRevision:'2'};
 await f.cache.state(f.scope);
 assert(!events.includes('bridge-cache-cleared'));
 f.window.CodexBridgeNativeOffline.request=async()=>({clearRevision:'3',tombstones:{[f.cache.scopes.get(f.scope).sessionId]:['new-clear']}});
 await f.cache.state(f.scope);
 assert(events.includes('bridge-cache-cleared'));
});


test('a late native binding cannot persist a response after the account generation changes',async()=>{
 const f=await fixture();f.window.CodexBridgeNativeOffline={request:async()=>({})};
 let release;f.cache.nativeReady=new Promise(resolve=>{release=resolve;});
 const validation=f.cache.onlineKnown(f.scope,'recent');
 const writing=f.cache.commit(f.scope,'recent',{},f.page,{validation,source:'manual'});
 f.cache.generation++;release({clearRevision:'0'});
 assert.equal((await writing).discarded,true);
});

for(const blocked of ['known','write'])test('browser foreground displays a fresh page while cache '+blocked+' is stalled',async()=>{
 const f=await fixture();
 f.cache[blocked]=()=>new Promise(()=>{});
 let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async()=>{requests++;return f.page;}};
 const page=await Promise.race([f.read(target,'recent',{},'manual',()=>true),new Promise(resolve=>setTimeout(()=>resolve(null),80))]);
 assert.equal(page?.rows?.[0]?.text,f.row.text);
 assert.equal(requests,1);
});

test('browser body reads bypass stalled disk and reuse the authorized resident chunk',async()=>{
 const f=await fixture();f.cache.disk.get=()=>new Promise(()=>{});
 const body={key:f.row.key,version:f.row.version,offset:0,text:f.row.text,next:null,files:f.files};let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async()=>{requests++;return body;}};
 const params={key:body.key,version:body.version,offset:0};
 for(let i=0;i<2;i++){
  const page=await Promise.race([f.read(target,'body',params,'manual',()=>true),new Promise(resolve=>setTimeout(()=>resolve(null),80))]);
  assert.equal(page?.text,body.text);assert.equal(page.files[0].path,f.files[0].path);
 }
 assert.equal(requests,1);
});

test('browser live refresh reuses resident versions and drops responses invalidated by clearing',async()=>{
 const f=await fixture();f.cache.write=()=>new Promise(()=>{});let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async url=>{
  if(requests++===0)return f.page;
  assert(new URL(url,'https://fixture').searchParams.getAll('known').includes('answer:content'));
  return {...f.page,rows:[],notModified:true};
 }};
 await f.read(target,'recent',{},'manual',()=>true);
 assert.equal((await f.read(target,'recent',{},'live',()=>true)).rows[0].text,f.row.text);
 let reply;target.request=()=>new Promise(resolve=>{reply=resolve;});
 const pending=f.read(target,'recent',{},'live',()=>true);
 f.cache.cleared({[f.scope]:['turn']},false);reply(f.page);
 assert.equal(await pending,null);
});

test('browser falls back to a full authenticated page when a cache index probe stalls',async()=>{
 const f=await fixture();
 f.cache.known=async()=>Object.assign(new URLSearchParams('indexOnly=1'),{probe:true,generation:f.cache.generation,epoch:f.cache.epoch});
 f.cache.afterProbe=()=>new Promise(()=>{});let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async()=>requests++===0?{...f.page,rows:[],indexOnly:true}:f.page};
 const page=await Promise.race([f.read(target,'recent',{},'manual',()=>true),new Promise(resolve=>setTimeout(()=>resolve(null),150))]);
 assert.equal(page?.rows[0]?.text,f.row.text);assert.equal(requests,2);
});

test('a resident browser body cannot return after a clear during the bounded disk lookup',async()=>{
 const f=await fixture(),body={key:f.row.key,version:f.row.version,offset:0,text:f.row.text,next:null,files:f.files};let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async()=>{requests++;return body;}};
 const params={key:body.key,version:body.version,offset:0};
 await f.read(target,'body',params,'manual',()=>true);
 f.cache.read=()=>new Promise(()=>{});
 const pending=f.read(target,'body',params,'manual',()=>true);
 f.cache.cleared({[f.scope]:['turn']},false);
 assert.equal(await pending,null);assert.equal(requests,1);
});

for(const stalled of [false,true])test('browser conditional page '+(stalled?'falls back when indexed rows stall':'rehydrates expanded indexed rows without retransmission'),async()=>{
 const f=await fixture();
 f.cache.known=async()=>Object.assign(new URLSearchParams('known=answer:content'),{generation:f.cache.generation,epoch:f.cache.epoch,revision:0,resident:new Map(),bans:new Set()});
 if(stalled)f.cache.disk.get=()=>new Promise(()=>{});
 let requests=0;
 const target={scope:f.scope,url:'/fixture/sync',authorized:()=>true,request:async()=>requests++===0?{...f.page,rows:[],notModified:true}:f.page};
 const page=await Promise.race([f.read(target,'recent',{},'manual',()=>true),new Promise(resolve=>setTimeout(()=>resolve(null),150))]);
 assert.equal(page?.rows[0]?.text,f.row.text);assert.equal(requests,stalled?2:1);
});

test('resident process hints exclude messages and other process groups',async()=>{
 const f=await fixture();
 f.cache.onlinePage(f.scope,'recent',{},f.page,f.cache.onlineKnown(f.scope,'recent'));
 for(const group of ['one','two']){
  const params={key:group,after:'latest'},row={key:'step-'+group,version:'v',turnId:'turn',order:0,text:group};
  f.cache.onlinePage(f.scope,'process',params,{version:group,index:[row],rows:[row]},f.cache.onlineKnown(f.scope,'process',params));
 }
 const known=f.cache.onlineKnown(f.scope,'process',{key:'one',after:'latest'},'live');
 assert.deepEqual(Array.from(known.getAll('known')),['step-one:v']);
});

test('a browser index probe reuses displayed versions before their disk commit finishes',async()=>{
 const f=await fixture(),row={...f.row,version:'fresh',text:'fresh'};
 const page={...f.page,version:'fresh-page',rows:[row],index:[row]};
 f.cache.onlinePage(f.scope,'recent',{},page,f.cache.onlineKnown(f.scope,'recent'));
 const known=Object.assign(new URLSearchParams('indexOnly=1'),f.cache.onlineKnown(f.scope,'recent'),{lookup:f.scope,probe:true});
 const result=await f.cache.afterProbe(known,{...page,rows:[],indexOnly:true});
 assert.equal(result.get('ifVersion'),'fresh-page');
 assert.equal(result.resident.get(row.key).text,'fresh');
});

test('process lookup includes page manifests whose persistence is still pending',async()=>{
 const f=await fixture(),params={key:'group',after:'latest'};
 const rows=Array.from({length:100},(_,i)=>({key:'saved-'+i,version:'v',turnId:'turn',order:i,text:String(i)}));
 await f.cache.write(f.scope,'process',params,{version:'saved',rows,index:rows});
 const fresh=Array.from({length:20},(_,i)=>({key:'new-'+i,version:'v',turnId:'turn',order:100+i,text:String(i)}));
 f.cache.onlinePage(f.scope,'process',params,{version:'fresh',rows:fresh,index:fresh},f.cache.onlineKnown(f.scope,'process',params));
 const known=await f.cache.known(f.scope,'process',{key:'group',before:'new-0'},'manual');
 assert.equal(known.probe,true);assert.equal(known.get('indexOnly'),'1');
});
