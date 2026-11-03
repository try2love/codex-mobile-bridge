import test from 'node:test';
import assert from 'node:assert/strict';
import { nativeRequest } from '../bridge/clients/zcode/renderer.mjs';

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
    attachmentAbortV4: async () => {},
  };
  globalThis.window = {};
  globalThis.document = { querySelectorAll: () => [{ __reactContainer$test: {
    memoizedProps: { value: { zcodeAgentService: agent, oauthService:{getActiveProvider:async()=>'test'}, credentialService:{load:async()=>JSON.stringify({id:'user',name:'tester'})}, modelSelectionService:{getView:async()=>({providers:[{providerId:'account:test',providerName:'Test',models:[{modelId:'Flash',config:{enabled:true,optionSpecs:{reasoningLevel:{values:['low','high']}}}}]}]})}, layer } } } }] };
  globalThis.localStorage = { getItem: key => { assert.equal(key, 'zcode-v4-client-id:v1'); return binding.clientId; } };
  const request = (action, body = {}, extra = {}) => nativeRequest({ action, body, workspace: '/project',
    sessionId: 'sess-test', binding: structuredClone(binding), expiresAt: Date.now() + 10000, ...extra });
  const nodes = document.querySelectorAll();
  globalThis.document.querySelectorAll = () => nodes;
  const services = nodes[0].__reactContainer$test.memoizedProps.value;
  services.projectService = {readTree:async()=>({tasks:[{taskId:'sess-test',workspacePath:'/project',hostId:'local'}]})};
  return { request, calls, snapshot, binding, agent, listeners, storeState, services };
}

test('application handshake reads the original project service without activating any chat',async()=>{
  const f=fixture();
  assert.deepEqual(await f.request('application'),{ready:true});
  assert.deepEqual(f.calls,[]);
  f.agent.helloConversationV4=async()=>({protocolVersion:3,clientMode:'web-remote-replayable',connectionId:'foreign'});
  await assert.rejects(f.request('application'),/会话服务/);
  f.agent.helloConversationV4=async()=>({protocolVersion:3,clientMode:'desktop-continuous',connectionId:'native'});
  f.services.projectService.readTree=async()=>({});
  await assert.rejects(f.request('application'),/会话服务/);
});

test('runtime handshake requires owner, command and receipt primitives independently of release version',async()=>{
  for(const missing of ['helloConversationV4','getWorkspaceRuntimeIdentity','queryConversationCommandsV4','sendConversationCommandV4']){
    const f=fixture();delete f.agent[missing];
    await assert.rejects(f.request('application'),/会话服务/);
    assert.deepEqual(f.calls,[]);
  }
  const f=fixture();f.agent.helloConversationV4=async()=>({protocolVersion:4,clientMode:'desktop-continuous',connectionId:'other'});
  await assert.rejects(f.request('application'),/会话服务/);assert.deepEqual(f.calls,[]);
});

test('partial optional services keep text working and never begin an unsupported attachment transfer',async()=>{
  const f=fixture();delete f.services.modelSelectionService;delete f.services.oauthService;delete f.agent.attachmentChunkV4;
  await f.request('attach');
  const detail=await f.request('detail');
  assert.deepEqual(detail.capabilities,{models:false,effort:false,create:false,skills:false,attachments:false,uploadFiles:false,projectReferences:false});
  assert.equal(detail.account,undefined);
  await assert.rejects(f.request('send',{id:'file',text:'one',files:[{name:'a.txt',data:'MQ==',mime:'text/plain'}]}),/附件/);
  assert.deepEqual(f.calls,['acquire']);
  assert.equal((await f.request('send',{id:'text',text:'one'})).status,'accepted');
});

test('known active work blocks normal quit even if full inventory cannot be read',async()=>{
  const f=fixture();await f.request('attach');f.snapshot.pendingInteractions=[{interactionId:'ask'}];
  f.services.projectService.readTree=()=>{throw Error('inventory unavailable');};
  const result=await f.request('lifecycle',{}, {sessionId:null,expectedRuntime:null});
  assert.deepEqual(result,{complete:false,sessions:[{id:'sess-test',status:'active',runtimeKnown:true}]});
});

test('home screen exposes the native registry anchor without a mounted conversation', async () => {
  const f = fixture(), provider = function NativeServiceProvider() {};
  delete f.services.layer;
  document.querySelectorAll()[0].__reactContainer$test = {type:provider,memoizedProps:{services:f.services}};
  assert.equal(await f.request('registry-probe'),provider);
  assert.deepEqual(f.calls,[]);
});

