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
test('updater startup removes incomplete downloads and keeps all complete package versions',async t=>{
  const scratch=path.resolve(__dirname,'../.tmp');fs.mkdirSync(scratch,{recursive:true});
  const directory=fs.mkdtempSync(path.join(scratch,'update-cache-startup-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const updates=path.join(directory,'updates');fs.mkdirSync(updates);
  const names=['1.0.0','2.0.0-preview.3','2.2.2-preview.3'].map(version=>`verified-${version}-darwin-arm64-${'a'.repeat(64)}.zip`);
  for(const name of names)fs.writeFileSync(path.join(updates,name),'package');
  fs.mkdirSync(path.join(updates,'download-stale'));fs.writeFileSync(path.join(updates,'download-stale','package.tmp'),'partial');
  const f=controller({userData:directory,fixtureFiles:true});f.context.setupUpdater();await vm.runInContext('startupCleanup',f.context);
  assert.deepEqual(fs.readdirSync(updates).sort(),names.sort());
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
test('history and download IPC expose only the authorized opaque selection contract',async()=>{
  let selected,listed=0,downloaded=0;
  const f=controller(),value={candidateId:'historical',current:'2.2.2',version:'2.2.2-preview.3',intent:'switch-version',requiresConfirmation:true};
  f.register({status:()=>value,listVersions:async()=>{listed++;return {releases:[]};},selectVersion:async id=>{selected=id;return value;},download:async({candidateId})=>{assert.equal(candidateId,'historical');downloaded++;return {state:'downloaded'};}});
  for(const action of ['bridge:update-versions','bridge:download-update'])await assert.rejects(f.handlers.get(action)({sender:{},senderFrame:{}},{}),/不允许/);
  await f.invoke('bridge:update-versions',{action:'list'});assert.equal(listed,1);
  await f.invoke('bridge:update-versions',{action:'select',id:'opaque-asset'});assert.equal(selected,'opaque-asset');
  await assert.rejects(f.invoke('bridge:update-versions',{action:'install',url:'https://example.invalid'}),/请求无效/);
  await assert.rejects(f.invoke('bridge:download-update',{candidateId:'stale'}),/检查/);
  assert.equal((await f.invoke('bridge:download-update',{candidateId:'historical'})).state,'downloaded');
  assert.equal(downloaded,1);assert.equal(f.calls.dialogs.length,0);assert.deepEqual(f.calls.workers,[]);assert.deepEqual(f.calls.exit,[]);
});
test('download holds the shared cache lock while startup cleanup is pending',async()=>{
  let release,downloaded=0;const f=controller();
  f.context.fixtureCleanup=new Promise(resolve=>{release=resolve;});vm.runInContext('startupCleanup=fixtureCleanup',f.context);
  f.register({status:()=>({candidateId:'selected'}),download:async()=>{downloaded++;return {state:'downloaded'};}});
  const pending=f.invoke('bridge:download-update',{candidateId:'selected'});
  await assert.rejects(f.invoke('bridge:update-cache',{action:'delete',ids:['ignored']}),/更新完成/);
  await assert.rejects(f.invoke('bridge:install-update',{candidateId:'selected'}),/当前操作/);
  await assert.rejects(f.invoke('bridge:update-versions',{action:'select',id:'other'}),/当前操作/);
  release();await pending;assert.equal(downloaded,1);
});
test('arbitrary version switching confirms exact target and distinguishes manual installation',async()=>{
  for(const manualInstall of [false,true]){
    let installed=0;const f=controller({confirm:async()=>({response:1})});
    const value={candidateId:'selected',current:'2.2.2',version:'2.2.2-preview.3',assetName:'selected-package',intent:'switch-version',requiresConfirmation:true,manualInstall};
    f.register({status:()=>value,install:async options=>{assert.equal(options.confirmed,true);assert.equal(options.candidateId,'selected');installed++;}});
    await f.invoke('bridge:install-update',{candidateId:'selected'});
    const dialog=f.calls.dialogs[0];assert.equal(dialog.defaultId,0);assert.equal(dialog.cancelId,0);
    assert.match(dialog.message,/切换到所选版本/);assert.match(dialog.detail,/2\.2\.2 → 2\.2\.2-preview\.3/);assert.match(dialog.detail,/selected-package/);
    assert.match(dialog.detail,manualInstall?/系统安装器/:/备份网关配置/);assert.equal(installed,1);
  }
});
test('manual historical installers back up before handoff and never claim the application restarted',async()=>{
  for(const [platform,extension] of [['darwin','dmg'],['win32','exe'],['linux','deb'],['darwin','zip'],['linux','AppImage']]){
    const f=controller({platform}),archive='verified.'+extension;
    const candidate={manualInstall:true,archive,version:'1.0.0',asset:{name:'package.'+extension},transition:{intent:'switch-version',from:'2.2.2-preview.3',to:'1.0.0',dataFormat:'bridge-data-v1'}};
    const outcome=await f.context.installUpdate(candidate);
    assert.equal(f.calls.workers[0].action,'update-backup');assert.equal(f.calls.workers[0].payload.manualInstall,true);
    assert.equal(f.calls.workers[0].payload.transition,candidate.transition);
    const reveal=['zip','AppImage'].includes(extension);
    assert.equal(outcome.state,reveal?'downloaded':'installer-handoff');
    assert.deepEqual(reveal?f.calls.revealed:f.calls.opened,[archive]);assert.deepEqual(f.calls.exit,[]);
  }
});
function cacheController(t,options={}){
  const {Updater}=require('../desktop/features/updates/updater.cjs'),scratch=path.resolve(__dirname,'../.tmp');fs.mkdirSync(scratch,{recursive:true});
  const directory=fs.mkdtempSync(path.join(scratch,'update-cache-controller-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const f=controller(options),updater=new Updater({current:'2.2.1',directory,install:async()=>{}});
  const ids=['2.2.1','2.2.2-preview.3'].map(version=>`verified-${version}-darwin-arm64-${'a'.repeat(64)}.zip`);
  ids.forEach((id,index)=>fs.writeFileSync(path.join(directory,id),'x'.repeat(index+3)));
  updater.set('available',{candidateId:'checked'});f.register(updater);
  return {...f,updater,ids,directory};
}
test('cache IPC uses the authorized preload API and refuses another sender',async t=>{
  let api;const calls=[];vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../desktop/preload.cjs'),'utf8'),{process:{platform:'darwin'},require:()=>({
    contextBridge:{exposeInMainWorld(name,value){assert.equal(name,'bridgeDesktop');api=value;}},ipcRenderer:{invoke(...args){calls.push(args);}}})});
  const payload={action:'list'};api.updateCache(payload);assert.deepEqual(calls,[['bridge:update-cache',payload]]);
  const f=cacheController(t);
  await assert.rejects(f.handlers.get('bridge:update-cache')({sender:{},senderFrame:{}},payload),/不允许/);
  await assert.rejects(f.invoke('bridge:update-cache',{action:'install',id:f.ids[0]}),/请求无效/);
});
test('cache cancellation defaults to cancel and returns the unchanged idle list',async t=>{
  const f=cacheController(t),before=await f.invoke('bridge:update-cache',{action:'list'});
  const result=await f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[0]]});
  assert.equal(result.cancelled,true);assert.deepEqual(result.entries,before.entries);assert.equal(result.totalBytes,before.totalBytes);
  assert.equal(f.calls.dialogs[0].defaultId,0);assert.equal(f.calls.dialogs[0].cancelId,0);assert.equal(f.calls.dialogs[0].noLink,true);
  assert.match(f.calls.dialogs[0].detail,/1/);assert.match(f.calls.dialogs[0].detail,/MB/);assert.equal(fs.readFileSync(path.join(f.directory,f.ids[0]),'utf8'),'xxx');
});
test('cache confirmation deletes only checked packages and never accepts arbitrary paths',async t=>{
  const f=cacheController(t,{confirm:async()=>({response:1})});
  for(const ids of [['../'+f.ids[0]],['accounts.json'],[path.join(f.directory,f.ids[0])]])await assert.rejects(f.invoke('bridge:update-cache',{action:'delete',ids}),/请求无效/);
  assert.equal(f.calls.dialogs.length,0);
  const result=await f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[0]]});
  assert.deepEqual(result.deleted,[f.ids[0]]);assert.equal(result.entries.length,1);assert.equal(result.entries[0].id,f.ids[1]);assert.equal(result.totalBytes,4);
});
test('delete confirmation holds its lock before awaiting and blocks concurrent installation',async t=>{
  let release;const f=cacheController(t,{confirm:()=>new Promise(resolve=>{release=resolve;})});let installed=0;f.updater.install=async()=>{installed++;};
  const deleting=f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[0]]});
  await assert.rejects(f.invoke('bridge:install-update',{candidateId:'checked'}),/当前操作/);
  await assert.rejects(f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[1]]}),/更新完成/);
  assert.ok((await f.invoke('bridge:update-cache',{action:'list'})).entries.every(entry=>entry.inUse));
  await new Promise(setImmediate);release({response:0});await deleting;
  await f.invoke('bridge:install-update',{candidateId:'checked'});assert.equal(installed,1);
});
test('installation holds its lock across startup cleanup and blocks cache deletion',async t=>{
  const f=cacheController(t);let release,installed=0;
  f.context.fixtureCleanup=new Promise(resolve=>{release=resolve;});vm.runInContext('startupCleanup=fixtureCleanup',f.context);
  f.updater.install=async()=>{installed++;};const installing=f.invoke('bridge:install-update',{candidateId:'checked'});
  await assert.rejects(f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[0]]}),/更新完成/);
  assert.ok((await f.invoke('bridge:update-cache',{action:'list'})).entries.every(entry=>entry.inUse));
  release();await installing;assert.equal(installed,1);
});
test('cache deletion is blocked during each update state, confirmation and managed handoff',async t=>{
  for(const state of ['downloading','preparing','restarting']){
    const f=cacheController(t);f.updater.set(state);
    assert.ok((await f.invoke('bridge:update-cache',{action:'list'})).entries.every(entry=>entry.inUse));
    await assert.rejects(f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[0]]}),/更新完成/);assert.equal(f.calls.dialogs.length,0);
  }
  for(const blocker of ['updateConfirmation=true','updateManaged=()=>true']){
    const f=cacheController(t);vm.runInContext(blocker,f.context);
    assert.ok((await f.invoke('bridge:update-cache',{action:'list'})).entries.every(entry=>entry.inUse));
    await assert.rejects(f.invoke('bridge:update-cache',{action:'delete',ids:[f.ids[0]]}),/更新完成/);
  }
});
test('confirmation rechecks update ownership and selected file identity before any deletion',async t=>{
  for(const change of ['file','handoff','directory']){
    let release;const f=cacheController(t,{confirm:()=>new Promise(resolve=>{release=resolve;})});
    const deleting=f.invoke('bridge:update-cache',{action:'delete',ids:f.ids});await new Promise(setImmediate);
    if(change==='file')fs.writeFileSync(path.join(f.directory,f.ids[1]),'replaced package');
    else if(change==='directory'){fs.unlinkSync(path.join(f.directory,f.ids[1]));fs.mkdirSync(path.join(f.directory,f.ids[1]));}
    else vm.runInContext('updateManaged=()=>true',f.context);
    release({response:1});await assert.rejects(deleting,/已变化|更新完成/);assert.equal(fs.readFileSync(path.join(f.directory,f.ids[0]),'utf8'),'xxx');
  }
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
test('legacy Linux manual release requires confirmation and a successful backup before opening the official page',async()=>{
  let response=0,finishBackup;
  const f=controller({platform:'linux',confirm:async()=>({response})}),candidate={manualRelease:true,version:'2.2.1',
    releasePage:'https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.1',
    transition:{intent:'return-stable',from:'2.2.2-preview.2',to:'2.2.1',dataFormat:'bridge-data-v1'}};
  const value={candidateId:'checked',current:candidate.transition.from,version:candidate.version,intent:'return-stable',manualRelease:true,requiresConfirmation:true};
  f.register({status:()=>value,install:async options=>{assert.equal(options.candidateId,'checked');assert.equal(options.confirmed,true);return f.context.installUpdate(candidate);}});
  f.context.backupPending=new Promise(resolve=>{finishBackup=resolve;});
  vm.runInContext("worker=async(action,payload)=>{if(action!=='update-backup')throw Error('Unexpected action');globalThis.backupPayload=payload;return backupPending;}",f.context);
  await f.invoke('bridge:install-update',{candidateId:'checked'});assert.equal(f.context.backupPayload,undefined);assert.deepEqual(f.calls.opened,[]);
  response=1;const installing=f.invoke('bridge:install-update',{candidateId:'checked'});await new Promise(setImmediate);
  assert.deepEqual(f.calls.opened,[]);assert.equal(f.context.backupPayload.version,candidate.version);assert.equal(f.context.backupPayload.transition,candidate.transition);
  finishBackup({backup:'/private/backup',files:3});assert.equal((await installing).state,'manual-release');
  assert.deepEqual(f.calls.opened,[candidate.releasePage]);assert.deepEqual(f.calls.exit,[]);assert.equal(f.calls.closed,0);
});
test('Linux manual release backup failure blocks the official page and any system installer',async()=>{
  const f=controller({platform:'linux',confirm:async()=>({response:1})}),candidate={manualRelease:true,version:'2.2.1',releasePage:'https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.1',transition:{intent:'return-stable'}};
  f.register({status:()=>({candidateId:'checked',current:'2.2.2-preview.2',version:'2.2.1',intent:'return-stable',manualRelease:true,requiresConfirmation:true}),install:()=>f.context.installUpdate(candidate)});
  vm.runInContext("worker=async()=>{throw Error('backup failed');}",f.context);
  await assert.rejects(f.invoke('bridge:install-update',{candidateId:'checked'}),/backup failed/);
  assert.deepEqual(f.calls.opened,[]);assert.deepEqual(f.calls.revealed,[]);assert.deepEqual(f.calls.exit,[]);
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
  const cached=path.join(root,'updates','verified-2.0.0-preview.3-darwin-arm64-'+'a'.repeat(64)+'.zip');
  fs.mkdirSync(path.dirname(cached));fs.writeFileSync(cached,'retained package');
  write('launched.json',{pid:123});await valid.acknowledgeUpdate();
  assert.deepEqual(JSON.parse(fs.readFileSync(ack,'utf8')),{token,version,dataDir:data});assert.equal(fs.readFileSync(cached,'utf8'),'retained package');fs.unlinkSync(ack);
  valid.worker=async()=>write('result.json',{state:'failed'});
  await valid.acknowledgeUpdate();assert.equal(fs.existsSync(ack),false);assert.equal(valid.updateManaged(),false);assert.equal(fs.readFileSync(cached,'utf8'),'retained package');
  const finished=launch();assert.equal(directory(finished),explicit);assert.equal(finished.updateManaged(),false);
  const saved=path.join(root,'saved-test');
  fs.writeFileSync(path.join(root,'bridge-location.json'),JSON.stringify({dataDir:saved}));
  assert.equal(directory(launch({CMB_DATA_DIR:undefined})),saved);
  const rollback=launch({CMB_UPDATE_TRANSACTION:undefined,CMB_UPDATE_TOKEN:undefined});
  assert.equal(directory(rollback),data);assert.equal(rollback.updateManaged(),false);await rollback.acknowledgeUpdate();assert.equal(fs.existsSync(ack),false);
});
