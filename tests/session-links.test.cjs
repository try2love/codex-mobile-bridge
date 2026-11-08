'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({window:{}});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../web/features/chat/message-actions.js'),'utf8'),context);
const text=context.window.BridgeSessionLinks.text,id='019d0000-1234-7123-8123-123456789abc';
test('copies native Codex thread identity locally and on SSH without launching',()=>{
 assert.equal(text({provider:'codex',id}),'codex://threads/'+id);
 assert.equal(text({provider:'codex',id,host:'remote ssh / 主机'}),'codex://threads/'+id+'?hostId=remote%20ssh%20%2F%20%E4%B8%BB%E6%9C%BA');
});
test('endpoint digests, WSL runtime, invalid threads and unknown providers do not claim deep links',()=>{
 for(const identity of [{provider:'codex',id,host:'wsl:abc'},{provider:'codex',id,runtime:'wsl'},{provider:'codex',id:'bridge-only'},{provider:'claude',id},{provider:'codex',id,host:null}])assert.equal(text(identity),null);
});
test('Harness copies the actual native Session ID verbatim',()=>assert.equal(text({provider:'deepseek',id:'session-native-123'}),'session-native-123'));

const read=file=>fs.readFileSync(require('node:path').join(__dirname,'../web',file),'utf8');
test('Codex title delegates to the shared menu with its anchor',()=>{
 const button={},calls=[],ui=vm.createContext({get:()=>button,window:{openCodexSessionMenu:anchor=>calls.push(anchor)}});
 vm.runInContext(read('features/settings/presentation.js').split('\n').find(line=>line.includes("get('chat-details-button').onclick=")),ui);
 button.onclick();assert.equal(calls[0],button);
});
test('provider title delegates native ID and provider to the shared renderer',()=>{
 const calls=[],source=read('features/clients/desktop-sessions.js'),method=source.slice(source.indexOf('  chatDetails(){'),source.indexOf('  async loadNotificationPolicy(){'));
 const invoke=vm.runInNewContext('({'+method+'}).chatDetails');
 const view={provider:'deepseek',sid:'native-session',state:{title:'Example'},rows:[],titleButton:{},onOpenSessionMenu:(...args)=>calls.push(args)};
 invoke.call(view);assert.equal(calls[0][0].id,'native-session');assert.equal(calls[0][1],'deepseek');assert.equal(calls[0][2],view.titleButton);
});

 test('shared menu copies native identity while retaining gateway routing identity',()=>{
 const source=read('shell/app.js'),start=source.indexOf('function sessionMenuContext('),end=source.indexOf('window.openCodexSessionMenu=',start);
 for(const provider of ['codex','claude','deepseek','zcode']){
  const native=provider==='claude'?'native-claude-session':'session-'+provider,chat={id:'session-'+provider,nativeId:native};
  const c=vm.createContext({sessionUrl:(id,action,host)=>`/${host}/${id}/${action}`,listIdentity:'gateway',csrf:'token',sessionAccess:()=>({}),sourceLabel:()=> 'APP',clientNavigation:{provider,clients:[]},currentId:chat.id,currentHost:'local',state:chat,desktopSessions:{provider,sid:chat.id,state:chat},sessionPins:{value:()=>false},window:{BridgeSessionLinks:{text:()=>null}},t:v=>v,uuid:()=> 'request'});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../web/features/clients/client-lifecycle.js'),'utf8'),c);
  vm.runInContext(source.slice(start,end),c);const menu=c.sessionMenuContext(chat,provider);
  assert.equal(menu.sessionId,native);assert.equal(menu.details.find(row=>row[0]==='会话 ID')[1],native);assert.equal(chat.id,'session-'+provider);
  if(provider==='claude'){delete chat.nativeId;assert.equal(c.sessionMenuContext(chat,provider).sessionId,null,'legacy cached gateway ID is not advertised as native');}
 }
});
