'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const path=require('node:path');
const {workerFor,runWorker}=require('../desktop/shared/controller.cjs');
const {createTray,primaryUrl}=require('../desktop/shell/tray.cjs');
const i18n=require('../desktop/shared/i18n.js');

test('language normalization and error translation preserve unknown details',()=>{
  assert.equal(i18n.normalize('en-US'),'en');assert.equal(i18n.normalize('zh-TW'),'zh-CN');
  assert.equal(i18n.normalize(null),'zh-CN');
  assert.equal(i18n.translate('运行中','en'),'Running');
  assert.equal(i18n.translate("Error invoking remote method 'bridge:save': Error: 新密码至少需要 12 个字符",'en'),i18n.english['新密码至少需要 12 个字符']);
  assert.equal(i18n.translate('C:\\private\\未知.log','en'),'C:\\private\\未知.log');
});

test('tray prefers public HTTPS and rejects non-web addresses',()=>{
  assert.equal(primaryUrl(['file:///private','http://127.0.0.1:8787','http://192.168.1.2:8787']),'http://192.168.1.2:8787');
  assert.equal(primaryUrl(['http://192.168.1.2:8787','https://example.com']),'https://example.com');
  assert.equal(primaryUrl(['javascript:alert(1)','https://user:pass@example.com']),undefined);
});

async function trayFixture(t){
  let menu,timer,destroyed=false,cleared=false,quit=false,opened=false,copied,error,failStop=false;
  const state={runtime:{running:true},urls:['http://127.0.0.1:8787','https://example.com']};
  const actions=[];
  const tray=createTray({
    Tray:class{on(){}setToolTip(){}setContextMenu(value){menu=value;}destroy(){destroyed=true;}},
    Menu:{buildFromTemplate:value=>value},icon:'icon',show:()=>{opened=true;},t,
    worker:async action=>{if(action==='snapshot')return state;actions.push(action);if(failStop)throw Error('stop failed');state.runtime.running=false;},
    open:async()=>{opened=true;},copy:async value=>{copied=value;},quit:()=>{quit=true;},onError:value=>{error=value;},
    setTimer:callback=>{timer=callback;return 1;},clearTimer:()=>{cleared=true;},
  });
  await new Promise(setImmediate);
  return {tray,state,actions,find:text=>menu.find(item=>item.label?.includes(text)),poll:()=>timer(),
    fail:()=>{failStop=true;},result:()=>({destroyed,cleared,quit,opened,copied,error})};
}

test('tray can stop then quit and dispose without duplicate operations',async()=>{
  const ui=await trayFixture();await ui.find('复制手机').click();assert.equal(ui.result().copied,'https://example.com');
  await ui.find('停止网关').click();assert.deepEqual(ui.actions,['stop']);assert.equal(ui.result().quit,true);
  ui.tray.dispose();assert.equal(ui.result().destroyed,true);assert.equal(ui.result().cleared,true);
});

test('tray can relabel without changing gateway state',async()=>{
  let language='zh-CN';const ui=await trayFixture(text=>i18n.translate(text,language));
  assert.ok(ui.find('打开控制面板'));language='en';ui.tray.relabel();
  assert.ok(ui.find('Open control panel'));assert.ok(ui.find('Stop gateway and quit'));
  assert.deepEqual(ui.actions,[]);ui.tray.dispose();
});

test('tray never quits after a failed stop or stops an unmanaged port',async()=>{
  const ui=await trayFixture();ui.fail();await ui.find('停止网关').click();assert.equal(ui.result().quit,false);assert.match(ui.result().error.message,/stop failed/);
  ui.state.runtime={running:false,portOccupied:true};await ui.poll();assert.equal(ui.find('停止网关').enabled,false);assert.equal(ui.find('打开手机').enabled,false);ui.tray.dispose();
});

test('quitting only the controller leaves the gateway untouched',async()=>{
  const ui=await trayFixture();ui.find('保留网关').click();assert.equal(ui.result().quit,true);assert.deepEqual(ui.actions,[]);ui.tray.dispose();
});
test('packaged launch uses bundled runtime and literal data directory',()=>{
  const result=workerFor({packaged:true,resources:'/app resources',root:'/source',dataDir:'/my data'});
  assert.equal(result.dataDir,'/my data');assert.equal(result.executable,path.join('/app resources','gateway',process.platform==='win32'?'codex-mobile-gateway.exe':'codex-mobile-gateway'));
});
test('file-transfer settings reach the worker through the IPC allowlist',async()=>{
  const script="let value='';process.stdin.on('data',data=>value+=data);process.stdin.on('end',()=>console.log(JSON.stringify({ok:true,result:{action:process.argv[1],payload:JSON.parse(value)}})));";
  const result=await runWorker({executable:process.execPath,prefix:['-e',script],dataDir:'/fixture'},'transfer-settings',{clickDownloadMiB:60});
  assert.deepEqual(result,{action:'transfer-settings',payload:{clickDownloadMiB:60}});
});
test('unknown IPC action cannot become a command',async()=>{await assert.rejects(runWorker({executable:'unused',dataDir:'unused'},'shell'),/未知操作/);});
test('development launch keeps script as its own argument',()=>{
  const result=workerFor({packaged:false,root:'/source path',dataDir:'/data'});assert.deepEqual(result.prefix,['-B',path.join('/source path','desktop.py')]);
});

