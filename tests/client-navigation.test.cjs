'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  let mobile=false,choice=true,finishChoice;const choices=[];const context=vm.createContext({ClientLifecycle:{...require('../web/client-lifecycle.js'),chooseDisable(client){choices.push(client.id);return choice==='pending'?new Promise(resolve=>finishChoice=resolve):choice;}},document:{documentElement:{classList:{contains:()=>mobile}},getElementById:()=>({hidden:false})},localStorage:{setItem(){}},Event:class{},window:{}});
  vm.runInContext(fs.readFileSync('web/client-navigation.js','utf8'),context);
  let initializeChoice=false,finishInitialize;const initializations=[];context.ClientLifecycle.chooseInitialize=client=>{initializations.push(client.id);return initializeChoice==='pending'?new Promise(resolve=>finishInitialize=resolve):initializeChoice;};
  const C=vm.runInContext('ClientNavigation',context),nav=Object.create(C.prototype),painted=[];
  Object.assign(nav,{clients:[{id:'codex',enabled:true,configured:true},{id:'claude',enabled:true,configured:true},{id:'deepseek',enabled:true,configured:true}],provider:'claude',revision:0,pending:new Set(),notify(){},paint(){painted.push(this.clients.filter(c=>c.enabled).map(c=>c.id));},view:{provider:'claude',choose(id){this.provider=id;return Promise.resolve();}}});
  return {nav,painted,choices,document:context.document,setChoice:value=>choice=value,decide:value=>finishChoice(value),mobile:value=>mobile=value,initializations,setInitializeChoice:value=>initializeChoice=value,decideInitialize:value=>finishInitialize(value)};
}
test('changing provider paints the shell before its slow read resolves',async()=>{
  const {nav,painted}=fixture();let resolve;
  nav.view.choose=()=>new Promise(done=>resolve=done);
  const pending=nav.choose('deepseek');assert.equal(nav.provider,'deepseek');assert.equal(painted.length,1);
  resolve();await pending;
});

test('clicking the selected provider does not reset its view or issue another read',async()=>{
 const {nav}=fixture();let calls=0;nav.view.choose=async()=>{calls++;};await nav.choose('claude');assert.equal(calls,0);
});

test('phone initialization sends foreground intent only after an explicit confirmation',async()=>{
 for(const accepted of [false,true]){
  const ui=fixture(),nav=ui.nav,calls=[];ui.setInitializeChoice(accepted);Object.assign(nav.clients[1],{installed:true,connected:false,setupStatus:'needs-initialization'});nav.request=async(url,body)=>{calls.push(body);return {clients:nav.clients.map(client=>client.id==='claude'?{...client,connectionState:'connecting'}:client)};};
  await nav.initialize('claude');assert.deepEqual(ui.initializations,['claude']);assert.equal(calls.length,accepted?1:0);if(accepted){assert.equal(calls[0].initializeDesktop,true);assert.equal(calls[0].enabled,true);assert.equal(calls[0].provider,'claude');}
 }
 const {nav}=fixture();let body;nav.request=async(url,value)=>{body=value;return {clients:nav.clients};};await nav.toggle('claude',true);assert.equal(Object.hasOwn(body,'initializeDesktop'),false);
});

test('an unanswered initialization prompt blocks duplicate operations and stale polling',async()=>{
 const ui=fixture(),nav=ui.nav;ui.setInitializeChoice('pending');Object.assign(nav.clients[1],{installed:true,setupStatus:'needs-initialization'});let finishRead,calls=0;nav.request=()=>{calls++;return new Promise(resolve=>finishRead=resolve);};
 const reading=nav.refresh(),confirming=nav.initialize('claude');await nav.initialize('claude');await nav.toggle('deepseek',false);await nav.refresh();assert.equal(ui.initializations.length,1);assert.equal(calls,1);
 finishRead({clients:[]});await reading;assert.equal(nav.clients.length,3);ui.decideInitialize(false);await confirming;assert.equal(calls,1);assert.equal(nav.choosing,null);
});

