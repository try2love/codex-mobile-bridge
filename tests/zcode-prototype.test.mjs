import test from 'node:test';
import assert from 'node:assert/strict';
import { nativeRequest } from '../bridge/clients/zcode/renderer.mjs';
import { verifiedTarget } from '../bridge/clients/zcode/transport.mjs';

function fixture() {
  const binding = { clientId: 'original-client', connectionId: 'original-port',
    runtime: { identity: 'original-owner:1', workspaceKey: '/project' } };
  const snapshot = { sessionId: 'sess-test', control: { sessionEnded: true, canStop: false, activeWorks: [] },
    revision: 7, logEpoch: 'epoch', config: {provider:'account:test',model:'Flash',thought:'low'}, pendingInteractions: [], queue: { items: [] }, inputRouting: { mode: 'startNow' } };
  const calls = [], listeners = new Set();
  const storeState = {status:'live', snapshot, subscriptionId:'sub'};
  const layer = {entries:new Map([['conversation/sess-test',{}]]), acquire:()=>{calls.push('acquire');return {store:{getState:()=>storeState},release:()=>calls.push('release')};}};
  const agent = {
    helloConversationV4: async () => ({ protocolVersion: 3, clientMode: 'desktop-continuous', connectionId: binding.connectionId }),
    getWorkspaceRuntimeIdentity: async p => { assert.equal(p.runtimePolicy, 'existing-only'); return structuredClone(binding.runtime); },
    readSession: async p => { assert.equal(p.runtimePolicy, 'existing-only'); return {
      session: { sessionId: 'sess-test', workspace: { workspacePath: '/project' } }, messages: [
        { info: { role: 'user', synthetic: true, visibility: 'model-only' }, parts: [] },
        { info: { role: 'user', semantics: { uiVisibility: 'visible' } }, parts: [{ text: 'visible' }] }] }; },
    subscribeConversationV4: async () => { calls.push('subscribe'); return { ack: { subscriptionId: 'sub' } }; },
    onDynamicConversationFrame: () => listener => { listeners.add(listener); return { dispose: () => listeners.delete(listener) }; },
    resyncConversationV4: async () => { for (const listener of listeners) listener({ kind: 'complete',
      frame: { subscriptionId: 'sub', payload: { kind: 'snapshot', snapshot } } }); },
    unsubscribeConversationV4: async () => { calls.push('unsubscribe'); },
    sendConversationCommandV4: async p => { calls.push(p); return { status: 'accepted', commandId: p.envelope.commandId }; },
    queryConversationCommandsV4: async () => ({ results: [{ result: 'unknown' }] }),
    attachmentBeginV4: async p => { calls.push({ upload: p }); },
    attachmentChunkV4: async p => { calls.push({ chunk: p }); },
    attachmentCommitV4: async () => ({ ref: 'native-committed-ref' }),
  };
  globalThis.window = {};
  globalThis.document = { querySelectorAll: () => [{ __reactContainer$test: {
    memoizedProps: { value: { zcodeAgentService: agent, oauthService:{getActiveProvider:async()=>'test'}, credentialService:{load:async()=>JSON.stringify({id:'user',name:'tester'})}, modelSelectionService:{getView:async()=>({providers:[{providerId:'account:test',providerName:'Test',models:[{modelId:'Flash',config:{enabled:true,optionSpecs:{reasoningLevel:{values:['low','high']}}}}]}]})}, layer } } } }] };
  globalThis.localStorage = { getItem: key => { assert.equal(key, 'zcode-v4-client-id:v1'); return binding.clientId; } };
  const request = (action, body = {}, extra = {}) => nativeRequest({ action, body, workspace: '/project',
    sessionId: 'sess-test', binding: structuredClone(binding), expiresAt: Date.now() + 10000, ...extra });
  return { request, calls, snapshot, binding, agent, listeners, storeState };
}

test('passive history never subscribes or starts an executor and hides model-only context', async () => {
  const f = fixture();
  const result = await f.request('history');
  assert.equal(result.messages.length, 1);
  assert.deepEqual(f.calls, []);
});

test('reattach is explicit; repeated attach and reads do not replace a native subscription', async () => {
  const f = fixture();
  await assert.rejects(f.request('detail'), /detached/);
  await f.request('attach'); await f.request('attach'); await f.request('detail');
  assert.deepEqual(f.calls, ['acquire']);
  assert.equal(f.listeners.size, 0);
  await f.request('detach');
  await assert.rejects(f.request('detail'), /detached/);
});

