'use strict';
// Exercise the shipped runtime, signed ZIP extraction, real Electron restart and
// gateway restoration in an isolated directory. No release or user data is changed.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),sync=require('node:fs');
const path=require('node:path'),net=require('node:net'),{spawn}=require('node:child_process');
const {Readable}=require('node:stream'),{createHash,generateKeyPairSync,sign,randomUUID}=require('node:crypto');
const {Updater,RELEASES,assetName}=require('../desktop/features/updates/updater.cjs');
const root=path.resolve(__dirname,'..'),version=require('../package.json').version,mac=process.platform==='darwin';
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(callback,label,ms=45000){
  const end=Date.now()+ms;let last;
  while(Date.now()<end){try{const result=await callback();if(result)return result;}catch(error){last=error;}await delay(200);}
  throw Error(label+' timed out'+(last?': '+last.message:''));
}
async function port(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const value=server.address().port;await new Promise(resolve=>server.close(resolve));return value;}
function paths(target){return {app:path.join(target,mac?'Contents/MacOS/Codex Mobile Bridge':'Codex Mobile Bridge.exe'),runtime:path.join(target,mac?'Contents/Resources/gateway/codex-mobile-gateway':'resources/gateway/codex-mobile-gateway.exe')};}
function worker(executable,data,action,payload){return new Promise((resolve,reject)=>{
  const child=spawn(executable,[action,'--data-dir',data],{env:{...process.env,PYINSTALLER_RESET_ENVIRONMENT:'1'},windowsHide:true});let output='',errors='';
  const timer=setTimeout(()=>{child.kill();reject(Error(action+' timeout'));},180000);
  child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>errors+=chunk);child.on('error',reject);
  child.on('close',()=>{clearTimeout(timer);try{const result=JSON.parse(output);if(!result.ok)throw Error(result.error);resolve(result.result);}catch(error){reject(Error(action+': '+error.message+' '+errors));}});
  child.stdin.on('error',()=>{});child.stdin.end(payload?JSON.stringify(payload):'');
});}
async function authenticate(entry,cookie){
  const response=await fetch(new URL('/api/auth',entry),{headers:{Cookie:cookie,'User-Agent':'BridgeMobile/0.1-Android'}});
  assert.equal(response.status,200,'bound phone authentication response');
  const value=await response.json();
  assert.equal(value.authenticated,true,'original phone binding remains authenticated');
  assert.equal(value.trustedDevice,true,'original phone binding remains trusted');
  return value;
}
async function pairPhone(runtime,data){
  const snapshot=await worker(runtime,data,'snapshot');
  const entry=snapshot.urls.find(value=>{const url=new URL(value);return url.protocol==='http:'&&!['127.0.0.1','localhost','::1'].includes(url.hostname);});
  assert.ok(entry,'an isolated LAN entry is required for real QR pairing');
  const grant=await worker(runtime,data,'pairing',{action:'create',url:entry});
  const response=await fetch(new URL('/api/pair',entry),{method:'POST',headers:{'Content-Type':'application/json',Origin:new URL(entry).origin,'User-Agent':'BridgeMobile/0.1-Android'},body:JSON.stringify({token:new URL(grant.url).hash.slice('#pair='.length)})});
  assert.equal(response.status,200,'old gateway QR exchange');
  await response.json();
  const cookie=response.headers.get('set-cookie')?.split(';',1)[0];
  assert.match(cookie||'',/^codex_mobile_session(?:_[a-f0-9]{24})?=[A-Za-z0-9_-]{32,128}$/);
  const auth=await authenticate(entry,cookie);
  const key=createHash('sha256').update(cookie.split('=')[1]).digest('hex');
  const state=JSON.parse(await fs.readFile(path.join(data,'auth-sessions.json'),'utf8'));
  assert.equal(state.cookieName,auth.cookieName);
  assert.equal(state.sessions[key]?.trustedDevice,true);
  assert.equal(state.sessions[key]?.expires,0);
  return {entry,cookie,cookieName:auth.cookieName,key};
}
async function verifyPhone(data,phone){
  const auth=await authenticate(phone.entry,phone.cookie);
  assert.equal(auth.cookieName,phone.cookieName,'original cookie namespace');
  const state=JSON.parse(await fs.readFile(path.join(data,'auth-sessions.json'),'utf8'));
  assert.equal(state.cookieName,phone.cookieName,'persisted cookie namespace');
  assert.equal(state.sessions[phone.key]?.trustedDevice,true,'persisted original phone session');
  assert.equal(state.sessions[phone.key]?.expires,0,'original trust does not expire on upgrade');
}
async function inspectUI(debugPort,output){
  const page=await until(async()=>{const response=await fetch(`http://127.0.0.1:${debugPort}/json/list`);return (await response.json()).find(row=>row.type==='page'&&row.url.endsWith('/desktop/index.html'));},'renderer');
  const socket=new WebSocket(page.webSocketDebuggerUrl),pending=new Map();let id=0;
  socket.addEventListener('message',event=>{const value=JSON.parse(event.data);if(pending.has(value.id)){pending.get(value.id)(value);pending.delete(value.id);}});
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  async function call(method,params){const message=await Promise.race([new Promise(resolve=>{const n=++id;pending.set(n,resolve);socket.send(JSON.stringify({id:n,method,params}));}),delay(10000).then(()=>{throw Error('CDP timeout');})]);if(message.error)throw Error(message.error.message);return message.result;}
  try{
    const result=await until(async()=>{const value=await call('Runtime.evaluate',{returnByValue:true,expression:`(()=>{if(typeof snapshot==='undefined'||!snapshot)return null;tab('updates');return {version:snapshot.update.current,button:document.getElementById('check-update').textContent,overflow:document.documentElement.scrollWidth>innerWidth};})()`});return value.result.value?value:null;},'updates renderer');
    assert.equal(result.result.value?.version,version,JSON.stringify(result));assert.equal(result.result.value.overflow,false);
    const screenshot=await call('Page.captureScreenshot',{format:'png'});await fs.writeFile(output,Buffer.from(screenshot.data,'base64'));
  }finally{socket.close();}
}
async function main(){
  assert.ok(['darwin','win32'].includes(process.platform));
  const build=path.join(root,'dist/desktop'),source=path.join(build,mac?`${process.arch==='x64'?'mac':'mac-'+process.arch}/Codex Mobile Bridge.app`:'win-unpacked');
  const name=assetName(version,process.platform,process.arch),archive=path.join(build,name);
  const sha256=createHash('sha256');for await(const chunk of sync.createReadStream(archive))sha256.update(chunk);
  const size=(await fs.stat(archive)).size,digest=sha256.digest('hex');
  const work=await fs.mkdtemp(path.join(root,'.tmp/update-smoke-中文 ')),target=path.join(work,mac?'Codex Mobile Bridge.app':'app'),data=path.join(work,'data');
  await fs.cp(source,target,{recursive:true,verbatimSymlinks:true});await fs.mkdir(data);
  const {app,runtime}=paths(target),settings=await worker(runtime,data,'snapshot'),gatewayPort=await port();
  Object.assign(settings.preferences,{port:gatewayPort,lan:true,autoStart:false,tunnel:false,connections:[],codexHome:data});
  await worker(runtime,data,'save',settings);await fs.writeFile(path.join(data,'notification-watches.json'),'[]\n');
  const preserved=['config.json','desktop.json','notifications.json','notification-watches.json'];
  const env={...process.env,CMB_DATA_DIR:data};for(const key of ['ELECTRON_RUN_AS_NODE','CMB_UPDATE_DATA_DIR','CMB_UPDATE_TRANSACTION','CMB_UPDATE_TOKEN'])delete env[key];
  let appChild,newPid,helper;
  try{
    const before=(await worker(runtime,data,'start')).pid;
    await until(async()=>{const response=await fetch(`http://127.0.0.1:${gatewayPort}/api/auth`);return response.ok;},'original gateway');
    const phone=await pairPhone(runtime,data);
    const originals=await Promise.all(preserved.map(name=>fs.readFile(path.join(data,name))));
    for(const scenario of mac?['success','rollback']:['legacy-cwd','success','rollback']){
      const debugPort=await port();
      appChild=spawn(app,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debugPort],{cwd:target,env,stdio:'ignore'});
      const exit=new Promise(resolve=>appChild.once('exit',resolve));
      await inspectUI(debugPort,path.join(work,scenario+'-updates.png'));
      const {publicKey,privateKey}=generateKeyPairSync('ed25519');
      const payload=Buffer.from(JSON.stringify({schema:1,version,notes:'Isolated signed update smoke test',assets:{[process.platform+'-'+process.arch]:{name,size,sha256:digest}}}));
      const envelope=JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')});
      let planFile;
      const updater=new Updater({current:'0.0.0-beta.0',key:publicKey,directory:path.join(work,'downloads'),
        fetch:async url=>url===RELEASES?new Response(JSON.stringify([{tag_name:'v'+version,prerelease:true,assets:[{name:'bridge-update.json'}]}])):url.endsWith('.json')?new Response(envelope):new Response(Readable.toWeb(sync.createReadStream(archive))),
        install:async candidate=>{
          const token=randomUUID(),prepared=await worker(runtime,data,'update-prepare',{archive:candidate.archive,sha256:digest,version,platform:process.platform,arch:process.arch,target,parentPid:appChild.pid,token});
          planFile=prepared.plan;
          if(scenario==='rollback'){
            // Simulate an unusable installation after validation to exercise real rollback.
            const plan=JSON.parse(await fs.readFile(planFile,'utf8'));
            await fs.rename(paths(plan.staged).app,paths(plan.staged).app+'.broken');
          }
          helper=spawn(prepared.helper,['update-apply','--data-dir',data,'--plan',planFile],{cwd:scenario==='legacy-cwd'?target:path.dirname(target),env:{...env,PYINSTALLER_RESET_ENVIRONMENT:'1'},stdio:'ignore',windowsHide:true});
          await until(async()=>JSON.parse(await fs.readFile(path.join(path.dirname(planFile),'ready.json'),'utf8')).token===token,'update helper');
          appChild.kill();await exit;
        }});
      assert.equal((await updater.check()).state,'available');
      assert.equal((await updater.install()).state,'restarting');
      const result=await until(async()=>JSON.parse(await fs.readFile(path.join(path.dirname(planFile),'result.json'),'utf8')),scenario+' result',90000);
      assert.equal(result.state,scenario==='success'?'updated':'failed',JSON.stringify(result));
      if(scenario!=='success')assert.equal(result.recovered,true,JSON.stringify(result));
      if(scenario==='legacy-cwd')assert.match(result.message,/WinError 32/);
      const state=await worker(runtime,data,'snapshot');assert.equal(state.runtime.running,true);assert.notEqual(state.runtime.pid,before);newPid=state.runtime.pid;
      for(let i=0;i<preserved.length;i++)assert.deepEqual(await fs.readFile(path.join(data,preserved[i])),originals[i],preserved[i]);
      await verifyPhone(data,phone);
      // Stop only the isolated app launched by this helper before the next scenario.
      const launched=JSON.parse(await fs.readFile(path.join(path.dirname(planFile),'launched.json'),'utf8'));
      let guiPid=launched.pid;
      if(mac&&scenario==='success'){
        const receipt=JSON.parse(await fs.readFile(path.join(path.dirname(planFile),'launch-services.json'),'utf8'));
        const plan=JSON.parse(await fs.readFile(planFile,'utf8'));
        assert.equal(receipt.launcherPid,launched.pid);assert.equal(receipt.token,plan.token);
        assert.ok(Number.isSafeInteger(receipt.pid)&&receipt.pid>0);guiPid=receipt.pid;
      }
      try{process.kill(guiPid);}catch(error){if(error.code!=='ESRCH')throw error;}
      await until(()=>{try{process.kill(guiPid,0);return false;}catch(error){if(error.code==='ESRCH')return true;throw error;}},'isolated updated GUI exit');
      console.log('PASS: '+scenario+'; packaged Electron and gateway, exact configuration preservation, original phone binding.');
    }
    console.log(JSON.stringify({ok:true,work,gatewayPid:newPid}));
  }finally{
    if(appChild?.exitCode===null)appChild.kill();
    await worker(runtime,data,'stop').catch(error=>console.error('Cleanup: '+error.message));
    if(helper?.exitCode===null)await until(()=>helper.exitCode!==null,'helper exit',10000);
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