// Exercise the real renderer's start and polling handlers with a controlled
// backend and clock. DOM layout and input editing are outside these checks.
async function renderer(initialLanguage='zh-CN',{autoStart=false}={}){
  const fs=require('node:fs'),vm=require('node:vm');
  const nodes=new Map();
  function node(){return {parentNode:{insertBefore(){}},getAnimations(){return [];},animate(){},closest(){return null;},value:'',checked:false,hidden:false,textContent:'',dataset:{},
    classList:{toggle(){}},click(){return this.onclick?.();},append(...items){this.children=(this.children||[]).concat(items);},replaceChildren(...items){this.children=items;},setAttribute(){},removeAttribute(){},querySelectorAll(){return [];}};}
  const html=fs.readFileSync(path.join(__dirname,'../desktop/index.html'),'utf8');
  for(const match of html.matchAll(/\bid="([^"]+)"/g))nodes.set(match[1],node());
  for(const id of ['clients-start','clients-stop'])nodes.set(id,node());
  const value={runtime:{running:false,portOccupied:false,supportsNotifications:true},
    preferences:{port:8787,lan:true,tunnel:false,autoStart,connections:[]},
    auth:{mode:'password',username:'admin'},notifications:{enabled:false,server:'https://ntfy.sh',topic:''},
    notificationStatus:{},watches:[],origins:[],urls:[],dataDir:'/test',credentialsAvailable:false};
  let now=1000,poll;
  const calls=[];
  const api={account:async()=>({visible:false}),language:async()=>initialLanguage,setLanguage:async value=>value,snapshot:async()=>{calls.push('snapshot');return structuredClone(value);},start:async()=>{calls.push('start');return {started:true,message:'正在启动网关'};},
    save:async payload=>{value.preferences={...value.preferences,...payload.preferences};return structuredClone(value);},logs:async()=>({text:''})};
  const context=vm.createContext({window:{bridgeDesktop:api},
    localStorage:{getItem(){return null;},setItem(){}},document:{hidden:false,addEventListener(name,fn){this[name]=fn;},documentElement:{},getElementById:id=>nodes.get(id),createElement:node,querySelectorAll:()=>[]},
    URL,Date:class extends Date{static now(){return now;}},setTimeout(){},clearTimeout(){},clearInterval(){},setInterval:(callback,ms)=>{if(callback.name==='refresh')poll=callback;}});
  for(const name of ['web/shared/i18n.js','desktop/shared/secret-fields.js','desktop/features/connections/connections.js','desktop/features/connections/pairing.js','web/features/accounts/account.js','desktop/features/notifications/watches.js','desktop/shell/renderer.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',name),'utf8'),context);
  await new Promise(setImmediate);
  return {nodes,value,context,api,calls,run:code=>vm.runInContext(code,context),poll:()=>poll(),advance:ms=>{now+=ms;},start:()=>nodes.get('start').onclick()};
}

for(const transferFirst of [false,true])test(`independent transfer settings ${transferFirst?'before':'after'} snapshot do not block gateway startup or hide real drafts`,async()=>{
  const ui=await renderer(),fs=require('node:fs'),vm=require('node:vm');
  const create=ui.context.document.createElement;
  ui.context.document.createElement=tag=>{
    const element=create();element.tagName=tag.toUpperCase();element.id='';
    element.append=function(...items){this.children=(this.children||[]).concat(items);for(const child of items)child.parentElement=this;};
    element.closest=function(selector){for(let item=this;item;item=item.parentElement){if(selector==='[data-panel]'&&item.dataset.panel)return item;if(selector==='#'+item.id)return item;}return null;};
    element.validity={valid:true};element.reportValidity=()=>element.validity.valid;
    return element;
  };
  const panel=ui.context.document.createElement('section');panel.dataset.panel='advanced';
  const descendants=element=>[element,...(element.children||[]).flatMap(descendants)];
  const autoStart=ui.nodes.get('auto-start');autoStart.id='auto-start';autoStart.type='checkbox';autoStart.validity={valid:true};autoStart.closest=selector=>selector==='[data-panel]'?panel:null;
  ui.context.document.querySelector=selector=>selector==='[data-panel=advanced]'?panel:null;
  ui.nodes.get('settings').querySelectorAll=()=>[autoStart,...descendants(panel).filter(element=>element.tagName==='INPUT')];
  ui.context.MutationObserver=class {observe(){}};ui.context.window.addEventListener=()=>{};
  let resolveRead;const writes=[];
  ui.api.transferSettings=value=>value?(writes.push(value),Promise.resolve(value)):new Promise(resolve=>{resolveRead=resolve;});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/workspace/file-actions.js'),'utf8'),ui.context);
  ui.context.BridgeFileActions=ui.context.window.BridgeFileActions;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop/features/files/settings.js'),'utf8'),ui.context);
  const input=descendants(panel).find(element=>element.tagName==='INPUT');
  if(transferFirst){resolveRead({clickDownloadMiB:100});await new Promise(setImmediate);}
  ui.run('dirty=false;render(snapshot)');
  assert.equal(ui.run('dirty'),false);
  if(!transferFirst){resolveRead({clickDownloadMiB:100});await new Promise(setImmediate);}
  ui.run("applyLanguage('en')");
  assert.equal(ui.run('dirty'),false,'independently loaded transfer limit is not a gateway draft');
  await ui.start();assert.ok(ui.calls.includes('start'));
  input.value=200;input.oninput({stopPropagation(){}});ui.run('updateDirty()');
  assert.equal(ui.run('dirty'),false,'transfer limit has its own save button');
  await descendants(panel).find(element=>element.tagName==='BUTTON').onclick();
  assert.equal(writes.at(-1).clickDownloadMiB,200);
  autoStart.checked=true;ui.run('updateDirty()');
  assert.equal(ui.run('dirty'),true,'actual gateway drafts must still be protected');
  input.validity.valid=false;
  await ui.nodes.get('settings').onsubmit({preventDefault(){}});
  assert.equal(ui.value.preferences.autoStart,true,'independent validation cannot block saving gateway settings');
});

test('Tailscale detection fills a read-only URL and preserves its unsaved identity across polls',async()=>{
  const ui=await renderer('en');
  ui.run(`connectionDraft=[{id:'ts',name:'',enabled:true,accessMode:'tailscale',publicUrl:'',tailscaleMode:'funnel',tailscalePort:443,tailscaleNodeId:''}];savedConnections=JSON.stringify(connectionDraft);renderConnections();updateDirty();`);
  const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
  let controls=descendants(ui.nodes.get('connections'));
  const url=controls.find(node=>node.dataset.connectionField==='publicUrl');
  assert.equal(url.readOnly,true);assert.equal(url.onfocus,null);
  let requests=0;
  ui.api.tailscaleSetup=async payload=>{requests++;assert.equal(payload.id,'ts');return {state:'ready',url:'https://mac.example.ts.net',nodeId:'n123',message:'已读取 Tailscale 固定地址。保存配置后启动网关，再检测固定入口。'};};
  await controls.find(node=>node.dataset.connectionAction==='tailscaleInspect').click();
  assert.equal(requests,1);assert.equal(ui.run('dirty'),true);
  assert.equal(ui.run('connectionDraft[0].tailscaleNodeId'),'n123');
  await ui.poll();
  assert.equal(ui.run('connectionDraft[0].publicUrl'),'https://mac.example.ts.net');
  controls=descendants(ui.nodes.get('connections'));
  assert.equal(controls.find(node=>node.dataset.connectionAction==='tailscaleInspect').textContent,'Detect Tailscale and read URL');
});

