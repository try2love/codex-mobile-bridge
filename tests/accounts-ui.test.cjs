'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function fixture(desktop=false){
  function node(tag){return {tag,textContent:'',value:'',hidden:false,disabled:false,children:[],attributes:{},classList:{add(){}},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(k,v){this.attributes[k]=v;},closest(){return null;},reset(){},focus(){}};}
  const root=node('div'),requests=[],prompts=[];let confirmed=true,value={accounts:[{id:'a',name:'API <script>',kind:'api',baseUrl:'https://example.test/v1',model:'model'}],activeId:null,switch:{phase:'idle'},canSwitch:true};
  let request=async()=>structuredClone(value),read=async()=>structuredClone(value),timers=[],now=Date.now();
  class Clock extends Date{static now(){return now;}}
  const context=vm.createContext({document:{createElement:node,hidden:false,activeElement:null},root,Date:Clock,crypto:{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},localStorage:{getItem(){return null;},setItem(){}},window:{confirm:message=>{prompts.push(message);return confirmed;},prompt:()=>null}});
  for(const f of ['web/i18n.js','web/accounts.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',f),'utf8'),context);
  const Class=vm.runInContext('AccountsPanel',context),panel=new Class({root,desktop,read:()=>read(),request:v=>{requests.push(v);return request(v);}});
  const all=(n=root)=>[n,...n.children.flatMap(all)];
  return {panel,root,requests,prompts,all,now:()=>now,advance:ms=>now+=ms,hidden:value=>context.document.hidden=value,noRandomUUID:()=>{context.crypto={getRandomValues:bytes=>require("node:crypto").randomFillSync(bytes)};},text:()=>all().map(n=>n.textContent).join('\n'),setValue:v=>value=v,value:()=>value,setRequest:v=>request=v,setRead:v=>read=v,confirm:v=>confirmed=v,
    language:()=>{vm.runInContext("BridgeI18n.setLanguage('en')",context);panel.render();}};
}
test('web exposes saved account selection without enrollment or credential inputs',async()=>{
 const ui=fixture();await ui.panel.refresh();assert.equal(ui.all().some(n=>n.tag==='input'&&n.type!=='checkbox'),false);assert.match(ui.text(),/API <script>/);assert.equal(ui.all().some(n=>n.tag==='script'),false);
 ui.confirm(false);await ui.panel.choose(ui.value().accounts[0]);assert.equal(ui.requests.length,0);
 ui.confirm(true);await ui.panel.choose(ui.value().accounts[0]);assert.deepEqual(Object.keys(ui.requests[0]).sort(),['action','confirmed','id','requestId','tasksConfirmed']);
 assert.equal(ui.requests[0].tasksConfirmed,true);assert.match(ui.prompts[0],/未在网页显示/);
});
test('passwordless web disables switching and explains the desktop alternative',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),canSwitch:false});await ui.panel.refresh();
 assert.equal(ui.all().find(n=>n.textContent==='切换到此接入').disabled,true);assert.match(ui.text(),/免密访问不能切换/);
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
 ui.root.closest=selector=>selector==='[hidden]'?{}:null;
 await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=()=>null;await ui.panel.refresh();assert.equal(ui.requests.length,1);
 assert.equal(ui.requests[0].section,'usage');assert.equal(ui.requests[0].id,'official');
 assert.equal(ui.all(ui.panel.rows.children[0]).find(n=>n.tag==='h3').textContent,'Official');
 assert.equal(ui.all(ui.panel.rows.children[0]).find(n=>n.textContent==='当前接入').disabled,true);
 ui.advance(120000);await ui.panel.refresh();assert.equal(ui.requests.length,1);
 await ui.all().find(n=>n.textContent==='查看剩余额度').onclick();assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].refresh,true);
 ui.advance(180000);await ui.panel.refresh();assert.equal(ui.requests.length,2,'old five-minute deadline is cancelled');
 ui.advance(119000);await ui.panel.refresh();assert.equal(ui.requests.length,2);
 ui.advance(2000);ui.hidden(true);await ui.panel.refresh();assert.equal(ui.requests.length,2);
 ui.hidden(false);await ui.panel.refresh();assert.equal(ui.requests.length,3);
 await ui.panel.refresh();assert.equal(ui.requests.length,3,'status polling does not repeat the quota query');
});
test('quota and reset count precede all account actions',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000,limits:[],resetCredits:{availableCount:2}}}}]});
 await ui.panel.refresh();const nodes=ui.all(ui.panel.rows.children[0]);
 assert.ok(nodes.findIndex(n=>n.textContent==='重置卡 · 2')<nodes.findIndex(n=>n.textContent==='切换到此接入'));
 assert.ok(nodes.some(n=>n.textContent==='查看剩余额度'));assert.ok(!nodes.some(n=>n.textContent==='刷新额度'));
});
test('viewed quotas refresh at five minutes only while the account page is visible',async()=>{
 const ui=fixture();const official={id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',checkedAt:Date.now()/1000-299,limits:[]}}};
 ui.setValue({...ui.value(),accounts:[official]});await ui.panel.refresh();assert.equal(ui.requests.length,0);
 official.details.usage.checkedAt=Date.now()/1000-301;
 ui.root.closest=selector=>selector==='dialog'?{open:false}:null;await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=selector=>selector==='[hidden]'?{}:null;await ui.panel.refresh();assert.equal(ui.requests.length,0);
 ui.root.closest=()=>null;
 ui.setRequest(async()=>{official.details.usage.checkedAt=Date.now()/1000;return structuredClone(ui.value());});
 await ui.panel.refresh();assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].section,'usage');assert.equal(ui.requests[0].refresh,undefined);
 await ui.all().find(n=>n.textContent==='查看剩余额度').onclick();assert.equal(ui.requests.length,2);assert.equal(ui.requests[1].refresh,true);
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
 assert.match(ui.text(),/剩余 75%/);assert.match(ui.text(),/重置卡 · 2/);assert.match(ui.text(),/更新于/);assert.match(ui.text(),/后恢复|等待刷新/);
 if(status==='loading')assert.match(ui.text(),/查询中/);if(status==='error')assert.match(ui.text(),/保留上次结果/);}
 ui.panel.accept({...ui.value(),accounts:[{id:'other',kind:'chatgpt',name:'Different',details:{usage:{status:'loading'}}}]});assert.doesNotMatch(ui.text(),/剩余 75%|重置卡 · 2/);
});
test('current-account card has a direct reset entry and API cards have no subscription quota',()=>{
 const ui=fixture();let chosen;ui.panel.onReset=row=>chosen=row.id;
 ui.panel.accept({...ui.value(),activeId:'official',accounts:[{id:'official',kind:'chatgpt',name:'Official',details:{usage:{status:'ready',limits:[],resetCredits:{availableCount:2}}}}]});
 ui.all().find(n=>n.tag==='button'&&n.textContent==='重置卡 · 2').onclick();assert.equal(chosen,'official');
});
