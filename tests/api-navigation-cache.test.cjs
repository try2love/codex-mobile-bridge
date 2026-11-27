'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/shell/app.js','utf8');
const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('const workbench='));
function fixture(){
 const window=new EventTarget(),state={invalidations:0,calls:0};let reject;
 window.BridgeChatSyncCache={requireValidation(){state.invalidations++;}};
 const context=vm.createContext({window,AbortController,Error,TypeError,csrf:'fixture',hostUrl:value=>value,t:value=>value,authLost(){},
  fetch:(_url,options)=>{state.calls++;state.signal=options.signal;return new Promise((_resolve,no)=>{reject=no;});}});
 vm.runInContext(apiSource+'\nwindow.fixtureApi=api;',context);
 return {window,state,api:window.fixtureApi,fail(){reject(new TypeError('navigation cancelled'));}};
}
for(const event of ['beforeunload','pagehide'])test(event+' cancels an owned read without invalidating saved preview',async()=>{
 const f=fixture(),pending=f.api('/api/sessions/fixture/sync');
 f.window.dispatchEvent(new Event(event));f.fail();await assert.rejects(pending);
 assert.equal(f.state.signal?.aborted,true);assert.equal(f.state.invalidations,0);assert.equal(f.state.calls,1);
});
test('a genuine read network failure still invalidates saved preview',async()=>{
 const f=fixture(),pending=f.api('/api/sessions/fixture/sync');f.fail();await assert.rejects(pending);
 assert.equal(f.state.invalidations,1);
});
test('navigation never cancels or replays a write',async()=>{
 const f=fixture(),pending=f.api('/api/sessions/fixture/send',{text:'fixture'});
 f.window.dispatchEvent(new Event('pagehide'));assert.equal(f.state.signal,undefined);
 f.fail();await assert.rejects(pending);assert.equal(f.state.calls,1);assert.equal(f.state.invalidations,1);
});

test('navigation also cancels a read with a caller signal without aborting that caller',async()=>{
 const f=fixture(),upstream=new AbortController(),pending=f.api('/api/sessions/fixture/sync',undefined,upstream.signal);
 f.window.dispatchEvent(new Event('pagehide'));f.fail();await assert.rejects(pending);
 assert.equal(f.state.signal.aborted,true);assert.equal(upstream.signal.aborted,false);assert.equal(f.state.invalidations,0);
});
test('caller cancellation is forwarded into the read controller',async()=>{
 const f=fixture(),upstream=new AbortController(),pending=f.api('/api/sessions/fixture/sync',undefined,upstream.signal);
 upstream.abort();f.fail();await assert.rejects(pending);
 assert.equal(f.state.signal.aborted,true);assert.equal(f.state.invalidations,0);
});

test('the read-only activity POST is cancelled during navigation without revoking preview',async()=>{
 const f=fixture(),pending=f.api('/api/activity',{hosts:{local:['fixture']}});
 f.window.dispatchEvent(new Event('pagehide'));f.fail();await assert.rejects(pending);
 assert.equal(f.state.signal.aborted,true);assert.equal(f.state.invalidations,0);assert.equal(f.state.calls,1);
});
