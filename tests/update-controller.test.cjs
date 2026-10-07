'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const main=path.resolve(__dirname,'../desktop/main.cjs');
const requireMain=require('node:module').createRequire(main);

// Exercise the real controller with a virtual helper and clock. No app, files,
// gateway, or actual update process is started by these failure-path tests.
function controller({ready='valid',readyAt=0,pending=false,spawnError=false,exited=false}={}){
  let now=0;
  const calls={exit:[],quit:0,closed:0},timers=[];
  const app={isPackaged:true,requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},
    getPath:()=>'/fixture/user',exit:code=>calls.exit.push(code),quit:()=>calls.quit++};
  const context=vm.createContext({__dirname:path.dirname(main),
    process:{env:{},platform:'win32',arch:'x64',pid:123,execPath:'/fixture/app/Bridge.exe'},
    Date:{now:()=>now},
    setTimeout(fn,ms){
      timers.push(ms);
      if(ms===100)queueMicrotask(()=>{now+=ms;fn();});
      return timers.length;
    },
    require(name){
      if(name==='electron')return {app};
      if(name==='./qr.cjs')return {};
      if(name==='node:crypto')return {randomUUID:()=> 'verified-token'};
      if(name==='node:fs')return {mkdirSync(){},writeFileSync(){},openSync:()=>0,closeSync(){},
        readFileSync(){
          if(ready==='missing'||now<readyAt)throw Error('ENOENT');
          return ready==='malformed'?'{':JSON.stringify({token:ready==='valid'?'verified-token':'wrong-token'});
        }};
      if(name==='node:child_process')return {spawn:()=>({pid:456,exitCode:exited?1:null,unref(){},
        on(event,fn){if(spawnError&&event==='error')fn(Error('helper spawn failed'));}})};
      if(name==='./controller.cjs')return {createSnapshotWorker:()=>({close(){calls.closed++;}}),workerFor:()=>({}),
        runWorker:async()=>({helper:'/fixture/helper',plan:'/fixture/transaction/plan.json'})};
      return requireMain(name);
    }});
  vm.runInContext(fs.readFileSync(main,'utf8'),context);
  vm.runInContext("dataDir='/fixture/data'",context);
  if(pending)vm.runInContext('snapshotPending=new Promise(()=>{})',context);
  return {context,calls,timers,run:()=>context.installUpdate({archive:'verified.zip',asset:{sha256:'verified'},version:'2.0.0-preview.2'})};
}

test('verified helper exits immediately even with a stalled snapshot and cancellable quit',async()=>{
  const f=controller({pending:true});
  let timer;
  try{
    await Promise.race([f.run(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('controller waited for snapshot')),100);})]);
  }finally{clearTimeout(timer);}
  assert.deepEqual(f.calls.exit,[0]);assert.equal(f.calls.quit,0);
  assert.equal(vm.runInContext('updateQuitting',f.context),true);
});

test('verified helper does not rely on graceful quit or delayed forced exit',async()=>{
  const f=controller();await f.run();
  assert.deepEqual(f.calls.exit,[0]);assert.equal(f.calls.quit,0);assert.deepEqual(f.timers,[]);
});

for(const ready of ['missing','wrong-token','malformed'])test(`helper ${ready} never exits the controller`,async()=>{
  const f=controller({ready});await assert.rejects(f.run(),/无法启动应用更新进程/);
  assert.deepEqual(f.calls.exit,[]);assert.equal(f.calls.quit,0);
  assert.equal(vm.runInContext('updateQuitting',f.context),false);
});

test('slow helper is allowed to become ready after two seconds',async()=>{
  const f=controller({readyAt:3000});await f.run();
  assert.deepEqual(f.calls.exit,[0]);assert.equal(f.calls.quit,0);
  assert.equal(f.timers.filter(ms=>ms===100).length,30);
  assert.ok(f.timers.every(ms=>ms===100));
});

for(const option of ['spawnError','exited'])test(`helper ${option} keeps controller alive`,async()=>{
  const f=controller({[option]:true});await assert.rejects(f.run(),/helper spawn failed|无法启动应用更新进程/);
  assert.deepEqual(f.calls.exit,[]);assert.equal(f.calls.quit,0);
});
