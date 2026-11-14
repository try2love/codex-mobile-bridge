'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  let mobile=false,choice=true,finishChoice;const choices=[],preferences=new Map();const context=vm.createContext({ClientLifecycle:{...require('../web/features/clients/client-lifecycle.js'),chooseDisable(client){choices.push(client.id);return choice==='pending'?new Promise(resolve=>finishChoice=resolve):choice;}},document:{documentElement:{classList:{contains:()=>mobile}},getElementById:()=>({hidden:false})},setTimeout,clearTimeout,AbortController,localStorage:{setItem:(key,value)=>preferences.set(key,value),getItem:key=>preferences.get(key)},Event:class{},window:{}});
  context.BridgeI18n={t:value=>value};
  vm.runInContext(fs.readFileSync('web/hosts/environment.js','utf8'),context);
  context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
  let initializeChoice=false,finishInitialize;const initializations=[];context.ClientLifecycle.chooseInitialize=client=>{initializations.push(client.id);return initializeChoice==='pending'?new Promise(resolve=>finishInitialize=resolve):initializeChoice;};
  const C=vm.runInContext('ClientNavigation',context),nav=Object.create(C.prototype),painted=[];
  Object.assign(nav,{clients:[{id:'codex',enabled:true,configured:true},{id:'claude',enabled:true,configured:true},{id:'deepseek',enabled:true,configured:true}],provider:'claude',revision:0,pending:new Set(),notify(){},rememberSwipeList(){},paint(){painted.push(this.clients.filter(c=>c.enabled).map(c=>c.id));},view:{provider:'claude',choose(id){this.provider=id;return Promise.resolve();}}});
  return {nav,painted,choices,document:context.document,window:context.window,setChoice:value=>choice=value,decide:value=>finishChoice(value),mobile:value=>mobile=value,initializations,setInitializeChoice:value=>initializeChoice=value,decideInitialize:value=>finishInitialize(value)};
}
function networkPanel(nav){
  const panel={section:{},box:{setAttribute(){}},status:{},refresh:{}};
  nav.manager={networkAdaptation:panel};return panel;
}
test('independent notification links retain their full client identity and reject a second ZCode entry',()=>{
 const {nav}=fixture(),entry='claude:'+ 'a'.repeat(32),host='desktop:'+entry;
 assert.deepEqual(JSON.parse(JSON.stringify(nav.chatTarget('#same~'+encodeURIComponent(host)))),{provider:entry,id:'same',host});
 for(const value of ['desktop:zcode:'+ 'b'.repeat(32),'desktop:claude:bad','desktop:claude:'+ 'a'.repeat(31)])assert.equal(nav.chatTarget('#same~'+encodeURIComponent(value)),null);
});
test('network recovery status reads do not enable the option, and relay does not query it',async()=>{
  const ui=fixture(),panel=networkPanel(ui.nav),calls=[];
  ui.nav.request=async(path,body)=>{calls.push({path,body});return {enabled:false};};
  await ui.nav.readNetworkAdaptation();assert.equal(panel.box.checked,false);assert.equal(panel.box.disabled,false);
  assert.deepEqual(calls,[{path:'/api/network-adaptation',body:undefined}]);
  ui.window.BridgeSharedRelay=true;await ui.nav.readNetworkAdaptation();await ui.nav.setNetworkAdaptation(true);assert.equal(calls.length,1);
});
test('network recovery changes once and refreshes a lost write response without replay',async()=>{
  const {nav}=fixture(),panel=networkPanel(nav),calls=[];nav.networkAdaptationState={enabled:false};
  nav.request=async(path,body)=>{calls.push({path,body});if(body)throw Error('lost response');return {enabled:true};};
  await nav.setNetworkAdaptation(true);
  assert.equal(calls.length,2);assert.equal(calls[0].body.enabled,true);assert.equal(calls[1].body,undefined);
  assert.equal(panel.box.checked,true);assert.equal(panel.box.disabled,false);assert.equal(nav.networkAdaptationError,'');
});
test('failed recovery reconciliation disables writes until a successful explicit read',async()=>{
  const {nav}=fixture(),panel=networkPanel(nav),calls=[];nav.networkAdaptationState={enabled:false};
  nav.request=async(path,body)=>{calls.push(body);throw Error('disconnected');};await nav.setNetworkAdaptation(true);
  assert.equal(calls.length,2);assert.equal(panel.box.disabled,true);assert.match(panel.status.textContent,/尚未确认/);
  await nav.setNetworkAdaptation(true);assert.equal(calls.length,2);
  nav.request=async()=>({enabled:true});await nav.readNetworkAdaptation();assert.equal(panel.box.checked,true);assert.equal(panel.box.disabled,false);
});
test('network recovery prevents overlapping operations and leaves closed dialogs untouched',async()=>{
  const {nav}=fixture();networkPanel(nav);nav.networkAdaptationState={enabled:false};let finish,count=0;
  nav.request=()=>{count++;return new Promise(resolve=>finish=resolve);};
  const change=nav.setNetworkAdaptation(true);await nav.setNetworkAdaptation(false);await nav.readNetworkAdaptation();assert.equal(count,1);
  nav.manager=null;finish({enabled:true});await change;assert.equal(nav.networkAdaptationState.enabled,true);assert.equal(nav.networkAdaptationBusy,false);
});
test('older, malformed and password-free gateway responses do not leave active recovery controls',async()=>{
  const {nav}=fixture(),panel=networkPanel(nav);
  nav.request=async()=>{throw Object.assign(Error('Not found'),{status:404});};await nav.readNetworkAdaptation();assert.equal(panel.box.disabled,true);assert.match(panel.status.textContent,/更新网关/);
  nav.request=async()=>({});await nav.readNetworkAdaptation();assert.equal(panel.box.disabled,true);assert.match(panel.status.textContent,/读取失败/);
  nav.request=async()=>({enabled:false,canEdit:false});await nav.readNetworkAdaptation();assert.equal(panel.box.disabled,true);assert.match(panel.status.textContent,/免密访问/);
  nav.request=()=>{throw Error('must not write');};await nav.setNetworkAdaptation(true);
});
test('notification settings stay hidden on shared relay while remaining actions use all columns',()=>{
  const ui=fixture(),classes=new Set();
  ui.nav.switchToggle={hidden:false};ui.nav.settingsNotifications={hidden:false};
  ui.nav.settingsActions={classList:{toggle(name,enabled){if(enabled)classes.add(name);else classes.delete(name);}}};
  ui.window.BridgeSharedRelay=false;ui.nav.paintSettingsActions();
  assert.equal(ui.nav.settingsNotifications.hidden,false);assert.equal(classes.size,0);
  ui.window.BridgeSharedRelay=true;ui.nav.paintSettingsActions();
  assert.equal(ui.nav.settingsNotifications.hidden,true);assert.equal(classes.has('three-actions'),true);
  ui.nav.switchToggle.hidden=true;ui.nav.paintSettingsActions();
  assert.equal(classes.has('three-actions'),false);assert.equal(classes.has('two-actions'),true);
});
test('changing provider paints the shell before its slow read resolves',async()=>{
  const {nav,painted}=fixture();let resolve;
  nav.view.choose=()=>new Promise(done=>resolve=done);
  const pending=nav.choose('deepseek');assert.equal(nav.provider,'deepseek');assert.equal(painted.length,1);
  resolve();await pending;
});
test('an older client status reply cannot undo a later provider choice',async()=>{
  const {nav}=fixture();let finish,applied=0;
  nav.request=()=>new Promise(resolve=>{finish=resolve;});
  const reading=nav.refresh();
  nav.applyClients=()=>{applied++;};
  await nav.choose('deepseek');
  finish({clients:[{id:'claude',enabled:true}]});await reading;
  assert.equal(nav.provider,'deepseek');assert.equal(applied,0);
});

