"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const wsl='wsl:'+'a'.repeat(32),otherWsl='wsl:'+'b'.repeat(32),sid='12345678-1234-4234-9234-123456789012';
function fixture(){
 const choices=[],calls=[],switches=[],stored=new Map(),notices=[];
 const node=()=>({children:[],dataset:{},classList:{toggle(){}},querySelectorAll(){return [];},querySelector(){return null;},insertBefore(n){this.children.push(n);},setAttribute(k,v){this[k]=v;},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=[...v];}});
 const context=vm.createContext({document:{createElement:node,getElementById:()=>({hidden:false})},location:{search:''},URLSearchParams,localStorage:{setItem:(k,v)=>stored.set(k,v),getItem:k=>stored.get(k)},BridgeI18n:{t:v=>v},ClientLifecycle:{...require('../web/features/clients/client-lifecycle.js'),chooseDisable(client){choices.push({...client});return Promise.resolve(true);}}});
 context.window=context;
 context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;
  vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
 const Nav=vm.runInContext('ClientNavigation',context),nav=Object.create(Nav.prototype);
 const entries=[{id:'desktop',kind:'desktop',name:'Codex',enabled:true,configured:true,running:false,connected:false},{id:wsl,name:'WSL Codex · Ubuntu',kind:'wsl-cli',enabled:true,configured:true,canQuitDesktop:false},{id:otherWsl,name:'WSL Codex · Debian',kind:'wsl-cli',enabled:false,configured:true,canQuitDesktop:false}];
 Object.assign(nav,{clients:[{id:'codex',name:'Codex',enabled:true,configured:true,desktopEnabled:true,entries}],provider:'codex',pending:new Set(),revision:0,switchPreferences:node(),swipeSnapshots:new Map(),view:{provider:'codex',generation:0,choose(id){this.provider=id;return Promise.resolve();}},paint(){},syncViewClients(){},rememberSwipeList(){},notify:message=>notices.push(message),onApplicationChange:(from,to,options)=>switches.push([from,to,{...options}]),request:async(path,body)=>{calls.push({path,body});return {clients:nav.withClientEnabled(body.provider,body.enabled)};}});
 const all=root=>[root,...root.children.flatMap(all)];
 return {nav,choices,calls,switches,context,node,stored,notices,all};
}
test('WSL choices use the single Codex provider and keep the selected environment in the host',async()=>{
 const {nav,switches,context,stored}=fixture();await nav.choose(wsl);assert.equal(nav.provider,'codex');assert.equal(nav.view.provider,'codex');assert.deepEqual(switches,[['codex','codex',{activate:true,host:wsl,loadList:true}]]);
 assert.equal(vm.runInContext('isCodexClient("wsl:untrusted")',context),false);
 await nav.choose('claude');await nav.choose(otherWsl,{activate:false});assert.equal(nav.provider,'codex');assert.equal(stored.get('bridge-client'),'codex');assert.deepEqual(switches.at(-1),['claude','codex',{activate:false,host:otherWsl,loadList:true}]);
});
test('legacy WSL selections migrate to Codex while retaining their environment',()=>{
 const {nav,stored,context}=fixture();stored.set('bridge-client',wsl);nav.restoreClientSelection();assert.equal(nav.provider,'codex');assert.equal(nav.initialHost,wsl);assert.equal(stored.get('bridge-client'),'codex');
 context.location.search='?client='+encodeURIComponent(otherWsl);nav.restoreClientSelection();assert.equal(nav.provider,'codex');assert.equal(nav.initialHost,otherWsl);
});
test('WSL deep links use Codex and their original host, and disabled environments stay closed',async()=>{
 const {nav,notices}=fixture(),opened=[];await nav.openChatLink('#'+sid+'~'+encodeURIComponent(wsl),(...args)=>opened.push(args));
 assert.equal(nav.provider,'codex');assert.deepEqual(opened,[[sid,wsl]]);assert.equal(nav.chatTarget('#'+sid+'~wsl%3Auntrusted'),null);
 await nav.openChatLink('#'+sid+'~'+encodeURIComponent(otherWsl),(...args)=>opened.push(args));assert.equal(opened.length,1);assert.equal(notices.length,1);
 nav.clients=nav.withClientEnabled('codex',false);await nav.openChatLink('#'+sid,()=>opened.push('local'));assert.equal(opened.length,1);assert.equal(notices.length,2);
});
test('WSL environments and native Codex have separate switches and preserve aggregate availability',async()=>{
 const {nav,choices,calls}=fixture();await nav.toggle(wsl,false);assert.equal(choices.length,0);assert.equal(calls.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{path:'/api/clients',body:{provider:wsl,enabled:false,quitDesktop:false}});
 assert.equal(nav.clients[0].entries.find(row=>row.id===wsl).enabled,false);assert.equal(nav.clients[0].enabled,true);
 await nav.toggle(wsl,true);await nav.toggle('codex',false);assert.equal(nav.clients[0].desktopEnabled,false);assert.equal(nav.clients[0].enabled,true);assert.equal(nav.provider,'codex');assert.equal(choices.length,1);assert.equal(choices[0].running,false);
 await nav.toggle(wsl,false);assert.equal(nav.clients[0].enabled,false);assert.equal(nav.provider,null);
});
test('WSL-only Codex stays available in the single bottom application entry',()=>{
 const {nav}=fixture();nav.clients=nav.withClientEnabled('codex',false);nav.applyClients({clients:nav.clients});assert.equal(nav.provider,'codex');assert.deepEqual(Array.from(nav.visibleClients(),row=>row.id),['codex']);
});
test('app management groups native Codex and WSL environment controls under Codex',()=>{
 const {nav,node,all}=fixture();nav.manager={dialog:{open:true},list:node(),note:node()};nav.renderManager();
 assert.equal(nav.manager.list.children.length,1);const group=nav.manager.list.children[0];assert.equal(group['aria-label'],'Codex');
 const row=all(group).find(row=>row.dataset.clientId===wsl);assert.ok(row);const control=row.children.at(-1);assert.equal(control.children[0]['aria-checked'],'true');
 assert.equal(control.children.some(child=>child.textContent==='查看聊天'),true);assert.equal(control.children.some(child=>child.textContent==='关闭电脑 App…'),false);
 assert.equal(all(group).filter(row=>row.className==='client-management-row').length,3);
 assert.doesNotMatch(fs.readFileSync('web/index.html','utf8'),/id="codex-entries"/);
});
test('a scan response after logout cannot restore WSL environment state',async()=>{
 const {nav}=fixture();let finish;nav.request=()=>new Promise(resolve=>finish=resolve);const scanning=nav.scanWsl({disabled:false});nav.clear();finish({clients:[{id:'codex',enabled:true,entries:[{id:wsl,enabled:true}]}]});await scanning;assert.equal(nav.clients.length,0);assert.equal(nav.choosing,null);
});
test('native Codex unavailable state cannot borrow WSL configurability',async()=>{
 const {nav,calls}=fixture();nav.clients=nav.withClientEnabled('codex',false);Object.assign(nav.clients[0].entries[0],{configured:false,selectable:false});await nav.toggle('codex',true);assert.equal(calls.length,0);assert.equal(nav.clients[0].enabled,true);
});
test('native Codex quit reconciliation checks its entry while WSL stays enabled',async()=>{
 const {nav}=fixture();nav.clients=nav.withClientEnabled('codex',false);nav.clients[0].connected=true;nav.clients[0].running=true;nav.operationFailures=new Map([['codex',{action:'quit',uncertain:true,message:'pending'}]]);nav.request=async()=>({clients:nav.clients});
 await nav.refresh();assert.equal(nav.operationFailures.has('codex'),false);assert.equal(nav.clients[0].enabled,true);assert.equal(nav.provider,'codex');
});
test('a rejected WSL switch restores its environment and keeps native Codex state',async()=>{
 const {nav}=fixture();nav.request=async(_path,body)=>{if(body)throw Object.assign(Error('rejected'),{status:409});return {clients:nav.clients};};await assert.rejects(nav.toggle(wsl,false),/rejected/);
 assert.equal(nav.managementClients().find(row=>row.id===wsl).enabled,true);assert.equal(nav.clients[0].desktopEnabled,true);assert.equal(nav.provider,'codex');assert.equal(nav.pending.size,0);
});
test('a slow WSL deep link cannot reopen its chat after another Codex environment is selected',async()=>{
 const {nav}=fixture(),opened=[];let finish;nav.onApplicationChange=(_from,_to,{host})=>host===wsl?new Promise(resolve=>finish=resolve):Promise.resolve();
 const opening=nav.openChatLink('#'+sid+'~'+encodeURIComponent(wsl),(...args)=>opened.push(args));await nav.choose(otherWsl);finish();await opening;assert.equal(nav.provider,'codex');assert.deepEqual(opened,[]);
});
test('client-state updates report nested entry changes even when Codex remains selected',()=>{
 const {nav}=fixture(),snapshots=[];nav.onClientStates=clients=>snapshots.push({provider:nav.provider,clients});
 nav.applyClients({clients:nav.withClientEnabled(wsl,false)});
 assert.equal(snapshots.length,1);assert.equal(snapshots[0].provider,'codex');assert.equal(snapshots[0].clients[0].enabled,true);assert.equal(snapshots[0].clients[0].entries.find(row=>row.id===wsl).enabled,false);assert.equal(snapshots[0].clients[0].entries.find(row=>row.id==='desktop').enabled,true);
 nav.clients=nav.withClientEnabled(wsl,true);nav.applyClients({clients:nav.withClientEnabled('codex',false)});
 assert.equal(snapshots.length,2);assert.equal(snapshots[1].provider,'codex');assert.equal(snapshots[1].clients[0].desktopEnabled,false);assert.equal(snapshots[1].clients[0].entries.find(row=>row.id===wsl).enabled,true);
 nav.applyClients({clients:nav.withClientEnabled(wsl,false)});assert.equal(snapshots.at(-1).provider,null);assert.equal(snapshots.at(-1).clients[0].enabled,false);
});