test('changing the Tailscale HTTPS port invalidates the previously detected URL',async()=>{
  const ui=await renderer();
  ui.run(`connectionDraft=[{id:'ts',enabled:true,accessMode:'tailscale',publicUrl:'https://mac.example.ts.net',tailscaleMode:'funnel',tailscalePort:443,tailscaleNodeId:'n123'}];renderConnections();`);
  const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
  const port=descendants(ui.nodes.get('connections')).find(node=>node.dataset.connectionField==='tailscalePort');
  port.value='8443';port.onchange();
  assert.equal(ui.run('connectionDraft[0].tailscalePort'),8443);
  assert.equal(ui.run('connectionDraft[0].publicUrl'),'');
  assert.equal(ui.run('connectionDraft[0].tailscaleNodeId'),'');
});

test('opening the control panel and starting the gateway need no Documents permission API',async()=>{
  const ui=await renderer();
  assert.deepEqual(ui.calls,['snapshot']);
  assert.equal(ui.nodes.has('documents-access'),false);
  await ui.start();
  assert.deepEqual(ui.calls,['snapshot','start','snapshot']);
  assert.equal(ui.nodes.get('status').textContent,'启动中');
});

test('automatic gateway startup works without a Documents permission API',async()=>{
  const ui=await renderer('zh-CN',{autoStart:true});
  assert.deepEqual(ui.calls,['snapshot','start','snapshot']);
  assert.equal(ui.nodes.get('status').textContent,'启动中');
});

test('overview and client-page gateway controls share pending state and reject duplicate operations',async()=>{
  const ui=await renderer();let started,stopped,starts=0,stops=0;
  ui.api.start=()=>{starts++;return new Promise(resolve=>started=resolve);};
  const start=ui.nodes.get('clients-start').onclick();await new Promise(setImmediate);
  assert.equal(ui.nodes.get('start').disabled,true);assert.equal(ui.nodes.get('clients-start').disabled,true);
  await ui.nodes.get('start').onclick();assert.equal(starts,1);
  started({started:true,message:'正在启动网关'});await start;
  ui.value.runtime.running=true;await ui.poll();
  assert.equal(ui.nodes.get('clients-stop').disabled,false);
  ui.api.stop=()=>{stops++;return new Promise(resolve=>stopped=resolve);};
  const stop=ui.nodes.get('clients-stop').onclick();
  assert.equal(ui.nodes.get('stop').disabled,true);assert.equal(ui.nodes.get('clients-stop').disabled,true);
  await ui.nodes.get('stop').onclick();assert.equal(stops,1);
  ui.value.runtime.running=false;stopped({message:'网关已停止'});await stop;
  for(const id of ['start','clients-start'])assert.equal(ui.nodes.get(id).disabled,false);
  for(const id of ['stop','clients-stop'])assert.equal(ui.nodes.get(id).disabled,true);
});

test('client-page gateway controls retain save checks and recover both buttons on failure',async()=>{
  const ui=await renderer();ui.run('dirty=true');await ui.nodes.get('clients-start').onclick();
  assert.equal(ui.calls.includes('start'),false);assert.match(ui.nodes.get('toast-message').textContent,/请先保存/);
  ui.run('dirty=false');ui.api.start=async()=>{throw Error('start rejected');};
  await ui.nodes.get('clients-start').onclick();
  for(const id of ['start','clients-start'])assert.equal(ui.nodes.get(id).disabled,false);
  ui.value.runtime.running=true;await ui.poll();ui.api.stop=async()=>{throw Error('stop rejected');};
  await ui.nodes.get('clients-stop').onclick();
  for(const id of ['stop','clients-stop'])assert.equal(ui.nodes.get(id).disabled,false);
  assert.match(ui.nodes.get('toast-message').textContent,/stop rejected/);
});

test('startup feedback follows readiness, page changes and later shutdown',async()=>{
  const ui=await renderer();await ui.start();
  assert.match(ui.nodes.get('feedback').textContent,/正在启动/);
  assert.equal(ui.nodes.get('status').textContent,'启动中');
  ui.value.runtime.running=true;await ui.poll();
  assert.equal(ui.nodes.get('status').textContent,'运行中');
  assert.match(ui.nodes.get('feedback').textContent,/已启动/);
  for(const page of ['network','notifications','logs']){
    ui.context.tab(page);await ui.poll();
    assert.doesNotMatch(ui.nodes.get('feedback').textContent,/正在启动/);
  }
  ui.value.runtime.running=false;await ui.poll();
  assert.equal(ui.nodes.get('feedback').textContent,'网关已停止');
});

test('Bark-only readiness and delivery status remain independent of ntfy',async()=>{
  const ui=await renderer('en');
  ui.value.notifications.barkEnabled=true;ui.value.notifications.hasBarkKey=true;
  ui.value.runtime.running=true;
  ui.value.notificationStatus={ntfy:{error:'ntfy failure'},bark:{lastSent:1000,error:''}};
  await ui.poll();
  assert.match(ui.nodes.get('notification-summary').textContent,/Enabled/);
  assert.match(ui.nodes.get('notification-readiness').textContent,/ready/i);
  assert.equal(ui.nodes.get('notification-detail').textContent,'ntfy failure');
  assert.match(ui.nodes.get('bark-detail').textContent,/Last sent/);
  assert.equal(ui.nodes.get('bark-key').value,'');
  assert.match(ui.nodes.get('bark-key').placeholder,/Saved/);
});

test('notification test buttons select the channel and block unsaved drafts',async()=>{
  const ui=await renderer(),calls=[];
  ui.api.testNotification=async value=>{calls.push(value.channel);return {message:value.channel+' accepted'};};
  await ui.nodes.get('test-bark').onclick();await ui.nodes.get('test-notification').onclick();
  assert.deepEqual(calls,['bark','ntfy']);
  ui.run('dirty=true');await ui.nodes.get('test-bark').onclick();
  assert.equal(calls.length,2);assert.match(ui.nodes.get('toast-message').textContent,/请先保存通知配置/);
});