test('cold reconnect uses V4 history without a legacy session runtime and keeps passive reads dormant', async () => {
  const f = fixture(), identity=f.agent.getWorkspaceRuntimeIdentity;
  let warm=false,listener,removed=false;
  f.storeState.status='connecting';
  f.services.layer.acquire=()=>{
    f.calls.push('acquire');
    queueMicrotask(()=>{warm=true;f.storeState.status='live';listener?.();});
    return {store:{getState:()=>f.storeState,subscribe:fn=>{listener=fn;return ()=>{removed=true;listener=null;};}},release:()=>f.calls.push('release')};
  };
  f.agent.getWorkspaceRuntimeIdentity=async p=>{if(!warm)throw Error('runtime not running');return identity(p);};
  f.agent.readSession=async()=>{throw Error('legacy session runtime is not ready');};
  await assert.rejects(f.request('history'),/runtime not running/);
  assert.equal(warm,false);
  await assert.rejects(f.request('attach',{}, {workspace:'/wrong'}),/项目已变化/);
  assert.equal(warm,false);
  await f.request('attach');
  assert.equal(warm,true);assert.equal(removed,true);assert.deepEqual(f.calls,['acquire']);
});

test('cold restore timeout releases the new Bridge lease and listener without sending input', async () => {
  const f=fixture();let removed=false;
  f.storeState.status='connecting';
  f.services.layer.acquire=()=>({store:{getState:()=>f.storeState,subscribe:()=>()=>{removed=true;}},release:()=>f.calls.push('release')});
  window.__codexMobileZCodeAcquireWorkspace=()=>({layer:f.services.layer,release:()=>f.calls.push('workspace-release')});
  await assert.rejects(f.request('attach',{}, {expiresAt:Date.now()+260}),/恢复超时/);
  assert.equal(removed,true);assert.deepEqual(f.calls,['release','workspace-release']);
  assert.equal(window.__codexMobileZCodePrototypeV1,undefined);
});

test('passive history never subscribes or starts an executor and hides model-only context', async () => {
  const f = fixture();
  const result = await f.request('history');
  assert.equal(result.messages.length, 1);
  assert.deepEqual(f.calls, []);
});

test('reattach is explicit; repeated attach and reads do not replace a native subscription', async () => {
  const f = fixture();
  assert.equal((await f.request('detail')).detached,true);
  await f.request('attach'); await f.request('attach'); await f.request('detail');
  assert.deepEqual(f.calls, ['acquire']);
  assert.equal(f.listeners.size, 0);
  await f.request('detach');
  assert.equal((await f.request('detail')).detached,true);
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
  assert.deepEqual(f.calls, ['acquire', 'acquire', 'release']);
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
 const f=fixture();f.binding.runtime.processId=10;await f.request('attach');
 const nodes=document.querySelectorAll();document.querySelectorAll=()=>nodes;const services=nodes[0].__reactContainer$test.memoizedProps.value;
 services.projectService={readTree:async()=>({tasks:[{taskId:'sess-test',workspacePath:'/project',hostId:'local'}],deletedTasks:[]})};
 f.agent.collectLocalRuntimeChildProcesses=async()=>[{pid:10}];
 f.agent.listSessions=async()=>[{sessionId:'sess-test'}];
 f.agent.readSession=async()=>({runtime:{pendingRequestIds:[]},projection:{status:'idle',pendingPermissions:[],activeToolCalls:[],backgroundJobs:[]}});
 const extra={expectedRuntime:structuredClone(f.binding.runtime)};
 assert.equal((await f.request('lifecycle',{},extra)).complete,true);
 f.snapshot.queue.items.push({id:'held'});assert.equal((await f.request('lifecycle',{},extra)).sessions[0].status,'active');
 f.snapshot.queue.items=[];f.agent.listSessions=async()=>[{sessionId:'other'}];assert.equal((await f.request('lifecycle',{},extra)).complete,false);
 f.agent.collectLocalRuntimeChildProcesses=async()=>[{pid:11}];assert.equal((await f.request('lifecycle',{},extra)).complete,false);
 assert.deepEqual(f.calls,['acquire']);
});

