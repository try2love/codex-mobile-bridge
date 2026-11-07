'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
test('QR clocks exist only for visible live grants and rotated addresses revoke their grant',async()=>{
  const timers=new Map(),calls=[],listeners={};let now=10000,id=0;
  const document={hidden:false,addEventListener(name,fn){listeners[name]=fn;}};
  const ctx=vm.createContext({document,t:s=>s,Date:{now:()=>now},
    setInterval(fn,ms){timers.set(++id,{fn,ms});return id;},clearInterval:key=>timers.delete(key),
    api:{async pairing(request){calls.push(request);return request.action==='create'
      ?{id:'fixture',expires:now/1000+5,state:'active',image:'data:fixture'}:{states:{fixture:'used'}};}}});
  vm.runInContext(fs.readFileSync(require.resolve('../desktop/features/connections/pairing.js'),'utf8'),ctx);
  const run=src=>vm.runInContext(src,ctx);
  run("const old='https://old.test',entry={url:old,node:{open:true},running:true,generation:0,image:{removeAttribute(){}},summary:{},error:{},status:{},hint:{},privacy:{},refresh:{}};qrEntries.set(old,entry);");
  assert.equal(timers.size,0);await run('refreshPairing(entry)');assert.equal(timers.size,2);
  document.hidden=true;listeners.visibilitychange();assert.equal(timers.size,0);
  document.hidden=false;listeners.visibilitychange();assert.equal(timers.size,2);
  assert.equal(calls.filter(v=>v.action==='create').length,1);
  const tick=[...timers.values()].find(x=>x.ms===1000);now+=5500;tick.fn();
  assert.equal(timers.size,0);assert.match(run('entry.status.textContent'),/过期/);
  await run('refreshPairing(entry)');assert.equal(timers.size,2);
  run("prunePairing(['https://next.test']);");
  assert.equal(timers.size,0);assert.equal(run('qrEntries.size'),0);
  assert.equal(calls.at(-1).action,'revoke');
});
