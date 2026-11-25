'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {readFileSync}=require('node:fs'),{stripTypeScriptTypes}=require('node:module'),vm=require('node:vm'),path=require('node:path');
// Execute the production URL rules. This is not an ArkTS or device build.
const source=readFileSync(path.join(__dirname,'../mobile/harmony/entry/src/main/ets/services/GatewayUrl.ets'),'utf8');
const context=vm.createContext({url:{URL:{parseURL:value=>new URL(value)}}});
vm.runInContext(stripTypeScriptTypes(source.replace(/^import .*;\r?\n/,'').replace('export class','class'))+'\nglobalThis.GatewayUrl=GatewayUrl;',context);
const GatewayUrl=context.GatewayUrl;
test('Harmony accepts canonical gateway and one-time pairing entries',()=>{
  const prefix='/d/'+'a'.repeat(32),pair='#pair='+'b'.repeat(43);
  for(const base of ['https://gateway.example','http://192.168.1.2:8787','http://[::1]:8787','https://gateway.example'+prefix]){
    assert.equal(GatewayUrl.connection(' '+base+'/'+pair+' '),base+'/'+pair);
    assert.equal(GatewayUrl.origin(base+'/'),base);
  }
});
test('Harmony rejects public cleartext, userinfo, encoded and non-gateway paths',()=>{
  for(const value of ['http://example.com','http://192.0.2.1','https://name:secret@host','https://host/api/auth',
    'https://host/?key=secret','https://host/#pair=short','https://host/a/../','https://host/%2e/',
    'https://host/\\evil','https://ho\nst/','file:///data/private','https://host/d/'+'a'.repeat(31)]){
    assert.throws(()=>GatewayUrl.connection(value),value);
  }
});
test('Harmony navigation remains inside the selected origin and device prefix',()=>{
  const base='https://gateway.example/d/'+'a'.repeat(32);
  assert.equal(GatewayUrl.sameOrigin(base+'/api/auth?read=1',base),true);
  assert.equal(GatewayUrl.sameOrigin(base+'/#chat=fixture',base),true);
  for(const target of ['https://other.example/', 'https://gateway.example/api/auth',base+'other/',
    base+'/../api/auth',base+'/%2e%2e/api/auth',base+'/%2fapi/auth','javascript:alert(1)']){
    assert.equal(GatewayUrl.sameOrigin(target,base),false,target);
  }
});

const storeSource=readFileSync(path.join(__dirname,'../mobile/harmony/entry/src/main/ets/services/ComputerStore.ets'),'utf8');
function computerStore(initial='[]'){
 let value=initial,flushes=0,writes=0;
 const prefs={getSync:()=>value,putSync:(_key,next)=>{value=next;writes++;},flush:async()=>{flushes++;}};
 context.preferences={getPreferences:async()=>prefs};
 const isolated=vm.createContext({GatewayUrl,preferences:context.preferences});
 vm.runInContext(stripTypeScriptTypes(storeSource.replace(/^import .*;\r?\n/gm,'').replace('export class','class'))+'\nglobalThis.ComputerStore=ComputerStore;',isolated);
 return {store:new isolated.ComputerStore(),state:()=>({value,writes,flushes})};
}
test('Harmony address storage filters malformed entries and canonicalizes saved addresses',async()=>{
 const f=computerStore(JSON.stringify(['https://gateway.example/','https://gateway.example',42,'http://public.example','https://gateway.example/#pair=secret']));
 assert.deepEqual(Array.from(f.store.list()),[]);
 await f.store.init({});assert.deepEqual(Array.from(f.store.list()),['https://gateway.example']);
 await f.store.save(['http://192.168.1.2:8788/']);assert.deepEqual(JSON.parse(f.state().value),['http://192.168.1.2:8788']);assert.equal(f.state().flushes,1);
});
test('Harmony pairing tokens and invalid addresses never overwrite saved addresses',async()=>{
 const f=computerStore('["https://saved.example"]');await assert.rejects(f.store.save(['https://gateway.example']));
 await f.store.init({});
 for(const entry of ['https://gateway.example/#pair='+'a'.repeat(43),'http://public.example','https://gateway.example/api/auth'])await assert.rejects(f.store.save([entry]));
 assert.equal(f.state().writes,0);assert.deepEqual(Array.from(f.store.list()),['https://saved.example']);
});
test('Harmony project entry points and referenced resources exist without private signing material',()=>{
 const root=path.join(__dirname,'../mobile/harmony'),{existsSync,readdirSync}=require('node:fs');
 const app=JSON.parse(readFileSync(path.join(root,'AppScope/app.json5'),'utf8'));
 const module=JSON.parse(readFileSync(path.join(root,'entry/src/main/module.json5'),'utf8')).module;
 const profile=JSON.parse(readFileSync(path.join(root,'build-profile.json5'),'utf8'));
 assert.equal(app.app.bundleName,'io.github.try2love.codexbridge.harmony.experimental');assert.equal(profile.app.signingConfigs.length,0);
 const base=path.join(root,'entry/src/main');
 for(const ability of module.abilities)assert(existsSync(path.join(base,ability.srcEntry)));
 const pages=JSON.parse(readFileSync(path.join(base,'resources/base/profile/main_pages.json'),'utf8'));
 for(const entry of pages.src)assert(existsSync(path.join(base,'ets',entry+'.ets')));
 const resourceRoots=[path.join(base,'resources/base'),path.join(root,'AppScope/resources/base')];
 for(const ref of JSON.stringify({app,module}).matchAll(/\$(media|string|color|profile):([A-Za-z0-9_]+)/g)){
  const [_,kind,name]=ref;
  assert(resourceRoots.some(resource=>{
   if(kind==='media'||kind==='profile')return existsSync(path.join(resource,kind))&&readdirSync(path.join(resource,kind)).some(file=>file.slice(0,file.lastIndexOf('.'))===name);
   const file=path.join(resource,'element',kind+'.json');return existsSync(file)&&JSON.parse(readFileSync(file,'utf8'))[kind].some(row=>row.name===name);
  }),ref[0]);
 }
});