test('unknown mobile quit requires a successful status read before retry and ignores late replies',async()=>{
 const ui=fixture(),nav=ui.nav;nav.lifecycleTimeout=5;const client={id:'claude',name:'Claude',enabled:true,configured:true,installed:true,connected:true,running:true};nav.clients=[client];nav.provider='claude';let finish,reads=0,mutations=0,readable=false;
 nav.request=(url,body)=>{if(body){mutations++;return new Promise(resolve=>finish=resolve);}reads++;return readable?Promise.resolve({clients:[client]}):Promise.reject(Error('status offline'));};
 await assert.rejects(nav.toggle('claude',false),/尚未确认/);assert.equal(nav.pending.size,0);assert.equal(nav.hasUncertain(),true);assert.equal(nav.clients[0].connected,true);assert.equal(nav.managementState(nav.clients[0]),'结果待确认');
 const before=reads;await nav.poll();await nav.retryOperation('claude');await nav.toggle('claude',false);assert.equal(reads,before);assert.equal(mutations,1);await nav.refresh();assert.equal(nav.hasUncertain(),true);
 readable=true;await nav.refresh();assert.equal(nav.hasUncertain(),false);finish({clients:[{...client,enabled:false,running:false,connected:false}]});await Promise.resolve();await Promise.resolve();assert.equal(nav.clients[0].enabled,true);assert.equal(nav.clients[0].connected,true);
 ui.setChoice(null);await nav.retryOperation('claude');assert.equal(ui.choices.length,2);assert.equal(mutations,1);
});

test('an explicit quit rejection remains visible without rewriting the connection or replaying on unlock',async()=>{
 const ui=fixture(),nav=ui.nav,reason='Windows 已锁定；请解锁电脑后重试初始化连接';nav.clients=[{id:'claude',enabled:true,configured:true,installed:true,connected:true,running:true}];nav.provider='claude';let writes=0;
 nav.request=async(url,body)=>{if(body){writes++;throw Object.assign(Error(reason),{status:409});}return {clients:nav.clients,windowsSession:{state:'locked',interactive:false,reason}};};
 await assert.rejects(nav.toggle('claude',false),/Windows/);assert.equal(nav.hasUncertain(),false);assert.equal(nav.operationFailures.get('claude').message,reason);assert.equal(nav.runtimeState(nav.clients[0]),'已连接');await nav.refresh();assert.equal(nav.operationFailures.get('claude').message,reason);
 nav.applyClients({clients:nav.clients,windowsSession:{state:'unlocked',interactive:true}});assert.equal(writes,1);assert.equal(ui.initializations.length,0);
});

test('mobile connection progress survives launch acknowledgment and fast polls stop at a terminal state',async()=>{
 const ui=fixture(),nav=ui.nav;nav.clients=[{id:'deepseek',enabled:false,selectable:true,configured:true,running:false,connected:false,reason:'客户端已配置，可开启桌面应用'}];nav.provider='deepseek';nav.gatewayRunning=true;let finish,reads=0;const snapshots=[];
 nav.view.setClientStates=clients=>snapshots.push(clients);nav.request=(url,body)=>body?new Promise(resolve=>finish=resolve):Promise.resolve({clients:[{...nav.clients[0],connectionState:'connected',connected:true,running:true}]});
 const starting=nav.toggle('deepseek',true);assert.equal(nav.runtimeState(nav.clients[0]),'正在启动应用…');assert.equal(snapshots.at(-1)[0].pendingEnable,true);assert.equal(nav.clients[0].running,false);
 finish({clients:[{...nav.clients[0],connectionState:'connecting',reason:'Harness 已启动，正在等待桌面连接'}]});await starting;
 assert.equal(nav.managementState(nav.clients[0]),'已启用');assert.equal(nav.runtimeState(nav.clients[0]),'正在连接，请稍候…');
 const read=nav.request;nav.request=(...args)=>{reads++;return read(...args);};await nav.poll(true);assert.equal(reads,1);assert.equal(nav.runtimeState(nav.clients[0]),'已连接');await nav.poll(true);assert.equal(reads,1);
 nav.clients[0].connected=false;nav.clients[0].connectionState='connecting';ui.document.hidden=true;await nav.poll(true);assert.equal(reads,1);
});

