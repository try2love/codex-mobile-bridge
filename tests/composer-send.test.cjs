'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {ComposerDrafts}=require('../web/features/chat/composer-drafts.js');

function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};}
// Model the serialization seam here; the browser fixture exercises real IDB.
function coordinator(){let tail=Promise.resolve();return update=>{const result=tail.then(()=>update(true));tail=result.catch(()=>{});return result;};}
let tabSequence=0;
function fixture({durable=true,request,local=storage(),temporary=storage(),coordinate=coordinator()}={}){
  const drafts=new ComposerDrafts(local,{});drafts.coordinate=coordinate;
  const scope={gateway:'gateway',provider:'codex',account:'account',host:'local',session:'fixture'};
  const nodes={composer:{},message:{value:'same text'},'send-mode':{value:'send'},send:{},'send-error':{}};
  const calls=[];let sequence=0;const tab=++tabSequence;
  const context={$:id=>nodes[id],t:value=>value,BridgeI18n:{locale:()=> 'zh-CN'},
    codexQuotes:null,sending:false,currentId:'fixture',currentHost:'local',state:{},currentAccess:()=>({canSend:true}),
    attachments:{ready:()=>true,ids:()=>['attachment'],references:()=>[],rows:[{id:'attachment'}],setLocked(){},clear(){}},
    workModes:{value:()=> 'default',render(){},submitted(){}},invalidSkills:new Set(),selectedSkills:new Set(['skill']),
    codexProvisionalDrafts:new Map(),composerDrafts:drafts,codexDraftScope:durable?scope:null,messageManagement:null,renderQueue(){},
    sessionStorage:temporary,draftGateway:'gateway',chatKey:()=> 'local|fixture',uuid:()=> 'submission-'+tab+'-'+(++sequence),
    saveDraft:()=>drafts.save(scope,{text:nodes.message.value,sendMode:nodes['send-mode'].value,skills:['skill'],quotes:context.codexQuotes?.quotes||[]}),
    bumpRecency(){},renderSkillPills(){},restoringComposer:false,window:{BridgePresentation:{showError(){}}},sessionUrl:()=> '/synthetic/send',toast(){},
    api:async(url,body)=>{calls.push(body);return request?request(body):{status:'unknown'};}};
  const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
  const start=source.indexOf("$('composer').onsubmit=async event=>{"),end=source.indexOf("$('message').onkeydown",start);
  assert.ok(start>=0&&end>start,'Exercise the real composer submit handler');
  vm.runInNewContext(source.slice(start,end),context);
  return {nodes,calls,drafts,scope,context,temporary,
    submit:()=>nodes.composer.onsubmit({preventDefault(){}}),
    pending:()=>durable?drafts.pendingSend(scope):JSON.parse(temporary.getItem('pending:local|fixture'))};
}

