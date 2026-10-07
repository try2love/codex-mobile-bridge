'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

// Run the actual phone handlers with an isolated DOM and notification API.
async function fixture(){
  const nodes=new Map(),saved=new Map(),writes=[];
  const node=(tag='')=>({tagName:tag.toUpperCase(),children:[],value:'',checked:false,disabled:false,hidden:false,open:false,textContent:'',dataset:{},options:[],style:{},scrollTop:0,parentElement:{},
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},replaceChildren(...values){this.children=values;},append(...values){this.children.push(...values);},querySelectorAll(){return [];},querySelector(){return {disabled:false};},
    showModal(){this.open=true;},close(){this.open=false;}});
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  for(const match of html.matchAll(/\bid="([^"]+)"/g))nodes.set(match[1],node());
  const storage=()=>({getItem(){return null;},setItem(){},removeItem(){}});
  const archiveToggles=[];
  const archiveFilter={classList:{toggle:(name,enabled)=>archiveToggles.push([name,enabled])}};
  let nextPost;
  const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
  const context=vm.createContext({Workbench:class{open(){}reset(){}setThumbnails(){}relabel(){}},navigator:{userAgent:'test'},document:{addEventListener(){},documentElement:{},getElementById:id=>nodes.get(id),querySelector:selector=>selector==='.archive-filter'?archiveFilter:{disabled:false},querySelectorAll:()=>[],createElement:node},
    renderMarkdown(){},window:{addEventListener(){}},localStorage:storage(),sessionStorage:storage(),
    location:{hash:'',pathname:'/',search:''},history:{replaceState(){}},setTimeout(){},clearTimeout(){},setInterval(){},
    ChatTimeline:class{constructor(){this.abort=new AbortController();}async start(){}dispose(){this.abort.abort();}relabel(){}},
    fetch:async(url,options={})=>{
      if(url.includes('/respond?')){writes.push({url,body:JSON.parse(options.body)});return response({status:'accepted'});}
      if(url==='/api/auth')return response({authenticated:false});
      if(url.includes('/reconnect'))return response({ok:true});
      if(url==='/api/sessions?q=&offset=0&archived=false')return response({sessions:[],unavailableHosts:[]});
      if(url.startsWith('/api/notifications/pushplus')||url.includes('/rename?')){
        if(options.method==='POST'){
          const body=JSON.parse(options.body);writes.push({url,body});
          if(nextPost){const handle=nextPost;nextPost=null;return handle(body);}
          if(url.includes('/rename?'))return response({title:body.title});
          if(url.endsWith('/test'))return response({ok:true});
          saved.set(url,{pushplusEnabled:body.pushplusEnabled,hasPushplusToken:!!body.pushplusToken||!body.clearPushplusToken&&!!saved.get(url)?.hasPushplusToken});
        }
        return response(saved.get(url)||{pushplusEnabled:false,hasPushplusToken:false});
      }
      assert.match(url,/\/notifications\?/);
      if(options.method==='POST'){
        const body=JSON.parse(options.body);writes.push({url,body});
        if(nextPost){const handle=nextPost;nextPost=null;return handle(body);}
        saved.set(url,{available:true,watching:body.requests!=='off'||body.completion==='on',notifyOnCompletion:body.completion==='on',...body});
      }
      return response(saved.get(url)||{available:true,watching:false,notifyOnCompletion:false,requests:'inherit',completion:'inherit'});
    }});
  for(const file of ['web/i18n.js','web/list-sync.js','web/account.js','web/modes.js','web/attachments.js','web/activity.js','web/fast-mode.js','web/message-actions.js','web/app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  await new Promise(setImmediate);
  const run=code=>vm.runInContext(code,context);
  return {nodes,writes,saved,run,html,response,archiveToggles,setPost:handle=>{nextPost=handle;},
    async open(id='11111111-1111-4111-8111-111111111111',host='local'){
      await run(`openChat(${JSON.stringify(id)},${JSON.stringify(host)})`);await new Promise(setImmediate);
    },
    save:()=>nodes.get('notify-form').onsubmit({preventDefault(){}}),
    settings:()=>nodes.get('notify-button').onclick()};
}

test('homepage retains the v1.3 archive checkbox and complete mode labels',async()=>{
  const ui=await fixture();
  assert.match(ui.html, /id="archived" type="checkbox"/);
  for(const text of ['最近交互','按项目','发送新消息','完成后发送','补充当前任务','普通模式','计划模式','目标模式'])assert.ok(ui.html.includes(text));
});

test('request and completion choices are independent and persist',async()=>{
  const ui=await fixture();await ui.open();await ui.settings();
  assert.equal(ui.nodes.get('notify-completion').value,'inherit');
  ui.nodes.get('notify-enabled').value='off';ui.nodes.get('notify-completion').value='on';await ui.save();
  assert.deepEqual(ui.writes[0].body,{requests:'off',completion:'on'});
  assert.equal(ui.nodes.get('notify-dialog').open,false);
  await ui.open();await ui.settings();
  assert.equal(ui.nodes.get('notify-enabled').value,'off');assert.equal(ui.nodes.get('notify-completion').value,'on');
  ui.nodes.get('notify-enabled').value='inherit';ui.nodes.get('notify-completion').value='off';await ui.save();
  assert.deepEqual(ui.writes[1].body,{requests:'inherit',completion:'off'});
});

test('completion choices are isolated by chat and host; cancelling sends nothing',async()=>{
  const ui=await fixture();await ui.open();await ui.settings();
  ui.nodes.get('notify-enabled').value='on';ui.nodes.get('notify-completion').value='on';await ui.save();
  await ui.open(undefined,'remote:test');await ui.settings();
  assert.equal(ui.nodes.get('notify-completion').value,'inherit');
  ui.nodes.get('notify-enabled').value='on';await ui.save();
  assert.match(ui.writes[1].url,/host=remote%3Atest/);
  assert.equal(ui.writes[1].body.completion,'inherit');
  await ui.open('22222222-2222-4222-8222-222222222222');await ui.settings();
  assert.equal(ui.nodes.get('notify-enabled').value,'inherit');
  ui.nodes.get('notify-enabled').value='on';ui.nodes.get('notify-completion').value='on';ui.nodes.get('notify-dialog').close();
  await ui.settings();assert.equal(ui.nodes.get('notify-enabled').value,'inherit');assert.equal(ui.writes.length,2);
});

test('failed saves keep the dialog and draft choice for retry',async()=>{
  const ui=await fixture();await ui.open();await ui.settings();
  ui.nodes.get('notify-enabled').value='on';ui.nodes.get('notify-completion').value='on';
  ui.setPost(()=>ui.response({error:'Cannot save'},409));await ui.save();
  assert.equal(ui.nodes.get('notify-dialog').open,true);
  assert.equal(ui.nodes.get('notify-error').textContent,'Cannot save');
  assert.equal(ui.nodes.get('notify-save').disabled,false);
  assert.equal(ui.nodes.get('notify-completion').value,'on');
  assert.equal(ui.nodes.get('notify-button').textContent,'提醒');
  await ui.save();assert.equal(ui.nodes.get('notify-dialog').open,false);
});

test('late save responses cannot change or close another chat notification dialog',async()=>{
  const ui=await fixture();await ui.open();await ui.settings();
  ui.nodes.get('notify-enabled').value='on';ui.nodes.get('notify-completion').value='on';
  let finish;ui.setPost(()=>new Promise(resolve=>{finish=resolve;}));const saving=ui.save();
  await ui.open('22222222-2222-4222-8222-222222222222','remote:test');
  assert.equal(ui.nodes.get('notify-dialog').open,false);await ui.settings();
  finish(ui.response({available:true,watching:true,notifyOnCompletion:true}));await saving;
  assert.equal(ui.nodes.get('notify-dialog').open,true);
  assert.equal(ui.nodes.get('notify-enabled').value,'inherit');
  assert.equal(ui.nodes.get('notify-button').textContent,'提醒');
});

test('notification controls have English translations and language changes preserve choices',async()=>{
  const ui=await fixture();await ui.open();await ui.settings();
  ui.nodes.get('notify-enabled').value='on';ui.nodes.get('notify-completion').value='on';
  ui.nodes.get('phone-language').value='en';ui.nodes.get('phone-language').onchange();
  const dialog=ui.html.match(/<dialog id="notify-dialog"[\s\S]*?<\/dialog>/)[0];
  for(const [,text] of dialog.matchAll(/data-i18n(?:-aria-label)?="([^"]+)"/g)){
    assert.notEqual(ui.run(`BridgeI18n.t(${JSON.stringify(text)})`),text);
  }
  assert.equal(ui.nodes.get('notify-completion').value,'on');
  assert.equal(ui.writes.length,0);
});