test('mobile fast polls deduplicate reads and retry sends the same enabled=true request',async()=>{
 const {nav}=fixture();nav.clients=[{id:'deepseek',enabled:true,selectable:true,configured:true,running:false,connected:false,connectionState:'connecting'}];nav.provider='deepseek';let finish,reads=0;nav.request=()=>{reads++;return new Promise(resolve=>finish=resolve);};
 const first=nav.poll(true);await nav.poll(true);assert.equal(reads,1);finish({clients:[{...nav.clients[0],connectionState:'timeout',retryable:true}]});await first;await nav.poll(true);assert.equal(reads,1);assert.equal(nav.runtimeState(nav.clients[0]),'连接超时');
 let body;nav.request=async(url,value)=>{body=value;return {clients:[{...nav.clients[0],connectionState:'connecting',retryable:false}]};};await nav.toggle('deepseek',true);assert.equal(body.enabled,true);assert.equal(body.provider,'deepseek');assert.equal(nav.clients[0].connected,false);
});
test('disabling the active client updates the switcher immediately and stale polling cannot restore it',async()=>{
  const {nav}=fixture();let readDone,writeDone;
  nav.request=(path,body)=>new Promise(resolve=>body?writeDone=resolve:readDone=resolve);
  const read=nav.refresh(),write=nav.toggle('claude',false);await Promise.resolve();
  assert.equal(nav.provider,'codex');assert.equal(nav.clients.find(c=>c.id==='claude').enabled,false);
  readDone({clients:[{id:'claude',enabled:true,configured:true}]});await read;
  assert.equal(nav.clients.find(c=>c.id==='claude').enabled,false);
  writeDone({clients:nav.clients});await write;assert.equal(nav.pending.size,0);
});
test('a failed toggle restores the original selection and enabled clients',async()=>{
  const {nav}=fixture();nav.request=async()=>{throw Error('offline');};
  await assert.rejects(nav.toggle('claude',false),/offline/);
  assert.equal(nav.provider,'claude');assert.equal(nav.view.provider,'claude');assert.equal(nav.clients.find(c=>c.id==='claude').enabled,true);assert.equal(nav.pending.size,0);
});

test('notification links select the owning provider instead of the last selected client',async()=>{
  const {nav}=fixture(),opened=[];nav.view.open=async id=>opened.push([nav.provider,id]);
  const codex=(id,host)=>opened.push(['codex',id,host]);
  assert.equal(await nav.openChatLink('#run%2Fone%7Etwo~desktop%3Adeepseek',codex),true);
  assert.deepEqual(opened.pop(),['deepseek','run/one~two']);
  await nav.openChatLink('#claude-chat~desktop%3Aclaude',codex);assert.deepEqual(opened.pop(),['claude','claude-chat']);
  await nav.openChatLink('#aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee~my%20server%2F%7E',codex);assert.deepEqual(opened.pop(),['codex','aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee','my server/~']);
});
test('notification routing rejects malformed targets and never enables a disabled client',async()=>{
  const {nav}=fixture(),notices=[];nav.notify=value=>notices.push(value);nav.view.open=async()=>{throw Error('must not open');};
  for(const hash of ['#pair=secret','#not-a-uuid~local','#id~desktop%3Aother','#%zz~desktop%3Aclaude','#x%0A~desktop%3Aclaude','#'+('x'.repeat(513))+'~desktop%3Adeepseek'])assert.equal(await nav.openChatLink(hash,()=>{throw Error('must not open Codex');}),false);
  nav.clients.find(c=>c.id==='claude').enabled=false;
  assert.equal(await nav.openChatLink('#chat~desktop%3Aclaude',()=>{}),true);assert.equal(notices.length,1);assert.equal(nav.clients.find(c=>c.id==='claude').enabled,false);
});
test('a slow notification link cannot reopen a chat after the user switches clients',async()=>{
  const {nav}=fixture();nav.provider=nav.view.provider='codex';let finish;nav.view.generation=0;nav.view.choose=function(id){this.provider=id;this.generation++;return id==='claude'?new Promise(resolve=>finish=resolve):Promise.resolve();};nav.view.open=async()=>{throw Error('stale notification opened');};
  const opening=nav.openChatLink('#chat~desktop%3Aclaude',()=>{});await nav.choose('deepseek');finish();await opening;assert.equal(nav.provider,'deepseek');
});

