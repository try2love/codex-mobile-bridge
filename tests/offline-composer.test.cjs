'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/features/chat/offline-reader/composer-store.js'), 'utf8');
const auth = {authenticated:true, draftIdentity:'gateway', sessionSync:{version:1, gatewayId:'a'.repeat(32), identity:'b'.repeat(64)}};
const meta = {account:'provider-account', gateway:'gateway', canSend:true, title:'草稿会话'};
const route = {provider:'codex', host:'local', session:'01a210fd-3c59-7c00-b093-1433e9d65e13'};
const text = text => ({text, quotes:[], skills:[], workMode:'default'});
const body = value => ({text:value, mode:'send'});
const clone = value => JSON.parse(JSON.stringify(value));
function archive() {
  const records = new Map();
  return async (type, fields) => {
    const owner = [fields.gatewayId || auth.sessionSync.gatewayId, fields.identity || auth.sessionSync.identity].join('.');
    if (type === 'composerPending') return {sessions:[...records.entries()].filter(([key,r]) => key.startsWith(owner + '|') && r.value.outbox?.length).map(([key]) => key.slice(owner.length + 1))};
    const key = owner + '|' + fields.sessionId, old = records.get(key) || {revision:0,value:null};
    if (type === 'composerRead') return clone(old);
    assert.equal(type, 'composerWrite');
    if (old.revision !== fields.revision) return {...clone(old), applied:false};
    const row = {revision:old.revision + 1, value:clone(fields.value)};
    records.set(key,row);
    return {...clone(row), applied:true};
  };
}
function device(native, cold = false) {
  const win = new EventTarget(), document = new EventTarget(), navigator = {onLine:false};
  win.OfflineChatReader = {cold, enabled:cold, composer:native};
  if (!cold) win.CodexBridgeNativeOffline = {request:fields => native(fields.type, fields)};
  const context = {window:win, document, navigator, CustomEvent, URLSearchParams,
    crypto:require('node:crypto').webcrypto, setTimeout:() => 0, clearTimeout() {},
    ClientIdentity:{path:(p, a, sid, params) => '/api/desktop-sessions/' + p + '/' + a + '?' + new URLSearchParams({...params, sessionId:sid})}};
  vm.runInNewContext(source, context);
  return {store:win.BridgeOfflineComposer, navigator};
}
function gateway() {
  const tracker = {posts:[], reads:[], receipts:new Map(), account:meta.account, ready:true, auth};
  tracker.request = async (url, data) => {
    if (url === '/api/auth') return tracker.auth;
    const parsed = new URL(url, 'http://gateway'), id = parsed.searchParams.get('id');
    assert.equal(parsed.searchParams.get('gatewayId'), auth.sessionSync.gatewayId);
    if (!data) {
      tracker.reads.push(id);
      return {deliveryVersion:1, account:tracker.account, ready:tracker.ready, status:tracker.receipts.get(id) || 'notFound'};
    }
    tracker.posts.push({...data, path:parsed.pathname});
    assert.equal(data.id, id);
    if (tracker.send) return tracker.send(data);
    tracker.receipts.set(id,'accepted'); return {id,status:'accepted'};
  };
  return tracker;
}
test('hot and cold archives preserve queued A and independent draft B, across a second gateway view', async () => {
  const native = archive(), gatewayApi = gateway(), first = device(native);
  first.store.bind(auth,gatewayApi.request);
  await first.store.observe(route,meta);
  const sealed = {...body('A'),attachments:['already-uploaded'],references:[{path:'a.md'}],skills:['skill-a']};
  await Promise.all([first.store.saveDraft(route,text('A')),first.store.enqueue(route,sealed),first.store.saveDraft(route,text('B'))]);
  await first.store.saveContext(route,{usage:{syncedAt:1590100000}},meta.account);
  await first.store.savePreferences({layout:{topLeft:['context','more']}});
  const cold = device(native,true);
  const reopened = await cold.store.get(route);
  assert.equal(reopened.draft.text,'B');
  assert.deepEqual(clone(reopened.outbox[0].body),sealed);
  assert.equal(reopened.context.usage.syncedAt,1590100000);
  assert.deepEqual(clone(await cold.store.preferences()),{layout:{topLeft:['context','more']}});
  await cold.store.edit(route,reopened.outbox[0].id,'A edited');
  await cold.store.enqueue(route,body('withdraw me'));
  const pending = await cold.store.get(route);
  await cold.store.remove(route,pending.outbox[1].id);
  await cold.store.saveDraft(route,text('B'));
  const second = device(native), third = device(native);
  for (const visit of [second,third]) { visit.store.bind(auth,gatewayApi.request); visit.navigator.onLine = true; }
  await Promise.all([second.store.flush(),third.store.flush()]);
  assert.equal(gatewayApi.posts.length,1);
  assert.equal(gatewayApi.posts[0].text,'A edited');
  assert.deepEqual(clone(gatewayApi.posts[0].attachments),sealed.attachments);
  const finished = await second.store.get(route);
  assert.equal(finished.outbox.length,0);
  assert.equal(finished.draft.text,'B');
});
test('lost or missing receipt survives a restart and only GETs the original ID', async () => {
  const native = archive(), api = gateway(), a = device(native);
  a.store.bind(auth,api.request); await a.store.observe(route,meta);
  await a.store.enqueue(route,body('A'));
  await a.store.enqueue(route,body('later'));
  api.send = () => { throw Error('socket closed'); };
  a.navigator.onLine = true; await a.store.flush();
  const id = api.posts[0].id;
  assert.equal((await a.store.get(route)).outbox[0].state,'checking');
  await assert.rejects(a.store.edit(route,id,'duplicate'));
  await assert.rejects(a.store.remove(route,id));
  const b = device(native); b.store.bind(auth,api.request); b.navigator.onLine = true;
  await b.store.flush();
  assert.deepEqual(api.posts.map(row => row.text),['A']);
  assert.equal(api.reads.at(-1),id);
  api.receipts.set(id,'accepted'); api.send = null;
  await b.store.flush();
  assert.deepEqual(api.posts.map(row => row.text),['A','later']);
  assert.equal((await b.store.get(route)).outbox.length,0);
});
test('device login, provider binding, live account and send capability all gate an unattempted message', async () => {
  const native = archive(), api = gateway(), a = device(native);
  a.store.bind(auth,api.request); await a.store.observe(route,meta); await a.store.saveDraft(route,text('old draft'));
  await a.store.enqueue(route,body('private')); await a.store.saveDraft(route,text('B'));
  a.navigator.onLine = true;
  api.auth = {...auth, sessionSync:{...auth.sessionSync, identity:'c'.repeat(64)}};
  await a.store.flush(); assert.equal(api.reads.length,0);
  api.auth = auth; api.account = 'new-account'; await a.store.flush();
  api.account = meta.account; api.ready = false; await a.store.flush(); assert.equal(api.posts.length,0);
  await a.store.invalidate('codex','local');
  const b = device(native,true);
  const hidden = await b.store.get(route);
  assert.equal(hidden.draft,null); assert.equal(hidden.outbox.length,0);
  await assert.rejects(b.store.enqueue(route,body('unbound')));
  a.navigator.onLine = false;
  await a.store.observe(route,{...meta,account:'new-account'});
  assert.equal((await a.store.get(route)).outbox.length,0);
  await a.store.saveDraft(route,text('new account draft'));
  await a.store.observe(route,meta);
  const previous = await a.store.get(route);
  assert.equal(previous.draft.text,'B'); assert.equal(previous.outbox[0].body.text,'private');
  a.store.suspend();
  await assert.rejects(a.store.get(route));
  a.store.bind(api.auth = {...auth, sessionSync:{...auth.sessionSync, identity:'d'.repeat(64)}}, api.request);
  assert.equal(await a.store.get(route),null);
});
test('explicit pre-dispatch rejection can wait for reconnection, a legacy gateway cannot claim pending', async () => {
  const native = archive(), api = gateway(), a = device(native); let legacy = true;
  const request = (url,body) => legacy && url !== '/api/auth' ? {status:'notFound'} : api.request(url,body);
  a.store.bind(auth,request); await a.store.observe(route,meta); await a.store.enqueue(route,body('A'));
  a.navigator.onLine = true; await a.store.flush(); assert.equal((await a.store.get(route)).outbox[0].state,'pending');
  legacy = false;
  api.send = () => { throw Object.assign(Error('stopped before dispatch'),{code:'offline_delivery_wait'}); };
  await a.store.flush(); assert.equal((await a.store.get(route)).outbox[0].state,'pending');
  api.send = null; await a.store.flush(); assert.equal((await a.store.get(route)).outbox.length,0);
  assert.equal(api.posts[0].id,api.posts[1].id);
});

