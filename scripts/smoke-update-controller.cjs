'use strict';
// Exercise the real UI/controller handoff without externally killing Electron:
// renderer button -> main installUpdate -> helper ready -> app.exit -> swap.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),sync=require('node:fs');
const path=require('node:path'),net=require('node:net'),{spawn}=require('node:child_process');
const asar=require('@electron/asar');
const root=path.resolve(__dirname,'..'),currentVersion=require('../package.json').version;
// Increment the patch for stable builds, or the sequence for preview builds.
const nextVersion=currentVersion.replace(/\d+$/,number=>String(Number(number)+1));
const quickTunnel=process.argv.includes('--quick-tunnel');
const mac=process.platform==='darwin',python=process.platform==='win32'?'python':'python3';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(callback,label,ms=45000){
  const end=Date.now()+ms;let last;
  while(Date.now()<end){try{const result=await callback();if(result)return result;}catch(error){last=error;}await delay(200);}
  throw Error(label+' timed out'+(last?`: ${last&&last.message}`:''));
}
async function port(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const value=server.address().port;await new Promise(resolve=>server.close(resolve));return value;}

async function processGone(pid){
  try{process.kill(pid,0);return false;}catch(error){return error.code==='ESRCH';}
}
function paths(target){return {
  app:mac?path.join(target,'Contents/MacOS/Codex Mobile Bridge'):path.join(target,'Codex Mobile Bridge.exe'),
  runtime:mac?path.join(target,'Contents/Resources/gateway/codex-mobile-gateway'):path.join(target,'resources/gateway/codex-mobile-gateway.exe'),
  asar:mac?path.join(target,'Contents/Resources/app.asar'):path.join(target,'resources/app.asar'),
  resources:mac?path.join(target,'Contents/Resources'):path.join(target,'resources'),
  plist:mac?path.join(target,'Contents/Info.plist'):null};}
