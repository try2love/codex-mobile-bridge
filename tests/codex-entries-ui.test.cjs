"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const wsl='wsl:'+'a'.repeat(32),sid='12345678-1234-4234-9234-123456789012';
function fixture(){
 const choices=[],calls=[],switches=[],stored=new Map();
 const node=()=>({children:[],dataset:{},classList:{toggle(){}},setAttribute(k,v){this[k]=v;},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=[...v];}});
 const context=vm.createContext({document:{createElement:node,getElementById:()=>null},localStorage:{setItem:(k,v)=>stored.set(k,v),getItem:k=>stored.get(k)},BridgeI18n:{t:v=>v},ClientLifecycle:{...require('../web/features/clients/client-lifecycle.js'),chooseDisable(){choices.push(true);return Promise.resolve(true);}}});
 vm.runInContext(fs.readFileSync('web/features/clients/client-navigation.js','utf8'),context);
 const Nav=vm.runInContext('ClientNavigation',context),nav=Object.create(Nav.prototype);
 Object.assign(nav,{clients:[{id:'codex',name:'Codex',enabled:true,configured:true},{id:wsl,name:'WSL Codex · Ubuntu',kind:'wsl-cli',enabled:true,configured:true,canQuitDesktop:false}],provider:'codex',pending:new Set(),revision:0,switchPreferences:node(),swipeSnapshots:new Map(),view:{provider:'codex',generation:0,choose(id){this.provider=id;return Promise.resolve();}},paint(){},syncViewClients(){},rememberSwipeList(){},notify(){},onApplicationChange:(from,to)=>switches.push([from,to]),request:async(path,body)=>{calls.push({path,body});return {clients:nav.clients.map(row=>row.id===body.provider?{...row,enabled:body.enabled}:row)};}});
 return {nav,choices,calls,switches,context,node};
}
test('WSL is an independent application while reusing the Codex conversation renderer',async()=>{
 const {nav,switches,context}=fixture();await nav.choose(wsl);assert.equal(nav.provider,wsl);assert.equal(nav.view.provider,'codex');assert.deepEqual(switches,[['codex',wsl]]);
 await nav.choose('codex');assert.deepEqual(switches.at(-1),[wsl,'codex']);assert.equal(vm.runInContext('isCodexClient("wsl:untrusted")',context),false);
});
test('WSL deep links select their original app and original host',async()=>{
 const {nav}=fixture(),opened=[];await nav.openChatLink('#'+sid+'~'+encodeURIComponent(wsl),(...args)=>opened.push(args));
 assert.equal(nav.provider,wsl);assert.deepEqual(opened,[[sid,wsl]]);
});
test('WSL app switch uses common management endpoint and cannot offer desktop quit',async()=>{
 const {nav,choices,calls}=fixture();await nav.toggle(wsl,false);assert.equal(choices.length,0);assert.equal(calls.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{path:'/api/clients',body:{provider:wsl,enabled:false,quitDesktop:false}});
 assert.equal(nav.clients.find(row=>row.id===wsl).enabled,false);assert.equal(nav.clients.find(row=>row.id==='codex').enabled,true);
});
test('WSL app management renders an ordinary enable switch and chat action',()=>{
 const {nav,node}=fixture();nav.manager={dialog:{open:true},list:node(),note:node()};nav.renderManager();
 const row=nav.manager.list.children.find(row=>row.dataset.clientId===wsl);assert.ok(row);
 const control=row.children.at(-1);assert.equal(control.children[0]['aria-checked'],'true');
 assert.equal(control.children.some(child=>child.textContent==='查看聊天'),true);
 assert.equal(control.children.some(child=>child.textContent==='关闭电脑 App…'),false);
 assert.doesNotMatch(fs.readFileSync('web/index.html','utf8'),/id="codex-entries"/);
});

test('a scan response after logout cannot restore WSL application state',async()=>{
 const {nav}=fixture();let finish;nav.request=()=>new Promise(resolve=>finish=resolve);const scanning=nav.scanWsl({disabled:false});nav.clear();finish({clients:[{id:wsl,enabled:true}]});await scanning;assert.equal(nav.clients.length,0);assert.equal(nav.choosing,null);
});
