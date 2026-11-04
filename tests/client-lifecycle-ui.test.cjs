'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(language='zh'){
 const document={};
 function element(tag){
  const listeners={};return {tag,children:[],dataset:{},attributes:{},open:false,_text:'',
   get textContent(){return this._text+this.children.map(child=>child.textContent).join('');},set textContent(value){this._text=value;},
   append(...children){for(const child of children){child.parent=this;this.children.push(child);}},
   setAttribute(key,value){this.attributes[key]=value;},addEventListener(type,handler){listeners[type]=handler;},
   emit(type,event={}){listeners[type]?.(event);},showModal(){this.open=true;},close(){this.open=false;this.emit('close');},
   remove(){this.parent.children=this.parent.children.filter(child=>child!==this);},focus(){document.activeElement=this;}
  };
 }
 document.createElement=element;document.body=element('body');
 const context=vm.createContext({document,setTimeout,clearTimeout,AbortController,localStorage:{getItem:()=>language,setItem(){}}});
 for(const file of ['web/shared/i18n.js','web/features/clients/client-lifecycle.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
 return {context,document,choose:client=>vm.runInContext('ClientLifecycle',context).chooseDisable(client),initialize:client=>vm.runInContext('ClientLifecycle',context).chooseInitialize(client),dialog:()=>document.body.children[0]};
}
test('a real close dialog presents three distinct choices and defaults focus to cancel',async()=>{
 for(const expected of [false,true,null]){
  const ui=fixture(),result=ui.choose({id:'deepseek',name:'DSH'}),dialog=ui.dialog();
  const buttons=dialog.children.at(-1).children;assert.equal(dialog.tag,'dialog');assert.equal(dialog.open,true);assert.equal(buttons.length,3);
  assert.match(buttons[0].textContent,/仅停用手机接入.*保留电脑 App 和现有任务/);assert.match(buttons[1].textContent,/正常退出电脑 App.*所有任务结束/);assert.equal(buttons[2].textContent,'取消');
  assert.equal(ui.document.activeElement,buttons[2]);assert.equal(dialog.attributes['aria-labelledby'],dialog.children[0].id);
  buttons[expected===null?2:expected?1:0].onclick();assert.equal(await result,expected);assert.equal(ui.document.body.children.length,0);
 }
});
test('manual DSH binding only offers disabling access and cancellation',async()=>{
 const ui=fixture(),pending=ui.choose({id:'deepseek',name:'DSH',bindingMode:'manual',canForceQuit:false});
 const buttons=ui.dialog().children.at(-1).children;
 assert.equal(buttons.length,2);assert.match(buttons[0].textContent,/仅停用手机接入/);
 assert.equal(ui.document.activeElement,buttons[1]);buttons[0].onclick();assert.equal(await pending,false);
});

test('manual plugin controls use selected local paths and cancellation submits nothing',async()=>{
 const ui=fixture();vm.runInContext(fs.readFileSync('desktop/features/clients/desktop-sessions.js','utf8'),ui.context);
 const Panel=vm.runInContext('DesktopConnectionsPanel',ui.context),controller=Object.create(Panel.prototype),calls=[],messages=[];
 let selected='/local/plugin',confirmed=true;ui.context.window={confirm:()=>confirmed};
 Object.assign(controller,{clientsRevision:0,statusRevision:0,renderClients(){},renderScan(){},refreshClients:async()=>{},refresh:async()=>{},feedback:(...args)=>messages.push(args),api:{choose:async kind=>{calls.push(kind);return selected;},desktopSessions:async value=>{calls.push(JSON.parse(JSON.stringify(value)));return {bound:true,profile:'web',home:'/local/home',instanceId:'fixture',plugin:{version:'0.2.0'}};}}});
 await controller.manualDeepseek('bind-deepseek-plugin');assert.deepEqual(calls,['file',{action:'preview-deepseek-plugin',file:'/local/plugin'},{action:'bind-deepseek-plugin',file:'/local/plugin',instanceId:'fixture'}]);
 calls.length=0;confirmed=false;await controller.manualDeepseek('bind-deepseek-plugin');assert.deepEqual(calls,['file',{action:'preview-deepseek-plugin',file:'/local/plugin'}]);
 calls.length=0;selected=null;await controller.manualDeepseek('export-deepseek-plugin');assert.deepEqual(calls,['folder']);assert.equal(controller.busy,false);
});

test('background force quit requires an explicit capability and fresh choice with a translated warning',async()=>{
 for(const language of ['zh','en'])for(const id of ['codex','claude','deepseek']){
  const ui=fixture(language),client={id,name:id,canForceQuit:true};
  for(const expected of ['force',null]){
   const result=ui.choose(client),dialog=ui.dialog(),buttons=dialog.children.at(-1).children;
   assert.equal(buttons.length,4);assert.equal(ui.document.activeElement,buttons[3]);
   assert.equal(buttons[2].dataset.quitDesktop,'true');assert.equal(buttons[2].dataset.forceDesktop,'true');
   assert.match(buttons[1].textContent,language==='en'?/Quit the desktop app normally/:/正常退出电脑 App/);
   if(id==='codex')assert.match(buttons[1].textContent,language==='en'?/Codex's native menu.*all tasks finish.*unavailable.*locked.*confirmation on your computer/:/所有任务结束.*Codex 原生菜单正常退出.*锁屏时可能不可用.*退出确认.*电脑端处理/);
   assert.match(buttons[2].textContent,language==='en'?/Force quit App.*Unsaved content may be lost.*tasks may be interrupted.*locked/:/强制关闭 App.*可能丢失未保存内容或中断任务.*锁屏时也可使用/);
   if(language==='en')assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);
   if(expected==='force'){buttons[2].onclick();await Promise.resolve();const confirm=ui.dialog(),actions=confirm.children.at(-1).children;assert.notEqual(confirm,dialog);assert.match(confirm.textContent,language==='en'?/Running sessions may be interrupted.*unsaved content/:/可能中断正在运行的会话并丢失未保存内容/);assert.equal(ui.document.activeElement,actions[1]);actions[0].onclick();}else dialog.emit('cancel',{preventDefault(){}});
   assert.equal(await result,expected);
  }
 }
 for(const canForceQuit of [undefined,false,'true',1]){
  const ui=fixture(),result=ui.choose({id:'claude',canForceQuit}),dialog=ui.dialog();
  assert.equal(dialog.children.at(-1).children.length,3);assert.doesNotMatch(dialog.textContent,/后台强制结束/);dialog.close();assert.equal(await result,null);
 }
});

test('the second force confirmation can be cancelled without submitting the exit choice',async()=>{
 const ui=fixture(),result=ui.choose({id:'deepseek',canForceQuit:true});
 ui.dialog().children.at(-1).children[2].onclick();await Promise.resolve();
 ui.dialog().emit('cancel',{preventDefault(){}});assert.equal(await result,null);assert.equal(ui.document.body.children.length,0);
});

test('Claude normal and force exits require one translated unlock warning before desktop or Web mutations',async()=>{
 for(const surface of ['desktop','web'])for(const language of ['zh','en'])for(const force of [false,true])for(const choice of ['confirm','cancel','escape','close']){
  const ui=fixture(language),file=surface==='desktop'?'desktop/features/clients/desktop-sessions.js':'web/features/clients/client-navigation.js';
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),ui.context);
  const Class=vm.runInContext(surface==='desktop'?'DesktopConnectionsPanel':'ClientNavigation',ui.context),controller=Object.create(Class.prototype),client={id:'claude',name:'Claude Desktop',enabled:true,running:true,canForceQuit:true},calls=[];
  Object.assign(controller,{clients:[client],clientsRevision:0,statusRevision:0,revision:0,pending:new Set(),provider:'claude',renderClients(){},renderScan(){},paint(){},refreshClients:async()=>{},applyClients(value){this.clients=value.clients;},api:{desktopSessions:async body=>{calls.push(body);return {clients:[{...client,enabled:false,running:false}]};}},request:async(url,body)=>{calls.push(body);return {clients:[{...client,enabled:false,running:false}]};}});
  const pending=surface==='desktop'?controller.toggleClient('claude',false):controller.toggle('claude',false),first=ui.dialog();
  first.children.at(-1).children[force?2:1].onclick();await Promise.resolve();
  const confirm=ui.dialog(),buttons=confirm.children.at(-1).children;
  assert.notEqual(confirm,first);assert.equal(ui.document.body.children.length,1);assert.equal(calls.length,0);assert.equal(client.enabled,true);assert.equal(ui.document.activeElement,buttons[1]);
  assert.match(confirm.textContent,language==='en'?/After restarting.*initializing its connection requires the computer to be unlocked/:/重新启动.*初始化连接需要电脑处于解锁状态/);
  if(force)assert.match(confirm.textContent,language==='en'?/Running sessions may be interrupted and unsaved content may be lost/:/可能中断正在运行的会话并丢失未保存内容/);
  if(language==='en')assert.doesNotMatch(confirm.textContent,/[\u4e00-\u9fff]/);
  if(choice==='escape')confirm.emit('cancel',{preventDefault(){}});else if(choice==='close')confirm.close();else buttons[choice==='confirm'?0:1].onclick();
  await pending;assert.equal(ui.document.body.children.length,0);assert.equal(calls.length,choice==='confirm'?1:0);
  if(choice==='confirm')assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{...(surface==='desktop'?{action:'toggle-client'}:{}),provider:'claude',enabled:false,quitDesktop:true,...(force?{forceDesktop:true,forceConfirmed:true}:{})});
 }
});

