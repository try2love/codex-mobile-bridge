'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {createManagementWorker}=require('../desktop/controller.cjs');
const options={executable:'fixture',dataDir:'fixture-profile'};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fixture(timeout=1000){
  const children=[];
  const launch=(executable,args)=>{
    const child=new EventEmitter();
    child.args=args;child.writes=[];child.stdout=new EventEmitter();child.stdout.setEncoding=()=>{};
    child.stderr=new EventEmitter();child.stdin=new EventEmitter();child.kill=()=>{throw Error('must not kill accepted operation');};
    child.stdin.write=text=>child.writes.push(JSON.parse(text));
    child.stdin.end=()=>{child.ended=true;};
    child.reply=result=>child.stdout.emit('data',JSON.stringify({ok:true,result})+'\n');
    children.push(child);return child;
  };
  return {children,worker:createManagementWorker({launch,timeout})};
}

test('management commands remain distinct, serialized, and use one persistent owner',async()=>{
  const {children,worker}=fixture();
  const first=worker.call(options,'accounts',{action:'login',name:'Fixture'});
  const second=worker.call(options,'accounts',{action:'list'});
  await tick();assert.equal(children.length,1);const child=children[0];
  assert.equal(child.args[0],'management-stream');assert.equal(child.writes.length,1);
  child.reply({enrollment:{phase:'starting'}});await first;await tick();
  assert.equal(child.writes.length,2);assert.equal(child.writes[1].payload.action,'list');
  child.reply({enrollment:{phase:'waiting'}});
  assert.equal((await second).enrollment.phase,'waiting');
  worker.close();assert.ok(child.ended);child.emit('close',0);
});

test('a UI timeout never replays a command or advances to another owner before completion',async()=>{
  const {children,worker}=fixture(20);
  const first=worker.call(options,'accounts',{action:'switch',id:'a'});
  const rejected=assert.rejects(first,/超时/);
  const second=worker.call(options,'accounts',{action:'list'});
  // Attach a handler immediately so the failing implementation reports a test
  // assertion instead of an unhandled rejection while this test inspects it.
  second.catch(()=>{});
  await tick();const child=children[0];await rejected;await delay(25);
  assert.equal(children.length,1,'timeout must not launch a second owner');
  assert.equal(child.writes.length,1,'next request must wait for accepted operation');
  assert.equal(child.ended,undefined,'timeout keeps the accepted operation alive');
  child.reply({switch:{phase:'complete'}});await tick();
  assert.equal(child.writes.length,2);assert.equal(child.writes[1].payload.action,'list');
  child.reply({switch:{phase:'complete'}});assert.equal((await second).switch.phase,'complete');
  worker.close();child.emit('close',0);
});

test('explicit close cancels queued commands and waits for old owner before a new profile',async()=>{
  const {children,worker}=fixture();
  const first=worker.call(options,'accounts',{action:'login'});
  const second=worker.call(options,'accounts',{action:'delete',id:'must-not-run'});
  const firstRejected=assert.rejects(first,/关闭/),secondRejected=assert.rejects(second,/关闭/);
  await tick();const old=children[0];worker.close();
  const next=worker.call({...options,dataDir:'new-profile'},'accounts',{action:'list'});
  await tick();assert.equal(children.length,1);assert.ok(old.ended);
  old.reply({enrollment:{phase:'waiting'}});await tick();
  assert.equal(children.length,1,'wait for worker exit before transferring ownership');
  old.emit('close',0);await firstRejected;await secondRejected;await tick();
  assert.equal(children.length,2);assert.equal(old.writes.length,1);
  children[1].reply({accounts:[]});await next;worker.close();children[1].emit('close',0);
});

test('a worker exit rejects the active command without retrying it',async()=>{
  const {children,worker}=fixture();
  const first=worker.call(options,'accounts',{action:'addApi'});
  const rejected=assert.rejects(first,/退出/);
  await tick();children[0].emit('close',1);await rejected;await tick();
  assert.equal(children.length,1);assert.equal(children[0].writes.length,1);
  worker.close();
});

test('changing profiles waits for graceful exit rather than sharing or overlapping owners',async()=>{
  const {children,worker}=fixture();
  const first=worker.call(options,'accounts',{action:'list'});
  await tick();children[0].reply({profile:'first'});await first;
  const next=worker.call({...options,dataDir:'second-profile'},'accounts',{action:'list'});
  await tick();assert.ok(children[0].ended);assert.equal(children.length,1);
  children[0].emit('close',0);await tick();assert.equal(children.length,2);
  assert.equal(children[1].args.at(-1),'second-profile');
  children[1].reply({profile:'second'});assert.equal((await next).profile,'second');
  worker.close();children[1].emit('close',0);
});
