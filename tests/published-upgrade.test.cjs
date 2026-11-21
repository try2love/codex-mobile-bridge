'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process'),path=require('node:path');
const {EventEmitter,once}=require('node:events');
const fs=require('node:fs/promises'),sync=require('node:fs'),vm=require('node:vm');
const {createHash,generateKeyPairSync,sign}=require('node:crypto');
const {stopOwnedGui,transportScript,baselinePolicy}=require('../scripts/smoke-published-upgrade.cjs');
const script=path.join(__dirname,'../scripts/smoke-published-upgrade.cjs');
test('Linux package discovery remains in the installed channel and cannot imply automatic installation',()=>{
  const {verifyUpdateDiscovery}=require('../scripts/smoke-linux.cjs');
  for(const current of ['2.2.1','2.2.2-preview.2']){
    const channel=current.includes('-')?'preview':'stable',update={current,currentChannel:channel,channel};
    for(const state of ['idle','checking','current','available','skipped','error'])verifyUpdateDiscovery({...update,state},current);
    for(const state of ['unsupported','downloading','preparing','restarting','downloaded','installer-handoff'])assert.throws(()=>verifyUpdateDiscovery({...update,state},current));
    assert.throws(()=>verifyUpdateDiscovery({...update,current:'0.0.0',state:'idle'},current));
    assert.throws(()=>verifyUpdateDiscovery({...update,channel:channel==='stable'?'preview':'stable',state:'idle'},current));
  }
});
test('baseline expectations respect old Linux support and published preview manifest availability',()=>{
  for(const platform of ['darwin','win32']){
    assert.deepEqual(baselinePolicy('2.2.1',platform),{state:'current',includeCandidate:true,previewDiscoveryTested:true});
    assert.deepEqual(baselinePolicy('2.2.2-preview.1',platform),{state:'current',includeCandidate:false,previewDiscoveryTested:false});
  }
  for(const version of ['2.2.1','2.2.2-preview.1'])assert.deepEqual(baselinePolicy(version,'linux'),{state:'unsupported',includeCandidate:false,previewDiscoveryTested:false});
  assert.throws(()=>baselinePolicy('2.2.2-preview.2','darwin'),/Unknown official baseline/);
});
test('published-package installer refuses a daily workstation before any install or fixture write',()=>{
  const result=spawnSync(process.execPath,[script],{env:{...process.env,GITHUB_ACTIONS:'false',RUNNER_ENVIRONMENT:'github-hosted'},encoding:'utf8'});
  assert.equal(result.status,1);assert.match(result.stderr,/Only run against an ephemeral GitHub-hosted runner/);
});
test('published-package installer refuses a self-hosted runner',()=>{
  const result=spawnSync(process.execPath,[script],{env:{...process.env,GITHUB_ACTIONS:'true',RUNNER_ENVIRONMENT:'self-hosted'},encoding:'utf8'});
  assert.equal(result.status,1);assert.match(result.stderr,/Self-hosted\/daily machines are excluded/);
});
test('owned GUI signal exit completes without a numeric exit code',async()=>{
  const child=new EventEmitter();Object.assign(child,{exitCode:null,signalCode:null,killed:false});
  child.kill=()=>{child.killed=true;queueMicrotask(()=>{child.signalCode='SIGTERM';child.emit('exit',null,'SIGTERM');});return true;};
  await stopOwnedGui(child,100);
  assert.equal(child.exitCode,null);assert.equal(child.signalCode,'SIGTERM');
  child.kill=()=>{throw Error('An exited process must not be signaled again');};
  await stopOwnedGui(child,100);
});
test('a sent kill signal never substitutes for the owned GUI exit event',async()=>{
  const child=new EventEmitter();Object.assign(child,{exitCode:null,signalCode:null,killed:false});
  child.kill=()=>{child.killed=true;return true;};
  await assert.rejects(stopOwnedGui(child,20),/owned GUI exit timed out/);
  assert.equal(child.killed,true);assert.equal(child.exitCode,null);assert.equal(child.signalCode,null);
  assert.equal(child.listenerCount('exit'),0);
});
test('owned GUI shutdown observes a real spawned process exit',async()=>{
  const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
  try{
    await once(child,'spawn');await stopOwnedGui(child,5000);
    assert.ok(child.exitCode!==null||child.signalCode!==null);
  }finally{if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');}
});
test('acceptance transport runs production signed selection and archive hashes without replacing the key',async t=>{
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-transport-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  for(const folder of ['old','preview1'])await fs.mkdir(path.join(directory,folder));
  const module={exports:{}};
  vm.runInNewContext(sync.readFileSync(path.join(__dirname,'../desktop/features/updates/updater.cjs'),'utf8'),
    {module,require,Buffer,URL,AbortController,AbortSignal,setTimeout,Response});
  const {Updater,assetName,RELEASES,releaseUrl}=module.exports,keys=generateKeyPairSync('ed25519'),bytes=Buffer.from('Authentic signed fixture ZIP bytes');
  const name=assetName('2.2.1','win32','x64'),digest=createHash('sha256').update(bytes).digest('hex');
  const payload=Buffer.from(JSON.stringify({schema:1,version:'2.2.1',assets:{'win32-x64':{name,size:bytes.length,sha256:digest}}}));
  const manifest=JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')});
  await fs.writeFile(path.join(directory,'old',name),bytes);await fs.writeFile(path.join(directory,'old','bridge-update.json'),manifest);
  const native={dialog:{showMessageBox:async()=>({response:0})},shell:{}};
  const req=name=>name==='./desktop/features/updates/updater.cjs'?module.exports:name==='electron'?native:require(name);
  const context=vm.createContext({process:{resourcesPath:'/unpacked/resources',getBuiltinModule:name=>{assert.equal(name,'module');return {createRequire:()=>req};}},Buffer,Response});
  vm.runInContext(transportScript(directory),context);
  let installed=0;
  const options={current:'2.2.2-preview.2',platform:'win32',arch:'x64',key:keys.publicKey,directory:path.join(directory,'downloads'),
    install:async candidate=>{assert.equal(candidate.transition.intent,'return-stable');assert.deepEqual(await fs.readFile(candidate.archive),bytes);installed++;}};
  let updater=new Updater(options);
  const state=await updater.check({manual:true,channel:'stable'});
  assert.equal(state.state,'available',state.message);assert.equal(state.intent,'return-stable');assert.equal(updater.key,keys.publicKey);
  await assert.rejects(updater.install({candidateId:state.candidateId}),/确认/);
  assert.equal((await updater.install({candidateId:state.candidateId,confirmed:true})).state,'restarting');assert.equal(installed,1);
  assert.deepEqual([...context.acceptance.requests],[RELEASES,releaseUrl('2.2.1','bridge-update.json'),releaseUrl('2.2.1',name)]);
  await assert.rejects(updater.fetch('https://fixture.invalid/'+name),/refuses unexpected URL/);
  updater=new Updater(options);
  await updater.check({manual:true,channel:'stable'});await fs.writeFile(path.join(directory,'old',name),Buffer.alloc(bytes.length));
  await updater.cleanupVerifiedPackages({startup:true});
  assert.equal((await updater.install({candidateId:updater.status().candidateId,confirmed:true})).state,'restarting');assert.equal(installed,2);
  const cached=(await fs.readdir(options.directory)).find(name=>name.startsWith('verified-'));
  await fs.writeFile(path.join(options.directory,cached),Buffer.alloc(bytes.length));
  updater=new Updater(options);await updater.check({manual:true,channel:'stable'});
  assert.equal((await updater.install({candidateId:updater.status().candidateId,confirmed:true})).state,'error');assert.equal(installed,2);
  await fs.writeFile(path.join(directory,'old','bridge-update.json'),JSON.stringify({payload:payload.toString('base64'),signature:Buffer.alloc(64).toString('base64')}));
  assert.equal((await updater.check({manual:true,channel:'stable'})).state,'error');assert.match(updater.status().message,/签名/);
});
