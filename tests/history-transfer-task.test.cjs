'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const HistoryTransferTask = require('../web/features/chat/history-transfer-task.js');

function fixture(items, configure = () => {}) {
  const chunks = new Set(), committed = new Set(), calls = [];
  const state = {freeBytes: 1e9, clearRevision: '0', allowTransfer: true, networkAvailable: true};
  let saved = null;
  const environment = {chunks, committed, calls, state, get saved() { return saved; }};
  const request = async (url, body) => {
    calls.push({url, body});
    if (body) return {id: 'manifest', capturedAt: 100};
    return {id: 'manifest', status: 'ready', capturedAt: 100,
      summary: {sessions: 1, records: 1, attachments: 1, textBytes: 8, attachmentBytes: 6}, items};
  };
  const native = async (type, value = {}) => {
    calls.push({type, ...value});
    if (environment.before) await environment.before(type, value);
    if (type === 'transferStats') return {...state};
    if (type === 'transferTaskSave') { saved = structuredClone(value.task); return {saved: true}; }
    if (type === 'transferTaskLoad') return {task: saved};
    if (type === 'transferCached') return {present: committed.has(value.version), cleared: environment.cleared?.has(value.version)};
    if (type === 'transferChunkHas') return {present: chunks.has(value.version + ':' + value.index)};
    if (type === 'transferSessionComplete') return {saved: true};
    if (type === 'transferChunkFetch') {
      if (environment.fetch) return environment.fetch(value);
      chunks.add(value.version + ':' + value.index); return {saved: true};
    }
    if (type.endsWith('Commit')) { committed.add(value.version); return {saved: true}; }
    throw Error('Unexpected native operation ' + type);
  };
  configure(environment);
  const manager = new HistoryTransferTask({request, native, delay: async () => {}});
  return {...environment, manager, request, native, environment};
}
const item = (version, kind = 'record', bytes = 8) => ({
  id: version, version, recordVersion: 'original-version', key: version, kind, bytes, chunkBytes: 4,
  cacheSessionId: '["codex","local","thread"]', turnId: 'turn', questionTime: 1,
  session: {title: 'chat'},
});
async function settled(manager) {
  for (let attempt = 0; attempt < 100 && manager.running; attempt++)
    await new Promise(resolve => setImmediate(resolve));
  assert.equal(manager.running, false, 'task loop should settle');
}

test('existing complete records transfer zero bytes and only missing attachment segments count', async () => {
  const f = fixture([item('text'), item('file', 'attachment', 6)], e => {
    e.committed.add('text'); e.chunks.add('file:0');
  });
  const prepared = await f.manager.prepare(['selected']);
  assert.equal(prepared.downloadBytes, 14);
  assert.equal(f.calls.some(call=>call.type==='transferCached'||call.type==='transferChunkHas'),false,'preparation does not preflight every cached record');
  await f.manager.start(prepared); await settled(f.manager);
  const downloads = f.calls.filter(call => call.type === 'transferChunkFetch');
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].version, 'file'); assert.equal(downloads[0].offset, 4); assert.equal(downloads[0].length, 2);
  assert.equal(f.manager.task.state, 'completed');
});

test('all selected text is saved before attachments even when manifest items are interleaved', async () => {
  const f = fixture([item('image', 'attachment'), item('first'), item('file', 'attachment'), item('second')]);
  await f.manager.start(await f.manager.prepare(['selected'])); await settled(f.manager);
  assert.deepEqual(f.calls.filter(call => call.type?.endsWith('Commit')).map(call => call.version), ['first', 'second', 'image', 'file']);
});

test('metered transition saves an admitted chunk and pauses subsequent requests, Wi-Fi resumes missing chunks', async () => {
  const f = fixture([item('text')]);
  f.environment.fetch = async value => {
    f.chunks.add(value.version + ':' + value.index);
    if (value.index === 0) f.state.allowTransfer = false;
    return {saved: true};
  };
  await f.manager.start(await f.manager.prepare(['selected'])); await settled(f.manager);
  assert.equal(f.manager.task.reason, 'network');
  assert.equal(f.calls.filter(call => call.type === 'transferChunkFetch').length, 1);
  f.state.allowTransfer = true;
  await f.manager.network({...f.state}); await settled(f.manager);
  assert.equal(f.manager.task.state, 'completed');
  assert.deepEqual(f.calls.filter(call => call.type === 'transferChunkFetch').map(call => call.index), [0, 1]);
});

test('storage pause preserves the manifest and continues from the original segment after space is available', async () => {
  const f = fixture([item('text')]);
  const prepared = await f.manager.prepare(['selected']); f.state.freeBytes = 1;
  await f.manager.start(prepared); await settled(f.manager);
  assert.equal(f.manager.task.reason, 'space');
  assert.equal(f.calls.some(call => call.type === 'transferChunkFetch'), false);
  f.state.freeBytes = 100;
  await f.manager.resume(); await settled(f.manager);
  assert.equal(f.manager.task.state, 'completed');
  assert.equal(f.calls.filter(call => call.body).length, 1, 'continue must use the original manifest');
});

