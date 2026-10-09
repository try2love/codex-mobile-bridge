'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(){
  const element=tag=>({tagName:tag,children:[],dataset:{},attributes:{},append(...nodes){this.children.push(...nodes);},replaceChildren(){this.children=[];},setAttribute(name,value){this.attributes[name]=String(value);}});
  let mobile=false;const document={documentElement:{classList:{contains:()=>mobile}},getElementById:()=>({hidden:false}),createElement:element},context=vm.createContext({document,localStorage:{setItem(){}},BridgeI18n:{t:text=>text},Event:class{},window:{}});
  vm.runInContext(fs.readFileSync('web/client-navigation.js','utf8'),context);
  const C=vm.runInContext('ClientNavigation',context),nav=Object.create(C.prototype),painted=[];
  Object.assign(nav,{clients:[{id:'codex',enabled:true,configured:true},{id:'claude',enabled:true,configured:true},{id:'deepseek',enabled:true,configured:true}],provider:'claude',revision:0,pending:new Set(),notify(){},paint(){painted.push(this.clients.filter(c=>c.enabled).map(c=>c.id));},view:{provider:'claude',choose(id){this.provider=id;return Promise.resolve();}}});
  return {nav,painted,document,mobile:value=>mobile=value};
}
test('changing provider paints the shell before its slow read resolves',async()=>{
  const {nav,painted}=fixture();let resolve;
  nav.view.choose=()=>new Promise(done=>resolve=done);
  const pending=nav.choose('deepseek');assert.equal(nav.provider,'deepseek');assert.equal(painted.length,1);
  resolve();await pending;
});
test('disabling the active client updates the switcher immediately and stale polling cannot restore it',async()=>{
  const {nav}=fixture();let readDone,writeDone;
  nav.request=(path,body)=>new Promise(resolve=>body?writeDone=resolve:readDone=resolve);
  const read=nav.refresh(),write=nav.toggle('claude',false);
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
  const {nav}=fixture();let finish;nav.view.generation=0;nav.view.choose=function(id){this.provider=id;this.generation++;return id==='claude'?new Promise(resolve=>finish=resolve):Promise.resolve();};nav.view.open=async()=>{throw Error('stale notification opened');};
  const opening=nav.openChatLink('#chat~desktop%3Aclaude',()=>{});await nav.choose('deepseek');finish();await opening;assert.equal(nav.provider,'deepseek');
});

test('client preferences and observed runtime remain separate during a pending operation',async()=>{
 const {nav}=fixture();nav.gatewayRunning=true;const client=nav.clients.find(row=>row.id==='claude');client.connected=true;client.running=true;let finish;nav.request=()=>new Promise(resolve=>finish=resolve);
 const pending=nav.toggle('claude',false);const optimistic=nav.clients.find(row=>row.id==='claude');assert.equal(nav.managementState(optimistic),'关闭中…');assert.equal(nav.runtimeState(optimistic),'已连接');await nav.toggle('deepseek',false);assert.equal(nav.pending.size,1);
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
test('Claude reconnect preserves preferences, blocks concurrent actions, and ignores stale polling',async()=>{
 const {nav}=fixture(),calls=[],notices=[];nav.gatewayRunning=true;Object.assign(nav.clients[1],{reconnectSupported:true,canReconnect:true,configured:false,running:true});nav.notify=text=>notices.push(text);let readDone,reconnectDone;nav.request=(url,body)=>{calls.push({url,body});return new Promise(resolve=>body?reconnectDone=resolve:readDone=resolve);};
 const read=nav.refresh(),pending=nav.reconnect('claude');assert.equal(nav.managementState(nav.clients[1]),'正在连接…');assert.equal(nav.clients[1].enabled,true);assert.equal(nav.provider,'claude');await nav.toggle('claude',false);await nav.reconnect('claude');assert.equal(calls.length,2);assert.equal(calls[1].url,'/api/clients/claude/reconnect');assert.deepEqual(Object.keys(calls[1].body),[]);
 readDone({clients:[{id:'codex',enabled:true}]});await read;assert.equal(nav.clients.length,3);reconnectDone({clients:nav.clients.map(row=>row.id==='claude'?{...row,setupStatus:'connecting'}:row),gatewayRunning:true});await pending;assert.equal(nav.runtimeState(nav.clients[1]),'正在连接…');assert.equal(nav.provider,'claude');assert.deepEqual(notices,['已发起重新连接']);assert.equal(nav.pending.size,0);assert.equal(nav.reconnecting,null);
});
test('Claude reconnect only uses the advertised capability and reports actual connection separately',async()=>{
 const {nav}=fixture(),calls=[],notices=[];let refreshed=0;nav.view.refresh=async()=>refreshed++;nav.notify=text=>notices.push(text);nav.request=async(url,body)=>{calls.push({url,body});return {clients:nav.clients.map(row=>row.id==='claude'?{...row,connected:true,canReconnect:false}:row)};};
 for(const state of [{reconnectSupported:false,canReconnect:true},{reconnectSupported:true,canReconnect:false},{reconnectSupported:true,canReconnect:true,setupStatus:'connecting'}]){Object.assign(nav.clients[1],state);await nav.reconnect('claude');}await nav.reconnect('deepseek');assert.equal(calls.length,0);
 Object.assign(nav.clients[1],{reconnectSupported:true,canReconnect:true,setupStatus:'failed'});await nav.reconnect('claude');assert.equal(calls.length,1);assert.deepEqual(notices,['Claude 已连接']);assert.equal(refreshed,1);
 nav.clients[1].canReconnect=true;await nav.choose('deepseek');await nav.reconnect('claude');assert.equal(nav.provider,'deepseek');assert.equal(refreshed,1);
});
test('reconnect control is shared by web/mobile, disabled while unavailable, and displays failures',async()=>{
 for(const mobile of [false,true]){const ui=fixture(),{nav,document}=ui,notices=[];ui.mobile(mobile);nav.icon=()=>document.createElement('span');nav.manager={dialog:{open:true},list:document.createElement('div'),note:document.createElement('p')};nav.notify=text=>notices.push(text);nav.request=async()=>{throw Error('请在电脑端完成连接');};
  const reconnect=()=>nav.manager.list.children[1].children[2].children.find(node=>node.className==='plain client-reconnect');nav.renderManager();assert.equal(reconnect(),undefined);
  Object.assign(nav.clients[1],{reconnectSupported:true,canReconnect:false});nav.renderManager();assert.equal(reconnect().disabled,true);Object.assign(nav.clients[1],{canReconnect:true});nav.renderManager();assert.equal(reconnect().disabled,false);await reconnect().onclick();assert.deepEqual(notices,['请在电脑端完成连接']);assert.equal(nav.pending.size,0);assert.equal(nav.reconnecting,null);assert.equal(nav.clients[1].enabled,true);
  nav.clients[1].setupStatus='connecting';nav.renderManager();assert.equal(reconnect().textContent,'正在连接…');assert.equal(reconnect().disabled,true);
 }
});