test('native plan and goal commands keep original owner, CAS and stale-goal guards',async()=>{
 const f=fixture();await f.request('attach');f.snapshot.config.mode='build';f.snapshot.goal=null;
 await f.request('work-mode',{id:'plan',mode:'plan'});
 assert.equal(f.calls.at(-1).envelope.type,'switchCollaborationMode');assert.equal(f.calls.at(-1).envelope.baseRevision,7);
 f.snapshot.config.planEnabled=true;await assert.rejects(f.request('goal',{id:'goal',operation:'set',objective:'one'}),/退出计划/);
 f.snapshot.config.planEnabled=false;await f.request('goal',{id:'goal',operation:'set',objective:'one'});
 assert.equal(f.calls.at(-1).envelope.type,'sendGoalCommand');assert.deepEqual(f.calls.at(-1).envelope.payload,{text:'one'});
 f.snapshot.goal={targetId:'target',objective:'one',status:'active'};
 await assert.rejects(f.request('goal',{id:'old',operation:'pause',expected:{goalId:'old',objective:'one'}}),/目标已变化/);
 await f.request('goal',{id:'pause',operation:'pause',expected:{goalId:'target',objective:'one'}});
 assert.equal(f.calls.at(-1).envelope.type,'pauseGoal');assert.equal(f.calls.at(-1).envelope.baseLogEpoch,'epoch');
 const nodes=document.querySelectorAll();document.querySelectorAll=()=>nodes;
 nodes[0].__reactContainer$test.memoizedProps.value.zcodeTaskService={goalSession:async p=>{f.calls.push(p);return {status:'accepted'};}};
 f.agent.readSession=async()=>({session:{target:{targetId:'target'}},runtime:{stateRevision:21}});
 await f.request('goal',{id:'clear',operation:'clear',expected:{goalId:'target',objective:'one'}});
 assert.equal(f.calls.at(-1).expectedRevision,21,'legacy goal API uses its native revision, not V4 revision');
});

test('new native chat uses atomic firstInput and independent shared lease without inference defaults',async()=>{
 const f=fixture();await f.request('attach');const nodes=document.querySelectorAll();document.querySelectorAll=()=>nodes;
 const layer=nodes[0].__reactContainer$test.memoizedProps.value.layer;
 layer.acquire=sid=>{f.calls.push(['acquire',sid]);return {store:{getState:()=>({status:'live',snapshot:{...f.snapshot,sessionId:sid}})},release:()=>f.calls.push(['release',sid])};};
 f.agent.sendConversationCommandV4=async p=>{f.calls.push(p);return {status:'accepted',result:{type:'createSession',sessionId:p.envelope.payload.sessionId}};};
 const result=await f.request('create',{id:'new',firstMessage:'first message'});
 assert.equal(result.result.id,'sess_new');const create=f.calls.find(v=>v.envelope?.type==='createSession');
 assert.equal(create.envelope.clientId,'original-client');assert.equal(create.envelope.sessionId,null);
 assert.equal(create.envelope.payload.firstInput.text,'first message');assert.equal(create.envelope.payload.config.model,'Flash');assert.equal(create.envelope.payload.config.thought,'low');
 assert.equal(window.__codexMobileZCodePrototypeV1.sessions.size,2);
 const before=f.calls.length;await assert.rejects(f.request('create',{id:'empty',firstMessage:''}),/首条消息/);assert.equal(f.calls.length,before);
});

test('new project uses the original project service and workspace registry, preserving the old lease', async()=>{
  const f=fixture();await f.request('attach');
  const scopes=[],released=[];
  window.__codexMobileZCodeAcquireWorkspace=scope=>{scopes.push(scope);return {layer:{acquire:sid=>({store:{getState:()=>({status:'live',snapshot:{...f.snapshot,sessionId:sid}})},release:()=>released.push(sid)})},release:()=>released.push(scope.workspacePath)};};
  f.services.projectService={saveProject:async p=>{f.calls.push({save:p});return {id:'native-project',hostId:'local'};},prepareSession:async p=>{f.calls.push({prepare:p});return {projectId:p.projectId,workspacePath:'/new-project'};}};
  f.agent.getWorkspaceRuntimeIdentity=async p=>({...f.binding.runtime,workspaceKey:p.workspacePath});
  f.agent.sendConversationCommandV4=async p=>{f.calls.push(p);return {status:'accepted',result:{sessionId:p.envelope.payload.sessionId}};};
  const result=await f.request('create',{id:'new-project-command',firstMessage:'hello',projectKey:'registered',directoryProject:{cwd:'/new-project'}});
  assert.equal(result.workspace,'/new-project');assert.equal(result.binding.runtime.workspaceKey,'/new-project');
  const command=f.calls.find(p=>p.envelope?.type==='createSession');assert.equal(command.workspacePath,'/new-project');assert.equal(command.projectId,'native-project');
  assert.equal(command.envelope.payload.config.model,'Flash');assert.equal(command.envelope.payload.config.thought,'low');
  assert.deepEqual(scopes,[{workspacePath:'/new-project'}]);assert.deepEqual(released,[]);
  assert.equal((await f.request('detail')).snapshot.sessionId,'sess-test');
  await f.request('detach',{}, {sessionId:result.result.id,workspace:'/new-project',binding:result.binding});
  assert.deepEqual(released,[result.result.id,'/new-project']);
});

