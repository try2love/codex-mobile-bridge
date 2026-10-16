'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

// Run the actual phone handlers with an isolated DOM and notification API.
async function fixture(){
  const nodes=new Map(),saved=new Map(),writes=[],listRequests=[];
  const node=(tag='')=>({tagName:tag.toUpperCase(),children:[],value:'',checked:false,disabled:false,hidden:false,open:false,textContent:'',dataset:{},options:[],style:{setProperty(k,v){this[k]=v;}},scrollTop:0,parentElement:{},listeners:{},
    setAttribute(k,v){this[k]=v;},after(child){this.nextSibling=child;},getBoundingClientRect(){return {width:390,height:844};},classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(type,listener){(this.listeners[type]??=[]).push(listener);},dispatchEvent(event){for(const listener of this.listeners[event.type]||[])listener(event);},replaceChildren(...values){this.children=values;},append(...values){this.children.push(...values);},querySelectorAll(){return [];},querySelector(){return {disabled:false};},
    showModal(){this.open=true;},close(){this.open=false;this.dispatchEvent({type:'close'});}});
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  for(const match of html.matchAll(/\bid="([^"]+)"/g))nodes.set(match[1],node());
  const channelButtons=['bark','ntfy','pushplus'].map(channel=>Object.assign(node('button'),{dataset:{notificationChannel:channel}}));
  const storage=()=>({getItem(){return null;},setItem(){},removeItem(){}});
  const archiveToggles=[];
  const archiveFilter={classList:{toggle:(name,enabled)=>archiveToggles.push([name,enabled])}};
  let nextPost,nextGet;
  const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
  let listResponse=()=>response({sessions:[],unavailableHosts:[]});
  const context=vm.createContext({ClientNavigation:class{constructor(){this.provider="codex";}async refresh(){}acknowledgeList(){}},DesktopSessionsView:class{clear(){}},Workbench:class{open(){}reset(){}setThumbnails(){}relabel(){}},navigator:{userAgent:'test'},document:{addEventListener(){},documentElement:{},getElementById:id=>nodes.get(id),querySelector:selector=>selector==='.archive-filter'?archiveFilter:{disabled:false},querySelectorAll:selector=>selector==='[data-notification-channel]'?channelButtons:[],createElement:node},
    renderMarkdown(){},window:{addEventListener(){}},localStorage:storage(),sessionStorage:storage(),
    location:{hash:'',pathname:'/',search:''},history:{replaceState(){}},setTimeout(){},clearTimeout(){},setInterval(){},
    ChatTimeline:class{constructor(){this.abort=new AbortController();}async start(){}dispose(){this.abort.abort();}relabel(){}},
    fetch:async(url,options={})=>{
      if(url.includes('/respond?')){writes.push({url,body:JSON.parse(options.body)});return response({status:'accepted'});}
      if(url==='/api/auth')return response({authenticated:false});
      if(url.includes('/reconnect'))return response({ok:true});
      if(url.startsWith('/api/sessions?')){listRequests.push(url);return listResponse(url);}
      if(url==='/api/activity')return response({sessions:[]});
      if(url.startsWith('/api/notifications/channels/')||url.includes('/rename?')){
        if(options.method!=='POST'&&nextGet){const handle=nextGet;nextGet=null;return handle(url);}
        if(options.method==='POST'){
          const body=JSON.parse(options.body);writes.push({url,body});
          if(nextPost){const handle=nextPost;nextPost=null;return handle(body);}
          if(url.includes('/rename?'))return response({title:body.title});
          if(url.endsWith('/test'))return response({ok:true});
          if(url.endsWith('/common'))saved.set(url,{...saved.get(url),...body});
          else {
            const secret=url.endsWith('/bark')?'barkKey':url.endsWith('/ntfy')?'token':'pushplusToken';
            const marker=url.endsWith('/bark')?'hasBarkKey':url.endsWith('/ntfy')?'hasToken':'hasPushplusToken';
            saved.set(url,{...saved.get(url),...body,[secret]:undefined,[marker]:!!body[secret]||!body['clear'+secret[0].toUpperCase()+secret.slice(1)]&&!!saved.get(url)?.[marker]});
          }
        }
        return response(saved.get(url)||{pushplusEnabled:false,hasPushplusToken:false,barkServer:'https://api.day.app',server:'https://ntfy.sh'});
      }
      assert.match(url,/\/notifications\?/);
      if(options.method==='POST'){
        const body=JSON.parse(options.body);writes.push({url,body});
        if(nextPost){const handle=nextPost;nextPost=null;return handle(body);}
        saved.set(url,{available:true,watching:body.requests!=='off'||body.completion==='on',notifyOnCompletion:body.completion==='on',...body});
      }
      return response(saved.get(url)||{available:true,watching:false,notifyOnCompletion:false,requests:'inherit',completion:'inherit'});
    }});
  for(const file of ['web/hosts/environment.js','web/shared/i18n.js','web/features/chat/list-sync.js','web/features/accounts/account.js','web/features/chat/modes.js','web/features/chat/attachments.js','web/features/chat/activity.js','web/features/chat/fast-mode.js','web/features/chat/message-actions.js','web/features/chat/permissions.js','web/shell/app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  await new Promise(setImmediate);
  const run=code=>vm.runInContext(code,context);
  return {nodes,channelButtons,writes,saved,run,html,response,archiveToggles,listRequests,setList:handle=>{listResponse=handle;},setPost:handle=>{nextPost=handle;},setGet:handle=>{nextGet=handle;},
    async open(id='11111111-1111-4111-8111-111111111111',host='local'){
      await run(`openChat(${JSON.stringify(id)},${JSON.stringify(host)})`);await new Promise(setImmediate);
    },
    save:()=>nodes.get('notify-form').onsubmit({preventDefault(){}}),
    settings:()=>nodes.get('notify-button').onclick()};
}

test('gateway reminders and advanced notification settings are separate expanded groups',async()=>{
  const {html}=await fixture();
  const group=id=>html.match(new RegExp('<section[^>]*aria-labelledby="'+id+'"[\\s\\S]*?</section>'))?.[0]||'';
  const gateway=group('notification-gateway-heading'),advanced=group('notification-advanced-heading');
  assert.ok(gateway&&advanced);
  for(const id of ['security-enabled','address-enabled'])assert.ok(gateway.includes('id="notification-'+id+'"'));
  for(const id of ['address-name','include-title','click-base'])assert.ok(advanced.includes('id="notification-'+id+'"'));
  assert.doesNotMatch(gateway+advanced,/<details|\bhidden\b/);
});

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


test('third-party notification channels save independently, mask secrets and test the selected saved channel',async()=>{
  const ui=await fixture();await ui.open();
  await ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.get('notification-channels-dialog').open,true);
  await ui.channelButtons[2].onclick();
  assert.equal(ui.nodes.get('pushplus-dialog').open,true);
  ui.nodes.get('pushplus-enabled').checked=true;ui.nodes.get('pushplus-token').value='secret';
  await ui.nodes.get('pushplus-form').onsubmit({preventDefault(){}});
  assert.deepEqual(ui.writes[0].body,{pushplusEnabled:true,pushplusToken:'secret',clearPushplusToken:false});
  assert.equal(ui.nodes.get('pushplus-token').value,'');
  assert.equal(ui.nodes.get('pushplus-token').placeholder,'已保存，留空保留');
  await ui.nodes.get('pushplus-test').onclick();assert.equal(ui.writes[1].url,'/api/notifications/channels/pushplus/test');
  ui.nodes.get('notification-channel-back').onclick();await ui.channelButtons[0].onclick();
  assert.equal(ui.nodes.get('notification-server').value,'https://api.day.app');
  ui.nodes.get('pushplus-enabled').checked=true;ui.nodes.get('pushplus-token').value='bark-secret';
  await ui.nodes.get('pushplus-form').onsubmit({preventDefault(){}});
  assert.deepEqual(ui.writes[2].body,{barkEnabled:true,barkKey:'bark-secret',clearBarkKey:false,barkServer:'https://api.day.app'});
  await ui.nodes.get('pushplus-test').onclick();assert.equal(ui.writes[3].url,'/api/notifications/channels/bark/test');
  ui.nodes.get('pushplus-token').value='retry-token';
  ui.setPost(()=>ui.response({error:'Network failure'},400));
  await ui.nodes.get('pushplus-form').onsubmit({preventDefault(){}});
  assert.equal(ui.nodes.get('pushplus-token').value,'retry-token');
  assert.equal(ui.nodes.get('pushplus-error').textContent,'Network failure');
});

test('notification switches save immediately and advanced fields save separately',async()=>{
  const ui=await fixture();await ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.has('notification-common-save'),false);
  ui.nodes.get('notification-address-name').value='Home gateway';ui.nodes.get('notification-click-base').value='https://bridge.example';
  for(const [id,key,checked] of [['security-enabled','securityEnabled',true],['address-enabled','addressEnabled',false],['include-title','includeTitle',true]]){
    const input=ui.nodes.get('notification-'+id);input.checked=checked;await input.onchange();
    assert.deepEqual(ui.writes.at(-1).body,{[key]:checked});assert.equal(input.checked,checked);
    assert.equal(ui.nodes.get('notification-address-name').value,'Home gateway');assert.equal(ui.nodes.get('notification-click-base').value,'https://bridge.example');
  }
  await ui.nodes.get('notification-advanced-save').onclick();
  assert.deepEqual(ui.writes.at(-1).body,{addressName:'Home gateway',clickBase:'https://bridge.example'});
  assert.equal(ui.saved.get('/api/notifications/channels/common').securityEnabled,true);
  assert.equal(ui.saved.get('/api/notifications/channels/common').includeTitle,true);
  assert.equal(ui.nodes.get('notification-channels-dialog').open,true);
});

