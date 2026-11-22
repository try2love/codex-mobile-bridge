'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=file=>fs.readFileSync('web/'+file,'utf8');
test('ordinary chat uses the bounded native timeline and the shared flat renderer',async()=>{
 const ctx=vm.createContext({window:{},AbortController,URLSearchParams,clearTimeout,setTimeout,BridgeI18n:{t:s=>s}});
 for(const name of ['activity','timeline'])vm.runInContext(source('features/chat/'+name+'.js'),ctx);
 const Type=vm.runInContext('ChatTimeline',ctx),reads=[];
 const view=Object.assign(Object.create(Type.prototype),{abort:new AbortController(),sequence:-1,older:{},
  read:async(action,params)=>{reads.push({action,params});return {rows:[],meta:{},hasMore:false};},apply(){},updates(){}});
 await view.start();assert.equal(reads[0].action,'timeline');assert.equal(reads[0].params.limit,20);
 assert.doesNotMatch(source('features/chat/timeline.js'),/this\.url\('process'\)|projection:'conversation'|renderChatProcessPage\b/);
});
test('desktop details and history use native rows and a separate unfolded cache generation',async()=>{
 const ctx=vm.createContext({window:{},URLSearchParams});vm.runInContext(source('features/clients/desktop-sessions.js'),ctx);
 const Type=vm.runInContext('DesktopSessionsView',ctx),requests=[];
 const view=Object.assign(Object.create(Type.prototype),{readEpoch:1,providerReadEpochs:new Map(),readTasks:new Map(),
  path:(action,sid)=>'/'+action+'?sessionId='+sid,request:async url=>{requests.push(url);return {};}});
 await view.sharedRead('zcode','detail','sess_new');assert.equal(requests[0],'/detail?sessionId=sess_new');
 const text=source('features/clients/desktop-sessions.js');assert.doesNotMatch(text,/historyMode:'conversation'|renderChatProcessPage\b|conversation-recent-v1/);
 assert.match(text,/timeline-recent-v1/);
});
test('message synchronization keeps direct chronological siblings and does not invent grouping',()=>{
 const text=source('features/chat/activity.js');assert.doesNotMatch(text,/function (?:groupChatTasks|groupChatActivities|renderChatProcessPage)\(/);
 const container={children:[],get firstChild(){return this.children[0]||null;},insertBefore(node,next){node.remove();const i=this.children.indexOf(next);this.children.splice(i<0?this.children.length:i,0,node);node.parentElement=this;}};
 const row=(key,role)=>({tagName:'ARTICLE',dataset:{key},hidden:false,parentElement:null,__chatMessage:{role,turnId:'same',turnStatus:'inProgress'},
  querySelector(){return null;},remove(){const p=this.parentElement;if(p){p.children.splice(p.children.indexOf(this),1);this.parentElement=null;}},
  get nextSibling(){const p=this.parentElement;return p?.children[p.children.indexOf(this)+1]||null;}});
 const nodes=[row('user','user'),row('reason','reasoning'),row('tool-a','activity'),row('progress','assistant'),row('tool-b','activity'),row('final','assistant')];
 const ctx=vm.createContext({window:{},document:{createElement(){throw Error('must not create group wrappers');}},container,nodes});vm.runInContext(text,ctx);
 vm.runInContext('syncChatMessageNodes(container,nodes)',ctx);assert.deepEqual(container.children,nodes);
 for(const node of nodes)node.__chatMessage.turnStatus='completed';
 vm.runInContext('syncChatMessageNodes(container,nodes)',ctx);assert.deepEqual(container.children,nodes);
});
