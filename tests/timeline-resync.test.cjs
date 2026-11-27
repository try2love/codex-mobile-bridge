'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/features/chat/timeline.js','utf8');
function fixture(pages){
 const ctx=vm.createContext({window:{},BridgeI18n:{t:s=>s}});vm.runInContext(source,ctx);const Type=vm.runInContext('ChatTimeline',ctx),abort=new AbortController(),calls=[],received=[];
 const ui=Object.assign(Object.create(Type.prototype),{sequence:1,epoch:'same',before:'same.old',generation:0,windowRevision:0,abort,active:()=>true,
  read:async(action,params)=>{calls.push({...params});return pages.shift();},
  apply(page,reset){assert.equal(reset,false);received.push(...page.rows);this.sequence=page.sequence;if(!pages.length)abort.abort();},pause:async()=>{}});
 return {ui,calls,received};
}
test('bounded resync retains the loaded cursor and revisits edits made during catch-up',async()=>{
 const f=fixture([{sequence:30,rows:['a'],next:'same.a'}, {sequence:32,rows:['b'],next:null}, {sequence:34,rows:['a-edited'],next:null}]);
 await f.ui.updates();assert.deepEqual(f.received,['a','b','a-edited']);assert.deepEqual(f.calls.map(c=>c.after),[1,1,30]);
 assert.deepEqual(f.calls.map(c=>c.through),['','same.a','']);assert.ok(f.calls.every(c=>c.start==='same.old'&&c.paged===1));
});
test('history expansion during catch-up restarts bounded resync without clearing rows',async()=>{
 const f=fixture([{sequence:20,rows:['discard'],next:'same.x'},{sequence:21,rows:['fresh'],next:null}]);
 const read=f.ui.read;let first=true;f.ui.read=async(...args)=>{const page=await read(...args);if(first){first=false;f.ui.windowRevision++;f.ui.before='same.older';}return page;};
 await f.ui.updates();assert.deepEqual(f.received,['fresh']);assert.equal(f.calls[1].after,-1);assert.equal(f.calls[1].through,'');assert.equal(f.calls[1].start,'same.older');
});

function syncProjection(){
 const context=vm.createContext({queueMicrotask,window:{},chatTurnStopped:status=>['interrupted','failed'].includes(status)});
 vm.runInContext(fs.readFileSync('web/features/chat/timeline-sync.js','utf8'),context);
 return vm.runInContext('({mergeChatSyncRows,carryChatSyncContext,ChatTimelineSync})',context);
}

test('opening process disclosure drains every page and closing cancels further reads',async()=>{
 const {ChatTimelineSync}=syncProjection(),calls=[];
 const group={cursor:'process',version:'v1',rows:[],turnId:'turn'};
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{abort:new AbortController(),target:{},host:{},files:new Map(),turns:new Map([['turn',{status:'completed'}]]),processes:new Map([['process',group]]),active:()=>true,paint(){},
  read:async(_mode,query)=>{calls.push(query);return {rows:[{key:String(calls.length),order:calls.length}],next:calls.length<3?String(calls.length):null};}});
 sync.disclosure('turn',true);
 await new Promise(setImmediate);
 assert.equal(calls.length,3);
 assert.equal(group.rows.length,3);
 assert.equal(group.next,null);
 group.loadedVersion=null;group.rows=[];calls.length=0;
 sync.read=async()=>{calls.push({});sync.disclosure('turn',false);return {rows:[],next:'next'};};
 sync.disclosure('turn',true);await new Promise(setImmediate);
 assert.equal(calls.length,1);
});
test('a visible running process drains its older pages without waiting for a disclosure click',async()=>{
 const {ChatTimelineSync}=syncProjection(),calls=[];
 const group={cursor:'process',version:'v1',rows:[],turnId:'turn'};
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{abort:new AbortController(),target:{},host:{},files:new Map(),turns:new Map([['turn',{status:'inProgress'}]]),processes:new Map([['process',group]]),active:()=>true,paint(){},
  read:async(_mode,query)=>{calls.push(query);return query.after==='latest'?{rows:[{key:'recent',order:2}],previous:'recent',next:null}:{rows:[{key:'earlier',order:1}],previous:null,next:'recent'};}});
 sync.refreshRunning();await new Promise(setImmediate);
 assert.equal(group.open,true);assert.equal(calls.length,2);assert.deepEqual(Array.from(group.rows,row=>row.key),['earlier','recent']);assert.equal(group.previous,null);
});

