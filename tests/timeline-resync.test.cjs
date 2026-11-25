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
