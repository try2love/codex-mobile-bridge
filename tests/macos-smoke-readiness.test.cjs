'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {waitForRenderer,evaluate}=require('../scripts/smoke-macos.cjs');
class CDP extends EventTarget{
  constructor(replies){super();this.replies=replies;this.calls=[];}
  send(packet){
    const request=JSON.parse(packet);this.calls.push(request);
    const reply=this.replies.shift();
    queueMicrotask(()=>this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({id:request.id,...reply})})));
  }
}
test('initial document replacement retries only read-only readiness before one mutation',async()=>{
  const socket=new CDP([{error:{code:-32000,message:'Execution context was destroyed.'}},
    {result:{result:{value:false}}},{result:{result:{value:true}}},{result:{result:{value:'saved'}}}]);
  await waitForRenderer(socket,'file:///app/desktop/index.html',{timeout:1000,interval:1});
  assert.equal(await evaluate(socket,'changeDevices({action:"save"})'), 'saved');
  assert.equal(socket.calls.length,4);
  for(const call of socket.calls.slice(0,3)){
    assert.ok(call.params.expression.includes('document.readyState'));
    assert.ok(call.params.expression.includes('file:///app/desktop/index.html'));
    assert.ok(!call.params.expression.includes('changeDevices'));
    assert.equal(call.params.awaitPromise,false);
  }
  assert.equal(socket.calls.filter(call=>call.params.expression.includes('changeDevices')).length,1);
});
test('missing default context is allowed only during bounded readiness',async()=>{
  const socket=new CDP([{error:{code:-32000,message:'Cannot find default execution context'}},{result:{result:{value:true}}}]);
  await waitForRenderer(socket,'file:///app/desktop/index.html',{timeout:1000,interval:1});
  assert.equal(socket.calls.length,2);
});
test('renderer crashes and other CDP or script errors are not retried',async()=>{
  for(const reply of [{error:{code:-32000,message:'Target crashed'}},
    {error:{code:-32601,message:'Method not found'}},
    {result:{exceptionDetails:{text:'snapshot failed'}}}]){
    const socket=new CDP([reply]);
    await assert.rejects(waitForRenderer(socket,'file:///app/desktop/index.html',{timeout:1000,interval:1}));
    assert.equal(socket.calls.length,1);
  }
});
test('context destroyed during device-policy write fails without replay',async()=>{
  const socket=new CDP([{result:{result:{value:true}}},{error:{code:-32000,message:'Execution context was destroyed.'}}]);
  await waitForRenderer(socket,'file:///app/desktop/index.html',{timeout:1000,interval:1});
  await assert.rejects(evaluate(socket,'changeDevices({action:"save"})'),/Execution context was destroyed/);
  assert.equal(socket.calls.length,2);
});
test('an absent document or unanswered readiness request has a finite deadline',async()=>{
  const notReady=new CDP(Array.from({length:50},()=>({result:{result:{value:false}}})));
  await assert.rejects(waitForRenderer(notReady,'file:///app/desktop/index.html',{timeout:20,interval:5}),/did not become ready/);
  const silent=new EventTarget();silent.send=()=>{};
  await assert.rejects(waitForRenderer(silent,'file:///app/desktop/index.html',{timeout:20}),/snapshot timed out/);
});
test('readiness requires the selected complete document and a worker snapshot',async()=>{
  const vm=require('node:vm'),socket=new CDP([{result:{result:{value:true}}}]);
  await waitForRenderer(socket,'file:///app/desktop/index.html',{timeout:1000});
  const expression=socket.calls[0].params.expression;
  for(const [url,ready,snapshot,expected] of [
    ['about:blank','complete',{},false],['file:///app/desktop/index.html','loading',{},false],
    ['file:///app/desktop/index.html','complete',null,false],['file:///app/desktop/index.html','complete',{},true]]){
    assert.equal(vm.runInNewContext(expression,{location:{href:url},document:{readyState:ready},snapshot}),expected);
  }
});
async function runSmoke(source){
  const vm=require('node:vm'),path=require('node:path'),{EventEmitter}=require('node:events');
  const directory='/fixture/private-data',url='file:///fixture/desktop/index.html',calls=[];
  const state={directory,running:false,notifications:false,node:'undefined',accounts:true,sessionHours:'12',
    devices:{sessions:[],policy:{allowlistEnabled:true,allowlist:['192.0.2.7']}},deviceOverflow:false,
    barkEnabled:false,barkServer:'https://api.day.app',barkKeyType:'password',barkTest:true,
    pushplusEnabled:false,pushplusToken:'',pushplusTokenType:'password',pushplusTest:true};
  const child=new EventEmitter();child.stderr=new EventEmitter();child.exitCode=null;
  child.kill=()=>{child.exitCode=0;child.emit('exit',0);};
  const server={listen(_port,_host,done){done();},address(){return {port:12345};},close(done){done();}};
  class Socket extends CDP{
    constructor(){super([]);queueMicrotask(()=>this.dispatchEvent(new Event('open')));}
    send(packet){
      const request=JSON.parse(packet);calls.push(request);
      this.replies.push(calls.length===1?{error:{code:-32000,message:'Execution context was destroyed.'}}:
        {result:{result:{value:request.params.expression.includes('changeDevices')?state:true}}});
      super.send(packet);
    }
    close(){}
  }
  let completed;const result=new Promise(resolve=>{completed=resolve;});
  const mod={exports:{}};
  const modules={'node:assert/strict':assert,'node:path':path,
    'node:fs/promises':{mkdir:async()=>{},mkdtemp:async()=>directory},'node:net':{createServer:()=>server},
    'node:child_process':{execFileSync:()=> 'Codex Mobile Bridge',spawn:()=>child}};
  const requireMock=name=>modules[name];requireMock.main=mod;
  vm.runInNewContext(source,{require:requireMock,module:mod,__dirname:'/fixture/scripts',
    process:{platform:'darwin',argv:['node','smoke','/fixture/App.app'],env:{}},
    fetch:async()=>({json:async()=>[{type:'page',url,webSocketDebuggerUrl:'ws://127.0.0.1/fixture'}]}),
    WebSocket:Socket,AbortSignal,setTimeout,clearTimeout,console:{log:()=>completed({passed:true,calls}),error:error=>completed({error,calls})}});
  return result;
}
test('signed-App smoke survives initial context replacement and performs all UI checks once',async()=>{
  const fs=require('node:fs');
  const source=fs.readFileSync(process.env.CMB_SMOKE_TEST_SOURCE||require.resolve('../scripts/smoke-macos.cjs'),'utf8');
  const result=await runSmoke(source);
  assert.equal(result.error,undefined,result.error?.message);
  assert.equal(result.passed,true);
  assert.equal(result.calls.filter(call=>call.params.expression.includes('changeDevices')).length,1);
});
