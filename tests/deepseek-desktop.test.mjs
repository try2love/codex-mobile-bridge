// Adapted from coding-mobile (MIT); see bridge/clients/LICENSE.coding-mobile.
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {apply} from '../bridge/clients/deepseek/host.mjs';

await mkdir('.tmp',{recursive:true});
const folder=await mkdtemp(join('.tmp','mobile-harness-test-')),endpoint=join(folder,'endpoint.json'),configPath=join(folder,'config.json');
await writeFile(configPath,JSON.stringify({token:'fixture-secret',endpoint}));
const handlers=new Map(),calls=[],disposers=[];let pluginReads=0,modelReads=0,promptAccepted=true;
const defaultSelection={provider:'deepseek',model:'chat',reasoningEffort:'high'};
const row={sessionId:'session-one',cwd:folder,running:true,updatedAt:1,projections:{asOfSeq:3,values:{title:'Fixture',todos:[{content:'check',status:'in_progress'}],modelSelection:{next:{provider:'deepseek',model:'chat',reasoningEffort:'high'}}}}};
const ctx={sessionController:{list:async()=>({items:[row]}),projections:async()=>({asOfSeq:3}),page:async()=>({records:[{event:{seq:1,type:'request/header',data:{token:'PRIVATE'}}},{event:{seq:2,type:'user/message',data:{content:[{type:'text',text:'hello'}]}}}],hasMore:false}),
  modelCatalog:async()=>{modelReads++;return {default:defaultSelection,groups:[{id:'deepseek',name:'DeepSeek API',models:[{id:'chat',name:'Chat',reasoning:{efforts:[{id:'high'}],defaultEffort:'high'}}]},{id:'deepseek-account',name:'DeepSeek Account',models:[{id:'chat',name:'Chat'}]}]};},
  prompt:async value=>{calls.push(value);return {accepted:promptAccepted};},cancel:async value=>calls.push(value),selectModel:async value=>{calls.push(value);return {selected:{...value,reasoningEffort:'high'}};},create:async value=>{calls.push(value);return value;}},
  pluginInventory:{list:async()=>{pluginReads++;return {entries:[{moduleName:'@deepseek-ai/dsh-skill-office',enabled:true,fiberPhase:'active',meta:{title:{'zh-CN':'Office 文档'}}}],agentPresets:[{id:'default',rows:[{moduleName:'@deepseek-ai/dsh-skill-office',enabled:true,fiberPhase:'active'},{moduleName:'@deepseek-ai/dsh-tool-browser',enabled:false,fiberPhase:null}]}]};}},
  sessionSkillCatalog:{list:async()=>({skills:[{name:'review',description:'Review'}]})},permissionPresets:{catalog:()=>({options:[{value:'default',name:'Default'},{value:'full-access',name:'Full access'}]}),set:(session,mode)=>{calls.push({permissionSession:session,mode});}},workspaceRegistry:{list:()=>[]},agents:{list:()=>[],get:()=>({session:{id:'native-session'}})},on:(name,fn)=>handlers.set(name,fn),effect:setup=>{const dispose=setup();disposers.push(dispose);return dispose;}};