test('Bark drafts survive polling and language changes and remain after saving',async()=>{
  const ui=await renderer();
  const ids=['bark-key','bark-server','bark-enabled','clear-bark-key'];
  for(const id of ids){ui.nodes.get(id).id=id;ui.nodes.get(id).closest=()=>({dataset:{panel:'notifications'}});}
  for(const id of ['bark-enabled','clear-bark-key'])ui.nodes.get(id).type='checkbox';
  ui.run("fields=()=>['bark-key','bark-server','bark-enabled','clear-bark-key'].map($);savedFields=fieldValues()");
  ui.nodes.get('bark-key').value='first-key';ui.nodes.get('bark-enabled').checked=true;
  ui.nodes.get('bark-server').value='https://push.example.com';ui.run('updateDirty()');
  await ui.poll();ui.nodes.get('language').value='en';await ui.nodes.get('language').onchange();
  assert.equal(ui.nodes.get('bark-key').value,'first-key');
  let submitted,resolveSave;
  ui.api.save=payload=>{submitted=payload;return new Promise(resolve=>{resolveSave=resolve;});};
  const first=ui.nodes.get('settings').onsubmit({preventDefault(){}});
  assert.equal(submitted.notifications.barkKey,'first-key');
  assert.equal(submitted.notifications.barkEnabled,true);
  assert.equal(submitted.notifications.barkServer,'https://push.example.com');
  ui.nodes.get('bark-key').value='newer-key';ui.run('updateDirty()');resolveSave(structuredClone(ui.value));await first;
  assert.equal(ui.nodes.get('bark-key').value,'newer-key');assert.equal(ui.run('dirty'),true);
  const second=ui.nodes.get('settings').onsubmit({preventDefault(){}});
  resolveSave(structuredClone(ui.value));await second;
  assert.equal(ui.nodes.get('bark-key').value,'newer-key');
  assert.equal(ui.run("collect().notifications.barkKey"),'');
});

test('preload forwards the selected notification channel over private IPC',()=>{
  const fs=require('node:fs'),vm=require('node:vm'),calls=[];let api;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../desktop/preload.cjs'),'utf8'),{require:()=>({
    contextBridge:{exposeInMainWorld:(name,value)=>{api=value;}},ipcRenderer:{invoke:(...args)=>{calls.push(args);}}
  })});
  assert.equal('documentsAccess' in api,false);
  api.testNotification({channel:'bark'});
  assert.deepEqual(calls,[['bridge:test-notification',{channel:'bark'}]]);
});

test('startup timeout replaces pending feedback and a late ready state recovers',async()=>{
  const ui=await renderer();await ui.start();ui.advance(71000);await ui.poll();
  assert.equal(ui.nodes.get('feedback').hidden,true);
  assert.equal(ui.nodes.get('error').hidden,false);
  assert.match(ui.nodes.get('error').textContent,/启动超时/);
  await ui.poll();assert.match(ui.nodes.get('error').textContent,/启动超时/);
  ui.value.runtime.running=true;await ui.poll();
  assert.equal(ui.nodes.get('error').hidden,true);
  assert.match(ui.nodes.get('feedback').textContent,/已启动/);
});

test('an occupied port ends the pending startup indication',async()=>{
  const ui=await renderer();await ui.start();ui.value.runtime.portOccupied=true;await ui.poll();
  assert.equal(ui.nodes.get('status').textContent,'端口已占用');
  assert.equal(ui.nodes.get('feedback').hidden,true);
  assert.match(ui.nodes.get('error').textContent,/端口/);
});

test('runtime polling preserves a newer save notification',async()=>{
  const ui=await renderer();await ui.start();
  await ui.nodes.get('settings').onsubmit({preventDefault(){}});
  const saved=ui.nodes.get('toast-message').textContent;
  assert.match(saved,/配置已保存/);
  ui.value.runtime.running=true;await ui.poll();
  assert.equal(ui.nodes.get('status').textContent,'运行中');
  assert.equal(ui.nodes.get('toast-message').textContent,saved);
});

test('language changes update runtime feedback and preserve entered values',async()=>{
  const ui=await renderer();await ui.start();
  ui.nodes.get('password').value='private draft';ui.context.applyLanguage('en');
  assert.equal(ui.nodes.get('status').textContent,'Starting');
  assert.equal(ui.nodes.get('feedback').textContent,i18n.english['正在启动网关']);
  assert.equal(ui.nodes.get('password').value,'private draft');
  ui.context.applyLanguage('zh-CN');assert.equal(ui.nodes.get('status').textContent,'启动中');
});

test('saved Windows language initializes the shared UI and selector',async()=>{
  const ui=await renderer('en');
  assert.equal(ui.nodes.get('language').value,'en');
  assert.equal(ui.nodes.get('status').textContent,'Stopped');
});

test('connection additions and removals are unsaved, and language changes preserve edits',async()=>{
  const ui=await renderer();
  ui.run(`connectionDraft=[{id:'example',name:'Home',enabled:true,accessMode:'server',publicUrl:'https://codex.example.com',sshTarget:'my-server',sshRemotePort:18787,proxyUpstream:''}];renderConnections();updateDirty();`);
  assert.equal(ui.nodes.get('dirty-dot').hidden,false);
  ui.nodes.get('language').value='en';await ui.nodes.get('language').onchange();
  assert.equal(ui.context.collect().preferences.connections[0].name,'Home');
  assert.equal(ui.nodes.get('dirty-dot').hidden,false);
  assert.equal(ui.nodes.get('status').textContent,'Stopped');
  await ui.nodes.get('settings').onsubmit({preventDefault(){}});
  assert.equal(ui.nodes.get('dirty-dot').hidden,true);
  ui.run('connectionDraft=[];updateDirty();');
  assert.equal(ui.nodes.get('dirty-dot').hidden,false);
  ui.nodes.get('language').value='zh';await ui.nodes.get('language').onchange();
  assert.equal(ui.nodes.get('status').textContent,'未启动');
});

test('opening logs starts at the newest records without changing their contents',async()=>{
  const ui=await renderer();ui.api.logs=async()=>({text:'new\nold'});
  ui.nodes.get('log-output').scrollTop=500;
  await ui.context.loadLogs();
  assert.equal(ui.nodes.get('log-output').textContent,'new\nold');
  assert.equal(ui.nodes.get('log-output').scrollTop,0);
});