test('PushPlus saves and tests settings without redisplaying its token',async()=>{
  const ui=await fixture();await ui.open();
  await ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.get('pushplus-dialog').open,true);
  ui.nodes.get('pushplus-enabled').checked=true;ui.nodes.get('pushplus-token').value='secret';
  await ui.nodes.get('pushplus-form').onsubmit({preventDefault(){}});
  assert.deepEqual(ui.writes[0].body,{pushplusEnabled:true,pushplusToken:'secret',clearPushplusToken:false});
  assert.equal(ui.nodes.get('pushplus-token').value,'');
  assert.equal(ui.nodes.get('pushplus-token').placeholder,'已保存，留空保留');
  await ui.nodes.get('pushplus-test').onclick();assert.equal(ui.writes[1].url,'/api/notifications/pushplus/test');
  ui.nodes.get('pushplus-token').value='retry-token';
  ui.setPost(()=>ui.response({error:'Network failure'},400));
  await ui.nodes.get('pushplus-form').onsubmit({preventDefault(){}});
  assert.equal(ui.nodes.get('pushplus-token').value,'retry-token');
  assert.equal(ui.nodes.get('pushplus-error').textContent,'Network failure');
});

test('rename stays scoped to its original host and cannot overwrite a newly opened chat',async()=>{
  const ui=await fixture();await ui.open(undefined,'remote:test');
  ui.nodes.get('chat-title').textContent='Old';ui.nodes.get('rename-chat').onclick();
  assert.equal(ui.nodes.get('rename-title').value,'Old');
  ui.nodes.get('rename-title').value='New';
  let resolve;ui.setPost(()=>new Promise(r=>resolve=r));
  const pending=ui.nodes.get('rename-form').onsubmit({preventDefault(){}});
  await new Promise(setImmediate);
  await ui.open('22222222-2222-4222-8222-222222222222');
  ui.nodes.get('chat-title').textContent='Other chat';
  resolve(ui.response({title:'New'}));await pending;
  assert.match(ui.writes[0].url,/rename\?host=remote%3Atest/);
  assert.deepEqual(ui.writes[0].body,{title:'New'});
  assert.equal(ui.nodes.get('chat-title').textContent,'Other chat');
  assert.equal(ui.nodes.get('rename-dialog').open,false);
});