for(const durable of [true,false]){
  test(`explicit rejection releases ${durable?'durable':'legacy'} pending ID and allows queueing`,async()=>{
    let first=true;
    const ui=fixture({durable,request:()=>{if(first){first=false;throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}return {status:'unknown'};}});
    await ui.submit();assert.equal(ui.pending(),null);
    assert.equal(ui.nodes.message.value,'same text');
    assert.equal(ui.drafts.load(ui.scope).text,'same text');
    ui.context.state.status='active';await ui.submit();
    assert.equal(ui.calls.length,2);assert.equal(ui.calls[1].mode,'queue');
    assert.notEqual(ui.calls[0].id,ui.calls[1].id);
    assert.deepEqual(Array.from(ui.calls[1].attachments),['attachment']);
    assert.deepEqual(Array.from(ui.calls[1].skills),['skill']);
    assert.equal(ui.pending().id,ui.calls[1].id,'Unknown delivery must still retain its ID');
  });

  for(const error of [{status:500,code:'send_not_submitted'},
    {status:400,message:'Codex 正在执行。请选择「排队发送」或「补充当前任务」。'},
    {status:409,code:'desktop_unavailable'},{message:'network disconnected'}]){
    test(`${durable?'durable':'legacy'} pending ID survives ${JSON.stringify(error)}`,async()=>{
      const ui=fixture({durable,request:()=>{throw Object.assign(Error('fixture failure'),error);}});
      await ui.submit();assert.equal(ui.pending().id,ui.calls[0].id);
      ui.context.state.status='active';await ui.submit();assert.equal(ui.calls.length,1);
    });
  }

  test(`late rejection cannot discard a newer ${durable?'durable':'legacy'} pending request`,async()=>{
    let reject;
    const ui=fixture({durable,request:()=>new Promise((resolve,fail)=>reject=fail)});
    const sending=ui.submit();
    while(!reject)await new Promise(resolve=>setImmediate(resolve));
    if(durable)await ui.drafts.beginSend(ui.scope,{text:'new text'},'new-id','new-attempt');
    else ui.temporary.setItem('pending:local|fixture',JSON.stringify({id:'new-id'}));
    ui.context.currentId='another-chat';
    reject(Object.assign(Error('busy'),{status:400,code:'send_not_submitted'}));
    await sending;assert.equal(ui.pending().id,'new-id');
  });
}

for(const first of ['old','new'])test(`two tabs retain same ID when ${first} rejection precedes unknown`,async()=>{
  const local=storage(),coordinate=coordinator();let rejectOld,resolveNew;
  const old=fixture({local,coordinate,request:()=>new Promise((resolve,reject)=>rejectOld=reject)});
  const newer=fixture({local,coordinate,request:()=>new Promise(resolve=>resolveNew=resolve)});
  // Distinct generated IDs identify the tab; begin must still reuse the logical ID.
  newer.context.uuid=()=> 'new-tab-'+Math.random();
  const oldTask=old.submit();while(!rejectOld)await new Promise(resolve=>setImmediate(resolve));
  const newTask=newer.submit();while(!resolveNew)await new Promise(resolve=>setImmediate(resolve));
  assert.equal(old.calls[0].id,newer.calls[0].id);
  if(first==='old'){rejectOld(Object.assign(Error('busy'),{status:400,code:'send_not_submitted'}));await oldTask;resolveNew({status:'unknown'});await newTask;}
  else {resolveNew({status:'unknown'});await newTask;rejectOld(Object.assign(Error('busy'),{status:400,code:'send_not_submitted'}));await oldTask;}
  assert.equal(old.pending().id,newer.calls[0].id);
  newer.context.state.status='active';await newer.submit();assert.equal(newer.calls.length,1);
});

test('newer rejection cannot release older in-flight attempt',async()=>{
  const local=storage(),coordinate=coordinator();let resolveOld;
  const old=fixture({local,coordinate,request:()=>new Promise(resolve=>resolveOld=resolve)});
  const newer=fixture({local,coordinate,request:()=>{throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}});
  const oldTask=old.submit();while(!resolveOld)await new Promise(resolve=>setImmediate(resolve));
  await newer.submit();assert.equal(newer.pending().id,old.calls[0].id);
  resolveOld({status:'unknown'});await oldTask;
  assert.equal(old.pending().id,old.calls[0].id);
});

test('all same-ID attempts explicitly rejected permits queueing',async()=>{
  const local=storage(),coordinate=coordinator();let rejectOld;
  const old=fixture({local,coordinate,request:()=>new Promise((resolve,reject)=>rejectOld=reject)});
  const newer=fixture({local,coordinate,request:()=>{throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}});
  const task=old.submit();while(!rejectOld)await new Promise(resolve=>setImmediate(resolve));
  await newer.submit();rejectOld(Object.assign(Error('busy'),{status:400,code:'send_not_submitted'}));await task;
  assert.equal(old.pending(),null);
});

