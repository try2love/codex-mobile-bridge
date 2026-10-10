'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');

async function fixture({missing='',auth='anonymous',hash='',language='zh',pairPending=false}={}){
  const nodes=new Map(),timers=new Map(),listeners=new Map(),requests=[],writes=[];let clock=0,reloads=0,error=null;
  const node=(tag='div')=>({tagName:tag.toUpperCase(),value:'',checked:false,disabled:false,hidden:false,open:false,textContent:'',dataset:{},options:[],children:[],parentElement:{},style:{setProperty(){}},scrollTop:0,
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},getBoundingClientRect(){return {width:0};},addEventListener(){},setAttribute(k,v){this[k]=v;},closest(){return nodes.get('accounts-dialog');},
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
    Workbench:class{reset(){}relabel(){}},DesktopSessionsView:class{clear(){}},ClientNavigation:class{constructor(){this.provider='codex';}async refresh(){}async openChatLink(){}},
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
      if(url.startsWith('/api/sessions?'))return response({sessions:[]});
      throw Error('Unexpected fixture request');
    }});
  const run=file=>vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context,{filename:file});
  if(missing!=='bootstrap')run('web/shared/bootstrap.js');
  const dependencies=['web/hosts/environment.js','web/shared/i18n.js','web/features/chat/list-sync.js','web/features/accounts/account.js','web/features/accounts/accounts.js','web/features/chat/modes.js','web/features/chat/attachments.js','web/features/chat/activity.js','web/features/chat/fast-mode.js','web/features/chat/message-actions.js','web/features/chat/permissions.js'];
  for(const file of dependencies){if(file.endsWith('/'+missing+'.js'))continue;run(file);}
  try{run('web/shell/app.js');}catch(e){error=e;dispatch('error',{target:window,message:e.message});}
  await new Promise(setImmediate);
  return {nodes,requests,writes,location,error,timers,listeners,dispatch,
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

test('normal login and authenticated workspace hide startup status and cancel its error listeners',async()=>{
  for(const auth of ['anonymous','signed-in']){
    const ui=await fixture({auth});assert.equal(ui.error,null);assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
    assert.equal(ui.nodes.get(auth==='anonymous'?'login':'app').hidden,false);
    assert.equal([...ui.timers.values()].some(timer=>timer.delay===12000),false);
    assert.equal(ui.listeners.get('error').size,0);ui.dispatch('error',{target:{tagName:'SCRIPT'},message:'late error'});
    assert.equal(ui.nodes.get('bootstrap-status').hidden,true);
  }
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

test('bootstrap failure leaves static status available and does not prevent the main app from revealing login',async()=>{
  const waiting=await fixture({missing:'bootstrap',auth:'pending'});assert.equal(waiting.nodes.get('bootstrap-status').hidden,false);
  const loaded=await fixture({missing:'bootstrap'});assert.equal(loaded.nodes.get('login').hidden,false);assert.equal(loaded.nodes.get('bootstrap-status').hidden,true);
});