test('disabling only Claude phone access does not ask for an unlock or desktop exit confirmation',async()=>{
 for(const language of ['zh','en']){
  const ui=fixture(language),result=ui.choose({id:'claude',name:'Claude',canForceQuit:true});
  ui.dialog().children.at(-1).children[0].onclick();assert.equal(await result,false);assert.equal(ui.document.body.children.length,0);
 }
});

test('an uncertain force quit needs a stopped app as well as disabled access before it is complete',()=>{
 const lifecycle=require('../web/features/clients/client-lifecycle.js');assert.equal(lifecycle.retryLabel('force'),'重试后台强制结束');
 for(const running of [true,false]){
  const failures=new Map([['claude',{action:'force',uncertain:true}]]);
  lifecycle.reconcile(failures,[{id:'claude',enabled:false,running}]);
  assert.equal(failures.has('claude'),running);if(running)assert.equal(failures.get('claude').uncertain,false);
 }
});

test('ZCode initialization uses only private pipes and keeps restart consent separate',async()=>{
 for(const surface of ['desktop','web'])for(const scenario of ['cancel','cold','restart','keep-running']){
  const ui=fixture('en'),desktop=surface==='desktop',file=desktop?'desktop/features/clients/desktop-sessions.js':'web/features/clients/client-navigation.js';
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),ui.context);
  const Controller=vm.runInContext(desktop?'DesktopConnectionsPanel':'ClientNavigation',ui.context),panel=Object.create(Controller.prototype),writes=[];
  const client={id:'zcode',name:'ZCode',enabled:true,installed:true,configured:true,running:scenario!=='cold',confirmTaskState:true,
    setupStatus:'needs-initialization',canInitializeDesktop:true,pipeSupported:true,compatibilitySupported:true};
  const save=async body=>{writes.push(JSON.parse(JSON.stringify(body)));return {clients:[client]};};
  Object.assign(panel,{clients:[client],clientsRevision:0,statusRevision:0,revision:0,pending:new Set(),provider:'zcode',
    renderClients(){},renderScan(){},paint(){},refreshClients:async()=>{},applyClients(){},api:{desktopSessions:save},request:(url,body)=>save(body)});
  const result=desktop?panel.toggleClient('zcode',true,{initializeDesktop:true}):panel.initialize('zcode');
  const first=ui.dialog();assert.ok(first);assert.match(first.textContent,/private local pipe/);assert.doesNotMatch(first.textContent,/17742|compatibility|Claude|[一-鿿]/);assert.equal(writes.length,0);
  assert.equal(first.children.at(-1).children.length,2);first.children.at(-1).children[scenario==='cancel'?1:0].onclick();await Promise.resolve();
  if(['restart','keep-running'].includes(scenario)){
   const second=ui.dialog();assert.match(second.textContent,/Some tasks could not be checked/);assert.equal(writes.length,0);
   second.children.at(-1).children[scenario==='restart'?0:1].onclick();
  }
  await result;
  assert.deepEqual(writes,['cold','restart'].includes(scenario)?[{...(desktop?{action:'toggle-client'}:{}),provider:'zcode',enabled:true,initializeDesktop:true,connectionMode:'private-pipe',...(scenario==='restart'?{acknowledgeUnavailable:true}:{})}]:[]);
 }
 const lifecycle=require('../web/features/clients/client-lifecycle.js');
 assert.equal(lifecycle.connection({id:'zcode',canInitializeDesktop:true,compatibilitySupported:true}).canInitialize,false);
 const ready={id:'zcode',pipeSupported:true,pipeReady:true,enabled:true,canInitializeDesktop:true,retryable:true};
 assert.equal(lifecycle.connection(ready).canInitialize,false);assert.equal(lifecycle.connection(ready).label,'已连接');assert.equal(lifecycle.connection(ready).retryable,false);
 const failed=new Map([['zcode',{action:'initialize',uncertain:true,connectionMode:'private-pipe'}]]);lifecycle.reconcile(failed,[ready]);assert.equal(failed.size,0);
});

