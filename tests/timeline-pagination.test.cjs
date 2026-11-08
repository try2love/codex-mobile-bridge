'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('chat readiness follows fresh content rendering and excludes pending, cached and history pages',()=>{
 const ctx=vm.createContext({window:{},BridgeI18n:{t:s=>s}});for(const file of ['activity','timeline'])vm.runInContext(fs.readFileSync('web/features/chat/'+file+'.js','utf8'),ctx);
 const Type=vm.runInContext('ChatTimeline',ctx);let ready=0,rendered=0;
 const ui=Object.assign(Object.create(Type.prototype),{cached:false,rows:new Map(),nodes:new Map(),files:new Map(),details:new Map(),container:{children:[],firstChild:null,replaceChildren(){}},sequence:0,following:true,abort:new AbortController(),capture(){},renderMeta(){rendered++;},refreshActions(){},historyStatus(){},restore(){},onReady(){assert.ok(rendered>0);ready++;}});
 const page={rows:[],files:[],sequence:1,meta:{loadingHistory:true}};
 ui.apply(page);assert.equal(ready,0);page.meta={loadingHistory:false};ui.apply(page);assert.equal(ready,1);
 ui.apply(page,false,false,true);ui.apply(page,false,true);ui.abort.abort();ui.apply(page);assert.equal(ready,1);
});
function fixture(){
 const ctx=vm.createContext({window:{},AbortController,URLSearchParams,BridgeI18n:{t:s=>s}});vm.runInContext(fs.readFileSync('web/features/chat/timeline.js','utf8'),ctx);
 const type=vm.runInContext('ChatTimeline',ctx),pending=[],rows=new Map([['recent',{row:{key:'recent'}}]]);
 const ui=Object.assign(Object.create(type.prototype),{cached:false,busy:false,hasMore:true,abort:new AbortController(),generation:1,before:'a.recent',epoch:'a',retryCount:0,rows,
  historyStatus(){},capture(){return {key:'recent',offset:0};},following:false,active:()=>true,
  rowVisible:row=>row.role!=='activity',nearTop:()=>true,
  read(action,params){return new Promise(resolve=>pending.push({action,params,resolve}));},
  apply(page,reset,history){if(reset){this.generation++;this.rows.clear();this.epoch=page.epoch;}for(const row of page.rows)this.rows.set(row.key,{row});this.before=page.before;this.hasMore=page.hasMore;}
 });
 return {ui,pending};
}
const page=(epoch,key,role='assistant',more=true)=>({epoch,rows:[{key,role}],before:epoch+'.'+key,hasMore:more});
const tick=()=>new Promise(r=>setImmediate(r));
test('history intent survives a live reset while page is in flight',async()=>{
 const {ui,pending}=fixture(),done=ui.loadOlder(100);
 ui.apply(page('b','recent'),true);
 pending.shift().resolve(page('a','old'));
 await tick();assert.equal(pending.length,1,'retry against current cursor rather than dropping user scroll');
 assert.equal(pending[0].params.before,'b.recent');pending.shift().resolve(page('b','older', 'assistant',false));await done;
 assert.ok(ui.rows.has('older'));assert.equal(ui.busy,false);
});
test('an on-demand page containing only hidden activity continues to an older reply',async()=>{
 const {ui,pending}=fixture(),done=ui.loadOlder(100);
 pending.shift().resolve(page('a','tool','activity'));await tick();
 assert.equal(pending.length,1,'hidden rows cannot strand a reader at scroll top');
 pending.shift().resolve(page('a','reply','assistant',false));await done;assert.ok(ui.rows.has('reply'));
});
test('empty page with more history does not lose the current cursor or loop forever',async()=>{
 const {ui,pending}=fixture(),done=ui.loadOlder(100);
 pending.shift().resolve({epoch:'a',rows:[],before:null,hasMore:true});await done;
 assert.equal(ui.before,'a.recent');assert.equal(ui.retryCount,100);assert.equal(pending.length,0);
});

test('a live reset keeps the visible message during a mobile handoff and resumes following after release',()=>{
 const window={}, ctx=vm.createContext({window,BridgeI18n:{t:s=>s},requestAnimationFrame:cb=>cb()});
 for(const file of ['activity','timeline'])vm.runInContext(fs.readFileSync('web/features/chat/'+file+'.js','utf8'),ctx);
 const Type=vm.runInContext('ChatTimeline',ctx),viewport={clientHeight:500,scrollHeight:2000,scrollTop:1500,getBoundingClientRect:()=>({top:0})};
 const node={hidden:false,getBoundingClientRect:()=>({top:800-viewport.scrollTop})};
 const timeline=Object.assign(Object.create(Type.prototype),{viewport,abort:{signal:{aborted:false}},nodes:new Map([['read-here',{node}]]),newer:{},capture:()=>({key:'read-here',offset:800-viewport.scrollTop})});
 window.BridgeMobileHandoff={readingAnchor:()=>({key:'read-here',offset:-30})};
 timeline.restore(null,true);assert.equal(viewport.scrollTop,830);assert.equal(timeline.following,false);
 viewport.scrollTop=1500;timeline.restore(null,true);assert.equal(viewport.scrollTop,830);
 delete window.BridgeMobileHandoff;timeline.restore(null,true);assert.equal(timeline.following,true);
});
