'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(desktop=false){
  function node(tag){return {tag,textContent:'',value:'',hidden:false,disabled:false,scrollTop:0,children:[],attributes:{},classList:{add(){}},
    append(...children){for(const child of children){child.remove();child.parent=this;child.parentNode=this;this.children.push(child);}},insertBefore(child,before){child.remove();const index=before?this.children.indexOf(before):-1;child.parent=this;child.parentNode=this;this.children.splice(index<0?this.children.length:index,0,child);},replaceChildren(...children){for(const child of [...this.children])child.remove();this.append(...children);if(this.className==='accounts-models')this.scrollTop=0;},setAttribute(k,v){this.attributes[k]=v;},closest(){return null;},reset(){},get isConnected(){for(let parent=this;parent;parent=parent.parent)if(parent===root)return true;return false;},focus(){context.document.activeElement=this;},showModal(){this.open=true;},close(){this.open=false;this.onclose?.();},remove(){if(this.parent){this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;this.parentNode=null;}}};}
  const root=node('div'),requests=[],prompts=[],listeners=new Map();let confirmed=true,value={accounts:[{id:'a',name:'API <script>',kind:'api',baseUrl:'https://example.test/v1',model:'model'}],activeId:null,switch:{phase:'idle'},canSwitch:true},configHidden=false,ancestorHidden=false;
  root.closest=selector=>selector==='[hidden],.client-config-hidden'&&(configHidden||ancestorHidden)?root:null;
  let request=async()=>structuredClone(value),read=async()=>structuredClone(value),readCalls=0,timers=[],now=Date.now();
  class Clock extends Date{static now(){return now;}}
  const context=vm.createContext({document:{createElement:node,body:node('body'),hidden:false,activeElement:null,addEventListener:(name,fn)=>listeners.set(name,[...(listeners.get(name)||[]),fn])},root,Date:Clock,crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout:id=>{if(timers[id-1])timers[id-1].cancelled=true;},localStorage:{getItem(){return null;},setItem(){}},window:{confirm:message=>{prompts.push(message);return confirmed;},prompt:()=>null}});
  for(const f of ['web/shared/i18n.js','web/features/accounts/account.js','web/features/accounts/accounts.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',f),'utf8'),context);
  const Class=vm.runInContext('AccountsPanel',context),panel=new Class({root,desktop,read:()=>{readCalls++;return read();},request:v=>{requests.push(v);return request(v);}});
  const all=(n=root)=>[n,...n.children.flatMap(all)];
  return {panel,root,requests,prompts,all,timers,readCalls:()=>readCalls,focus:node=>context.document.activeElement=node,blur:()=>context.document.activeElement=context.document.body,active:()=>context.document.activeElement,now:()=>now,advance:ms=>now+=ms,hidden:value=>{context.document.hidden=value;for(const fn of listeners.get('visibilitychange')||[])fn();},configHidden:value=>{configHidden=value;for(const fn of listeners.get('bridge-client-config')||[])fn();},ancestorHidden:value=>{ancestorHidden=value;},fireTimer:async ms=>{const timer=timers.find(row=>!row.cancelled&&row.ms===ms);assert.ok(timer,`expected ${ms}ms timer`);timer.cancelled=true;await timer.fn();},noRandomUUID:()=>{context.crypto={getRandomValues:bytes=>require("node:crypto").randomFillSync(bytes)};},text:()=>all().map(n=>n.textContent).join('\n'),setValue:v=>value=v,value:()=>value,setRequest:v=>request=v,setRead:v=>read=v,confirm:v=>confirmed=v,
    language:()=>{vm.runInContext("BridgeI18n.setLanguage('en')",context);panel.render();}};
}
test('native login card survives an empty saved list without enrollment or switching actions',async()=>{
 const ui=fixture(),login={id:'current-login',kind:'chatgpt',name:'ChatGPT',email:'current@example.test',transient:true,details:{usage:{status:'ready',checkedAt:ui.now()/1000,limits:[],resetCredits:{availableCount:2}}}};
 ui.panel.onReset=()=>{};
 ui.setValue({...ui.value(),accounts:[],loginAccount:login,current:{status:'ready',kind:'api',name:'API · custom'}});
 await ui.panel.refresh();
 assert.match(ui.text(),/current@example.test/);assert.match(ui.text(),/已登录/);assert.match(ui.text(),/2 张可用/);
 assert.equal(ui.all().filter(n=>n.tag==='section'&&n.className==='account-bucket').length,1);
 assert.equal(ui.all().some(n=>['切换到此接入','删除','修改'].includes(n.textContent)),false);
 assert.ok(ui.all().find(n=>n.tag==='button'&&n.textContent==='使用重置卡'));
 const modelsButton=ui.all().find(n=>n.textContent==='查看可用模型');assert.equal(modelsButton.parent.className,'accounts-card-footer');
 ui.panel.accept({...ui.value(),accounts:[{...login,transient:false}],loginAccount:{...login,transient:false}});
 assert.equal(ui.all().filter(n=>n.tag==='section'&&n.className==='account-bucket').length,1);
});

