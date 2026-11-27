'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {OfficialRemote}=require('../desktop/features/zcode-official/controller.cjs');
const {serveLocal}=require('../desktop/shared/local-service.cjs');
const root=path.resolve(__dirname,'../.tmp');
function profile(t){const p=fs.mkdtempSync(path.join(root,'official-controller-'));t.after(()=>fs.rmSync(p,{recursive:true,force:true}));return p;}
const link='https://zcode.z.ai/remote/v4?sid=native-session&mid=this-machine';
function storage(extra={}){return {isEncryptionAvailable(){throw Error('synchronous storage probe');},isAsyncEncryptionAvailable:async()=>true,getSelectedStorageBackend:()=> 'gnome_libsecret',encryptStringAsync:async text=>Buffer.from('v11sealed:'+text),decryptStringAsync:async data=>({result:data.toString().replace(/^v11sealed:/,'')}),...extra};}
function fixture(t){const p=profile(t),home=path.join(p,'native');fs.mkdirSync(home);fs.mkdirSync(path.join(p,'desktop-sessions'));
fs.writeFileSync(path.join(home,'telemetry-state.json'),JSON.stringify({deviceMid:'this-machine'}));
fs.writeFileSync(path.join(p,'desktop-sessions/settings.json'),JSON.stringify({discovered:{zcode:{dataDirectory:home}}}));
// Keep the existing OS-vault contract tests explicit; Mac's profile store is covered below.
const Controller=platformController('linux',serveLocal);return new Controller({electron:{safeStorage:storage()},profile:p,rendererPath:__filename});}
test('official target must match existing native telemetry without generating a new identity',t=>{
 const c=fixture(t);assert.equal(c.localLink(link).mid,'this-machine');
 assert.throws(()=>c.localLink(link.replace('this-machine','another-machine')),/本机/);
 assert.throws(()=>c.localLink(link.replace('zcode.z.ai','evil.invalid')),/官方/);
 assert.throws(()=>c.localLink(link+'&mid=this-machine'),/官方/);
});
test('public state never contains authentication and preserves explicit local/official selection',t=>{
 const c=fixture(t);c.auth={jwt:'private-secret',link,provider:'zai',name:'Fixture'};
 const state=c.status();assert.equal(state.selected,false);assert.equal(state.link,link);assert.equal(JSON.stringify(state).includes('private-secret'),false);
});
test('saved link and encrypted login survive controller restart and a local-mode selection',async t=>{
 const c=fixture(t);c.electron.safeStorage=storage();
 c.save(link);assert.equal(c.status().configured,false);
 fs.writeFileSync(c.authFile,await c.electron.safeStorage.encryptStringAsync(JSON.stringify({jwt:'private-secret',provider:'zai',name:'Fixture',link})),{mode:0o600});
 const restored=new c.constructor({electron:c.electron,profile:c.profile,rendererPath:__filename});await restored.restore();
 assert.equal(restored.status().configured,true);assert.equal(restored.status().connected,false);assert.equal(restored.status().selected,false);
 assert.equal(restored.status().link,link);assert.equal(restored.status().accountName,'Fixture');
 if(process.platform!=='win32')assert.equal(fs.statSync(c.settingsFile).mode&0o777,0o600);assert.equal(JSON.stringify(restored.status()).includes('private-secret'),false);
 const config=JSON.parse(fs.readFileSync(path.join(c.profile,'desktop-sessions/settings.json')));config.zcodeConnectionMode='official-remote';
 fs.writeFileSync(path.join(c.profile,'desktop-sessions/settings.json'),JSON.stringify(config));assert.equal(restored.status().selected,true);
});
test('standby pairing waits for explicit access before reading the keychain and shares one private listener',async t=>{
 const c=platformFixture(t,'linux');t.after(()=>c.dispose());c.save(link);
 fs.writeFileSync(c.authFile,await c.electron.safeStorage.encryptStringAsync(JSON.stringify({jwt:'fixture',provider:'zai',link})));
 let decrypts=0;const decode=c.electron.safeStorage.decryptStringAsync;
 c.electron.safeStorage.decryptStringAsync=async bytes=>{decrypts++;return decode(bytes);};
 let listeners=0;const start=c.startServer.bind(c);c.startServer=()=>{listeners++;return start();};
 await Promise.all([c.start({standby:true}),c.start({standby:true})]);
 assert.equal(listeners,1);assert.equal(decrypts,0);assert.equal((await c.handle({command:'status'})).loginDeferred,true);
 await c.start();await c.restorePending;
 assert.equal(listeners,1);assert.equal(decrypts,1);assert.equal(c.status().configured,true);
 assert.equal(c.status().connected,false);
});
test('saving configuration cannot silently move a live connection to a different target',t=>{
 const c=fixture(t);c.save(link);c.runtime={};
 assert.throws(()=>c.save(link.replace('native-session','another-session')),/先断开/);
 assert.equal(c.status().link,link);assert.throws(()=>c.save(link.replace('this-machine','another-machine')),/本机/);
});
test('legacy encrypted login supplies the target and forget retains the saved link',async t=>{
 const c=fixture(t);c.electron.safeStorage=storage();
 fs.writeFileSync(c.authFile,await c.electron.safeStorage.encryptStringAsync(JSON.stringify({jwt:'fixture-only',provider:'zai',link})));await c.restore();assert.equal(c.targetLink(),link);
 await c.forget();assert.equal(c.status().configured,false);assert.equal(c.status().link,link);
 const restored=new c.constructor({electron:c.electron,profile:c.profile,rendererPath:__filename});await restored.restore();
 assert.equal(restored.status().configured,false);assert.equal(restored.status().link,link);
});
test('requests cannot cross a relay epoch or bypass expiry and action validation',async t=>{
 const c=fixture(t);c.runtime={};c.owner={epoch:'current',device:'native-session'};
 await assert.rejects(c.handle({command:'call',request:{action:'send',expectedProcess:{epoch:'old',device:'native-session'},expiresAt:Date.now()+10000}}),/已变化/);
 await assert.rejects(c.handle({command:'call',request:{action:'send',expectedProcess:c.owner,expiresAt:Date.now()-1}}),/过期/);
 await assert.rejects(c.handle({command:'call',request:{action:'arbitrary-eval'}}),/不支持/);
});
test('forget closes pending login, drops relay and removes encrypted credentials only',async t=>{
 const c=fixture(t);let closed=0,disposed=0;c.loginWindow={destroy(){closed++;}};c.runtime={dispose(){disposed++;}};c.auth={jwt:'private-secret'};
 fs.writeFileSync(c.authFile,'ciphertext');await c.forget();assert.equal(closed,1);assert.equal(disposed,1);assert.equal(c.auth,null);assert.equal(fs.existsSync(c.authFile),false);
});
async function request(record,token,request){return new Promise((resolve,reject)=>{
 const socket=net.connect(record.endpoint);const chunks=[];socket.on('error',reject);socket.on('data',c=>chunks.push(c));
 socket.on('end',()=>{const data=Buffer.concat(chunks);resolve(data.length?JSON.parse(data.subarray(4)):null);});
 socket.on('connect',()=>{const data=Buffer.from(JSON.stringify({token,request})),head=Buffer.alloc(4);head.writeUInt32BE(data.length);socket.write(Buffer.concat([head,data]));});
});}
test('POSIX private IPC authenticates before dispatch and redacts internal errors',{skip:process.platform==='win32'},async t=>{
 const p=profile(t);let calls=0;const stop=await serveLocal(p,'zcode-official',value=>{calls++;if(value.fail)throw Error('secret-token');return {safe:true};});t.after(stop);
 const record=JSON.parse(fs.readFileSync(path.join(p,'zcode-official-ipc.json')));
 assert.equal(fs.statSync(path.join(p,'zcode-official-ipc.json')).mode&0o777,0o600);
 assert.equal(fs.statSync(path.dirname(record.endpoint)).mode&0o777,0o700);
 assert.equal(await request(record,'wrong',{}),null);assert.equal(calls,0);
 assert.deepEqual(await request(record,record.token,{}),{ok:true,value:{safe:true}});
 const error=await request(record,record.token,{fail:true});assert.equal(error.ok,false);assert.equal(JSON.stringify(error).includes('secret-token'),false);
});

