// Test the real packaged app over its loopback-only Chromium debugging socket.
// Only synthetic gateway data is used; no model requests or real phone pushes.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),net=require('node:net');
const {spawn,execFile}=require('node:child_process');
const {promisify}=require('node:util');
const execute=promisify(execFile),root=path.resolve(__dirname,'..');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function get(url){
  try{return await fetch(url,{headers:{Connection:'close'},signal:AbortSignal.timeout(5000)});}
  catch(error){throw Error('GET '+url+': '+(error.cause?.message||error.message),{cause:error});}
}
async function freePort(){
  const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port;
}
async function until(callback,label,timeout=30000){
  const deadline=Date.now()+timeout;let lastError;
  while(Date.now()<deadline){try{const value=await callback();if(value)return value;}catch(error){lastError=error;}await delay(200);}
  throw Error(label+' timed out'+(lastError?': '+lastError.message:''));
}
async function connect(url){
  const socket=new WebSocket(url),pending=new Map();let next=0;
  socket.addEventListener('message',event=>{
    const value=JSON.parse(event.data),request=pending.get(value.id);
    if(!request)return;pending.delete(value.id);clearTimeout(request.timer);
    if(value.error)request.reject(Error(value.error.message));else request.resolve(value.result);
  });
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  function call(method,params={}){
    return new Promise((resolve,reject)=>{
      const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' timed out'));},20000);
      pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
    });
  }
  return {call,close:()=>socket.close(),async evaluate(expression){
    const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||'Renderer evaluation failed');
    return result.result.value;
  }};
}