test('web exposes saved account selection without enrollment or credential inputs',async()=>{
 const ui=fixture();await ui.panel.refresh();assert.equal(ui.all().some(n=>n.tag==='input'&&n.type!=='checkbox'),false);assert.match(ui.text(),/API <script>/);assert.equal(ui.all().some(n=>n.tag==='script'),false);
 ui.confirm(false);await ui.panel.choose(ui.value().accounts[0]);assert.equal(ui.requests.length,0);
 ui.confirm(true);await ui.panel.choose(ui.value().accounts[0]);assert.deepEqual(Object.keys(ui.requests[0]).sort(),['action','confirmed','id','requestId','tasksConfirmed']);
 assert.equal(ui.requests[0].tasksConfirmed,true);assert.match(ui.prompts[0],/未在网页显示/);
});
test('Codex switch sends local CLI choice only after checkbox selection',async()=>{
 const ui=fixture();await ui.panel.refresh();const row=ui.value().accounts[0];
 assert.match(ui.text(),/共用账号配置；切换后 CLI 也会使用新接入/);
 const box=ui.all().find(n=>n.tag==='input'&&n.type==='checkbox'&&n.parent?.children.some(child=>child.textContent==='同时应用到本机 CLI'));
 assert.ok(box);box.checked=true;box.onchange();await ui.panel.choose(row);
 assert.equal(ui.requests[0].syncLocalCli,true);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} merges imported login and keeps other official accounts and API cards separate`,async()=>{
 const ui=fixture(desktop),usage={status:'ready',checkedAt:ui.now()/1000,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300}]}],resetCredits:{availableCount:2}};
 const login={id:'one',kind:'chatgpt',name:'My login',email:'same@example.test',accountKey:'a'.repeat(64),details:{usage}};
 const other={id:'two',kind:'chatgpt',name:'Other workspace',email:login.email,details:{usage}};
 const api={id:'api',kind:'api',name:'Current API',baseUrl:'https://fixture.test/v1',model:'routed'};
 ui.panel.onReset=()=>{};
 ui.panel.accept({...ui.value(),accounts:[other,api,login],loginAccount:login,activeId:api.id,current:{kind:'api',id:api.id,status:'ready',name:api.name}});
 const cards=ui.panel.rows.children.filter(n=>n.className==='account-bucket');
 assert.equal(cards.length,3);assert.equal(ui.all(cards[0]).find(n=>n.tag==='h3').textContent,login.name);
 assert.equal(ui.all().filter(n=>n.textContent==='已登录').length,1);
 assert.equal(ui.all().filter(n=>n.textContent==='当前接入').length,1);
 assert.equal(ui.all().filter(n=>n.tag==='progress').length,2);
 assert.equal(ui.all().filter(n=>n.tag==='button'&&n.textContent==='使用重置卡').length,2);
 const transient={...login,id:'current-login',transient:true};
 ui.panel.accept({...ui.value(),accounts:[],activeId:null,loginAccount:transient});
 assert.equal(ui.panel.rows.children.filter(n=>n.className==='account-bucket').length,1);
 assert.equal(ui.all().some(n=>['切换到此接入','当前接入'].includes(n.textContent)),false);
});
test('passwordless web disables switching and explains the desktop alternative',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),canSwitch:false});await ui.panel.refresh();
 assert.equal(ui.all().find(n=>n.textContent==='切换到此接入').disabled,true);assert.match(ui.text(),/免密访问不能切换/);
});

test('external Magpie management disables configuration replacement on both surfaces',async()=>{
 for(const desktop of [false,true]){
  const ui=fixture(desktop);ui.setValue({...ui.value(),management:{mode:'auto',external:true,reason:'由 Magpie 管理'}});await ui.panel.refresh();
  assert.equal(ui.all().find(n=>n.textContent==='切换到此接入').disabled,true);assert.match(ui.text(),/由 Magpie 管理/);
  if(desktop){assert.equal(ui.panel.recover.disabled,true);assert.equal(ui.panel.managementMode.value,'auto');}
 }
});
test('switch phases disable repeat actions and recovery only exists on desktop',async()=>{
 const ui=fixture(true);ui.setValue({...ui.value(),switch:{phase:'verifying'}});await ui.panel.refresh();
 assert.equal(ui.all().find(n=>n.textContent==='切换到此接入').disabled,true);assert.equal(ui.panel.submit.disabled,true);assert.equal(ui.panel.recover.disabled,true);
 ui.setValue({...ui.value(),switch:{phase:'interrupted'}});await ui.panel.refresh();assert.equal(ui.panel.recover.disabled,false);
});
test('desktop masks API keys and never prepopulates a saved key',async()=>{
 const ui=fixture(true);await ui.panel.refresh();assert.equal(ui.panel.key.input.type,'password');
 ui.all().find(n=>n.textContent==='修改').onclick();assert.equal(ui.panel.key.input.value,'');assert.equal(ui.panel.url.input.value,'https://example.test/v1');
});
test('official login is opened by trusted desktop IPC, not arbitrary browser navigation',async()=>{
 const ui=fixture(true);ui.setValue({...ui.value(),enrollment:{phase:'waiting',authUrl:'https://auth.openai.com/authorize?fixture=1'}});await ui.panel.refresh();
 assert.equal(ui.all().some(n=>n.tag==='a'),false);await ui.all().find(n=>n.textContent==='打开官方登录').onclick();assert.equal(ui.requests[0].action,'openLogin');
});
test('desktop renames through an inline form without unsupported native prompts',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.all().find(n=>n.textContent==='重命名').onclick();
 assert.equal(ui.panel.renameForm.hidden,false);assert.equal(ui.panel.renameName.input.value,'API <script>');
 ui.panel.renameName.input.value='New name';await ui.panel.renameForm.onsubmit({preventDefault(){}});
 assert.equal(ui.requests[0].action,'rename');assert.equal(ui.requests[0].name,'New name');assert.equal(ui.panel.renameForm.hidden,true);
});
test('late response cannot refill the account selector after logout',async()=>{
 const ui=fixture();let finish;ui.setRead(()=>new Promise(r=>finish=r));const p=ui.panel.refresh();ui.panel.clear();finish(ui.value());await p;assert.equal(ui.panel.value,null);assert.equal(ui.panel.rows.children.length,0);
});
test('English dynamic states retain account names',async()=>{
 const ui=fixture();await ui.panel.refresh();ui.language();assert.match(ui.text(),/Choose a saved account/);assert.match(ui.text(),/API <script>/);
});
test('a read started before an account mutation cannot resurrect a deleted row',async()=>{
 const ui=fixture(true);await ui.panel.refresh();let finish;
 ui.setRead(()=>new Promise(r=>finish=r));const stale=ui.panel.refresh();
 ui.setRequest(async()=>({...ui.value(),accounts:[]}));await ui.panel.perform({action:'delete',id:'a'});
 finish(ui.value());await stale;assert.equal(ui.panel.value.accounts.length,0);
});
test('desktop scanner imports official logins without displaying credentials',async()=>{
 const ui=fixture(true);ui.setValue({...ui.value(),discovery:{source:'/fixture/home',candidates:[{id:'official',kind:'chatgpt',name:'Local login',canImport:true}]}});await ui.panel.refresh();
 await ui.all().find(n=>n.textContent==='导入账号').onclick();assert.equal(ui.requests[0].action,'import');assert.equal(ui.requests[0].candidateId,'official');
 assert.equal(ui.requests[0].apiKey,undefined);
});
test('scanned APIs fetch models using opaque candidate IDs and import the selected model',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.panel.importCandidate({id:'candidate',kind:'api',name:'Local API',baseUrl:'https://example.test/v1',model:''});
 assert.equal(ui.panel.key.input.disabled,true);assert.equal(ui.panel.key.input.value,'');
 ui.setRequest(async()=>({...ui.value(),models:['model-one','model-two']}));await ui.panel.modelsButton.onclick();
 assert.equal(ui.requests[0].candidateId,'candidate');assert.equal(ui.requests[0].apiKey,undefined);assert.equal(ui.panel.modelOptions.hidden,false);
 ui.panel.modelOptions.value='model-two';ui.panel.modelOptions.onchange();await ui.panel.form.onsubmit({preventDefault(){}});
 assert.equal(ui.requests[1].action,'import');assert.equal(ui.requests[1].model,'model-two');assert.equal(ui.requests[1].candidateId,'candidate');
});
test('a delayed model response does not refill a changed API form',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.panel.kind.value='api';ui.panel.url.input.value='https://first.test/v1';ui.panel.key.input.value='fixture-key';
 let finish;ui.setRequest(()=>new Promise(r=>finish=r));const pending=ui.panel.modelsButton.onclick();ui.panel.url.input.value='https://second.test/v1';ui.panel.url.input.oninput();
 finish({...ui.value(),models:['old-provider-model']});await pending;assert.equal(ui.panel.modelOptions.hidden,true);
});
test('model-fetch errors remain visible after status refresh and manual entry stays available',async()=>{
 const ui=fixture(true);await ui.panel.refresh();ui.panel.kind.value='api';ui.panel.kind.onchange();ui.setRequest(async()=>{throw Error('上游拒绝访问，请检查 API Key 和模型列表权限');});
 await ui.panel.modelsButton.onclick();await ui.panel.refresh();assert.match(ui.panel.error.textContent,/上游拒绝/);assert.equal(ui.panel.model.wrap.hidden,false);
});
test('LAN HTTP switching works without crypto.randomUUID and reports failures',async()=>{
 const ui=fixture();await ui.panel.refresh();ui.noRandomUUID();
 ui.setRequest(async()=>{throw Error("Error invoking remote method 'bridge:accounts': Error: task blocked");});
 await ui.panel.choose(ui.value().accounts[0]);assert.equal(ui.requests.length,1);assert.match(ui.requests[0].requestId,/^[0-9a-f-]{36}$/);
 assert.equal(ui.panel.error.textContent,'task blocked');
});
test('actual current account is named and cannot be selected again',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),activeId:'a',current:{status:'ready',id:'a',kind:'api',name:'Imported current'}});await ui.panel.refresh();
 assert.match(ui.panel.current.textContent,/Imported current/);assert.equal(ui.all().find(n=>n.textContent==='当前接入').disabled,true);
});
test('official rows show compact quota and reset count, while API rows only offer models',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),accounts:[...ui.value().accounts,{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300}]}],resetCredits:{availableCount:2}},models:{status:'ready',models:[{id:'model-1',name:'Model one'}]}}}]});
 await ui.panel.refresh();assert.match(ui.text(),/剩余 75%/);assert.match(ui.text(),/重置卡 · 2/);assert.equal(ui.all().filter(n=>n.tag==='progress').length,1);
 assert.equal(ui.all().filter(n=>n.textContent==='查看可用模型').length,2);
 ui.panel.expandedModels.add('official');ui.panel.render();assert.match(ui.text(),/Model one · model-1/);
});
test('blockers show the concrete running or pending chat instead of a generic warning',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),blockers:[{reason:'approval',title:'Review fixture'}]});await ui.panel.refresh();assert.match(ui.text(),/Review fixture/);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} automatically loads initial quota and restarts five minutes after a manual query`,async()=>{
 const ui=fixture(desktop),official={id:'official',kind:'chatgpt',name:'Official'};
 ui.setValue({...ui.value(),activeId:'official',accounts:[...ui.value().accounts,official]});
 ui.setRequest(async()=>{official.details={usage:{status:'ready',checkedAt:ui.now()/1000,limits:[]}};return structuredClone(ui.value());});
 ui.ancestorHidden(true);
 await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.ancestorHidden(false);await ui.panel.refresh();assert.equal(ui.requests.length,1);
 assert.equal(ui.requests[0].section,'usage');assert.equal(ui.requests[0].id,'official');
 assert.equal(ui.all(ui.panel.rows.children[0]).find(n=>n.tag==='h3').textContent,'Official');
 assert.equal(ui.all(ui.panel.rows.children[0]).find(n=>n.textContent==='当前接入').disabled,true);
 ui.advance(120000);await ui.panel.refresh();assert.equal(ui.requests.length,1);
 await ui.all().find(n=>n.attributes['aria-label']==='查看剩余额度').onclick();assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].refresh,true);
 ui.advance(180000);await ui.panel.refresh();assert.equal(ui.requests.length,2,'old five-minute deadline is cancelled');
 ui.advance(119000);await ui.panel.refresh();assert.equal(ui.requests.length,2);
 ui.advance(2000);ui.hidden(true);await ui.panel.refresh();assert.equal(ui.requests.length,2);
 ui.hidden(false);await ui.panel.refresh();assert.equal(ui.requests.length,3);
 await ui.panel.refresh();assert.equal(ui.requests.length,3,'status polling does not repeat the quota query');
});
test('quota and reset count precede account switching',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000,limits:[],resetCredits:{availableCount:2}}}}]});
 await ui.panel.refresh();const nodes=ui.all(ui.panel.rows.children[0]);
 assert.ok(nodes.findIndex(n=>n.textContent==='重置卡 · 2 张可用')<nodes.findIndex(n=>n.textContent==='切换到此接入'));
 const refresh=nodes.find(n=>n.attributes['aria-label']==='查看剩余额度');assert.equal(refresh.textContent,'');assert.match(refresh.innerHTML,/<svg[^>]*viewBox="0 0 24 24"/);assert.equal(refresh.parent.className,'accounts-usage-controls');assert.equal(refresh.parent.parent,nodes.find(n=>n.className==='accounts-card-footer'));assert.ok(!nodes.some(n=>n.textContent==='刷新额度'));
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} groups reset count, next usable expiry and the selected account action`,()=>{
 const ui=fixture(desktop);ui.panel.onReset=()=>{};let chosen;ui.panel.openReset=row=>chosen=row.id;
 const now=ui.now()/1000,expiry=now+86400,card=(id,expiresAt,extra={})=>({id,expiresAt,status:'available',resetType:'codexRateLimits',...extra});
 const official={id:'official',kind:'chatgpt',name:'Official',email:'official@test',details:{usage:{status:'ready',updatedAt:now,limits:[],subscription:{periodEndsAt:now+86400,source:'online'},resetCredits:{availableCount:2,credits:[card('later',expiry+86400),card('expired',now-1),card('other',now+10,{resetType:'other'}),card('used',now+20,{status:'used'}),card('next',expiry)]}}}};
 ui.panel.accept({...ui.value(),accounts:[...ui.value().accounts,official]});
 const nodes=ui.all(ui.panel.rows.children[1]),group=nodes.find(n=>n.className==='accounts-reset-summary'),grouped=ui.all(group);
 assert.match(grouped.map(n=>n.textContent).join('\n'),/重置卡 · 2 张可用/);
 const time=grouped.find(n=>n.tag==='time');assert.equal(time.dateTime,new Date(expiry*1000).toISOString());assert.equal(time.title,new Date(expiry*1000).toLocaleString('zh-CN'));
 const use=grouped.find(n=>n.tag==='button');assert.equal(use.parent,group);use.onclick();assert.equal(chosen,'official');assert.equal(ui.requests.length,0);
 assert.equal(ui.all(ui.panel.rows.children[0]).some(n=>n.className==='accounts-reset-summary'),false);
 const note=nodes.find(n=>n.className==='account-muted account-subscription-note'),info=nodes.find(n=>n.attributes['aria-label']==='订阅日期说明');assert.equal(note.hidden,true);info.onclick();assert.equal(note.hidden,false);assert.equal(info.attributes['aria-expanded'],'true');
 assert.equal(info.parent.parent.className,'accounts-name');assert.equal(info.textContent,'订阅周期至 '+new Date(expiry*1000).toLocaleDateString('zh-CN',{month:'short',day:'numeric'}));assert.ok(info.title.includes(new Date(expiry*1000).toLocaleString('zh-CN')));assert.ok(!nodes.some(n=>n.textContent==='ⓘ'));
 ui.panel.render();assert.equal(ui.all().find(n=>n.className==='account-muted account-subscription-note').hidden,false);
 const updated=nodes.find(n=>n.className==='account-updated account-muted'),refresh=nodes.find(n=>n.attributes['aria-label']==='查看剩余额度');assert.equal(updated.textContent,new Date(now*1000).toLocaleString('zh-CN',{hour:'2-digit',minute:'2-digit'})+' 更新');assert.equal(updated.parent,refresh.parent);assert.equal(updated.parent.parent.className,'accounts-card-footer');assert.equal(updated.parent.children.at(-1),refresh);assert.equal(updated.title,new Date(now*1000).toLocaleString('zh-CN'));
 ui.language();assert.match(ui.text(),/Usage resets · 2 available/);assert.match(ui.text(),/Subscription period ends:/);
});

test('official credits retain zero and unlimited balances and omit missing values',()=>{
 const ui=fixture();ui.panel.accept({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',limits:[{name:'Missing',credits:{balance:null}},{name:'Empty',credits:{balance:' '}},{name:'Zero',credits:{balance:0}},{name:'Paid',credits:{balance:'12.5'}},{name:'Unlimited',credits:{unlimited:true}}]}}}]});
 assert.doesNotMatch(ui.text(),/Missing · Credits:|Empty · Credits:/);assert.match(ui.text(),/Zero · Credits: 0/);assert.match(ui.text(),/Paid · Credits: 12.5/);assert.match(ui.text(),/Unlimited · Credits: 不限量/);
});
test('viewed quotas refresh at five minutes only while the account page is visible',async()=>{
 const ui=fixture();const official={id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000-299,limits:[]}}};
 ui.setValue({...ui.value(),accounts:[official]});await ui.panel.refresh();assert.equal(ui.requests.length,0);
 official.details.usage.checkedAt=Date.now()/1000-301;
 ui.root.closest=selector=>selector==='dialog'?{open:false}:null;await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=selector=>selector==='[hidden],.client-config-hidden'?{}:null;await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=()=>null;
 ui.setRequest(async()=>{official.details.usage.checkedAt=Date.now()/1000;return structuredClone(ui.value());});
 await ui.panel.refresh();assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].section,'usage');assert.equal(ui.requests[0].refresh,undefined);
 await ui.all().find(n=>n.attributes['aria-label']==='查看剩余额度').onclick();assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].refresh,true);
});

test('desktop Codex configuration stops polling while hidden and resumes on return',async()=>{
 const ui=fixture(true);await ui.panel.refresh();assert.ok(ui.timers.some(row=>row.ms===300000&&!row.cancelled));
 ui.configHidden(true);assert.equal(ui.panel.visible(),false);assert.equal(ui.timers.some(row=>!row.cancelled),false);
 const before=ui.readCalls();ui.configHidden(false);await new Promise(setImmediate);
 assert.equal(ui.readCalls(),before+1);assert.ok(ui.timers.some(row=>row.ms===300000&&!row.cancelled));
});

test('leaving during a quota batch drops its response and skips later accounts',async()=>{
 const ui=fixture(true),rows=['first','second'].map(id=>({id,kind:'chatgpt',name:id}));
 ui.setValue({...ui.value(),accounts:rows});let finish;
 ui.setRequest(()=>new Promise(resolve=>{finish=resolve;}));
 const pending=ui.panel.refresh();await new Promise(setImmediate);assert.equal(ui.requests.length,1);
 ui.configHidden(true);finish({...ui.value(),accounts:rows.map(row=>({...row,details:{usage:{status:'ready',checkedAt:ui.now()/1000,limits:[]}}}))});
 await pending;assert.equal(ui.requests.length,1);assert.equal(ui.panel.value.accounts[0].details,undefined);
 assert.equal(ui.timers.some(row=>!row.cancelled),false);
});

test('an old quota response is ignored across hide and return, then a new visit reloads',async()=>{
 const ui=fixture(true),row={id:'one',kind:'chatgpt',name:'one'};ui.setValue({...ui.value(),accounts:[row]});let finish;
 ui.setRequest(()=>new Promise(resolve=>{finish=resolve;}));
 const pending=ui.panel.refresh();await new Promise(setImmediate);ui.configHidden(true);ui.configHidden(false);
 ui.setRequest(async()=>({...ui.value(),accounts:[{...row,details:{usage:{status:'ready',checkedAt:ui.now()/1000,limits:[]}}}]}));
 finish({...ui.value(),accounts:[{...row,name:'stale'}]});await pending;await new Promise(setImmediate);
 assert.equal(ui.requests.length,2);assert.equal(ui.panel.value.accounts[0].name,'one');
});

test('a list read from a previous visit cannot replace the reopened account snapshot',async()=>{
 const ui=fixture(true);await ui.panel.refresh();let finish,changes=0;ui.panel.onChanged=()=>changes++;
 ui.setRead(()=>new Promise(resolve=>{finish=resolve;}));const old=ui.panel.refresh();
 ui.configHidden(true);ui.configHidden(false);
 ui.setRead(async()=>({...ui.value(),activeId:'fresh'}));
 finish({...ui.value(),activeId:'stale'});await old;await new Promise(setImmediate);
 assert.equal(ui.panel.value.activeId,'fresh');assert.equal(changes,1);
});

test('a hidden passive account read can update identity without querying quota',async()=>{
 const ui=fixture(true),official={id:'official',kind:'chatgpt',name:'Official'};
 ui.configHidden(true);ui.setValue({...ui.value(),activeId:'official',accounts:[official]});
 await ui.panel.refresh();assert.equal(ui.panel.value.activeId,'official');
 assert.equal(ui.requests.length,0);assert.equal(ui.timers.some(row=>!row.cancelled),false);
});

test('returning during an account write refreshes the panel after the write settles',async()=>{
 const ui=fixture(true);await ui.panel.refresh();let finish;
 ui.setRequest(()=>new Promise(resolve=>{finish=resolve;}));const pending=ui.panel.perform({action:'rename',id:'a',name:'new'});
 ui.configHidden(true);ui.configHidden(false);const before=ui.readCalls();
 const updated={...ui.value(),accounts:[{...ui.value().accounts[0],name:'new'}]};ui.setValue(updated);finish(updated);await pending;await new Promise(setImmediate);
 assert.equal(ui.readCalls(),before+1);assert.equal(ui.panel.value.accounts[0].name,'new');
});

test('first account read failure stays visible and retries after ten seconds',async()=>{
 const ui=fixture();ui.setRead(async()=>{throw Error('offline');});await ui.panel.refresh();
 assert.match(ui.panel.error.textContent,/offline/);assert.equal(ui.panel.value,null);
 assert.ok(ui.timers.some(row=>row.ms===10000&&!row.cancelled));
 ui.setRead(async()=>structuredClone(ui.value()));await ui.fireTimer(10000);
 assert.equal(ui.readCalls(),2);assert.ok(ui.panel.value);assert.equal(ui.panel.error.textContent,'');
});


test('desktop and web can ignore only the selected unknown submission',async()=>{
 for(const desktop of [false,true]){
  const ui=fixture(desktop);ui.setValue({...ui.value(),blockers:[{reason:'unknown',title:'Chat',id:'thread',submissionId:'message',host:'local'},{reason:'running',title:'Busy',id:'busy'}]});
  await ui.panel.refresh();assert.equal(ui.all().filter(n=>n.textContent==='忽略').length,1);
  ui.confirm(false);await ui.all().find(n=>n.textContent==='忽略').onclick();assert.equal(ui.requests.length,0);
  ui.confirm(true);await ui.all().find(n=>n.textContent==='忽略').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.requests[0])),{action:'ignoreSubmission',threadId:'thread',submissionId:'message',host:'local'});
 }
});

test('loading and failed refresh retain percentages, cards and the last successful time',()=>{
 const ui=fixture();const usage={status:'ready',updatedAt:1900000000,limits:[{name:'Codex',windows:[{remainingPercent:75,windowDurationMins:300,resetsAt:2000000000}]}],resetCredits:{availableCount:2}};
 for(const status of ['ready','loading','error']){ui.panel.accept({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{...usage,status,error:status==='error'?'查询失败':undefined}}}]});
 assert.match(ui.text(),/剩余 75%/);assert.match(ui.text(),/重置卡 · 2/);assert.match(ui.all().find(n=>n.className==='account-updated account-muted').textContent,/更新/);assert.match(ui.text(),/后恢复|等待刷新/);
 if(status==='loading')assert.match(ui.text(),/查询中/);if(status==='error')assert.match(ui.text(),/保留上次结果/);}
 ui.panel.accept({...ui.value(),accounts:[{id:'other',kind:'chatgpt',name:'Different',details:{usage:{status:'loading'}}}]});assert.doesNotMatch(ui.text(),/剩余 75%|重置卡 · 2/);
});
test('saved official card has a direct reset entry and API cards have no subscription quota',()=>{
 const ui=fixture();let chosen;ui.panel.openReset=row=>chosen=row.id;
 ui.panel.accept({...ui.value(),activeId:'official',accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',limits:[],resetCredits:{availableCount:2}}}}]});
 ui.all().find(n=>n.tag==='button'&&n.textContent==='使用重置卡'&&!n.hidden).onclick();assert.equal(chosen,'official');
});

test('active official account exposes reset entry before quota details load',()=>{
 const ui=fixture();let chosen;ui.panel.openReset=row=>chosen=row.id;
 ui.panel.accept({...ui.value(),activeId:'official',current:{kind:'chatgpt',status:'ready',id:'official'},accounts:[{id:'official',kind:'chatgpt',name:'Official'}]});
 const reset=ui.all().find(n=>n.tag==='button'&&n.textContent==='使用重置卡'&&!n.hidden);assert.ok(reset);reset.onclick();assert.equal(chosen,'official');
});


test('both saved-account views treat stale subscription claims as unknown',()=>{
 for(const desktop of [false,true]){
  const ui=fixture(desktop);
  const accounts=[1790147934,1788250708].map((periodEndsAt,i)=>({id:'official-'+i,kind:'chatgpt',name:'Account '+i,details:{usage:{status:'ready',checkedAt:ui.now()/1000,updatedAt:ui.now()/1000,limits:[],subscription:{periodEndsAt,source:'login'}}}}));
  ui.panel.accept({...ui.value(),accounts});
  assert.equal(ui.all().filter(n=>n.textContent==='订阅有效期暂未确认').length,2);
  assert.doesNotMatch(ui.text(),/当前订阅周期|已到记录日期|后结束/);
  accounts[0].details.usage.subscription.periodEndsAt=ui.now()/1000+86400;ui.panel.render();
  assert.match(ui.text(),/登录记录中的订阅日期（非实时）：/);assert.match(ui.text(),/请以 ChatGPT 订阅页面为准/);
  ui.language();assert.match(ui.text(),/Subscription date in login record \(not live\):/);
  ui.advance(86401000);ui.panel.render();assert.equal(ui.all().filter(n=>n.textContent==='Subscription end date is unconfirmed').length,2);
 }
});

test('every saved official account opens its own reset dialog without switching',async()=>{
 const ui=fixture();
 const saved={...ui.value(),activeId:'api',current:{kind:'api',status:'ready'},accounts:[...ui.value().accounts,{id:'one',kind:'chatgpt',name:'One'},{id:'two',kind:'chatgpt',name:'Two'}]};
 ui.panel.accept(saved);
 assert.equal(ui.all().filter(n=>n.textContent==='使用重置卡').length,2);
 ui.setRequest(async value=>value.action==='account'?{visible:true,email:value.id+'@test',accountKey:'a'.repeat(64),limits:[],canReset:true,resetCredits:{availableCount:0,credits:[]}}:saved);
 await ui.panel.openReset(saved.accounts[2]);
 assert.equal(ui.requests[0].action,'account');assert.equal(ui.requests[0].id,'two');assert.equal(ui.requests[0].operation,'read');
 assert.equal(ui.panel.resetPanel.value.email,'two@test');assert.ok(ui.panel.resetDialog.open);
 assert.equal(ui.panel.resetPanel.resetOnly,true);const dialogText=ui.all(ui.panel.resetDialog).map(n=>n.textContent).join('\n');assert.doesNotMatch(dialogText,/订阅|额度|更新于/);
 assert.equal(ui.requests.some(r=>r.action==='switch'),false);
 ui.panel.resetDialog.close();assert.equal(ui.panel.resetPanel,null);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} reset inventory appears immediately and use waits for the account read`,async()=>{
 const ui=fixture(desktop);ui.panel.onReset=()=>{};
 const cards={availableCount:2,credits:[{id:'credit',title:'Cached reset',status:'available',resetType:'codexRateLimits',expiresAt:null}]};
 const row={id:'official',kind:'chatgpt',name:'Official',email:'saved@test',details:{usage:{status:'ready',resetCredits:cards}}};
 ui.panel.accept({...ui.value(),accounts:[row]});let resolveRead;
 ui.setRequest(()=>new Promise(resolve=>resolveRead=resolve));const opening=ui.panel.openReset(row),panel=ui.panel.resetPanel;
 const text=()=>ui.all(ui.panel.resetDialog).map(n=>n.textContent).join('\n');
 assert.match(text(),/Cached reset/);assert.match(text(),/可用数量：2/);assert.match(text(),/查询中/);assert.doesNotMatch(text(),/此账号暂不可使用/);
 assert.equal(panel.loading,true);assert.equal(panel.canRedeem('credit'),false);await panel.redeem('credit');assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].refresh,false);
 resolveRead({visible:true,email:'saved@test',accountKey:'a'.repeat(64),limits:[],canReset:true,resetCredits:cards});await opening;
 assert.equal(panel.loading,false);assert.equal(panel.canRedeem('credit'),true);assert.doesNotMatch(text(),/查询中/);
 ui.panel.resetDialog.close();
});

