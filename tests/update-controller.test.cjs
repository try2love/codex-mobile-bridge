'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const main=path.resolve(__dirname,'../desktop/main.cjs');
const requireMain=require('node:module').createRequire(main);

// Exercise the real controller with a virtual helper and clock. No app, files,
// gateway, or actual update process is started by these failure-path tests.
function controller({ready='valid',readyAt=0,pending=false,spawnError=false,exited=false,platform='win32',packaged=true}={}){
  let now=0;
  const calls={exit:[],quit:0,closed:0,managementClosed:0},timers=[],intervals=[];
  const app={isPackaged:packaged,requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},
    getVersion:()=> '2.0.0-preview.3',getPath:()=>'/fixture/user',exit:code=>calls.exit.push(code),quit:()=>calls.quit++};
  const context=vm.createContext({__dirname:path.dirname(main),
    process:{env:{},platform,arch:'x64',pid:123,execPath:'/fixture/app/Bridge.exe'},
    Date:{now:()=>now},
    setTimeout(fn,ms){
      timers.push(ms);
      if(ms===100)queueMicrotask(()=>{now+=ms;fn();});
      return {unref(){}};
    },
    setInterval(fn,ms){intervals.push(ms);return {unref(){}};},
    require(name){
      if(name==='electron')return {app};
      if(name==='./features/connections/qr.cjs')return {};
      if(name==='node:crypto')return {randomUUID:()=> 'verified-token'};
      if(name==='node:fs')return {mkdirSync(){},writeFileSync(){},openSync:()=>0,closeSync(){},
        readFileSync(file){
          if(file===path.join(path.dirname(main),'features/updates/update-public-key.pem'))return fs.readFileSync(file);
          if(ready==='missing'||now<readyAt)throw Error('ENOENT');
          return ready==='malformed'?'{':JSON.stringify({token:ready==='valid'?'verified-token':'wrong-token'});
        }};
      if(name==='node:child_process')return {spawn:()=>({pid:456,exitCode:exited?1:null,unref(){},
        on(event,fn){if(spawnError&&event==='error')fn(Error('helper spawn failed'));}})};
      if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({close(){calls.closed++;}}),createManagementWorker:()=>({close(){calls.managementClosed++;}}),workerFor:()=>({}),
        runWorker:async()=>({helper:'/fixture/helper',plan:'/fixture/transaction/plan.json'})};
      return requireMain(name);
    }});
  vm.runInContext(fs.readFileSync(main,'utf8'),context);
  vm.runInContext("dataDir='/fixture/data'",context);
  if(pending)vm.runInContext('snapshotPending=new Promise(()=>{})',context);
  return {context,calls,timers,intervals,run:()=>context.installUpdate({archive:'verified.zip',asset:{sha256:'verified'},version:'2.0.0-preview.2'})};
}

for(const platform of ['darwin','win32','linux'])for(const packaged of [true,false]){
  test(`real updater setup respects ${platform} packaged=${packaged}`,()=>{
    const f=controller({platform,packaged});f.context.setupUpdater();
    const updater=vm.runInContext('updater',f.context),supported=packaged&&platform!=='linux';
    assert.ok(updater instanceof require('../desktop/features/updates/updater.cjs').Updater);
    assert.equal(updater.supported,supported);
    assert.equal(updater.status().state,supported?'idle':'unsupported');
    assert.equal(updater.current,'2.0.0-preview.3');
    assert.equal(updater.directory,path.join('/fixture/user','updates'));
    assert.deepEqual(updater.key,fs.readFileSync(path.join(path.dirname(main),'features/updates/update-public-key.pem')));
    assert.equal(updater.apply,f.context.installUpdate);
    assert.equal(typeof updater.fetch,'function');
    assert.deepEqual(f.timers,[5000]);assert.deepEqual(f.intervals,[6*60*60*1000]);
  });
}

test('verified helper exits immediately even with a stalled snapshot and cancellable quit',async()=>{
  const f=controller({pending:true});
  let timer;
  try{
    await Promise.race([f.run(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('controller waited for snapshot')),100);})]);
  }finally{clearTimeout(timer);}
  assert.deepEqual(f.calls.exit,[0]);assert.equal(f.calls.quit,0);
  assert.equal(f.calls.managementClosed,1);
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
