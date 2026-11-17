'use strict';
// Real released packages, manual overwrite and rollback. Hosted runners only.
// This does not change or test stable-channel prerelease selection.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
const {spawn,execFileSync}=require('node:child_process'),net=require('node:net');
const {createHash,randomUUID}=require('node:crypto');
const {runWorker}=require('../desktop/shared/controller.cjs');
const oldVersion='2.2.1',candidateVersion='2.2.2-preview.1';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
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
async function inspectDesktop(executable,data,version,work,phase){
  const debug=await freePort(),env={...process.env,CMB_DATA_DIR:data};
  for(const key of ['ELECTRON_RUN_AS_NODE','CMB_PYTHON','PYTHONHOME','PYTHONPATH'])delete env[key];
  const child=spawn(executable,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debug],{env,stdio:['ignore','ignore','pipe']});
  let socket,errors='';child.stderr.on('data',b=>errors+=b);let spawnError;child.on('error',error=>spawnError=error);
  try{
    const page=await until(async()=>{
      if(spawnError)throw spawnError;if(child.exitCode!==null)throw Error('GUI exited: '+errors);
      const response=await fetch(`http://127.0.0.1:${debug}/json/list`,{signal:AbortSignal.timeout(2000)});
      return (await response.json()).find(row=>row.type==='page'&&row.url.endsWith('/desktop/index.html'));
    },'packaged GUI');
    socket=new WebSocket(page.webSocketDebuggerUrl);const pending=new Map();let next=0;
    socket.addEventListener('message',event=>{const value=JSON.parse(event.data);const resolve=pending.get(value.id);if(resolve){pending.delete(value.id);resolve(value);}});
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    async function call(method,params={}){
      const id=++next,result=await Promise.race([new Promise(resolve=>{pending.set(id,resolve);socket.send(JSON.stringify({id,method,params}));}),delay(10000).then(()=>{throw Error('CDP timeout');})]);
      if(result.error||result.result?.exceptionDetails)throw Error(JSON.stringify(result));return result.result;
    }
    const state=await until(async()=>{const result=await call('Runtime.evaluate',{returnByValue:true,expression:'typeof snapshot!=="undefined"&&snapshot?({version:snapshot.update.current,dataDir:snapshot.dataDir}):null'});return result.result.value;},'GUI version');
    assert.equal(state.version,version);assert.equal(path.resolve(state.dataDir),data);
    const screenshot=await call('Page.captureScreenshot',{format:'png'});await fs.writeFile(path.join(work,phase+'-desktop.png'),Buffer.from(screenshot.data,'base64'));
  }finally{
    socket?.close();if(child.exitCode===null){child.kill();await until(()=>child.exitCode!==null,'owned GUI exit',15000);}
  }
}
async function main(){
  assert.equal(process.env.GITHUB_ACTIONS,'true','Only run against an ephemeral GitHub-hosted runner');
  assert.equal(process.env.RUNNER_ENVIRONMENT,'github-hosted','Self-hosted/daily machines are excluded');
  assert.ok(['darwin','win32','linux'].includes(process.platform));
  assert.equal(require('../package.json').version,candidateVersion);
  const {chromium}=require('@playwright/test');
  const work=path.resolve('.tmp/published-upgrade'),data=path.join(work,'data'),profile=path.join(work,'browser');
  await fs.mkdir(data); // Never reuse an earlier fixture's bindings or drafts.
  const target=process.platform==='linux'?'/opt/Codex Mobile Bridge':path.join(work,process.platform==='darwin'?'Codex Mobile Bridge.app':'installed');
  let files=paths(target);const port=await freePort();let phone,scope,originals,worker;
  const preserved=['config.json','desktop.json','notifications.json','notification-watches.json'];
  const draftText='Synthetic preserved draft — 未发送';
  const results=[];
  try{
    for(const [phase,version,folder] of [['old',oldVersion,'old'],['candidate',candidateVersion,'candidate'],['rollback',oldVersion,'old']]){
      const archive=path.join(work,folder,packageName(version));await fs.access(archive);
      const sha256=createHash('sha256').update(await fs.readFile(archive)).digest('hex');
      await install(archive,target,work,phase);
      if(process.platform==='linux')files=paths(path.dirname(await fs.realpath('/usr/bin/codex-mobile-bridge')));
      worker=(action,payload)=>runWorker({executable:files.runtime,dataDir:data},action,payload);
      const metadata=JSON.parse(await fs.readFile(files.metadata,'utf8'));
      assert.equal(metadata.version,version);assert.equal(metadata.arch,process.arch);assert.equal(metadata.platform,process.platform);
      if(phase==='old'){
        await fs.mkdir(path.join(data,'synthetic-codex'));
        const settings=await worker('snapshot');Object.assign(settings.preferences,{port,lan:true,localAccess:true,autoStart:false,tunnel:false,connections:[],codexHome:path.join(data,'synthetic-codex'),ipcPath:path.join(data,'fixture.sock')});
        Object.assign(settings.auth,{username:'admin',password:randomUUID(),sessionHours:0});
        await worker('save',settings);await fs.writeFile(path.join(data,'notification-watches.json'),'[]\n');
      }
      await inspectDesktop(files.app,data,version,work,phase);
      await worker('start');await until(async()=>{const response=await fetch(`http://127.0.0.1:${port}/api/auth`,{signal:AbortSignal.timeout(2000)});return response.ok;},'gateway');
      if(phase==='old'){
        const snapshot=await worker('snapshot');const entry=snapshot.urls.find(value=>{const u=new URL(value);return u.protocol==='http:'&&!['127.0.0.1','localhost','::1'].includes(u.hostname);});assert.ok(entry,'LAN pairing entry');
        const grant=await worker('pairing',{action:'create',url:entry});
        const response=await fetch(new URL('/api/pair',entry),{method:'POST',headers:{Origin:new URL(entry).origin,'Content-Type':'application/json','User-Agent':'BridgeMobile/0.1-Android'},body:JSON.stringify({token:new URL(grant.url).hash.slice('#pair='.length)})});
        assert.equal(response.status,200);await response.json();
        const cookie=response.headers.get('set-cookie').split(';')[0];assert.match(cookie,/^codex_mobile_session(?:_[a-f0-9]{24})?=/);
        phone={entry,cookie};originals=await Promise.all(preserved.map(name=>fs.readFile(path.join(data,name))));
      }
      const authResponse=await fetch(new URL('/api/auth',phone.entry),{headers:{Cookie:phone.cookie,'User-Agent':'BridgeMobile/0.1-Android'}});
      assert.equal(authResponse.status,200);const auth=await authResponse.json();assert.equal(auth.authenticated,true);assert.equal(auth.trustedDevice,true);
      if(!scope)scope={gateway:auth.draftIdentity,provider:'codex',account:'synthetic-account',host:'local',session:'synthetic-unsent-chat'};
      assert.equal(auth.draftIdentity,scope.gateway,'same verified draft gateway');
      const browser=await chromium.launchPersistentContext(profile,{headless:true,userAgent:'BridgeMobile/0.1-Android'});
      try{
        if(phase==='old'){
          const split=phone.cookie.indexOf('=');await browser.addCookies([{name:phone.cookie.slice(0,split),value:phone.cookie.slice(split+1),url:phone.entry,expires:Math.floor(Date.now()/1000)+86400,httpOnly:true,sameSite:'Lax'}]);
        }
        const page=await browser.newPage();await page.goto(phone.entry,{waitUntil:'networkidle'});
        const browserAuth=await page.evaluate(async()=>await(await fetch('/api/auth')).json());assert.equal(browserAuth.trustedDevice,true,'persisted browser cookie');
        // Await the product bootstrap rather than inject a second global class.
        await page.waitForFunction(()=>typeof ComposerDrafts!=='undefined');
        const value=await page.evaluate(({scope,text,seed})=>{const drafts=new ComposerDrafts();if(seed)drafts.save(scope,{text,selectionStart:2,selectionEnd:2});return drafts.load(scope);},{scope,text:draftText,seed:phase==='old'});
        assert.equal(value.text,draftText);assert.equal(value.selectionStart,2);
        await page.screenshot({path:path.join(work,phase+'-browser.png'),fullPage:true});
      }finally{await browser.close();}
      for(let i=0;i<preserved.length;i++)assert.deepEqual(await fs.readFile(path.join(data,preserved[i])),originals[i],phase+': '+preserved[i]);
      await worker('stop');assert.equal((await worker('snapshot')).runtime.running,false);
      results.push({phase,version,sha256,checks:['real packaged GUI version','settings bytes','original paired trust','persistent browser cookie','ComposerDrafts unsent text and selection']});
      console.log('PASS '+phase+' '+version);
    }
    await fs.writeFile(path.join(work,'result.json'),JSON.stringify({ok:true,mode:'manual-install',automaticPrereleaseUpdate:false,results},null,2)+'\n');
  }catch(error){await fs.writeFile(path.join(work,'result.json'),JSON.stringify({ok:false,results,error:error.message},null,2)+'\n');throw error;}
  finally{if(worker)await worker('stop').catch(error=>console.error('Owned gateway cleanup: '+error.message));}
}
module.exports={packageName,paths};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
