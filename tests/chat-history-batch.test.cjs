'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({});vm.runInContext(fs.readFileSync('web/features/chat/activity.js','utf8'),context);
const readBatch=(...args)=>context.readChatHistoryBatch(...args);
const page=(rows,before,hasMore=true)=>({rows,before,hasMore,epoch:'a'});
test('two hundred process records do not consume a request to reveal earlier dialogue',async()=>{
 const pages=[page(Array.from({length:100},(_,i)=>({key:'t'+i,role:'activity'})),'first'),page(Array.from({length:100},(_,i)=>({key:'u'+i,role:'activity'})),'second'),page([{key:'supplement',role:'user',text:'补充任务'}],'third')];let requests=0;
 const result=await readBatch(async()=>{requests++;return pages.shift();},'recent');
 assert.equal(requests,3);assert.equal(result.pages.length,3);assert.equal(result.pages.at(-1).rows[0].key,'supplement');
});
test('answered cards remain process but ordinary answer-shaped supplements end dialogue backfill',async()=>{
 const pages=[page([{id:'answer',role:'user',interaction:'response'}],'older'),page([{id:'ordinary',role:'user',text:'同意并补充'}],'oldest')];
 const result=await readBatch(async()=>pages.shift(),'recent');assert.equal(result.pages.length,2);
});
test('native cursor stalls, scope change and repeated failures cannot loop or silently lose rows',async()=>{
 let calls=0;const stalled=await readBatch(async()=>{calls++;return page([{id:'t',role:'activity'}],'same');},'same');assert.equal(calls,1);assert.equal(stalled.stalled,true);
 const scoped=await readBatch(async()=>page([{id:'u',role:'user'}],'next'),'old',{current:()=>false});assert.equal(scoped.cancelled,true);
 calls=0;const partial=await readBatch(async()=>{if(calls++)throw Error('offline');return page([{id:'tool',role:'activity'}],'more');},'recent');assert.equal(partial.pages.length,1);assert.equal(partial.error.message,'offline');
 calls=0;const bounded=await readBatch(async()=>page([{id:'t'+calls,role:'activity'}],String(++calls)),'start');assert.equal(calls,4);assert.equal(bounded.pages.length,4);
});
test('already cached dialogue does not strand an older-history request inside a process page',async()=>{
 const pages=[page([{key:'known',role:'assistant'}],'one'),page([{key:'new',role:'assistant'}],'two')];
 const result=await readBatch(async()=>pages.shift(),'recent',{known:new Set(['known'])});assert.equal(result.pages.length,2);
});

test('a failed request after switching chats discards every buffered page',async()=>{
 let calls=0,valid=true;
 const result=await readBatch(async()=>{if(calls++){valid=false;throw Error('old transport closed');}return page([{id:'old',role:'activity'}],'more');},'recent',{current:()=>valid});
 assert.equal(result.cancelled,true);assert.equal(result.pages.length,0);
});