test('legacy imported durable request remains untracked after rejection',async()=>{
  const ui=fixture({request:()=>{throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}});
  const body={text:'same text',mode:'send',skills:['skill'],workMode:'default',attachments:['attachment'],uiLocale:'zh-CN'};
  ui.drafts.begin(ui.scope,body,'old-page-id');await ui.submit();assert.equal(ui.pending().id,'old-page-id');
});

test('coordination failure without shared pending falls back without hanging',async()=>{
  const ui=fixture({coordinate:async()=>null});await ui.submit();assert.equal(ui.calls.length,1);assert.equal(ui.context.sending,false);
});

test('waiting registration locks double-clicks and captures original target',async()=>{
  let unlock;const wait=new Promise(resolve=>unlock=resolve);
  const ui=fixture({coordinate:async update=>{await wait;return update(true);}});
  ui.context.sessionUrl=(target,action,host)=>{assert.equal(target,'fixture');assert.equal(host,'local');return '/synthetic/send';};
  const task=ui.submit();await ui.submit();ui.context.currentId='another-chat';ui.context.currentHost='another-host';unlock();await task;
  assert.equal(ui.calls.length,1);
});

test('editing while confirmation waits preserves the current composer',async()=>{
  let release,entered;const wait=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve);let count=0;
  const ui=fixture({request:()=>({status:'accepted'}),coordinate:async update=>{if(++count===2){entered();await wait;}return update(true);}});
  const task=ui.submit();await started;
  ui.nodes.message.value='new edit';ui.context.saveDraft();release();await task;
  assert.equal(ui.nodes.message.value,'new edit');assert.equal(ui.drafts.load(ui.scope).text,'new edit');
});

test('without IndexedDB a fresh request uses private fallback and can queue after rejection',async()=>{
  const ui=fixture({request:()=>{throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}});ui.drafts.coordinator=undefined;
  await ui.submit();assert.equal(ui.calls.length,1);assert.equal(ui.drafts.pendingSend(ui.scope),null);
  assert.equal(ui.temporary.getItem('pending:local|fixture'),null);
  ui.context.state.status='active';await ui.submit();assert.equal(ui.calls.length,2);
});

test('without IndexedDB an existing durable unknown is blocked, preserving its ID',async()=>{
  const ui=fixture();ui.drafts.begin(ui.scope,{text:'same text'},'unknown-id');ui.drafts.coordinator=undefined;
  await ui.submit();assert.equal(ui.calls.length,0);assert.equal(ui.drafts.pending(ui.scope).id,'unknown-id');
});

test('unavailable local and session storage still retains a private unknown ID',async()=>{
  const unavailable={getItem(){throw Error('disabled');},setItem(){throw Error('disabled');},removeItem(){throw Error('disabled');}};
  const ui=fixture({local:unavailable,temporary:unavailable});await ui.submit();await ui.submit();
  assert.equal(ui.calls.length,2);assert.equal(ui.calls[0].id,ui.calls[1].id);
});

test('old confirmations and ignores cannot clear a newer coordinated request',async()=>{
  const drafts=new ComposerDrafts(storage(),{});drafts.coordinate=coordinator();const key={gateway:'g',provider:'codex',account:'a',host:'h',session:'s'};
  await drafts.beginSend(key,{text:'old'},'old-id','old-attempt');
  await drafts.beginSend(key,{text:'new'},'new-id','new-attempt');
  assert.equal(await drafts.settleSend(key,'old-id','old-attempt','confirm',0),false);
  assert.equal(await drafts.settleSend(key,'old-id',null,'ignore'),false);
  assert.equal(drafts.pendingSend(key).id,'new-id');
});

test('confirming imported legacy request does not resurrect its old pending record',async()=>{
  const ui=fixture({request:()=>({status:'accepted'})});
  const body={text:'same text',mode:'send',skills:['skill'],workMode:'default',attachments:['attachment'],uiLocale:'zh-CN'};
  ui.drafts.begin(ui.scope,body,'old-page-id');await ui.submit();assert.equal(ui.drafts.pendingSend(ui.scope),null);
  assert.equal(ui.drafts.pending(ui.scope).id,'old-page-id','Older page storage is untouched');
});