test('first reset load shows progress and a closed dialog cannot be repopulated by a late read',async()=>{
 const ui=fixture(),row={id:'official',kind:'chatgpt',name:'Official'};ui.panel.onReset=()=>{};ui.panel.accept({...ui.value(),accounts:[row]});
 let resolveRead;ui.setRequest(()=>new Promise(resolve=>resolveRead=resolve));const opening=ui.panel.openReset(row),panel=ui.panel.resetPanel;
 const nodes=ui.all(ui.panel.resetDialog);assert.ok(nodes.some(n=>n.textContent==='查询中…'&&n.attributes.role==='status'));
 ui.panel.resetDialog.close();resolveRead({visible:true,email:'saved@test',accountKey:'a'.repeat(64),limits:[],canReset:true,resetCredits:{availableCount:0,credits:[]}});await opening;
 assert.equal(ui.panel.resetPanel,null);assert.equal(panel.value,null);assert.equal(panel.root.children.length,0);
});

test('desktop updates distinguish remote version checks from computer-side installation',async()=>{
 for(const desktop of [false,true]){
  const ui=fixture(desktop);ui.setValue({...ui.value(),desktopUpdate:{state:'available',canRequest:true,currentBuild:'1',currentVersion:'1',targetVersion:'2'}});await ui.panel.refresh();
  assert.match(ui.text(),/下载和安装仍需在电脑端确认/);
  await ui.all().find(n=>n.textContent==='查看 Codex 新版本').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.requests)),[{action:'checkDesktopUpdate'}]);assert.equal(ui.prompts.length,0);
  ui.confirm(false);await ui.all().find(n=>n.textContent==='打开电脑端更新窗口').onclick();assert.equal(ui.requests.length,1);
  ui.confirm(true);await ui.all().find(n=>n.textContent==='打开电脑端更新窗口').onclick();assert.equal(ui.requests[1].action,'requestDesktopUpdate');assert.equal(ui.requests[1].tasksConfirmed,true);
  ui.language();assert.match(ui.text(),/installing still require confirmation on the computer/);
 }
});


