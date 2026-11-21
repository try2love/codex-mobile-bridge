'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {readFileSync}=require('node:fs'),{stripTypeScriptTypes}=require('node:module'),vm=require('node:vm'),path=require('node:path');
// Execute the production URL rules. This is not an ArkTS or device build.
const source=readFileSync(path.join(__dirname,'../mobile/harmony/entry/src/main/ets/services/GatewayUrl.ets'),'utf8');
const context=vm.createContext({url:{URL:{parseURL:value=>new URL(value)}}});
vm.runInContext(stripTypeScriptTypes(source.replace(/^import .*;\n/,'').replace('export class','class'))+'\nglobalThis.GatewayUrl=GatewayUrl;',context);
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