test('rejected messages edit in place with a new ID and concurrent recovery preserves order and draft', async () => {
  const native = archive(), api = gateway(), a = device(native);
  a.store.bind(auth,api.request); await a.store.observe(route,meta);
  await a.store.enqueue(route,body('A'));
  await a.store.enqueue(route,body('later'));
  await a.store.saveDraft(route,text('B'));
  const before = await a.store.get(route), oldId = before.outbox[0].id;
  api.send = row => {
    api.receipts.set(row.id,'rejected');
    throw Object.assign(Error('Skill unavailable'),{code:'offline_delivery_rejected'});
  };
  a.navigator.onLine = true; await a.store.flush();
  assert.equal((await a.store.get(route)).outbox[0].state,'failed');
  const edited = await a.store.edit(route,oldId,'A edited');
  assert.notEqual(edited.outbox[0].id,oldId);
  assert.equal(edited.outbox[0].state,'pending');
  assert.equal(edited.outbox[0].error,undefined);
  assert.equal(edited.outbox[1].id,before.outbox[1].id);
  assert.equal(edited.draft.text,'B');
  // A stale editor cannot overwrite or delete the replacement request.
  await assert.rejects(a.store.edit(route,oldId,'stale edit'));
  await a.store.remove(route,oldId);
  api.send = null;
  const other = device(native); other.store.bind(auth,api.request); other.navigator.onLine = true;
  await Promise.all([a.store.flush(),other.store.flush()]);
  assert.deepEqual(api.posts.map(row=>row.text),['A','A edited','later']);
  assert.equal((await other.store.get(route)).draft.text,'B');
});