test('rejection preserves quoted text, attachments and selected Skill',async()=>{
  const ui=fixture({request:()=>{throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}});
  ui.context.codexQuotes={quotes:[{text:'quoted',source:'source'}],serialize:text=>'quoted\n'+text};
  await ui.submit();assert.equal(ui.nodes.message.value,'same text');assert.equal(ui.context.codexQuotes.quotes[0].text,'quoted');
  assert.equal(ui.drafts.load(ui.scope).quotes[0].text,'quoted');assert.deepEqual(Array.from(ui.context.selectedSkills),['skill']);
  assert.deepEqual(Array.from(ui.calls[0].attachments),['attachment']);
});

test('a late private confirmation cannot clear a newer pending request',async()=>{
  let accept;const ui=fixture({durable:false,request:()=>new Promise(resolve=>accept=resolve)});
  const task=ui.submit();ui.temporary.setItem('pending:local|fixture',JSON.stringify({id:'new-id',attempts:['new-attempt']}));
  accept({status:'accepted'});await task;assert.equal(ui.pending().id,'new-id');assert.equal(ui.nodes.message.value,'same text');
});

for(const kind of ['throw','error','blocked'])test(`IndexedDB open ${kind} safely falls back or blocks existing shared IDs`,async()=>{
  const ui=fixture();
  ui.drafts.coordinator={open(){if(kind==='throw')throw Error('denied');const request={};queueMicrotask(()=>request[kind==='blocked'?'onblocked':'onerror']());return request;}};
  delete ui.drafts.coordinate;
  await ui.submit();assert.equal(ui.calls.length,1);assert.equal(ui.context.sending,false);
  const existing=fixture();existing.drafts.coordinator=ui.drafts.coordinator;delete existing.drafts.coordinate;
  existing.drafts.begin(existing.scope,{text:'same text'},'unknown-id');await existing.submit();assert.equal(existing.calls.length,0);assert.equal(existing.drafts.pending(existing.scope).id,'unknown-id');
});

test('an IndexedDB timeout falls back once and ignores a late open callback',async()=>{
  const ui=fixture();let request,closed=0,transactions=0;
  ui.drafts.coordinator={open(){request={};return request;}};delete ui.drafts.coordinate;
  const start=Date.now();await ui.submit();assert.ok(Date.now()-start<3500);assert.equal(ui.calls.length,1);
  request.result={close(){closed++;},transaction(){transactions++;}};request.onsuccess();
  assert.equal(transactions,0);assert.equal(closed,1);assert.equal(ui.drafts.pendingSend(ui.scope),null);
});

test('aborted coordination after a local write never falls through to a new ID',async()=>{
  const ui=fixture();
  ui.drafts.coordinator={open(){
    const request={};queueMicrotask(()=>{request.result={close(){},transaction(){
      const transaction={objectStore(){return {get(){const get={};queueMicrotask(()=>{get.onsuccess();queueMicrotask(()=>transaction.onabort());});return get;}};},abort(){transaction.onabort();}};
      return transaction;
    }};request.onsuccess();});return request;
  }};delete ui.drafts.coordinate;
  await ui.submit();assert.equal(ui.calls.length,0);assert.ok(ui.drafts.pendingSend(ui.scope)?.id);assert.equal(ui.context.sending,false);
});

test('successful private send clears the composer when both storages are unavailable',async()=>{
  const unavailable={getItem(){throw Error('disabled');},setItem(){throw Error('disabled');},removeItem(){throw Error('disabled');}};
  const ui=fixture({local:unavailable,temporary:unavailable,request:()=>({status:'accepted'})});await ui.submit();
  assert.equal(ui.nodes.message.value,'');assert.equal(ui.drafts.temporaryPending.size,0);
});

