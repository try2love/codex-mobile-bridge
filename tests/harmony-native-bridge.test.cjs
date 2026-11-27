'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {stripTypeScriptTypes}=require('node:module');
const root='mobile/harmony/entry/src/main/ets/services/';
function load(name,context){
 const source=fs.readFileSync(root+name+'.ets','utf8').replace(/^import .*;\r?\n/gm,'').replaceAll('export class','class');
 vm.runInContext(stripTypeScriptTypes(source)+'\nglobalThis.'+name+'='+name+';',context);
 return context[name];
}
function fixture(){
 const copied=[],saved=new Map(),scripts=[],base='https://gateway.example/d/'+'a'.repeat(32);let current=base+'/';
 const context=vm.createContext({url:{URL:{parseURL:value=>new URL(value)}},pasteboard:{MIMETYPE_TEXT_PLAIN:'text/plain',createData:(_type,value)=>value,getSystemPasteboard:()=>({setDataSync:value=>copied.push(value)})}});
 load('GatewayUrl',context);const Guard=load('DocumentGuard',context),guard=new Guard();guard.navigate(base);
 const Bridge=load('NativeBridge',context),prefs={read:key=>saved.get(key)||'',save:async(key,value)=>saved.set(key,value)};
 const bridge=new Bridge({getUrl:()=>current,runJavaScript:async value=>scripts.push(value)},guard,prefs,'fixture-secret');
 return {bridge,guard,base,copied,saved,scripts,current:value=>current=value};
}
test('Harmony native copy requires current origin, private token and one real touch',()=>{
 const f=fixture(),copy=(url=f.base,token='fixture-secret')=>f.bridge.prompt(url,'codexbridge-copy:'+token,'private text');
 assert.equal(copy(),null);f.guard.touch();assert.equal(copy('https://other.example'),null);
 assert.equal(copy(f.base,'wrong'),null);assert.equal(copy(),'copied');assert.deepEqual(f.copied,['private text']);assert.equal(copy(),null);
 f.guard.touch();f.current('https://other.example');assert.equal(copy(),null);
 f.current(f.base+'/');f.guard.foreground=false;assert.equal(copy(),null);
});
test('Harmony native preferences use shared display replies and discard old document results',async()=>{
 const f=fixture(),command={id:'1',op:'write',key:'appearance',value:{theme:'dark',fontSize:18}};
 assert.equal(f.bridge.prompt(f.base,'codexbridge-display:fixture-secret',JSON.stringify(command)),'queued');
 await new Promise(setImmediate);assert.deepEqual(JSON.parse(f.saved.get('appearance')),command.value);assert.match(f.scripts[0],/BridgeMobileStorage/);
 f.scripts.length=0;
 assert.equal(f.bridge.prompt(f.base,'codexbridge-display:fixture-secret',JSON.stringify({...command,id:'2'})),'queued');
 f.guard.navigate('https://another.example');await new Promise(setImmediate);assert.equal(f.scripts.length,0);
 assert.equal(f.bridge.prompt('https://another.example','codexbridge-display:fixture-secret',JSON.stringify({...command,key:'password'})),null);
});
test('Harmony touch expires and navigation invalidates every prior document generation',()=>{
 const f=fixture(),generation=f.guard.generation;
 f.guard.touch(100);assert.equal(f.guard.consumeTouch(10101),false);assert.equal(f.guard.consumeTouch(10102),false);
 f.guard.touch(100);f.guard.navigate(f.base);assert.equal(f.guard.accepts(f.base,f.base,generation),false);assert.equal(f.guard.consumeTouch(101),false);
});
test('Harmony native system colors accept only bounded colors and recheck the active document',()=>{
 const f=fixture(),calls=[];f.bridge.systemBars=(top,bottom,current)=>calls.push({top,bottom,current});
 assert.equal(f.bridge.prompt(f.base,'codexbridge-system-bars:fixture-secret',JSON.stringify({top:'#ffffff',bottom:'#111111'})),'saved');
 assert.equal(calls[0].current(),true);f.guard.navigate(f.base);assert.equal(calls[0].current(),false);
 assert.equal(f.bridge.prompt(f.base,'codexbridge-system-bars:fixture-secret',JSON.stringify({top:'red',bottom:'#111111'})),null);
});

