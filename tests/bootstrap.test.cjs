'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const workbenchBack=vm.runInNewContext(fs.readFileSync(path.join(root,'web/features/workspace/workbench.js'),'utf8')+'\nWorkbench.prototype.back');

async function fixture({missing='',auth='anonymous',hash='',language='zh',pairPending=false,navigationPending=false,allDisabled=false}={}){
  const nodes=new Map(),timers=new Map(),listeners=new Map(),requests=[],writes=[],navigation=[];let clock=0,reloads=0,error=null,releaseNavigation;
  const node=(tag='div')=>({tagName:tag.toUpperCase(),value:'',checked:false,disabled:false,hidden:false,open:false,textContent:'',dataset:{},options:[],children:[],parentElement:{},style:{setProperty(){}},scrollTop:0,
    classList:(()=>{const classes=new Set();return {add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));},toggle(name,force){const enabled=force??!classes.has(name);if(enabled)classes.add(name);else classes.delete(name);return enabled;},contains(name){return classes.has(name);}};})(),click(){this.onclick?.();},getBoundingClientRect(){return {width:0};},addEventListener(){},setAttribute(k,v){this[k]=v;},closest(){return nodes.get('accounts-dialog');},
    replaceChildren(...values){this.children=values;},append(...values){this.children.push(...values);},after(){},querySelectorAll(){return [];},querySelector(){return null;},showModal(){this.open=true;},close(){this.open=false;}});
  for(const match of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const n=node(match[1]);n.hidden=/\bhidden\b/.test(match[2]);nodes.set(match[3],n);
  }
  let current=new URL('https://gateway.test/?view=phone'+hash);
  const location={get href(){return current.href;},get hash(){return current.hash;},get pathname(){return current.pathname;},get search(){return current.search;},host:current.host,reload(){reloads++;}};
  const storage={getItem(key){return key==='bridge-language'?language:null;},setItem(){throw Error('No startup writes to storage');},removeItem(){},clear(){}};
  const window={addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn);},removeEventListener(name,fn){listeners.get(name)?.delete(fn);}};
  const dispatch=(name,event)=>{for(const fn of listeners.get(name)||[])fn(event);};
  const response=data=>({ok:true,status:200,json:async()=>data});
  const context=vm.createContext({window,location,history:{replaceState(_a,_b,url){current=new URL(url,current);}},localStorage:storage,sessionStorage:storage,navigator:{userAgent:'fixture'},
    document:{documentElement:{},getElementById:id=>nodes.get(id),addEventListener(){},dispatchEvent(){},querySelectorAll(){return [];},querySelector(){return {classList:{toggle(){}}};},createElement:node},
    Event:class{},CustomEvent:class{},Option:function(text,value){return {...node('option'),textContent:text,value};},
    Workbench:class{constructor(){this.current=null;this.panel={querySelector(){return null;}};this.backCalls=0;}get splitMode(){return false;}get isFileVisible(){return !!this.current&&this.current.active!=='chat';}back(){this.backCalls++;return workbenchBack.call(this);}directory(session,_tab,dir){session.directory=dir;}select(id){this.current.active=id;}rememberScroll(){}stopThumbnails(){}reset(){}relabel(){}},DesktopSessionsView:class{clear(){}},ClientNavigation:class{constructor(){this.provider='codex';}refresh(){navigation.push('refresh');if(navigationPending)return new Promise(resolve=>{releaseNavigation=()=>{if(allDisabled)this.provider=null;resolve();};});if(allDisabled)this.provider=null;}async openChatLink(){navigation.push('open');}acknowledgeList(){}},
    setTimeout(fn,delay){timers.set(++clock,{fn,delay});return clock;},clearTimeout(id){timers.delete(id);},setInterval(){},clearInterval(){},
    fetch:async(url,options={})=>{
      requests.push(url);if(options.method==='POST')writes.push({url,body:options.body});
      if(url==='/api/auth'){
        if(auth==='pending')return new Promise(()=>{});
        if(auth==='reject')throw Error('private-token-in-network-message');
        return response({authenticated:auth==='signed-in',csrf:'fixture'});
      }
      if(url==='/api/pair')return pairPending?new Promise(()=>{}):response({csrf:'paired'});
      if(url==='/api/accounts')return response({accounts:[],current:{kind:'api'}});
      if(url.startsWith('/api/sessions?'))return allDisabled?{ok:false,status:400,json:async()=>({error:'此应用已关闭，请在应用管理中开启'})}:response({sessions:[]});
      throw Error('Unexpected fixture request');
    }});
  const run=file=>vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context,{filename:file});
  if(missing!=='bootstrap')run('web/shared/bootstrap.js');
  const dependencies=['web/hosts/environment.js','web/shared/i18n.js','web/features/chat/list-sync.js','web/features/accounts/account.js','web/features/accounts/accounts.js','web/features/chat/modes.js','web/features/chat/attachments.js','web/features/chat/activity.js','web/features/chat/fast-mode.js','web/features/chat/message-actions.js','web/features/chat/permissions.js'];
  for(const file of dependencies){if(file.endsWith('/'+missing+'.js'))continue;run(file);}
  try{run('web/shell/app.js');}catch(e){error=e;dispatch('error',{target:window,message:e.message});}
  await new Promise(setImmediate);
  return {nodes,requests,writes,location,error,timers,listeners,dispatch,window,navigation,releaseNavigation(){releaseNavigation?.();},
    get reloads(){return reloads;},timeout(){for(const [id,timer] of timers)if(timer.delay===12000){timers.delete(id);timer.fn();break;}}};
}