test('a private unknown survives a later explicit rejection of its same-ID retry',async()=>{
  let count=0;const ui=fixture({durable:false,request:()=>{if(++count===1)return {status:'unknown'};throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}});
  await ui.submit();const id=ui.pending().id;await ui.submit();assert.equal(ui.pending().id,id);
  ui.context.state.status='active';await ui.submit();assert.equal(ui.calls.length,2);
});

test('a legacy second unknown is not hidden by a coordinated tombstone',async()=>{
  const local=storage(),ui=fixture({local});const body={text:'first',mode:'send'};
  ui.drafts.begin(ui.scope,body,'legacy-first');
  await ui.drafts.beginSend(ui.scope,body,'unused','first-attempt');
  await ui.drafts.settleSend(ui.scope,'legacy-first','first-attempt','confirm',0);
  const old=new ComposerDrafts(local);old.confirm(ui.scope,'legacy-first',0);
  old.begin(ui.scope,{text:'second',mode:'send'},'legacy-unknown');
  assert.equal(ui.drafts.pendingSend(ui.scope).id,'legacy-unknown');
  const changed=await ui.drafts.beginSend(ui.scope,{text:'second',mode:'queue'},'new-id','new-attempt');
  assert.equal(changed.blocked,true);assert.equal(old.pending(ui.scope).id,'legacy-unknown');
  const same=await ui.drafts.beginSend(ui.scope,{text:'second',mode:'send'},'another-id','same-attempt');
  assert.equal(same.id,'legacy-unknown');assert.equal(same.untracked,true);
  await ui.drafts.settleSend(ui.scope,same.id,'same-attempt','reject');
  assert.equal(ui.drafts.pendingSend(ui.scope).id,'legacy-unknown');
});

test('a legacy write after active v2 blocks replacement and prevents rejection release',async()=>{
  const local=storage(),ui=fixture({local});await ui.drafts.beginSend(ui.scope,{text:'same',mode:'send'},'active-id','active-attempt');
  const old=new ComposerDrafts(local);old.begin(ui.scope,{text:'same',mode:'send'},'legacy-unknown');
  const retry=await ui.drafts.beginSend(ui.scope,{text:'same',mode:'send'},'new-id','new-attempt');assert.equal(retry.blocked,true);
  await ui.drafts.settleSend(ui.scope,'active-id','active-attempt','reject');
  assert.equal(ui.drafts.pendingSend(ui.scope).id,'active-id');assert.equal(old.pending(ui.scope).id,'legacy-unknown');
  await ui.drafts.settleSend(ui.scope,'legacy-unknown',null,'ignore');
  assert.equal(ui.drafts.pendingSend(ui.scope).id,'active-id','Explicitly ignoring v1 preserves active v2');
});

test('real handler blocks queue after an old page writes its second unknown',async()=>{
  const local=storage(),ui=fixture({local}),old=new ComposerDrafts(local);
  const first={text:'first'};old.begin(ui.scope,first,'first-id');
  await ui.drafts.beginSend(ui.scope,first,'unused','first-attempt');
  await ui.drafts.settleSend(ui.scope,'first-id','first-attempt','confirm',0);old.confirm(ui.scope,'first-id',0);
  const second={text:'same text',mode:'send',skills:['skill'],workMode:'default',attachments:['attachment'],uiLocale:'zh-CN'};
  old.begin(ui.scope,second,'second-unknown');
  // The old page's request delivered an unknown response, so it keeps v1 intact.
  ui.context.state.status='active';await ui.submit();
  assert.equal(ui.calls.length,0);assert.equal(old.pending(ui.scope).id,'second-unknown');
  ui.context.state.status='idle';await ui.submit();
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].id,'second-unknown');
  assert.equal(ui.drafts.pendingSend(ui.scope).id,'second-unknown');
});