test('shared connection chooser shows saved methods and never activates an unavailable option',async()=>{
 for(const language of ['zh','en'])for(const selected of ['private-pipe','official-remote',null]){
  const ui=fixture(language),lifecycle=vm.runInContext('ClientLifecycle',ui.context);
  const client={id:'zcode',name:'ZCode',connectionMode:'private-pipe',connectionModes:[
   {id:'private-pipe',label:'本机连接',configured:true,available:true},
   {id:'official-remote',label:'官方远控',configured:true,available:true},
   {id:'future',label:'Other',configured:false,available:true}]};
  const result=lifecycle.chooseConnectionMode(client),dialog=ui.dialog(),buttons=dialog.children.at(-1).children;
  assert.equal(buttons.length,4);assert.equal(buttons[0].attributes['aria-pressed'],'true');assert.equal(buttons[2].disabled,true);assert.equal(ui.document.activeElement,buttons[3]);
  assert.match(dialog.textContent,language==='en'?/Switching does not automatically quit/:/切换不会自动退出/);if(language==='en')assert.doesNotMatch(dialog.textContent,/[一-鿿]/);
  buttons[2].onclick();assert.equal(dialog.open,true);
  buttons[selected==='private-pipe'?0:selected==='official-remote'?1:3].onclick();assert.equal(await result,selected);
 }
});