test('HTML provides visible status before the independent CSP-safe bootstrap, without revealing login',()=>{
  assert.match(html,/<section id="bootstrap-status"[^>]*aria-busy="true"/);
  assert.doesNotMatch(html.match(/<section id="bootstrap-status"[^>]*>/)[0],/\bhidden\b/);
  assert.match(html,/<section id="login" class="login" hidden>/);
  assert.ok(html.indexOf('id="bootstrap-status"')<html.indexOf('src="/bootstrap.js"'));
  assert.ok(html.indexOf('src="/bootstrap.js"')<html.indexOf('src="/host.js"'));
  assert.doesNotMatch(html,/<script[^>]*>\s*[^<\s]|\son(?:click|error|load)=/);
});

function resourceFixture(){
  const started=[],pending=[],listeners=new Map(),nodes=new Map();let domReady,peak=0;
  for(const id of ['bootstrap-status','bootstrap-title','bootstrap-message','bootstrap-retry'])nodes.set(id,{style:{},setAttribute(){}});
  function record(node,url,kind){started.push({url,kind,async:node.async});pending.push(node);peak=Math.max(peak,pending.length);}
  const styles=[...html.matchAll(/<link[^>]*data-bridge-href="([^"]+)"/g)].map(([,url])=>({getAttribute(){return url;},removeAttribute(){},set href(value){record(this,value,'style');}}));
  const scripts=[...html.matchAll(/<script[^>]*data-bridge-src="([^"]+)"/g)].map(([,url])=>({getAttribute(){return url;},parentNode:{replaceChild(node){record(node,node.src,'script');}}}));
  const window={addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(){}};
  const context=vm.createContext({window,navigator:{userAgent:'resource-fixture'},location:{href:'https://gateway.test/',hash:'',search:''},
    localStorage:{getItem(){return null;}},setTimeout(){return 1;},clearTimeout(){},
    document:{readyState:'loading',getElementById:id=>nodes.get(id),createElement(){return {};},
      querySelectorAll:selector=>selector.startsWith('link')?styles:scripts,addEventListener(name,fn){if(name==='DOMContentLoaded')domReady=fn;}}});
  vm.runInContext(fs.readFileSync(path.join(root,'web/shared/bootstrap.js'),'utf8'),context);
  return {started,pending,nodes,styles,scripts,ready(){domReady();},get peak(){return peak;},
    finish(index=0,failed=false){const node=pending.splice(index,1)[0];node[failed?'onerror':'onload']();}};
}