await apply(ctx,{configPath});const {port}=JSON.parse(await readFile(endpoint,'utf8'));
const request=async(action,body={},token='fixture-secret',sid='session-one')=>{const response=await fetch(`http://127.0.0.1:${port}/mobile`,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({action,sid,body})});return response;};
try{
  let runtime=await(await request('lifecycle')).json();assert.equal(runtime.complete,true);assert.equal(runtime.sessions.length,0);
  ctx.agents.list=()=>[{id:'hidden-child',status:'running'}];runtime=await(await request('lifecycle')).json();assert.equal(runtime.sessions[0].status,'active');assert.equal(runtime.sessions[0].runtimeKnown,true);
  ctx.agents.list=()=>[{id:'unknown-child'}];runtime=await(await request('lifecycle')).json();assert.equal(runtime.sessions[0].status,'unknown');assert.equal(runtime.sessions[0].runtimeKnown,false);
  delete ctx.agents.list;assert.equal((await(await request('lifecycle')).json()).complete,false);ctx.agents.list=()=>[];
  row.projections.values.permissions={currentValue:'full-access'};
  row.projections.values.contextPressure={projectedTokens:120,pressureTokens:100,contextWindow:1000};
  const session=(await(await request('detail')).json()).session;
  assert.equal(session.permissionMode,'full-access');assert.equal(session.permissionLabel,'Full access');
  assert.deepEqual(session.contextUsage,{usedTokens:120,contextWindow:1000,estimated:true});
  delete row.projections.values.contextPressure;delete row.projections.values.permissions;
  const initialStatus=await(await request('status')).json();assert.equal(initialStatus.configured,false);assert.equal(initialStatus.bridgeRevision,4);
  const services={settings:{describe:options=>{assert.deepEqual(options,{redactSecrets:true});return [{ns:'provider',value:{apiKeyEnv:'FIXTURE_KEY',private:'DO-NOT-EXPOSE'}}];}},credentials:{describe:async ref=>{assert.equal(ref,'FIXTURE_KEY');return {configured:true};},resolve:()=>{throw Error('Must not resolve credentials');}},llm:{listConfigurableProviders:()=>[{provider:'deepseek',settingsNs:'provider',settingsPath:[]}]}};
  ctx.get=name=>services[name];
  assert.equal((await(await request('status')).json()).configured,true);
  const account=await(await request('account',{},'fixture-secret',null)).json();assert.equal(account.current.kind,'api');assert.equal(account.current.label,'DeepSeek API');assert.equal(account.readOnly,true);assert.equal(account.canManage,false);assert.ok(!JSON.stringify(account).includes('FIXTURE_KEY'));assert.ok(!JSON.stringify(account).includes('DO-NOT-EXPOSE'));assert.equal(calls.length,0);
  assert.deepEqual(account.groups.map(g=>g.id),['deepseek']);assert.deepEqual(account.models,[{id:'deepseek/chat',name:'Chat',efforts:['high'],defaultEffort:'high'}]);
  services.deepseekAccount={getState:async()=>({status:'credential-stored',attempt:{private:'DO-NOT-EXPOSE'}})};
  const official=await(await request('account')).json();assert.equal(official.sources.find(s=>s.kind==='account').label,'DeepSeek Account');assert.ok(!JSON.stringify(official).includes('DO-NOT-EXPOSE'));
  assert.ok(official.models.some(m=>m.id==='deepseek-account/chat'));assert.equal(calls.length,0);
  assert.equal((await request('list',{},'wrong')).status,403);
  let listed=(await(await request('list')).json()).sessions[0];assert.equal(listed.runtimeKnown,true);assert.equal(listed.status,'active');
  delete row.running;listed=(await(await request('list')).json()).sessions[0];assert.equal(listed.runtimeKnown,false);assert.equal(listed.status,'unknown');row.running=true;
  const detail=await(await request('detail')).json();assert.equal(detail.session.title,'Fixture');assert.ok(!JSON.stringify(detail).includes('PRIVATE'));
  const models=await(await request('catalog')).json();assert.equal(models.models[0].id,'deepseek/chat');
  assert.equal(models.capabilities.models,true);assert.equal(models.capabilities.attachments,true);assert.equal(detail.capabilities.permissions,true);assert.equal(detail.session.backend,'deepseek');
  const originalProjection=row.projections.values.modelSelection;delete row.projections.values.modelSelection;
  const fresh=await(await request('catalog',{section:'models'})).json();assert.equal(fresh.currentModel,'deepseek/chat');assert.equal(fresh.currentEffort,'high');
  services.agentDefaultModel={currentSelection:()=>defaultSelection};
  assert.equal((await(await request('detail')).json()).session.model,'deepseek/chat');
  row.projections.values.modelSelection=originalProjection;
  assert.equal(models.skills.filter(p=>p.id==='installed:@deepseek-ai/dsh-skill-office').length,1);assert.equal(models.skills.find(p=>p.name==='Office 文档').selectable,false);
  const before=pluginReads;await request('catalog',{section:'models'});assert.equal(pluginReads,before);
  const modelBefore=modelReads;const plugins=await(await request('catalog',{section:'plugins'})).json();assert.equal(modelReads,modelBefore);assert.equal(plugins.models,undefined);
  assert.equal((await request('send',{id:'invalid',text:'no',plugins:['installed:@deepseek-ai/dsh-skill-office']})).status,400);assert.equal(calls.length,0);
  assert.equal((await(await request('send',{id:'r',text:'follow-up',plugins:['review']})).json()).status,'queued');assert.equal(calls[0].mode,'queue');assert.ok(calls[0].content[0].text.includes('review'));
  const selected=await(await request('settings',{model:'deepseek/chat',effort:''})).json();assert.equal(selected.effort,'high');assert.equal(calls.at(-1).provider,'deepseek');assert.equal(calls.at(-1).model,'chat');assert.equal(calls.at(-1).reasoningEffort,undefined);
  assert.equal((await request('settings',{model:'deepseek/chat',effort:'unsupported'})).status,400);
  const beforeAccess=calls.length;const permissions=await(await request('access')).json();assert.equal(permissions.options.length,2);assert.equal(calls.length,beforeAccess);
  assert.equal((await request('access',{mode:'invalid'})).status,400);assert.equal(calls.length,beforeAccess);
  assert.equal((await request('access',{mode:'full-access'})).status,200);assert.deepEqual(calls.at(-1),{permissionSession:{id:'native-session'},mode:'full-access'});
  // The gateway's validated upload may exceed the previous 5 MiB host JSON limit.
  const pixels=Buffer.alloc(4*1024*1024,1).toString('base64');
  assert.equal((await(await request('send',{id:'image',text:'',mode:'steer',resolvedImages:[{url:'data:image/png;base64,'+pixels,name:'example.png'}]})).json()).status,'accepted');
  assert.deepEqual(calls.at(-1).content,[{type:'image',mediaType:'image/png',data:pixels,name:'example.png'}]);assert.equal(calls.at(-1).mode,'steer');
  const beforeInvalid=calls.length;assert.equal((await request('send',{id:'invalid-image',text:'',resolvedImages:[{url:'data:image/svg+xml;base64,AAAA'}]})).status,400);assert.equal(calls.length,beforeInvalid);
  assert.equal((await request('send',{id:'file',text:'hello',resolvedFiles:[{path:'/tmp/secret'}]})).status,400);assert.equal(calls.length,beforeInvalid);
  promptAccepted=false;assert.equal((await request('send',{id:'unconfirmed',text:'hello'})).status,400);promptAccepted=true;
  let desktopAborted=false;
  const req={agent:{id:'session-one'},questions:[{id:'q',question:'Choose',options:[{label:'A'}]}],signal:new AbortController().signal};
  const waiting=handlers.get('user-questions/request')(req,()=>new Promise((resolve,reject)=>req.signal.addEventListener('abort',()=>{desktopAborted=true;reject(Error('cancelled'));})));
  const question=(await(await request('list')).json()).sessions[0].requests[0];assert.equal(question.input.questions[0].id,'q');
  await request('respond',{requestId:question.id,answers:{q:{answers:['A','custom']}}});
  const answer=await waiting;assert.deepEqual(answer.answers[0],{id:'q',selected:['A'],custom:'custom'});assert.ok(desktopAborted);
  // A native tool approval is distinct from a structured user question.
  let desktopCancelled=false;
  const approvalReq={agent:{id:'session-one'},toolName:'shell',reason:'Run command',signal:new AbortController().signal};
  const approval=handlers.get('approval/request')(approvalReq,()=>new Promise((resolve,reject)=>approvalReq.signal.addEventListener('abort',()=>{desktopCancelled=true;reject(Error('cancelled'));})));
  const pending=(await(await request('list')).json()).sessions[0].requests[0];
  assert.equal(pending.needsInput,false);assert.equal(pending.tool,'shell');
  assert.equal((await request('respond',{requestId:pending.id,decision:'always'})).status,400);
  await request('respond',{requestId:pending.id,decision:'accept'});
  assert.equal(await approval,'allowed-once');assert.ok(desktopCancelled);
  assert.equal((await request('respond',{requestId:pending.id,decision:'accept'})).status,400);
  let answerDesktop;
  const second=handlers.get('approval/request')({...approvalReq,signal:new AbortController().signal},()=>new Promise(resolve=>{answerDesktop=resolve;}));
  await new Promise(resolve=>setTimeout(resolve,0));answerDesktop('rejected');
  assert.equal(await second,'rejected');assert.equal((await(await request('list')).json()).sessions[0].requests.length,0);
  const origin=await fetch(`http://127.0.0.1:${port}/mobile`,{method:'POST',headers:{Authorization:'Bearer fixture-secret',Origin:'https://evil.invalid'},body:'{}'});
  assert.equal(origin.status,403);
  assert.equal((await request('unsupported')).status,400);
  assert.equal(disposers.length,1,'native fiber disposal must own the HTTP server');
  await disposers.shift()();
  await assert.rejects(readFile(endpoint),{code:'ENOENT'});
  await assert.rejects(request('status'));
  // Disposing an older connector must not erase a newer host generation's endpoint.
  await apply(ctx,{configPath});const first=JSON.parse(await readFile(endpoint,'utf8'));
  await apply(ctx,{configPath});const secondHost=JSON.parse(await readFile(endpoint,'utf8'));
  assert.notEqual(first.generation,secondHost.generation);
  await disposers.shift()();assert.deepEqual(JSON.parse(await readFile(endpoint,'utf8')),secondHost);
  await disposers.shift()();await assert.rejects(readFile(endpoint),{code:'ENOENT'});
  await assert.rejects(fetch(`http://127.0.0.1:${secondHost.port}/mobile`,{method:'POST'}));
  console.log('PASS: Harness authenticated session adapter, code-free reads, model catalog, queued prompt, synchronized question and stale-dialog cancellation.');
}finally{for(const dispose of disposers.reverse())await dispose();if(handlers.has('dispose'))await handlers.get('dispose')();await rm(folder,{recursive:true,force:true});}