function httpFixture({status=200,effectiveUrl,parts=[Buffer.from('{"ok":true}')]}={}){
 const requests=[],sessions=[];
 const context=vm.createContext({Uint8Array,MobileIdentity:{userAgent:()=> 'BridgeMobile/fixture-HarmonyOS'},url:{URL:{parseURL:value=>new URL(value)}},util:{TextDecoder:{create:()=>({decodeToString:data=>new TextDecoder().decode(data)})}},rcp:{
  Request:class {constructor(url,method,headers){Object.assign(this,{url,method,headers});}},
  createSession(options){
   const session={options,closed:0,close(){this.closed++;},async fetch(request){requests.push(request);for(const part of parts)options.requestConfiguration.tracing.httpEventsHandler.onDataReceive(part.buffer.slice(part.byteOffset,part.byteOffset+part.byteLength));return {statusCode:status,headers:{},effectiveUrl};}};
   sessions.push(session);return session;
  }
 }});
 load('GatewayUrl',context);const Http=load('GatewayHttp',context);
 return {http:new Http(),requests,sessions,context};
}
test('Harmony credential requests are bounded, exact-origin and explicitly refuse automatic redirects',async()=>{
 const f=httpFixture(),base='https://gateway.example/d/'+'a'.repeat(32);
 assert.deepEqual(JSON.parse(JSON.stringify(await f.http.json(base,'/api/auth','session=fixture'))),{ok:true});
 assert.equal(f.sessions[0].options.requestConfiguration.transfer.autoRedirect,false);assert.equal(f.sessions[0].closed,1);
 assert.equal(f.requests[0].headers.Origin,'https://gateway.example');assert.equal(f.requests[0].url,base+'/api/auth');
 for(const path of ['https://other.example/api/auth','//other.example/api/auth','/api/../auth','/api/%2e%2e/auth'])await assert.rejects(f.http.json(base,path,'session=fixture'));
 const changed=httpFixture({effectiveUrl:'https://other.example/'});await assert.rejects(changed.http.json(base,'/api/auth','session=fixture'));
 const redirect=httpFixture({status:302});await assert.rejects(redirect.http.json(base,'/api/auth','session=fixture'));assert.equal(redirect.requests.length,1);
 const large=httpFixture({parts:[Buffer.alloc(8)]});await assert.rejects(large.http.request(base,'/api/auth','session=fixture',4));assert.equal(large.sessions[0].closed,1);
});
test('Harmony streaming errors cannot turn a partially written attachment into a success',async()=>{
 const f=httpFixture();await assert.rejects(f.http.request('https://gateway.example','/api/auth','session=fixture',1024,()=>{throw Error('disk full');}));
 assert.equal(f.sessions[0].closed,1);
 const Download=load('AttachmentDownload',f.context),base='https://gateway.example/d/'+'a'.repeat(32);
 const target=base+'/api/sessions/12345678-1234-4234-9234-123456789012/workspace/download?path=notes.txt';
 assert.equal(Download.accepts(target,base),true);assert.equal(Download.accepts(target.replace('gateway.example','other.example'),base),false);
 assert.equal(Download.accepts('https://gateway.example/api/auth',base),false);
 assert.equal(Download.accepts(base+'/api/desktop-sessions/claude/images/'+'a'.repeat(64)+'?download=1',base),true);
 assert.equal(Download.accepts(base+'/api/desktop-sessions/claude/images/'+'a'.repeat(64)+'?download=1&download=1',base),false);
 assert.equal(Download.accepts(base+'/api/sessions/diagnostics?thread=12345678-1234-4234-9234-123456789012',base),true);
 assert.equal(Download.accepts(base+'/api/sessions/diagnostics?thread=all',base),false);
 assert.equal(Download.filename('attachment; filename="../../notes.txt"'),'notes.txt');assert.equal(Download.filename("attachment; filename*=UTF-8''%ZZ"),'download');
});