test('cold app creates its first chat in the selected project without opening any other chat',async()=>{
 const f=fixture(), project={id:'native-p',name:'New',hostId:'local',roots:[{rootPath:'/new',isPrimary:true}]};
 const p=f.services.projectService={readTree:async()=>({tasks:[],projects:[project]}),readProjects:async()=>[project],
   prepareSession:async()=>({projectId:project.id,workspacePath:'/new'})};
 const settings={providerId:'account:test',modelId:'Flash',options:{reasoningLevel:'low'}};
 const catalog=await f.services.modelSelectionService.getView();
 f.services.modelSelectionService.getView=async()=>({...catalog,preferredSelection:{...settings,options:{reasoningLevel:'high'}}});
 const recent='zcode-composer-recent:v2:'+JSON.stringify(['desktop','local',project.id]);
 globalThis.localStorage.getItem=key=>key==='zcode-v4-client-id:v1'?'original-client':key===recent?JSON.stringify({modelSelection:settings}):null;
 const cold={workspace:null,sessionId:null,binding:null}, workspace={...f.binding.runtime,workspaceKey:'/new'};
 f.agent.getWorkspaceRuntimeIdentity=async({sessionId})=>{assert.equal(sessionId,'sess_new');assert.equal(f.calls.length,1);return workspace;};
 window.__codexMobileZCodeAcquireWorkspace=()=>({layer:{acquire:sessionId=>({store:{getState:()=>({status:'live',snapshot:{...f.snapshot,sessionId}})}})}});
 f.agent.sendConversationCommandV4=async cmd=>{f.calls.push(cmd);return {status:'accepted',result:{sessionId:cmd.envelope.payload.sessionId}};};
 assert.deepEqual(await f.request('application',{},cold),{ready:true,canCreate:true});
 assert.equal((await f.request('list',{},cold)).sessions.length,0);assert.equal(f.calls.length,0);
 const result=await f.request('create',{id:'new',projectKey:project.id,firstMessage:'first'},cold);
 assert.equal(result.result.id,'sess_new');assert.equal(f.calls.length,1);
 assert.equal(f.calls[0].projectId,project.id);assert.equal(f.calls[0].workspacePath,'/new');
 assert.equal(f.calls[0].envelope.sessionId,null);assert.equal(f.calls[0].envelope.clientId,'original-client');
 assert.deepEqual(f.calls[0].envelope.payload.firstInput.modelSelection,settings);
 assert.equal(window.__codexMobileZCodePrototypeV1.sessionId,'sess_new');
 p.prepareSession=async()=>({projectId:'different-project',workspacePath:'/new'});
 await assert.rejects(f.request('create',{id:'wrong',projectKey:project.id,firstMessage:'x'},cold),/目录已变化/);
 assert.equal(f.calls.length,1);
});

test('native project cwd mismatch rejects first input instead of sending it to the anchor',async()=>{
  const f=fixture();await f.request('attach');window.__codexMobileZCodeAcquireWorkspace=()=>{throw Error('must not acquire')};
  f.services.projectService={saveProject:async()=>({id:'project',hostId:'local'}),prepareSession:async()=>({projectId:'project',workspacePath:'/changed'})};
  await assert.rejects(f.request('create',{id:'new',firstMessage:'hello',projectKey:'registered',directoryProject:{cwd:'/requested'}}),/目录已变化/);
  assert.equal(f.calls.filter(p=>p.envelope).length,0);
});

test('project inventory and cold session target are passive and use native host membership',async()=>{
  const f=fixture();f.services.projectService={readTree:async()=>({projects:[{id:'p',name:'Native',hostId:'local',roots:[{rootPath:'/new',isPrimary:true}]},{id:'remote',hostId:'ssh',roots:[{rootPath:'/remote',isPrimary:true}]}],tasks:[{taskId:'sess-new',workspacePath:'/new',hostId:'local'}]})};
  assert.equal((await f.request('projects')).projects.length,1);
  assert.deepEqual(await f.request('target',{}, {sessionId:'sess-new'}),{workspace:'/new'});
  await assert.rejects(f.request('target',{}, {sessionId:'sess-unknown'}),/找不到/);assert.deepEqual(f.calls,[]);
});



