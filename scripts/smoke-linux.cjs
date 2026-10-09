'use strict';
// Run as a desktop user (or under dbus-run-session + xvfb-run), never as root.
// All gateway data, ports and credentials are synthetic and isolated.
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {runWorker}=require('../desktop/shared/controller.cjs');
const root=path.resolve(__dirname,'..'),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function freePort(){
  const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port;
}
async function until(fn,label){
  const deadline=Date.now()+30000;let last;
  while(Date.now()<deadline){try{const value=await fn();if(value)return value;}catch(error){last=error;}await delay(200);}
  throw Error(label+' timed out: '+(last?.message||''));
}
async function connect(url){
  const socket=new WebSocket(url),pending=new Map();let next=0;
  socket.addEventListener('message',event=>{
    const value=JSON.parse(event.data),request=pending.get(value.id);
    if(!request)return;pending.delete(value.id);clearTimeout(request.timer);
    if(value.error)request.reject(Error(value.error.message));else request.resolve(value.result);
  });
  const cancel=()=>{for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('Debugger closed'));}pending.clear();};
  socket.addEventListener('close',cancel);
  await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{socket.close();reject(Error('Debugger connection timed out'));},10000);
    socket.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
    socket.addEventListener('error',()=>{clearTimeout(timer);reject(Error('Debugger connection failed'));},{once:true});
  });
  const call=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' timed out'));},20000);
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
  });
  return {call,close:()=>{cancel();socket.close();},async evaluate(expression){
    const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||'Renderer evaluation failed');
    return result.result.value;
  }};
}
async function checkElf(file){
  const handle=await fs.open(file,'r');
  try{
    const header=Buffer.alloc(20);await handle.read(header,0,20,0);
    assert.equal(header.subarray(0,4).toString(),'\x7fELF',file);
    assert.equal(header[4],2,'Expected ELF64');assert.equal(header[5],1,'Expected little endian');
    assert.equal(header.readUInt16LE(18),{x64:62,arm64:183}[process.arch],'Wrong binary architecture: '+file);
  }finally{await handle.close();}
}
async function cleanupGateway(worker){
  if((await worker('snapshot')).runtime.running)await worker('stop');
}
async function main(){
  assert.equal(process.platform,'linux');assert.ok(['x64','arm64'].includes(process.arch));
  assert.notEqual(process.getuid(),0,'Run the app as a regular desktop user');
  assert.ok(process.argv[2],'Pass the installed or extracted codex-mobile-bridge executable');
  const executable=await fs.realpath(process.argv[2]),resources=path.join(path.dirname(executable),'resources');
  const runtime=path.join(resources,'gateway/codex-mobile-gateway');
  await checkElf(executable);await checkElf(runtime);
  const metadata=JSON.parse(await fs.readFile(path.join(resources,'update-version.json'),'utf8'));
  assert.equal(metadata.platform,'linux');assert.equal(metadata.arch,process.arch);
  assert.equal(metadata.version,require('../package.json').version);
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const data=await fs.mkdtemp(path.join(root,'.tmp','linux-app-'));
  const port=await freePort(),debugPort=await freePort(),env={...process.env,CMB_DATA_DIR:data};
  for(const key of ['CMB_PYTHON','PYTHONPATH','PYTHONHOME','ELECTRON_RUN_AS_NODE'])delete env[key];
  const worker=(action,payload)=>runWorker({executable:runtime,dataDir:data},action,payload);
  const settings=await worker('snapshot');
  Object.assign(settings.preferences,{port,lan:false,tunnel:false,connections:[],autoStart:false,codexHome:data,ipcPath:path.join(data,'missing.sock')});
  settings.auth.password='synthetic-linux-smoke-password';await worker('save',settings);
  let child,exited,client,errors='',launchError;
  async function launch(){
    child=spawn(executable,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debugPort],{env,stdio:['ignore','ignore','pipe']});
    child.stderr.on('data',chunk=>errors+=chunk);child.on('error',error=>launchError=error);
    exited=new Promise(resolve=>child.once('close',resolve));
    const target=await until(async()=>{
      if(launchError)throw launchError;
      assert.equal(child.exitCode,null,'App exited: '+errors);
      const response=await fetch('http://127.0.0.1:'+debugPort+'/json/list',{signal:AbortSignal.timeout(1000)});
      return (await response.json()).find(item=>item.type==='page'&&item.url.endsWith('/desktop/index.html'));
    },'Packaged app');
    client=await connect(target.webSocketDebuggerUrl);
    await until(()=>client.evaluate('Boolean(snapshot)'),'Bundled worker');
  }
  async function stopController(){
    if(client){await client.call('Browser.close').catch(()=>{});client.close();client=null;}
    await Promise.race([exited,delay(5000)]);
    assert.ok(child.exitCode!==null||child.signalCode!==null,'Controller did not close');
  }
  try{
    await launch();
    assert.equal(await client.evaluate('typeof require'),'undefined');
    assert.equal(await client.evaluate('snapshot.dataDir'),data);
    assert.equal(await client.evaluate('snapshot.update.state'),'unsupported');
    assert.equal(await client.evaluate('snapshot.runtime.running'),false);
    await client.evaluate("document.getElementById('language').value='en';document.getElementById('language').dispatchEvent(new Event('change'))");
    await until(()=>client.evaluate("document.documentElement.lang==='en'"),'English UI');
    for(const [width,height] of [[1100,850],[820,640]]){
      await client.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
      assert.equal(await client.evaluate('document.documentElement.scrollWidth>innerWidth'),false);
      const screenshot=await client.call('Page.captureScreenshot');
      await fs.writeFile(path.join(data,`desktop-${width}.png`),Buffer.from(screenshot.data,'base64'));
    }
    await client.evaluate("document.getElementById('start').click()");
    await until(()=>client.evaluate('snapshot.runtime.running'),'Gateway startup');
    const base='http://127.0.0.1:'+port;
    assert.equal((await fetch(base+'/api/sessions',{signal:AbortSignal.timeout(3000)})).status,401);
    for(const route of ['/','/app.js','/i18n.js'])assert.equal((await fetch(base+route,{signal:AbortSignal.timeout(3000)})).status,200,route);
    // GNOME may not expose a tray: closing the controller must not stop the gateway.
    await stopController();assert.equal((await worker('snapshot')).runtime.running,true);
    await launch();assert.equal(await client.evaluate('document.documentElement.lang'),'en');
    assert.equal(await client.evaluate('snapshot.runtime.running'),true);
    await client.evaluate("document.getElementById('stop').click()");
    await until(()=>client.evaluate('!snapshot.runtime.running'),'Gateway shutdown');
    await stopController();
    console.log('PASS: Linux '+process.arch+' packaged GUI, ELF architecture, gateway, authentication, language persistence, close/reopen. Screenshots: '+data);
  }finally{
    client?.close();
    if(child&&child.exitCode===null&&child.signalCode===null){
      child.kill();await Promise.race([exited,delay(3000)]);
      if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await exited;}
    }
    await cleanupGateway(worker);
  }
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={cleanupGateway};