function run(command,args,options={}){return new Promise((resolve,reject)=>{
  const child=spawn(command,args,{stdio:['ignore','pipe','pipe'],...options});let stdout='',stderr='';
  child.stdout.on('data',chunk=>stdout+=chunk);child.stderr.on('data',chunk=>stderr+=chunk);
  child.once('error',reject);child.once('exit',(code,signal)=>code===0?resolve({stdout,stderr}):reject(Error(`${command} exited ${signal||code}: ${stderr||stdout}`)));
});}
async function copyTree(source,target){await fs.cp(source,target,{recursive:true,verbatimSymlinks:true});}
async function rewriteAsar(asarPath,mutate){
  const source=await fs.mkdtemp(path.join(root,'.tmp/cmb-asar-'));
  try{asar.extractAll(asarPath,source);await mutate(source);await fs.rm(asarPath);await asar.createPackage(source,asarPath);}
  finally{await fs.rm(source,{recursive:true,force:true,maxRetries:20,retryDelay:100});}
}
async function makeStubUpdater(asarPath){
  await rewriteAsar(asarPath,async source=>{
    const file=path.join(source,'desktop/features/updates/updater.cjs');
    // Only release discovery/download is stubbed. installUpdate, helper spawn,
    // ready polling, app.exit, gateway ownership and swap remain production.
    await fs.writeFile(file,`'use strict';
class Updater {
  constructor({current,install}){this.current=current;this.apply=install;this.state={state:'idle',current};}
  status(){return {...this.state};}
  get busy(){return ['downloading','preparing','restarting'].includes(this.state.state);}
  set(state,extra={}){this.state={current:this.current,...extra,state};return this.status();}
  async check(){
    this.candidate={version:${JSON.stringify(nextVersion)},archive:process.env.CMB_TEST_UPDATE_ARCHIVE,
      asset:{name:'update.zip',size:Number(process.env.CMB_TEST_UPDATE_SIZE),sha256:process.env.CMB_TEST_UPDATE_SHA256}};
    return this.set('available',{version:this.candidate.version});
  }
  async install(){
    if(this.busy)return this.status();
    const candidate=this.candidate;
    this.set('downloading',{version:candidate.version,received:candidate.asset.size,total:candidate.asset.size});
    try{this.set('preparing',{version:candidate.version});await this.apply(candidate);return this.set('restarting',{version:candidate.version});}
    catch(error){return this.set('error',{message:error.message,version:candidate.version});}
  }
}
module.exports={Updater,allowedMirrorUrl:value=>value};
`);
  });
}
async function bumpCandidate(appRoot,asarPath,resources,version){
  await rewriteAsar(asarPath,async source=>{
    const file=path.join(source,'package.json'),value=JSON.parse(await fs.readFile(file,'utf8'));
    value.version=version;await fs.writeFile(file,JSON.stringify(value,null,2)+'\n');
  });
  await fs.writeFile(path.join(resources,'update-version.json'),JSON.stringify({version,platform:process.platform,arch:process.arch})+'\n');
  if(mac){
    await run('/usr/libexec/PlistBuddy',['-c','Set :CFBundleShortVersionString '+version,'-c','Set :CFBundleVersion '+version,paths(appRoot).plist]);
    await run('/usr/bin/codesign',['--force','--deep','--sign','-',appRoot],{cwd:root});
  }
}
function makeZip(source,destination){
  const script=`
import os,stat,sys,zipfile
source,destination=sys.argv[1:]
with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
    for root,dirs,files in os.walk(source):
        for name in dirs+files:
            full=os.path.join(root,name)
            arc=os.path.relpath(full,source).replace(os.sep,'/')
            if os.path.islink(full):
                info=zipfile.ZipInfo(arc)
                info.create_system=3
                info.external_attr=(stat.S_IFLNK|0o777)<<16
                archive.writestr(info,os.readlink(full))
            else:
                archive.write(full,arc)
`;
  return run(python,['-c',script,source,destination]);
}
function worker(executable,data,action,payload){return new Promise((resolve,reject)=>{
  const child=spawn(executable,[action,'--data-dir',data],{env:{...process.env,PYINSTALLER_RESET_ENVIRONMENT:'1'},windowsHide:true});
  let output='',errors='';const timer=setTimeout(()=>child.kill(),120000);
  child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>errors+=chunk);
  child.once('error',error=>{clearTimeout(timer);reject(error);});
  child.once('close',()=>{clearTimeout(timer);try{const value=JSON.parse(output);if(!value.ok)throw Error(value.error);resolve(value.result);}
    catch(error){reject(Error(`${action}: ${error.message} ${errors}`));}});
  child.stdin.once('error',()=>{});child.stdin.end(payload?JSON.stringify(payload):'');
});}
async function windowBridgeSnapshot(cdp){
  const value=await cdp.call('Runtime.evaluate',{awaitPromise:true,returnByValue:true,expression:'window.bridgeDesktop.snapshot()'});
  return value.result.value;
}
async function rendererManagementState(cdp){
  const value=await cdp.call('Runtime.evaluate',{returnByValue:true,expression:`(()=>{
    if(typeof desktopConnections==='undefined'||!desktopConnections)return null;
    return {scanning:desktopConnections.scanning??null,busy:!!desktopConnections.busy,
      clientsLoading:!!desktopConnections.clientsLoading,choosingClient:desktopConnections.choosingClient??null,
      statusLoaded:!!desktopConnections.connectionValue,scanError:!!desktopConnections.scanError,
      scanMessage:desktopConnections.scanMessage??'',statusError:desktopConnections.statusError??''};
  })()`});
  return value.result.value;
}