function platformController(platform,serveLocal){
 const vm=require('node:vm'),{createRequire}=require('node:module'),filename=require.resolve('../desktop/features/zcode-official/controller.cjs'),nativeRequire=createRequire(filename);
 const scope=vm.createContext({require:name=>name==='../../shared/local-service.cjs'?{serveLocal}:nativeRequire(name),module:{exports:{}},process:{platform},Buffer,URL,setTimeout,clearTimeout,setInterval,clearInterval});
 vm.runInContext(fs.readFileSync(filename,'utf8'),scope);return scope.module.exports.OfficialRemote;
}
test('optional IPC startup failure remains isolated and never advertises a usable connection',async t=>{
 const Controller=platformController('win32',async()=>{throw Error('private-path-or-token');}),p=profile(t),c=new Controller({electron:{safeStorage:storage()},profile:p,rendererPath:__filename,localServiceWorker:{executable:'gateway.exe'}});
 assert.equal(await c.start(),false);assert.equal(c.status().available,false);assert.equal(c.status().connected,false);assert.match(c.status().reason,/其他网关功能不受影响/);assert.equal(JSON.stringify(c.status()).includes('private-path-or-token'),false);
});
test('Linux credentials require a real keyring and never permit plaintext or pre-ready storage',async t=>{
 const Controller=platformController('linux',async()=>()=>{}),p=profile(t);
 for(const backend of ['basic_text','unknown','gnome_libsecret','kwallet','kwallet5','kwallet6']){
  const c=new Controller({electron:{safeStorage:storage({getSelectedStorageBackend:()=>backend})},profile:p,rendererPath:__filename});
  assert.equal(await c.secure(),!['basic_text','unknown'].includes(backend));assert.equal(await c.start(),true);
  assert.equal(c.status().available,true);assert.equal(c.status().secureStorageAvailable,!['basic_text','unknown'].includes(backend));
  c.dispose();
 }
});

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function platformFixture(t,platform='linux',safe=storage({getSelectedStorageBackend:()=> 'gnome_libsecret'}),serve=async()=>()=>{}){
 const base=fixture(t),Controller=platformController(platform,serve);
 return new Controller({electron:{safeStorage:safe},profile:base.profile,rendererPath:__filename});
}
function saved(c,tag='v11'){
 const auth={jwt:'fixture-token',provider:'zai',name:'Fixture',link};
 fs.writeFileSync(c.authFile,Buffer.from(tag+'fixture-ciphertext'),{mode:0o600});c.save(link);return auth;
}
function loginBrowser(c,data){
 const {EventEmitter}=require('node:events');
 const windows=[],partitions=[],browserSession={setPermissionRequestHandler(){},setPermissionCheckHandler(){}};
 class Window extends EventEmitter{
  constructor(options){super();this.options=options;this.loaded=[];this.shown=0;this.destroyed=false;windows.push(this);this.webContents=new EventEmitter();this.webContents.getURL=()=>link;this.webContents.setWindowOpenHandler=handler=>{this.openHandler=handler;};this.webContents.executeJavaScript=async source=>typeof data==='function'?data(source):data;}
  isDestroyed(){return this.destroyed;}show(){this.shown++;}focus(){}async loadURL(url){this.loaded.push(url);}destroy(){if(!this.destroyed){this.destroyed=true;this.emit('closed');}}
 }
 c.electron.BrowserWindow=Window;c.electron.session={fromPartition:name=>{partitions.push(name);return browserSession;}};
 return {windows,partitions,browserSession};
}
function loginStorage(c,values){
 const reads=[];loginBrowser(c,source=>require('node:vm').runInNewContext(source,{localStorage:{getItem(key){reads.push(key);return values[key]??null;}}}));return reads;
}
async function browserAuthorization(t,{external=true,direct=false,failShell=false,bigmodel=false,beforeOpen}={}){
 const c=platformFixture(t,'linux');t.after(()=>c.dispose());const browser=loginBrowser(c,null),opened=[];
 c.electron.shell={openExternal:async url=>{opened.push(url);if(failShell)throw Error('synthetic shell failure');}};
 await c.login(link,{external});const main=c.loginWindow,authorize=new URL(bigmodel?'https://open.bigmodel.cn/login':'https://zchat.z.ai/api/oauth/authorize');
 authorize.searchParams.set('state','fixture-authorization-nonce');authorize.searchParams.set(bigmodel?'redirect':'redirect_uri',link);
 const decision=main.openHandler({url:direct?authorize.href:'about:blank'});assert.equal(decision.action,'allow');
 decision.createWindow(decision.overrideBrowserWindowOptions);const popup=browser.windows.at(-1);let prevented=false;
 popup.webContents.emit('will-navigate',{preventDefault(){prevented=true;}},authorize.href);beforeOpen?.({c,main,popup});await tick();
 const receipt=new URL(link);receipt.searchParams.set('state','fixture-authorization-nonce');receipt.searchParams.set('code','fixture-code');
 return {c,main,popup,opened,authorize,receipt,prevented,browser};
}