test('clicking the selected provider does not reset its view or issue another read',async()=>{
 const {nav}=fixture();let calls=0;nav.view.choose=async()=>{calls++;};await nav.choose('claude');assert.equal(calls,0);
});

test('discovery confirms a cached preview only while its provider remains enabled',async()=>{
 const {nav}=fixture();let confirmations=0;
 nav.view.previewOnly=true;nav.view.confirmCachedList=function(){this.previewOnly=false;confirmations++;};
 nav.applyClients({clients:nav.clients});assert.equal(confirmations,1);assert.equal(nav.provider,'claude');
 nav.view.previewOnly=true;nav.view.choose=async function(id){this.provider=id;this.previewOnly=false;};
 nav.applyClients({clients:nav.clients.map(client=>({...client,enabled:false}))});await Promise.resolve();
 assert.equal(nav.provider,null);assert.equal(nav.view.provider,'codex');assert.equal(nav.view.previewOnly,false);assert.equal(confirmations,1);
});

test('turning the last client off and re-enabling Codex refreshes its list',async()=>{
 const {nav}=fixture(),selected=[];nav.clients=[{id:'codex',enabled:true,configured:true}];nav.provider=nav.view.provider='codex';
 nav.view.onSelect=id=>selected.push(id);
 nav.view.choose=async function(id){if(id===this.provider)return;this.provider=id;this.onSelect(id);};
 let complete;
 nav.request=(_path,body)=>new Promise(resolve=>{complete=()=>resolve({clients:[{...nav.clients[0],enabled:body.enabled}]});});
 nav.applyClients({clients:[{...nav.clients[0],enabled:false}]});await Promise.resolve();
 assert.equal(nav.provider,null);assert.deepEqual(selected,[]);
 const enabling=nav.toggle('codex',true);assert.deepEqual(selected,[]);
 complete();await enabling;
 assert.equal(nav.provider,'codex');assert.deepEqual(selected,['codex']);
});