test('failed immediate saves restore switches and preserve unsaved text',async()=>{
  const ui=await fixture();await ui.nodes.get('pushplus-settings').onclick();
  ui.nodes.get('notification-address-name').value='Unsaved gateway';
  const input=ui.nodes.get('notification-security-enabled');input.checked=true;
  ui.setPost(()=>ui.response({error:'Save failed'},500));await input.onchange();
  assert.equal(input.checked,false);assert.equal(input.disabled,false);assert.equal(ui.writes.length,1);
  assert.equal(ui.nodes.get('notification-address-name').value,'Unsaved gateway');assert.equal(ui.nodes.get('notification-common-error').textContent,'Save failed');
  input.checked=true;await input.onchange();assert.equal(input.checked,true);assert.equal(ui.writes.length,2);
});

test('provider switches save only their enable flag and retain credential drafts',async()=>{
  const ui=await fixture();await ui.nodes.get('pushplus-settings').onclick();await ui.channelButtons[2].onclick();
  ui.nodes.get('pushplus-token').value='Unsaved token';const toggle=ui.nodes.get('pushplus-enabled');toggle.checked=true;await toggle.onchange();
  assert.deepEqual(ui.writes.at(-1).body,{pushplusEnabled:true});assert.equal(ui.nodes.get('pushplus-token').value,'Unsaved token');
  ui.setPost(()=>ui.response({error:'Save failed'},500));toggle.checked=false;await toggle.onchange();
  assert.equal(toggle.checked,true);assert.equal(ui.nodes.get('pushplus-token').value,'Unsaved token');assert.equal(ui.nodes.get('pushplus-error').textContent,'Save failed');
});

