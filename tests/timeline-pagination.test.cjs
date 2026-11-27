'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('chat readiness follows fresh content rendering and excludes pending, cached and history pages',()=>{
 const ctx=vm.createContext({window:{},BridgeI18n:{t:s=>s}});for(const file of ['activity','timeline-sync','timeline'])vm.runInContext(fs.readFileSync('web/features/chat/'+file+'.js','utf8'),ctx);
 const Type=vm.runInContext('ChatTimeline',ctx);let ready=0,rendered=0;
 const ui=Object.assign(Object.create(Type.prototype),{active:()=>true,cached:false,rows:new Map(),nodes:new Map(),files:new Map(),details:new Map(),container:{children:[],firstChild:null,replaceChildren(){}},sequence:0,following:true,abort:new AbortController(),capture(){},renderMeta(){rendered++;},refreshActions(){},historyStatus(){},restore(){},onReady(){assert.ok(rendered>0);ready++;}});
 const page={rows:[],files:[],sequence:1,meta:{loadingHistory:true}};
 ui.apply(page);assert.equal(ready,0);page.meta={loadingHistory:false};ui.apply(page);assert.equal(ready,1);
 ui.apply(page,false,false,true);ui.apply(page,false,true);ui.abort.abort();ui.apply(page);assert.equal(ready,1);
});
function fixture(){
 const ctx=vm.createContext({window:{},AbortController,URLSearchParams,BridgeI18n:{t:s=>s}});vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),ctx);vm.runInContext(fs.readFileSync('web/features/chat/timeline.js','utf8'),ctx);
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
function scrollFixture(){
 let resize;
 const listeners=new Map(),requests=[],viewport={scrollTop:3000,scrollHeight:10000,clientHeight:600,getBoundingClientRect:()=>({top:100}),closest:()=>null,
  addEventListener:(type,fn)=>listeners.set(type,fn),removeEventListener:type=>listeners.delete(type)};
 const container={children:Array.from({length:8},(_,index)=>({hidden:false,classList:{contains:()=>false},dataset:{key:'row-'+index},getBoundingClientRect:()=>({top:100+index*1000-viewport.scrollTop,bottom:1100+index*1000-viewport.scrollTop})}))};
 const document={documentElement:{dataset:{}}},ctx=vm.createContext({window:{},document,AbortController,URLSearchParams,clearTimeout,setTimeout,requestAnimationFrame:fn=>fn(),BridgeI18n:{t:s=>s},ResizeObserver:class{constructor(callback){resize=callback;}observe(){}disconnect(){}}});
 for(const file of ['activity','timeline-sync','timeline'])vm.runInContext(fs.readFileSync('web/features/chat/'+file+'.js','utf8'),ctx);
 const Type=vm.runInContext('ChatTimeline',ctx),ui=new Type({elements:{viewport,container,older:{},newer:{}},url:action=>'/fixture/'+action+'?scope=synthetic',renderMeta(){},renderText(){},status(){},request:(url,body,signal)=>{
  requests.push(url);return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('fixture disposed')),{once:true}));
 }});
 ui.sequence=1;ui.epoch='test';ui.before='test.oldest';ui.hasMore=true;ui.lastTop=3200;
 return {ui,viewport,requests,resize:()=>resize(),emit:(type,event={})=>listeners.get(type)?.({target:viewport,...event})};
}
test('an explicit 36px upward read near the bottom disables following before resize',()=>{
 const f=scrollFixture();
 try{f.ui.following=true;f.ui.lastTop=9400;f.viewport.scrollTop=9364;f.emit('scroll');assert.equal(f.ui.following,false);f.viewport.scrollHeight=12000;f.resize();assert.equal(f.viewport.scrollTop,9364);}
 finally{f.ui.dispose();}
});
test('opening an activity pins its row instead of following a resized bottom',()=>{
 const f=scrollFixture(),group={dataset:{key:'process'},hidden:false,classList:{contains:()=>false},parentElement:null,closest:selector=>selector==='[data-key]'?group:null,getBoundingClientRect:()=>({top:9900-f.viewport.scrollTop,bottom:10000-f.viewport.scrollTop})};
 const summary={closest:selector=>selector==='.activity'?group:null},target={closest:selector=>selector==='summary'?summary:null};
 f.ui.nodes.set('process',{node:group});f.ui.following=true;f.viewport.scrollTop=9400;f.ui.lastTop=9400;
 try{f.emit('click',{target});f.viewport.scrollHeight=14000;f.resize();assert.equal(f.ui.following,false);assert.equal(f.viewport.scrollTop,9400);}
 finally{f.ui.dispose();}
});
test('upward scrolling within the first few tall messages does not load history far from the actual top',async()=>{
 const f=scrollFixture();
 try{
  f.emit('scroll');assert.equal(f.requests.length,0,'reading 3000px into eight tall outer rows is not near the history edge');
  f.ui.lastTop=180;f.viewport.scrollTop=120;f.emit('scroll');assert.equal(f.requests.length,1,'genuine upward scrolling near the top still loads history');
 }finally{f.ui.dispose();await tick();}
});
for(const className of ['activity-body'])for(const gesture of ['wheel','touch']){
 test('history edge ignores '+gesture+' originating inside '+className,async()=>{
  const f=scrollFixture(),inner={scrollTop:90},target={closest:selector=>selector.split(',').some(value=>value.trim()==='.'+className)?inner:null};
  f.viewport.scrollTop=0;
  const upward=target=>{
   if(gesture==='wheel')f.emit('wheel',{target,deltaY:-100});
   else{f.emit('touchstart',{target,touches:[{clientY:100}]});f.emit('touchmove',{target,touches:[{clientY:130}]});}
  };
  try{
   upward(target);assert.equal(f.requests.length,0,'the inner reading surface owns this gesture even when the outer timeline is at its top');
   upward(f.viewport);assert.equal(f.requests.length,1,'the same gesture on the outer timeline still requests its older page');
  }finally{f.ui.dispose();await tick();}
 });
}
function persistentFixture(){
 const writes=[],meta=[],window={BridgeChatHistoryCache:{read:()=>new Promise(resolve=>cachedResolve=resolve),write:(...args)=>writes.push(args)}};let cachedResolve,freshResolve;
 const ctx=vm.createContext({window,AbortController,URLSearchParams,clearTimeout,setTimeout,BridgeI18n:{t:s=>s},syncChatMessageNodes(container,nodes){container.children=nodes;}});
 vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),ctx);
 ctx.syncChatMessageNodes=(container,nodes)=>{container.children=nodes;};
 vm.runInContext(fs.readFileSync('web/features/chat/timeline-sync.js','utf8'),ctx);
 vm.runInContext(fs.readFileSync('web/features/chat/timeline.js','utf8'),ctx);const Type=vm.runInContext('ChatTimeline',ctx);
 const ui=Object.assign(Object.create(Type.prototype),{cacheKey:'synthetic-history',sequence:-1,epoch:'',generation:0,windowRevision:0,rows:new Map(),nodes:new Map(),files:new Map(),details:new Map(),abort:new AbortController(),older:{},newer:{},following:true,
  container:{children:[],replaceChildren(){this.children=[];}},viewport:{removeEventListener(){}},resize:{disconnect(){}},
  active:()=>true,read:()=>new Promise(resolve=>freshResolve=resolve),renderMeta:value=>meta.push(value),renderRow:()=>({dataset:{}}),capture(){},refreshActions(){},historyStatus(){},restore(){},updates(){},status(){}});
 return {ui,writes,meta,cached:value=>cachedResolve(value),fresh:value=>freshResolve(value)};
}
const historyPage=(key,meta={},sequence=1)=>({rows:key?[{key,role:'assistant',order:1}]:[],files:[],sequence,epoch:'fixture',before:null,hasMore:false,meta:{requests:[],...meta}});