test('QR PNG decodes to the exact one-time fragment URL without exposing it in metadata',async()=>{
  const {pairingImage}=require('../desktop/features/connections/qr.cjs'),{PNG}=require('pngjs'),decode=require('jsqr');
  const url='https://bridge.example.com/#pair='+'x'.repeat(43);
  const result=await pairingImage({id:'test',url,expires:12345,state:'active'});
  assert.equal(result.url,undefined);
  const png=PNG.sync.read(Buffer.from(result.image.split(',')[1],'base64'));
  assert.equal(decode(new Uint8ClampedArray(png.data),png.width,png.height).data,url);
  assert.equal(result.id,'test');assert.equal(result.expires,12345);
});

const cloudflared=require('../desktop/features/connections/cloudflared.cjs');
test('Linux CI artifact selectors match electron-builder architecture names',()=>{
  const fs=require('node:fs'),yaml=require('js-yaml'),{Arch,getArtifactArchName}=require('builder-util');
  const job=yaml.load(fs.readFileSync(path.join(__dirname,'../.github/workflows/desktop.yml'),'utf8')).jobs.linux;
  for(const row of job.strategy.matrix.include){
    for(const [stepName,extension] of [['Install and verify the Debian package','deb'],['Extract and launch the AppImage payload','AppImage']]){
      const script=job.steps.find(step=>step.name===stepName).run.replace(/\$\{\{ matrix\.([\w-]+) \}\}/g,(_,key)=>row[key]);
      assert.ok(script.includes(`*-Linux-${getArtifactArchName(Arch[row.arch],extension)}.${extension}`),`${row.arch} ${extension} selector`);
    }
  }
});
test('Linux smoke cleanup does not stop an already stopped gateway',async()=>{
  const {cleanupGateway}=require('../scripts/smoke-linux.cjs');let running=true,stops=0;
  const worker=async action=>{
    if(action==='snapshot')return {runtime:{running}};
    assert.equal(action,'stop');if(!running)throw Error('No gateway control record');
    running=false;stops++;
  };
  await cleanupGateway(worker);await cleanupGateway(worker);assert.equal(stops,1);
});
test('Linux smoke cleanup preserves unexpected stop errors',async()=>{
  const {cleanupGateway}=require('../scripts/smoke-linux.cjs');
  await assert.rejects(cleanupGateway(async action=>{
    if(action==='snapshot')return {runtime:{running:true}};
    throw Error('owned gateway failed to stop');
  }),/failed to stop/);
});
test('packaging rejects a gateway from another architecture, OS or version',async()=>{
  const fs=require('node:fs/promises'),os=require('node:os'),{Arch}=require('builder-util');
  const verify=require('../scripts/verify-build.cjs'),root=await fs.mkdtemp(path.join(os.tmpdir(),'gateway-build-'));
  const context={packager:{projectDir:root,appInfo:{version:'1.2.2'}},arch:Arch.arm64,electronPlatformName:'linux'};
  try{
    await fs.mkdir(path.join(root,'dist/gateway'),{recursive:true});
    const write=value=>fs.writeFile(path.join(root,'dist/update-version.json'),JSON.stringify(value));
    const valid={platform:'linux',arch:'arm64',version:'1.2.2'};
    await fs.writeFile(path.join(root,'dist/gateway/codex-mobile-gateway'),'fixture');
    for(const wrong of [{arch:'x64'},{platform:'darwin'},{version:'1.2.1'}]){
      await write({...valid,...wrong});await assert.rejects(verify(context),/does not match/);
    }
    await write(valid);
    const bundled=path.join(root,'dist/gateway/_internal/cloudflared');
    await fs.mkdir(bundled,{recursive:true});await fs.mkdir(path.join(root,'scripts'));
    await fs.writeFile(path.join(root,'scripts/cloudflared-lock.json'),JSON.stringify({version:'fixture'}));
    await fs.writeFile(path.join(bundled,'version.json'),JSON.stringify({platform:'linux',arch:'arm64',version:'fixture'}));
    await fs.writeFile(path.join(bundled,'cloudflared'),'fixture');await fs.writeFile(path.join(bundled,'LICENSE'),'license');
    await verify(context);
    await fs.writeFile(path.join(bundled,'version.json'),JSON.stringify({platform:'linux',arch:'amd64',version:'fixture'}));
    await assert.rejects(verify(context),/cloudflared/);
    await fs.writeFile(path.join(bundled,'version.json'),JSON.stringify({platform:'linux',arch:'arm64',version:'fixture'}));
    await fs.unlink(path.join(root,'dist/gateway/codex-mobile-gateway'));
    await assert.rejects(verify(context),/ENOENT/);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});
test('Cloudflare installer selects official platform assets and rejects unsafe redirects',async()=>{
  assert.equal(cloudflared.assetName('darwin','arm64'),'cloudflared-darwin-arm64.tgz');
  assert.equal(cloudflared.assetName('darwin','x64'),'cloudflared-darwin-amd64.tgz');
  assert.equal(cloudflared.assetName('win32','x64'),'cloudflared-windows-amd64.exe');
  assert.equal(cloudflared.assetName('linux','x64'),'cloudflared-linux-amd64');
  assert.equal(cloudflared.assetName('linux','arm64'),'cloudflared-linux-arm64');
  assert.throws(()=>cloudflared.assetName('linux','arm'),/手动/);
  assert.throws(()=>cloudflared.assetName('win32','arm64'),/手动/);
  let calls=0;
  const fetch=async()=>{calls++;return new Response(null,{status:302,headers:{location:'https://evil.example/cloudflared'}});};
  await assert.rejects(cloudflared.download(fetch,'https://api.github.com/repos/cloudflare/cloudflared/releases/latest',1000,AbortSignal.timeout(1000)),/来源/);
  assert.equal(calls,1);
  for(const url of ['http://github.com/cloudflare/cloudflared/releases/download/x/y','https://github.com/other/project/releases/download/x/y','https://user:pass@github.com/cloudflare/cloudflared/releases/download/x/y'])assert.throws(()=>cloudflared.allowedUrl(url));
});

test('Cloudflare install verifies before execution, preserves settings and never overwrites another binary',async()=>{
  const fs=require('node:fs/promises');
  await fs.mkdir(path.join(__dirname,'../.tmp'),{recursive:true});
  const dir=await fs.mkdtemp(path.join(__dirname,'../.tmp/cloudflare-install-test-'));
  const bytes=Buffer.from('fixture executable'),name='cloudflared-windows-amd64.exe',tag='2026.1.0';
  const asset={name,size:bytes.length,digest:'sha256:'+cloudflared.digest(bytes),browser_download_url:'https://github.com/cloudflare/cloudflared/releases/download/'+tag+'/'+name};
  let checks=0,bad=false;
  const fetch=async(url,options)=>{
    assert.equal(options.credentials,'omit');assert.equal(options.redirect,'manual');
    return new Response(url.includes('api.github.com')?JSON.stringify({tag_name:tag,assets:[{...asset,digest:bad?'sha256:'+'0'.repeat(64):asset.digest}]}):bytes);
  };
  const args={dataDir:dir,platform:'win32',arch:'x64',fetch,check:async()=>{checks++;return 'cloudflared version 2026.1.0';}};
  try{
    await fs.writeFile(path.join(dir,'desktop.json'),'original settings');
    bad=true;await assert.rejects(cloudflared.install(args),/SHA-256/);assert.equal(checks,0);
    assert.deepEqual(await fs.readdir(dir),['desktop.json']);
    bad=false;const result=await cloudflared.install(args);assert.equal(checks,1);
    assert.deepEqual(await fs.readFile(result.path),bytes);
    assert.equal(await fs.readFile(path.join(dir,'desktop.json'),'utf8'),'original settings');
    await fs.writeFile(result.path,'existing customized program');
    await assert.rejects(cloudflared.install(args),/已有不同程序/);
    assert.equal(await fs.readFile(result.path,'utf8'),'existing customized program');
    assert.equal((await fs.readdir(path.join(dir,'bin'))).filter(name=>name.startsWith('.cloudflared-')).length,0);
  }finally{await fs.rm(dir,{recursive:true,force:true});}
});

test('Mac archive reader extracts only the regular executable and rejects symlinks and truncation',()=>{
  const {gzipSync}=require('node:zlib');
  function archive(name,type='0',length=4){const header=Buffer.alloc(512);header.write(name);header.write(length.toString(8).padStart(11,'0'),124);header.write(type,156);return gzipSync(Buffer.concat([header,Buffer.from('test'),Buffer.alloc(508)]));}
  assert.equal(cloudflared.executableFromArchive(archive('./cloudflared'),'mac.tgz').toString(),'test');
  assert.throws(()=>cloudflared.executableFromArchive(archive('../cloudflared'),'mac.tgz'),/未找到/);
  assert.throws(()=>cloudflared.executableFromArchive(archive('cloudflared','2'),'mac.tgz'),/格式/);
  assert.throws(()=>cloudflared.executableFromArchive(archive('cloudflared','0',9999),'mac.tgz'),/不完整/);
});

for(const arch of ['x64','arm64'])test('Linux '+arch+' installer verifies the raw executable before probing',async()=>{
  const fs=require('node:fs/promises'),os=require('node:os');
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'cloudflared-linux-'));
  const bytes=Buffer.from('synthetic ELF'),name=cloudflared.assetName('linux',arch),tag='2026.1.0';
  const asset={name,size:bytes.length,digest:'sha256:'+cloudflared.digest(bytes),browser_download_url:'https://github.com/cloudflare/cloudflared/releases/download/'+tag+'/'+name};
  let checks=0,bad=false;
  const fetch=async url=>new Response(url.includes('api.github.com')?JSON.stringify({tag_name:tag,assets:[{...asset,digest:bad?'sha256:'+'0'.repeat(64):asset.digest}]}):bytes);
  try{
    const args={dataDir:dir,platform:'linux',arch,fetch,check:async file=>{
      checks++;assert.deepEqual(await fs.readFile(file),bytes);
      if(process.platform!=='win32')assert.equal((await fs.stat(file)).mode&0o777,0o700);
      return 'cloudflared version '+tag;
    }};
    bad=true;await assert.rejects(cloudflared.install(args),/SHA-256/);assert.equal(checks,0);
    bad=false;const result=await cloudflared.install(args);assert.equal(checks,1);
    assert.equal(path.basename(result.path),'cloudflared');
    assert.equal(path.basename(path.dirname(result.path)),'cloudflared-'+tag+'-'+arch);
    assert.deepEqual(await fs.readFile(result.path),bytes);
  }finally{await fs.rm(dir,{recursive:true,force:true});}
});

