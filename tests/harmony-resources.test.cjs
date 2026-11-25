'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process'),{stripTypeScriptTypes}=require('node:module');
test('Harmony prepares the shared phone scripts and serves the complete offline shell from an exact allowlist',()=>{
 fs.mkdirSync('.tmp',{recursive:true});const temporary=fs.mkdtempSync('.tmp/harmony-resource-test-');
 try{
  execFileSync('python3',['-B','scripts/prepare-harmony.py','--destination',temporary],{env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:'pipe'});
  for(const name of ['channel-link.js','mobile-ui.js','mobile-clipboard.js','mobile-system-bars.js','mobile-offline.js'])
   assert.deepEqual(fs.readFileSync(path.join(temporary,name)),fs.readFileSync('mobile/shared/web/'+name));
  assert.equal(fs.existsSync(path.join(temporary,'mobile-session.js')),false);
  const manifest=JSON.parse(fs.readFileSync(path.join(temporary,'offline-shell/manifest.json')));
  assert(Object.keys(manifest).length>100);for(const value of Object.values(manifest))assert(fs.statSync(path.join(temporary,'offline-shell',value)).size>0);
  const resources={resourceManager:{getRawFileContentSync:key=>new Uint8Array(fs.readFileSync(path.join(temporary,key.replace(/^generated\//,''))))}};
  const context=vm.createContext({Uint8Array,util:{TextDecoder:{create:()=>({decodeToString:bytes=>new TextDecoder().decode(bytes)})}},WebResourceResponse:class{
   setResponseCode(value){this.status=value;}setReasonMessage(){}setResponseEncoding(){}setResponseMimeType(value){this.mime=value;}
   setResponseHeader(value){this.headers=value;}setResponseData(value){this.bytes=value;}
  }});
  for(const name of ['OfflineShell','WebScriptLoader']){
   const source=fs.readFileSync('mobile/harmony/entry/src/main/ets/services/'+name+'.ets','utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export class','class');
   vm.runInContext(stripTypeScriptTypes(source)+';globalThis.'+name+'='+name+';',context);
  }
  const shell=new context.OfflineShell(resources),origin=context.OfflineShell.ORIGIN;
  const response=shell.response(origin+'/offline-reader.html','GET');assert.equal(response.status,200);assert.equal(response.mime,'text/html');assert(response.bytes.byteLength>100);
  assert.match(response.headers.find(row=>row.headerKey==='Content-Security-Policy').headerValue,/connect-src 'none'/);
  for(const value of ['https://other.example/offline-reader.html',origin+'/missing.js',origin+'/../manifest.json',origin+'/%2e%2e/manifest.json',origin+'/offline-reader.html?url=remote'])
   assert.equal(shell.response(value,'GET').status,403);
  assert.equal(shell.response(origin+'/offline-reader.html','POST').status,403);
  const online=context.WebScriptLoader.load(resources,'private-token','en','https://gateway.example'),offline=context.WebScriptLoader.load(resources,'private-token','en',origin,false);
  assert.match(online,/window\.CodexBridgeNativeOffline/);assert.doesNotMatch(offline,/window\.CodexBridgeNativeOffline/);
  for(const script of [online,offline]){assert.doesNotMatch(script,/__BRIDGE_CLIPBOARD_TOKEN__|__BRIDGE_NATIVE_LANGUAGE__/);assert.match(script,/window!==window\.top/);new vm.Script(script);}
 }finally{fs.rmSync(temporary,{recursive:true,force:true});}
});