for(const channel of ['bark','ntfy','pushplus'])for(const status of [200,500])test(`${channel} controls recover after closing during an immediate save (${status})`,async()=>{
  const ui=await fixture();await ui.nodes.get('pushplus-settings').onclick();
  const button=ui.channelButtons.find(button=>button.dataset.notificationChannel===channel);await button.onclick();
  let finish;ui.setPost(()=>new Promise(resolve=>{finish=resolve;}));
  ui.nodes.get('pushplus-enabled').checked=true;const saving=ui.nodes.get('pushplus-enabled').onchange();
  for(const id of ['pushplus-enabled','pushplus-save','pushplus-test'])assert.equal(ui.nodes.get(id).disabled,true);
  ui.nodes.get('pushplus-dialog').close();finish(ui.response(status===200?{}:{error:'Save failed'},status));await saving;
  await ui.nodes.get('pushplus-settings').onclick();await button.onclick();
  assert.equal(ui.nodes.get('pushplus-dialog').open,true);
  for(const id of ['pushplus-enabled','pushplus-save','pushplus-test'])assert.equal(ui.nodes.get(id).disabled,false,id);
  await ui.nodes.get('pushplus-test').onclick();assert.equal(ui.writes.at(-1).url,'/api/notifications/channels/'+channel+'/test');
});

test('provider list reports configured and enabled states',async()=>{
  const ui=await fixture();
  ui.saved.set('/api/notifications/channels/bark',{barkEnabled:true,hasBarkKey:true});
  ui.saved.set('/api/notifications/channels/ntfy',{enabled:false,topic:'saved-topic'});
  await ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.get('notification-status-bark').textContent,'已开启');
  assert.equal(ui.nodes.get('notification-status-ntfy').textContent,'已关闭');
  assert.equal(ui.nodes.get('notification-status-pushplus').textContent,'未配置');
});

