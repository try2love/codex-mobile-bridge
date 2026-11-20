'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const main=path.resolve(__dirname,'../desktop/main.cjs');
const requireMain=require('node:module').createRequire(main);

// Exercise the real controller with a virtual helper and clock. No app, files,
// gateway, or actual update process is started by these failure-path tests.
function controller({ready='valid',readyAt=0,pending=false,spawnError=false,exited=false,platform='win32',packaged=true,environment={},execPath='/fixture/app/Bridge.exe',userData='/fixture/user',fixtureFiles=false,confirm=async()=>({response:0}),shellError=''}={}){
  let now=0;
  const calls={exit:[],quit:0,closed:0,managementClosed:0,workers:[],dialogs:[],opened:[],revealed:[]},timers=[],intervals=[],handlers=new Map();
  const app={isPackaged:packaged,setPath(){},requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},
    getVersion:()=> '2.0.0-preview.3',getPath:()=>userData,exit:code=>calls.exit.push(code),quit:()=>calls.quit++};
  const context=vm.createContext({__dirname:path.dirname(main),
    process:{env:environment,platform,arch:'x64',pid:123,execPath,resourcesPath:path.join(userData,'resources')},
    Date:{now:()=>now},
    setTimeout(fn,ms){
      timers.push(ms);
      if(ms===100)queueMicrotask(()=>{now+=ms;fn();});
      return {unref(){}};
    },
    setInterval(fn,ms){intervals.push(ms);return {unref(){}};},
    require(name){
      if(name==='electron')return {app,ipcMain:{handle:(name,handler)=>handlers.set(name,handler)},
        dialog:{showMessageBox:async(parent,options)=>{calls.dialogs.push(options);return confirm();}},
        shell:{openExternal:async url=>calls.opened.push(url),openPath:async file=>{calls.opened.push(file);return shellError;},showItemInFolder:file=>calls.revealed.push(file)}};
      if(name==='./features/updates/mac-handoff.cjs')return {...requireMain(name),relaunchMacUpdate(){}};
      if(name==='./features/connections/qr.cjs')return {};
      if(name==='node:crypto')return {randomUUID:()=> 'verified-token'};
      if(name==='node:fs')return fixtureFiles?fs:{mkdirSync(){},writeFileSync(){},renameSync(){},openSync:()=>0,closeSync(){},
        readFileSync(file){
          if(file===path.join(path.dirname(main),'features/updates/update-public-key.pem'))return fs.readFileSync(file);
          if(ready==='missing'||now<readyAt)throw Error('ENOENT');
          return ready==='malformed'?'{':JSON.stringify({token:ready==='valid'?'verified-token':'wrong-token'});
        }};
      if(name==='node:child_process')return {spawn:()=>({pid:456,exitCode:exited?1:null,unref(){},
        on(event,fn){if(spawnError&&event==='error')fn(Error('helper spawn failed'));}})};
      if(name==='./shared/controller.cjs')return {createSnapshotWorker:()=>({close(){calls.closed++;}}),createManagementWorker:()=>({close(){calls.managementClosed++;}}),workerFor:()=>({}),
        runWorker:async(options,action,payload)=>{calls.workers.push({action,payload});return {helper:'/fixture/helper',plan:'/fixture/transaction/plan.json'};}};
      return requireMain(name);
    }});
  vm.runInContext(fs.readFileSync(main,'utf8'),context);
  vm.runInContext("dataDir='/fixture/data'",context);
  if(pending)vm.runInContext('snapshotPending=new Promise(()=>{})',context);
  return {context,calls,timers,intervals,handlers,register(updater){context.fixtureUpdater=updater;vm.runInContext("updater=fixtureUpdater;window={webContents:{mainFrame:{url:entry}}}",context);context.register();},
    invoke(name,payload){return handlers.get(name)(vm.runInContext('({sender:window.webContents,senderFrame:window.webContents.mainFrame})',context),payload);},
    run:()=>context.installUpdate({archive:'verified.zip',asset:{sha256:'verified'},version:'2.0.0-preview.2'})};
}