test('desktop and remote mode selection submit the same explicit connectionMode without restarting the app',async()=>{
 for(const surface of ['desktop','web'])for(const choice of ['official-remote',null]){
  const ui=fixture(),desktop=surface==='desktop';vm.runInContext(fs.readFileSync(desktop?'desktop/features/clients/desktop-sessions.js':'web/features/clients/client-navigation.js','utf8'),ui.context);
  const Controller=vm.runInContext(desktop?'DesktopConnectionsPanel':'ClientNavigation',ui.context),panel=Object.create(Controller.prototype),calls=[];
  const client={id:'zcode',name:'ZCode',enabled:true,configured:true,running:true,connectionMode:'private-pipe',connectionModes:[{id:'private-pipe',label:'本机连接',configured:true,available:true},{id:'official-remote',label:'官方远控',configured:true,available:true}]};
  const save=async body=>{calls.push(JSON.parse(JSON.stringify(body)));return {clients:[{...client,connectionMode:body.connectionMode}]};};
  Object.assign(panel,{clients:[client],clientsRevision:0,statusRevision:0,revision:0,pending:new Set(),provider:'zcode',renderClients(){},renderScan(){},paint(){},notify(){},refreshClients:async()=>{},applyClients(data){this.clients=data.clients;},api:{desktopSessions:save},request:(url,body)=>save(body)});
  const result=panel.selectConnectionMode('zcode'),buttons=ui.dialog().children.at(-1).children;assert.equal(calls.length,0);buttons[choice?1:2].onclick();await result;
  assert.deepEqual(calls,choice?[{...(desktop?{action:'toggle-client'}:{}),provider:'zcode',enabled:true,connectionMode:'official-remote'}]:[]);
 }
});

test('ready session access does not show retry and unknown switches reconcile only the selected mode',()=>{
 const lifecycle=require('../web/features/clients/client-lifecycle.js'),client={id:'zcode',enabled:true,running:true,setupStatus:'session-access',retryable:true,connectionMode:'official-remote',connectionModes:[{id:'official-remote',label:'官方远控'}]};
 assert.equal(lifecycle.connection(client).label,'已连接');assert.equal(lifecycle.connection(client).modeLabel,'官方远控');assert.equal(lifecycle.connection(client).retryable,false);
 for(const connectionMode of ['official-remote','private-pipe']){
  const failures=new Map([['zcode',{action:'enable',uncertain:true,connectionMode}]]);lifecycle.reconcile(failures,[client]);assert.equal(failures.has('zcode'),connectionMode!=='official-remote');
 }
});