test('owner change and expired requests fail before mutation', async () => {
  const f = fixture(); await f.request('attach');
  const old = structuredClone(f.binding); f.binding.runtime.identity = 'different-owner';
  await assert.rejects(f.request('send', { id: 'once', text: 'hello' }, { binding: old }), /owner changed/);
  await assert.rejects(f.request('send', {}, { expiresAt: 1 }), /expired/);
  assert.deepEqual(f.calls, ['acquire']);
});

test('unavailable native projection fails immediately without resync or replacing the desktop subscription', async () => {
  const f = fixture(); await f.request('attach');
  f.storeState.status = 'error';
  await assert.rejects(f.request('detail'), /尚未就绪/);
  assert.equal(f.listeners.size, 0);
  assert.deepEqual(f.calls, ['acquire']);
});

test('only explicit reattach can adopt a new owner generation, without replaying a command', async () => {
  const f = fixture(); await f.request('attach');
  const old = structuredClone(f.binding); f.binding.runtime.identity = 'new-owner';
  await assert.rejects(f.request('detail', {}, { binding: old }), /owner changed/);
  const attached = await f.request('attach', {}, { binding: null });
  assert.equal(attached.binding.runtime.identity, 'new-owner');
  await f.request('detail');
  assert.deepEqual(f.calls, ['acquire', 'release', 'acquire']);
});

test('stop targets exactly an observed native execution; stale approval never sends', async () => {
  const f = fixture(); await f.request('attach');
  f.snapshot.control = { canStop: true, activeWorks: [{ foregroundExecutionId: 'next-turn' }] };
  await assert.rejects(f.request('stop', { executionId: 'old-turn' }), /ended or changed/);
  await assert.rejects(f.request('respond', { interactionId: 'old-approval' }), /ended or changed/);
  await f.request('stop', { id: 'stop-once', executionId: 'next-turn' });
  assert.deepEqual(f.calls.at(-1).envelope.payload, { expectedForegroundExecutionId: 'next-turn' });
});

test('permission uses an offered native option; arbitrary and persistent grants are not invented', async () => {
  const f = fixture(); await f.request('attach');
  f.snapshot.pendingInteractions = [{ interactionId: 'permission', kind: 'permission',
    payload: { options: [{ optionId: 'deny', kind: 'deny' }] } }];
  await assert.rejects(f.request('respond', { interactionId: 'permission', answer: { optionId: 'fullAccess' } }), /offered/);
  await f.request('respond', { id: 'answer-once', interactionId: 'permission', answer: { optionId: 'deny' } });
  assert.equal(f.calls.at(-1).envelope.payload.answer.optionId, 'deny');
});

test('file bytes use native upload references, with the original client and unchanged user text', async () => {
  const f = fixture(); await f.request('attach');
  await f.request('send', { id: 'send-once', text: 'what is this?',
    files: [{ name: 'test.md', mime: 'text/markdown', data: btoa('test') }] });
  const command = f.calls.at(-1).envelope;
  assert.equal(command.clientId, 'original-client');
  assert.equal(command.payload.text, 'what is this?');
  assert.deepEqual(command.payload.attachments, [{ ref: 'native-committed-ref', fileName: 'test.md', mime: 'text/markdown', bytes: 4 }]);
  assert.equal(command.payload.modelSelection, undefined);
  assert.equal(f.listeners.size, 0);
});

test('receipt query never resends an unknown command', async () => {
  const f = fixture(); assert.equal((await f.request('receipt')).results[0].result, 'unknown');
  assert.deepEqual(f.calls, []);
});

test('CDP rejects redirect-shaped, remote, ambiguous and wrong-App targets', () => {
  const target = { type: 'page', url: 'file:///Applications/ZCode.app/Contents/Resources/app.asar/out/renderer/index.html',
    webSocketDebuggerUrl: 'ws://127.0.0.1:17742/devtools/page/ABC' };
  assert.equal(verifiedTarget([target], '/Applications/ZCode.app', 17742), target.webSocketDebuggerUrl);
  for (const url of ['ws://example.com:17742/devtools/page/ABC', 'ws://127.0.0.1:17743/devtools/page/ABC',
    'ws://user@127.0.0.1:17742/devtools/page/ABC', 'ws://127.0.0.1:17742/devtools/page/ABC?redirect=1'])
    assert.throws(() => verifiedTarget([{ ...target, webSocketDebuggerUrl: url }], '/Applications/ZCode.app', 17742));
  assert.throws(() => verifiedTarget([target, target], '/Applications/ZCode.app', 17742), /ambiguous/);
  assert.throws(() => verifiedTarget([{ ...target, url: 'file:///wrong/index.html' }], '/Applications/ZCode.app', 17742));
});

