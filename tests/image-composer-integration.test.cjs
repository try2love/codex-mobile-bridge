'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const sid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',upload='11111111-2222-3333-4444-555555555555';
function fixture(provider='codex',host='local'){
 const scope={gateway:'gateway',account:'account-a',provider,host:provider==='codex'?host:'local',session:provider==='codex'?sid:'native-chat'};
 const identity=()=>JSON.stringify(scope),nodes={app:{hidden:false,classList:{contains:()=>true}},chat:{hidden:false}},requests=[],uploads=[],registered=new WeakSet();let callback;
 const quotes={available:()=>true,identity},input={value:'existing text',selectionStart:2,selectionEnd:4},existing={id:'existing',name:'old.png',size:5,status:'ready'},quoteRows=[{text:'existing quote'}];quotes.quotes=quoteRows;
 const view={provider:provider==='codex'?'codex':provider,sid:scope.session,quotes,container:{hidden:false},conversation:{hidden:false},state:{capabilities:{attachments:true}},canMutate:()=>true,input};
 const workspace={current:{id:scope.session,host:scope.host,provider,active:'file:workspace.png'},select(id){this.current.active=id;}};view.workbench=workspace;view.resizeInput=()=>{};
 const window={BridgeImageViewer:{configureComposer:value=>callback=value,isRegisteredSource:value=>registered.has(value)}};
 let counter=0,response=()=>({ok:true,headers:new Headers(),blob:async()=>new Blob(['image'],{type:'image/png'})});
 const context=vm.createContext({window,location:new URL('https://gateway.test/'),URL,Blob,File,Headers,$:id=>nodes[id],t:text=>text,clientNavigation:{provider},desktopSessions:view,codexQuotes:quotes,currentId:sid,currentHost:host,sending:false,state:{},currentAccess:()=>({canSend:true}),workbench:workspace,isCodexClient:value=>value==='codex'||value.startsWith('wsl:'),chatKey:(id,host)=>host+'|'+id,uuid:()=>String(++counter),fetch:async(url,options)=>{requests.push({url,options});return response();}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/chat/attachments.js'),'utf8'),context);
 const panel=vm.runInContext('Object.create(ChatAttachments.prototype)',context);Object.assign(panel,{key:provider==='codex'?host+'|'+sid:JSON.stringify([provider,scope.session]),locked:false,canUpload:()=>true,rows:[existing],render(){},uploadPending(key,rows){uploads.push({key,rows:[...rows]});}});context.attachments=panel;view.attachments=panel;
 const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8'),start=source.indexOf('function imageComposerTarget(){'),end=source.indexOf('function saveDraft(){',start);assert.ok(start>0&&end>start);vm.runInContext(source.slice(start,end),context);
 const url=provider==='codex'?`/api/sessions/${sid}/uploads/${upload}/preview?host=${encodeURIComponent(host)}&variant=original`:`/api/desktop-sessions/${provider}/uploads/${upload}/preview?sessionId=${scope.session}&variant=original`;
 const value={url,name:'photo.png'};
 return {api:callback,scope,nodes,quotes,view,context,panel,workspace,requests,uploads,value,input,existing,setResponse:value=>response=value,register:value=>{registered.add(value);return value;}};
}

test('actual image composer callback uses the current Codex upload path and preserves existing draft, quotes and attachments',async()=>{
 const ui=fixture(),scope=ui.api.scope();assert.equal(ui.api.canReuse(ui.value),true);assert.equal(ui.requests.length,0);
 assert.equal(await ui.api.add(ui.value,scope),true);assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].url,'https://gateway.test'+ui.value.url);
 assert.deepEqual({...ui.requests[0].options},{credentials:'same-origin',redirect:'error'});assert.equal(ui.uploads.length,1);assert.equal(ui.uploads[0].key,'local|'+sid);assert.equal(ui.workspace.current.active,'chat');
 assert.equal(ui.panel.rows[0],ui.existing);assert.equal(ui.panel.rows[1].file.name,'photo.png');assert.equal(ui.panel.rows[1].file.type,'image/png');
 assert.equal(ui.input.value,'existing text');assert.equal(ui.input.selectionStart,2);assert.equal(ui.input.selectionEnd,4);assert.deepEqual(ui.quotes.quotes,[{text:'existing quote'}]);
});

test('current-session file and desktop image grants work for local, SSH and WSL host identities',async()=>{
 for(const host of ['local','ssh:work','wsl:'+'a'.repeat(32)]){
  const ui=fixture('codex',host);
  for(const kind of ['files','desktop-images'])assert.equal(ui.api.canReuse({url:`/api/sessions/${sid}/${kind}/${'f'.repeat(64)}?host=${encodeURIComponent(host)}`}),true);
  assert.equal(await ui.api.add(ui.value,ui.api.scope()),true);assert.equal(ui.uploads[0].key,host+'|'+sid);
 }
});

test('callback rejects cross-session, cross-host, cross-provider, arbitrary and redirected URLs before adding',async()=>{
 const ui=fixture(),denied=[
  ui.value.url.replace(sid,upload),ui.value.url.replace('host=local','host=remote'),ui.value.url+'&host=local',ui.value.url+'&side=other',ui.value.url+'&unknown=x',ui.value.url+'&variant=original',ui.value.url.replace('variant=original','variant=raw'),ui.value.url+'#fragment',
  'https://other.test'+ui.value.url,'https://user@gateway.test'+ui.value.url,'blob:https://gateway.test/forged','data:image/png;base64,aGVsbG8=',`/api/sessions/${sid}/workspace/download?host=local&path=private.png`,`/api/desktop-sessions/claude/uploads/${upload}/preview?sessionId=native-chat`,`/api/sessions/${sid}/files/${'f'.repeat(64)}?host=local&variant=original`,
 ];
 for(const url of denied){const value={url,name:'bad.png'};assert.equal(ui.api.canReuse(value),false,url);assert.equal(await ui.api.add(value,ui.api.scope()),false,url);}
 assert.equal(ui.requests.length,0);assert.equal(ui.uploads.length,0);
 ui.setResponse(()=>({ok:true,redirected:true,headers:new Headers(),blob:async()=>new Blob(['image'],{type:'image/png'})}));await assert.rejects(ui.api.add(ui.value,ui.api.scope()));assert.equal(ui.uploads.length,0);
});

test('registered workspace data images require their original provider, session, host and opaque scope',async()=>{
 for(const provider of ['codex','claude','deepseek']){
  const ui=fixture(provider),context={provider,id:ui.scope.session,host:ui.scope.host};assert.equal(ui.api.canReuse({context}),true,'pre-read context captures scope');
  const value=ui.register({url:'data:image/png;base64,aGVsbG8=',name:'workspace.png',context,composerScope:ui.api.scope()});
  assert.equal(ui.api.canReuse({...value}),false,'copying record cannot register a data URL');
  for(const key of ['provider','id','host'])assert.equal(ui.api.canReuse(ui.register({...value,context:{...context,[key]:'other'}})),false,key);
  assert.equal(ui.api.canReuse(ui.register({...value,composerScope:'old-account'})),false);
  assert.equal(await ui.api.add(value,ui.api.scope()),true);assert.equal(ui.panel.rows[1].file.name,'workspace.png');assert.equal(ui.input.value,'existing text');
  if(provider!=='codex'){const other=ui.value.url.replace('sessionId=native-chat','sessionId=other');assert.equal(ui.api.canReuse({url:other}),false);}
 }
});

test('image target is unavailable outside the active unlocked current chat and attachment key',()=>{
 const mutations=[ui=>ui.nodes.app.hidden=true,ui=>ui.nodes.app.classList.contains=()=>false,ui=>ui.nodes.chat.hidden=true,ui=>ui.quotes.available=()=>false,ui=>ui.panel.key='other',ui=>ui.panel.locked=true,ui=>ui.context.sending=true,ui=>ui.context.state.loadingHistory=true,ui=>ui.context.state.activating=true,ui=>ui.context.currentAccess=()=>({canSend:false}),ui=>ui.context.desktopSessions.provider='claude',ui=>ui.workspace.current.id='other',ui=>ui.workspace.current.host='other',ui=>ui.workspace.current.provider='other'];
 for(const change of mutations){const ui=fixture();change(ui);assert.equal(ui.api.scope(),null);assert.equal(ui.api.canReuse(ui.value),false);}
 for(const change of [ui=>ui.view.container.hidden=true,ui=>ui.view.conversation.hidden=true,ui=>ui.view.busy=true,ui=>ui.view.canMutate=()=>false,ui=>ui.view.state.capabilities.attachments=false,ui=>ui.context.clientNavigation.provider='claude']){const ui=fixture('deepseek');change(ui);assert.equal(ui.api.scope(),null);}
});

test('an image read finishing after account, session, host, key or lock changes cannot enter a new draft',async()=>{
 for(const change of [ui=>ui.scope.account='account-b',ui=>ui.scope.gateway='gateway-b',ui=>ui.context.currentId=upload,ui=>ui.context.currentHost='remote',ui=>ui.panel.key='other',ui=>ui.panel.locked=true]){
  const ui=fixture();let finish;ui.setResponse(()=>({ok:true,headers:new Headers(),blob:()=>new Promise(resolve=>finish=resolve)}));const pending=ui.api.add(ui.value,ui.api.scope());await new Promise(setImmediate);change(ui);finish(new Blob(['image'],{type:'image/png'}));assert.equal(await pending,false);assert.equal(ui.uploads.length,0);assert.equal(ui.panel.rows.length,1);assert.equal(ui.input.value,'existing text');
 }
});

test('image reuse enforces response MIME and 20 MB limits before the existing attachment uploader',async()=>{
 for(const blob of [{type:'text/html',size:10},{type:'image/svg+xml',size:10},{type:'image/png',size:0},{type:'image/png',size:20*1024*1024+1}]){
  const ui=fixture();ui.setResponse(()=>({ok:true,headers:new Headers(),blob:async()=>blob}));await assert.rejects(ui.api.add(ui.value,ui.api.scope()));assert.equal(ui.uploads.length,0);
 }
 const ui=fixture();let read=false;ui.setResponse(()=>({ok:true,headers:new Headers({'Content-Length':String(20*1024*1024+1)}),blob:async()=>{read=true;return new Blob(['image'],{type:'image/png'});}}));await assert.rejects(ui.api.add(ui.value,ui.api.scope()));assert.equal(read,false);assert.equal(ui.uploads.length,0);
});


test('shared relay image reads stay inside the currently selected computer path',async()=>{
 const ui=fixture(),current='/d/'+'a'.repeat(32),other='/d/'+'b'.repeat(32);
 ui.context.window.BridgeRelayPath=path=>path.startsWith(current+'/')?path.slice(current.length):'';
 const value={...ui.value,url:current+ui.value.url};assert.equal(ui.api.canReuse(value),true);
 assert.equal(ui.api.canReuse({...value,url:other+ui.value.url}),false);assert.equal(ui.api.canReuse(ui.value),false);
 assert.equal(await ui.api.add(value,ui.api.scope()),true);assert.equal(ui.requests[0].url,'https://gateway.test'+current+ui.value.url);
});