test('initialization distinguishes background attempts from interactive foreground setup and defaults to cancel',async()=>{
 for(const language of ['zh','en'])for(const accepted of [false,true])for(const initializationMode of ['background','foreground']){
  const ui=fixture(language),result=ui.initialize({id:'claude',name:'Claude',initializationMode}),dialog=ui.dialog(),buttons=dialog.children.at(-1).children;
  const pattern=initializationMode==='background'?(language==='en'?/Windows.*background initialization.*keyboard focus.*foreground setup/:/Windows.*后台初始化.*键盘焦点.*前台引导/):(language==='en'?/Claude and its developer tools.*keyboard focus.*screen saver or lock screen/:/打开 Claude 和开发者工具.*键盘焦点.*屏保或锁屏/);
  assert.equal(buttons.length,2);assert.equal(ui.document.activeElement,buttons[1]);assert.match(dialog.textContent,pattern);if(language==='en')assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);
  buttons[accepted?0:1].onclick();assert.equal(await result,accepted);
 }
 for(const escape of [false,true]){const ui=fixture(),result=ui.initialize({id:'claude'}),dialog=ui.dialog();if(escape)dialog.emit('cancel',{preventDefault(){}});else dialog.close();assert.equal(await result,false);}
});

test('connection labels distinguish startup, required action, failure and actual connectivity',()=>{
 const {connection}=require('../web/features/clients/client-lifecycle.js'),base={enabled:true,configured:true,installed:true,connected:false};
 for(const running of [false,true])for(const configured of [false,true]){
  assert.equal(connection({...base,running,configured,connectionState:'starting'}).label,'正在启动应用…');assert.equal(connection({...base,running,configured,connectionState:'connecting'}).label,'正在连接，请稍候…');
 }
 for(const [setupStatus,label] of [['needs-initialization','需要初始化连接'],['needs-permission','等待授权'],['failed','连接失败'],['history-only','本机历史（只读）']])assert.equal(connection({...base,running:false,setupStatus}).label,label);
 assert.equal(connection({...base,connected:true,connectionState:'connecting'}).label,'已连接');assert.equal(connection({...base,connected:true,connectionState:'connecting'}).waiting,false);
 assert.equal(connection({...base,connectionState:'timeout',retryable:true}).retryable,true);assert.equal(connection({...base,connectionState:'connecting',retryable:true}).retryable,false);
 assert.equal(connection({...base,setupStatus:'recovery-required',connectionState:'connecting',backgroundRunning:true,mainRunning:false}).label,'后台运行，桌面未打开');
 assert.equal(connection({...base,pendingEnable:true,reason:'请手动连接 Claude'}).reason,'正在等待客户端连接，完成后会自动显示聊天。');
 const restarting=connection({...base,pendingEnable:true,setupStatus:'failed',connectionState:'error'});
 assert.equal(restarting.label,'正在启动应用…');assert.equal(restarting.error,false);
 for(const setupStatus of ['needs-initialization','needs-retry','needs-developer-mode','needs-trust','needs-permission','failed'])assert.equal(connection({...base,id:'claude',setupStatus}).canInitialize,true);
 const prepared={...base,id:'claude',setupStatus:'needs-initialization',reconnectSupported:true,canReconnect:true};
 assert.equal(connection(prepared).canReconnect,true);assert.equal(connection(prepared).canInitialize,true);
 assert.equal(connection({...prepared,connectionState:'connecting'}).canReconnect,false);
 assert.equal(connection({...prepared,setupStatus:'owner-conflict'}).canReconnect,true);
 assert.equal(connection({...prepared,canReconnect:false}).canReconnect,false);
 for(const client of [{...base,id:'deepseek',setupStatus:'needs-initialization'},{...base,id:'claude',connected:true,setupStatus:'needs-initialization'},{...base,id:'claude',connectionState:'connecting',setupStatus:'needs-initialization'},{...base,id:'claude',setupStatus:'unsupported'}])assert.equal(connection(client).canInitialize,false);
 assert.equal(connection({...base,setupStatus:'restart-required',retryable:true}).retryable,false);
});

