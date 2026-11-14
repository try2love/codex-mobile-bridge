'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {activeUpdateHandoff}=require('../desktop/features/updates/updater.cjs');
const {relaunchMacUpdate,restoreMacUpdate}=require('../desktop/features/updates/mac-handoff.cjs');
function fixture(t){
 const root=fs.mkdtempSync(path.resolve('.tmp/mac-update-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const target=path.join(root,'Test.app'),transaction=path.join(root,'.cmb-update-test'),data=path.join(root,'data');fs.mkdirSync(transaction);
 const token='fixture-token',version='2.2.1',env={CMB_UPDATE_TRANSACTION:transaction,CMB_UPDATE_DATA_DIR:data,CMB_UPDATE_TOKEN:token};
 const write=(name,value)=>fs.writeFileSync(path.join(transaction,name),JSON.stringify(value));
 write('plan.json',{target,dataDir:data,token,version,runtime:{running:true}});write('ready.json',{token});write('launched.json',{pid:101});
 const runtime={platform:'darwin',pid:101,execPath:path.join(target,'Contents/MacOS/Test'),resourcesPath:path.join(target,'Contents/Resources'),env};
 const app={isPackaged:true,getVersion:()=>version};
 const read=(pid=101)=>activeUpdateHandoff(env,target,version,pid);
 return {root,target,transaction,token,version,env,write,runtime,app,read};
}
test('only the verified macOS update direct child replaces itself before opening the UI',t=>{
 const f=fixture(t),calls=[];f.runtime.execve=(...args)=>calls.push(args);
 relaunchMacUpdate({app:f.app,process:f.runtime});assert.equal(calls.length,1);
 assert.match(calls[0][0],/client-helpers\/update-launcher$/);assert.deepEqual(calls[0][1],[calls[0][0],f.target,f.transaction]);assert.equal(calls[0][2],f.env);
 for(const alter of [()=>f.runtime.platform='win32',()=>{f.runtime.platform='darwin';f.env.CMB_UPDATE_TOKEN='wrong';},()=>{f.env.CMB_UPDATE_TOKEN=f.token;f.app.isPackaged=false;}]){
  alter();relaunchMacUpdate({app:f.app,process:f.runtime});assert.equal(calls.length,1);
 }
});
test('Launch Services child must match both original supervisor and actual GUI receipt',t=>{
 const f=fixture(t);f.env.CMB_UPDATE_LAUNCHER_PID='101';
 assert.ok(f.read(202),'allow launch completion to publish the real PID before acknowledgement');assert.equal(f.read(),null);
 f.write('launch-services.json',{pid:202,launcherPid:101,token:f.token});assert.ok(f.read(202));assert.equal(f.read(303),null);
 for(const row of [{pid:202,launcherPid:999,token:f.token},{pid:202,launcherPid:101,token:'wrong'}]){f.write('launch-services.json',row);assert.equal(f.read(202),null);}
 for(const value of ['x','101x','-1','0','9007199254740992']){f.env.CMB_UPDATE_LAUNCHER_PID=value;assert.equal(f.read(202),null);}
});
test('GUI restores its gateway before acknowledging the legacy updater, without duplicate start',async t=>{
 const f=fixture(t);f.env.CMB_UPDATE_LAUNCHER_PID='101';f.write('launch-services.json',{pid:202,launcherPid:101,token:f.token});
 let snapshots=0;const calls=[];
 await restoreMacUpdate({handoff:f.read(202),env:f.env,pid:202,stillManaged:()=>!!f.read(202),worker:async action=>{calls.push(action);return {runtime:{running:action==='snapshot'&&++snapshots===2}};}});
 assert.deepEqual(calls,['start','snapshot','snapshot']);
});
test('stopped gateway stays stopped and an invalidated handoff cannot start a gateway',async t=>{
 const f=fixture(t);f.env.CMB_UPDATE_LAUNCHER_PID='101';f.write('launch-services.json',{pid:202,launcherPid:101,token:f.token});
 const handoff=f.read(202),calls=[];const options={handoff,env:f.env,pid:202,stillManaged:()=>false,worker:async action=>calls.push(action)};
 await assert.rejects(restoreMacUpdate(options),/失效/);assert.deepEqual(calls,[]);
 const plan=JSON.parse(fs.readFileSync(path.join(f.transaction,'plan.json')));plan.runtime.running=false;f.write('plan.json',plan);
 await restoreMacUpdate({...options,stillManaged:()=>true});assert.deepEqual(calls,[]);
});
test('missing launch receipt does not permit a gateway start or acknowledgement',async t=>{
 const f=fixture(t);f.env.CMB_UPDATE_LAUNCHER_PID='101';let calls=0;
 await assert.rejects(restoreMacUpdate({handoff:f.read(202),env:f.env,pid:202,stillManaged:()=>false,worker:async()=>calls++}),/尚未确认/);assert.equal(calls,0);
});