class CDP{
  constructor(socket){this.socket=socket;this.next=1;this.pending=new Map();
    this.socket.addEventListener('message',event=>{const value=JSON.parse(event.data);const resolve=this.pending.get(value.id);if(resolve){this.pending.delete(value.id);value.error?resolve.reject(Error(value.error.message)):resolve.resolve(value.result);}});}
  static async connect(debugPort){
    const page=await until(async()=>{const rows=await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(value=>value.json());return rows.find(row=>row.type==='page'&&row.url.endsWith('/desktop/index.html'));},'renderer page');
    const socket=new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    return new CDP(socket);
  }
  call(method,params={}){return new Promise((resolve,reject)=>{const id=this.next++;this.pending.set(id,{resolve,reject});
    this.socket.send(JSON.stringify({id,method,params}));setTimeout(()=>{if(this.pending.has(id)){this.pending.delete(id);reject(Error(`CDP ${method} timeout`));}},10000);});}
  close(){this.socket.close();}
}
async function runScenario(gatewayRunning){
  assert.ok(['darwin','win32'].includes(process.platform),'real controller update test supports macOS and Windows');
  const build=path.resolve(process.env.CMB_TEST_BUILD_DIR||path.join(root,'dist/desktop'));
  const source=mac?path.join(build,process.arch==='x64'?'mac':'mac-'+process.arch,'Codex Mobile Bridge.app'):path.join(build,'win-unpacked');
  assert.ok(sync.existsSync(source),'packaged app is required; run desktop build first');
  const work=await fs.mkdtemp(path.join(root,'.tmp/update-controller-smoke-'));
  const targetParent=path.join(work,'current'),candidateParent=path.join(work,'candidate'),data=path.join(work,'data');
  const target=mac?path.join(targetParent,'Codex Mobile Bridge.app'):path.join(targetParent,'app');
  const candidate=mac?path.join(candidateParent,'Codex Mobile Bridge.app'):path.join(candidateParent,'app');
  const archive=path.join(work,`Codex-Mobile-Bridge-${nextVersion}-${process.platform}.zip`);
  let transaction=null;
  await fs.mkdir(targetParent);await fs.mkdir(candidateParent);await fs.mkdir(path.join(data,'codex-home'),{recursive:true});
  let appChild,launchedPid,oldControllerExited=false,cdp=null;
  try{
    await copyTree(source,target);await copyTree(source,candidate);
    const targetPaths=paths(target),candidatePaths=paths(candidate);
    await bumpCandidate(candidate,candidatePaths.asar,candidatePaths.resources,nextVersion);
    await makeZip(mac?candidateParent:candidate,archive);
    const {createHash}=require('node:crypto'),hasher=createHash('sha256');
    for await(const chunk of sync.createReadStream(archive))hasher.update(chunk);
    const digest=hasher.digest('hex'),archiveSize=(await fs.stat(archive)).size;
    await makeStubUpdater(targetPaths.asar);
    if(mac)await run('/usr/bin/codesign',['--force','--deep','--sign','-',target],{cwd:root});
    const settings=await worker(targetPaths.runtime,data,'snapshot'),gatewayPort=await port();
    Object.assign(settings.preferences,{port:gatewayPort,lan:false,autoStart:false,
      connections:gatewayRunning&&quickTunnel?[{id:'controller-smoke-quick',name:'real Quick Tunnel',enabled:true,accessMode:'quick'}]:[],codexHome:path.join(data,'codex-home')});
    await worker(targetPaths.runtime,data,'save',settings);
    let beforeGateway=null;
    if(gatewayRunning){
      beforeGateway=await worker(targetPaths.runtime,data,'start');
      await until(async()=>{const response=await fetch(`http://127.0.0.1:${gatewayPort}/api/health`);return response.ok;},'original gateway health');
      if(quickTunnel)await until(async()=>{const value=await worker(targetPaths.runtime,data,'snapshot');return value.quickTunnel?.state==='ready'&&value.urls.some(url=>url.startsWith('https://')&&url.includes('.trycloudflare.com'));},'original Quick Tunnel ready',180000);
    }else{
      await worker(targetPaths.runtime,data,'stop').catch(()=>{});
    }
    const debugPort=await port(),env={...process.env,CMB_DATA_DIR:data,CMB_TEST_UPDATE_ARCHIVE:archive,
      CMB_TEST_UPDATE_SHA256:digest,CMB_TEST_UPDATE_SIZE:String(archiveSize)};
    for(const key of ['ELECTRON_RUN_AS_NODE','CMB_UPDATE_DATA_DIR','CMB_UPDATE_TRANSACTION','CMB_UPDATE_TOKEN'])delete env[key];
    appChild=spawn(targetPaths.app,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debugPort],{cwd:targetParent,env,stdio:['ignore','inherit','inherit'],windowsHide:true});
    const oldExit=new Promise(resolve=>appChild.once('exit',(code,signal)=>resolve({code,signal})));
    cdp=await CDP.connect(debugPort);
    await until(async()=>{const value=await cdp.call('Runtime.evaluate',{returnByValue:true,expression:'Boolean(window.bridgeDesktop)'});return value.result.value===true;},'preload bridge');
    await until(async()=>{const value=await cdp.call('Runtime.evaluate',{returnByValue:true,expression:"typeof document.getElementById('check-update').onclick==='function'"});return value.result.value===true;},'renderer update handlers');
    await until(async()=>{const value=await windowBridgeSnapshot(cdp);return value.runtime.running===gatewayRunning&&(!gatewayRunning||!quickTunnel||value.quickTunnel?.state==='ready');},'renderer gateway and tunnel state match fixture',180000);
    // The first snapshot renders before automatic client discovery finishes.
    // Wait for the actual scan (undefined is not finished) and its follow-up
    // reads; the production controller must still reject concurrent writes.
    const management=await until(async()=>{
      const value=await rendererManagementState(cdp);
      return value?.scanning===false&&!value.busy&&!value.clientsLoading&&!value.choosingClient&&value.statusLoaded?value:null;
    },'initial client discovery settled',180000);
    console.log('Initial client management',JSON.stringify(management));
    await cdp.call('Runtime.evaluate',{expression:"document.getElementById('check-update').click()"});
    await until(async()=>{const value=await windowBridgeSnapshot(cdp);return value.update?.state==='available';},'stub update check',30000);
    await until(async()=>{
      const value=await cdp.call('Runtime.evaluate',{returnByValue:true,expression:"(()=>{const button=document.getElementById('install-update');return {hidden:button.hidden,disabled:button.disabled,handler:typeof button.onclick==='function'}})()"}).then(value=>value.result.value);
      return value.hidden===false&&value.disabled===false&&value.handler===true?value:null;
    },'available install button',60000);
    await cdp.call('Runtime.evaluate',{expression:"document.getElementById('install-update').click()"});
    // The test never signals this controller. Wait for helper ready, then prove
    // the exact parent PID disappears before the helper can report success.
    transaction=await until(async()=>{
      const rows=(await fs.readdir(targetParent)).filter(name=>name.startsWith('.cmb-update-'));
      for(const name of rows){
        const candidateDir=path.join(targetParent,name);
        if(sync.existsSync(path.join(candidateDir,'ready.json'))&&sync.existsSync(path.join(candidateDir,'plan.json')))return candidateDir;
      }
      return null;
    },'ready update transaction',20000);
    const plan=JSON.parse(await fs.readFile(path.join(transaction,'plan.json'),'utf8'));
    assert.equal(plan.parentPid,appChild.pid,'helper parent must be the real Electron controller');
    await until(async()=>{try{process.kill(plan.parentPid,0);return false;}catch(error){return error.code==='ESRCH';}},'old controller self-exit',45000);
    oldControllerExited=true;
    let oldExitValue=null;
    try{oldExitValue=await Promise.race([oldExit,delay(100).then(()=>null)]);}catch{}
    const result=await until(async()=>JSON.parse(await fs.readFile(path.join(transaction,'result.json'),'utf8')),'update result',90000);
    const launched=JSON.parse(await fs.readFile(path.join(transaction,'launched.json'),'utf8'));
    const ack=JSON.parse(await fs.readFile(path.join(transaction,'ack.json'),'utf8'));
    launchedPid=launched.pid;
    assert.equal(result.state,'updated',result.message||result.state);
    assert.equal(result.version,nextVersion);
    assert.equal(ack.version,nextVersion);
    assert.equal(ack.dataDir,path.resolve(data));
    await until(async()=>{const value=await worker(candidatePaths.runtime,data,'snapshot');return value.runtime.running===gatewayRunning;},'updated gateway state',40000);
    const updatedGateway=await worker(candidatePaths.runtime,data,'snapshot');
    assert.equal(updatedGateway.runtime.running,gatewayRunning);
    if(gatewayRunning){
      assert.notEqual(updatedGateway.runtime.pid,beforeGateway.pid);
      if(quickTunnel)await until(async()=>{const value=await worker(candidatePaths.runtime,data,'snapshot');return value.quickTunnel?.state==='ready'&&value.urls.some(url=>url.startsWith('https://')&&url.includes('.trycloudflare.com'));},'updated Quick Tunnel ready',180000);
      await until(()=>processGone(beforeGateway.pid),'old gateway process exits',15000);
      await until(async()=>{const response=await fetch(`http://127.0.0.1:${gatewayPort}/api/health`);return response.ok;},'updated gateway health');
    }
    if(mac)assert.equal(await run('/usr/libexec/PlistBuddy',['-c','Print :CFBundleShortVersionString',targetPaths.plist]).then(value=>value.stdout.trim()),nextVersion);
    console.log(JSON.stringify({ok:true,scenario:gatewayRunning?(quickTunnel?'gateway-running-real-quick-tunnel':'gateway-running-local'):'gateway-stopped',oldPid:appChild.pid,oldExit:oldExitValue,newPid:launchedPid,result,ack,work},null,2));
    cdp?.close();
    try{
      if(mac)process.kill(-launchedPid,'SIGTERM');else process.kill(launchedPid,'SIGTERM');
    }catch(error){}
    await delay(500);
    if(gatewayRunning)await worker(candidatePaths.runtime,data,'stop').catch(error=>console.error('cleanup gateway stop:',error.message));
  }catch(error){
    console.error(error.stack||error);
    if(cdp){
      try{
        const diagnostic=await cdp.call('Runtime.evaluate',{awaitPromise:true,returnByValue:true,expression:'window.bridgeDesktop.snapshot().then(value=>({update:value.update,updateResult:value.updateResult,updateManaged:value.updateManaged,feedback:typeof lastFeedback==="undefined"?null:lastFeedback}))'});
        console.error('UI diagnostic',JSON.stringify(diagnostic.result.value,null,2));
        console.error('Client management diagnostic',JSON.stringify(await rendererManagementState(cdp),null,2));
      }catch(diagnosticError){console.error('UI diagnostic failed',diagnosticError);}
    }
    console.error(`Preserving failure fixture: ${work}`);
    process.exitCode=1;
  }finally{
    if(appChild&&appChild.exitCode===null&&!oldControllerExited){appChild.kill();await delay(200);}
    if(!process.exitCode){
      let removed=false;
      for(let attempt=0;attempt<20&&!removed;attempt++){
        try{await fs.rm(work,{recursive:true,force:true,maxRetries:20,retryDelay:100});removed=true;}
        catch(error){await delay(200);}
      }
      if(!removed)throw Error(`could not clean success fixture: ${work}`);
    }
  }
}
async function main(){
  for(const gatewayRunning of [false,true]){
    console.log(`\n=== update controller scenario: ${gatewayRunning?'gateway running':'gateway stopped'} ===`);
    await runScenario(gatewayRunning);
  }
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