test('native update completion is shown only after the installed build is verified',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),desktopUpdate:{state:'needsDesktop',installation:{state:'pending',previousBuild:'100',currentBuild:'101'}}});await ui.panel.refresh();assert.doesNotMatch(ui.text(),/已核验电脑端安装版本已更新/);
 ui.setValue({...ui.value(),desktopUpdate:{state:'checked',installation:{state:'verified',previousBuild:'100',currentBuild:'101'}}});await ui.panel.refresh();assert.match(ui.text(),/已核验电脑端安装版本已更新 · 100 → 101/);
 ui.language();assert.match(ui.text(),/verified as updated · 100 → 101/);
});

for(const desktop of [false,true])test(`${desktop?'desktop':'web'} hides successful switch status and preserves progress and errors`,async()=>{
 const ui=fixture(desktop);
 for(const phase of ['complete','restored']){
  ui.setValue({...ui.value(),switch:{phase}});await ui.panel.refresh();assert.equal(ui.panel.status.hidden,true);
  ui.setValue({...ui.value(),switch:{phase,error:'Review required'}});await ui.panel.refresh();assert.equal(ui.panel.status.hidden,false);assert.match(ui.panel.status.textContent,/Review required/);
 }
 for(const phase of ['preparing','verifying','failed','interrupted']){
  ui.setValue({...ui.value(),switch:{phase}});await ui.panel.refresh();assert.equal(ui.panel.status.hidden,false);
 }
});