test('missing Cloudflare opens setup before start and installing preserves drafts',async()=>{
  const ui=await renderer();let started=0;
  ui.value.preferences.tunnel=true;await ui.poll();ui.api.start=async()=>{started++;};
  ui.api.checkCloudflared=async()=>{throw Error('未找到 cloudflared，请点击一键安装，或选择已下载的程序。');};
  await ui.start();assert.equal(started,0);assert.equal(ui.run('activeTab'),'network');
  for(const id of ['cloudflared','password','ntfy-topic'])ui.nodes.get(id).closest=()=>({dataset:{panel:'advanced'}});
  ui.run("fields=()=>['cloudflared','password','ntfy-topic'].map($);savedFields=fieldValues()");
  ui.nodes.get('password').value='unsaved password';ui.nodes.get('ntfy-topic').value='draft-topic';
  ui.api.installCloudflared=async()=>({path:'/data/bin/cloudflared',version:'cloudflared version fixture'});
  await ui.nodes.get('install-cloudflared').onclick();
  assert.equal(ui.nodes.get('cloudflared').value,'/data/bin/cloudflared');
  assert.equal(ui.nodes.get('password').value,'unsaved password');assert.equal(ui.nodes.get('ntfy-topic').value,'draft-topic');
  assert.match(ui.nodes.get('toast-message').textContent,/保存配置/);
});


test('update UI blocks installation while settings are unsaved and preserves drafts',async()=>{
  const ui=await renderer();let installs=0;ui.api.installUpdate=async()=>{installs++;};
  ui.value.update={state:'available',current:'0.2.0-beta.5',version:'0.2.0-beta.6',notes:'<b>plain text</b>'};
  await ui.poll();ui.run("connectionDraft=[{id:'unsaved'}];updateDirty()");
  await ui.nodes.get('install-update').onclick();assert.equal(installs,0);
  assert.match(ui.nodes.get('toast-message').textContent,/先保存/);
  assert.equal(ui.nodes.get('update-notes').textContent,'<b>plain text</b>');
  ui.context.applyLanguage('en');assert.equal(ui.context.collect().preferences.connections[0].id,'unsaved');
});