for(const platform of ['darwin','win32','linux'])for(const packaged of [true,false]){
  test(`real updater setup respects ${platform} packaged=${packaged}`,()=>{
    const f=controller({platform,packaged});f.context.setupUpdater();
    const updater=vm.runInContext('updater',f.context),supported=packaged;
    assert.ok(updater instanceof require('../desktop/features/updates/updater.cjs').Updater);
    assert.equal(updater.supported,supported);
    assert.equal(updater.status().state,supported?'idle':'unsupported');
    assert.equal(updater.current,'2.0.0-preview.3');
    assert.equal(updater.directory,path.join('/fixture/user','updates'));
    assert.deepEqual(updater.key,fs.readFileSync(path.join(path.dirname(main),'features/updates/update-public-key.pem')));
    assert.equal(updater.apply,f.context.installUpdate);
    assert.equal(typeof updater.preflight,platform==='win32'?'function':'undefined');
    assert.equal(typeof updater.fetch,'function');
    assert.deepEqual(f.timers,[5000]);assert.deepEqual(f.intervals,[6*60*60*1000]);
  });
}

test('Windows download preflight uses the current installation through the private worker',async()=>{
  const f=controller();f.context.setupUpdater();
  await vm.runInContext('updater.preflight()',f.context);
  assert.equal(f.calls.workers[0].action,'update-preflight');
  assert.equal(f.calls.workers[0].payload.target,path.dirname('/fixture/app/Bridge.exe'));
  assert.deepEqual(f.calls.exit,[]);
});

test('selected channel crosses authorized IPC and confirmations default to cancel without installation',async()=>{
  let checked,installed=0;
  const value={candidateId:'checked',current:'2.2.2-preview.2',version:'2.2.1',intent:'return-stable',requiresConfirmation:true};
  const f=controller();f.register({status:()=>value,check:options=>{checked=options;},install:async()=>{installed++;}});
  await f.invoke('bridge:check-update',{channel:'stable'});assert.equal(checked.channel,'stable');assert.equal(checked.manual,true);
  await assert.rejects(f.invoke('bridge:install-update',{candidateId:'old'}),/检查/);assert.equal(f.calls.dialogs.length,0);
  await f.invoke('bridge:install-update',{candidateId:'checked'});assert.equal(installed,0);
  assert.equal(f.calls.dialogs[0].defaultId,0);assert.equal(f.calls.dialogs[0].cancelId,0);assert.match(f.calls.dialogs[0].detail,/2\.2\.2-preview\.2 → 2\.2\.1/);
});
test('confirmation forwards the checked identity and rechecks writes after the asynchronous dialog',async()=>{
  let installed,release;
  const value={candidateId:'checked',current:'2.2.1',version:'2.2.2-preview.2',intent:'opt-in-preview',requiresConfirmation:true};
  const f=controller({confirm:()=>new Promise(resolve=>{release=resolve;})});
  f.register({status:()=>value,install:async options=>{installed=options;}});
  const pending=f.invoke('bridge:install-update',{candidateId:'checked'});await new Promise(setImmediate);
  await assert.rejects(f.invoke('bridge:install-update',{candidateId:'checked'}),/当前操作/);
  vm.runInContext('workerWrites=1',f.context);release({response:1});await assert.rejects(pending,/当前操作/);assert.equal(installed,undefined);
  vm.runInContext('workerWrites=0',f.context);const again=f.invoke('bridge:install-update',{candidateId:'checked'});release({response:1});await again;
  assert.equal(installed.candidateId,'checked');assert.equal(installed.confirmed,true);
});
test('Linux verified DEB handoff backs up first and never exits or claims completed installation',async()=>{
  const f=controller({platform:'linux'}),candidate={archive:'/private/verified.deb',version:'2.2.2-preview.2',asset:{name:'Bridge.deb'},transition:{intent:'update'}};
  assert.equal((await f.context.installUpdate(candidate)).state,'installer-handoff');
  assert.equal(f.calls.workers[0].action,'update-backup');assert.equal(f.calls.workers[0].payload.transition,candidate.transition);
  assert.deepEqual(f.calls.opened,[candidate.archive]);assert.deepEqual(f.calls.exit,[]);assert.equal(f.calls.closed,0);
  const failed=controller({platform:'linux',shellError:'No installer'});assert.equal((await failed.context.installUpdate(candidate)).state,'downloaded');
  assert.deepEqual(failed.calls.revealed,[candidate.archive]);
  const appimage=controller({platform:'linux'});candidate.asset.name='Bridge.AppImage';assert.equal((await appimage.context.installUpdate(candidate)).state,'downloaded');
  assert.deepEqual(appimage.calls.opened,[]);assert.deepEqual(appimage.calls.revealed,[candidate.archive]);
});
test('legacy Linux release fallback only opens its fixed release page without backup or system install',async()=>{
  const f=controller({platform:'linux'}),candidate={manualRelease:true,releasePage:'https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.1'};
  assert.equal((await f.context.installUpdate(candidate)).state,'manual-release');assert.deepEqual(f.calls.opened,[candidate.releasePage]);
  assert.deepEqual(f.calls.workers,[]);assert.deepEqual(f.calls.exit,[]);
});

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