test('system browser authorization handles blank and direct popups and accepts each matching receipt only once',async t=>{
 for(const direct of [false,true]){
  const {c,popup,opened,authorize,receipt,prevented}=await browserAuthorization(t,{direct,bigmodel:direct});
  assert.equal(prevented,true);assert.deepEqual(opened,[authorize.href]);assert.equal(popup.options.show,false);assert.equal(c.status().browserLoginPending,true);assert.equal(c.status().canSubmitLoginReceipt,true);
  popup.webContents.emit('will-redirect',{preventDefault(){}},authorize.href);await tick();assert.equal(opened.length,1,'the same pending authorization cannot open the browser twice');
  await assert.rejects(c.login(link),/切换登录方式/);
  if(direct){receipt.searchParams.delete('code');receipt.searchParams.set('authCode','fixture-code');}
  await c.completeLoginReceipt(receipt.href);assert.deepEqual(popup.loaded,[receipt.href]);assert.equal(c.status().canSubmitLoginReceipt,false);
  await assert.rejects(c.completeLoginReceipt(receipt.href),/结束或过期/);assert.equal(popup.loaded.length,1);assert.equal(c.auth,null);assert.equal(fs.existsSync(c.authFile),false);
 }
});

test('system browser receipts require the exact nonce callback origin path and expected query',async t=>{
 const {c,popup,receipt}=await browserAuthorization(t);
 const invalid=[receipt.href.replace('https:','http:'),receipt.href.replace('zcode.z.ai','evil.invalid'),receipt.href.replace('/remote/v4','/other'),receipt.href.replace('https://','https://user@'),receipt.href+'#fragment',receipt.href.replace('fixture-authorization-nonce','other-nonce'),receipt.href+'&state=fixture-authorization-nonce',receipt.href+'&code=duplicate',receipt.href+'&authCode=second-code',receipt.href+'&unexpected=value',receipt.href.replace('this-machine','other-machine'),receipt.href+'&mid=this-machine'];
 const missing=new URL(receipt);missing.searchParams.delete('state');invalid.push(missing.href);missing.searchParams.set('state','fixture-authorization-nonce');missing.searchParams.delete('code');invalid.push(missing.href);missing.searchParams.set('code',' ');invalid.push(missing.href);
 for(const value of invalid){await assert.rejects(c.completeLoginReceipt(value),/不匹配/);assert.equal(c.pendingAuthorization.used,false);}
 for(const value of ['not a URL',{},'x'.repeat(16385)])await assert.rejects(c.completeLoginReceipt(value),/完整/);
 assert.equal(popup.loaded.length,0);assert.equal(fs.existsSync(c.authFile),false);await c.completeLoginReceipt(receipt.href);assert.equal(popup.loaded.length,1);
});