test('check update button locks immediately and unlocks after failed check',async()=>{
  const ui=await renderer();let release;
  ui.value.update={state:'idle',current:'1.2.2'};
  await ui.poll();
  ui.api.checkUpdate=()=>new Promise(resolve=>{release=value=>{ui.value.update=value;resolve(value);};});
  const clicked=ui.nodes.get('check-update').onclick();
  assert.equal(ui.nodes.get('check-update').disabled,true);
  release({state:'error',current:'1.2.2',message:'network failed'});
  await clicked;await ui.poll();
  assert.equal(ui.nodes.get('check-update').disabled,false);
  assert.match(ui.nodes.get('update-state').textContent,/network failed/);
});

test('download and retry states disable updates without permanently locking settings',async()=>{
  const ui=await renderer();
  ui.value.update={state:'downloading',current:'0.2.0-beta.5',version:'0.2.0-beta.6',received:20,total:100};
  await ui.poll();assert.equal(ui.nodes.get('install-update').disabled,true);assert.equal(ui.nodes.get('settings').inert,true);assert.equal(ui.nodes.get('update-progress').value,20);
  ui.value.update.state='error';ui.value.update.message='download failed';await ui.poll();
  assert.equal(ui.nodes.get('install-update').disabled,false);assert.equal(ui.nodes.get('settings').inert,false);
});

test('unsupported updates explain manual downloads in both languages',async()=>{
  const ui=await renderer();ui.value.update={state:'unsupported',current:'1.2.2'};
  await ui.poll();assert.equal(ui.nodes.get('check-update').disabled,true);
  assert.equal(ui.nodes.get('install-update').hidden,true);
  assert.match(ui.nodes.get('update-state').textContent,/发布页面/);
  ui.run("applyLanguage('en')");assert.match(ui.nodes.get('update-state').textContent,/releases page/);
});

test('login validity is collected as zero and survives polling and language edits',async()=>{
  const ui=await renderer();const field=ui.nodes.get('session-hours');field.id='session-hours';field.type='number';field.closest=selector=>selector==='[data-panel]'?({dataset:{panel:'network'}}):null;
  ui.nodes.get('settings').querySelectorAll=()=>[field];
  ui.run('dirty=false;render(snapshot)');assert.equal(Number(field.value),12);
  field.value='0';ui.run('updateDirty()');await ui.poll();ui.run("applyLanguage('en')");
  assert.equal(field.value,'0');assert.equal(ui.run('collect().auth.sessionHours'),0);
});

test('device policy drafts survive list refresh and language changes and use local IPC',async()=>{
  const ui=await renderer();const calls=[];
  const state={policy:{allowlistEnabled:false,allowlist:[],blocklist:[],trustedProxies:[]},sessions:[]};
  ui.api.devices=async payload=>{calls.push(payload);if(payload.action==='save')Object.assign(state.policy,payload.policy);return structuredClone(state);};
  await ui.run('loadDevices()');
  ui.nodes.get('ip-allowlist').value='192.0.2.7';ui.nodes.get('allowlist-enabled').checked=true;
  ui.nodes.get('device-policy').oninput();await ui.run('loadDevices()');ui.run("applyLanguage('en')");
  assert.equal(ui.nodes.get('ip-allowlist').value,'192.0.2.7');
  assert.equal(ui.nodes.get('allowlist-enabled').checked,true);
  await ui.nodes.get('device-policy').onsubmit({preventDefault(){}});
  assert.equal(calls.at(-1).action,'save');assert.deepEqual([...calls.at(-1).policy.allowlist],['192.0.2.7']);
  assert.equal(ui.run('devicesDirty'),false);assert.equal(ui.nodes.get('device-policy').inert,false);
});

test('failed device policy save retains the editable draft',async()=>{
  const ui=await renderer();ui.api.devices=async()=>{throw Error('synthetic failure');};
  ui.nodes.get('ip-allowlist').value='192.0.2.7';ui.nodes.get('device-policy').oninput();
  await ui.nodes.get('device-policy').onsubmit({preventDefault(){}});
  assert.equal(ui.run('devicesDirty'),true);assert.equal(ui.nodes.get('ip-allowlist').value,'192.0.2.7');
  assert.equal(ui.nodes.get('device-policy').inert,false);
});

test('LAN selection drafts survive polling and are saved with local browser access disabled',async()=>{
  const ui=await renderer();
  ui.value.networkInterfaces=[{name:'Ethernet',address:'192.168.1.7'},{name:'VMnet8',address:'192.168.55.1'}];
  await ui.poll();
  assert.deepEqual(Array.from(ui.run('lanDraft')),['192.168.1.7','192.168.55.1']);
  ui.nodes.get('lan-scope').value='selected';
  ui.nodes.get('local-access').checked=false;
  ui.run("lanDraft=['192.168.1.7'];savedFields=fieldValues();savedLan='[]';updateDirty()");
  await ui.poll();
  assert.deepEqual(Array.from(ui.run('collect().preferences.lanAddresses')),['192.168.1.7']);
  assert.equal(ui.run('collect().preferences.localAccess'),false);
  let saved;
  ui.api.save=async value=>{saved=value;ui.value.preferences={...ui.value.preferences,...value.preferences};return structuredClone(ui.value);};
  await ui.nodes.get('settings').onsubmit({preventDefault(){}});
  assert.deepEqual(Array.from(saved.preferences.lanAddresses),['192.168.1.7']);
  assert.equal(ui.run('dirty'),false);
  ui.value.runtime.running=true;await ui.poll();
  assert.equal(ui.nodes.get('lan-scope').disabled,true);
  assert.equal(ui.nodes.get('local-access').disabled,true);
});

test('project navigation uses fixed project destinations',async()=>{
  const ui=await renderer(),opened=[];ui.api.open=async target=>opened.push(target);
  await ui.nodes.get('project-home').onclick();
  assert.deepEqual(opened,['project-home']);
  assert.equal(ui.nodes.has('project-issues'),false);assert.equal(ui.nodes.has('project-pulls'),false);
});

