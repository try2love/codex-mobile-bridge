const {test}=require('node:test');
const assert=require('node:assert/strict');
const {ComposerDrafts}=require('../web/features/chat/composer-drafts.js');

function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};}
const scope=(session,account='one')=>({gateway:'gateway',provider:'codex',account,host:'local',session});

test('durable drafts survive a new document and isolate chat, host, provider, gateway and account',()=>{
  const local=storage(),first=new ComposerDrafts(local);
  first.save(scope('A'),{text:'unsent',selectionStart:3,selectionEnd:3,workMode:'plan',skills:['s']});
  const next=new ComposerDrafts(local);
  assert.equal(next.load(scope('A')).text,'unsent');
  assert.equal(next.load(scope('A')).selectionStart,3);
  for(const changed of [{session:'B'},{host:'ssh'},{provider:'claude'},{gateway:'other'},{account:'two'}])assert.equal(next.load({...scope('A'),...changed}),null);
});

test('confirmed send clears only the submitted revision',()=>{
  const local=storage(),drafts=new ComposerDrafts(local),key=scope('A');
  const sent=drafts.save(key,{text:'first'});
  const pending=drafts.begin(key,{text:'first',mode:'send'},'id-1');
  drafts.save(key,{text:'later'});
  drafts.confirm(key,pending.id,sent.revision);
  assert.equal(drafts.load(key).text,'later');
  assert.equal(drafts.pending(key),null);
});

test('failed or unknown send keeps its durable id until an explicit retry or confirmation',()=>{
  const local=storage(),drafts=new ComposerDrafts(local),key=scope('A');
  const pending=drafts.begin(key,{text:'first'},'id-1');
  const reopened=new ComposerDrafts(local);
  assert.equal(reopened.pending(key).id,'id-1');
  assert.equal(reopened.begin(key,{text:'first'},'id-2').id,'id-1');
  assert.equal(reopened.begin(key,{text:'changed'},'id-3').id,'id-3');
  assert.notEqual(pending.id,reopened.pending(key).id);
});

test('missing uploaded attachment references cannot turn an uncertain send into a new id',()=>{
  const drafts=new ComposerDrafts(storage()),key=scope('A');
  drafts.begin(key,{text:'same',attachments:['uploaded']},'id-1');
  const retry=drafts.begin(key,{text:'same',attachments:[]},'id-2');
  assert.equal(retry.blocked,true);
  assert.equal(drafts.pending(key).id,'id-1');
});

test('old acknowledgement cannot clear a newer pending submission',()=>{
  const drafts=new ComposerDrafts(storage()),key=scope('A');
  const first=drafts.save(key,{text:'first'}),pending=drafts.begin(key,{text:'first'},'id-1');
  drafts.save(key,{text:'second'});drafts.begin(key,{text:'second'},'id-2');
  assert.equal(drafts.confirm(key,pending.id,first.revision),false);
  assert.equal(drafts.load(key).text,'second');
  assert.equal(drafts.pending(key).id,'id-2');
});

test('legacy temporary draft migrates only under an authenticated scope',()=>{
  const local=storage(),temporary=storage(),drafts=new ComposerDrafts(local);
  temporary.setItem('draft:local|A','old');
  assert.equal(drafts.load(scope('A')),null);
  const migrated=drafts.migrate(scope('A'),temporary,{text:'draft:local|A'});
  assert.equal(migrated.text,'old');
  assert.equal(temporary.getItem('draft:local|A'),null);
  assert.equal(new ComposerDrafts(local).load(scope('A')).text,'old');
});

test('failed durable write leaves temporary draft available for migration later',()=>{
  const temporary=storage(),failing={getItem:()=>null,setItem:()=>{throw Error('quota');},removeItem:()=>{}};
  temporary.setItem('draft:local|A','old');
  assert.equal(new ComposerDrafts(failing).migrate(scope('A'),temporary,{text:'draft:local|A'}),null);
  assert.equal(temporary.getItem('draft:local|A'),'old');
});

test('explicit account continuation copies only draft content within the same complete scope',()=>{
  const drafts=new ComposerDrafts(storage()),previous=scope('original'),next={...previous,account:'two'};
  drafts.save(previous,{text:'unsent',selectionStart:2,selectionEnd:5,workMode:'plan',skills:['old-skill'],quotes:[{text:'quoted',source:'original reply'}]});
  const saved=drafts.continueAccount(previous,next);assert.equal(saved.text,'unsent');assert.equal(saved.selectionStart,2);assert.equal(saved.selectionEnd,5);assert.equal(saved.quotes[0].text,'quoted');assert.equal(saved.workMode,'default');assert.deepEqual(saved.skills,[]);
  assert.equal(drafts.load(previous).text,'unsent');assert.equal(drafts.pending(next),null);
  for(const different of [{gateway:'other'},{host:'other'},{provider:'claude'},{session:'other'}])assert.equal(drafts.continueAccount(previous,{...next,...different}),null);
});
test('account continuation never copies pending IDs or overwrites another account draft',()=>{
  for(const side of ['previous','next']){const drafts=new ComposerDrafts(storage()),previous=scope('same'),next={...previous,account:'two'};drafts.save(previous,{text:'possibly sent'});drafts.begin(side==='previous'?previous:next,{text:'possibly sent'},'unknown-id');const old=JSON.stringify(drafts.pending(side==='previous'?previous:next));assert.equal(drafts.continueAccount(previous,next),null);assert.equal(JSON.stringify(drafts.pending(side==='previous'?previous:next)),old);}
  const drafts=new ComposerDrafts(storage()),previous=scope('same'),next={...previous,account:'two'};drafts.save(previous,{text:'old'});drafts.save(next,{text:'newer draft'});assert.equal(drafts.continueAccount(previous,next),null);assert.equal(drafts.load(next).text,'newer draft');
});

test('account continuation leaves v2 unknown and memory pending records in their original scope',()=>{
  for(const kind of ['v2','memory']){
    const drafts=new ComposerDrafts(storage()),previous=scope('same'),next={...previous,account:'two'};drafts.save(previous,{text:'possibly sent'});
    const record={id:'unknown-v2',body:{text:'possibly sent'},gateway:previous.gateway,provider:previous.provider,account:previous.account,target:previous.host+'|'+previous.session};
    if(kind==='v2')drafts.write(drafts.key(previous,'pending-v2'),record);else drafts.temporaryPending.set('pending:'+record.target,record);
    assert.equal(drafts.continueAccount(previous,next),null);assert.equal(drafts.load(next),null);assert.equal(drafts.pendingRecords(previous)[0].id,'unknown-v2');assert.equal(drafts.pendingRecords(next).length,0);
  }
});