test('account models retain cached names and efforts when explicit refresh fails',async()=>{
 const ui=fixture(),account={...ui.value().accounts[0],details:{models:{status:'error',error:'Refresh failed',cache:{updatedAt:1000},models:[{id:'route/model',name:'Concrete model',efforts:['low','high']}]}}};
 ui.setValue({...ui.value(),accounts:[account]});ui.panel.expandedModels.add(account.id);await ui.panel.refresh();assert.match(ui.text(),/Concrete model/);assert.match(ui.text(),/low.*high/);assert.match(ui.text(),/Refresh failed/);
 await ui.all().find(node=>node.tag==='button'&&node.textContent==='刷新模型').onclick();assert.equal(ui.requests.at(-1).refresh,true);assert.equal(ui.requests.at(-1).section,'models');
});

test('idle account reads do not poll every two seconds; pending account work does',async()=>{
 const ui=fixture(true),delay=()=>ui.timers[ui.panel.timer-1]?.ms;
 await ui.panel.refresh();assert.equal(delay(),300000);const idle=ui.timers[ui.panel.timer-1];assert.equal(ui.readCalls(),1);
 ui.advance(2000);assert.equal(ui.readCalls(),1);
 ui.setValue({...ui.value(),enrollment:{phase:'waiting'}});await idle.fn();assert.equal(ui.readCalls(),2);assert.equal(delay(),2000);
 const pending=ui.timers[ui.panel.timer-1];ui.setValue({...ui.value(),enrollment:{phase:'complete'}});await pending.fn();assert.equal(ui.readCalls(),3);assert.equal(delay(),300000);
 ui.setValue({...ui.value(),enrollment:{phase:'complete'},switch:{phase:'verifying'}});await ui.panel.refresh();assert.equal(delay(),2000);
 ui.setValue({...ui.value(),switch:{phase:'idle'},accounts:[{...ui.value().accounts[0],details:{models:{status:'loading'}}}]});await ui.panel.refresh();assert.equal(delay(),2000);
 ui.setValue({...ui.value(),accounts:[{...ui.value().accounts[0],details:{models:{status:'ready',models:[]}}}]});await ui.panel.refresh();assert.notEqual(delay(),2000);
 ui.setValue({...ui.value(),switch:{phase:'interrupted'}});await ui.panel.refresh();assert.equal(delay(),300000);
});