test('phone initialization sends foreground intent only after an explicit confirmation',async()=>{
 for(const accepted of [false,true]){
  const ui=fixture(),nav=ui.nav,calls=[];ui.setInitializeChoice(accepted);Object.assign(nav.clients[1],{installed:true,connected:false,setupStatus:'needs-initialization'});nav.request=async(url,body)=>{calls.push(body);return {clients:nav.clients.map(client=>client.id==='claude'?{...client,connectionState:'connecting'}:client)};};
  await nav.initialize('claude');assert.deepEqual(ui.initializations,['claude']);assert.equal(calls.length,accepted?1:0);if(accepted){assert.equal(calls[0].initializeDesktop,true);assert.equal(calls[0].enabled,true);assert.equal(calls[0].provider,'claude');}
 }
 const {nav}=fixture();let body;nav.request=async(url,value)=>{body=value;return {clients:nav.clients};};await nav.toggle('claude',true);assert.equal(Object.hasOwn(body,'initializeDesktop'),false);
});

test('phone enabling, retrying and reconnecting Claude never initialize or prompt automatically',async()=>{
 const ui=fixture(),nav=ui.nav,calls=[];Object.assign(nav.clients[1],{installed:true,connected:false,initializationMode:'foreground',setupStatus:'needs-initialization',reconnectSupported:true,canReconnect:true});
 nav.request=async(url,body)=>{calls.push({url,body});return {clients:nav.clients};};await nav.toggle('claude',true);
 nav.operationFailures=new Map([['claude',{action:'enable',uncertain:false,message:'retry'}]]);await nav.retryOperation('claude');await nav.reconnect('claude');
 assert.equal(calls.length,3);assert.equal(calls[2].url,'/api/clients/claude/reconnect');assert.ok(calls.every(call=>!Object.hasOwn(call.body,'initializeDesktop')));assert.equal(ui.initializations.length,0);assert.equal(nav.connection(nav.clients[1]).canInitialize,true);
});

test('an unanswered initialization prompt blocks duplicate operations and stale polling',async()=>{
 const ui=fixture(),nav=ui.nav;ui.setInitializeChoice('pending');Object.assign(nav.clients[1],{installed:true,setupStatus:'needs-initialization'});let finishRead,calls=0;nav.request=()=>{calls++;return new Promise(resolve=>finishRead=resolve);};
 const reading=nav.refresh(),confirming=nav.initialize('claude');await nav.initialize('claude');await nav.toggle('deepseek',false);await nav.refresh();assert.equal(ui.initializations.length,1);assert.equal(calls,1);
 finishRead({clients:[]});await reading;assert.equal(nav.clients.length,3);ui.decideInitialize(false);await confirming;assert.equal(calls,1);assert.equal(nav.choosing,null);
});

test('pending client status preserves selection and uncertain operation evidence',async()=>{
 const {nav}=fixture(),notices=[];nav.notify=value=>notices.push(value);
 nav.operationFailures=new Map([['claude',{action:'quit',uncertain:true,message:'结果未知'}]]);
 nav.request=async()=>({pending:true,clients:[]});
 await nav.refresh();
 assert.equal(nav.provider,'claude');assert.equal(nav.clients.length,3);
 assert.equal(nav.operationFailures.get('claude').uncertain,true);
 assert.equal(nav.statusPending,true);assert.deepEqual(notices,[]);
});