test('native range order updates older rows without crossing the current turn',()=>{
 const {mergeChatSyncRows:merge}=syncProjection();
 const row=(key,order)=>({key,order,role:'user'});
 let rows=merge([],[row('old-start',10),row('old-tail',20)]);
 rows=merge(rows,[row('old-tail',20),row('running-input',22),row('running-answer',23)]);
 rows=merge(rows,[row('old-start',4),row('old-backfill',19),row('old-tail',22)],{older:true,range:true});
 assert.deepEqual(Array.from(rows,row=>row.key),['old-start','old-backfill','old-tail','running-input','running-answer']);
 rows=merge(rows,[row('earlier-unseen',0)],{older:true});
 assert.deepEqual(Array.from(rows,row=>row.key),['earlier-unseen','old-start','old-backfill','old-tail','running-input','running-answer']);
});

test('a recent projection missing usage keeps only verified context and honors unsupported',()=>{
 const {carryChatSyncContext:carry}=syncProjection();
 const first={contextUsage:{usedTokens:500,contextWindow:2000},contextUsageStatus:'available',lastPromptContextUsage:{usedTokens:320}};
 const missing=carry({status:'active'},first);
 assert.equal(missing.contextUsage.cached,true);
 assert.equal(missing.contextUsage.usedTokens,500);
 assert.equal(missing.lastPromptContextUsage.usedTokens,320);
 assert.equal(carry({contextUsage:{usedTokens:0,contextWindow:2000}},missing).contextUsage.usedTokens,0);
 const unsupported=carry({contextUsageStatus:'unsupported'},missing);
 assert.equal(unsupported.contextUsage,null);
 assert.equal(carry({contextUsage:{usedTokens:25,contextWindow:2000}},unsupported).contextUsage.usedTokens,25);
});


test('an authorized first-screen preview only exposes readings and yields to live context',()=>{
 const {ChatTimelineSync}=syncProjection(),previews=[];
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{target:{},rows:[],turns:new Map(),
  files:new Map(),processes:new Map(),host:{renderCachedContext:meta=>previews.push(meta),renderSync(){}},refreshRunning(){}});
 sync.accept({rows:[],meta:{loadingHistory:true,contextUsage:null}});
 const contextUsage={usedTokens:20,contextWindow:100};
 sync.accept({rows:[],meta:{connected:true,contextUsage,capabilities:{compact:true},requests:[{id:'saved'}]}},{cached:true});
 assert.deepEqual(Object.keys(previews[0]),['contextUsage']);
 assert.equal(previews[0].contextUsage,contextUsage);
 assert.equal(sync.value.meta.contextUsage,null);
 sync.accept({rows:[],meta:{connected:true,contextUsageStatus:'unavailable'}});
 assert.equal(sync.value.meta.contextUsage.usedTokens,20);
 assert.equal(sync.value.meta.contextUsage.cached,true);
 assert.equal(sync.value.meta.capabilities,undefined);
 sync.accept({rows:[],meta:{connected:true,contextUsage:{usedTokens:35,contextWindow:100}}});
 assert.equal(sync.value.meta.contextUsage.usedTokens,35);
 assert.equal(sync.value.meta.contextUsage.cached,undefined);
 sync.accept({rows:[],meta:{contextUsageStatus:'unsupported'}});
 sync.accept({rows:[],meta:{contextUsage}},{cached:true});
 assert.equal(previews.length,1);
 assert.equal(sync.value.meta.contextUsage,null);
});