test('lock and desktop-unavailable states stop waiting without disconnecting an existing client',()=>{
 const {connection,sessionNotice}=require('../web/features/clients/client-lifecycle.js');
 for(const [setupStatus,label] of [['needs-unlock','电脑已锁定'],['needs-desktop','电脑桌面暂不可用']]){
  const client={id:'claude',enabled:true,installed:true,connected:false,initializationMode:'background',setupStatus,connectionState:'needs-initialization',reason:'Windows 已锁定；请解锁电脑后重试初始化连接'};const state=connection(client);
  assert.equal(state.label,label);assert.equal(state.waiting,false);assert.equal(state.canInitialize,true);assert.equal(connection({...client,connected:true}).label,'已连接');assert.equal(connection({...client,connected:true}).canInitialize,false);
 }
 assert.match(sessionNotice({state:'locked',interactive:false}),/后台服务继续运行/);assert.equal(sessionNotice({state:'unlocked',interactive:true}),'');
});

test('macOS foreground setup keeps explicit desktop waits active until it can resume',()=>{
 const {connection}=require('../web/features/clients/client-lifecycle.js');
 for(const [setupStatus,label] of [['needs-screen-saver','等待退出屏保'],['needs-unlock','等待解锁'],['needs-desktop','等待桌面恢复']]){
  const client={id:'claude',enabled:true,installed:true,connected:false,initializationMode:'foreground',connectionState:'connecting',setupStatus},state=connection(client);
  assert.equal(state.label,label);assert.equal(state.waiting,true);assert.equal(state.canInitialize,false);assert.equal(state.retryable,false);
  assert.equal(connection({...client,connected:true}).label,'已连接');
 }
});

test('bounded lifecycle requests abort only their wait and ignore a late result',async()=>{
 const lifecycle=require('../web/features/clients/client-lifecycle.js');let finish,signal;
 const pending=lifecycle.request(value=>{signal=value;return new Promise(resolve=>finish=resolve);},{timeout:5});await assert.rejects(pending,error=>error.uncertain===true&&/尚未确认/.test(error.message));assert.equal(signal.aborted,true);finish('late');await Promise.resolve();await assert.rejects(pending,/尚未确认/);
 for(const status of [400,401,409,423])assert.equal(lifecycle.failure('quit',Object.assign(Error('rejected'),{status})).uncertain,false);
 for(const error of [Error('offline'),new SyntaxError('bad response'),Object.assign(Error('server'),{status:500})])assert.equal(lifecycle.failure('quit',error).uncertain,true);
 assert.equal(lifecycle.failure('quit',Error('本机操作超时，请刷新状态确认结果'),'desktop').uncertain,true);
});
test('Escape and other dialog close events always cancel the desktop exit choice',async()=>{
 for(const escape of [true,false]){
  const ui=fixture(),result=ui.choose({id:'deepseek',name:'DSH'}),dialog=ui.dialog();let prevented=false;
  if(escape)dialog.emit('cancel',{preventDefault(){prevented=true;}});else dialog.close();
  assert.equal(await result,null);assert.equal(prevented,escape);assert.equal(ui.document.body.children.length,0);
 }
});
test('the shared close dialog is fully translated in English and preserves client names',async()=>{
 const ui=fixture('en'),result=ui.choose({id:'claude',name:'Claude Test'}),dialog=ui.dialog();assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);assert.match(dialog.textContent,/Claude Test/);assert.match(dialog.textContent,/Disable phone access only/);assert.match(dialog.textContent,/Quit the desktop app normally/);dialog.close();await result;
});
test('both UIs load the shared dialog and desktop packages include it',()=>{
 const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
 for(const file of ['web/index.html','desktop/index.html'])assert.match(read(file),/<script[^>]+client-lifecycle\.js/);
 assert.ok(JSON.parse(read('package.json')).build.files.includes('web/features/clients/client-lifecycle.js'));
 assert.match(read('bridge/api/assets.py'),/"\/client-lifecycle\.js"/);
});