test('pending client status uses existing fast poll and applies only complete results',async()=>{
 const {nav}=fixture();let reads=0;const current=nav.clients;
 nav.applyClients=data=>{nav.clients=data.clients;};
 nav.request=async()=>++reads===1?{pending:true,clients:[]}:{pending:false,clients:[{...current[0],name:'新状态'}]};
 await nav.refresh();assert.equal(nav.clients.length,3);
 await nav.poll(true);assert.equal(reads,2);assert.equal(nav.clients.length,1);
 assert.equal(nav.clients[0].name,'新状态');assert.equal(nav.statusPending,false);
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
test('notification waits through pending status and opens after a complete read without a list fetch',async()=>{
  const {nav}=fixture(),opened=[],notices=[],options=[];let reads=0;
  nav.clients=[];nav.provider=nav.view.provider='codex';nav.notify=value=>notices.push(value);
  nav.view.open=async id=>opened.push([nav.provider,id]);
  nav.view.choose=function(id,value){this.provider=id;options.push(value);};
  nav.onApplicationChange=async(previous,id,value)=>options.push(value);
  nav.request=async()=>++reads===1?{pending:true,clients:[]}:{clients:[{id:'claude',enabled:true}]};
  await nav.openChatLink('#target~desktop%3Aclaude',()=>{});
  await nav.refresh();assert.equal(nav.statusPending,true);assert.deepEqual(opened,[]);assert.deepEqual(notices,[]);
  await nav.poll(true);assert.deepEqual(opened,[['claude','target']]);assert.deepEqual(notices,[]);
  assert.equal(nav.pendingChatLink,null);assert.ok(options.length);assert.ok(options.every(value=>value.loadList===false));
  await nav.refresh();assert.equal(opened.length,1);
});
test('failed status reads retain only the latest pending notification',async()=>{
  const {nav}=fixture(),opened=[],notices=[];nav.clients=[];nav.notify=value=>notices.push(value);nav.view.open=async id=>opened.push(id);
  nav.request=async()=>{throw Error('offline');};
  await nav.openChatLink('#first~desktop%3Aclaude',()=>{});await nav.refresh();
  await nav.openChatLink('#latest~desktop%3Aclaude',()=>{});
  nav.request=async()=>({clients:[{id:'claude',enabled:true}]});await nav.refresh();
  assert.deepEqual(opened,['latest']);assert.deepEqual(notices,['offline']);
});
test('complete empty client status rejects the queued target instead of waiting forever',async()=>{
  const {nav}=fixture(),notices=[];nav.clients=[];nav.notify=value=>notices.push(value);
  nav.request=async()=>({clients:[]});
  await nav.openChatLink('#chat~desktop%3Aclaude',()=>{});await nav.refresh();
  assert.equal(nav.clientsKnown,true);assert.equal(nav.pendingChatLink,null);assert.equal(notices.length,1);
});
test('manual application selection cancels a queued notification',async()=>{
  const {nav}=fixture();nav.clients=[];
  await nav.openChatLink('#chat~desktop%3Aclaude',()=>{});await nav.choose('deepseek');
  assert.equal(nav.pendingChatLink,null);assert.equal(nav.provider,'deepseek');
});
test('logout clears the queued notification and ignores the old status reply',async()=>{
  const {nav}=fixture();nav.clients=[];nav.swipeSnapshots=new Map();let finish;
  nav.request=()=>new Promise(resolve=>finish=resolve);
  await nav.openChatLink('#chat~desktop%3Aclaude',()=>{throw Error('stale login');});
  const reading=nav.refresh();nav.clear();finish({clients:[{id:'claude',enabled:true}]});await reading;
  assert.equal(nav.pendingChatLink,null);assert.equal(nav.clientsKnown,false);assert.equal(nav.clients.length,0);
});
test('queued WSL notifications validate the exact entry and preserve its host',async()=>{
  const {nav}=fixture(),opened=[],host='wsl:'+'a'.repeat(32),id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';nav.clients=[];
  nav.request=async()=>({clients:[{id:'codex',enabled:true,entries:[{id:'desktop',enabled:false},{id:host,enabled:true}]}]});
  await nav.openChatLink('#'+id+'~'+encodeURIComponent(host),(...args)=>opened.push(args));await nav.refresh();
  assert.deepEqual(opened,[[id,host]]);
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


test('bottom app visibility preferences do not disable clients or change the selected provider',()=>{
 const {nav}=fixture();assert.deepEqual([...nav.visibleClients()].map(row=>row.id),['codex','claude','deepseek']);
 nav.preference('switch:claude',false);assert.deepEqual([...nav.visibleClients()].map(row=>row.id),['codex','deepseek']);assert.equal(nav.provider,'claude');assert.equal(nav.clients[1].enabled,true);
 nav.preference('switch:codex',false);nav.preference('switch:deepseek',false);assert.equal(nav.visibleClients().length,0);assert.equal(nav.clients.filter(row=>row.enabled).length,3);
 nav.preference('switch:claude',true);assert.deepEqual([...nav.visibleClients()].map(row=>row.id),['claude']);
});

function managerFixture(){
 const ui=fixture(),node=tag=>({tag,children:[],attributes:{},dataset:{},classList:{toggle(){}},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(key,value){this.attributes[key]=value;}});
 ui.document.createElement=node;ui.nav.icon=()=>node('img');ui.nav.manager={dialog:{open:true},list:node('div'),note:node('p')};
 const all=root=>[root,...root.children.flatMap(all)];ui.controls=()=>all(ui.nav.manager.list);return ui;
}
test('remote main list shares WSL connect routing and blocks concurrent starts for both clients',async(t)=>{
 for(const [provider,kind,prefix] of [['codex','wsl-cli','wsl:'],['claude','wsl-claude-cli','wsl-claude:']]){
  const ui=managerFixture(),nav=ui.nav,client={id:prefix+'b'.repeat(32),kind,name:'WSL fixture',enabled:true,configured:true,distributionRunning:false,connected:false};
  nav.clients=[client];nav.gatewayRunning=true;const calls=[];let finish,refreshes=0;t.after(()=>finish?.({}));
  nav.request=(url,body)=>{calls.push({url,body:JSON.parse(JSON.stringify(body))});return new Promise(resolve=>finish=resolve);};
  nav.refresh=async()=>{refreshes++;};nav.renderManager();
  const button=ui.controls().find(n=>n.tag==='button'&&n.textContent==='启动并连接 WSL');assert.ok(button);
  const pending=button.onclick();await nav.connectWsl(client.id);nav.renderManager();
  assert.equal(ui.controls().find(n=>n.tag==='button'&&n.textContent==='正在连接，请稍候…').disabled,true);
  assert.deepEqual(calls,[{url:'/api/wsl-accounts',body:{provider,entry:client.id,action:'connect'}}]);
  finish({provider,entry:client.id,available:true});await pending;assert.equal(refreshes,1);assert.equal(client.connected,false);assert.equal(client.enabled,true);
  for(const change of [{enabled:false},{distributionRunning:true},{connected:true}]){
   nav.clients=[{...client,...change}];nav.renderManager();assert.equal(ui.controls().some(n=>n.tag==='button'&&n.textContent==='启动并连接 WSL'),false);
  }
  nav.clients=[client];nav.gatewayRunning=false;await nav.connectWsl(client.id);assert.equal(calls.length,1);
  nav.gatewayRunning=true;ui.window.BridgeSharedRelay={};nav.renderManager();
  assert.equal(ui.controls().some(n=>n.tag==='button'&&n.textContent==='启动并连接 WSL'),false);
  await nav.connectWsl(client.id);assert.equal(calls.length,1,'shared relay does not expose account mutation routes');
 }
});
test('an uncertain WSL start refreshes without replay and waits for known distribution status',async()=>{
 const {nav}=fixture(),client={id:'wsl:'+'c'.repeat(32),kind:'wsl-cli',enabled:true,distributionRunning:null};
 nav.clients=[client];nav.gatewayRunning=true;let writes=0,reads=0;
 nav.request=async()=>{writes++;throw Object.assign(Error('connection lost'),{uncertain:true});};nav.refresh=async()=>{reads++;};
 await assert.rejects(nav.connectWsl(client.id),/connection lost/);assert.equal(writes,1);assert.equal(reads,1);
 await nav.connectWsl(client.id);assert.equal(writes,1);
 const lifecycle=require('../web/features/clients/client-lifecycle.js');
 lifecycle.reconcile(nav.operationFailures,[client]);assert.equal(nav.operationFailures.get(client.id).uncertain,true);
 lifecycle.reconcile(nav.operationFailures,[{...client,distributionRunning:false}]);assert.equal(nav.operationFailures.get(client.id).uncertain,false);
 nav.operationFailures.get(client.id).uncertain=true;
 lifecycle.reconcile(nav.operationFailures,[{...client,distributionRunning:true}]);assert.equal(nav.operationFailures.has(client.id),false);
});
test('app management offers force only through close choices and retains a disabled running app exit',async()=>{
 for(const id of ['codex','claude','deepseek']){
  const ui=managerFixture(),nav=ui.nav,client={id,name:id,enabled:true,configured:true,running:true,connected:true,canForceQuit:true};nav.clients=[client];nav.gatewayRunning=true;nav.renderManager();
  assert.equal(ui.controls().some(node=>node.textContent==='强制关闭 App'),false);assert.equal(ui.controls().some(node=>node.textContent==='关闭电脑 App…'),false);
  Object.assign(client,{enabled:false,configured:false,selectable:false,connected:false});nav.renderManager();
  const close=ui.controls().find(node=>node.textContent==='关闭电脑 App…');assert.ok(close);assert.equal(!!close.disabled,false);assert.equal(ui.controls().find(node=>node.attributes.role==='switch').disabled,true);
  ui.setChoice(null);await close.onclick();assert.deepEqual(ui.choices,[id]);assert.equal(nav.clients[0].enabled,false);
  let body;ui.setChoice('force');nav.request=async(url,value)=>{body=value;return {clients:[{...client,running:false}]};};await close.onclick();
  assert.equal(body.enabled,false);assert.equal(body.quitDesktop,true);assert.equal(body.forceDesktop,true);assert.equal(body.forceConfirmed,true);
  nav.renderManager();assert.equal(ui.controls().some(node=>node.textContent==='关闭电脑 App…'),false);
  nav.clients=[{...client,running:true}];nav.gatewayRunning=false;nav.renderManager();assert.equal(ui.controls().some(node=>node.textContent==='关闭电脑 App…'),false);
 }
});

test('app management explicitly starts supported Claude Desktop alongside CLI access',async()=>{
 const ui=managerFixture(),nav=ui.nav,client={id:'claude',name:'Claude',enabled:false,installed:true,
  selectable:true,connected:false,running:false,cliAvailable:true,canStartDesktop:true,setupStatus:'ready-to-connect'};
 nav.clients=[client];nav.gatewayRunning=true;nav.renderManager();
 let start=ui.controls().find(node=>node.textContent==='启动 Desktop');assert.ok(start);
 client.enabled=true;client.retryable=true;nav.renderManager();
 start=ui.controls().find(node=>node.textContent==='启动 Desktop');assert.ok(start);
 assert.equal(ui.controls().some(node=>node.textContent==='重试连接'),false);
 ui.setInitializeChoice(true);const calls=[];
 nav.request=async(url,body)=>{calls.push({url,body});return {clients:[{...client,connectionState:'connecting'}]};};
 await start.onclick();
 assert.deepEqual(ui.initializations,['claude']);
 assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/clients');
 assert.equal(calls[0].body.initializeDesktop,true);assert.equal(calls[0].body.enabled,true);
 assert.equal(require('../web/shared/i18n.js').translateEnglish('启动 Desktop'),'Start Desktop');
 for(const changed of [{running:true,canStartDesktop:false},{installed:false,canStartDesktop:false},
   {canStartDesktop:false,setupStatus:'unsupported'}]){
  nav.clients=[{...client,...changed}];nav.renderManager();
  assert.equal(ui.controls().some(node=>node.textContent==='启动 Desktop'),false);
 }
});

test('app management keeps Desktop initialization for a running unconnected Claude beside CLI',async()=>{
 const ui=managerFixture(),nav=ui.nav,client={id:'claude',name:'Claude',enabled:true,installed:true,
  selectable:true,connected:false,running:true,cliAvailable:true,canStartDesktop:false,canInitializeDesktop:true,setupStatus:'idle'};
 nav.clients=[client];nav.gatewayRunning=true;nav.renderManager();
 const initialize=ui.controls().find(node=>node.textContent==='初始化连接');assert.ok(initialize);
 assert.equal(ui.controls().some(node=>node.textContent==='启动 Desktop'),false);
 ui.setInitializeChoice(true);const calls=[];nav.request=async(url,body)=>{calls.push({url,body});return {clients:[client]};};
 await initialize.onclick();
 assert.deepEqual(ui.initializations,['claude']);
 assert.equal(calls.length,1);assert.equal(calls[0].body.initializeDesktop,true);
});

function touchFixture(){
 const ui=fixture(),nav=ui.nav,list={};ui.document.querySelector=()=>null;
 // Provide browser-only globals for the production gesture handlers.
 const context=vm.createContext({document:ui.document,localStorage:{getItem:()=>null},window:{getSelection:()=>null},innerWidth:390,Date,BridgeI18n:{t:s=>s}});
 context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
 nav.listTouch=vm.runInContext('ClientNavigation.prototype.listTouch',context);
 const calls=[];nav.dragListSwipe=()=>{};nav.finishListSwipe=(_gesture,_dx,next)=>{if(next)nav.choose(next);};nav.choose=async id=>{calls.push(id);nav.provider=id;};
 const target={closest:()=>null};
 const touch=(phase,x,y=100,extra={})=>{const point={identifier:1,clientX:x,clientY:y};let prevented=false;nav.listTouch(phase,{target,touches:phase==='end'?[]:[point],changedTouches:[point],cancelable:true,preventDefault(){prevented=true;},...extra},list);return prevented;};
 return {...ui,list,touch,calls,target};
}
test('list swipes default on, preserve explicit opt-out, use enabled apps even when icons are hidden, and stop at edges',()=>{
 const f=touchFixture();assert.equal(f.nav.preference('swipe-clients'),true);f.nav.preference('swipe-clients',false);f.touch('start',200);f.touch('move',110);f.touch('end',90);assert.deepEqual(f.calls,[]);
 f.nav.preference('swipe-clients',true);f.nav.preference('switch:deepseek',false);f.nav.preference('switch-collapsed',true);
 f.touch('start',200);assert.equal(f.touch('move',110),true);f.touch('end',90);assert.deepEqual(f.calls,['deepseek']);
 f.touch('start',200);f.touch('end',90);assert.deepEqual(f.calls,['deepseek']);
 f.nav.clients[1].enabled=false;f.touch('start',100);f.touch('move',180);f.touch('end',210);assert.deepEqual(f.calls,['deepseek','codex']);
});
test('vertical scroll, two fingers, edge navigation, controls, modal and short swipes never switch apps',()=>{
 for(const kind of ['vertical','two','edge','control','modal','short','provider-change']){
  const f=touchFixture();f.nav.preference('swipe-clients',true);
  if(kind==='control')f.target.closest=()=>({closest:()=>null});
  if(kind==='modal')f.document.querySelector=()=>({});
  f.touch('start',kind==='edge'?10:200);
  if(kind==='vertical'){assert.equal(f.touch('move',180,145),false);f.touch('end',90,150);}
  else if(kind==='two'){f.touch('move',150,100,{touches:[{identifier:1,clientX:150,clientY:100},{identifier:2,clientX:200,clientY:100}]});f.touch('end',90);}
  else if(kind==='short'){f.touch('move',170);f.touch('end',160);}
  else {if(kind==='provider-change')f.nav.provider='codex';f.touch('move',110);f.touch('end',90);}
  assert.deepEqual(f.calls,[],kind);
 }
});

test('swipe settles before choosing and canceled animations cannot switch later',async()=>{
 const source=fs.readFileSync('web/features/clients/client-navigation.js','utf8');
 for(const cancel of [false,true]){
  const context=vm.createContext({window:{matchMedia:()=>({matches:false})}});vm.runInContext(source,context);
  const C=vm.runInContext('ClientNavigation',context),nav=Object.create(C.prototype),calls=[],waits=[];
  const node=()=>({style:{transform:'translate3d(-100px,0,0)'},animate(){let resolve,reject;const finished=new Promise((yes,no)=>{resolve=yes;reject=no;});const animation={finished,cancel(){reject(Object.assign(Error('canceled'),{name:'AbortError'}));}};waits.push({resolve,animation});return animation;}});
  let removed=false;const preview={current:node(),incoming:node(),root:{remove(){removed=true;}},next:'deepseek',direction:1,width:390};
  Object.assign(nav,{provider:'claude',clients:[{id:'deepseek',enabled:true}],swipePreview:preview,dragListSwipe(){},choose:async id=>calls.push(id),notify:message=>{throw Error(message);}});
  const done=nav.finishListSwipe({provider:'claude'},-100,'deepseek');assert.deepEqual(calls,[]);
  if(cancel)nav.cancelListSwipe();else waits.forEach(wait=>wait.resolve());
  await done;assert.deepEqual(calls,cancel?[]:['deepseek']);assert.equal(removed,true);assert.equal(nav.swipePreview,null);
 }
});
test('a second flick adopts the first settling choice and reaches the following client',async()=>{
 const context=vm.createContext({window:{matchMedia:()=>({matches:false}),getSelection:()=>null},document:{querySelector:()=>null},localStorage:{getItem:()=>null},innerWidth:390,Date,BridgeI18n:{t:s=>s}});
 context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
 const C=vm.runInContext('ClientNavigation',context),nav=Object.create(C.prototype),calls=[],waits=[],list={getBoundingClientRect:()=>({width:390})};
 const node=()=>({style:{transform:'translate3d(-100px,0,0)'},animate(){let resolve,reject;const finished=new Promise((yes,no)=>{resolve=yes;reject=no;});const animation={finished,cancel(){reject(Object.assign(Error('canceled'),{name:'AbortError'}));}};waits.push({resolve});return animation;}});
 Object.assign(nav,{provider:'codex',clients:['codex','claude','deepseek'].map(id=>({id,enabled:true})),sidebar:list,view:{list},dragListSwipe(gesture,dx){if(this.swipePreview)return;const enabled=this.clients,index=enabled.findIndex(c=>c.id===gesture.provider),next=enabled[index+(dx<0?1:-1)];this.swipePreview={provider:gesture.provider,next:next?.id,direction:1,width:390,current:node(),incoming:node(),root:{remove(){}}};},choose(id){this.cancelListSwipe();this.provider=id;calls.push(id);return Promise.resolve();},notify:message=>{throw Error(message);}});
 const touch=(phase,x)=>{const point={identifier:1,clientX:x,clientY:100};nav.listTouch(phase,{target:{closest:()=>null},touches:phase==='end'?[]:[point],changedTouches:[point],cancelable:true,preventDefault(){}},list);};
 touch('start',200);touch('move',110);touch('end',90);assert.deepEqual(calls,[]);
 touch('start',200);assert.deepEqual(calls,['claude']);assert.equal(nav.listGesture.provider,'claude');
 touch('move',110);touch('end',90);waits.slice(2).forEach(wait=>wait.resolve());await new Promise(setImmediate);
 assert.deepEqual(calls,['claude','deepseek']);assert.equal(nav.provider,'deepseek');
});

test('reduced-motion swipes commit without animation and recheck enabled state',async()=>{
 const context=vm.createContext({window:{matchMedia:()=>({matches:true})}});context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
 const C=vm.runInContext('ClientNavigation',context);
 for(const enabled of [true,false]){
  const nav=Object.create(C.prototype),calls=[];Object.assign(nav,{provider:'claude',clients:[{id:'deepseek',enabled}],swipePreview:{next:'deepseek',direction:1,width:390,root:{remove(){}}},dragListSwipe(){},choose:async id=>calls.push(id),notify:message=>{throw Error(message);}});
  await nav.finishListSwipe({provider:'claude'},-100,'deepseek');assert.deepEqual(calls,enabled?['deepseek']:[]);assert.equal(nav.swipePreview,null);
 }
});

test('list entry acknowledges only the displayed provider snapshot, once per visit',async()=>{
 const {nav,document}=fixture(),calls=[];nav.request=async(path,body)=>calls.push({path,body});
 const value={notificationRead:{streamId:'stream',cursor:12}};
 nav.acknowledgeList('codex',value);nav.acknowledgeList('claude',{...value,connected:false});assert.equal(calls.length,0);
 nav.acknowledgeList('claude',{...value,complete:false});assert.equal(calls.length,0);
 document.hidden=true;nav.acknowledgeList('claude',value);assert.equal(calls.length,0);document.hidden=false;
 nav.acknowledgeList('claude',value);nav.acknowledgeList('claude',{notificationRead:{streamId:'stream',cursor:13}});
 assert.equal(calls.length,1);assert.equal(calls[0].body.provider,'claude');assert.equal(calls[0].body.through,12);
 await nav.choose('deepseek');nav.acknowledgeList('deepseek',value);assert.equal(calls.length,2);
});

test('Claude WSL entries stay under Claude and can be enabled independently of native access',()=>{
 const {nav}=fixture(),id='wsl-claude:'+'a'.repeat(32);
 nav.clients=[{id:'claude',name:'Claude',enabled:true,nativeEnabled:false,entries:[{id,enabled:true,kind:'wsl-claude-cli',name:'WSL Claude Code'}]}];
 const rows=nav.managementClients();assert.equal(rows.length,2);assert.equal(rows[0].enabled,false);assert.equal(rows[1].parentId,'claude');
 nav.clients=nav.withClientEnabled('claude',true);assert.equal(nav.clients[0].nativeEnabled,true);assert.equal(nav.clients[0].entries[0].enabled,true);
 nav.clients=nav.withClientEnabled(id,false);assert.equal(nav.clients[0].enabled,true);assert.equal(nav.clients[0].entries[0].enabled,false);
 nav.clients=nav.withClientEnabled('claude',false);assert.equal(nav.clients[0].enabled,false);
});


test('opening a WSL Claude entry reuses the shared Claude list and its cached selection',async()=>{
 const {nav}=fixture(),entry='wsl-claude:'+'a'.repeat(32),opened=[];
 nav.provider=nav.view.provider='codex';
 nav.view.choose=async function(id){this.provider=id;opened.push(id);};
 await nav.choose(entry);
 assert.equal(nav.provider,'claude');assert.equal(nav.view.provider,'claude');assert.deepEqual(opened,['claude']);
 await nav.choose(entry);assert.deepEqual(opened,['claude'],'same family must retain its list and cache');
 const independent='claude:'+'b'.repeat(32);await nav.choose(independent);
 assert.equal(nav.provider,independent,'independent desktop profiles keep their own identity');
});

test('Claude WSL management uses the packaged Claude icon',()=>{
 const {nav,document}=fixture();document.createElement=tag=>({tag,setAttribute(){},append(child){this.child=child;}});const icon=nav.icon('wsl-claude:'+'a'.repeat(32));assert.equal(icon.child.src,'/client-icons/claude.png');
});
test('authenticated startup opens only the configured native or WSL entry before status arrives',async()=>{
 const {nav}=fixture(),opened=[],notices=[];
 const host='wsl:'+'a'.repeat(32),id='11111111-1111-4111-8111-111111111111';
 nav.clients=[];nav.clientsKnown=false;nav.enabledClients=[host];nav.provider=nav.view.provider='codex';
 nav.notify=text=>notices.push(text);nav.choose=async()=>{};
 await nav.openChatLink('#'+id+'~local',(...args)=>opened.push(args));
 assert.equal(opened.length,0);assert.equal(notices.length,1);
 await nav.openChatLink('#'+id+'~'+encodeURIComponent(host),(...args)=>opened.push(args));
 assert.deepEqual(opened,[[id,host]]);assert.equal(nav.pendingChatLink,null);
 nav.clientsKnown=true;
 await nav.openChatLink('#'+id+'~'+encodeURIComponent(host),(...args)=>opened.push(args));
 assert.equal(opened.length,1);assert.equal(notices.length,2);
});


test('independent Codex entry routes through the shared Codex page and retains its host',async()=>{
  const {nav}=fixture(),calls=[];nav.onApplicationChange=(previous,provider,options)=>calls.push({previous,provider,...options});
  const host='codex:'+'a'.repeat(32);await nav.choose(host);
  assert.equal(nav.provider,'codex');assert.equal(nav.view.provider,'codex');assert.equal(calls[0].host,host);
  await nav.choose(host);assert.equal(calls.length,1,'selecting the current entry preserves list and scroll state');
});

test('returning from an independent Codex entry selects the default list instead of retaining the profile',async()=>{
 const {nav}=fixture(),calls=[];nav.onApplicationChange=(...args)=>calls.push(args);
 await nav.choose('codex:'+'b'.repeat(32));await nav.choose('codex');
 assert.equal(calls.at(-1)[2].host,'local');assert.equal(nav.codexEntry,null);
});

test('independent Codex profiles keep separate bottom identities and survive status refresh',async()=>{
 const {nav}=fixture(),id='codex:'+'a'.repeat(32),other='codex:'+'b'.repeat(32),events=[];
 nav.clients=[{id:'codex',enabled:true},{id,kind:'codex-local',enabled:true},{id:other,kind:'codex-local',enabled:true}];
 nav.onApplicationChange=(previous,provider,options)=>events.push({previous,provider,...options});
 await nav.choose(id);assert.equal(nav.provider,'codex');assert.equal(nav.activeClient(),id);assert.equal(events.at(-1).host,id);
 events.length=0;nav.applyClients({clients:nav.clients});assert.equal(events.length,0);assert.equal(nav.activeClient(),id);
 await nav.choose(other);assert.equal(nav.activeClient(),other);assert.equal(events.at(-1).host,other);
 await nav.choose('codex');assert.equal(nav.activeClient(),'codex');assert.equal(events.at(-1).host,'local');
});


test('account management owns the shared connection chooser and releases its status subscription',async()=>{
 const {nav}=fixture(),values=[],selected=[];nav.clients=[{id:'zcode',enabled:true,connected:true,connectionMode:'official-remote',connectionModes:[{id:'private-pipe',label:'本机连接',configured:true},{id:'official-remote',label:'官方远控',configured:true}]}];
 nav.selectConnectionMode=async(id,mode)=>selected.push([id,mode]);const release=nav.bindAccountManagement({setManagementAction:value=>values.push(value)},'zcode');
 assert.equal(values.at(-1).client.connectionMode,'official-remote');await values.at(-1).select('private-pipe');assert.deepEqual(selected,[['zcode','private-pipe']]);
 nav.pending.add('zcode');for(const render of nav.accountManagementRenderers)render();assert.equal(values.at(-1).disabled,true);
 nav.pending.clear();nav.clients[0].connectionMode='private-pipe';for(const render of nav.accountManagementRenderers)render();assert.equal(values.at(-1).client.connectionMode,'private-pipe');release();assert.equal(nav.accountManagementRenderers.size,0);
});