test('late persistent history cannot replace any accepted fresh timeline including disconnected and empty responses',async()=>{
 for(const fresh of [historyPage('fresh'),historyPage('fresh',{connected:false},0),historyPage(null,{connected:false},0),historyPage(null,{connected:false},-1)]){
  const f=persistentFixture(),starting=f.ui.start();f.fresh(fresh);await starting;
  f.cached(historyPage('stale'));await tick();assert.equal(f.ui.rows.has('stale'),false,'late cache after fresh sequence '+fresh.sequence);assert.equal(f.ui.cached,false);assert.equal(f.ui.rows.size,fresh.rows.length);
 }
});

test('a disposed timeline ignores both outstanding persistent history and its fresh response',async()=>{
 const f=persistentFixture(),starting=f.ui.start();f.ui.dispose();f.cached(historyPage('stale'));f.fresh(historyPage('fresh'));await starting;await tick();
 assert.equal(f.ui.rows.size,0);assert.equal(f.writes.length,0);assert.equal(f.meta.length,0);
});

test('temporary pages never persist while fresh empty history replaces the old page and loading history preserves it',()=>{
 const temporary=persistentFixture();temporary.ui.apply(historyPage('temporary',{temporary:true}),true);assert.equal(temporary.writes.length,0);
 const empty=persistentFixture();empty.ui.apply(historyPage('old'),true);empty.ui.apply(historyPage(null,{connected:false}),true);
 assert.equal(empty.writes.length,2);assert.equal(empty.writes[1][0],empty.writes[0][0]);assert.equal(empty.writes[1][1],'timeline-recent-v1');assert.equal(empty.writes[1][2].rows.length,0);
 const loading=persistentFixture();loading.ui.apply(historyPage('old'),true);loading.ui.cached=true;loading.ui.apply(historyPage(null,{loadingHistory:true,connected:false}),true);
 assert.equal(loading.writes.length,1);assert.equal(loading.ui.rows.has('old'),true);
});