test('only the matching unfinished update binds the GUI data directory and acknowledges startup',async t=>{
  const scratch=path.resolve(__dirname,'..','.tmp');fs.mkdirSync(scratch,{recursive:true});
  const root=fs.mkdtempSync(path.join(scratch,'cmb-handoff-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const target=path.join(root,'Bridge Test.app'),execPath=path.join(target,'Contents','MacOS','Bridge');
  const transaction=path.join(root,'.cmb-update-a'),data=path.join(root,'expected'),explicit=path.join(root,'other');
  fs.mkdirSync(transaction);
  const token='test-token',version='2.0.0-preview.3',plan={target,dataDir:data,version,token};
  const write=(file,row)=>fs.writeFileSync(path.join(transaction,file),JSON.stringify(row));
  const env={CMB_UPDATE_TRANSACTION:transaction,CMB_UPDATE_TOKEN:token,CMB_UPDATE_DATA_DIR:data};
  function launch(changes={}){
    const {context}=controller({platform:'darwin',environment:{CMB_DATA_DIR:explicit,...env,...changes},execPath,userData:root,fixtureFiles:true});
    vm.runInContext('dataDir=loadDataDir();worker=async()=>({});',context);
    return context;
  }
  const directory=c=>vm.runInContext('dataDir',c),ack=path.join(transaction,'ack.json');
  write('ready.json',{token});
  for(const invalid of [{target:path.join(root,'Bridge.app')},{token:'stale'},{version:'1.0.0'},{dataDir:explicit}]){
    write('plan.json',{...plan,...invalid});
    const c=launch();assert.equal(directory(c),explicit);assert.equal(c.updateManaged(),false);
    await c.acknowledgeUpdate();assert.equal(fs.existsSync(ack),false);
  }
  write('plan.json',plan);
  write('launched.json',{pid:99});
  const foreign=launch();assert.equal(directory(foreign),explicit);assert.equal(foreign.updateManaged(),false);
  fs.unlinkSync(path.join(transaction,'launched.json'));
  const valid=launch();assert.equal(directory(valid),data);assert.equal(valid.updateManaged(),true);
  write('launched.json',{pid:123});await valid.acknowledgeUpdate();
  assert.deepEqual(JSON.parse(fs.readFileSync(ack,'utf8')),{token,version,dataDir:data});fs.unlinkSync(ack);
  valid.worker=async()=>write('result.json',{state:'failed'});
  await valid.acknowledgeUpdate();assert.equal(fs.existsSync(ack),false);assert.equal(valid.updateManaged(),false);
  const finished=launch();assert.equal(directory(finished),explicit);assert.equal(finished.updateManaged(),false);
  const saved=path.join(root,'saved-test');
  fs.writeFileSync(path.join(root,'bridge-location.json'),JSON.stringify({dataDir:saved}));
  assert.equal(directory(launch({CMB_DATA_DIR:undefined})),saved);
  const rollback=launch({CMB_UPDATE_TRANSACTION:undefined,CMB_UPDATE_TOKEN:undefined});
  assert.equal(directory(rollback),data);assert.equal(rollback.updateManaged(),false);await rollback.acknowledgeUpdate();assert.equal(fs.existsSync(ack),false);
});