test('client preferences and observed runtime remain separate during a pending operation',async()=>{
 const {nav}=fixture();nav.gatewayRunning=true;const client=nav.clients.find(row=>row.id==='claude');client.connected=true;client.running=true;let finish;nav.request=()=>new Promise(resolve=>finish=resolve);
 const pending=nav.toggle('claude',false);await Promise.resolve();const optimistic=nav.clients.find(row=>row.id==='claude');assert.equal(nav.managementState(optimistic),'正在退出应用…');assert.equal(nav.runtimeState(optimistic),'已连接');await nav.toggle('deepseek',false);assert.equal(nav.pending.size,1);
 finish({clients:nav.clients.map(row=>row.id==='claude'?{...row,connected:false,running:false}:row),gatewayRunning:true});await pending;assert.equal(nav.managementState(nav.clients[1]),'未启用');assert.equal(nav.runtimeState(nav.clients[1]),'应用未运行');
});
test('stopped gateway controls only startup preferences and preserves actual runtime evidence',async()=>{
 const {nav}=fixture();nav.gatewayRunning=false;nav.clients[1].running=true;nav.clients[1].connected=false;let finish;nav.request=()=>new Promise(resolve=>finish=resolve);
 const pending=nav.toggle('claude',false);assert.equal(nav.managementState(nav.clients[1]),'正在保存…');assert.equal(nav.runtimeState(nav.clients[1]),'应用运行中，尚未连接');finish({clients:nav.clients,gatewayRunning:false});await pending;assert.equal(nav.managementState(nav.clients[1]),'未选择，下次启动生效');assert.equal(nav.runtimeState({...nav.clients[1],running:undefined}),'尚未连接');
});
test('installed selectable clients can be enabled without pretending they are connected',async()=>{
 const {nav}=fixture();nav.clients[1]={id:'claude',installed:true,configured:false,selectable:true,enabled:false};nav.request=async()=>({clients:nav.clients,gatewayRunning:true});await nav.toggle('claude',true);assert.equal(nav.clients[1].enabled,true);assert.equal(nav.runtimeState(nav.clients[1]),'尚未连接');
});

test('the computer-list shortcut exists only in the native mobile shell',()=>{
 const ui=fixture();ui.nav.home={};ui.nav.syncHome();assert.equal(ui.nav.home.hidden,true);ui.mobile(true);ui.nav.syncHome();assert.equal(ui.nav.home.hidden,false);ui.mobile(false);ui.nav.syncHome();assert.equal(ui.nav.home.hidden,true);
});