test('showLogin clears persistent history even before the timeline feature has loaded',()=>{
 const source=fs.readFileSync('web/shell/app.js','utf8'),entry=source.slice(source.indexOf('function showLogin('),source.indexOf('\nasync function start('));let forgotten=0,replaced='',contextState='previous';
 const context=vm.createContext({window:{BridgeSharedRelay:true,BridgeChatHistoryCache:{forget(){forgotten++;}}},sessionPins:{suspend(){}},renderCodexContext:value=>{contextState=value;},clearQuoteComposer(){},clearCodexProvisionalDrafts(){},claudeCliAccessPanel:null,draftGateway:'old',codexDraftScope:'old',codexActiveAccountId:'old',$:()=>({}),location:{replace:value=>replaced=value}});
 vm.runInContext("'use strict';"+entry,context);assert.equal(vm.runInContext('typeof ChatTimeline',context),'undefined');vm.runInContext('showLogin()',context);
 assert.equal(forgotten,1);assert.equal(contextState,null);assert.equal(replaced,'/relay/');
});

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
 for(const file of ['activity','timeline-sync','timeline'])vm.runInContext(fs.readFileSync('web/features/chat/'+file+'.js','utf8'),ctx);
 const Type=vm.runInContext('ChatTimeline',ctx),viewport={clientHeight:500,scrollHeight:2000,scrollTop:1500,getBoundingClientRect:()=>({top:0})};
 const node={hidden:false,getBoundingClientRect:()=>({top:800-viewport.scrollTop})};
 const timeline=Object.assign(Object.create(Type.prototype),{viewport,abort:{signal:{aborted:false}},nodes:new Map([['read-here',{node}]]),newer:{},capture:()=>({key:'read-here',offset:800-viewport.scrollTop})});
 window.BridgeMobileHandoff={readingAnchor:()=>({key:'read-here',offset:-30})};
 timeline.restore(null,true);assert.equal(viewport.scrollTop,830);assert.equal(timeline.following,false);
 viewport.scrollTop=1500;timeline.restore(null,true);assert.equal(viewport.scrollTop,830);
 delete window.BridgeMobileHandoff;timeline.restore(null,true);assert.equal(timeline.following,true);
});

test('viewport height clamping keeps bottom following but an ordinary upward read still opts out',()=>{
 const f=scrollFixture();
 try{
  f.ui.following=true;f.ui.lastTop=9400;f.viewport.clientHeight=800;f.viewport.scrollTop=9200;f.emit('scroll');
  assert.equal(f.ui.following,true);
  f.viewport.scrollTop=9164;f.emit('scroll');assert.equal(f.ui.following,false);
 }finally{f.ui.dispose();}
});

test('restoring after a viewport resize does not swallow the next real upward read',()=>{
 const f=scrollFixture();
 try{
  f.viewport.clientHeight=800;f.ui.restore(null,true);
  f.ui.lastTop=9200;f.viewport.scrollTop=9164;f.emit('scroll');assert.equal(f.ui.following,false);
 }finally{f.ui.dispose();}
});
