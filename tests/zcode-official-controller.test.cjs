'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {OfficialRemote}=require('../desktop/features/zcode-official/controller.cjs');
const {serveLocal}=require('../desktop/shared/local-service.cjs');
const root=path.resolve(__dirname,'../.tmp');
function profile(t){const p=fs.mkdtempSync(path.join(root,'official-controller-'));t.after(()=>fs.rmSync(p,{recursive:true,force:true}));return p;}
const link='https://zcode.z.ai/remote/v4?sid=native-session&mid=this-machine';
function fixture(t){const p=profile(t),home=path.join(p,'native');fs.mkdirSync(home);fs.mkdirSync(path.join(p,'desktop-sessions'));
fs.writeFileSync(path.join(home,'telemetry-state.json'),JSON.stringify({deviceMid:'this-machine'}));
fs.writeFileSync(path.join(p,'desktop-sessions/settings.json'),JSON.stringify({discovered:{zcode:{dataDirectory:home}}}));
return new OfficialRemote({electron:{safeStorage:{isEncryptionAvailable:()=>true}},profile:p,rendererPath:__filename});}
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
test('saved link and encrypted login survive controller restart and a local-mode selection',t=>{
 const c=fixture(t);c.electron.safeStorage={isEncryptionAvailable:()=>true,decryptString:data=>data.toString().slice(7)};
 c.save(link);assert.equal(c.status().configured,false);
 fs.writeFileSync(c.authFile,'sealed:'+JSON.stringify({jwt:'private-secret',provider:'zai',name:'Fixture',link}),{mode:0o600});
 const restored=new OfficialRemote({electron:c.electron,profile:c.profile,rendererPath:__filename});restored.restore();
 assert.equal(restored.status().configured,true);assert.equal(restored.status().connected,false);assert.equal(restored.status().selected,false);
 assert.equal(restored.status().link,link);assert.equal(restored.status().accountName,'Fixture');
 assert.equal(fs.statSync(c.settingsFile).mode&0o777,0o600);assert.equal(JSON.stringify(restored.status()).includes('private-secret'),false);
 const config=JSON.parse(fs.readFileSync(path.join(c.profile,'desktop-sessions/settings.json')));config.zcodeConnectionMode='official-remote';
 fs.writeFileSync(path.join(c.profile,'desktop-sessions/settings.json'),JSON.stringify(config));assert.equal(restored.status().selected,true);
});
test('saving configuration cannot silently move a live connection to a different target',t=>{
 const c=fixture(t);c.save(link);c.runtime={};
 assert.throws(()=>c.save(link.replace('native-session','another-session')),/先断开/);
 assert.equal(c.status().link,link);assert.throws(()=>c.save(link.replace('this-machine','another-machine')),/本机/);
});
test('legacy encrypted login supplies the target and forget retains the saved link',async t=>{
 const c=fixture(t);c.electron.safeStorage={isEncryptionAvailable:()=>true,decryptString:data=>data.toString()};
 fs.writeFileSync(c.authFile,JSON.stringify({jwt:'fixture-only',provider:'zai',link}));c.restore();assert.equal(c.targetLink(),link);
 await c.forget();assert.equal(c.status().configured,false);assert.equal(c.status().link,link);
 const restored=new OfficialRemote({electron:c.electron,profile:c.profile,rendererPath:__filename});restored.restore();
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
test('private IPC authenticates before dispatch and redacts internal errors',async t=>{
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
 const scope=vm.createContext({require:name=>name==='../../shared/local-service.cjs'?{serveLocal}:nativeRequire(name),module:{exports:{}},process:{platform},Buffer,setTimeout,clearTimeout,setInterval,clearInterval});
 vm.runInContext(fs.readFileSync(filename,'utf8'),scope);return scope.module.exports.OfficialRemote;
}
test('optional IPC startup failure remains isolated and never advertises a usable connection',async t=>{
 const Controller=platformController('win32',async()=>{throw Error('private-path-or-token');}),p=profile(t),c=new Controller({electron:{safeStorage:{isEncryptionAvailable:()=>true}},profile:p,rendererPath:__filename,localServiceWorker:{executable:'gateway.exe'}});
 assert.equal(await c.start(),false);assert.equal(c.status().available,false);assert.equal(c.status().connected,false);assert.match(c.status().reason,/其他网关功能不受影响/);assert.equal(JSON.stringify(c.status()).includes('private-path-or-token'),false);
});
test('Linux credentials require a real keyring and never permit plaintext or pre-ready storage',async t=>{
 const Controller=platformController('linux',async()=>()=>{}),p=profile(t);
 for(const backend of ['basic_text','unknown','gnome_libsecret','kwallet','kwallet5','kwallet6']){
  const c=new Controller({electron:{safeStorage:{isEncryptionAvailable:()=>true,getSelectedStorageBackend:()=>backend}},profile:p,rendererPath:__filename});
  assert.equal(c.secure(),!['basic_text','unknown'].includes(backend));assert.equal(await c.start(),true);
  assert.equal(c.status().available,true);assert.equal(c.status().secureStorageAvailable,c.secure());
  if(!c.secure())assert.match(c.status().reason,/系统安全存储不可用/);c.dispose();
 }
});