test('mobile close choices send an explicit desktop quit intent and distinct pending labels',async()=>{
 for(const choice of [false,true,'force']){
  const quitDesktop=choice!==false,forceDesktop=choice==='force';
  const ui=fixture(),nav=ui.nav,calls=[];ui.setChoice(choice);nav.gatewayRunning=true;Object.assign(nav.clients[1],{running:true,canForceQuit:true});let finish;
  nav.request=(url,body)=>{calls.push(body);return new Promise(resolve=>finish=resolve);};
  const pending=nav.toggle('claude',false);assert.equal(nav.clients[1].enabled,true);await Promise.resolve();
  assert.equal(calls.length,1);assert.equal(calls[0].quitDesktop,quitDesktop);assert.equal(calls[0].enabled,false);
  assert.equal(Object.hasOwn(calls[0],'forceDesktop'),forceDesktop);if(forceDesktop)assert.equal(calls[0].forceDesktop,true);
  assert.equal(nav.managementState(nav.clients[1]),forceDesktop?'正在后台强制结束…':quitDesktop?'正在退出应用…':'正在停用接入…');
  finish({clients:nav.clients.map(row=>row.id==='claude'?{...row,connected:false,running:!quitDesktop}:row)});await pending;
  assert.equal(nav.clients[1].enabled,false);assert.equal(nav.clients[1].running,!quitDesktop);assert.equal(nav.pending.size,0);assert.equal(nav.pendingForceDesktop,false);
 }
});
test('a mobile force quit retry requires a new choice and can fall back to normal quit',async()=>{
 const ui=fixture(),nav=ui.nav,calls=[];ui.setChoice('force');nav.clients[1].canForceQuit=true;
 nav.request=async(url,body)=>{if(body){calls.push(body);throw Object.assign(Error('rejected'),{status:409});}return {clients:nav.clients};};
 await assert.rejects(nav.toggle('claude',false),/rejected/);assert.equal(nav.operationFailures.get('claude').action,'force');assert.equal(nav.clients[1].enabled,true);
 ui.setChoice(null);await nav.retryOperation('claude');assert.equal(ui.choices.length,2);assert.equal(calls.length,1);
 ui.setChoice(true);nav.request=async(url,body)=>{calls.push(body);return {clients:nav.clients};};await nav.retryOperation('claude');
 assert.equal(ui.choices.length,3);assert.equal(calls[1].quitDesktop,true);assert.equal(Object.hasOwn(calls[1],'forceDesktop'),false);
});
test('cancelling a mobile close choice sends no request and keeps the current client',async()=>{
 const ui=fixture(),nav=ui.nav;ui.setChoice(null);let requests=0;nav.request=async()=>{requests++;};
 await nav.toggle('claude',false);assert.equal(requests,0);assert.equal(nav.provider,'claude');assert.equal(nav.clients[1].enabled,true);assert.equal(nav.pending.size,0);assert.equal(nav.choosing,null);
});
test('an unanswered mobile close choice blocks duplicate toggles and stale polling',async()=>{
 const ui=fixture(),nav=ui.nav;ui.setChoice('pending');let finishRead,requests=0;
 nav.request=()=>{requests++;return new Promise(resolve=>finishRead=resolve);};const read=nav.refresh(),pending=nav.toggle('claude',false);
 await nav.toggle('deepseek',false);await nav.refresh();assert.equal(ui.choices.length,1);assert.equal(requests,1);
 finishRead({clients:[{id:'codex',enabled:true,configured:true}]});await read;assert.equal(nav.clients.length,3);
 ui.decide(null);await pending;assert.equal(nav.provider,'claude');assert.equal(requests,1);
});
test('mobile can disable a previously enabled app that is no longer selectable',async()=>{
 const ui=fixture(),nav=ui.nav;ui.setChoice(false);Object.assign(nav.clients[1],{selectable:false,configured:false,installed:false});const calls=[];
 nav.request=async(url,body)=>{calls.push(body);return {clients:nav.clients};};await nav.toggle('claude',false);
 assert.equal(calls[0].quitDesktop,false);assert.equal(nav.clients[1].enabled,false);await nav.toggle('claude',true);assert.equal(calls.length,1);
});
test('mobile only-disable failure restores selection and keeps desktop runtime evidence',async()=>{
 const ui=fixture(),nav=ui.nav;ui.setChoice(false);nav.clients[1].running=true;nav.request=async()=>{throw Error('cannot save');};
 await assert.rejects(nav.toggle('claude',false),/cannot save/);assert.equal(nav.provider,'claude');assert.equal(nav.clients[1].enabled,true);assert.equal(nav.clients[1].running,true);
});
test('mobile startup preferences skip the close dialog and enabling omits quit intent',async()=>{
 const ui=fixture(),nav=ui.nav,calls=[];nav.gatewayRunning=false;nav.request=async(url,body)=>{calls.push(body);return {clients:nav.clients};};
 await nav.toggle('claude',false);assert.equal(ui.choices.length,0);assert.equal(calls[0].quitDesktop,false);
 await nav.toggle('claude',true);assert.equal(Object.hasOwn(calls[1],'quitDesktop'),false);
 assert.equal(calls.some(call=>Object.hasOwn(call,'forceDesktop')),false);
});