test('entry notification preference, current-link test and update readiness stay independent of chat watches',async()=>{
  const ui=await renderer();
  ui.value.preferences.tunnel=true;
  await ui.poll();
  assert.match(ui.nodes.get('update-address-state').textContent,/未开启/);
  ui.value.notifications.addressEnabled=true;
  ui.value.notifications.addressName='Home computer';
  await ui.poll();
  assert.match(ui.nodes.get('update-address-state').textContent,/至少一个/);
  ui.value.notifications.barkEnabled=true;
  await ui.poll();
  assert.equal(ui.nodes.get('address-enabled').checked,true);
  assert.equal(ui.nodes.get('address-name').value,'Home computer');
  assert.match(ui.nodes.get('update-address-state').textContent,/已开启/);
  const calls=[];ui.api.testNotification=async payload=>{calls.push(payload);return {message:'accepted'};};
  await ui.nodes.get('test-address').onclick();
  assert.equal(calls[0].channel,'address');
  const config=ui.run('collect()');
  assert.equal(config.notifications.addressEnabled,true);
  assert.equal(config.notifications.addressName,'Home computer');
});


test('hidden controller pauses snapshots and unchanged state does not rebuild address cards',async()=>{
 const ui=await renderer();let reads=0,paints=0;
 ui.api.snapshot=async()=>{reads++;return structuredClone(ui.value);};
 ui.nodes.get('addresses').replaceChildren=()=>paints++;
 await ui.poll();await ui.poll();assert.equal(paints,0);
 ui.value.runtime.running=true;await ui.poll();assert.equal(paints,1);
 ui.context.document.hidden=true;await ui.context.document.visibilitychange();await ui.poll();assert.equal(reads,3);
 ui.context.document.hidden=false;await ui.context.document.visibilitychange();await new Promise(setImmediate);assert.equal(reads,4);
});

test('snapshot worker reuses a process and isolates data directory changes',async()=>{
 const {createSnapshotWorker}=require('../desktop/shared/controller.cjs'),{EventEmitter}=require('node:events');
 let spawned=0;const children=[];
 const launch=()=>{spawned++;const child=new EventEmitter();child.stdout=new EventEmitter();child.stdout.setEncoding=()=>{};child.stderr=new EventEmitter();child.stdin=new EventEmitter();child.kill=()=>{child.killed=true;};child.stdin.write=()=>setImmediate(()=>child.stdout.emit('data',JSON.stringify({ok:true,result:{value:spawned}})+'\n'));children.push(child);return child;};
 const worker=createSnapshotWorker({launch}),a={executable:'fixture',dataDir:'a'};
 try{assert.equal((await worker.read(a)).value,1);await worker.read(a);assert.equal(spawned,1);
  assert.equal((await worker.read({...a,dataDir:'b'})).value,2);assert.ok(children[0].killed);
  children[1].emit('close');assert.equal((await worker.read(a)).value,3);
 }finally{worker.close();}assert.ok(children[2].killed);
});


test('native window title ignores page changes and only updates when locale changes',()=>{
  const fs=require('node:fs'),vm=require('node:vm'),requireMain=require('node:module').createRequire(path.resolve(__dirname,'../desktop/main.cjs'));
  const handlers={},events={};let instance,changes=0;
  class Window {
    constructor(options){instance=this;this.title=options.title;this.webContents={mainFrame:{url:require('node:url').pathToFileURL(path.resolve(__dirname,'../desktop/index.html')).href},setWindowOpenHandler(){},on(){},once(){}};}
    on(name,handler){events[name]=handler;}loadFile(){}getTitle(){return this.title;}setTitle(value){changes++;this.title=value;}
  }
  const context=vm.createContext({__dirname:path.resolve(__dirname,'../desktop'),process:{env:{},platform:'darwin'},require(name){
    if(name==='electron')return {app:{requestSingleInstanceLock:()=>true,whenReady:()=>({then(){}}),on(){},getPath:()=>'.tmp'},BrowserWindow:Window,ipcMain:{handle:(name,handler)=>handlers[name]=handler}};
    if(name==='node:fs')return {...fs,mkdirSync(){},writeFileSync(){}};
    if(name==='./features/connections/qr.cjs')return {};
    if(name==='./shared/gateway-language.cjs')return {publishLanguage(){}};
    return requireMain(name);
  }});
  vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../desktop/main.cjs'),'utf8'),context);
  context.createWindow();context.register();
  assert.equal(instance.title,'Codex 手机网关');
  let prevented=0;events['page-title-updated']({preventDefault(){prevented++;}},'Different page title');
  assert.equal(prevented,1);assert.equal(instance.title,'Codex 手机网关');
  const event={sender:instance.webContents,senderFrame:instance.webContents.mainFrame};
  handlers['bridge:set-language'](event,'zh-CN');assert.equal(changes,0);
  handlers['bridge:set-language'](event,'en');assert.equal(changes,1);
  handlers['bridge:set-language'](event,'en');assert.equal(changes,1);
});


test('English toast translates account failures and dynamic operation feedback',async()=>{
 const ui=await renderer('en');
 for(const message of ['请先保存连接配置。', '账号不存在，请刷新列表', '部分通道发送失败：ntfy；请在手机确认其他通道是否收到。']){
  ui.context.feedback(message,true);assert.doesNotMatch(ui.nodes.get('toast-message').textContent,/[\u4e00-\u9fff]/);
 }
});

test('SSH phone URL can stay blank and explains domain DNS and trusted IP HTTPS in both languages',async()=>{
  const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
  for(const language of ['zh-CN','en']){
    const ui=await renderer(language);
    ui.run(`connectionDraft=[{id:'server',name:'',enabled:true,accessMode:'server',publicUrl:'',sshAuth:'password',sshHost:'93.184.216.34',sshUser:'example',sshPort:22,sshRemotePort:18787}];renderConnections();`);
    const controls=descendants(ui.nodes.get('connections'));
    const url=controls.find(node=>node.dataset.connectionField==='publicUrl');
    assert.equal(url.value,'');assert.equal(url.onfocus,null);assert.notEqual(url.required,true);
    const text=controls.map(node=>node.textContent).join('\n');
    if(language==='en'){
      assert.match(text,/Phone access URL \(optional\)/);assert.match(text,/DNS record.*public IP/);
      assert.match(text,/trusted HTTPS certificate matching that IP/);assert.doesNotMatch(text,/[\u4e00-\u9fff]/);
    }else{
      assert.match(text,/手机访问地址（可选）/);assert.match(text,/域名解析到服务器公网 IP/);assert.match(text,/匹配该 IP 的受信任 HTTPS 证书/);
    }
  }
});
