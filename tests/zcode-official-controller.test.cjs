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
return new OfficialRemote({electron:{safeStorage:storage()},profile:p,rendererPath:__filename});}
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
 const restored=new OfficialRemote({electron:c.electron,profile:c.profile,rendererPath:__filename});await restored.restore();
 assert.equal(restored.status().configured,true);assert.equal(restored.status().connected,false);assert.equal(restored.status().selected,false);
 assert.equal(restored.status().link,link);assert.equal(restored.status().accountName,'Fixture');
 if(process.platform!=='win32')assert.equal(fs.statSync(c.settingsFile).mode&0o777,0o600);assert.equal(JSON.stringify(restored.status()).includes('private-secret'),false);
 const config=JSON.parse(fs.readFileSync(path.join(c.profile,'desktop-sessions/settings.json')));config.zcodeConnectionMode='official-remote';
 fs.writeFileSync(path.join(c.profile,'desktop-sessions/settings.json'),JSON.stringify(config));assert.equal(restored.status().selected,true);
});
test('standby pairing waits for explicit access before reading the keychain and shares one private listener',async t=>{
 const c=platformFixture(t,process.platform);t.after(()=>c.dispose());c.save(link);
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
 const restored=new OfficialRemote({electron:c.electron,profile:c.profile,rendererPath:__filename});await restored.restore();
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
 class Window extends EventEmitter{
  constructor(){super();this.destroyed=false;this.webContents=new EventEmitter();this.webContents.getURL=()=>link;this.webContents.setWindowOpenHandler=()=>{};this.webContents.executeJavaScript=async()=>data;}
  isDestroyed(){return this.destroyed;}show(){}focus(){}async loadURL(){}destroy(){if(!this.destroyed){this.destroyed=true;this.emit('closed');}}
 }
 c.electron.BrowserWindow=Window;c.electron.session={fromPartition:()=>({setPermissionRequestHandler(){},setPermissionCheckHandler(){}})};
}

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
test('Mac and Windows async restore preserve existing synchronous-format encrypted credentials',async t=>{
 for(const platform of ['darwin','win32']){
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