test('expired closed cancelled or replaced login bindings reject receipts before navigation',async t=>{
 for(const action of ['expire','close-main','close-popup','generation','settings','auth-file','forget']){
  const {c,main,popup,receipt}=await browserAuthorization(t);
  if(action==='expire')c.pendingAuthorization.expiresAt=Date.now()-1;
  else if(action==='close-main')main.destroy();else if(action==='close-popup')popup.destroy();else if(action==='generation')c.authGeneration++;
  else if(action==='forget')await c.forget();else {const file=action==='settings'?c.settingsFile:c.authFile;fs.writeFileSync(file+'.replacement','synthetic replacement');fs.renameSync(file+'.replacement',file);}
  await assert.rejects(c.completeLoginReceipt(receipt.href),/结束或过期/);assert.equal(popup.loaded.length,0);assert.equal(c.auth,null);
 }
});

test('system browser shell and callback failures are visible without replaying and embedded login remains embedded',async t=>{
 const failed=await browserAuthorization(t,{failShell:true});assert.match(failed.c.status().reason,/无法打开系统浏览器/);assert.ok(failed.c.loginWindow);assert.equal(failed.opened.length,1);
 let loads=0;failed.popup.loadURL=async()=>{loads++;throw Error('synthetic callback failure');};
 await assert.rejects(failed.c.completeLoginReceipt(failed.receipt.href),/已提交.*尚未确认/);await assert.rejects(failed.c.completeLoginReceipt(failed.receipt.href),/结束或过期/);assert.equal(loads,1);
 const embedded=await browserAuthorization(t,{external:false});assert.equal(embedded.prevented,false);assert.equal(embedded.opened.length,0);assert.equal(embedded.popup.options.show,true);assert.equal(embedded.c.pendingAuthorization,null);assert.equal(embedded.c.status().browserLoginPending,false);
 await assert.rejects(embedded.c.completeLoginReceipt(embedded.receipt.href),/结束或过期/);assert.equal(embedded.popup.loaded.length,0);
});

test('cancelled system browser authorizations cannot run their queued shell launch',async t=>{
 for(const action of ['main','popup','generation','settings']){
  const {opened}=await browserAuthorization(t,{beforeOpen({c,main,popup}){
   if(action==='main')main.destroy();else if(action==='popup')popup.destroy();else if(action==='generation')c.authGeneration++;
   else{fs.writeFileSync(c.settingsFile+'.replacement','replaced');fs.renameSync(c.settingsFile+'.replacement',c.settingsFile);}
  }});assert.equal(opened.length,0,action);
 }
});

test('system browser placeholders stay hidden only until a recognized official page is needed',async t=>{
 const {main,opened,browser}=await browserAuthorization(t);
 const ordinary=main.openHandler({url:'https://accounts.google.com/login'});assert.equal(ordinary.overrideBrowserWindowOptions.show,true);
 const blank=main.openHandler({url:'about:blank'});assert.equal(blank.overrideBrowserWindowOptions.show,false);const contents=blank.createWindow(blank.overrideBrowserWindowOptions);
 const popup=browser.windows.at(-1);
 contents.emit('will-navigate',{preventDefault(){throw Error('official non-authorization navigation should remain available');}},'https://accounts.google.com/login');assert.equal(popup.shown,1);assert.equal(opened.length,1);
});