test('offline archive process descriptors keep their source position in the flat timeline',()=>{
 const {ChatTimelineSync}=syncProjection();
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{target:{offline:true},rows:[],turns:new Map(),
  files:new Map(),processes:new Map(),host:{renderSync(){}},refreshRunning(){}});
 const turnId='saved';
 sync.accept({rows:[{key:'question',role:'user',order:0,turnId},{key:'answer',role:'assistant',order:2,turnId}],
  turns:[{id:turnId,status:'completed',processes:[{key:'group',processCursor:'offline-group',order:1}]}]});
 assert.deepEqual(Array.from(sync.project(),row=>row.key),['question','process-earlier:group','process-later:group','answer']);
 assert.equal(sync.processes.get('group').cursor,'offline-group');
});

test('offline process can expand from a cached screen without replacing newer live rows',async()=>{
 const {ChatTimelineSync}=syncProjection(),calls=[],group={cursor:'online.process',version:'live',rows:[{key:'current',version:'newer',text:'Live update',order:2}],turnId:'turn'};
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{target:{offline:true},host:{cached:true},files:new Map(),turns:new Map([['turn',{status:'completed'}]]),processes:new Map([['process',group]]),active:()=>true,paint(){},
  read:async()=>{calls.push('archive');return {rows:[{key:'earlier',version:'saved',text:'Earlier saved process',order:1},{key:'current',version:'older',text:'Old snapshot',order:2}]};}});
 group.open=true;await sync.fillProcess('process');
 assert.equal(calls.length,1);
 assert.deepEqual(Array.from(group.rows,row=>row.text),['Earlier saved process','Live update']);
});


test('a connecting empty response does not suppress a later local first screen',async()=>{
 const {ChatTimelineSync}=syncProjection();let release;
 const preview=new Promise(resolve=>{release=resolve;});
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{abort:new AbortController(),target:{scope:'s'},rows:[],clearRevision:0,
  initial:{preview,promise:Promise.resolve({page:{index:[],rows:[],meta:{loadingHistory:true},turns:[]}}),valid:()=>true,mayPreview:()=>true,close(){}},
  active:()=>true,accept(page,options){this.rows=page.rows;this.host.cached=!!options?.cached;},host:{},
  mergeRecent:async()=>{},});
 await sync.start();release({index:[{key:'saved'}],rows:[{key:'saved',text:'Saved first screen'}]});
 await new Promise(setImmediate);assert.equal(sync.rows[0]?.text,'Saved first screen');
});

test('a changed process cursor requests a fresh screen instead of leaving a permanent retry button',async()=>{
 const {ChatTimelineSync}=syncProjection(),group={cursor:'process',version:'v1',loadedVersion:'v1',rows:[{key:'old'}],turnId:'turn',next:'removed',open:true};
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{target:{},host:{},files:new Map(),turns:new Map([['turn',{status:'completed'}]]),processes:new Map([['process',group]]),active:()=>true,paint(){},
  read:async()=>{throw Object.assign(Error('Process changed'),{status:409,code:'sync_cursor_changed'});}});
 await sync.readProcess('process','later');
 assert.equal(sync.needsRefresh,true);assert.equal(group.next,null);assert.equal(group.loadedVersion,null);
 assert.deepEqual(group.rows,[{key:'old'}],'keep previously visible content during refresh');
});

test('returning to a visible chat retries open failed processes without deleting its displayed rows',()=>{
 const {ChatTimelineSync}=syncProjection(),group={cursor:'process',version:'v1',loadedVersion:'v1',rows:[{key:'saved'}],turnId:'turn',error:'later',open:true};
 let cancelled=0,woken=0;
 const sync=Object.assign(Object.create(ChatTimelineSync.prototype),{target:{},host:{},processes:new Map([['process',group]]),turns:new Map(),cancelWatch(){cancelled++;},wake(){woken++;}});
 sync.resume();
 assert.equal(sync.needsRefresh,true);assert.equal(group.error,null);assert.equal(group.loadedVersion,null);
 assert.equal(group.rows[0].key,'saved');assert.equal(cancelled,1);assert.equal(woken,1);
});
