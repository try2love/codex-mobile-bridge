// Launch the actual signed bundle with isolated data; do not start a gateway.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),net=require('node:net');
const {execFileSync,spawn}=require('node:child_process');
const root=path.resolve(__dirname,'..'),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let requestId=0;
function evaluate(socket,expression,{timeout=15000,awaitPromise=true}={}){
  return new Promise((resolve,reject)=>{
    const id=++requestId;
    const finish=(error,value)=>{
      clearTimeout(timer);socket.removeEventListener('message',message);socket.removeEventListener('close',closed);
      if(error)reject(error);else resolve(value);
    };
    const closed=()=>finish(Error('Packaged renderer disconnected'));
    const message=event=>{
      const value=JSON.parse(event.data);if(value.id!==id)return;
      if(value.error)finish(Object.assign(Error(value.error.message),{code:value.error.code}));
      else if(value.result.exceptionDetails)finish(Error(JSON.stringify(value.result.exceptionDetails)));
      else finish(null,value.result.result.value);
    };
    const timer=setTimeout(()=>finish(Error('Packaged UI snapshot timed out')),timeout);
    socket.addEventListener('message',message);socket.addEventListener('close',closed,{once:true});
    try{socket.send(JSON.stringify({id,method:'Runtime.evaluate',params:{awaitPromise,returnByValue:true,expression}}));}
    catch(error){finish(error);}
  });
}
async function waitForRenderer(socket,url,{timeout=15000,interval=100}={}){
  const deadline=Date.now()+timeout;
  // URL discovery can precede the first document's execution-context replacement.
  // Poll only reads; the subsequent device-policy write is never replayed.
  while(Date.now()<deadline){
    try{
      if(await evaluate(socket,`location.href===${JSON.stringify(url)}&&document.readyState==='complete'&&typeof snapshot!=='undefined'&&!!snapshot`,
        {timeout:Math.max(1,deadline-Date.now()),awaitPromise:false}))return;
    }catch(error){
      if(error.code!==-32000||!['Execution context was destroyed.','Cannot find default execution context'].includes(error.message))throw error;
    }
    await delay(Math.min(interval,Math.max(0,deadline-Date.now())));
  }
  throw Error('Packaged renderer document and snapshot did not become ready');
}
async function main(){
  assert.equal(process.platform,'darwin');
  assert.ok(process.argv[2],'Pass the packaged .app path');
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const data=await fs.mkdtemp(path.join(root,'.tmp','macos-launch-'));
  const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const port=server.address().port;await new Promise(resolve=>server.close(resolve));
  const app=path.resolve(process.argv[2]);
  const executableName=execFileSync('/usr/libexec/PlistBuddy',['-c','Print :CFBundleExecutable',path.join(app,'Contents/Info.plist')],{encoding:'utf8'}).trim();
  const executable=path.join(app,'Contents/MacOS',executableName);
  const env={...process.env,CMB_DATA_DIR:data};
  for(const key of ['CMB_PYTHON','PYTHONPATH','PYTHONHOME','ELECTRON_RUN_AS_NODE'])delete env[key];
  const child=spawn(executable,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+port],{env,stdio:['ignore','ignore','pipe']});
  let errors='',launchError,socket;
  child.stderr.on('data',chunk=>errors+=chunk);child.on('error',error=>launchError=error);
  const exited=new Promise(resolve=>child.once('exit',resolve));
  const deadline=Date.now()+30000;
  try{
    let target;
    while(Date.now()<deadline&&!target){
      if(launchError)throw launchError;
      assert.equal(child.exitCode,null,'Packaged app exited: '+errors);
      try{
        const response=await fetch('http://127.0.0.1:'+port+'/json/list',{signal:AbortSignal.timeout(1000)});
        target=(await response.json()).find(item=>item.type==='page'&&item.url.endsWith('/desktop/index.html'));
      }catch{}
      if(!target)await delay(200);
    }
    assert.ok(target,'Packaged renderer failed to load: '+errors);
    socket=new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    await waitForRenderer(socket,target.url);
    const state=await evaluate(socket,`(async()=>{
        const deadline=Date.now()+10000;
        while((typeof snapshot==='undefined'||!snapshot)&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
        if(typeof snapshot==='undefined'||!snapshot)throw Error('Bundled worker did not return a snapshot');
        tab('devices');const deviceDeadline=Date.now()+5000;
        while(devicesBusy&&Date.now()<deviceDeadline)await new Promise(resolve=>setTimeout(resolve,50));
        if(devicesBusy||!devicesState)throw Error('Device management did not load');
        document.getElementById('ip-allowlist').value='192.0.2.7';document.getElementById('allowlist-enabled').checked=true;
        await changeDevices({action:'save',policy:devicePolicy()});
        return {accounts:typeof AccountsPanel==='function'&&typeof window.bridgeDesktop.accounts==='function',deviceOverflow:document.documentElement.scrollWidth>innerWidth,directory:snapshot.dataDir,running:snapshot.runtime.running,notifications:snapshot.notifications.enabled,
          sessionHours:document.getElementById('session-hours').value,devices:await window.bridgeDesktop.devices({action:'list'}),
          barkEnabled:snapshot.notifications.barkEnabled,barkServer:document.getElementById('bark-server').value,
          barkKeyType:document.getElementById('bark-key').type,barkTest:!!document.getElementById('test-bark'),
          pushplusEnabled:snapshot.notifications.pushplusEnabled,pushplusToken:snapshot.notifications.pushplusToken,
          pushplusTokenType:document.getElementById('pushplus-token').type,pushplusTest:!!document.getElementById('test-pushplus'),node:typeof require};
      })()`);
    assert.equal(state.directory,data);assert.equal(state.running,false);assert.equal(state.notifications,false);assert.equal(state.node,'undefined');assert.equal(state.accounts,true);
    assert.equal(state.sessionHours,'12');assert.equal(state.devices.sessions.length,0);assert.equal(state.devices.policy.allowlistEnabled,true);assert.deepEqual(state.devices.policy.allowlist,['192.0.2.7']);assert.equal(state.deviceOverflow,false);
    assert.equal(state.barkEnabled,false);assert.equal(state.barkServer,'https://api.day.app');
    assert.equal(state.barkKeyType,'password');assert.equal(state.barkTest,true);
    assert.equal(state.pushplusEnabled,false);assert.equal(state.pushplusToken,'');
    assert.equal(state.pushplusTokenType,'password');assert.equal(state.pushplusTest,true);
    console.log('PASS: signed packaged App opens, renderer and bundled worker load, isolated data, gateway remains stopped.');
  }finally{
    socket?.close();child.kill();
    await Promise.race([exited,delay(3000)]);
    if(child.exitCode===null&&!launchError)child.kill('SIGKILL');
  }
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={evaluate,waitForRenderer};
