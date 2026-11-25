'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process'),path=require('node:path');
const {EventEmitter,once}=require('node:events');
const fs=require('node:fs/promises'),sync=require('node:fs'),vm=require('node:vm');
const {createHash,generateKeyPairSync,sign}=require('node:crypto');
const {installerHandoffEvidence,click,stopOwnedGui,debuggerClient,transportScript,baselinePolicy,verifiedArchiveProof,stableReturnCacheProof,pausedUpdateEvidence,automaticHandoffLine,credentialBoundaryProof,paths}=require('../scripts/smoke-published-upgrade.cjs');
const script=path.join(__dirname,'../scripts/smoke-published-upgrade.cjs');
test('candidate transport requires the CI signature and exact ZIP bytes',async t=>{
  const {prepareCandidateRelease}=require('../scripts/smoke-published-upgrade.cjs');
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-candidate-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const keys=generateKeyPairSync('ed25519'),bytes=Buffer.from('Synthetic candidate ZIP'),version='2.2.2-preview.4';
  const name=`Codex-Mobile-Bridge-${version}-Windows-x64.zip`,sha256=createHash('sha256').update(bytes).digest('hex');
  const payload=Buffer.from(JSON.stringify({schema:1,version,dataFormat:'bridge-data-v1',assets:{'win32-x64':{name,size:bytes.length,sha256}}}));
  await fs.mkdir(path.join(directory,'candidate'));
  const manifest=path.join(directory,'candidate','bridge-update.json'),archive=path.join(directory,'candidate',name);
  await fs.writeFile(manifest,JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')}));
  await fs.writeFile(archive,bytes);
  const result=await prepareCandidateRelease(directory,'win32','x64',keys.publicKey);
  assert.equal(result.asset.name,name);assert.equal(result.asset.sha256,sha256);
  assert.equal(result.release.tag_name,'v'+version);
  assert.equal(result.release.assets.find(row=>row.name===name).digest,'sha256:'+sha256);
  await assert.rejects(prepareCandidateRelease(directory,'win32','x64',generateKeyPairSync('ed25519').publicKey),/签名/);
  await fs.writeFile(archive,Buffer.alloc(bytes.length));
  await assert.rejects(prepareCandidateRelease(directory,'win32','x64',keys.publicKey),/candidate ZIP/);
});
test('upgrade receipts cannot report success without the real matching ACK and restored runtime',async t=>{
  const {verifyUpgradeReceipt}=require('../scripts/smoke-published-upgrade.cjs');
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-ack-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const plan={token:'synthetic-token',version:'2.2.2-preview.4',dataDir:directory,runtime:{running:true}};
  await fs.writeFile(path.join(directory,'plan.json'),JSON.stringify(plan));
  await fs.writeFile(path.join(directory,'result.json'),JSON.stringify({state:'updated',version:plan.version}));
  await assert.rejects(verifyUpgradeReceipt(path.join(directory,'plan.json'),async()=>({runtime:{running:true}})),{code:'ENOENT'});
  await fs.writeFile(path.join(directory,'ack.json'),JSON.stringify({token:'wrong',version:plan.version,dataDir:directory}));
  await assert.rejects(verifyUpgradeReceipt(path.join(directory,'plan.json'),async()=>({runtime:{running:true}})));
  await fs.writeFile(path.join(directory,'ack.json'),JSON.stringify({token:plan.token,version:plan.version,dataDir:directory}));
  await assert.rejects(verifyUpgradeReceipt(path.join(directory,'plan.json'),async()=>({runtime:{running:false}})),/restored gateway/);
  assert.equal((await verifyUpgradeReceipt(path.join(directory,'plan.json'),async()=>({runtime:{running:true}}))).acknowledged,true);
});
test('same-version source proof rejects a patched ZIP with the original digest or mismatched official metadata',async t=>{
  const {originalPreview4Proof}=require('../scripts/smoke-published-upgrade.cjs');
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-original-preview4-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  await fs.mkdir(path.join(directory,'preview4'));
  const keys=generateKeyPairSync('ed25519'),bytes=Buffer.from('Original official Preview4 ZIP'),version='2.2.2-preview.4';
  const name=`Codex-Mobile-Bridge-${version}-Windows-x64.zip`,sha256=createHash('sha256').update(bytes).digest('hex');
  const payload=Buffer.from(JSON.stringify({schema:1,version,dataFormat:'bridge-data-v1',assets:{'win32-x64':{name,size:bytes.length,sha256}}}));
  await fs.writeFile(path.join(directory,'preview4','bridge-update.json'),JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')}));
  await fs.writeFile(path.join(directory,'preview4',name),bytes);
  const asset={name,size:bytes.length,digest:'sha256:'+sha256,browser_download_url:'https://github.com/try2love/codex-mobile-bridge/releases/download/v'+version+'/'+name};
  const file=path.join(directory,'preview4','release.json');
  await fs.writeFile(file,JSON.stringify({tag_name:'v'+version,assets:[asset]}));
  const candidate={sha256:'a'.repeat(64)};
  assert.equal((await originalPreview4Proof(directory,candidate,'win32','x64',keys.publicKey)).sha256,sha256);
  await assert.rejects(originalPreview4Proof(directory,{sha256},'win32','x64',keys.publicKey),/different from original/);
  await fs.writeFile(file,JSON.stringify({tag_name:'v'+version,assets:[{...asset,digest:'sha256:'+'b'.repeat(64)}]}));
  await assert.rejects(originalPreview4Proof(directory,candidate,'win32','x64',keys.publicKey),/official API digest/);
});
test('same-version UI refresh replaces cached catalogue metadata before selection',async()=>{
  const {selectSameVersionHotfix}=require('../scripts/smoke-published-upgrade.cjs');
  const renderer=sync.readFileSync(path.join(__dirname,'../desktop/shell/renderer.js'),'utf8');
  const load=renderer.slice(renderer.indexOf('async function loadUpdateVersions('),renderer.indexOf('async function selectUpdateVersion('));
  const version='2.2.2-preview.4',name=`Codex-Mobile-Bridge-${version}-Windows-x64.zip`,sha256='a'.repeat(64);
  const list=id=>({current:version,releases:[{version,assets:[{id,name,format:'zip'}]}]});
  const refresh={hidden:false,disabled:false,scrollIntoView(){},getBoundingClientRect:()=>({x:0,y:0,width:100,height:30}),contains:element=>element===refresh};
  const select={value:'',dispatchEvent(){context.updateVersionsState.version=select.value;context.snapshot={update:{state:'available',selected:true,current:version,version}};}};
  const context=vm.createContext({innerWidth:1000,innerHeight:800,Event:class{},
    document:{getElementById:id=>id==='update-versions-refresh'?refresh:select,elementFromPoint:()=>refresh},
    updateVersionsState:{value:list('original'),version:'',loading:false,selecting:false},snapshot:{update:{}},
    acceptance:{requests:[],updater:{catalogueAssets:new Map([['new',{asset:{sha256}}]])}},
    tab(){},renderUpdateVersions(){},updateCacheBusy:()=>false,updateCacheState:{deleting:false},
    updateVersionAsset:()=>null,updateVersionRelease:()=>null,
    api:{updateVersions:async()=>{context.acceptance.requests.push('https://api.github.com/repos/try2love/codex-mobile-bridge/releases?per_page=100&page=1');return list('new');}}});
  vm.runInContext(load,context);refresh.onclick=()=>context.loadUpdateVersions(true);
  const ui={evaluate:expression=>vm.runInContext(expression,context),call:async(method,event)=>{
    assert.equal(method,'Input.dispatchMouseEvent');if(event.type==='mouseReleased')await refresh.onclick();
  }},main={evaluate:expression=>vm.runInContext(expression,context)};
  const selected=await selectSameVersionHotfix(ui,main,{name,sha256});
  assert.equal(selected.version,version);assert.equal(context.acceptance.requests.length,1);
  assert.equal(context.updateVersionsState.value.releases[0].assets[0].id,'new');
  // A cached/no-op refresh cannot satisfy the acceptance helper.
  refresh.onclick=async()=>{};
  await assert.rejects(selectSameVersionHotfix(ui,main,{name,sha256}),/force refresh must fetch/);
});
test('candidate transport lets a Preview3 updater authenticate and install Preview4 as a normal update',async t=>{
  const {prepareCandidateRelease}=require('../scripts/smoke-published-upgrade.cjs');
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-forward-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  for(const [version,folder] of [['2.2.1','old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2'],['2.2.2-preview.3','preview3']]){
    await fs.mkdir(path.join(directory,folder));
    await fs.writeFile(path.join(directory,folder,'release.json'),JSON.stringify({tag_name:'v'+version,draft:false,prerelease:version.includes('-'),published_at:'2026-11-01T00:00:00Z',assets:[]}));
  }
  await fs.mkdir(path.join(directory,'candidate'));
  const keys=generateKeyPairSync('ed25519'),bytes=Buffer.from('Synthetic CI ZIP'),version='2.2.2-preview.4';
  const name=`Codex-Mobile-Bridge-${version}-Windows-x64.zip`,sha256=createHash('sha256').update(bytes).digest('hex');
  const payload=Buffer.from(JSON.stringify({schema:1,version,dataFormat:'bridge-data-v1',assets:{'win32-x64':{name,size:bytes.length,sha256}}}));
  await fs.writeFile(path.join(directory,'candidate','bridge-update.json'),JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')}));
  await fs.writeFile(path.join(directory,'candidate',name),bytes);
  await prepareCandidateRelease(directory,'win32','x64',keys.publicKey);
  const module={exports:{}};
  vm.runInNewContext(sync.readFileSync(path.join(__dirname,'../desktop/features/updates/updater.cjs'),'utf8'),
    {module,require,Buffer,URL,AbortController,AbortSignal,setTimeout,Response});
  const native={dialog:{showMessageBox:async()=>({response:0})},shell:{}};
  const req=name=>name==='./desktop/features/updates/updater.cjs'?module.exports:name==='electron'?native:require(name);
  const context=vm.createContext({process:{resourcesPath:'/unpacked/resources',getBuiltinModule:()=>({createRequire:()=>req})},Buffer,Response});
  vm.runInContext(transportScript(directory,true,true,true,true),context);
  let preflight=0,applied=0;
  const options={current:'2.2.2-preview.3',platform:'win32',arch:'x64',key:keys.publicKey,directory:path.join(directory,'downloads'),
    preflight:async()=>{preflight++;},install:async candidate=>{
      assert.equal(candidate.version,version);assert.equal(candidate.transition.intent,'update');
      assert.deepEqual(await fs.readFile(candidate.archive),bytes);applied++;
    }};
  const updater=new module.exports.Updater(options),state=await updater.check({manual:true});
  assert.equal(state.state,'available',state.message);assert.equal(state.version,version);assert.equal(state.requiresConfirmation,false);
  assert.equal((await updater.install({candidateId:state.candidateId})).state,'restarting');
  assert.equal(preflight,1);assert.equal(applied,1);assert.equal(updater.key,keys.publicKey);
  const unknown=new module.exports.Updater({...options,key:generateKeyPairSync('ed25519').publicKey});
  assert.equal((await unknown.check({manual:true})).state,'error');assert.match(unknown.status().message,/签名/);
  assert.equal(applied,1);
  const originalBytes=Buffer.from('Original same-version cached ZIP'),originalSha=createHash('sha256').update(originalBytes).digest('hex');
  const downloads=path.join(directory,'same-version-downloads');await fs.mkdir(downloads);
  const originalCache=path.join(downloads,`verified-${version}-win32-x64-${originalSha}.zip`);await fs.writeFile(originalCache,originalBytes);
  let sameVersionApplied=0;
  const same=new module.exports.Updater({...options,current:version,directory:downloads,install:async candidate=>{
    assert.equal(candidate.transition.intent,'switch-version');assert.equal(candidate.transition.from,version);assert.equal(candidate.transition.to,version);
    assert.equal(candidate.asset.sha256,sha256);assert.deepEqual(await fs.readFile(candidate.archive),bytes);sameVersionApplied++;
  }});
  assert.equal((await same.check({manual:true})).state,'current','ordinary update does not discover a same-version patch');
  const list=await same.listVersions(),selected=await same.selectVersion(list.releases.find(row=>row.version===version).assets[0].id);
  assert.equal(selected.current,version);assert.equal(selected.version,version);assert.equal(selected.requiresConfirmation,true);
  await assert.rejects(same.install({candidateId:selected.candidateId}),/确认/);
  context.acceptance.requests.length=0;
  assert.equal((await same.install({candidateId:selected.candidateId,confirmed:true})).state,'restarting');
  assert.equal(sameVersionApplied,1);assert.deepEqual(await fs.readFile(originalCache),originalBytes);
  assert.equal(context.acceptance.requests.filter(url=>url.endsWith('/'+name)).length,1,'new digest downloads new bytes rather than the original cache');
});
test('acceptance mouse click scrolls an offscreen history action into view and verifies its hit target',async()=>{
  let y=1280,clicks=0,pressed=false,covered=false,disabled=false;
  const button={hidden:false,get disabled(){return disabled;},
    scrollIntoView(options){assert.deepEqual(JSON.parse(JSON.stringify(options)),{block:'center',inline:'center',behavior:'instant'});y=394;},
    getBoundingClientRect:()=>({x:400,y,width:180,height:35}),contains:element=>element===button};
  const context={innerWidth:1100,innerHeight:823,document:{
    getElementById:id=>{assert.equal(id,'update-versions-install');return button;},
    elementFromPoint:(x,pointY)=>!covered&&x===490&&pointY===411.5?button:null}};
  const events=[],ui={evaluate:expression=>vm.runInNewContext(expression,context),call:async(method,event)=>{
    assert.equal(method,'Input.dispatchMouseEvent');events.push(event.type);
    if(event.type==='mousePressed')pressed=event.x===490&&event.y===411.5;
    if(event.type==='mouseReleased'&&pressed)clicks++;
  }};
  await click(ui,'update-versions-install');assert.equal(clicks,1);
  assert.deepEqual(events,['mousePressed','mouseReleased']);
  covered=true;await assert.rejects(click(ui,'update-versions-install'),/not reachable/);
  disabled=true;await assert.rejects(click(ui,'update-versions-install'),/unavailable/);
  assert.equal(clicks,1);assert.equal(events.length,2);
});
test('Linux handoff checks the settled install result and exposes backup errors without a state timeout',async()=>{
  const renderer={update:{state:'downloaded'},feedback:['backup retained',true,'']};
  let complete=false;
  const ui={evaluate:async expression=>{
    if(expression==='!updateBusy'){complete=true;return true;}
    assert.equal(complete,true);return renderer;
  }};
  const evidence={update:{state:'error',message:'backup retained'},opened:[],packagesOpened:[]};
  const main={evaluate:async()=>{assert.equal(complete,true);return evidence;}};
  await assert.rejects(installerHandoffEvidence(main,ui),error=>{
    assert.match(error.message,/Official Linux package handoff failed/);assert.match(error.message,/backup retained/);return true;
  });
  evidence.update={state:'installer-handoff'};
  assert.equal(await installerHandoffEvidence(main,ui),evidence);
});
test('Linux package discovery remains in the installed channel and cannot imply automatic installation',()=>{
  const {verifyUpdateDiscovery}=require('../scripts/smoke-linux.cjs');
  for(const current of ['2.2.1','2.2.2-preview.3','2.2.2-preview.4']){
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
  for(const version of ['2.2.2-preview.2','2.2.2-preview.3','2.2.2-preview.4'])for(const platform of ['darwin','win32','linux'])assert.deepEqual(baselinePolicy(version,platform),{state:'current',includeCandidate:false,previewDiscoveryTested:false,channelDiscoveryTested:true});
  assert.throws(()=>baselinePolicy('2.2.2-preview.5','darwin'),/Unknown official baseline/);
});
test('credential acceptance runs the production source worker and blocks stable and preview3 before any backup',async t=>{
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-credential-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const files=paths(path.join(directory,'app'));
  await fs.mkdir(path.dirname(files.metadata),{recursive:true});
  await fs.writeFile(files.metadata,JSON.stringify({version:'2.2.2-preview.4',dataFormat:'bridge-data-v1'}));
  const {runWorker}=require('../desktop/shared/controller.cjs');
  const result=await credentialBoundaryProof(files,directory,(options,action,payload)=>runWorker({...options,
    executable:process.platform==='win32'?'python':'python3',prefix:['-B',path.join(__dirname,'../desktop.py')]},action,payload));
  assert.deepEqual(result.blocked,[{version:'2.2.1',manualInstall:false},{version:'2.2.1',manualInstall:true},
    {version:'2.2.2-preview.3',manualInstall:false},{version:'2.2.2-preview.3',manualInstall:true}]);
  assert.equal(result.configurationBytesUnchanged,true);assert.equal(result.backupCreated,false);
  assert.equal(result.legacyReturnFixtureHasCmb1,false);assert.equal(result.decryptionTested,false);
});
test('credential acceptance cannot count an accepted switch or unrelated worker failure as a safety rejection',async t=>{
  for(const worker of [async()=>({}),async()=>{throw Error('unrelated fixture failure');}]){
    const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-credential-failure-'));
    t.after(()=>fs.rm(directory,{recursive:true,force:true}));
    await assert.rejects(credentialBoundaryProof(paths(path.join(directory,'app')),directory,worker),/Missing expected rejection|unrelated fixture failure/);
  }
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
test('update evidence is read from the paused installer before the handoff resumes',{timeout:10000},async()=>{
  const fixture=`
    globalThis.acceptance={requests:['https://fixture.invalid/package.zip']};
    globalThis.handoffReached=false;
    function installUpdate(){
      const prepared={plan:'synthetic-plan'},candidate={archive:'synthetic-archive'};
      debugger;
      globalThis.handoffReached=true;
    }
    process.stdout.write('ready\\n');setInterval(()=>{},1000);`;
  const child=spawn(process.execPath,['--inspect=127.0.0.1:0','-e',fixture],{stdio:['ignore','pipe','pipe']});
  let main,timer;
  try{
    const [port]=await Promise.all([
      new Promise((resolve,reject)=>{
        let errors='';child.stderr.on('data',bytes=>{errors+=bytes;const match=errors.match(/ws:\/\/127\.0\.0\.1:(\d+)\//);if(match)resolve(Number(match[1]));});
        child.once('error',reject);
      }),
      once(child.stdout,'data')
    ]);
    main=await debuggerClient(port,row=>row.type==='node');await main.call('Debugger.enable');
    const paused=main.event('Debugger.paused',3000);
    await main.call('Runtime.evaluate',{expression:'setTimeout(installUpdate,0);void 0',returnByValue:true});
    const frame=(await paused).callFrames.find(value=>value.functionName==='installUpdate');assert.ok(frame);
    const evidence=await Promise.race([
      pausedUpdateEvidence(main,frame),
      new Promise((resolve,reject)=>{timer=setTimeout(()=>reject(Error('Paused update evidence did not complete')),2000);})
    ]);
    clearTimeout(timer);
    assert.deepEqual(evidence,{plan:'synthetic-plan',archive:'synthetic-archive',requests:['https://fixture.invalid/package.zip']});
    assert.equal(main.events.some(value=>value.method==='Debugger.resumed'),false);
    const handoff=await main.call('Debugger.evaluateOnCallFrame',{callFrameId:frame.callFrameId,expression:'handoffReached',returnByValue:true});
    assert.equal(handoff.result.value,false);
    await main.call('Debugger.resume');
  }finally{clearTimeout(timer);main?.close();await stopOwnedGui(child,5000);}
});
test('automatic handoff breakpoint follows preparation after the separate manual save path',()=>{
  const source=sync.readFileSync(path.join(__dirname,'../desktop/main.cjs'),'utf8'),lines=source.split('\n');
  const selected=automaticHandoffLine(source);
  assert.equal(lines[selected].trim(),'saveDataDir(dataDir);');
  assert.ok(selected>lines.findIndex(line=>line.includes("await worker('update-prepare'")));
  assert.ok(selected>lines.findIndex(line=>line.trim()==='saveDataDir(dataDir);'));
  assert.throws(()=>automaticHandoffLine('saveDataDir(dataDir);'));
});
test('acceptance transport runs production signed selection and archive hashes without replacing the key',async t=>{
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-transport-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  for(const folder of ['old','preview1','preview2','preview3'])await fs.mkdir(path.join(directory,folder));
  const module={exports:{}};
  vm.runInNewContext(sync.readFileSync(path.join(__dirname,'../desktop/features/updates/updater.cjs'),'utf8'),
    {module,require,Buffer,URL,AbortController,AbortSignal,setTimeout,Response});
  const {Updater,assetName,RELEASES,releaseUrl}=module.exports,keys=generateKeyPairSync('ed25519'),bytes=Buffer.from('Authentic signed fixture ZIP bytes');
  const name=assetName('2.2.1','win32','x64'),digest=createHash('sha256').update(bytes).digest('hex');
  const payload=Buffer.from(JSON.stringify({schema:1,version:'2.2.1',assets:{'win32-x64':{name,size:bytes.length,sha256:digest}}}));
  const manifest=JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')});
  await fs.writeFile(path.join(directory,'old',name),bytes);await fs.writeFile(path.join(directory,'old','bridge-update.json'),manifest);
  for(const [version,folder] of [['2.2.1','old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2'],['2.2.2-preview.3','preview3']]){
    const release={tag_name:'v'+version,draft:false,prerelease:version.includes('-'),published_at:'2026-11-01T00:00:00Z',
      assets:[{name:'bridge-update.json',browser_download_url:releaseUrl(version,'bridge-update.json')},
        ...(version==='2.2.1'?[{name,size:bytes.length,digest:'sha256:'+digest,browser_download_url:releaseUrl(version,name)}]:[])]};
    await fs.writeFile(path.join(directory,folder,'release.json'),JSON.stringify(release));
  }
  const native={dialog:{showMessageBox:async()=>({response:0})},shell:{}};
  const req=name=>name==='./desktop/features/updates/updater.cjs'?module.exports:name==='electron'?native:require(name);
  const context=vm.createContext({process:{resourcesPath:'/unpacked/resources',getBuiltinModule:name=>{assert.equal(name,'module');return {createRequire:()=>req};}},Buffer,Response});
  const productionMethods=Object.fromEntries(['check','listVersions','selectVersion'].map(name=>[name,Updater.prototype[name]]));
  const useTransport=(...flags)=>{
    Object.assign(Updater.prototype,productionMethods);
    vm.runInContext(transportScript(directory,...flags),context);
  };
  useTransport();
  let installed=0;
  const options={current:'2.2.2-preview.4',platform:'win32',arch:'x64',key:keys.publicKey,directory:path.join(directory,'downloads'),
    install:async candidate=>{assert.equal(candidate.transition.intent,'return-stable');assert.deepEqual(await fs.readFile(candidate.archive),bytes);installed++;}};
  let updater=new Updater(options);
  const state=await updater.check({manual:true,channel:'stable'});
  assert.equal(state.state,'available',state.message);assert.equal(state.intent,'return-stable');assert.equal(updater.key,keys.publicKey);
  await assert.rejects(updater.install({candidateId:state.candidateId}),/确认/);
  assert.equal((await updater.install({candidateId:state.candidateId,confirmed:true})).state,'restarting');assert.equal(installed,1);
  assert.deepEqual([...context.acceptance.requests],[RELEASES,releaseUrl('2.2.1','bridge-update.json'),releaseUrl('2.2.1',name)]);
  await assert.rejects(updater.fetch('https://fixture.invalid/'+name),/refuses unexpected URL/);
  // Preview2 introduced channels, including Linux discovery. Its own catalogue
  // entry must not make preview1 request a newer baseline's manifest.
  const legacyCatalogue=await (await updater.fetch(RELEASES)).json();
  assert.equal(legacyCatalogue.some(row=>row.tag_name==='v2.2.2-preview.2'),false);
  assert.equal(legacyCatalogue.some(row=>row.tag_name==='v2.2.2-preview.3'),false);
  useTransport(false,true);updater=new Updater(options);await updater.check({manual:true,channel:'stable'});
  assert.equal((await (await updater.fetch(RELEASES)).json()).some(row=>row.tag_name==='v2.2.2-preview.3'),false);
  useTransport(false,true,true);updater=new Updater(options);await updater.check({manual:true,channel:'stable'});
  assert.equal((await (await updater.fetch(RELEASES)).json()).some(row=>row.tag_name==='v2.2.2-preview.3'),true);
  for(const current of ['2.2.2-preview.2','2.2.2-preview.3'])for(const platform of ['win32','linux']){
    useTransport(false,true,current==='2.2.2-preview.3');
    const baseline=new Updater({...options,current,platform});
    assert.equal((await baseline.check({manual:true})).state,'current');
    const stable=await baseline.check({manual:true,channel:'stable'});
    assert.equal(stable.state,'available',stable.message);assert.equal(stable.version,'2.2.1');
    assert.equal(stable.intent,'return-stable');assert.equal(stable.requiresConfirmation,true);
    if(platform==='linux')assert.equal(stable.manualRelease,true);
    const preview=await baseline.check({manual:true,channel:'preview'});
    assert.equal(preview.state,'current');assert.equal(preview.channel,'preview');
  }
  updater=new Updater(options);
  await updater.check({manual:true,channel:'stable'});await fs.writeFile(path.join(directory,'old',name),Buffer.alloc(bytes.length));
  assert.equal(updater.cacheStatus().entries.length,1);
  assert.deepEqual(await fs.readFile(path.join(options.directory,updater.cacheStatus().entries[0].id)),bytes);
  assert.equal((await updater.install({candidateId:updater.status().candidateId,confirmed:true})).state,'restarting');assert.equal(installed,2);
  const cached=(await fs.readdir(options.directory)).find(name=>name.startsWith('verified-'));
  await fs.writeFile(path.join(options.directory,cached),Buffer.alloc(bytes.length));
  updater=new Updater(options);await updater.check({manual:true,channel:'stable'});
  assert.equal((await updater.install({candidateId:updater.status().candidateId,confirmed:true})).state,'error');assert.equal(installed,2);
  await fs.writeFile(path.join(directory,'old','bridge-update.json'),JSON.stringify({payload:payload.toString('base64'),signature:Buffer.alloc(64).toString('base64')}));
  assert.equal((await updater.check({manual:true,channel:'stable'})).state,'error');assert.match(updater.status().message,/签名/);
});

for(const [platform,arch,format,otherFormat] of [['darwin','arm64','zip','dmg'],['win32','x64','zip','exe'],['linux','x64','deb','AppImage']]){
  test(`history transport retains official metadata and download-only cache reuse on ${platform}`,async t=>{
    const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-history-'));
    t.after(()=>fs.rm(directory,{recursive:true,force:true}));
    const module={exports:{}};
    vm.runInNewContext(sync.readFileSync(path.join(__dirname,'../desktop/features/updates/updater.cjs'),'utf8'),
      {module,require,Buffer,URL,AbortController,AbortSignal,setTimeout,Response});
    const {Updater,assetName,releaseUrl,RELEASES}=module.exports,keys=generateKeyPairSync('ed25519');
    const bytes=Buffer.from('Official-byte transport fixture '+platform),digest=createHash('sha256').update(bytes).digest('hex');
    const name=assetName('2.2.1',platform,arch,format),missing=assetName('2.2.1',platform,arch,otherFormat);
    const assets=platform==='linux'?{}:{[platform+'-'+arch]:{name,size:bytes.length,sha256:digest}};
    const payload=Buffer.from(JSON.stringify({schema:1,version:'2.2.1',assets}));
    for(const [version,folder] of [['2.2.1','old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2'],['2.2.2-preview.3','preview3']]){
      await fs.mkdir(path.join(directory,folder));
      const release={tag_name:'v'+version,draft:false,prerelease:version.includes('-'),published_at:'2026-11-01T00:00:00Z',body:'Original official release metadata',
        assets:[{name:'bridge-update.json',browser_download_url:releaseUrl(version,'bridge-update.json')},
          ...(version==='2.2.1'?[name,missing].map(name=>({name,size:bytes.length,digest:'sha256:'+digest,browser_download_url:releaseUrl(version,name)})):[])]};
      await fs.writeFile(path.join(directory,folder,'release.json'),JSON.stringify(release));
    }
    await fs.writeFile(path.join(directory,'old',name),bytes);
    await fs.writeFile(path.join(directory,'old','bridge-update.json'),JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,keys.privateKey).toString('base64')}));
    const native={dialog:{showMessageBox:async()=>({response:0})},shell:{}};
    const req=name=>name==='./desktop/features/updates/updater.cjs'?module.exports:name==='electron'?native:require(name);
    const context=vm.createContext({process:{platform,resourcesPath:'/unpacked/resources',getBuiltinModule:()=>({createRequire:()=>req})},Buffer,Response});
    vm.runInContext(transportScript(directory),context);
    let preflights=0,applied=0;
    const options={current:'2.2.2-preview.4',platform,arch,format,key:keys.publicKey,directory:path.join(directory,'downloads'),
      fetch:async()=>{throw Error('Unhooked package transport');},catalogueFetch:async()=>{throw Error('Unhooked catalogue transport');},
      preflight:async()=>{preflights++;},install:async candidate=>{assert.equal(candidate.transition.intent,'switch-version');applied++;}};
    let updater=new Updater(options),list=await updater.listVersions();
    assert.equal(updater.key,keys.publicKey);assert.equal(list.releases[0].publishedAt,'2026-11-01T00:00:00Z');
    assert.deepEqual([...list.releases[0].assets.map(asset=>asset.format)],[format,otherFormat]);
    const selected=await updater.selectVersion(list.releases[0].assets.find(asset=>asset.format===format).id);
    assert.equal(selected.intent,'switch-version');assert.equal(selected.manualInstall,platform==='linux');
    const downloaded=await updater.download({candidateId:selected.candidateId});
    assert.equal(downloaded.state,'downloaded');assert.equal(preflights,0);assert.equal(applied,0);
    const proof=await verifiedArchiveProof(downloaded.path,{name,sha256:digest},context.acceptance.requests,1);
    assert.equal(updater.cacheStatus().entries[0].id,path.basename(proof.archive));assert.equal(updater.cacheStatus().entries[0].inUse,false);
    assert.equal(context.acceptance.dialogs.length,0);assert.equal(context.acceptance.packagesOpened.length,0);
    await assert.rejects(updater.fetch(releaseUrl('2.2.1',missing)),/refuses unexpected URL/);
    context.acceptance.requests.length=0;
    updater=new Updater(options);list=await updater.listVersions();
    const again=await updater.selectVersion(list.releases[0].assets.find(asset=>asset.format===format).id);
    assert.equal((await updater.download({candidateId:again.candidateId})).state,'downloaded');
    await verifiedArchiveProof(proof.archive,{name,sha256:digest},context.acceptance.requests,0);
    assert.equal(preflights,0);assert.equal(applied,0);
    await updater.install({candidateId:again.candidateId,confirmed:true});
    assert.equal(preflights,1);assert.equal(applied,1);
    if(platform==='linux'){
      assert.equal(await native.shell.openPath(proof.archive),'');
      assert.deepEqual([...context.acceptance.packagesOpened],[proof.archive]);
      await assert.rejects(native.shell.openPath(proof.archive+'.unexpected'),/Unexpected package handoff/);
    }
    await verifiedArchiveProof(proof.archive,{name,sha256:digest},context.acceptance.requests,0);
    assert.ok(context.acceptance.requests.includes(RELEASES+'&page=1'));
  });
}

test('real archive evidence requires the trusted digest and exact per-attempt package request count',async t=>{
  const directory=await fs.mkdtemp(path.join(__dirname,'../.tmp/acceptance-cache-proof-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  const archive=path.join(directory,'verified.zip'),bytes=Buffer.from('Retained actual archive bytes');
  const asset={name:'Codex-Mobile-Bridge-2.2.1-Windows-x64.zip',sha256:createHash('sha256').update(bytes).digest('hex')};
  const url='https://github.com/try2love/codex-mobile-bridge/releases/download/v2.2.1/'+asset.name;
  await fs.writeFile(archive,bytes);
  const first=await verifiedArchiveProof(archive,asset,[url],1),retry=await verifiedArchiveProof(archive,asset,[],0);
  assert.equal(first.archive,retry.archive);assert.equal(first.sha256,retry.sha256);
  // The actual rollback target is old stable221, whose acknowledgement does not
  // clean verified caches. Require retained authentic bytes, not a new capability.
  const returned=await stableReturnCacheProof(retry,asset);
  assert.equal(returned.retainedAfterSuccess,true);assert.equal(returned.cleanupSupportedByTarget,false);
  assert.equal(returned.reason,'v2.2.1 predates verified-cache cleanup');
  assert.deepEqual(await fs.readFile(returned.archive),bytes);

  await assert.rejects(verifiedArchiveProof(archive,asset,[url],0),/package downloads/);
  await fs.writeFile(archive,Buffer.alloc(bytes.length));
  await assert.rejects(verifiedArchiveProof(archive,asset,[],0),/trusted package digest/);
  await assert.rejects(stableReturnCacheProof(retry,asset),/trusted package digest/);
  await fs.unlink(archive);await assert.rejects(verifiedArchiveProof(archive,asset,[],0),{code:'ENOENT'});
});
