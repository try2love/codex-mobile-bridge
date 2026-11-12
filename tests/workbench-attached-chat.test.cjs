'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({BridgeI18n:{t:x=>x}});
vm.runInContext(fs.readFileSync('web/features/workspace/workbench.js','utf8'),context);
const Workbench=vm.runInContext('Workbench',context);
function fixture(){const opened=[],wb=Object.create(Workbench.prototype);Object.assign(wb,{sessions:new Map(),canSplit(){return false;},rememberScroll(){},cancelLocate(){},paint(){},node(){return{};},openChat(id,host){opened.push({id,host});}});return{wb,opened};}
test('native child shares parent tools and has a closeable view without a child workspace',async()=>{
 const{wb,opened}=fixture();wb.open('parent','local');const parent=wb.current,terminal={id:'terminal',body:{},terminal:{pid:42}};parent.files.push(terminal);
 wb.open('child','local',{rootId:'parent',title:'Child'});
 assert.equal(wb.current,parent);assert.equal(wb.sessions.size,1);assert.equal(parent.files[0],terminal);assert.equal(wb.isFileVisible,false);
 wb.select('chat');assert.deepEqual(opened.pop(),{id:'parent',host:'local'});
 await wb.close('child:child');assert.deepEqual(opened.pop(),{id:'parent',host:'local'});assert.equal(parent.files[0],terminal);assert.equal(parent.files.length,1);
});
test('child view identities stay isolated by provider and host',()=>{
 const{wb}=fixture();wb.open('child','one',{rootId:'parent'});const first=wb.current;wb.open('child','two',{rootId:'parent'});assert.notEqual(wb.current,first);
 wb.provider='claude';wb.open('child','one',{rootId:'parent'});assert.notEqual(wb.current,first);assert.equal(wb.sessions.size,3);
});
test('notification entry resolves nested native parents before opening the root workspace',async()=>{
 const{wb}=fixture(),reads=[];const data={child:{title:'Child',attachedParent:{id:'middle',host:'local',available:true}},middle:{attachedParent:{id:'parent',host:'local',available:true}},parent:{}};
 const route=await wb.resolveChat('child','local',async id=>{reads.push(id);return data[id];});assert.deepEqual(reads,['child','middle','parent']);assert.equal(route.rootId,'parent');assert.equal(route.title,'Child');
 wb.open('child','local',route);assert.equal(wb.current.id,'parent');assert.equal(wb.current.chatId,'child');assert.equal(wb.sessions.size,1);
});
test('unavailable, cross-host and cyclic parents never create a child workspace',async()=>{
 for(const parent of [{id:'parent',host:'other',available:true},{id:'parent',host:'local',available:false},{id:'child',host:'local',available:true}]){
  const{wb}=fixture();await assert.rejects(wb.resolveChat('child','local',async()=>({attachedParent:parent})));assert.equal(wb.sessions.size,0);
 }
});
function routeController(overrides={}){
 const source=fs.readFileSync('web/shell/app.js','utf8'),start=source.indexOf('async function openChat(id,host="local")'),end=source.indexOf('  messageActions.dialog.close();workbench.open',start);
 const c=vm.createContext({clientNavigation:{provider:'codex'},sharedSessionMenu:null,messageActions:{pending:false},chatOpenGeneration:0,currentId:'parent',currentHost:'local',chatTimeline:{abort:{signal:{aborted:false}}},workbench:{current:{id:'parent',chatId:'parent'},select(){}},listRows:[],api:async()=>({meta:{}}),sessionUrl:id=>id,toast(){},t:x=>x,$:()=>({classList:{add(){}}}),window:{BridgePresentation:{openChat(){}}},browserNavigation:{sync(){}},reportVisibleChat(){},...overrides});
 vm.runInContext(source.slice(start,end)+'return route;}',c);return c;
}
test('selecting the current parent cancels a pending child notification route',async()=>{
 let release;const pending=new Promise(resolve=>release=resolve),{wb}=fixture();
 const c=routeController({workbench:{...wb,current:{id:'parent',chatId:'parent'},select(){},resolveChat:()=>pending}});
 const child=c.openChat('child','local');await c.openChat('parent','local');release({rootId:'parent'});assert.equal(await child,undefined);
});
test('a known ordinary chat opens without waiting for a metadata request',async()=>{
 let reads=0;const c=routeController({currentId:'other',listRows:[{id:'parent',host:'local',cwd:'/project'}],api:async()=>{reads++;throw Error('offline');}});
 const route=await c.openChat('parent','local');assert.equal(route.rootId,'parent');assert.equal(route.cwd,'/project');assert.equal(reads,0);
});

test('switching provider cancels a pending child route',async()=>{
 let release;const pending=new Promise(resolve=>release=resolve);
 const c=routeController({workbench:{current:{id:'parent',chatId:'parent'},resolveChat:()=>pending}});
 const child=c.openChat('child','local');c.clientNavigation.provider='claude';release({rootId:'parent'});assert.equal(await child,undefined);
});