test('Codex model scroller survives status reads and model timestamp changes',async()=>{
 const ui=fixture(),row={...ui.value().accounts[0],details:{models:{status:'ready',cache:{updatedAt:1000},models:[{id:'model-a'},{id:'model-b'}]}}};
 ui.setValue({...ui.value(),accounts:[row]});ui.panel.expandedModels.add(row.id);await ui.panel.refresh();
 const box=ui.all().find(node=>node.className==='accounts-models'),card=box.parent;box.scrollTop=74;
 ui.setValue({...ui.value(),switch:{phase:'verifying'},accounts:[{...row,details:{models:{...row.details.models,cache:{updatedAt:2000}}}}]});await ui.panel.refresh();
 assert.equal(ui.all().find(node=>node.className==='accounts-models'),box);assert.equal(box.parent,card);assert.equal(box.scrollTop,74);assert.match(ui.text(),/model-b/);
 let finish;ui.setRequest(()=>new Promise(resolve=>finish=resolve));ui.focus(box.refresh);const pending=box.refresh.onclick();ui.blur();finish(ui.value());await pending;assert.equal(ui.active(),box.refresh);
 ui.setValue({...ui.value(),accounts:[{...row,accountKey:'new-profile'}],switch:{phase:'idle'}});await ui.panel.refresh();
 assert.notEqual(ui.all().find(node=>node.className==='accounts-models'),box);
});