test('common settings wait for a valid read and ignore late responses after close',async()=>{
  const ui=await fixture();let finish;
  ui.setGet(url=>url.endsWith('/common')?new Promise(resolve=>{finish=resolve;}):ui.response({}));
  const opening=ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.get('notification-advanced-save').disabled,true);
  await ui.nodes.get('notification-advanced-save').onclick();
  assert.equal(ui.writes.length,0);
  ui.nodes.get('notification-channels-dialog').close();
  finish(ui.response({securityEnabled:false,addressEnabled:false,addressName:'old',includeTitle:false,clickBase:''}));
  await opening;
  assert.equal(ui.nodes.get('notification-advanced-save').disabled,true);
  ui.saved.set('/api/notifications/channels/common',{securityEnabled:true,addressEnabled:true,addressName:'new',includeTitle:true,clickBase:''});
  await ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.get('notification-address-name').value,'new');
  assert.equal(ui.nodes.get('notification-advanced-save').disabled,false);
});

test('failed common read blocks save and permits explicit retry',async()=>{
  const ui=await fixture();
  ui.setGet(()=>ui.response({error:'Read failed'},500));
  await ui.nodes.get('pushplus-settings').onclick();
  assert.equal(ui.nodes.get('notification-advanced-save').disabled,true);
  assert.equal(ui.nodes.get('notification-common-retry').hidden,false);
  assert.equal(ui.nodes.get('notification-common-error').textContent,'Read failed');
  await ui.nodes.get('notification-advanced-save').onclick();
  assert.equal(ui.writes.length,0);
  await ui.nodes.get('notification-common-retry').onclick();
  assert.equal(ui.nodes.get('notification-advanced-save').disabled,false);
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

const sessionPage=(start,count)=>({sessions:Array.from({length:count},(_,i)=>({id:'chat-'+(start+i),host:'local',title:'Chat '+(start+i),projectKey:'project',projectName:'Project',recency:10000-start-i})),unavailableHosts:[]});

test('refresh retains unchanged chat nodes but renders title and selection updates',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');const first=root.children[0];
  await ui.run('loadList(true)');assert.equal(root.children[0],first);
  ui.run("listRows[0].title='Renamed';renderList()");assert.notEqual(root.children[0],first);assert.equal(root.children[0].children[0].textContent,'Renamed');
  ui.run("currentId='chat-0';renderList()");assert.match(root.children[0].className,/selected/);
  root.replaceChildren();ui.run('renderList()');assert.equal(root.children.length,100);
});

test('chat list scroll loads one page at a time, preserves position and stops at the end',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:12000});
  const replace=root.replaceChildren;root.replaceChildren=function(...items){replace.apply(this,items);this.scrollTop=0;};
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');
  assert.equal(ui.nodes.has('more'),false);assert.equal(ui.listRequests.length,1);
  root.scrollTop=600;root.dispatchEvent({type:'scroll'});assert.equal(ui.listRequests.length,1);
  let finish;ui.setList(()=>new Promise(resolve=>{finish=resolve;}));root.scrollTop=11500;
  for(let i=0;i<5;i++)root.dispatchEvent({type:'scroll'});
  assert.equal(ui.listRequests.length,2);assert.match(ui.listRequests[1],/offset=100/);
  finish(ui.response(sessionPage(100,20)));await new Promise(setImmediate);
  assert.equal(root.children.length,120);assert.equal(root.scrollTop,11500);
  root.dispatchEvent({type:'scroll'});await new Promise(setImmediate);assert.equal(ui.listRequests.length,2);
});

test('failed continuation retains rows and retries the same page on a new scroll',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:12000});
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');
  ui.setList(()=>ui.response({error:'Offline'},503));root.scrollTop=11500;root.dispatchEvent({type:'scroll'});await new Promise(setImmediate);
  assert.equal(root.children.length,100);assert.equal(ui.nodes.get('toast').textContent,'Offline');assert.equal(ui.listRequests.length,2);
  ui.setList(()=>ui.response(sessionPage(100,10)));root.dispatchEvent({type:'scroll'});await new Promise(setImmediate);
  assert.equal(ui.listRequests[2],ui.listRequests[1]);assert.equal(root.children.length,110);
});

test('failed refresh keeps the loaded page available for scroll continuation',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:12000});
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');
  ui.setList(()=>ui.response({error:'Offline'},503));await assert.rejects(ui.run('loadList(true)'),/Offline/);
  assert.equal(root.children.length,100);
  ui.setList(()=>ui.response(sessionPage(100,20)));root.scrollTop=11500;root.dispatchEvent({type:'scroll'});await new Promise(setImmediate);
  assert.match(ui.listRequests[2]||'',/offset=100/);assert.equal(root.children.length,120);
});

