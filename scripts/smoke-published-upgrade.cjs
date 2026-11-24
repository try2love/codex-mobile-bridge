'use strict';
// Official baseline -> manual candidate bootstrap -> production Updater return.
// Hosted runners only. Debugger transport uses official bytes and the shipped key.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),net=require('node:net');
const {createHash,randomUUID}=require('node:crypto');
const {runWorker}=require('../desktop/shared/controller.cjs');
const oldVersion='2.2.1',candidateVersion='2.2.2-preview.3';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function baselinePolicy(version,platform=process.platform){
  assert.ok([oldVersion,'2.2.2-preview.1','2.2.2-preview.2'].includes(version),'Unknown official baseline');
  if(version==='2.2.2-preview.2')return {state:'current',includeCandidate:false,previewDiscoveryTested:false,channelDiscoveryTested:true};
  return {state:platform==='linux'?'unsupported':'current',includeCandidate:platform!=='linux'&&version===oldVersion,
    previewDiscoveryTested:platform!=='linux'&&version===oldVersion};
}
function packageName(version,platform=process.platform,arch=process.arch){
  const suffix=platform==='darwin'?`macOS-${arch}.zip`:platform==='win32'?'Windows-x64-Setup.exe':`Linux-${arch==='x64'?'amd64':'arm64'}.deb`;
  return `Codex-Mobile-Bridge-${version}-${suffix}`;
}
function paths(target,platform=process.platform){
  return platform==='darwin'?{app:path.join(target,'Contents/MacOS/Codex Mobile Bridge'),runtime:path.join(target,'Contents/Resources/gateway/codex-mobile-gateway'),metadata:path.join(target,'Contents/Resources/update-version.json')}:
    {app:path.join(target,platform==='win32'?'Codex Mobile Bridge.exe':'codex-mobile-bridge'),runtime:path.join(target,'resources/gateway/codex-mobile-gateway'+(platform==='win32'?'.exe':'')),metadata:path.join(target,'resources/update-version.json')};
}
async function until(fn,label,timeout=45000){
  const deadline=Date.now()+timeout;let last;
  while(Date.now()<deadline){try{const value=await fn();if(value)return value;}catch(error){last=error;}await delay(300);}
  throw Error(label+' timed out'+(last?': '+last.message:''));
}
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
async function install(archive,target,work,phase){
  if(process.platform==='darwin'){
    const unpack=path.join(work,'unpack-'+phase);await fs.mkdir(unpack);
    execFileSync('ditto',['-x','-k',archive,unpack]);
    const source=path.join(unpack,'Codex Mobile Bridge.app');await fs.access(source);
    // A stopped .app is replaced in place; the isolated data directory is separate.
    await fs.rm(target,{recursive:true,force:true});execFileSync('ditto',[source,target]);
    await fs.rm(unpack,{recursive:true});
  }else if(process.platform==='win32'){
    // NSIS requires /D last and unquoted. Reject shell metacharacters in paths.
    assert.ok(!/["\r\n&|<>^%]/.test(archive+target),'Unsafe installer path');
    execFileSync('python',['-c',`import subprocess,sys; subprocess.run('"'+sys.argv[1]+'" /S /D='+sys.argv[2],check=True,timeout=180)`,archive,target],{timeout:190000,stdio:'inherit'});
  }else{
    // Hosted ephemeral runner only. Explicitly permit downgrading for rollback.
    execFileSync('sudo',['apt-get','install','-y','--allow-downgrades',archive],{timeout:180000,stdio:'inherit'});
  }
}
async function stopOwnedGui(child,timeout=15000){
  if(child.exitCode!==null||child.signalCode!==null)return;
  await new Promise((resolve,reject)=>{
    const finish=error=>{clearTimeout(timer);child.removeListener('exit',onExit);error?reject(error):resolve();};
    const onExit=()=>finish();
    const timer=setTimeout(()=>finish(Error('owned GUI exit timed out')),timeout);
    // Subscribe before signaling: a successful kill request is not an exit.
    child.once('exit',onExit);
    try{child.kill();}catch(error){finish(error);}
  });
}
async function debuggerClient(port,select){
  const target=await until(async()=>{
    const response=await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(2000)});
    return (await response.json()).find(select);
  },'loopback debugger target');
  const socket=new WebSocket(target.webSocketDebuggerUrl),pending=new Map(),listeners=new Set(),events=[];let next=0;
  socket.addEventListener('message',event=>{
    const value=JSON.parse(event.data);
    if(value.id){const item=pending.get(value.id);if(item){pending.delete(value.id);clearTimeout(item.timer);value.error?item.reject(Error(JSON.stringify(value.error))):item.resolve(value.result);}}
    else{events.push(value);for(const listener of listeners)listener(value);}
  });
  socket.addEventListener('close',()=>{for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('Owned debugger closed'));}pending.clear();});
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  const call=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout: '+method));},15000);
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
  });
  const evaluate=async expression=>{
    const value=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(value.exceptionDetails)throw Error(JSON.stringify(value.exceptionDetails));return value.result.value;
  };
  const event=(method,timeout=60000)=>new Promise((resolve,reject)=>{
    const listener=value=>{if(value.method===method){clearTimeout(timer);listeners.delete(listener);resolve(value.params);}};
    const timer=setTimeout(()=>{listeners.delete(listener);reject(Error('Debugger event timed out: '+method));},timeout);listeners.add(listener);
  });
  return {call,evaluate,event,events,close:()=>socket.close()};
}
async function click(ui,id){
  const point=await ui.evaluate(`(()=>{const e=document.getElementById(${JSON.stringify(id)});if(!e||e.hidden||e.disabled)throw Error('UI action unavailable');e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;if(!r.width||!r.height||x<0||y<0||x>=innerWidth||y>=innerHeight||!e.contains(document.elementFromPoint(x,y)))throw Error('UI action is not reachable');return {x,y};})()`);
  await ui.call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
  await ui.call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
}
async function inspectDesktop(executable,data,version,work,phase,exercise){
  const debug=await freePort(),mainDebug=exercise?await freePort():0,env={...process.env,CMB_DATA_DIR:data};
  for(const key of ['ELECTRON_RUN_AS_NODE','CMB_PYTHON','PYTHONHOME','PYTHONPATH'])delete env[key];
  const args=['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debug];
  if(mainDebug)args.push('--inspect=127.0.0.1:'+mainDebug);
  const child=spawn(executable,args,{env,stdio:['ignore','ignore','pipe']});
  let ui,main,errors='',spawnError;child.stderr.on('data',b=>errors+=b);child.on('error',error=>spawnError=error);
  try{
    ui=await debuggerClient(debug,row=>row.type==='page'&&row.url.endsWith('/desktop/index.html'));
    const state=await until(async()=>{
      if(spawnError)throw spawnError;if(child.exitCode!==null||child.signalCode!==null)throw Error('GUI exited: '+errors);
      return ui.evaluate('typeof snapshot!=="undefined"&&snapshot?({version:snapshot.update.current,dataDir:snapshot.dataDir}):null');
    },'GUI version');
    assert.equal(state.version,version);assert.equal(path.resolve(state.dataDir),data);
    if(exercise){main=await debuggerClient(mainDebug,row=>row.type==='node');assert.equal(await main.evaluate('process.pid'),child.pid);}
    const result=exercise?await exercise({ui,main,child}):undefined;
    if(child.exitCode===null&&child.signalCode===null){const shot=await ui.call('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(work,phase+'-desktop.png'),Buffer.from(shot.data,'base64'));}
    return result;
  }finally{ui?.close();main?.close();await stopOwnedGui(child);console.log(`${phase} GUI exited: code=${child.exitCode} signal=${child.signalCode}`);}
}
function transportScript(directory,includeCandidate=false,includePreview2=false){
  // No signature, key, selection, install or helper logic is replaced. The
  // synthetic release directory cannot supply bytes outside this exact map.
  return `(()=>{const req=process.getBuiltinModule('module').createRequire(process.resourcesPath+'/app.asar/package.json');
    const fs=req('node:fs'),path=req('node:path'),{Readable}=req('node:stream'),electron=req('electron');
    const module=req('./desktop/features/updates/updater.cjs'),directory=${JSON.stringify(directory)};
    const releases=new Map(),files=new Map();for(const [version,folder] of [['2.2.1','old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2']]){
      const release=JSON.parse(fs.readFileSync(path.join(directory,folder,'release.json'),'utf8'));
      if(release.tag_name!=='v'+version||release.draft||release.prerelease!==version.includes('-'))throw Error('Unexpected official acceptance release');
      releases.set(version,release);
      for(const name of fs.readdirSync(path.join(directory,folder)))if(!['SHA256SUMS.txt','release.json'].includes(name))files.set(module.releaseUrl(version,name),path.join(directory,folder,name));
    }
    const catalogue=${JSON.stringify(['2.2.1','2.2.2-preview.1',...(includePreview2?['2.2.2-preview.2']:[])])}.map(version=>releases.get(version));
    ${includeCandidate?`catalogue.push({tag_name:'v${candidateVersion}',draft:false,prerelease:true,assets:[{name:'bridge-update.json'}]});`:''}
    const fetch=async(url,{signal}={})=>{if(signal?.aborted)throw signal.reason;
      acceptance.requests.push(url);
      if(url===module.RELEASES)return new Response(JSON.stringify(catalogue));
      if(url===module.RELEASES+'&page=1')return new Response(JSON.stringify(catalogue));
      if(!files.has(url))throw Error('Acceptance transport refuses unexpected URL: '+url);
      return new Response(Readable.toWeb(fs.createReadStream(files.get(url))));};
    for(const method of ['check','listVersions','selectVersion']){
      const original=module.Updater.prototype[method];if(typeof original!=='function')continue;
      module.Updater.prototype[method]=function(...args){this.fetch=fetch;this.catalogueFetch=fetch;acceptance.updater=this;return Reflect.apply(original,this,args);};
    }
    const show=electron.dialog.showMessageBox.bind(electron.dialog);
    globalThis.acceptance={dialogs:[],response:null,opened:[],packagesOpened:[],requests:[]};
    electron.dialog.showMessageBox=async(...args)=>{const options=args.at(-1);acceptance.dialogs.push(options);
      return acceptance.response===null?show(...args):{response:acceptance.response,checkboxChecked:false};};
    electron.shell.openExternal=async url=>{if(url!=='https://github.com/try2love/codex-mobile-bridge/releases/tag/v2.2.1')throw Error('Unexpected release handoff');acceptance.opened.push(url);};
    electron.shell.openPath=async archive=>{
      const updater=acceptance.updater,candidate=updater.candidate;
      const expected=path.join(updater.directory,module.cacheName(candidate,updater.platform,updater.arch,candidate.format||updater.format));
      if(process.platform!=='linux'||archive!==expected||!archive.endsWith('.deb'))throw Error('Unexpected package handoff');
      acceptance.packagesOpened.push(archive);return '';};
    return {keyUnchanged:true,transport:'synthetic-directory / official-byte-cache'};})()`;
}
function nativeClick(pid,label,platform=process.platform){
  assert.ok(Number.isSafeInteger(pid)&&pid>0);
  if(platform==='darwin'){
    const script=`tell application "System Events"
      set owner to first application process whose unix id is ${pid}
      repeat 30 times
        try
          if exists sheet 1 of window 1 of owner then
            click button ${JSON.stringify(label)} of sheet 1 of window 1 of owner
          else
            click button ${JSON.stringify(label)} of window 1 of owner
          end if
          return "clicked"
        end try
        delay 0.1
      end repeat
      error "Owned native confirmation button unavailable"
    end tell`;
    return execFileSync('osascript',['-e',script],{encoding:'utf8',timeout:15000}).trim();
  }
  if(platform==='win32'){
    const quoted=label.replace(/'/g,"''"),script=`Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes
      $root=[System.Windows.Automation.AutomationElement]::RootElement
      $pidCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,${pid})
      $nameCondition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty,'${quoted}')
      for($attempt=0;$attempt -lt 30;$attempt++){
        $owners=$root.FindAll([System.Windows.Automation.TreeScope]::Children,$pidCondition)
        foreach($owner in $owners){
          if($owner.Current.ClassName -ne '#32770'){continue}
          $button=$owner.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$nameCondition)
          if($button -and $button.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button){
            $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke();Write-Output 'clicked';exit 0
          }
        };Start-Sleep -Milliseconds 100
      };throw 'Owned native confirmation button unavailable'`;
    return execFileSync('powershell',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{encoding:'utf8',timeout:15000}).trim();
  }
  throw Error('Linux native confirmation is covered by controller integration; installer handoff is verified separately');
}
async function selectStable(ui){
  await until(async()=>(await ui.evaluate('snapshot.update')).state!=='checking','initial background check completion');
  await ui.evaluate("tab('updates');loadUpdateVersions()");
  const list=await until(async()=>{
    const state=await ui.evaluate('({value:updateVersionsState.value,loading:updateVersionsState.loading,error:updateVersionsState.error})');
    if(state.error)throw Error(state.error);return !state.loading&&state.value;
  },'official history IPC list');
  assert.equal(list.platform,process.platform);assert.equal(list.arch,process.arch);assert.equal(list.current,candidateVersion);
  const release=list.releases.find(row=>row.version===oldVersion);assert.ok(release);
  const format=process.platform==='linux'?'deb':'zip',asset=release.assets.find(row=>row.format===format);assert.ok(asset);
  await ui.evaluate(`document.getElementById('update-versions-version').value=${JSON.stringify(oldVersion)};document.getElementById('update-versions-version').dispatchEvent(new Event('change',{bubbles:true}));`);
  const value=await until(async()=>{const value=await ui.evaluate('snapshot.update');if(value.state==='error')throw Error(value.message);return value.state==='available'&&value.selected&&value;},'official history IPC selection');
  assert.equal(value.current,candidateVersion);assert.equal(value.version,oldVersion);assert.equal(value.intent,'switch-version');assert.equal(value.requiresConfirmation,true);assert.ok(value.candidateId);
  assert.equal(value.format,format);assert.equal(value.assetName,asset.name);assert.equal(!!value.manualInstall,process.platform==='linux');
  return value;
}
async function confirmation(main,ui,child,response,native){
  if(!native)await main.evaluate('acceptance.response='+response);
  const count=await main.evaluate('acceptance.dialogs.length');await click(ui,'update-versions-install');
  const options=await until(async()=>main.evaluate(`acceptance.dialogs.length>${count}?acceptance.dialogs.at(-1):null`),'main native confirmation request');
  assert.equal(options.defaultId,0);assert.equal(options.cancelId,0);assert.ok(options.detail.includes(candidateVersion+' → '+oldVersion));
  if(native)nativeClick(child.pid,options.buttons[response]);
  return {mode:native?'native-os-click':'controller-confirmation-integration',defaultId:options.defaultId,cancelId:options.cancelId};
}
async function installerHandoffEvidence(main,ui){
  // The renderer settles its install IPC before clearing updateBusy. Observe
  // that completion so a rejected backup or handoff cannot hide in a timeout.
  await until(()=>ui.evaluate('!updateBusy'),'official Linux install IPC completion');
  const renderer=await ui.evaluate('({update:snapshot.update,feedback:lastFeedback})');
  const evidence=await main.evaluate('({update:acceptance.updater.status(),opened:acceptance.opened,packagesOpened:acceptance.packagesOpened})');
  assert.equal(evidence.update.state,'installer-handoff','Official Linux package handoff failed: '+JSON.stringify({renderer,...evidence}));
  return evidence;
}
async function stopLaunchedGui(planFile){
  const transaction=path.dirname(planFile),launched=JSON.parse(await fs.readFile(path.join(transaction,'launched.json'),'utf8'));
  let pid=launched.pid;
  if(process.platform==='darwin'){
    try{const receipt=JSON.parse(await fs.readFile(path.join(transaction,'launch-services.json'),'utf8'));assert.equal(receipt.launcherPid,pid);pid=receipt.pid;}catch(error){if(error.code!=='ENOENT')throw error;}
  }
  assert.ok(Number.isSafeInteger(pid)&&pid>0&&pid!==process.pid);
  try{process.kill(pid);}catch(error){if(error.code!=='ESRCH')throw error;}
  await until(()=>{try{process.kill(pid,0);return false;}catch(error){if(error.code==='ESRCH')return true;throw error;}},'helper-owned GUI exit');
}
async function verifiedArchiveProof(archive,asset,requests,expectedDownloads){
  const url=`https://github.com/try2love/codex-mobile-bridge/releases/download/v${oldVersion}/${asset.name}`;
  const downloads=requests.filter(value=>value===url).length;
  assert.equal(downloads,expectedDownloads,'official selected package downloads in this attempt');
  assert.ok(path.isAbsolute(archive));
  const bytes=await fs.readFile(archive),sha256=createHash('sha256').update(bytes).digest('hex');
  assert.equal(sha256,asset.sha256,'actual retained archive matches trusted package digest');
  return {archive,sha256,downloads};
}
async function stableReturnCacheProof(cache,asset){
  const retained=await verifiedArchiveProof(cache.archive,asset,[],0);
  assert.equal(retained.sha256,cache.sha256);
  return {...cache,retainedAfterSuccess:true,cleanupSupportedByTarget:false,
    reason:'v2.2.1 predates verified-cache cleanup'};
}
async function pausedUpdateEvidence(main,frame){
  // Runtime.evaluate with awaitPromise cannot complete until this pause resumes.
  const value=await main.call('Debugger.evaluateOnCallFrame',{callFrameId:frame.callFrameId,
    expression:'({plan:prepared.plan,archive:candidate.archive,requests:acceptance.requests})',returnByValue:true});
  if(value.exceptionDetails)throw Error(JSON.stringify(value.exceptionDetails));return value.result.value;
}
function automaticHandoffLine(source){
  const lines=source.split('\n'),prepare=lines.findIndex(line=>line.includes("await worker('update-prepare'"));assert.ok(prepare>=0);
  const line=lines.findIndex((value,index)=>index>prepare&&value.trim()==='saveDataDir(dataDir);');assert.ok(line>prepare);return line;
}
async function downloadHistoryPackage(ui,main,files,data){
  const before=await ui.evaluate("bridgeDesktop.updateCache({action:'list'})");
  const backups=await fs.readdir(path.join(data,'update-backups')).catch(error=>{if(error.code==='ENOENT')return [];throw error;});
  const target=process.platform==='darwin'?path.dirname(path.dirname(path.dirname(files.app))):path.dirname(files.app),parent=path.dirname(target);
  const transactions=(await fs.readdir(parent)).filter(name=>name.startsWith('.cmb-update-'));
  const proof={...await main.evaluate('({name:acceptance.updater.candidate.asset.name,sha256:acceptance.updater.candidate.asset.sha256})'),
    digestSource:process.platform==='linux'?'official-release-api':'signed-manifest-and-official-release-api'};
  await ui.evaluate("globalThis.acceptanceDownload=null;bridgeDesktop.downloadUpdate({candidateId:snapshot.update.candidateId}).then(value=>globalThis.acceptanceDownload={value},error=>globalThis.acceptanceDownload={error:error.message});void 0");
  const completed=await until(()=>ui.evaluate('globalThis.acceptanceDownload'),'official download-only IPC completion',180000);
  if(completed.error)throw Error(completed.error);const downloaded=completed.value;
  assert.equal(downloaded.state,'downloaded',downloaded.message);assert.equal(downloaded.intent,'switch-version');
  const cached=before.entries.some(entry=>entry.version===oldVersion&&entry.format===downloaded.format);
  const cache=await verifiedArchiveProof(downloaded.path,proof,await main.evaluate('acceptance.requests'),cached?0:1);
  await ui.evaluate('refresh()');
  const listing=await ui.evaluate("bridgeDesktop.updateCache({action:'list'})"),entry=listing.entries.find(row=>row.id===path.basename(cache.archive));assert.ok(entry);
  assert.equal(entry.size,(await fs.stat(cache.archive)).size);assert.equal(entry.version,oldVersion);assert.equal(entry.format,downloaded.format);assert.equal(entry.inUse,false);
  assert.equal(JSON.parse(await fs.readFile(files.metadata,'utf8')).version,candidateVersion);
  assert.deepEqual(await fs.readdir(path.join(data,'update-backups')).catch(error=>{if(error.code==='ENOENT')return [];throw error;}),backups);
  assert.deepEqual((await fs.readdir(parent)).filter(name=>name.startsWith('.cmb-update-')),transactions);
  return {package:proof,cache,downloadOnly:true,cacheListed:true,installationStarted:false};
}
async function returnThroughUpdater(files,data,work,phase,directory,{failure=false,native=false,manual=false,retryCache}={}){
  let planFile;
  const evidence=await inspectDesktop(files.app,data,candidateVersion,work,phase,async({ui,main,child})=>{
    await main.evaluate(transportScript(directory,false,true));
    const candidate=await selectStable(ui);
    const download=await downloadHistoryPackage(ui,main,files,data);
    if(retryCache){assert.equal(download.cache.archive,retryCache.archive);assert.equal(download.cache.sha256,retryCache.sha256);assert.equal(download.cache.downloads,0);}
    if(manual){
      assert.equal(candidate.manualInstall,true);assert.equal(candidate.manualRelease,undefined);
      const dialog=await confirmation(main,ui,child,1,false);
      await installerHandoffEvidence(main,ui);
      assert.deepEqual(await main.evaluate('acceptance.opened'),[]);
      assert.deepEqual(await main.evaluate('acceptance.packagesOpened'),[download.cache.archive]);
      assert.equal(JSON.parse(await fs.readFile(files.metadata,'utf8')).version,candidateVersion);
      const listing=await ui.evaluate("bridgeDesktop.updateCache({action:'list'})");
      assert.equal(listing.entries.find(entry=>entry.id===path.basename(download.cache.archive)).inUse,true);
      const backups=await fs.readdir(path.join(data,'update-backups'));assert.ok(backups.length);
      return {dialog,...download,manual:true,automaticInstall:false,currentVersionUnchanged:true,configurationBackupRetained:true};
    }
    await main.call('Debugger.enable');
    const script=main.events.find(value=>value.method==='Debugger.scriptParsed'&&/[\\/]desktop[\\/]main\.cjs$/.test(value.params.url));
    assert.ok(script,'the actual packaged main process is debugged');
    const source=(await main.call('Debugger.getScriptSource',{scriptId:script.params.scriptId})).scriptSource;
    const lineNumber=automaticHandoffLine(source);
    await main.call('Debugger.setBreakpoint',{location:{scriptId:script.params.scriptId,lineNumber}});
    const packageProof=download.package;
    const paused=main.event('Debugger.paused',180000);paused.catch(()=>{});
    const dialog=await confirmation(main,ui,child,1,native);
    const pause=await paused,frame=pause.callFrames.find(value=>value.functionName==='installUpdate');assert.ok(frame);
    const prepared=await pausedUpdateEvidence(main,frame);planFile=prepared.plan;
    const cache=await verifiedArchiveProof(prepared.archive,packageProof,prepared.requests,download.cache.downloads);
    if(retryCache){assert.equal(cache.archive,retryCache.archive);assert.equal(cache.sha256,retryCache.sha256);}
    const plan=JSON.parse(await fs.readFile(planFile,'utf8'));
    const target=process.platform==='darwin'?path.dirname(path.dirname(path.dirname(files.app))):path.dirname(files.app);
    assert.equal(plan.target,target);
    assert.equal(plan.dataDir,data);assert.equal(plan.transition.intent,'switch-version');assert.equal(plan.version,oldVersion);
    assert.equal(path.dirname(path.dirname(planFile)),path.dirname(target));
    if(failure){const executable=paths(plan.staged).app;await fs.rename(executable,executable+'.acceptance-broken');}
    await main.call('Debugger.resume');
    main.close();ui.close();
    await until(()=>child.exitCode!==null||child.signalCode!==null,'candidate-owned installer handoff',30000);
    return {dialog,planFile,package:packageProof,cache,downloadOnly:download.downloadOnly,cacheListed:download.cacheListed,failureInjected:failure};
  });
  if(manual)return evidence;
  const result=await until(async()=>JSON.parse(await fs.readFile(path.join(path.dirname(planFile),'result.json'),'utf8')),'actual update helper result',180000);
  assert.equal(result.state,failure?'failed':'updated',JSON.stringify(result));if(failure)assert.equal(result.recovered,true,JSON.stringify(result));
  assert.ok(result.configurationBackup,'pre-replacement data backup');await fs.access(result.configurationBackup);
  const backup=JSON.parse(await fs.readFile(path.join(result.configurationBackup,'backup.json'),'utf8'));
  assert.equal(backup.transition.intent,'switch-version');
  for(const name of ['config.json','desktop.json','accounts/index.json']){
    assert.deepEqual(await fs.readFile(path.join(result.configurationBackup,name)),await fs.readFile(path.join(data,name)),'current live data safety copy: '+name);
  }
  await stopLaunchedGui(planFile);
  if(failure){
    const retained=await verifiedArchiveProof(evidence.cache.archive,evidence.package,[],0);
    assert.equal(retained.sha256,evidence.cache.sha256);
  }
  const cache=failure?{...evidence.cache,retainedAfterFailure:true}:await stableReturnCacheProof(evidence.cache,evidence.package);
  return {...evidence,cache,helper:{state:result.state,recovered:result.recovered,configurationBackupRetained:true}};
}
async function main(){
  assert.equal(process.env.GITHUB_ACTIONS,'true','Only run against an ephemeral GitHub-hosted runner');
  assert.equal(process.env.RUNNER_ENVIRONMENT,'github-hosted','Self-hosted/daily machines are excluded');
  assert.ok(['darwin','win32','linux'].includes(process.platform));
  assert.equal(require('../package.json').version,candidateVersion);
  const {chromium}=require('@playwright/test'),directory=path.resolve('.tmp/published-upgrade'),results=[];
  let native=process.platform!=='linux',nativeLimit;
  try{
    for(const [baseline,folder] of [[oldVersion,'old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2']]){
      const work=path.join(directory,folder+'-bootstrap'),data=path.join(work,'data'),profile=path.join(work,'browser');
      await fs.mkdir(data,{recursive:true});assert.deepEqual(await fs.readdir(data),[],'Never reuse earlier fixture data');
      const target=process.platform==='linux'?'/opt/Codex Mobile Bridge':path.join(work,process.platform==='darwin'?'Codex Mobile Bridge.app':'installed');
      let files=paths(target),worker,originals,scope;const phones=[],accounts=[],drafts=[],accountBytes=new Map();
      const preserved=['config.json','desktop.json','notifications.json','notification-watches.json'],checks=[];
      const port=await freePort(),draftText='Synthetic original draft — 未发送',previewText='Synthetic preview-created draft — 保留';
      const useRuntime=async()=>{
        if(process.platform==='linux')files=paths(path.dirname(await fs.realpath('/usr/bin/codex-mobile-bridge')));
        worker=(action,payload)=>runWorker({executable:files.runtime,dataDir:data},action,payload);
      };
      const verifyVersion=async version=>{
        const metadata=JSON.parse(await fs.readFile(files.metadata,'utf8'));
        assert.equal(metadata.version,version);assert.equal(metadata.arch,process.arch);assert.equal(metadata.platform,process.platform);
        if(version===candidateVersion)assert.equal(metadata.dataFormat,'bridge-data-v1','Candidate installed metadata must declare its reviewed data format');
      };
      const pair=async()=>{
        const snapshot=await worker('snapshot'),entry=snapshot.urls.find(value=>{const url=new URL(value);return url.protocol==='http:'&&!['127.0.0.1','localhost','::1'].includes(url.hostname);});assert.ok(entry,'LAN pairing entry');
        const grant=await worker('pairing',{action:'create',url:entry});
        const response=await fetch(new URL('/api/pair',entry),{method:'POST',headers:{Origin:new URL(entry).origin,'Content-Type':'application/json','User-Agent':'BridgeMobile/0.1-Android'},body:JSON.stringify({token:new URL(grant.url).hash.slice('#pair='.length)})});
        assert.equal(response.status,200);await response.json();const cookie=response.headers.get('set-cookie').split(';')[0];
        assert.match(cookie,/^codex_mobile_session(?:_[a-f0-9]{24})?=/);phones.push({entry,cookie});
      };
      const verifyData=async(phase,{seed=false,preview=false}={})=>{
        await worker('start');await until(async()=>(await fetch(`http://127.0.0.1:${port}/api/auth`,{signal:AbortSignal.timeout(2000)})).ok,'fixture gateway');
        if(seed||preview){
          await pair();
          const before=new Set(accounts),value=await worker('accounts',{action:'addApi',name:preview?'Synthetic preview account':'Synthetic original account',baseUrl:'https://fixture.invalid/v1',apiKey:preview?'synthetic-preview-key':'synthetic-original-key',model:'gemini-fixture'});
          const added=value.accounts.filter(row=>!before.has(row.id));assert.equal(added.length,1);accounts.push(added[0].id);
          accountBytes.set(added[0].id,await fs.readFile(path.join(data,'accounts',added[0].id,'api.json')));
        }
        for(const phone of phones){
          const response=await fetch(new URL('/api/auth',phone.entry),{headers:{Cookie:phone.cookie,'User-Agent':'BridgeMobile/0.1-Android'}});assert.equal(response.status,200);
          const auth=await response.json();assert.equal(auth.authenticated,true);assert.equal(auth.trustedDevice,true);
          const state=JSON.parse(await fs.readFile(path.join(data,'auth-sessions.json'),'utf8')),key=createHash('sha256').update(phone.cookie.slice(phone.cookie.indexOf('=')+1)).digest('hex');
          assert.equal(state.sessions[key]?.trustedDevice,true);assert.equal(state.sessions[key]?.expires,0);
          if(!scope)scope={gateway:auth.draftIdentity,provider:'codex',account:'synthetic-account',host:'local',session:'synthetic-original-chat'};
          assert.equal(auth.draftIdentity,scope.gateway,'same verified draft gateway');
        }
        assert.deepEqual((await worker('accounts',{action:'list'})).accounts.map(row=>row.id).sort(),[...accounts].sort());
        for(const [id,bytes] of accountBytes)assert.deepEqual(await fs.readFile(path.join(data,'accounts',id,'api.json')),bytes,'saved synthetic account credential bytes');
        if(seed)drafts.push({scope,text:draftText});
        if(preview)drafts.push({scope:{...scope,session:'synthetic-preview-created-chat'},text:previewText});
        const browser=await chromium.launchPersistentContext(profile,{headless:true,userAgent:'BridgeMobile/0.1-Android'});
        try{
          if(seed){const phone=phones[0],split=phone.cookie.indexOf('=');await browser.addCookies([{name:phone.cookie.slice(0,split),value:phone.cookie.slice(split+1),url:phone.entry,expires:Math.floor(Date.now()/1000)+86400,httpOnly:true,sameSite:'Lax'}]);}
          const page=await browser.newPage();await page.goto(phones[0].entry,{waitUntil:'networkidle'});
          assert.equal((await page.evaluate(async()=>await(await fetch('/api/auth')).json())).trustedDevice,true,'original persistent browser cookie');
          await page.waitForFunction(()=>typeof ComposerDrafts!=='undefined');
          for(let i=0;i<drafts.length;i++){
            const draft=drafts[i],value=await page.evaluate(({scope,text,write})=>{const store=new ComposerDrafts();if(write)store.save(scope,{text,selectionStart:2,selectionEnd:2});return store.load(scope);},{...draft,write:seed||preview&&i===drafts.length-1});
            assert.equal(value.text,draft.text);assert.equal(value.selectionStart,2);
          }
          await page.screenshot({path:path.join(work,phase+'-browser.png'),fullPage:true});
        }finally{await browser.close();}
        if(!originals)originals=await Promise.all(preserved.map(name=>fs.readFile(path.join(data,name))));
        for(let i=0;i<preserved.length;i++)assert.deepEqual(await fs.readFile(path.join(data,preserved[i])),originals[i],phase+': '+preserved[i]);
        checks.push({phase,accounts:accounts.length,trustedBindings:phones.length,drafts:drafts.length,configurationBytes:true,persistedBrowserCookie:true});
      };
      try{
        const baselineArchive=path.join(directory,folder,packageName(baseline)),candidateArchive=path.join(directory,'candidate',packageName(candidateVersion));
        const archiveDigests={};for(const archive of [baselineArchive,candidateArchive])archiveDigests[path.basename(archive)]=createHash('sha256').update(await fs.readFile(archive)).digest('hex');
        await install(baselineArchive,target,work,'baseline');await useRuntime();await verifyVersion(baseline);
        await fs.mkdir(path.join(data,'synthetic-codex'));
        const settings=await worker('snapshot');Object.assign(settings.preferences,{port,lan:true,localAccess:true,autoStart:false,tunnel:false,connections:[],codexHome:path.join(data,'synthetic-codex'),ipcPath:path.join(data,'fixture.sock')});
        Object.assign(settings.auth,{username:'admin',password:randomUUID(),sessionHours:0});await worker('save',settings);await fs.writeFile(path.join(data,'notification-watches.json'),'[]\n');
        const policy=baselinePolicy(baseline);
        await inspectDesktop(files.app,data,baseline,work,'baseline',async({ui,main})=>{
          await ui.evaluate("tab('updates')");
          if(policy.state==='unsupported'){
            assert.equal((await ui.evaluate('snapshot.update')).state,'unsupported');
            assert.equal(await ui.evaluate("document.getElementById('check-update').disabled"),true);
            return;
          }
          await main.evaluate(transportScript(directory,policy.includeCandidate,policy.channelDiscoveryTested));
          await until(async()=>(await ui.evaluate('snapshot.update')).state!=='checking','baseline background check completion');
          await click(ui,'check-update');
          const value=await until(async()=>{const value=await ui.evaluate('snapshot.update');return !['idle','checking'].includes(value.state)&&value;},'legacy stable filtering');
          assert.equal(value.state,policy.state,'official baseline release-directory behavior');
          if(policy.channelDiscoveryTested){
            assert.equal(value.currentChannel,'preview');assert.equal(value.channel,'preview');
            await ui.evaluate("document.getElementById('update-channel').value='stable';document.getElementById('update-channel').dispatchEvent(new Event('change',{bubbles:true}));");
            const stable=await until(async()=>{const value=await ui.evaluate('snapshot.update');if(value.state==='error')throw Error(value.message);return value.state==='available'&&value;},'preview2 signed stable discovery');
            assert.equal(stable.version,oldVersion);assert.equal(stable.intent,'return-stable');assert.equal(stable.requiresConfirmation,true);assert.ok(stable.candidateId);
            await ui.evaluate("document.getElementById('update-channel').value='preview';document.getElementById('update-channel').dispatchEvent(new Event('change',{bubbles:true}));");
            await until(async()=>{const value=await ui.evaluate('snapshot.update');return value.channel==='preview'&&value.state==='current';},'preview2 channel restored');
          }
        });
        await verifyData('baseline',{seed:true});await worker('stop');
        await install(candidateArchive,target,work,'candidate');await useRuntime();await verifyVersion(candidateVersion);
        await inspectDesktop(files.app,data,candidateVersion,work,'candidate');await verifyData('candidate',{preview:true});
        // First prove cancel leaves the verified candidate and live data intact.
        let cancellation;
        const cancel=()=>inspectDesktop(files.app,data,candidateVersion,work,'cancel-return',async({ui,main,child})=>{
          await main.evaluate(transportScript(directory,false,true));await selectStable(ui);
          const download=await downloadHistoryPackage(ui,main,files,data);
          const dialog=await confirmation(main,ui,child,0,native);
          await delay(300);assert.equal((await ui.evaluate('snapshot.update')).state,'downloaded');await verifyVersion(candidateVersion);return {...dialog,...download};
        });
        try{cancellation=await cancel();}catch(error){
          if(!native||!/(native confirmation|osascript|powershell|button unavailable|not allowed|assistive)/i.test(error.message))throw error;
          nativeLimit=error.message.slice(0,800);native=false;console.log('Native UI limitation; continuing explicit controller confirmation integration: '+nativeLimit);cancellation=await cancel();
        }
        await verifyData('cancelled-return');
        let failure,returned;
        if(process.platform==='linux'){
          returned=await returnThroughUpdater(files,data,work,'manual-return',directory,{manual:true,retryCache:cancellation.cache});await verifyData('manual-handoff');await worker('stop');
          await install(path.join(directory,'old',packageName(oldVersion)),target,work,'manual-stable');await useRuntime();
          returned.cache=await stableReturnCacheProof(returned.cache,returned.package);
        }else{
          failure=await returnThroughUpdater(files,data,work,'failed-return',directory,{failure:true,native,retryCache:cancellation.cache});await verifyVersion(candidateVersion);
          await inspectDesktop(files.app,data,candidateVersion,work,'recovered-candidate');await verifyData('recovered-candidate');
          returned=await returnThroughUpdater(files,data,work,'return-stable',directory,{native,retryCache:failure.cache});await useRuntime();
        }
        await verifyVersion(oldVersion);await inspectDesktop(files.app,data,oldVersion,work,'returned-stable');await verifyData('returned-stable');
        const result={ok:true,baseline,baselineUpdate:policy,candidate:candidateVersion,returned:oldVersion,archiveDigests,cancellation,failure,returnedByUpdater:returned,checks,
          transport:'fixture release directory; original official manifests/packages and API digests; shipped public key',nativeUiLimitation:nativeLimit||null,dataSnapshotRestored:false};
        results.push(result);await fs.writeFile(path.join(work,'result.json'),JSON.stringify(result,null,2)+'\n');console.log('PASS official '+baseline+' bootstrap and stable return');
      }finally{if(worker)await worker('stop').catch(error=>console.error('Owned gateway cleanup: '+error.message));}
    }
    await fs.writeFile(path.join(directory,'result.json'),JSON.stringify({ok:true,results,nativeUiLimitation:nativeLimit||null},null,2)+'\n');
  }catch(error){await fs.writeFile(path.join(directory,'result.json'),JSON.stringify({ok:false,results,error:error.message,nativeUiLimitation:nativeLimit||null},null,2)+'\n');throw error;}
}
module.exports={installerHandoffEvidence,click,packageName,paths,baselinePolicy,stopOwnedGui,debuggerClient,transportScript,nativeClick,verifiedArchiveProof,stableReturnCacheProof,pausedUpdateEvidence,automaticHandoffLine};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