async function main(){
  assert.equal(process.platform,'win32','This smoke test requires Windows');
  const executable=path.resolve(process.argv[2]||path.join(root,'dist/desktop/win-unpacked/Codex Mobile Bridge.exe'));
  const runtime=path.join(path.dirname(executable),'resources/gateway/codex-mobile-gateway.exe');
  await fs.mkdir(path.join(root,'.tmp'),{recursive:true});
  const data=await fs.mkdtemp(path.join(root,'.tmp','Windows App \u4e2d\u6587 '));
  const port=await freePort(),debugPort=await freePort();
  const env={...process.env,CMB_DATA_DIR:data,PATH:path.join(process.env.SystemRoot,'System32')};
  for(const key of ['CMB_PYTHON','PYTHONPATH','PYTHONHOME','ELECTRON_RUN_AS_NODE'])delete env[key];
  async function worker(action,payload){
    if(payload===undefined){const {stdout}=await execute(runtime,[action,'--data-dir',data],{env,windowsHide:true,timeout:30000,maxBuffer:4*1024*1024});const value=JSON.parse(stdout);assert.equal(value.ok,true,value.error);return value.result;}
    return new Promise((resolve,reject)=>{
      const child=spawn(runtime,[action,'--data-dir',data],{env,windowsHide:true});let output='',errors='';
      const timer=setTimeout(()=>{child.kill();reject(Error('Runtime timed out'));},30000);
      child.stdout.on('data',value=>output+=value);child.stderr.on('data',value=>errors+=value);
      child.on('error',error=>{clearTimeout(timer);reject(error);});
      child.on('exit',()=>{clearTimeout(timer);try{const value=JSON.parse(output);if(!value.ok)throw Error(value.error);resolve(value.result);}catch(error){reject(Error(error.message+' '+errors));}});
      child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(payload));
    });
  }
  const settings=await worker('snapshot');
  Object.assign(settings.preferences,{port,lan:true,tunnel:false,codexHome:data,autoStart:false,connections:[
    {id:'smoke-server',name:'Server test',enabled:true,accessMode:'server',publicUrl:'https://server.example.com',sshTarget:'test-only',sshRemotePort:18787},
    {id:'smoke-nas',name:'NAS test',enabled:true,accessMode:'nas',publicUrl:'https://nas.example.com',proxyUpstream:'http://192.0.2.1:'+port},
    {id:'smoke-quick',name:'Temporary test',enabled:false,accessMode:'quick'}
  ]});
  await worker('save',settings);
  for(const id of ['smoke-server','smoke-nas']){
    const bundle=await worker('deployment',{id});
    assert.ok(bundle.files['compose.yaml']);assert.ok(bundle.files['DEPLOYMENT_EN.md']);
    assert.ok(!JSON.stringify(bundle.files).includes('password_hash'));
    const exported=await worker('export-deployment',{id});
    assert.equal((await fs.readFile(exported.path)).subarray(0,2).toString(),'PK');
  }
  let stderr='',client,started=false,child,exited;
  function launch(){
    child=spawn(executable,['--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debugPort],{env,stdio:['ignore','ignore','pipe']});
    child.stderr.on('data',value=>stderr+=value);
    exited=new Promise(resolve=>{child.on('exit',resolve);child.on('error',resolve);});
  }
  async function connectApp(){
    const target=await until(async()=>{
      if(child.exitCode!==null)throw Error('App exited: '+stderr);
      const response=await get('http://127.0.0.1:'+debugPort+'/json/list');
      return (await response.json()).find(item=>item.type==='page'&&item.url.endsWith('/desktop/index.html'));
    },'Packaged app');
    client=await connect(target.webSocketDebuggerUrl);
    await until(()=>client.evaluate('Boolean(snapshot)'), 'Initial settings');
  }
  launch();
  try{
    await connectApp();
    assert.equal(await client.evaluate('typeof require'),'undefined');
    assert.equal(await client.evaluate('snapshot.preferences.port'),port);
    assert.equal(await client.evaluate('snapshot.notifications.enabled'),false);
    assert.equal(await client.evaluate('snapshot.notifications.barkEnabled'),false);
    assert.equal(await client.evaluate("document.getElementById('bark-key').type"),'password');
    assert.equal(await client.evaluate("document.getElementById('bark-server').value"),'https://api.day.app');
    assert.equal(await client.evaluate("Boolean(document.getElementById('test-bark'))"),true);
    assert.equal(await client.evaluate("document.querySelectorAll('.connection-card').length"),3);
    // Only local access is started. The synthetic SSH/NAS entries never connect.
    await client.evaluate("document.querySelector('[data-tab=network]').click();for(const node of document.querySelectorAll('[data-connection-field=enabled]')){node.checked=false;node.dispatchEvent(new Event('input',{bubbles:true}));}document.getElementById('lan').checked=false;document.getElementById('lan').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('settings').requestSubmit()");
    await until(()=>client.evaluate('!dirty&&snapshot.preferences.connections.every(row=>!row.enabled)'),'Parallel connection settings');
    assert.equal(await client.evaluate('snapshot.preferences.lan'),false);
    await client.evaluate("tab('overview')");
    await client.evaluate("document.getElementById('start').click()");started=true;
    await until(()=>client.evaluate('snapshot.runtime.running'),'Bundled gateway startup');
    assert.equal(await client.evaluate("document.getElementById('port').disabled"),true);
    const base='http://127.0.0.1:'+port;
    assert.equal((await get(base+'/api/sessions')).status,401);
    for(const route of ['/','/app.js','/i18n.js','/markdown.js','/timeline.js','/vendor/katex/katex.min.js'])assert.equal((await get(base+route)).status,200,route);
    assert.ok((await (await get(base+'/')).text()).includes('id="notify-completion"'));
    assert.ok((await (await get(base+'/app.js')).text()).includes('notifyOnCompletion'));
    const phone=await execute(require('electron'),[path.join(root,'scripts/smoke-mobile.cjs'),base+'/'],{env,windowsHide:true,timeout:30000});
    assert.ok(phone.stdout.includes('"passed":11'),'Mobile browser checks must run');
    async function screenshot(name){
      const image=await client.call('Page.captureScreenshot',{format:'png'});
      const filename=path.join(data,name+'.png');await fs.writeFile(filename,Buffer.from(image.data,'base64'));return filename;
    }
    const screenshots=[await screenshot('overview')];
    await client.evaluate("document.querySelector('[data-tab=notifications]').click();document.getElementById('ntfy-topic').value='unsaved-topic';document.getElementById('ntfy-topic').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('language').value='en';document.getElementById('language').dispatchEvent(new Event('change'))");
    await until(()=>client.evaluate("document.documentElement.lang==='en'&&!document.getElementById('language').disabled"),'English switch');
    assert.equal(await client.evaluate("document.getElementById('page-title').textContent"),require('../web/shared/i18n.js').dictionary['手机通知']);
    assert.equal(await client.evaluate("document.getElementById('status').textContent"),'Running');
    assert.equal(await client.evaluate("document.getElementById('ntfy-topic').value"),'unsaved-topic');
    assert.equal(await client.evaluate('dirty'),true);
    assert.equal(await client.evaluate("document.getElementById('save').textContent"),'Save settings');
    const savedLanguage=JSON.parse(await fs.readFile(path.join(data,'desktop-runtime/language.json'),'utf8'));
    assert.equal(savedLanguage.language,'en');
    await client.call('Emulation.setDeviceMetricsOverride',{width:820,height:640,deviceScaleFactor:1,mobile:false});
    for(const panel of ['overview','network','devices','notifications','advanced','logs']){
      await client.evaluate(`tab('${panel}')`);
      assert.equal(await client.evaluate('document.documentElement.scrollWidth>innerWidth'),false,panel);
      screenshots.push(await screenshot('english-'+panel));
    }
    await client.evaluate("document.getElementById('language').value='zh';document.getElementById('language').dispatchEvent(new Event('change'))");
    await until(()=>client.evaluate("document.documentElement.lang==='zh-CN'&&!document.getElementById('language').disabled"),'Chinese switch');
    assert.equal(await client.evaluate("document.getElementById('save').textContent"),'保存配置');
    assert.equal(await client.evaluate("document.getElementById('ntfy-topic').value"),'unsaved-topic');
    await client.call('Emulation.clearDeviceMetricsOverride');
    await client.evaluate("document.querySelector('[data-tab=notifications]').click();document.getElementById('ntfy-topic').value='windows-smoke';document.getElementById('ntfy-topic').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('session-hours').value='0';document.getElementById('session-hours').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('bark-key').value='synthetic-bark-key';document.getElementById('bark-key').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('settings').requestSubmit()");
    await until(()=>client.evaluate("!dirty&&snapshot.notifications.topic==='windows-smoke'"),'Settings save');
    assert.equal(await client.evaluate('snapshot.notifications.enabled'),false);
    assert.equal(await client.evaluate('snapshot.notifications.barkEnabled'),false);
    assert.equal(await client.evaluate('snapshot.notifications.hasBarkKey'),true);
    assert.equal(await client.evaluate('snapshot.notifications.barkKey'),'');
    assert.equal(await client.evaluate('snapshot.auth.sessionHours'),0);
    assert.equal(await client.evaluate("document.getElementById('bark-key').value"),'synthetic-bark-key');
    assert.equal(await client.evaluate("document.getElementById('bark-key').type"),'password');
    screenshots.push(await screenshot('notifications'));
    await client.evaluate("tab('devices')");
    await until(()=>client.evaluate('!devicesBusy&&!!devicesState'),'Device management');
    await client.evaluate("document.getElementById('ip-allowlist').value='127.0.0.1';document.getElementById('allowlist-enabled').checked=true;changeDevices({action:'save',policy:devicePolicy()})");
    await until(()=>client.evaluate('!devicesBusy&&devicesState.policy.allowlistEnabled'),'IP rule save');
    assert.deepEqual(await client.evaluate('devicesState.policy.allowlist'),['127.0.0.1']);
    assert.equal(await client.evaluate('document.documentElement.scrollWidth>innerWidth'),false);
    screenshots.push(await screenshot('devices'));

    // Closing is a hide-to-tray operation. A second launch must restore the same app.
    const closed=await execute(path.join(process.env.SystemRoot,'System32/WindowsPowerShell/v1.0/powershell.exe'),['-NoProfile','-NonInteractive','-Command',`(Get-Process -Id ${child.pid}).CloseMainWindow()`],{env,windowsHide:true,timeout:15000});
    assert.equal(closed.stdout.trim(),'True','Native window close must be delivered');
    // Hidden Chromium windows can suspend both evaluation and discovery.
    // Check the independent gateway, then reuse this CDP session after restore.
    await delay(300);
    assert.equal(child.exitCode,null);
    assert.equal((await get(base+'/api/auth')).status,200);
    await execute(executable,[],{env,timeout:15000});
    await until(()=>client.evaluate("document.visibilityState==='visible'"),'Single-instance restore');
    await client.evaluate("document.querySelector('[data-tab=advanced]').click()");
    assert.equal(await client.evaluate("document.getElementById('data-dir').textContent"),data);
    const overflow=await client.evaluate('document.documentElement.scrollWidth>innerWidth');assert.equal(overflow,false);
    screenshots.push(await screenshot('advanced'));
    await client.evaluate("document.querySelector('[data-tab=overview]').click();document.getElementById('stop').click()");
    await until(()=>client.evaluate('!snapshot.runtime.running'),'Graceful stop');started=false;
    assert.equal((await worker('snapshot')).runtime.running,false);
    await client.evaluate("document.getElementById('language').value='en';document.getElementById('language').dispatchEvent(new Event('change'))");
    await until(()=>client.evaluate("document.documentElement.lang==='en'&&!document.getElementById('language').disabled"),'Persist English');
    client.close();child.kill();await exited;launch();await connectApp();
    assert.equal(await client.evaluate('snapshot.notifications.hasBarkKey'),true);
    assert.equal(await client.evaluate('snapshot.notifications.barkKey'),'');
    assert.equal(await client.evaluate('snapshot.auth.sessionHours'),0);
    assert.equal(await client.evaluate('document.documentElement.lang'),'en');
    assert.equal(await client.evaluate("document.getElementById('language').value"),'en');
    assert.equal(await client.evaluate("document.getElementById('start').textContent"),'Start gateway');
    console.log(JSON.stringify({ok:true,executable,data,screenshots,checks:['bundled runtime without Python or Node on PATH','Unicode data path','renderer isolation','start and stop','private HTTP routes','local assets','parallel connection settings','server and NAS deployment ZIPs','11 mobile bilingual browser checks','notification settings saved without publishing','close to tray keeps gateway online','second launch restores window','bilingual switch preserves drafts','language survives app restart','English layout at 820x640','no horizontal overflow']}));
  }finally{
    if(started){try{await worker('stop');}catch(error){console.error('Test cleanup:',error.message);}}
    client?.close();
    if(child.exitCode===null)child.kill();
    await exited;
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
