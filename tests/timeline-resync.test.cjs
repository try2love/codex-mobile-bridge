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
 const context=vm.createContext({queueMicrotask,chatTurnStopped:status=>['interrupted','failed'].includes(status)});
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