test('official browser session capture executes the injected script for all providers and restores zai_cn',async t=>{
 for(const provider of ['zai','zai_cn','bigmodel']){
  const c=platformFixture(t,'linux');t.after(()=>c.dispose());
  const session={provider,revision:'fixture-revision',zcodeJwtToken:'fixture-token-'+provider,userInfo:{id:'fixture-user',displayName:'Fixture '+provider}};
  const reads=loginStorage(c,{'oauth:browser_session':JSON.stringify(session)});await c.login(link);const win=c.loginWindow;
  win.webContents.emit('did-finish-load');await tick();await tick();
  assert.equal(c.status().configured,true);assert.equal(c.auth.provider,provider);assert.equal(c.auth.jwt,session.zcodeJwtToken);assert.equal(c.auth.name,session.userInfo.displayName);assert.equal(win.isDestroyed(),true);
  assert.deepEqual(reads,['oauth:browser_session']);const encrypted=fs.readFileSync(c.authFile);assert.ok(encrypted.toString().startsWith('v11sealed:'));
  const restored=new c.constructor({electron:c.electron,profile:c.profile,rendererPath:__filename});t.after(()=>restored.dispose());
  assert.equal(await restored.restore(),true);assert.equal(restored.status().provider,provider);assert.equal(restored.auth.jwt,session.zcodeJwtToken);assert.deepEqual(fs.readFileSync(c.authFile),encrypted);
 }
});

test('legacy browser login remains compatible only when the primary session is absent',async t=>{
 const c=platformFixture(t,'linux');t.after(()=>c.dispose());
 const reads=loginStorage(c,{'oauth:active_provider':'bigmodel',zcodejwttoken:'legacy-fixture-token','oauth:bigmodel:user_info':JSON.stringify({name:'Legacy Fixture'})});
 await c.login(link);c.loginWindow.webContents.emit('did-finish-load');await tick();await tick();
 assert.equal(c.auth.provider,'bigmodel');assert.equal(c.auth.jwt,'legacy-fixture-token');assert.equal(c.auth.name,'Legacy Fixture');assert.ok(fs.existsSync(c.authFile));
 assert.deepEqual(reads,['oauth:browser_session','oauth:active_provider','zcodejwttoken','oauth:bigmodel:user_info']);
});

test('malformed incomplete or invalid primary sessions never resurrect the legacy account',async t=>{
 const complete={provider:'zai_cn',revision:'fixture-revision',zcodeJwtToken:'current-fixture-token',userInfo:{id:'fixture-user'}};
 const records=['{broken','null',JSON.stringify({...complete,revision:''}),JSON.stringify({...complete,userInfo:null}),JSON.stringify({...complete,userInfo:{id:'unknown'}}),JSON.stringify({...complete,zcodeJwtToken:''}),JSON.stringify({...complete,provider:'untrusted'})];
 for(const primary of records){
  const c=platformFixture(t,'linux');t.after(()=>c.dispose());let encrypted=0;const encrypt=c.electron.safeStorage.encryptStringAsync;c.electron.safeStorage.encryptStringAsync=async value=>{encrypted++;return encrypt(value);};
  const reads=loginStorage(c,{'oauth:browser_session':primary,'oauth:active_provider':'zai',zcodejwttoken:'stale-legacy-token','oauth:zai:user_info':JSON.stringify({name:'Old Account'})});
  await c.login(link);c.loginWindow.webContents.emit('did-finish-load');await tick();await tick();
  assert.deepEqual(reads,['oauth:browser_session']);assert.equal(c.auth,null);assert.equal(encrypted,0);assert.equal(fs.existsSync(c.authFile),false);assert.ok(c.loginWindow);
 }
});