test('startup bounds resource concurrency and preserves style and classic-script order',()=>{
  const ui=resourceFixture();assert.equal(ui.started.length,0);ui.ready();
  assert.equal(ui.pending.length,4);assert.ok(ui.started.every(row=>row.kind==='style'));
  while(ui.started.length<ui.styles.length||ui.pending.some(node=>'href' in node))ui.finish(ui.pending.length-1);
  assert.deepEqual(ui.started.filter(row=>row.kind==='style').map(row=>row.url),[...html.matchAll(/data-bridge-href="([^"]+)"/g)].map(match=>match[1]));
  while(ui.pending.length)ui.finish(ui.pending.length-1);
  const loadedScripts=ui.started.filter(row=>row.kind==='script');
  assert.equal(ui.peak,4);assert.equal(ui.styles.length,9);assert.equal(loadedScripts.length,40);
  assert.ok(loadedScripts.every(row=>row.async===false));
  assert.deepEqual(loadedScripts.map(row=>row.url),[...html.matchAll(/data-bridge-src="([^"]+)"/g)].map(match=>match[1]));
  assert.equal([...html.matchAll(/<script\s+src=/g)].length,1);
  assert.equal([...html.matchAll(/<link rel="stylesheet" href=/g)].length,1);
});

test('a failed queued resource is reported once and does not stall or replay the queue',()=>{
  const ui=resourceFixture();ui.ready();ui.finish(0,true);
  while(ui.pending.length)ui.finish();
  assert.equal(ui.started.length,49);assert.equal(new Set(ui.started.map(row=>row.url)).size,49);
  assert.match(ui.nodes.get('bootstrap-message').textContent,/resource @ \/vendor\/katex\/katex\.min\.css/);
});

test('missing dependency fails in the real synchronous app chain before auth and produces a visible error',async()=>{
  for(const missing of ['i18n','activity']){
    const ui=await fixture({missing});assert.ok(ui.error);assert.equal(ui.requests.length,0);
    assert.equal(ui.nodes.get('login').hidden,true);assert.equal(ui.nodes.get('app').hidden,true);
    assert.equal(ui.nodes.get('bootstrap-status').hidden,false);assert.match(ui.nodes.get('bootstrap-message').textContent,/页面组件/);
  }
});

test('stalled auth gets bounded visible feedback with no automatic request retry or writes',async()=>{
  const ui=await fixture({auth:'pending'});ui.timeout();ui.timeout();
  assert.deepEqual(ui.requests,['/api/auth']);assert.equal(ui.writes.length,0);assert.equal(ui.nodes.get('login').hidden,true);
  assert.match(ui.nodes.get('bootstrap-message').textContent,/等待网关/);
  ui.nodes.get('bootstrap-retry').onclick();assert.equal(ui.reloads,1);assert.equal(ui.location.href,'https://gateway.test/?view=phone');
});

test('auth rejection reports generic localized text without exposing the exception',async()=>{
  const ui=await fixture({auth:'reject',language:'en'});
  assert.match(ui.nodes.get('bootstrap-message').textContent,/gateway/);
  assert.doesNotMatch(ui.nodes.get('bootstrap-message').textContent,/private-token/);
  assert.equal(ui.nodes.get('login').hidden,true);
});

test('startup diagnostics show script path and error type without URL secrets or exception text',async()=>{
  const ui=await fixture({auth:'pending',hash:'#pair=private-pairing-token'});
  ui.dispatch('error',{target:ui.window,filename:'https://gateway.test/app.js?token=private-query#private-fragment',lineno:17,
    error:{name:'TypeError',message:'private-exception-text'},message:'private-event-text'});
  const text=ui.nodes.get('bootstrap-message').textContent;
  assert.match(text,/启动诊断/);assert.match(text,/TypeError @ \/app\.js:17/);assert.match(text,/auth/);
  assert.doesNotMatch(text,/private-|gateway\.test/);
});

test('startup resource and rejection diagnostics do not print untrusted failure messages',async()=>{
  const ui=await fixture({auth:'pending'});
  ui.dispatch('error',{target:{tagName:'SCRIPT',src:'https://gateway.test/i18n.js?private-query'}});
  assert.match(ui.nodes.get('bootstrap-message').textContent,/resource @ \/i18n\.js/);
  ui.dispatch('unhandledrejection',{reason:{name:'private-error-name',message:'private-rejection'}});
  assert.doesNotMatch(ui.nodes.get('bootstrap-message').textContent,/private-/);
});

test('normal login and authenticated workspace hide startup status and cancel its error listeners',async()=>{
  for(const auth of ['anonymous','signed-in']){
    const ui=await fixture({auth});assert.equal(ui.error,null);assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
    assert.equal(ui.nodes.get(auth==='anonymous'?'login':'app').hidden,false);
    assert.equal([...ui.timers.values()].some(timer=>timer.delay===12000),false);
    assert.equal(ui.listeners.get('error').size,0);ui.dispatch('error',{target:{tagName:'SCRIPT'},message:'late error'});
    assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
  }
});

test('authenticated list starts before client discovery while a deep link waits for discovery',async()=>{
  const ui=await fixture({auth:'signed-in',hash:'#11111111-1111-4111-8111-111111111111',navigationPending:true});
  assert.deepEqual(ui.navigation,['refresh']);
  assert.ok(ui.requests.some(url=>url.startsWith('/api/sessions?scope=host')));
  ui.releaseNavigation();await new Promise(setImmediate);
  assert.deepEqual(ui.navigation,['refresh','open']);
});

test('all-disabled gateway login keeps the management shell without a Codex list error',async()=>{
  const ui=await fixture({auth:'signed-in',navigationPending:true,allDisabled:true});
  assert.equal(ui.nodes.get('app').hidden,false);
  ui.releaseNavigation();await new Promise(setImmediate);
  assert.equal(ui.nodes.get('toast').hidden,true);
});

test('manual retry preserves an unsubmitted pairing fragment after app.js has already removed it',async()=>{
  for(const mode of [{missing:'activity'},{auth:'pending'}]){
    const ui=await fixture({...mode,hash:'#pair=unsubmitted-token'});
    assert.equal(ui.location.hash,'');assert.equal(ui.writes.length,0);
    assert.doesNotMatch(ui.nodes.get('bootstrap-message').textContent,/unsubmitted-token/);
    ui.nodes.get('bootstrap-retry').onclick();
    assert.equal(ui.location.href,'https://gateway.test/?view=phone#pair=unsubmitted-token');assert.equal(ui.reloads,1);
  }
});

test('unknown pairing writes are not replayed by reload and instead request a new scan',async()=>{
  const ui=await fixture({hash:'#pair=submitted-token',pairPending:true});ui.timeout();
  assert.equal(ui.location.hash,'');assert.equal(ui.writes.length,1);assert.equal(ui.writes[0].url,'/api/pair');
  assert.match(ui.nodes.get('bootstrap-message').textContent,/重新扫描新的配对码/);
  ui.nodes.get('bootstrap-retry').onclick();assert.equal(ui.location.hash,'');assert.equal(ui.writes.length,1);assert.equal(ui.reloads,1);
});

test('the standalone app can reveal login without the optional bootstrap API',async()=>{
  const waiting=await fixture({missing:'bootstrap',auth:'pending'});assert.equal(waiting.nodes.get('bootstrap-status').hidden,false);
  const loaded=await fixture({missing:'bootstrap'});assert.equal(loaded.nodes.get('login').hidden,false);assert.equal(loaded.nodes.get('bootstrap-status').hidden,true);
});

test('Codex header Back walks file parents, chat, then list; native Back consumes one level',async()=>{
  const ui=await fixture({auth:'signed-in'}),app=ui.nodes.get('app'),back=ui.nodes.get('back'),workbench=ui.window.BridgeWorkbench;
  const session={active:'files',directory:'src/lib',files:[{id:'files'}]};workbench.current=session;app.classList.add('chat-open');
  const listRequests=()=>ui.requests.filter(url=>url.startsWith('/api/sessions?')).length;
  const initialLists=listRequests();
  back.click();assert.equal(session.directory,'src');assert.equal(session.active,'files');assert.equal(listRequests(),initialLists);
  back.click();assert.equal(session.directory,'');assert.equal(session.active,'files');assert.equal(listRequests(),initialLists);
  back.click();assert.equal(session.active,'chat');assert.equal(app.classList.contains('chat-open'),true);assert.equal(listRequests(),initialLists);
  back.click();assert.equal(app.classList.contains('chat-open'),false);assert.equal(listRequests(),initialLists+1);

  session.directory='src/lib';session.active='files';app.classList.add('chat-open');
  const beforeNative=workbench.backCalls;
  assert.equal(ui.window.BridgeNavigation.back(),true);
  assert.equal(workbench.backCalls,beforeNative+1);assert.equal(session.directory,'src');assert.equal(listRequests(),initialLists+1);
  session.directory='';session.active='chat';
  const beforeList=workbench.backCalls;
  assert.equal(ui.window.BridgeNavigation.back(),true);
  assert.equal(workbench.backCalls,beforeList+2);assert.equal(app.classList.contains('chat-open'),false);assert.equal(listRequests(),initialLists+2);
});