test('rename success updates header and detail; failed save retains draft',async()=>{
  const ui=await fixture();await ui.open();ui.nodes.get('rename-chat').onclick();
  ui.nodes.get('rename-title').value='A new title';
  await ui.nodes.get('rename-form').onsubmit({preventDefault(){}});
  assert.equal(ui.nodes.get('chat-title').textContent,'A new title');
  assert.equal(ui.nodes.get('details-title').textContent,'A new title');
  ui.nodes.get('rename-chat').onclick();ui.nodes.get('rename-title').value='Retry title';
  ui.setPost(()=>ui.response({error:'Rename failed'},409));
  await ui.nodes.get('rename-form').onsubmit({preventDefault(){}});
  assert.equal(ui.nodes.get('rename-dialog').open,true);
  assert.equal(ui.nodes.get('rename-title').value,'Retry title');
  assert.equal(ui.nodes.get('rename-error').textContent,'Rename failed');
  assert.equal(ui.nodes.get('chat-title').textContent,'A new title');
});

test('unconfigured channels still open chat policies instead of PushPlus',async()=>{
  const ui=await fixture();await ui.open();
  ui.saved.set('/api/sessions/11111111-1111-4111-8111-111111111111/notifications?host=local',{available:false,requests:'inherit',completion:'off'});
  await ui.settings();
  assert.equal(ui.nodes.get('notify-dialog').open,true);
  assert.equal(ui.nodes.get('pushplus-dialog').open,false);
});

test('plan feedback blocks execution and submits its text to revision',async()=>{
  const ui=await fixture();await ui.open();
  const card=ui.run("renderPlanApproval({id:'plan',params:{planContent:'Original'}})");
  const descendants=n=>[n,...(n.children||[]).flatMap(descendants)];
  const all=descendants(card),input=all.find(n=>n.tagName==='TEXTAREA'),buttons=all.filter(n=>n.tagName==='BUTTON');
  assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,true);
  input.value='Add recovery checks';input.oninput();
  assert.equal(buttons[0].disabled,true);assert.equal(buttons[1].className,'primary');
  await buttons[0].onclick();assert.equal(ui.writes.length,0);
  await buttons[1].onclick();assert.deepEqual(ui.writes[0].body,{requestId:'plan',response:{action:'revise',text:'Add recovery checks'}});
});

test('accepted interaction bumps recent order while merely rendering does not',async()=>{
  const ui=await fixture();
  ui.run("listRows=[{id:'new',host:'local',title:'New',recency:200},{id:'old',host:'local',title:'Old',recency:100}];renderList()");
  assert.equal(ui.run('listRows[0].id'),'new');
  ui.run("bumpRecency('old','local',300)");assert.equal(ui.run('listRows[0].id'),'old');
  ui.run("bumpRecency('old','local',150)");assert.equal(ui.run('listRows[0].recency'),300);
});