test('official login popups share the ephemeral session and enforce the same origin policy at every depth',async t=>{
 const c=platformFixture(t,'linux');t.after(()=>c.dispose());const browser=loginBrowser(c,null);await c.login(link);
 const main=c.loginWindow,authorize='https://accounts.google.com/o/oauth2/v2/auth?client_id=synthetic';
 assert.equal(browser.partitions.length,1);assert.match(browser.partitions[0],/^zcode-official-login-/);assert.ok(!browser.partitions[0].startsWith('persist:'));
 const secure=preferences=>{assert.equal(preferences.session,browser.browserSession);assert.equal(preferences.nodeIntegration,false);assert.equal(preferences.contextIsolation,true);assert.equal(preferences.sandbox,true);};secure(main.options.webPreferences);
 assert.equal(main.openHandler({url:authorize}).action,'allow');
 assert.equal(main.openHandler({url:'https://zchat.z.ai/api/oauth/authorize?state=synthetic'}).action,'allow');
 const allowed=main.openHandler({url:'about:blank'});assert.equal(allowed.action,'allow');assert.equal(allowed.outlivesOpener,false);secure(allowed.overrideBrowserWindowOptions.webPreferences);
 const childContents=allowed.createWindow({webPreferences:allowed.overrideBrowserWindowOptions.webPreferences}),child=browser.windows.at(-1);assert.equal(childContents,child.webContents);assert.equal(child.options.webPreferences.session,browser.browserSession);
 const nested=child.openHandler({url:'https://github.com/login/oauth/authorize'});assert.equal(nested.action,'allow');nested.createWindow({webPreferences:nested.overrideBrowserWindowOptions.webPreferences});const grandchild=browser.windows.at(-1);
 const denied=['https://evil.invalid/authorize','https://accounts.google.com.evil.invalid/','https://user@accounts.google.com/','https://user:pass@zcode.z.ai/','http://zcode.z.ai/','file:///tmp/login','javascript:alert(1)','blob:https://zcode.z.ai/fixture','blob:https://accounts.google.com/fixture','about:blank#untrusted','about:blank?untrusted'];
 for(const win of [main,child,grandchild]){
  for(const url of denied){
   assert.equal(win.openHandler({url}).action,'deny',url);
   for(const event of ['will-navigate','will-redirect']){let prevented=false;win.webContents.emit(event,{preventDefault(){prevented=true;}},url);assert.equal(prevented,true,event+' '+url);}
  }
  for(const event of ['will-navigate','will-redirect']){let prevented=false;win.webContents.emit(event,{preventDefault(){prevented=true;}},authorize);assert.equal(prevented,false);}
 }
 main.destroy();assert.equal(child.isDestroyed(),true);assert.equal(grandchild.isDestroyed(),true);assert.equal(c.loginWindow,null);
 for(const win of [main,child,grandchild])assert.equal(win.openHandler({url:authorize}).action,'deny');
});

