'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const {stripTypeScriptTypes}=require('node:module');
function fixture(){
 const assets=new Map(),installed=[],cookieReads=[];let cookie='',proof={authenticated:true,cookieName:'codex_mobile_session'},requests=0;
 const context=vm.createContext({Uint8Array,Map,url:{URL:{parseURL:value=>new URL(value)}},
  util:{TextEncoder:class{encodeInto(value){return Buffer.from(value);}},TextDecoder:{create:()=>({decodeToString:bytes=>Buffer.from(bytes).toString()})}},
  cryptoFramework:{createMd(){const hash=crypto.createHash('sha256');return {update:async({data})=>hash.update(data),digest:async()=>({data:hash.digest()})};}},
  asset:{Tag:{ALIAS:1,SECRET:2,ACCESSIBILITY:3,CONFLICT_RESOLUTION:4,RETURN_TYPE:5},Accessibility:{DEVICE_UNLOCKED:2},ConflictResolution:{OVERWRITE:0},ReturnType:{ALL:0},
   add:async value=>assets.set(Buffer.from(value.get(1)).toString(),new Map(value)),query:async query=>{const row=assets.get(Buffer.from(query.get(1)).toString());return row?[row]:[];},remove:async query=>assets.delete(Buffer.from(query.get(1)).toString())},
  webview:{WebCookieManager:{fetchCookieSync:(...args)=>{cookieReads.push(args);return cookie;},configCookieSync:(...args)=>installed.push(args),saveCookieAsync:async()=>{}}},
  GatewayHttp:class{async json(){requests++;if(proof instanceof Error)throw proof;return proof;}}
 });
 for(const name of ['GatewayUrl','Digest','DeviceBinding']){
  const text=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/'+name+'.ets','utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export class','class');
  vm.runInContext(stripTypeScriptTypes(text)+'\nglobalThis.'+name+'='+name+';',context);
 }
 return {Binding:context.DeviceBinding,assets,installed,cookieReads,proof:value=>proof=value,cookie:value=>cookie=value,requests:()=>requests};
}
const base='https://gateway.example/d/'+'a'.repeat(32),token='x'.repeat(43),cookie='codex_mobile_session='+token;
test('Harmony binding tokens stay in unlocked-device assets and restore only after same-gateway verification',async()=>{
 const f=fixture();await f.Binding.capture(base,cookie,'codex_mobile_session');assert.equal(f.assets.size,1);
 const stored=[...f.assets.values()][0];assert.equal(stored.get(3),2);
 assert.equal(await f.Binding.restore(base,()=>true),true);assert.equal(f.requests(),1);assert.equal(f.installed.length,1);
 assert.equal(f.installed[0][0],base+'/');assert.match(f.installed[0][1],/HttpOnly; SameSite=Strict; Secure/);assert(f.installed[0][1].includes('Path=/d/'+'a'.repeat(32)));
 assert(f.cookieReads.every(args=>args[1]===false&&args[2]===true));
 assert.equal(await f.Binding.restore('https://other.example',()=>true),false);
 f.cookie(cookie);assert.equal(await f.Binding.restore(base,()=>true),false);assert.equal(f.requests(),1);
});
test('Harmony network failure retains a binding while authoritative rejection clears only that route',async()=>{
 const f=fixture();await f.Binding.capture(base,cookie,'codex_mobile_session');f.proof(Error('network unavailable'));
 assert.equal(await f.Binding.restore(base,()=>true),false);assert.equal(f.assets.size,1);
 f.proof({authenticated:false});assert.equal(await f.Binding.restore(base,()=>true),false);assert.equal(f.assets.size,0);assert.equal(f.installed.length,0);
});
test('Harmony rejects malformed cookie copies and a stale restoration operation',async()=>{
 const f=fixture();for(const input of ['other=abc',cookie+'; '+cookie,'codex_mobile_session=short'])assert.equal(f.Binding.credential(input,'codex_mobile_session'),null);
 await f.Binding.capture(base,cookie,'codex_mobile_session');assert.equal(await f.Binding.restore(base,()=>false),false);assert.equal(f.installed.length,0);
});
