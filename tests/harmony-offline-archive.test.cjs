'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {stripTypeScriptTypes}=require('node:module'),{DatabaseSync}=require('node:sqlite');

async function fixture(){
 const sql=new DatabaseSync(':memory:'),queries=[];
 const db={run:async(query,values=[])=>{sql.prepare(query).run(...values);},rows:async(query,values=[])=>{
  queries.push(query);return sql.prepare(query).all(...values).map(row=>Object.values(row));
 }};
 const context=vm.createContext({Uint8Array,Promise,util:{TextEncoder:class{encodeInto(value){return new TextEncoder().encode(value);}}}});
 const source=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/OfflineArchive.ets','utf8')
  .replace(/^import .*;\r?\n/gm,'').replaceAll('export class','class').replaceAll('export type','type');
 vm.runInContext(stripTypeScriptTypes(source)+'\nglobalThis.Archive=OfflineArchive;',context);
 const archive=new context.Archive(db);await archive.init();
 const account=await archive.bind('https://gateway.example','Fixture computer','a'.repeat(32),'b'.repeat(64));
 const merge=event=>archive.merge(account,{sessionId:'["codex","local","fixture"]',title:'Saved fixture',source:'live',syncedAt:100,clearRevision:account.revision,turns:[],...event});
 return {archive,account,sql,queries,merge,context,session:'["codex","local","fixture"]',close:()=>sql.close()};
}
const message=(key,text,order,extra={})=>({key,version:'v1',role:extra.role||'assistant',text,order,row:{role:'assistant',order,...extra}});

test('Harmony archive pages native records and preserves Unicode body chunks without reading one chat JSON',async()=>{
 const f=await fixture();
 try{
  const text='😀中文'.repeat(16000);
  await f.merge({turns:[{id:'turn',questionTime:123,messages:[message('answer',text,1,{turnStatus:'completed',phase:'final_answer'})]}]});
  const page=await f.archive.page(f.account,f.session,{});assert.equal(page.recordPage,1);assert.equal(page.rows[0].truncated,true);assert(page.rows[0].text.length<=6000);
  let result='',offset=0;
  do{const part=await f.archive.page(f.account,f.session,{mode:'body',key:'answer',version:'v1',offset});assert.equal(part.offset,offset);result+=part.text;offset=part.next;}while(offset!==null);
  assert.equal(result,text);assert(f.queries.some(sql=>sql.includes('LIMIT 41')));
  assert.equal((await f.archive.list(f.account)).sessions.length,1);
 }finally{f.close();}
});
test('Harmony process paging keeps source positions and complete expanded process rows',async()=>{
 const f=await fixture();
 try{
  const messages=[message('question','Question',0,{role:'user'}),...Array.from({length:120},(_,i)=>message('step-'+i,'Step '+i,i+1,{role:'activity',kind:'commandExecution'})),message('answer','Answer',121)];
  await f.merge({turns:[{id:'turn',questionTime:123,messages}]});
  const first=await f.archive.page(f.account,f.session,{}),group=first.turns[0].processes[0];
  assert.equal(first.rows.length,2);assert(group.order>first.rows[0].order&&group.order<first.rows[1].order);
  let after=null,steps=[];
  do{const page=await f.archive.page(f.account,f.session,{mode:'process',key:group.key,...(after?{after}:{})});steps.push(...page.rows.map(row=>row.text));after=page.next;}while(after);
  assert.deepEqual(steps,Array.from({length:120},(_,i)=>'Step '+i));
 }finally{f.close();}
});
test('Harmony clearing rejects late writes and stale cursors while retaining other dates and identities',async()=>{
 const f=await fixture();
 try{
  await f.merge({turns:Array.from({length:45},(_,i)=>({id:'turn-'+i,questionTime:i+1,messages:[message('answer-'+i,'Answer '+i,0)]}))});
  const before=(await f.archive.page(f.account,f.session,{})).before;assert(before);
  await f.archive.clear(f.account,1,20);
  await assert.rejects(f.merge({turns:[{id:'turn-1',questionTime:2,messages:[message('answer-1','Late',0)]}]}));
  assert.equal(await f.archive.page(f.account,f.session,{before}),null);
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM records').get().n,26);
  const other=await f.archive.bind('https://another.example','Other','a'.repeat(32),'c'.repeat(64));assert.equal((await f.archive.list(other)).sessions.length,0);
  await f.archive.status(f.account,'revoked');await assert.rejects(f.archive.page(f.account,f.session,{}));
 }finally{f.close();}
});

