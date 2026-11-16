'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {ChatHistoryCache}=require('../web/features/chat/history-cache.js');
function storage(){const rows=new Map();return {rows,async request(op,key,value){if(op==='read')return rows.get(key)??null;if(op==='write'){rows.set(key,value);return true;}if(op==='list')return [...rows.keys()].filter(k=>k.startsWith(key));if(op==='remove'){rows.delete(key);return true;}}};}
test('native cache reuses pages across documents and keeps session namespaces separate',async()=>{
 const native=storage();global.window={BridgeMobileStorage:native};
 try{const lan=new ChatHistoryCache(null,()=>100),publicPage=new ChatHistoryCache(null,()=>101),scope=JSON.stringify(['login','gateway','claude','chat']);
 assert.equal(await lan.write(scope,'recent',{messages:['cached']}),true);
 assert.deepEqual(await publicPage.read(scope),{messages:['cached']});assert.equal(await publicPage.read(JSON.stringify(['other-login','gateway','claude','chat'])),null);
 await lan.write(JSON.stringify(['login','gateway','codex','other']),'recent',{messages:['keep']});
 await publicPage.forgetNamespace(JSON.stringify(['login','gateway','claude']));assert.equal(await lan.read(scope),null);
 assert.deepEqual(await lan.read(JSON.stringify(['login','gateway','codex','other'])),{messages:['keep']});
 }finally{delete global.window;}
});
test('native expiry removes stale display data without deleting another page',async()=>{
 const native=storage();global.window={BridgeMobileStorage:native};let now=100;
 try{const cache=new ChatHistoryCache(null,()=>now);cache.ttl=50;await cache.write('old','recent',{});now=150;await cache.write('new','recent',{ok:true});assert.equal(await cache.read('old'),null);assert.deepEqual(await cache.read('new'),{ok:true});assert.equal(native.rows.size,1);}finally{delete global.window;}
});
test('a clear cancels queued native writes and permits subsequent fresh writes',async()=>{
 const native=storage();global.window={BridgeMobileStorage:native};
 try{const cache=new ChatHistoryCache(null);const pending=cache.write('chat','recent',{old:true}),clear=cache.forget('chat');assert.equal(await pending,false);await clear;assert.equal(await cache.read('chat'),null);assert.equal(await cache.write('chat','recent',{fresh:true}),true);assert.deepEqual(await cache.read('chat'),{fresh:true});}finally{delete global.window;}
});