test('unconfigured startup, status, saving a link and disposal never touch safeStorage',async t=>{
 let served=0;const safe=new Proxy({}, {get(){throw Error('unconfigured safeStorage probe');}});
 const c=platformFixture(t,'linux',safe,async()=>{served++;return ()=>{};});
 assert.equal(c.status().secureStorageAvailable,null);assert.equal(await c.start(),true);assert.equal(served,1);
 for(let index=0;index<5;index++)assert.equal(c.status().configured,false);
 c.save(link);await c.disconnect();assert.equal(c.status().secureStorageAvailable,null);c.dispose();
});
test('pending keyring cannot hold private service startup and timeout cannot restore late credentials',async t=>{
 const check=deferred();let decrypts=0;
 const c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',isAsyncEncryptionAvailable:()=>check.promise,
  decryptStringAsync:async()=>{decrypts++;return {result:JSON.stringify(auth)};}}));const auth=saved(c);c.storageTimeoutMs=8;
 assert.equal(await c.start(),true);assert.equal(c.status().available,true);assert.equal(c.status().restoring,true);assert.equal(c.status().secureStoragePending,true);
 const pending=c.restorePending;await pending;assert.equal(c.status().configured,false);assert.match(c.status().reason,/安全存储尚未就绪/);
 check.resolve(true);await tick();assert.equal(c.auth,null);assert.equal(decrypts,0);assert.ok(fs.existsSync(c.authFile));c.dispose();
});
test('availability checks share one pending native call and explicit retries recover after failure',async t=>{
 const check=deferred();let calls=0;
 const safe=storage({getSelectedStorageBackend:()=> 'gnome_libsecret',isAsyncEncryptionAvailable:()=>{calls++;return check.promise;}});
 const c=platformFixture(t,'linux',safe),one=c.secure(),two=c.secure();await tick();assert.equal(calls,1);
 check.resolve(false);assert.deepEqual(await Promise.all([one,two]),[false,false]);
 safe.isAsyncEncryptionAvailable=async()=>{calls++;return true;};assert.equal(await c.secure(),true);assert.equal(calls,2);assert.equal(c.status().secureStorageAvailable,true);
});
test('forget and changed encrypted file identities reject late restore results',async t=>{
 for(const action of ['forget','replace']){
  const decrypt=deferred(),c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',decryptStringAsync:()=>decrypt.promise}));const auth=saved(c);
  const pending=c.restore();await tick();
  if(action==='forget')await c.forget();else{fs.writeFileSync(c.authFile+'.new','v11replacement');fs.renameSync(c.authFile+'.new',c.authFile);}
  decrypt.resolve({result:JSON.stringify(auth),shouldReEncrypt:true});assert.equal(await pending,false);assert.equal(c.auth,null);
  if(action==='forget')assert.equal(fs.existsSync(c.authFile),false);else assert.equal(fs.readFileSync(c.authFile,'utf8'),'v11replacement');
  c.dispose();
 }
});
test('connect waits for restoration and an explicit link change cancels the old pending target',async t=>{
 const decrypt=deferred(),c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',decryptStringAsync:()=>decrypt.promise}));const auth=saved(c);
 const restoring=c.restore();await tick();let complete=false;const connecting=c.connect().finally(()=>{complete=true;});
 await tick();assert.equal(complete,false);c.save(link.replace('native-session','other-session'));
 const rejected=assert.rejects(connecting,/已取消/);decrypt.resolve({result:JSON.stringify(auth)});await restoring;await rejected;
 assert.equal(c.auth,null);assert.match(c.targetLink(),/other-session/);c.dispose();
});
test('Linux rejects weak ciphertext on both restore and login without modifying saved credentials',async t=>{
 let decrypts=0;const c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',
  decryptStringAsync:async()=>{decrypts++;return {result:'{}'};},encryptStringAsync:async()=>Buffer.from('v10weak-ciphertext')}));
 saved(c,'v10');assert.equal(await c.restore(),false);assert.equal(decrypts,0);assert.equal(fs.readFileSync(c.authFile,'utf8'),'v10fixture-ciphertext');
 await c.forget();loginBrowser(c,{jwt:'fixture-token',provider:'zai',name:'Fixture'});await c.login(link);
 c.loginWindow.webContents.emit('did-finish-load');await tick();await tick();assert.equal(fs.existsSync(c.authFile),false);assert.equal(c.auth,null);c.dispose();
});
test('closing login or replacing settings prevents late encrypted capture from writing auth',async t=>{
 for(const action of ['close','settings','forget']){
  const encrypt=deferred(),c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',encryptStringAsync:()=>encrypt.promise}));
  loginBrowser(c,{jwt:'fixture-token',provider:'zai',name:'Fixture'});await c.login(link);const win=c.loginWindow;
  win.webContents.emit('did-finish-load');await tick();
  if(action==='close')win.destroy();else if(action==='forget')await c.forget();else{fs.writeFileSync(c.settingsFile+'.new',JSON.stringify({link:link.replace('native-session','replacement')}));fs.renameSync(c.settingsFile+'.new',c.settingsFile);}
  encrypt.resolve(Buffer.from('v11encrypted-fixture'));await tick();await tick();assert.equal(fs.existsSync(c.authFile),false);assert.equal(c.auth,null);c.dispose();
 }
});
test('Windows async restore preserves existing synchronous-format encrypted credentials',async t=>{
 for(const platform of ['win32']){
  const c=platformFixture(t,platform,storage()),auth=saved(c,'v10');const original=fs.readFileSync(c.authFile);
  c.electron.safeStorage.decryptStringAsync=async value=>{assert.deepEqual(value,original);return {result:JSON.stringify(auth),shouldReEncrypt:true};};
  assert.equal(await c.restore(),true);assert.equal(c.status().configured,true);assert.deepEqual(fs.readFileSync(c.authFile),original);c.dispose();
 }
});
test('an existing login window can explicitly retry unavailable storage without invalidating capture',async t=>{
 let available=true;const c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',isAsyncEncryptionAvailable:async()=>available,encryptStringAsync:async()=>Buffer.from('v11encrypted-fixture')}));
 loginBrowser(c,{jwt:'fixture-token',provider:'zai',name:'Fixture'});await c.login(link);const win=c.loginWindow;
 available=false;await assert.rejects(c.login(link),/安全存储不可用/);assert.equal(c.loginWindow,win);
 available=true;await c.login(link);assert.equal(c.loginWindow,win);win.webContents.emit('did-finish-load');await tick();await tick();
 assert.equal(c.status().configured,true);assert.equal(fs.readFileSync(c.authFile,'utf8'),'v11encrypted-fixture');c.dispose();
});
test('encryption timeout reports promptly and its late result cannot write credentials',async t=>{
 const encrypt=deferred();let calls=0;const c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',encryptStringAsync:()=>{calls++;return encrypt.promise;}}));c.storageTimeoutMs=5;
 loginBrowser(c,{jwt:'fixture-token',provider:'zai',name:'Fixture'});await c.login(link);const win=c.loginWindow;
 win.webContents.emit('did-finish-load');await new Promise(resolve=>setTimeout(resolve,12));assert.match(c.status().reason,/安全存储尚未就绪/);
 win.webContents.emit('did-finish-load');await tick();assert.equal(calls,1);assert.equal(fs.existsSync(c.authFile),false);
 encrypt.resolve(Buffer.from('v11late-ciphertext'));await tick();await tick();assert.equal(fs.existsSync(c.authFile),false);assert.equal(c.auth,null);c.dispose();
});
test('explicit login retries saved credentials first but an authenticated user can still open account login',async t=>{
 let decrypts=0;const c=platformFixture(t,'linux',storage({getSelectedStorageBackend:()=> 'gnome_libsecret',decryptStringAsync:async()=>{decrypts++;return {result:JSON.stringify(auth)};}}));const auth=saved(c),original=fs.readFileSync(c.authFile);
 c.secureStorageAvailable=false;loginBrowser(c,{jwt:'new-fixture-token',provider:'zai',name:'Other'});
 assert.equal((await c.login(link)).configured,true);assert.equal(c.loginWindow,null);assert.equal(decrypts,1);assert.deepEqual(fs.readFileSync(c.authFile),original);
 await c.login(link);assert.ok(c.loginWindow);assert.equal(decrypts,1);assert.deepEqual(fs.readFileSync(c.authFile),original);c.dispose();
});