test('cancelling retains verified chunks and a later explicit transfer downloads only the remainder', async () => {
  const f = fixture([item('text')]);
  f.environment.fetch = async value => {
    f.chunks.add(value.version + ':' + value.index);
    await f.manager.cancel(); return {saved: true};
  };
  await f.manager.start(await f.manager.prepare(['selected'])); await settled(f.manager);
  assert.equal(f.manager.task.state, 'cancelled'); assert.equal(f.chunks.size, 1);
  delete f.environment.fetch;
  const again = await f.manager.prepare(['selected']); assert.equal(again.downloadBytes, 14);
  await f.manager.start(again); await settled(f.manager);
  assert.equal(f.manager.task.state, 'completed');
});

test('a cleared turn is skipped and the other selected turns keep downloading', async () => {
  const f = fixture([item('cleared'), item('kept')], e => { e.cleared = new Set(['cleared']); });
  await f.manager.start(await f.manager.prepare(['selected'])); await settled(f.manager);
  assert.equal(f.manager.task.clearedItems, 1);
  assert.equal(f.committed.has('kept'), true);
  assert.equal(f.calls.some(call => call.type === 'transferChunkFetch' && call.version === 'cleared'), false);
});

test('persisted unfinished task reopens without making a new manifest or fetching saved chunks', async () => {
  const f = fixture([item('text')]);
  f.environment.fetch = async value => {
    f.chunks.add(value.version + ':' + value.index);
    f.state.networkAvailable = false; return {saved: true};
  };
  await f.manager.start(await f.manager.prepare(['selected'])); await settled(f.manager);
  f.state.networkAvailable = true; delete f.environment.fetch;
  const reopened = new HistoryTransferTask({request: f.request, native: f.native, delay: async () => {}});
  await reopened.restore(); await settled(reopened);
  assert.equal(reopened.task.state, 'completed');
  assert.equal(f.calls.filter(call => call.body).length, 1);
  assert.deepEqual(f.calls.filter(call => call.type === 'transferChunkFetch').map(call => call.index), [0, 1]);
});

test('an unfinished live tail remains visibly partial after copying the saved boundary',async()=>{
 const row=item('saved'),f=fixture([row]);
 const prepared=await f.manager.prepare(['selected']);
 prepared.warnings=[{cacheSessionId:row.cacheSessionId,message:'Unflushed native tail'}];
 await f.manager.start(prepared);await settled(f.manager);
 assert.deepEqual(f.manager.task.incompleteSessions,[row.cacheSessionId]);
 assert.equal(f.calls.some(call=>call.type==='transferSessionComplete'),false);
 assert.equal(f.manager.task.state,'completed');
});

test('preparation persists its manifest and resumes without posting a second request',async()=>{
 let saved=null,posts=0,release;
 const gate=new Promise(resolve=>{release=resolve;});
 const native=async(type,value)=>type==='transferStats'?{clearRevision:0}:type==='transferTaskSave'?(saved=structuredClone(value.task),{}):type==='transferTaskLoad'?{task:saved}:{};
 const request=async(url,body)=>{
  if(body){posts++;return {id:'active',sessionCount:3,capturedAt:100};}
  await gate;return {status:'ready',summary:{textBytes:54},items:[]};
 };
 const first=new HistoryTransferTask({request,native});
 const prepare=first.prepare(['one']);
 for(let i=0;i<5;i++)await new Promise(setImmediate);
 assert.equal(saved.state,'preparing');assert.equal(saved.manifestId,'active');
 const restored=new HistoryTransferTask({request,native});await restored.restore();
 release();await prepare;await restored.preparing;
 assert.equal(posts,1);assert.equal(restored.task.state,'ready');assert.equal(restored.task.prepared.downloadBytes,54);
});

test('batch records require both gateway and native capability',async()=>{
 const f=fixture([]);f.manager.recordBatch=1;
 const original=f.manager.native;f.manager.native=async(type,...args)=>type==='transferStats'?{...f.state,recordBatch:1}:original(type,...args);
 await f.manager.prepare(['selected']);
 assert.equal(f.calls.find(call=>call.body).body.batchRecords,true);
});


test('closing a completed card persists its dismissal across controller restoration and leaves archived content intact',async()=>{
 const f=fixture([item('saved')]);
 await f.manager.start(await f.manager.prepare(['selected']));await settled(f.manager);
 assert.equal(f.manager.task.state,'completed');
 await f.manager.dismiss();
 const restored=new HistoryTransferTask({request:f.request,native:f.native,delay:async()=>{}});
 await restored.restore();
 assert.equal(restored.task.dismissed,true);
 assert(f.committed.has('saved'),'closing a card does not erase saved history');
 const prepared=await restored.prepare(['selected']);
 assert.notEqual(restored.task.dismissed,true,'a new task must have a visible card');
 await restored.start(prepared);await settled(restored);
 assert.notEqual(restored.task.dismissed,true);
});

test('legacy completed tasks without a close field stay hidden while new unclosed completions remain visible',async()=>{
 const f=fixture([item('saved')]);
 await f.manager.start(await f.manager.prepare(['selected']));await settled(f.manager);
 const current=new HistoryTransferTask({request:f.request,native:f.native,delay:async()=>{}});
 await current.restore();assert.equal(current.task.dismissed,false);
 const legacy=structuredClone(f.manager.task);delete legacy.dismissed;
 await f.native('transferTaskSave',{task:legacy});
 const restored=new HistoryTransferTask({request:f.request,native:f.native,delay:async()=>{}});
 await restored.restore();assert.equal(restored.task.dismissed,true);
 assert(f.committed.has('saved'));assert.equal(f.environment.saved.dismissed,true);
});
