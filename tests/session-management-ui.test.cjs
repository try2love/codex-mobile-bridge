'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
const code=source.slice(source.indexOf('function renderList(){'),source.indexOf('async function loadList('));
function fixture(){
 const nodes=new Map(),timers=new Map(),requests=[],dialogs=[],copies=[];let timer=0;
 const node=(tag='',cls='',textContent='')=>({tag,cls,textContent,dataset:{},classList:{add(){}},children:[],events:{},scrollTop:0,isConnected:true,append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},addEventListener(name,run){this.events[name]=run;},setAttribute(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.events.close?.();},remove(){this.isConnected=false;}});
 const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};$('list-mode').value='recent';
 const context=vm.createContext({CliConnectionsPanel:class{},$,el:node,window:{BridgeSessionLinks:{text:identity=>'codex://threads/'+identity.id}},document:{body:{append:box=>dialogs.push(box)}},SessionMenu:class{constructor(){}bind(button,context){button.menuContext=context;}},
   BridgeClipboard:{copyWithFeedback:async(get,item)=>{if(item.disabled)return;copies.push(get());}},BridgeI18n:{locale:()=> 'en-US'},t:s=>s,localStorage:{getItem(){return null;},setItem(){}},
   listIdentity:'gateway',csrf:'token',clientNavigation:{provider:'codex',clients:[]},state:null,listRows:[],currentId:null,currentHost:'local',listHasMore:false,listRenderStamp:'',listComplete:true,listReadFinished:true,availableSources:[],
   renderActivity(){},sessionAccess:v=>({canConfigure:true,canEditMessages:true,...v}),sourceLabel:()=> 'APP',dateText:()=>'',toast(){},pinRow:row=>row,sessionPins:{value:(_identity,pinned)=>!!pinned,set(identity,pinned){requests.push({url:`/${identity.host}/${identity.id}/pin`,body:{pinned}});}},desktopSessions:{refresh:async()=>{}},openChat:async()=>{},
   setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),uuid:()=> 'request-id',
   api:async(url,body)=>{requests.push({url,body});return {status:'confirmed'};},sessionUrl:(id,action,host)=>`/${host}/${id}/${action}`,loadList:async()=>{}});
 vm.runInContext(code,context);
 const row=(id,recency,pinned=false)=>({id,host:'local',title:id,projectKey:'p',projectName:'Project',hostLabel:'Local',recency,bridgePinned:pinned,access:{}});
 return {context,$,node,row,requests,dialogs,copies,fireTimers(){const work=[...timers.values()];timers.clear();work.forEach(fn=>fn());}};
}
test('native organization preserves host and expected assignment and rejects stale authentication',async()=>{
 const ui=fixture(),chat={...ui.row('native',1),host:'remote'};
 const context=ui.context.sessionMenuContext(chat);
 await context.organization({kind:'project',targetId:'project-b',expectedId:'project-a'});
 assert.equal(ui.requests.at(-1).url,'/remote/native/organization');
 assert.equal(ui.requests.at(-1).body.expectedId,'project-a');
 ui.context.csrf='replacement';
 await assert.rejects(()=>context.organization(),/连接或账号/);
 assert.equal(ui.requests.length,1);
});
test('recent sections sort pinned and unpinned independently; project grouping ignores pins',()=>{
 const ui=fixture();ui.context.listRows=[ui.row('recent',30),ui.row('old-pin',1,true),ui.row('new-pin',20,true),ui.row('older',2)];
 ui.context.renderList();const rows=ui.$('sessions').children;
 assert.deepEqual(rows.map(n=>n.dataset.id||n.textContent),['置顶聊天','new-pin','old-pin','最近聊天','recent','older']);
 ui.$('list-mode').value='project';ui.context.renderList();
 assert.deepEqual(ui.$('sessions').children[0].children.slice(1).map(n=>n.dataset.id),['recent','new-pin','older','old-pin']);
});
test('shared context preserves original host for pin, rename and archive',async()=>{
 const ui=fixture(),chat={...ui.row('a',1),host:'remote'};
 const menu=ui.context.sessionMenuContext(chat);
 await menu.pin(true);await menu.rename('New');await menu.archive();
 assert.equal(ui.requests[0].url,'/remote/a/pin');assert.equal(ui.requests[0].body.pinned,true);
 assert.equal(ui.requests[1].url,'/remote/a/rename');assert.equal(ui.requests[1].body.title,'New');
 assert.equal(ui.requests[2].url,'/remote/a/archive');assert.equal(ui.requests[2].body.disconnect,true);
 assert.equal(menu.nativeOrganization,true);
});
test('project groups put unassigned hosts last and preserve activity order within each group',()=>{
 const ui=fixture();ui.$('list-mode').value='project';
 ui.context.listRows=[
  {...ui.row('unassigned-new',100),projectKey:'local|unassigned',projectName:'未归类'},
  {...ui.row('assigned-old',5),projectKey:'local|project-a'},
  {...ui.row('remote-unassigned',90),host:'remote',projectKey:'remote|unassigned',projectName:'未归类'},
  {...ui.row('assigned-new',20),projectKey:'local|project-a'},
  {...ui.row('named-unassigned',30),projectKey:'local|named-project',projectName:'未归类'},
  {...ui.row('unassigned-old',1),projectKey:'local|unassigned',projectName:'未归类'}];
 ui.context.renderList();
 assert.deepEqual(ui.$('sessions').children.map(group=>group.children.slice(1).map(row=>row.dataset.id)),
  [['named-unassigned'],['assigned-new','assigned-old'],['unassigned-new','unassigned-old'],['remote-unassigned']]);
});
test('recent chat order continues to use activity when a chat is unassigned',()=>{
 const ui=fixture();ui.context.listRows=[ui.row('assigned',10),{...ui.row('unassigned',20),projectKey:'local|unassigned',projectName:'未归类'}];
 ui.context.renderList();
 assert.deepEqual(ui.$('sessions').children.map(row=>row.dataset.id),['unassigned','assigned']);
});
test('opaque provider IDs and archive capability stay isolated',async()=>{
 const ui=fixture(),chat={...ui.row('opaque/native-id',1),capabilities:{archive:true}};
 const menu=ui.context.sessionMenuContext(chat,'deepseek');
 assert.equal(typeof menu.rename,'function');assert.equal(menu.renameScope,'bridge');assert.equal(menu.fork,undefined);assert.equal(menu.nativeOrganization,true);assert.equal(menu.organizationScope,'bridge');
 await menu.archive();assert.equal(ui.requests[0].body.sessionId,chat.id);assert.equal(ui.requests[0].url,'/api/desktop-sessions/deepseek/archive');
 assert.equal(ui.context.sessionMenuContext({...chat,capabilities:{}},'claude').archive,undefined);
});
test('restoring an archived session never requests disconnect',async()=>{
 const ui=fixture();await ui.context.sessionMenuContext({...ui.row('a',1),archived:true}).archive();
 assert.equal(ui.requests[0].body.archived,false);assert.equal(ui.requests[0].body.disconnect,undefined);
});
test('account change invalidates a captured menu; read-only sources omit rename and fork',()=>{
 const ui=fixture(),menu=ui.context.sessionMenuContext(ui.row('a',1));assert.equal(menu.isCurrent(),true);
 ui.context.csrf='other';assert.equal(menu.isCurrent(),false);
 ui.context.sessionAccess=()=>({canConfigure:false,canEditMessages:false});
 const restricted=ui.context.sessionMenuContext(ui.row('a',1));assert.equal(restricted.rename,undefined);assert.equal(restricted.fork,undefined);
});
test('rename response for another active chat never replaces the current header',async()=>{
 const ui=fixture(),chat=ui.row('a',1);ui.context.currentId='b';ui.context.state={title:'Other'};ui.$('chat-title').textContent='Other';
 await ui.context.sessionMenuContext(chat).rename('New');assert.equal(ui.$('chat-title').textContent,'Other');
});
for(const [provider,runtime] of [['codex','desktop'],['codex','cli'],['claude','desktop'],['claude','code-cli'],['claude','cowork'],['deepseek','official'],['deepseek','manual']]){
 test(`${provider} ${runtime} menu keeps passive opening, notifications and supported capabilities`,async()=>{
  const ui=fixture(),chat={...ui.row('provider-session',1),runtime,capabilities:{fork:true,archive:true,unarchive:true}};
  let notified=0;
  if(provider==='codex'){
   ui.context.currentId=chat.id;ui.$('notify-button').onclick=()=>{notified++;};
  }else{
   ui.context.desktopSessions={provider,sid:chat.id,state:null,notifications:()=>{notified++;}};
  }
  const menu=ui.context.sessionMenuContext(chat,provider);
  assert.equal(ui.requests.length,0,'opening the menu does not activate or write');
  assert.equal(menu.nativeOrganization,true);assert.equal(menu.organizationScope,provider==='codex'?'native':'bridge');assert.equal(typeof menu.rename,'function');
  assert.equal(typeof menu.pin,'function');assert.equal(typeof menu.fork,'function');assert.equal(typeof menu.archive,'function');
  await menu.notifications();assert.equal(notified,1);
  await menu.pin(true);assert.equal(ui.requests.at(-1).body.pinned,true);
  assert.equal(menu.link,provider==='codex'?'codex://threads/'+chat.id:null);
 });
}

test('desktop metadata actions retain opaque identity and do not activate chats',async()=>{
 const ui=fixture(),chat={...ui.row('code/native',1),capabilities:{rename:true}};
 const menu=ui.context.sessionMenuContext(chat,'claude');assert.equal(menu.renameScope,'native');
 await menu.organization();assert.equal(ui.requests[0].url,'/api/desktop-sessions/claude/organization?sessionId=code%2Fnative');
 await menu.organization({id:'stable-id',kind:'create-section',title:'Todo'});
 assert.equal(ui.requests[1].body.sessionId,'code/native');
 await menu.rename('Title');await menu.rename('Title');
 assert.equal(ui.requests[2].body.sessionId,'code/native');assert.equal(ui.requests[2].body.id,ui.requests[3].body.id);
 assert.ok(ui.requests.every(row=>!/detail|open|resume|activate/.test(row.url)));
});