test('queue and guide keep native delivery and never interrupt a busy turn as a new send',async()=>{
 const f=fixture();await f.request('attach');f.snapshot.control.activeWorks=[{foregroundExecutionId:'active'}];f.snapshot.inputRouting.mode='enqueue';
 await assert.rejects(f.request('send',{id:'new',text:'new',mode:'send'}),/busy/);
 for(const [mode,delivery] of [['queue','queue'],['steer','guide']]){await f.request('send',{id:mode,text:mode,mode});assert.equal(f.calls.at(-1).envelope.payload.requestedDelivery,delivery);}
 f.snapshot.inputRouting.mode='choice';f.snapshot.control.activeWorks=[];f.snapshot.queue.items=[{id:'held'}];
 await assert.rejects(f.request('send',{id:'held',text:'new',mode:'send'}),/busy/);
 await f.request('send',{id:'more',text:'more',mode:'queue'});assert.equal(f.calls.at(-1).envelope.payload.heldQueueDisposition,undefined);
});

test('maintenance and individual task stop are native commands and reject stale targets',async()=>{
  const f=fixture();await f.request('attach');f.snapshot.availability={compact:{allowed:true}};
  f.snapshot.backgroundWorks=[{workId:'own-task',title:'Background shell',kind:'bash',status:'running',cancellable:true}];
  assert.equal((await f.request('tasks')).tasks[0].id,'own-task');
  await f.request('stop-task',{id:'stop-once',taskId:'own-task'});
  assert.equal(f.calls.at(-1).envelope.type,'cancelBackgroundWork');
  assert.deepEqual(f.calls.at(-1).envelope.payload,{workId:'own-task'});
  f.snapshot.backgroundWorks[0].status='resultPending';
  await assert.rejects(f.request('stop-task',{id:'late',taskId:'own-task'}),/已结束/);
  await f.request('compact',{id:'compact-once'});assert.equal(f.calls.at(-1).envelope.type,'compact');
  f.snapshot.queue.items=[{itemId:'held'}];
  await assert.rejects(f.request('compact',{id:'held'}),/队列结束/);
});

test('native message actions bind the row entity and epoch and preserve files and attachments',async()=>{
  const f=fixture();await f.request('attach');
  const attachment={ref:'original',fileName:'a.md',mime:'text/markdown',bytes:2};
  f.snapshot.rows={window:[{rowId:3,entityId:'user-message',kind:'userInput',actions:{canEdit:true},attachments:[attachment]},
    {rowId:4,entityId:'assistant-message',kind:'assistantText',actions:{canFork:true}}]};
  const target={rowId:3,entityId:'user-message',logEpoch:'epoch'};
  await f.request('edit-message',{id:'edit',target,text:'new text'});
  const edit=f.calls.at(-1).envelope;assert.equal(edit.type,'editUserQuery');assert.equal(edit.baseLogEpoch,'epoch');assert.equal(edit.baseRevision,7);
  assert.equal(edit.payload.workspaceMode,'preserve');assert.deepEqual(edit.payload.attachments,[attachment]);
  await assert.rejects(f.request('edit-message',{id:'stale',target:{...target,logEpoch:'old'},text:'bad'}),/消息已变化/);
  await assert.rejects(f.request('fork',{id:'wrong',target}),/消息已变化/);
  await f.request('fork',{id:'fork',target:{rowId:4,entityId:'assistant-message',logEpoch:'epoch'}});
  assert.equal(f.calls.at(-1).envelope.type,'forkAssistant');
  await f.request('rename',{id:'rename',title:'New title'});assert.deepEqual(f.calls.at(-1).envelope.payload,{title:'New title'});
});

test('native access switch requires confirmation for full access and preserves revision and original owner',async()=>{
 const f=fixture(); await f.request('attach'); f.snapshot.config.mode='build';
 await assert.rejects(f.request('access',{id:'unsafe',mode:'yolo'}),/确认/);
 assert.equal(f.calls.filter(x=>x?.envelope?.type==='switchCollaborationMode').length,0);
 f.snapshot.pendingInteractions=[{}];
 await assert.rejects(f.request('access',{id:'busy',mode:'yolo',confirmed:true}),/结束/);
 f.snapshot.pendingInteractions=[];
 await f.request('access',{id:'checked',mode:'yolo',confirmed:true});
 const cmd=f.calls.at(-1).envelope;
 assert.equal(cmd.type,'switchCollaborationMode');assert.equal(cmd.baseRevision,7);
 assert.equal(cmd.baseLogEpoch,'epoch');assert.equal(cmd.payload.mode,'yolo');
});