test('new filters start at the top, including a retry after their first request fails',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:12000});
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');root.scrollTop=9000;
  await ui.run('loadList(true)');assert.equal(root.scrollTop,9000);
  ui.nodes.get('search').value='new';ui.setList(()=>ui.response({error:'Offline'},503));
  await assert.rejects(ui.run('loadList(true)'),/Offline/);
  root.dispatchEvent({type:'scroll'});assert.equal(ui.listRequests.length,3);
  ui.setList(()=>ui.response(sessionPage(200,20)));await ui.run('loadList(true)');assert.equal(root.scrollTop,0);
  root.scrollTop=1200;ui.nodes.get('archived').checked=true;await ui.run('loadList(true)');assert.equal(root.scrollTop,0);
});

test('horizontal, cancelled and multi-touch gestures do not load a page',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:200});
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');
  for(const kind of ['horizontal','cancel','multi']){
    root.dispatchEvent({type:'touchstart',touches:[{clientX:20,clientY:100}]});
    if(kind==='horizontal')root.dispatchEvent({type:'touchmove',touches:[{clientX:220,clientY:50}]});
    if(kind==='cancel')root.dispatchEvent({type:'touchcancel',touches:[]});
    if(kind==='multi')root.dispatchEvent({type:'touchmove',touches:[{clientX:20,clientY:50},{clientX:40,clientY:50}]});
    root.dispatchEvent({type:'touchmove',touches:[{clientX:20,clientY:40}]});
    root.dispatchEvent({type:'touchend',touches:[]});
  }
  await new Promise(setImmediate);assert.equal(ui.listRequests.length,1);
});

test('changed search and archive conditions block old continuation and discard its late response',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:12000});
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');root.scrollTop=11500;
  ui.nodes.get('search').value='new';root.dispatchEvent({type:'scroll'});assert.equal(ui.listRequests.length,1);
  ui.nodes.get('search').value='';ui.nodes.get('archived').checked=true;root.dispatchEvent({type:'scroll'});assert.equal(ui.listRequests.length,1);
  ui.nodes.get('archived').checked=false;
  let finish;ui.setList(()=>new Promise(resolve=>{finish=resolve;}));root.dispatchEvent({type:'scroll'});
  ui.nodes.get('search').value='new';ui.nodes.get('archived').checked=true;
  ui.setList(()=>ui.response(sessionPage(200,2)));await ui.run('loadList(true)');
  assert.match(ui.listRequests[2],/q=new&offset=0&archived=true/);
  finish(ui.response(sessionPage(100,100)));await new Promise(setImmediate);
  assert.equal(root.children.length,2);assert.equal(root.children[0].dataset.id,'chat-200');
  root.dispatchEvent({type:'scroll'});assert.equal(ui.listRequests.length,3);
});

test('downward gestures load more when collapsed projects cannot scroll, without loading while hidden',async()=>{
  const ui=await fixture(),root=ui.nodes.get('sessions');ui.nodes.get('app').hidden=false;
  Object.assign(root,{clientHeight:400,scrollHeight:200});
  ui.setList(()=>ui.response(sessionPage(0,100)));await ui.run('loadList(true)');
  ui.nodes.get('app').hidden=true;root.dispatchEvent({type:'wheel',deltaY:100});assert.equal(ui.listRequests.length,1);
  ui.nodes.get('app').hidden=false;ui.run("clientNavigation.provider='claude'");root.dispatchEvent({type:'scroll'});assert.equal(ui.listRequests.length,1);
  ui.run("clientNavigation.provider='codex'");root.dispatchEvent({type:'wheel',deltaY:-100});assert.equal(ui.listRequests.length,1);
  ui.setList(()=>ui.response(sessionPage(100,100)));root.dispatchEvent({type:'wheel',deltaY:100});await new Promise(setImmediate);
  assert.equal(ui.listRequests.length,2);assert.equal(root.children.length,200);
  ui.setList(()=>ui.response(sessionPage(200,1)));
  root.dispatchEvent({type:'touchstart',touches:[{clientY:100,clientX:20}]});
  root.dispatchEvent({type:'touchmove',touches:[{clientY:40,clientX:20}]});await new Promise(setImmediate);
  assert.equal(ui.listRequests.length,3);assert.match(ui.listRequests[2],/offset=200/);assert.equal(root.children.length,201);
});