test('Harmony composer archive preserves CAS across the offline reader and independent accounts',async()=>{
 const f=await fixture();
 try{
  const call=(action,input={},who=f.account)=>f.archive.serial(()=>f.archive.composer(who,action,input));
  const sessionId=f.session,value={title:'Draft only',draft:{text:'中文📝'},outbox:[{id:'queued'}]};
  assert.equal((await call('composerRead',{sessionId})).value,null);
  const concurrent=await Promise.all(Array.from({length:2},()=>call('composerWrite',{sessionId,revision:0,value})));
  assert.deepEqual(concurrent.map(item=>item.applied),[true,false]);
  assert.equal(concurrent[1].value.draft.text,'中文📝');
  await call('composerWrite',{sessionId:'',revision:0,value});
  assert.equal(JSON.stringify((await call('composerPending')).sessions),JSON.stringify([sessionId]));
  assert.equal((await f.archive.available())[0].id,f.account.id);
  const listing=await f.archive.list(f.account),page=await f.archive.page(f.account,sessionId,{});
  assert.equal(listing.composerWritable,true);assert.equal(listing.sessions.length,1);
  assert.equal(page.title,'Draft only');assert.equal(page.rows.length,0);
  const other=await f.archive.bind('https://another.example','Other','a'.repeat(32),'c'.repeat(64));
  assert.equal((await call('composerRead',{sessionId},other)).value,null);
  assert.equal((await call('composerPending',{},other)).sessions.length,0);
  await f.archive.unlinkRoute('https://gateway.example');
  const retained=await f.archive.account(f.account.id);
  assert.equal((await f.archive.list(retained)).composerWritable,false);
  assert.equal((await call('composerRead',{sessionId},retained)).revision,1);
  await assert.rejects(call('composerWrite',{sessionId,revision:1,value},retained));
  await f.archive.clear(retained,0,Number.MAX_SAFE_INTEGER,true);
  assert.equal((await call('composerRead',{sessionId},retained)).value,null);
  assert.equal((await f.archive.available()).length,0);
 }finally{f.close();}
});

test('Harmony lists attachment-, Skill-, and work-mode-only drafts without chat history',async()=>{
 const f=await fixture();
 try{
  const drafts=[{attachmentDrafts:[{id:'upload',name:'draft.txt',status:'ready'}]},
    {skills:['check-notes']},{workMode:'plan'},{workMode:'goal'},{workMode:'default'}];
  const routes=drafts.map((_,i)=>JSON.stringify(['codex','local','prepared-'+i]));
  for(const [index,draft] of drafts.entries()){
   const value={title:'Draft '+index,draft:{text:'',...draft},outbox:[]};
   await f.archive.serial(()=>f.archive.composer(f.account,'composerWrite',{sessionId:routes[index],revision:0,value}));
  }
  const listing=await f.archive.list(f.account);
  assert.deepEqual(Array.from(listing.sessions,row=>row.id).sort(),routes.slice(0,4));
  for(const [index,route] of routes.slice(0,4).entries()){
   const page=await f.archive.page(f.account,route,{});
   assert.equal(page.title,'Draft '+index);
   assert.equal(page.rows.length,0);
  }
  assert.equal((await f.archive.serial(()=>f.archive.composer(f.account,'composerPending',{}))).sessions.length,0);
 }finally{f.close();}
});