test('lost rejection response becomes editable only after a durable rejected receipt', async () => {
  const native=archive(), api=gateway(), a=device(native);
  a.store.bind(auth,api.request); await a.store.observe(route,meta);
  await a.store.enqueue(route,body('A')); await a.store.enqueue(route,body('later'));
  await a.store.saveDraft(route,text('B'));
  api.send = row => { api.receipts.set(row.id,'rejected'); throw Error('response lost'); };
  a.navigator.onLine=true; await a.store.flush();
  const id=(await a.store.get(route)).outbox[0].id;
  assert.equal((await a.store.get(route)).outbox[0].state,'checking');
  await assert.rejects(a.store.edit(route,id,'too early'));
  const reopened=device(native); reopened.store.bind(auth,api.request); reopened.navigator.onLine=true;
  await reopened.store.flush();
  assert.equal((await reopened.store.get(route)).outbox[0].state,'failed');
  assert.equal(api.posts.length,1);
  await reopened.store.remove(route,id);
  api.send=null; await reopened.store.flush();
  assert.deepEqual(api.posts.map(row=>row.text),['A','later']);
  assert.equal((await reopened.store.get(route)).draft.text,'B');
});

test('an old account observation already in flight cannot authorize the next account after a switch',async () => {
  const native = archive(), api = gateway(); let release, started;
  const entering = new Promise(resolve => { started = resolve; });
  let hold = true;
  const slow = async (kind,fields) => {
    if (hold && fields.sessionId?.startsWith('[') && kind === 'composerRead') {
      hold=false; started(); await new Promise(resolve => { release = resolve; });
    }
    return native(kind,fields);
  };
  const phone = device(slow); phone.store.bind(auth,api.request);
  const old = phone.store.observe(route,meta);
  await entering; await phone.store.invalidate('codex','local'); release();
  await assert.rejects(old);
  assert.equal(await phone.store.get(route),null);
  await assert.rejects(phone.store.enqueue(route,body('wrong account')));
});