test('a failed turn follows native input routing; only a new explicit send may continue',async()=>{
  const f=fixture();await f.request('attach');
  f.snapshot.control={sessionEnded:false,activeWorks:[],phase:'error'};
  await f.request('send',{id:'new-intent',text:'new message'});
  f.snapshot.inputRouting.mode='queue';
  await assert.rejects(f.request('send',{id:'not-allowed',text:'message'}),/busy/);
  assert.equal(f.calls.filter(c=>c.envelope?.type==='sendText').length,1);
});

test('model selection uses native catalog, idle guard and revision CAS without starting a turn',async()=>{
 const f=fixture();await f.request('attach');const catalog=await f.request('catalog',{section:'models'});
 assert.equal(catalog.currentModel,'account:test/Flash');assert.deepEqual(catalog.models[0].efforts,['low','high']);
 await assert.rejects(f.request('settings',{id:'bad',model:'unknown',effort:'low'}),/原 App/);
 await assert.rejects(f.request('settings',{id:'bad',model:'account:test/Flash',effort:'max'}),/原 App/);
 await f.request('settings',{id:'switch',model:'account:test/Flash',effort:'low'});
 assert.deepEqual(f.calls.at(-1).envelope.payload,{provider:'account:test',model:'Flash',thought:'low'});
 assert.equal(f.calls.at(-1).envelope.baseRevision,7);assert.equal(f.calls.at(-1).envelope.baseLogEpoch,'epoch');
 f.snapshot.queue.items.push({id:'queued'});const n=f.calls.length;
 await assert.rejects(f.request('settings',{model:'account:test/Flash',effort:'low'}),/任务结束/);assert.equal(f.calls.length,n);
});

test('Skill selection revalidates session authority and sends native mentions, not copied instructions',async()=>{
 const f=fixture();await f.request('attach');let catalog={authority:'session',skills:[{id:'skill',name:'my skill',path:'/project/.agents/skills/check/SKILL.md',enabled:true}]};
 f.agent.getSkillReferenceCatalog=async p=>{assert.equal(p.runtimePolicy,'existing-only');return catalog;};
 await f.request('send',{id:'skill-send',text:'review this',plugins:['skill']});
 assert.equal(f.calls.at(-1).envelope.payload.text,'[$my skill](/project/.agents/skills/check/SKILL.md)\n\nreview this');
 const n=f.calls.length;catalog.skills=[];
 await assert.rejects(f.request('send',{id:'removed',text:'review',plugins:['skill']}),/已变化/);
 catalog.authority='workspace';await assert.rejects(f.request('catalog',{section:'skills'}),/尚未就绪/);assert.equal(f.calls.length,n);
});

test('account restart preflight blocks queued native work and refuses incomplete inventory',async()=>{
 const f=fixture();await f.request('attach');f.binding.runtime.processId=10;
 const nodes=document.querySelectorAll();document.querySelectorAll=()=>nodes;const services=nodes[0].__reactContainer$test.memoizedProps.value;
 services.projectService={readTree:async()=>({tasks:[{taskId:'sess-test',workspacePath:'/project',hostId:'local'}],deletedTasks:[]})};
 f.agent.collectLocalRuntimeChildProcesses=async()=>[{pid:10}];
 f.agent.listSessions=async()=>[{sessionId:'sess-test'}];
 f.agent.readSession=async()=>({runtime:{pendingRequestIds:[]},projection:{status:'idle',pendingPermissions:[],activeToolCalls:[],backgroundJobs:[]}});
 const extra={expectedRuntime:{processId:10}};
 assert.equal((await f.request('lifecycle',{},extra)).complete,true);
 f.snapshot.queue.items.push({id:'held'});assert.equal((await f.request('lifecycle',{},extra)).sessions[0].status,'active');
 f.snapshot.queue.items=[];f.agent.listSessions=async()=>[{sessionId:'other'}];assert.equal((await f.request('lifecycle',{},extra)).complete,false);
 f.agent.collectLocalRuntimeChildProcesses=async()=>[{pid:11}];assert.equal((await f.request('lifecycle',{},extra)).complete,false);
 assert.deepEqual(f.calls,['acquire']);
});