test('closing the controller during optional IPC startup closes its late listener without reading storage',async t=>{
 const server=deferred();let closed=0;
 const safe=new Proxy({}, {get(){throw Error('storage after disposal');}});
 const c=platformFixture(t,'linux',safe,()=>server.promise);saved(c);
 const starting=c.start();c.dispose();server.resolve(()=>{closed++;});
 assert.equal(await starting,false);assert.equal(closed,1);assert.equal(c.status().available,false);
 assert.equal(await c.restore(),false);await assert.rejects(c.login(link),/服务已关闭/);
});

test('Mac restores profile credentials after rebuild without probing Electron safeStorage',{skip:process.platform==='win32'},async t=>{
 const {PrivateCredentials}=require('../desktop/shared/private-credentials.cjs');
 const safe=new Proxy({}, {get(){throw Error('unexpected keychain access');}}),c=platformFixture(t,'darwin',safe);
 c.save(link);const local=new PrivateCredentials(c.profile,'zcode-official');
 const encrypted=await local.encryptStringAsync(JSON.stringify({jwt:'local-fixture-secret',provider:'zai',name:'Fixture',link}));
 fs.writeFileSync(c.authFile,encrypted,{mode:0o600});const original=fs.readFileSync(path.join(c.profile,'.credential-vault/key'));
 assert.equal(await c.restore(),true);assert.equal(c.status().configured,true);
 const rebuilt=platformFixture(t,'darwin',safe);rebuilt.profile=c.profile;rebuilt.authFile=c.authFile;rebuilt.settingsFile=c.settingsFile;rebuilt.localStorage=new PrivateCredentials(c.profile,'zcode-official');
 assert.equal(await rebuilt.restore(),true);assert.equal(rebuilt.auth.jwt,'local-fixture-secret');
 assert.deepEqual(fs.readFileSync(path.join(c.profile,'.credential-vault/key')),original);
 assert.equal(JSON.stringify(rebuilt.status()).includes('local-fixture-secret'),false);
});
test('Mac old login is never decrypted until explicit migration and retains an encrypted rollback copy',{skip:process.platform==='win32'},async t=>{
 let decrypts=0;const auth={jwt:'legacy-fixture-secret',provider:'zai',name:'Fixture',link};
 const c=platformFixture(t,'darwin',storage({decryptStringAsync:async()=>{decrypts++;return {result:JSON.stringify(auth)};}}));
 const original=Buffer.from('v10legacy-fixture');fs.writeFileSync(c.authFile,original,{mode:0o600});
 assert.equal(await c.restore(),false);assert.equal(decrypts,0);assert.equal(c.status().legacyLoginSaved,true);
 await c.migrateLogin();assert.equal(decrypts,1);assert.equal(c.auth.jwt,auth.jwt);assert.deepEqual(fs.readFileSync(c.legacyAuthFile),original);
 assert.equal(fs.readFileSync(c.authFile).subarray(0,4).toString(),'CMB1');await c.restore();assert.equal(decrypts,1);
 await c.forget();assert.equal(fs.existsSync(c.authFile),false);assert.equal(fs.existsSync(c.legacyAuthFile),false);
});

test('Mac migration preserves an externally replaced key and the original login ciphertext',{skip:process.platform==='win32'},async t=>{
 const auth={jwt:'legacy-fixture',provider:'zai',link},c=platformFixture(t,'darwin',storage({decryptStringAsync:async()=>({result:JSON.stringify(auth)})}));
 const original=Buffer.from('v10legacy-fixture');fs.writeFileSync(c.authFile,original,{mode:0o600});
 const encrypt=c.localStorage.encryptStringAsync.bind(c.localStorage),foreign=Buffer.alloc(32,42);
 c.localStorage.encryptStringAsync=async value=>{const encrypted=await encrypt(value);fs.writeFileSync(path.join(c.profile,'.credential-vault/key'),foreign);return encrypted;};
 await assert.rejects(c.migrateLogin());assert.deepEqual(fs.readFileSync(c.authFile),original);assert.equal(c.auth,null);
 assert.deepEqual(fs.readFileSync(path.join(c.profile,'.credential-vault/key')),foreign);
});
