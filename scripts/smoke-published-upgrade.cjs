'use strict';
// Official P3/P4 -> production Updater -> CI candidate; older baselines bootstrap manually.
// Hosted runners only. Debugger transport uses official bytes and the shipped key.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),net=require('node:net');
const {createHash,randomUUID}=require('node:crypto');
const {runWorker}=require('../desktop/shared/controller.cjs');
const oldVersion='2.2.1',candidateVersion='2.2.2-preview.4';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function baselinePolicy(version,platform=process.platform){
  assert.ok([oldVersion,'2.2.2-preview.1','2.2.2-preview.2','2.2.2-preview.3',candidateVersion].includes(version),'Unknown official baseline');
  if(['2.2.2-preview.2','2.2.2-preview.3',candidateVersion].includes(version))return {state:'current',includeCandidate:false,previewDiscoveryTested:false,channelDiscoveryTested:true};
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
async function install(archive,target,work,phase,reinstall=false){
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
    execFileSync('sudo',['apt-get','install','-y','--allow-downgrades',...(reinstall?['--reinstall']:[]),archive],{timeout:180000,stdio:'inherit'});
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
async function prepareCandidateRelease(directory,platform=process.platform,arch=process.arch,key){
  const {manifest,releaseUrl}=require('../desktop/features/updates/updater.cjs');
  key??=await fs.readFile(path.join(__dirname,'../desktop/features/updates/update-public-key.pem'));
  const candidate=path.join(directory,'candidate'),bytes=await fs.readFile(path.join(candidate,'bridge-update.json'));
  const value=manifest(bytes,key,{current:'2.2.2-preview.3',platform,arch,expected:candidateVersion,channel:'preview'});
  const zip=await fs.readFile(path.join(candidate,value.asset.name));
  assert.equal(zip.length,value.asset.size,'candidate ZIP size from CI signature');
  assert.equal(createHash('sha256').update(zip).digest('hex'),value.asset.sha256,'candidate ZIP digest from CI signature');
  const release={tag_name:'v'+candidateVersion,draft:false,prerelease:true,published_at:'2026-11-25T00:00:00Z',
    body:'CI candidate for isolated acceptance',assets:[
      {name:'bridge-update.json',browser_download_url:releaseUrl(candidateVersion,'bridge-update.json')},
      {...value.asset,digest:'sha256:'+value.asset.sha256,browser_download_url:releaseUrl(candidateVersion,value.asset.name)}]};
  await fs.writeFile(path.join(candidate,'release.json'),JSON.stringify(release,null,2)+'\n');
  return {asset:value.asset,release,manifestSha256:createHash('sha256').update(bytes).digest('hex')};
}
async function originalPreview4Proof(directory,candidate,platform=process.platform,arch=process.arch,key){
  const {manifest,releaseUrl}=require('../desktop/features/updates/updater.cjs');
  key??=await fs.readFile(path.join(__dirname,'../desktop/features/updates/update-public-key.pem'));
  const original=path.join(directory,'preview4');
  const value=manifest(await fs.readFile(path.join(original,'bridge-update.json')),key,
    {current:candidateVersion,platform,arch,expected:candidateVersion,channel:'preview',intent:'switch-version'});
  const release=JSON.parse(await fs.readFile(path.join(original,'release.json'),'utf8'));
  assert.equal(release.tag_name,'v'+candidateVersion);
  const asset=release.assets.find(row=>row.name===value.asset.name);assert.ok(asset);
  assert.equal(asset.digest,'sha256:'+value.asset.sha256,'original Preview4 official API digest');
  assert.equal(asset.size,value.asset.size);assert.equal(asset.browser_download_url,releaseUrl(candidateVersion,asset.name));
  const bytes=await fs.readFile(path.join(original,asset.name));
  assert.equal(bytes.length,asset.size);assert.equal(createHash('sha256').update(bytes).digest('hex'),value.asset.sha256);
  assert.notEqual(candidate.sha256,value.asset.sha256,'hotfix ZIP must be different from original Preview4');
  return value.asset;
}
function transportScript(directory,includeCandidate=false,includePreview2=false,includePreview3=false,installCandidate=false,includePreview4=false){
  // No signature, key, selection, install or helper logic is replaced. The
  // synthetic release directory cannot supply bytes outside this exact map.
  return `(()=>{const req=process.getBuiltinModule('module').createRequire(process.resourcesPath+'/app.asar/package.json');
    const fs=req('node:fs'),path=req('node:path'),{Readable}=req('node:stream'),electron=req('electron');
    const module=req('./desktop/features/updates/updater.cjs'),directory=${JSON.stringify(directory)};
    const releases=new Map(),files=new Map();for(const [version,folder] of ${JSON.stringify([[oldVersion,'old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2'],['2.2.2-preview.3','preview3'],...(installCandidate?[[candidateVersion,'candidate']]:includePreview4?[[candidateVersion,'preview4']]:[])])}){
      const release=JSON.parse(fs.readFileSync(path.join(directory,folder,'release.json'),'utf8'));
      if(release.tag_name!=='v'+version||release.draft||release.prerelease!==version.includes('-'))throw Error('Unexpected official acceptance release');
      releases.set(version,release);
      for(const name of fs.readdirSync(path.join(directory,folder)))if(!['SHA256SUMS.txt','release.json'].includes(name))files.set(module.releaseUrl(version,name),path.join(directory,folder,name));
    }
    const catalogue=${JSON.stringify(['2.2.1','2.2.2-preview.1',...(includePreview2?['2.2.2-preview.2']:[]),...(includePreview3?['2.2.2-preview.3']:[])])}.map(version=>releases.get(version));
    ${installCandidate||includePreview4?`catalogue.push(releases.get('${candidateVersion}'));`:includeCandidate?`catalogue.push({tag_name:'v${candidateVersion}',draft:false,prerelease:true,assets:[{name:'bridge-update.json'}]});`:''}
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
async function confirmation(main,ui,child,response,native,{from=candidateVersion,to=oldVersion}={}){
  if(!native)await main.evaluate('acceptance.response='+response);
  const count=await main.evaluate('acceptance.dialogs.length');await click(ui,'update-versions-install');
  const options=await until(async()=>main.evaluate(`acceptance.dialogs.length>${count}?acceptance.dialogs.at(-1):null`),'main native confirmation request');
  assert.equal(options.defaultId,0);assert.equal(options.cancelId,0);assert.ok(options.detail.includes(from+' → '+to));
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
async function verifiedArchiveProof(archive,asset,requests,expectedDownloads,version=oldVersion){
  const url=`https://github.com/try2love/codex-mobile-bridge/releases/download/v${version}/${asset.name}`;
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
    await main.evaluate(transportScript(directory,false,true,true));
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
async function credentialBoundaryProof(files,work,worker=runWorker){
  // This separate profile contains synthetic CMB1 headers, not real credentials.
  // The normal account/pairing/draft fixture deliberately remains legacy-readable.
  const dataDir=path.join(work,'credential-boundary'),vault=path.join(dataDir,'.credential-vault');
  await fs.mkdir(vault,{recursive:true,mode:0o700});
  const records={'config.json':Buffer.from(JSON.stringify({auth:{mode:'none'}})),
    '.credential-vault/key':Buffer.alloc(32,7),'.credential-vault/connections.enc':Buffer.from('CMB1synthetic-acceptance-ciphertext'),
    'desktop-sessions/settings.user.json':Buffer.from('{"synthetic":"preserve"}\n')};
  for(const [name,bytes] of Object.entries(records)){
    await fs.mkdir(path.dirname(path.join(dataDir,name)),{recursive:true});
    await fs.writeFile(path.join(dataDir,name),bytes,{flag:'wx',mode:0o600});
  }
  const target=process.platform==='darwin'?path.dirname(path.dirname(path.dirname(files.app))):path.dirname(files.app),blocked=[];
  for(const version of [oldVersion,'2.2.2-preview.3'])for(const manualInstall of [false,true]){
    const transition={intent:version===oldVersion?'return-stable':'switch-version',from:candidateVersion,to:version,dataFormat:'bridge-data-v1'};
    await assert.rejects(worker({executable:files.runtime,dataDir},'update-backup',{target,version,transition,manualInstall}),/凭据/);
    await assert.rejects(fs.access(path.join(dataDir,'update-backups')),error=>error.code==='ENOENT');
    for(const [name,bytes] of Object.entries(records))assert.deepEqual(await fs.readFile(path.join(dataDir,name)),bytes,'blocked credential switch preserves '+name);
    blocked.push({version,manualInstall});
  }
  return {syntheticCmb1:true,blocked,configurationBytesUnchanged:true,backupCreated:false,
    legacyReturnFixtureHasCmb1:false,decryptionTested:false};
}
async function seedLegacyConnection(data,files){
  // Seed only while P3 is paused after preparation: its old renderer must not
  // read the synthetic legacy index. The actual plan/runtime are never edited.
  const desktop=await fs.readFile(path.join(data,'desktop.json')),preferences=JSON.parse(desktop);
  const id='acceptance-legacy-cloudflare',index=Buffer.from(JSON.stringify({[id]:true})+'\n');
  assert.deepEqual(preferences.connections,[]);
  await assert.rejects(fs.access(path.join(data,'connection-vault.json')),{code:'ENOENT'});
  preferences.cloudflared=files.runtime; // Executable exists; pending credentials must prevent launching it.
  preferences.connections=[{id,name:'Synthetic legacy Cloudflare',enabled:true,accessMode:'cloudflare',publicUrl:'https://acceptance.invalid',
    proxyUpstream:'',sshTarget:'',sshRemotePort:18787,sshAuth:'config',sshHost:'',sshPort:22,sshUser:'',sshKeyPath:'',
    tailscaleMode:'funnel',tailscalePort:443,tailscaleNodeId:''}];
  const saved=Buffer.from(JSON.stringify(preferences,null,2)+'\n');
  await fs.writeFile(path.join(data,'desktop.json'),saved,{mode:0o600});
  await fs.writeFile(path.join(data,'connection-vault.json'),index,{mode:0o600,flag:'wx'});
  return {id,index,desktop,saved};
}
async function verifyUpgradeReceipt(planFile,worker){
  const transaction=path.dirname(planFile),plan=JSON.parse(await fs.readFile(planFile,'utf8'));
  const result=JSON.parse(await fs.readFile(path.join(transaction,'result.json'),'utf8'));
  assert.equal(result.state,'updated',JSON.stringify(result));assert.equal(result.version,candidateVersion);
  assert.equal(plan.version,candidateVersion);
  const ack=JSON.parse(await fs.readFile(path.join(transaction,'ack.json'),'utf8'));
  assert.deepEqual(ack,{token:plan.token,version:plan.version,dataDir:plan.dataDir},'new candidate GUI acknowledgement');
  assert.equal(plan.runtime.running,true,'original gateway was running');
  const snapshot=await worker('snapshot');assert.equal(snapshot.runtime.running,true,'actual restored gateway');
  return {state:result.state,acknowledged:true,gatewayRunning:true,configurationBackup:result.configurationBackup};
}
async function selectSameVersionHotfix(ui,main,candidate){
  await ui.evaluate("tab('updates');loadUpdateVersions()");
  await until(async()=>(await ui.evaluate('({loading:updateVersionsState.loading,error:updateVersionsState.error})')).loading===false,'original P4 history initial load');
  const before=await main.evaluate("acceptance.requests.filter(url=>url.includes('/releases?per_page=100&page=')).length");
  await click(ui,'update-versions-refresh'); // The shipped handler uses loadUpdateVersions(true).
  const list=await until(async()=>{
    const state=await ui.evaluate('({value:updateVersionsState.value,loading:updateVersionsState.loading,error:updateVersionsState.error})');
    if(state.error)throw Error(state.error);return !state.loading&&state.value;
  },'original P4 forced history refresh');
  const after=await main.evaluate("acceptance.requests.filter(url=>url.includes('/releases?per_page=100&page=')).length");
  assert.ok(after>before,'force refresh must fetch new official asset metadata');
  assert.equal(list.current,candidateVersion);
  const asset=list.releases.find(row=>row.version===candidateVersion)?.assets.find(row=>row.name===candidate.name);assert.ok(asset);
  const digest=await main.evaluate(`acceptance.updater.catalogueAssets.get(${JSON.stringify(asset.id)}).asset.sha256`);
  assert.equal(digest,candidate.sha256,'refreshed catalogue uses hotfix digest');
  await ui.evaluate(`document.getElementById('update-versions-version').value=${JSON.stringify(candidateVersion)};document.getElementById('update-versions-version').dispatchEvent(new Event('change',{bubbles:true}));`);
  return until(async()=>{const value=await ui.evaluate('snapshot.update');if(value.state==='error')throw Error(value.message);return value.state==='available'&&value.selected&&value;},'original P4 same-version hotfix selection');
}
async function upgradeThroughOriginalUpdater(files,data,work,directory,worker,baseline){
  const sameVersion=baseline===candidateVersion,intent=sameVersion?'switch-version':'update';
  const candidate=await prepareCandidateRelease(directory),original=sameVersion?await originalPreview4Proof(directory,candidate.asset):null;let planFile,legacy;
  const evidence=await inspectDesktop(files.app,data,baseline,work,sameVersion?'preview4-in-app-hotfix':'preview3-in-app-upgrade',async({ui,main,child})=>{
    await until(async()=>(await ui.evaluate('snapshot.update')).state!=='checking','original background check completion');
    await main.evaluate(transportScript(directory,true,true,true,true));
    let selected;
    if(sameVersion)selected=await selectSameVersionHotfix(ui,main,candidate.asset);
    else{
      await ui.evaluate("tab('updates')");await click(ui,'check-update');
      selected=await until(async()=>{const state=await ui.evaluate('snapshot.update');if(state.state==='error')throw Error(state.message);return state.state==='available'&&state;},'P3 signed P4 discovery');
    }
    assert.equal(selected.current,baseline);assert.equal(selected.version,candidateVersion);assert.equal(selected.intent,intent);assert.equal(selected.requiresConfirmation,sameVersion);
    const shippedKey=await main.evaluate("acceptance.updater.key.toString()");
    assert.equal(shippedKey,await fs.readFile(path.join(__dirname,'../desktop/features/updates/update-public-key.pem'),'utf8'),'original public key');
    await main.call('Debugger.enable');
    const script=main.events.find(value=>value.method==='Debugger.scriptParsed'&&/[\\/]desktop[\\/]main\.cjs$/.test(value.params.url));assert.ok(script);
    const source=(await main.call('Debugger.getScriptSource',{scriptId:script.params.scriptId})).scriptSource;
    await main.call('Debugger.setBreakpoint',{location:{scriptId:script.params.scriptId,lineNumber:automaticHandoffLine(source)}});
    const paused=main.event('Debugger.paused',180000);paused.catch(()=>{});
    if(sameVersion)await confirmation(main,ui,child,1,false,{from:baseline,to:candidateVersion});else await click(ui,'install-update');
    const pause=await paused,frame=pause.callFrames.find(value=>value.functionName==='installUpdate');assert.ok(frame);
    const prepared=await pausedUpdateEvidence(main,frame);planFile=prepared.plan;
    const archive=await verifiedArchiveProof(prepared.archive,candidate.asset,prepared.requests,1,candidateVersion);
    const plan=JSON.parse(await fs.readFile(planFile,'utf8'));
    assert.equal(plan.version,candidateVersion);assert.equal(plan.dataDir,data);assert.equal(plan.transition.intent,intent);assert.equal(plan.runtime.running,true);
    assert.equal(plan.target,process.platform==='darwin'?path.dirname(path.dirname(path.dirname(files.app))):path.dirname(files.app));
    if(process.platform==='darwin'&&!sameVersion)legacy=await seedLegacyConnection(data,files);
    await main.call('Debugger.resume');main.close();ui.close();
    await until(()=>child.exitCode!==null||child.signalCode!==null,'original updater handoff',30000);
    return {archive,manifestSha256:candidate.manifestSha256,shippedPublicKey:true,originalUpdater:true,
      ...(sameVersion?{originalPackage:original,forcedCatalogueRefresh:true,sameVersion:true}:{})};
  });
  const result=await until(async()=>JSON.parse(await fs.readFile(path.join(path.dirname(planFile),'result.json'),'utf8')),'original helper actual P4 result',180000);
  assert.equal(result.state,'updated',JSON.stringify(result));
  const receipt=await verifyUpgradeReceipt(planFile,worker);
  assert.ok(receipt.configurationBackup);await fs.access(receipt.configurationBackup);
  const backup=JSON.parse(await fs.readFile(path.join(receipt.configurationBackup,'backup.json'),'utf8'));
  assert.equal(backup.transition.intent,intent);
  for(const name of ['config.json','desktop.json','accounts/index.json',...(legacy?['connection-vault.json']:[])]){
    assert.deepEqual(await fs.readFile(path.join(receipt.configurationBackup,name)),await fs.readFile(path.join(data,name)),'original updater retained final data backup: '+name);
  }
  await stopLaunchedGui(planFile);
  let pending;
  if(legacy){
    pending=await inspectDesktop(files.app,data,candidateVersion,work,'preview4-legacy-pending',async({ui})=>{
      await ui.evaluate(`tab('network');document.querySelector('[data-connection-id="${legacy.id}"]').open=true`);
      const visible=await until(async()=>ui.evaluate(`(()=>{const banner=document.getElementById('credential-migration'),button=document.querySelector('[data-connection-action="migrateCredentials"][data-connection="${legacy.id}"]');return banner&&!banner.hidden&&button&&button.getClientRects().length?{banner:banner.textContent,migration:button.textContent,state:snapshot.externalStatus[${JSON.stringify(legacy.id)}],runtime:snapshot.runtime}:null;})()`),'P4 visible migration status and action');
      assert.match(visible.banner,/迁移旧凭据|saved credentials migrated/);assert.match(visible.migration,/迁移旧连接凭据|Migrate/);
      assert.equal(visible.runtime.running,true);assert.equal(visible.state.state,'credentials-required');assert.equal(visible.state.pid,visible.runtime.pid);
      return {gatewayRunning:true,status:'credentials-required',banner:visible.banner,migrationAction:visible.migration};
    });
    assert.deepEqual(await fs.readFile(path.join(data,'desktop.json')),legacy.saved,'enabled connection unchanged');
    assert.deepEqual(await fs.readFile(path.join(data,'connection-vault.json')),legacy.index,'legacy bool index was not migrated');
    await assert.rejects(fs.access(path.join(data,'.credential-vault/connections.enc')),{code:'ENOENT'});
    // Only this additional fixture is removed before the existing legacy return.
    await fs.writeFile(path.join(data,'desktop.json'),legacy.desktop);await fs.unlink(path.join(data,'connection-vault.json'));
    pending={...pending,indexUnchanged:true,encryptedVaultCreated:false,fixtureRemovedBeforeStableReturn:true,
      keychainReadVerification:'not instrumented in package; source backend trap plus packaged pending/index evidence'};
  }
  return {...evidence,...receipt,legacy:pending||null};
}
async function main(){
  assert.equal(process.env.GITHUB_ACTIONS,'true','Only run against an ephemeral GitHub-hosted runner');
  assert.equal(process.env.RUNNER_ENVIRONMENT,'github-hosted','Self-hosted/daily machines are excluded');
  assert.ok(['darwin','win32','linux'].includes(process.platform));
  assert.equal(require('../package.json').version,candidateVersion);
  const {chromium}=require('@playwright/test'),directory=path.resolve('.tmp/published-upgrade'),results=[];
  let native=process.platform!=='linux',nativeLimit;
  try{
    for(const [baseline,folder] of [[oldVersion,'old'],['2.2.2-preview.1','preview1'],['2.2.2-preview.2','preview2'],['2.2.2-preview.3','preview3'],[candidateVersion,'preview4']]){
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
        const archiveDigests={};for(const archive of [baselineArchive,candidateArchive])archiveDigests[(baseline===candidateVersion&&archive===baselineArchive?'original-':'')+path.basename(archive)]=createHash('sha256').update(await fs.readFile(archive)).digest('hex');
        if(baseline===candidateVersion)assert.notEqual(archiveDigests['original-'+path.basename(baselineArchive)],archiveDigests[path.basename(candidateArchive)],'same-version candidate package must differ from original baseline');
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
          await main.evaluate(transportScript(directory,policy.includeCandidate,policy.channelDiscoveryTested,['2.2.2-preview.3',candidateVersion].includes(baseline),false,baseline===candidateVersion));
          await until(async()=>(await ui.evaluate('snapshot.update')).state!=='checking','baseline background check completion');
          await click(ui,'check-update');
          const value=await until(async()=>{const value=await ui.evaluate('snapshot.update');return !['idle','checking'].includes(value.state)&&value;},'legacy stable filtering');
          assert.equal(value.state,policy.state,'official baseline release-directory behavior');
          if(policy.channelDiscoveryTested){
            assert.equal(value.currentChannel,'preview');assert.equal(value.channel,'preview');
            await ui.evaluate("document.getElementById('update-channel').value='stable';document.getElementById('update-channel').dispatchEvent(new Event('change',{bubbles:true}));");
            const stable=await until(async()=>{const value=await ui.evaluate('snapshot.update');if(value.state==='error')throw Error(value.message);return value.state==='available'&&value;},baseline+' signed stable discovery');
            assert.equal(stable.version,oldVersion);assert.equal(stable.intent,'return-stable');assert.equal(stable.requiresConfirmation,true);assert.ok(stable.candidateId);
            await ui.evaluate("document.getElementById('update-channel').value='preview';document.getElementById('update-channel').dispatchEvent(new Event('change',{bubbles:true}));");
            await until(async()=>{const value=await ui.evaluate('snapshot.update');return value.channel==='preview'&&value.state==='current';},baseline+' channel restored');
          }
        });
        await verifyData('baseline',{seed:true});
        let upgradedByPreview3,reinstalledPreview4;
        if(['2.2.2-preview.3',candidateVersion].includes(baseline)&&process.platform!=='linux'){
          const upgraded=await upgradeThroughOriginalUpdater(files,data,work,directory,worker,baseline);
          if(baseline===candidateVersion)reinstalledPreview4=upgraded;else upgradedByPreview3=upgraded;
        }else{await worker('stop');await install(candidateArchive,target,work,'candidate',baseline===candidateVersion);}
        await useRuntime();await verifyVersion(candidateVersion);
        await inspectDesktop(files.app,data,candidateVersion,work,'candidate');await verifyData('candidate',{preview:true});
        const credentialBoundary=await credentialBoundaryProof(files,work);
        // First prove cancel leaves the verified candidate and live data intact.
        let cancellation;
        const cancel=()=>inspectDesktop(files.app,data,candidateVersion,work,'cancel-return',async({ui,main,child})=>{
          await main.evaluate(transportScript(directory,false,true,true));await selectStable(ui);
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
        const result={ok:true,baseline,baselineUpdate:policy,candidate:candidateVersion,returned:oldVersion,archiveDigests,upgradedByPreview3,reinstalledPreview4,cancellation,failure,returnedByUpdater:returned,credentialBoundary,checks,
          transport:'fixture release directory; original official baseline bytes and signed CI candidate; shipped public key',nativeUiLimitation:nativeLimit||null,dataSnapshotRestored:false};
        results.push(result);await fs.writeFile(path.join(work,'result.json'),JSON.stringify(result,null,2)+'\n');console.log('PASS official '+baseline+' bootstrap and stable return');
      }finally{if(worker)await worker('stop').catch(error=>console.error('Owned gateway cleanup: '+error.message));}
    }
    await fs.writeFile(path.join(directory,'result.json'),JSON.stringify({ok:true,results,nativeUiLimitation:nativeLimit||null},null,2)+'\n');
  }catch(error){await fs.writeFile(path.join(directory,'result.json'),JSON.stringify({ok:false,results,error:error.message,nativeUiLimitation:nativeLimit||null},null,2)+'\n');throw error;}
}
module.exports={installerHandoffEvidence,click,packageName,paths,baselinePolicy,stopOwnedGui,debuggerClient,transportScript,nativeClick,verifiedArchiveProof,stableReturnCacheProof,pausedUpdateEvidence,automaticHandoffLine,credentialBoundaryProof,prepareCandidateRelease,verifyUpgradeReceipt,originalPreview4Proof,selectSameVersionHotfix};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