function bridgeFixture(f){
 const replies=[],base='https://gateway.example';
 f.context.url={URL:{parseURL:value=>new URL(value)}};
 f.context.webview={WebCookieManager:{fetchCookieSync:()=> 'session=fixture'}};
 f.context.DeviceBinding={cookies:()=> 'session=fixture',capture:async()=>{},clear:async()=>{}};
 for(const name of ['GatewayUrl','DocumentGuard','GatewayHttp','OfflineBridge']){
  const text=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/'+name+'.ets','utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export class','class').replaceAll('export interface','interface');
  vm.runInContext(stripTypeScriptTypes(text)+'\nglobalThis.'+name+'='+name+';',f.context);
 }
 const guard=new f.context.DocumentGuard();guard.navigate(base);
 f.context.window={CodexBridgeNativeOffline:{receive:(_token,value)=>replies.push(value)}};
 const bridge=new f.context.OfflineBridge(f.archive,guard,{getUrl:()=>base+'/',runJavaScript:async script=>vm.runInContext(script,f.context)},'fixture-token');
 const proof={authenticated:true,sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)}};
 bridge.http.json=async()=>proof;
 const send=value=>bridge.enqueue(JSON.stringify({requestId:String(replies.length+1),gatewayId:'a'.repeat(32),identity:'b'.repeat(64),...value}));
 return {bridge,replies,proof,send};
}
test('Harmony archive requires independent gateway proof and rejects page-supplied account identities',async()=>{
 const f=await fixture();try{
  const b=bridgeFixture(f);b.send({type:'auth',status:'valid',identity:'c'.repeat(64)});await new Promise(setImmediate);
  assert.equal(b.replies[0].ok,false);assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM accounts').get().n,1);
  b.send({type:'auth',status:'valid'});await new Promise(setImmediate);assert.equal(b.replies[1].ok,true);
  b.send({type:'merge',identity:'c'.repeat(64),sessionId:f.session,clearRevision:'0',syncedAt:200,turns:[]});await new Promise(setImmediate);
  assert.equal(b.replies[2].ok,false);assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM sessions').get().n,0);
 }finally{f.close();}
});
test('a verified Harmony online request reads and updates the same local composer record',async()=>{
 const f=await fixture();try{
  const b=bridgeFixture(f),sessionId=f.session;
  const value={draft:{text:'Draft from offline'},outbox:[{id:'queued'}]};
  await f.archive.serial(()=>f.archive.composer(f.account,'composerWrite',{sessionId,revision:0,value}));
  b.send({type:'composerRead',sessionId});await new Promise(setImmediate);
  assert.equal(b.replies[0].value.value.draft.text,'Draft from offline');
  b.send({type:'composerPending',identity:'c'.repeat(64)});await new Promise(setImmediate);
  assert.equal(b.replies[1].ok,false);
  b.send({type:'composerWrite',sessionId,revision:1,value:{draft:null,outbox:[]}});await new Promise(setImmediate);
  assert.equal(b.replies[2].value.applied,true);
  const pending=await f.archive.serial(()=>f.archive.composer(f.account,'composerPending',{}));
  assert.equal(pending.sessions.length,0);
 }finally{f.close();}
});
test('a late Harmony authentication proof cannot reauthorize an account after logout',async()=>{
 const f=await fixture();try{
  const b=bridgeFixture(f);let release;
  b.bridge.http.json=()=>new Promise(resolve=>{release=resolve;});
  b.send({type:'auth',status:'valid'});await new Promise(setImmediate);
  b.send({requestId:'2',type:'auth',status:'loggedOut'});await new Promise(setImmediate);
  release(b.proof);await new Promise(setImmediate);
  assert.equal(f.sql.prepare('SELECT status FROM accounts').get().status,'loggedOut');
  assert(b.replies.some(reply=>reply.ok===false));assert.equal(b.bridge.account,null);
 }finally{f.close();}
});