test('Claude explains its native quit menu while other clients retain the idle requirement',async()=>{
 for(const language of ['zh','en'])for(const id of ['claude','deepseek','codex']){
  const ui=fixture(language),result=ui.choose({id,name:id}),dialog=ui.dialog(),buttons=dialog.children.at(-1).children,note=buttons[1].children[1].textContent;
  assert.equal(buttons.length,3);assert.doesNotMatch(dialog.textContent,/强制退出|Force quit/i);
  if(id==='claude'){
   assert.match(note,language==='en'?/Quit normally through Claude's native menu/:/原生菜单正常退出/);assert.doesNotMatch(note,/may appear briefly|短暂出现/);
   assert.match(note,language==='en'?/task or save confirmation on your computer/:/任务或保存确认.*电脑端处理/);
   assert.match(note,language==='en'?/next time.*reconnect/:/下次开启时会尝试重新连接/);
   assert.doesNotMatch(note,/only after all tasks finish|仅在所有任务结束/i);
  }else assert.equal(note,language==='en'?'Quit only after all tasks finish and no approvals are pending.':'仅在所有任务结束且没有待确认操作时退出。');
  if(language==='en')assert.doesNotMatch(dialog.textContent,/[\u4e00-\u9fff]/);
  dialog.close();assert.equal(await result,null);
 }
});


function toggleFixture(ui,surface,client,calls){
  const file=surface==='desktop'?'desktop/features/clients/desktop-sessions.js':'web/features/clients/client-navigation.js';
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),ui.context);
  const Class=vm.runInContext(surface==='desktop'?'DesktopConnectionsPanel':'ClientNavigation',ui.context),controller=Object.create(Class.prototype);
  Object.assign(controller,{clients:[client],clientsRevision:0,statusRevision:0,revision:0,pending:new Set(),provider:client.id,renderClients(){},renderScan(){},paint(){},refreshClients:async()=>{},applyClients(value){this.clients=value.clients;},api:{desktopSessions:async body=>{calls.push(body);return {clients:[{...client,enabled:false,running:false}]};}},request:async(url,body)=>{calls.push(body);return {clients:[{...client,enabled:false,running:false}]};}});
  return surface==='desktop'?controller.toggleClient(client.id,false):controller.toggle(client.id,false);
}

test('failed Codex rebuild exposes a cancellable normal-exit risk confirmation on both surfaces',async()=>{
 for(const surface of ['web','desktop'])for(const language of ['zh','en'])for(const accepted of [false,true]){
  const ui=fixture(language),client={id:'codex',name:'Codex',enabled:true,running:true,desktopRestartRecommended:true},calls=[];
  const pending=toggleFixture(ui,surface,client,calls);
  ui.dialog().children.at(-1).children[1].onclick();await Promise.resolve();
  const confirmation=ui.dialog(),buttons=confirmation.children.at(-1).children;
  assert.equal(calls.length,0);assert.equal(ui.document.activeElement,buttons[1]);
  assert.match(confirmation.textContent,language==='zh'?/正常退出仍可能中断.*已知运行任务会阻止退出/:/Normal exit may interrupt.*known active tasks will still block exit/);
  if(language==='en')assert.doesNotMatch(confirmation.textContent,/[\u4e00-\u9fff]/);
  buttons[accepted?0:1].onclick();await pending;
  assert.equal(calls.length,accepted?1:0);
  if(accepted){assert.equal(calls[0].quitDesktop,true);assert.equal(calls[0].acknowledgeUnavailable,true);assert.equal(calls[0].forceDesktop,undefined);}
 }
});


test('ZCode phone disconnect, unknown-state quit and force are independent choices on both surfaces',async()=>{
 for(const surface of ['desktop','web'])for(const selection of ['disconnect','cancel','quit','force']){
  const ui=fixture('en'),client={id:'zcode',name:'ZCode',enabled:true,running:true,canForceQuit:true,confirmTaskState:true};
  const calls=[],pending=toggleFixture(ui,surface,client,calls);
  ui.dialog().children.at(-1).children[selection==='disconnect'?0:selection==='force'?2:1].onclick();await Promise.resolve();
  if(selection!=='disconnect'){
   const confirm=ui.dialog(),buttons=confirm.children.at(-1).children;
   assert.equal(calls.length,0);assert.equal(ui.document.activeElement,buttons[1]);
   assert.match(confirm.textContent,selection==='force'?/Force quit/i:/running task or pending approval/i);
   buttons[selection==='cancel'?1:0].onclick();
  }
  await pending;assert.equal(calls.length,selection==='cancel'?0:1);
  if(calls.length){assert.equal(calls[0].quitDesktop,selection!=='disconnect');assert.equal(!!calls[0].acknowledgeUnavailable,selection==='quit');assert.equal(!!calls[0].forceConfirmed,selection==='force');}
 }
});
