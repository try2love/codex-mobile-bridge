'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({desktop=false,provider='deepseek',toolbar=false}={}){
  function node(tag){return {tag,children:[],dataset:{},textContent:'',hidden:false,disabled:false,scrollTop:0,attributes:{},classList:{add(){}},append(...nodes){for(const child of nodes){child.remove();child.parent=this;child.parentNode=this;this.children.push(child);}},insertBefore(child,before){child.remove();const index=before?this.children.indexOf(before):-1;child.parent=this;child.parentNode=this;this.children.splice(index<0?this.children.length:index,0,child);},replaceChildren(...nodes){for(const child of [...this.children])child.remove();this.append(...nodes);if(this.className==='accounts-models')this.scrollTop=0;},remove(){if(this.parent){this.parent.children=this.parent.children.filter(value=>value!==this);this.parent=null;this.parentNode=null;}},get isConnected(){for(let parent=this;parent;parent=parent.parent)if(parent===root)return true;return false;},focus(){document.activeElement=this;},setAttribute(key,value){this.attributes[key]=value;},closest(){return null;},querySelectorAll(selector){return this.children.flatMap(child=>[...(selector==='[data-i18n]'&&child.dataset.i18n?[child]:[]),...child.querySelectorAll(selector)]);}};}
  const root=node('div'),outerToolbar=toolbar?node('div'):null,listeners={},timers=new Map(),calls=[],prompts=[],updates=[];let time=1000000,tid=0,visible=true,confirmed=true,value={accounts:[],activeId:null,canSwitch:true},handler=async()=>value,readHandler=async()=>value;
  const document={hidden:false,body:node('body'),activeElement:null,documentElement:{},createElement:node,querySelectorAll:()=>[],addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn),removeEventListener:(type,fn)=>listeners[type]=listeners[type].filter(row=>row!==fn),dispatchEvent(event){for(const fn of listeners[event.type]||[])fn(event);}};
  class Clock extends Date{static now(){return time;}}
  const context=vm.createContext({document,Date:Clock,CustomEvent:class{constructor(type){this.type=type;}},localStorage:{getItem(){return null;},setItem(){}},window:{confirm(message){prompts.push(message);return confirmed;}},setTimeout(fn,ms){timers.set(++tid,{fn,due:time+ms});return tid;},clearTimeout:id=>timers.delete(id)});
  for(const path of ['web/shared/i18n.js','web/features/clients/client-lifecycle.js','web/features/accounts/api-editor.js','web/features/accounts/client-accounts.js'])vm.runInContext(fs.readFileSync(path,'utf8'),context);
  const Panel=vm.runInContext('DesktopClientAccountsPanel',context),panel=new Panel({root,provider,desktop,toolbar:outerToolbar,read:()=>{calls.push({action:'list'});return readHandler();},request:body=>{calls.push(body);return handler(body);},readModels:(refresh=false)=>handler({action:'models',refresh}),visible:()=>visible,onUpdate:result=>updates.push(result)});
  const all=node=>[node,...node.children.flatMap(all)];
  return {panel,root,outerToolbar,calls,prompts,updates,timers,focus:node=>document.activeElement=node,blur:()=>document.activeElement=document.body,active:()=>document.activeElement,setValue:next=>value=next,setHandler:next=>handler=next,setRead:next=>readHandler=next,setVisible:next=>visible=next,confirm:next=>confirmed=next,advance:async ms=>{time+=ms;for(const [id,timer]of [...timers])if(timer.due<=time){timers.delete(id);await timer.fn();}},tick:ms=>time+=ms,now:()=>time,all:()=>all(root),text:()=>all(root).map(row=>row.textContent).join('\n'),english:()=>vm.runInContext("BridgeI18n.setLanguage('en')",context)};
}
const row=(id,kind='account',usage)=>({id,name:id,kind,usage});
const snapshot=(accounts,activeIds=['current'])=>({accounts,activeId:activeIds[0],activeIds,current:{name:'Current desktop access'},canSwitch:true});
test('native API editors share Codex fold placement and fetch models before saving',async()=>{
  for(const provider of ['claude','deepseek','zcode']){
    const ui=fixture({provider,desktop:true}),formats=provider==='zcode'?[{value:'openai-responses',label:'OpenAI Responses'}]:undefined;
    const value={...snapshot([]),capabilities:{addApi:true,quota:false,apiFormats:formats}};ui.panel.accept(value);
    assert.equal(ui.panel.apiDetails.open,undefined);assert.equal(ui.panel.apiDetails.parentNode,ui.panel.management);assert.equal(ui.panel.scanDetails.parentNode,ui.panel.management);assert.equal(ui.panel.importName.parentNode,ui.panel.scanDetails);
    ui.panel.addApiButton.onclick();ui.panel.editName.input.value='Fixture';ui.panel.editUrl.input.value='https://fixture.invalid/v1';ui.panel.editKey.input.value='fixture-key';
    const calls=[];ui.setHandler(async request=>{calls.push({...request});return request.action==='models-draft'?{models:['first','second']}:value;});
    await ui.panel.apiEditor.fetchModels.onclick();
    assert.equal(calls.length,1);assert.equal(calls[0].action,'models-draft');assert.equal(ui.panel.apiEditor.modelSelect.hidden,false);
    if(provider==='deepseek'){
      assert.equal(calls[0].baseUrl,undefined);assert.equal(calls[0].apiFormat,undefined);assert.equal(ui.panel.editUrl.wrap.hidden,true);assert.equal(ui.panel.editModels.wrap.hidden,true);
    }else{
      assert.equal(ui.panel.editModels.wrap.htmlFor,ui.panel.editModels.input.id);assert.equal(ui.panel.apiEditor.modelCaption.htmlFor,ui.panel.editModels.input.id);
      assert.equal(ui.panel.editModels.input.value,'first');ui.panel.apiEditor.modelSelect.value='second';ui.panel.apiEditor.modelSelect.onchange();assert.equal(ui.panel.editModels.input.value,'first\nsecond');
    }
    await ui.panel.saveEditor();assert.equal(calls[1].action,'save-api');assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.panel.apiDetails.open,false);
    if(provider==='deepseek')assert.equal(calls[1].models,undefined);else assert.equal(calls[1].models.join(','),'first,second');
  }
});
test('draft model responses cannot refill canceled or replaced native API editors',async()=>{
  const ui=fixture({provider:'zcode',desktop:true});ui.panel.accept({...snapshot([]),capabilities:{apiFormats:[{value:'openai-responses',label:'OpenAI Responses'}],quota:false}});ui.panel.openEditor({kind:'api',api:{}});
  ui.panel.editUrl.input.value='https://first.invalid';ui.panel.editKey.input.value='fixture-key';
  const pending=[];ui.setHandler(()=>new Promise(resolve=>pending.push(resolve)));
  const first=ui.panel.apiEditor.fetchModels.onclick();ui.panel.editUrl.input.value='https://second.invalid';ui.panel.editUrl.input.oninput();const second=ui.panel.apiEditor.fetchModels.onclick();
  pending[0]({models:['stale']});await first;assert.equal(ui.panel.editModels.input.value,'');assert.equal(ui.panel.apiEditor.fetchModels.disabled,true);
  pending[1]({models:['fresh']});await second;assert.equal(ui.panel.editModels.input.value,'fresh');
  ui.panel.editKey.input.value='changed-key';ui.panel.editKey.input.oninput();const third=ui.panel.apiEditor.fetchModels.onclick();ui.panel.closeEditor();pending[2]({models:['closed']});await third;
  assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.panel.editor.hidden,true);assert.equal(ui.panel.apiEditor.modelSelect.hidden,true);assert.equal(ui.panel.editModels.input.value,'');
});
test('account management action uses one reusable slot without moving the saved account cards',()=>{
  const ui=fixture({provider:'zcode'});ui.panel.accept(snapshot([row('current')]));const cards=ui.panel.rows,button=ui.panel.managementButton;let clicked=0;
  ui.panel.setManagementAction({label:'连接方式',description:'当前：官方远控',onClick:()=>clicked++});assert.equal(ui.panel.rows,cards);assert.equal(ui.panel.managementSlot.hidden,false);button.onclick();assert.equal(clicked,1);
  ui.panel.setManagementAction({label:'连接方式',disabled:true});assert.equal(ui.panel.managementButton,button);assert.equal(button.disabled,true);ui.panel.setManagementAction(null);assert.equal(ui.panel.managementSlot.hidden,true);
});
test('ZCode API editor reuses shared form and sends native protocol plus URL and models without applying',async()=>{
  const ui=fixture({provider:'zcode',desktop:true});
  const value={...snapshot([]),capabilities:{addApi:true,quota:false,apiFormats:[{value:'anthropic-messages',label:'Anthropic Messages'},{value:'openai-chat-completions',label:'OpenAI Chat Completions'},{value:'openai-responses',label:'OpenAI Responses'}],switchRequiresStopped:true}};
  ui.panel.accept(value);assert.equal(ui.panel.addApiButton.textContent,'添加 API 接入');ui.panel.addApiButton.onclick();
  assert.equal(ui.panel.editUrl.wrap.hidden,false);assert.equal(ui.panel.editModels.wrap.hidden,false);assert.equal(ui.panel.apiFormatWrap.hidden,false);assert.equal(ui.panel.authWrap.hidden,true);
  assert.equal(ui.panel.editApiFormat.children.length,3);assert.equal(ui.panel.editApiFormat.value,'anthropic-messages');
  ui.panel.editName.input.value='Local API';ui.panel.editUrl.input.value='http://127.0.0.1:9999/v1';ui.panel.editApiFormat.value='openai-responses';ui.panel.editKey.input.value='fixture-key';ui.panel.editModels.input.value='first\nsecond';
  ui.setHandler(async()=>value);await ui.panel.saveEditor();
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].action,'save-api');assert.equal(ui.calls[0].apiFormat,'openai-responses');assert.equal(ui.calls[0].baseUrl,'http://127.0.0.1:9999/v1');assert.equal(ui.calls[0].models.join(','),'first,second');assert.equal('authScheme' in ui.calls[0],false);
  assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.prompts.length,0);
  ui.panel.openEditor({id:'saved',kind:'api',name:'API',api:{baseUrl:'https://api.example.invalid',apiFormat:'openai-chat-completions',models:['x'],hasKey:true}});
  assert.equal(ui.panel.editApiFormat.value,'openai-chat-completions');assert.equal(ui.panel.editKey.input.required,false);assert.equal(ui.panel.editKey.input.value,'');
});
test('ZCode API applied state is distinct from official login and selecting a model',async()=>{
  const ui=fixture({provider:'zcode',desktop:true});
  const value={...snapshot([{...row('official','official'),saved:true},{...row('draft','api'),saved:true},{...row('applied','api'),saved:true}],['official','applied']),capabilities:{apiFormats:[{value:'openai-responses',label:'OpenAI Responses'}],switchRequiresStopped:true,quota:false}};
  ui.panel.accept(value);assert.match(ui.text(),/已配置/);assert.match(ui.text(),/待应用/);assert.match(ui.text(),/应用配置/);
  const applied=ui.panel.rows.children.find(card=>card.accountRowId==='applied');assert.equal(applied.children[0].children[0].children[0].children[1].textContent,'已配置');
  ui.setHandler(async()=>value);await ui.panel.choose(row('draft','api'));
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].action,'switch');assert.match(ui.prompts[0],/正常退出原 App/);assert.match(ui.prompts[0],/选择对应模型/);assert.doesNotMatch(ui.prompts[0],/切换会重启/);
});
test('manual DSH account state hides editing and restores official controls after unbinding',()=>{
 const ui=fixture({desktop:true});ui.panel.openEditor({api:{}});
 ui.panel.accept({...snapshot([]),readOnly:true,canImport:false,canSwitch:false});
 assert.equal(ui.panel.editor.hidden,true);assert.equal(ui.panel.apiDetails.hidden,true);assert.equal(ui.panel.scanDetails.hidden,true);
 assert.match(ui.panel.hint.textContent,/第三方客户端/);assert.equal(ui.panel.modelsButton.hidden,false);
 ui.panel.accept(snapshot([]));assert.equal(ui.panel.apiDetails.hidden,false);assert.equal(ui.panel.scanDetails.hidden,false);
});
test('multiple native active connections are first and cannot be switched again',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('other'),row('api','api'),row('current')],['current','api']));
  assert.deepEqual(ui.panel.rows.children.map(card=>card.children[0].children[0].children[0].children[0].textContent),['api','current','other']);
  const buttons=ui.all().filter(node=>node.tag==='button'&&['当前接入','切换到此接入'].includes(node.textContent));assert.deepEqual(buttons.map(button=>button.disabled),[true,true,false]);
  ui.panel.choose(row('api'));assert.equal(ui.prompts.length,0);ui.panel.value.canSwitch=false;ui.panel.choose(row('other'));assert.equal(ui.prompts.length,0);
});
test('first visible visit queries automatically; manual quota query resets the five minute clock',async()=>{
  const ui=fixture();ui.setValue(snapshot([row('current')]));await ui.panel.resume();assert.deepEqual(ui.calls.map(call=>call.action),['list','details']);assert.equal(ui.calls[1].refresh,false);
  await ui.advance(299000);assert.equal(ui.calls.length,2);await ui.panel.perform({action:'details',id:'current',refresh:true});assert.equal(ui.calls.length,3);
  await ui.advance(1000);assert.equal(ui.calls.length,3);await ui.advance(299000);assert.deepEqual(ui.calls.map(call=>call.action),['list','details','details','list','details']);
});
test('hidden panels do not query quota and resume when overdue',async()=>{
  const ui=fixture();ui.setValue(snapshot([row('current')]));await ui.panel.resume();ui.setVisible(false);await ui.advance(300000);assert.equal(ui.calls.length,2);assert.equal(ui.timers.size,0);
  ui.setVisible(true);await ui.panel.resume();assert.equal(ui.calls.length,4);
});
for(const provider of ['zcode','deepseek'])test(`${provider} skips unsupported quota reads on open and refresh`,async()=>{
  const ui=fixture({provider});ui.setValue({...snapshot([{...row('current'),saved:false},row('saved')]),capabilities:{quota:false}});
  await ui.panel.resume();await ui.panel.refresh(true);await ui.advance(300000);
  assert.deepEqual(ui.calls.map(call=>call.action),['list','list','list']);
  assert.equal(ui.panel.error,'');
});
test('Claude list, timer, and return visits only read saved quota snapshots',async()=>{
  const ui=fixture({provider:'claude'});ui.setValue(snapshot([row('current'),row('saved')]));
  await ui.panel.resume();await ui.advance(300000);ui.setVisible(false);await ui.advance(300000);ui.setVisible(true);await ui.panel.resume();
  assert.deepEqual(ui.calls.map(call=>call.action),['list','list','list']);
  assert.match(ui.text(),/尚未查询/);
  ui.english();assert.match(ui.text(),/Not queried yet/);
});
test('Claude explicit quota query is single-flight and failed refresh keeps the last success time',async()=>{
  const ui=fixture({provider:'claude'}),saved={status:'ready',checkedAt:1000,limits:[{name:'5h',windows:[{remainingPercent:70}]}]};
  ui.panel.accept(snapshot([row('current','account',saved)]));assert.match(ui.text(),/查询时间：/);assert.match(ui.text(),/1970-01-01 \d\d:16/);
  const card=ui.panel.rows.children[0],controls=card.children.find(n=>n.className==='accounts-card-footer').children.at(-1);
  assert.equal(controls.className,'accounts-usage-controls');assert.equal(controls.children.at(-1).attributes['aria-label'],'查看剩余额度');
  assert.match(controls.children[0].textContent,/查询时间：/);assert.equal(card.children[1].children[1].children.some(node=>node.className==='account-updated'),false);
  let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));
  const button=ui.all().find(node=>node.tag==='button'&&node.attributes['aria-label']==='查看剩余额度');const pending=button.onclick();
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].refresh,true);
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.attributes['aria-label']==='查询中…').disabled,true);
  await ui.panel.perform({action:'details',id:'current',refresh:true});assert.equal(ui.calls.length,1);
  finish(snapshot([row('current','account',{...saved,status:'error',error:'无法查询 Claude 剩余额度，请确认客户端登录有效并解锁系统凭据存储后重试'})]));await pending;
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.attributes['aria-label']==='查看剩余额度').disabled,false);
  assert.match(ui.text(),/70%/);assert.match(ui.text(),/无法查询 Claude 剩余额度/);assert.match(ui.text(),/保留上次结果/);assert.match(ui.text(),/1970-01-01 \d\d:16/);
  ui.english();assert.match(ui.text(),/Queried at: /);assert.match(ui.text(),/Showing the last result/);assert.match(ui.text(),/Cannot check Claude remaining usage/);
});
test('Claude quota button shows an English pending state',async()=>{
  const ui=fixture({provider:'claude'});ui.english();ui.panel.accept(snapshot([row('current')]));
  let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));
  const pending=ui.all().find(node=>node.tag==='button'&&node.attributes['aria-label']==='View remaining quota').onclick();
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.attributes['aria-label']==='Checking…').disabled,true);
  finish(snapshot([row('current')]));await pending;
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.attributes['aria-label']==='View remaining quota').disabled,false);
});
test('closing a panel discards its late account response and refresh timers',async()=>{
  const ui=fixture();let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.resume();ui.panel.dispose();finish(snapshot([row('current')]));await pending;assert.equal(ui.panel.value,null);assert.equal(ui.timers.size,0);assert.equal(ui.updates.length,0);
});
test('DSH wallet amounts stay separate from percent quota and never invent missing balances',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current','account',{status:'ready',limits:[],balance:{wallets:[{currency:'CNY',remaining:'12.30',toppedUp:'10.00',granted:'2.30'},{currency:'USD',remaining:'4.00'}]}})]));
  assert.match(ui.text(),/CNY 12.30/);assert.match(ui.text(),/USD 4.00/);assert.match(ui.text(),/10.00/);assert.equal(ui.all().filter(node=>node.tag==='progress').length,0);
  const card=ui.panel.rows.children[0];assert.match(card.className,/accounts-client-card is-current/);assert.match(card.children[1].className,/accounts-client-usage/);
  assert.equal(card.children[1].children[0].children[0].textContent,'账户余额');assert.equal(card.children.find(n=>n.className==='accounts-card-footer').children.at(-1).children[0].attributes['aria-label'],'刷新余额');
  ui.panel.accept(snapshot([row('current','account',{status:'unsupported',limits:[]})]));assert.match(ui.text(),/余额信息暂不可用/);assert.doesNotMatch(ui.text(),/0%|12.30|USD/);
});
test('DeepSeek balance time stays beside its refresh control',()=>{
  const ui=fixture(),checkedAt=1000;
  ui.panel.accept(snapshot([row('current','account',{status:'ready',checkedAt,balance:{wallets:[{currency:'CNY',remaining:'12.30'}]}})]));
  const controls=ui.panel.rows.children[0].children.find(n=>n.className==='accounts-card-footer').children.at(-1);
  assert.equal(controls.className,'accounts-usage-controls');assert.equal(controls.children.at(-1).attributes['aria-label'],'刷新余额');
  assert.match(controls.children[0].textContent,/更新于：/);
});
test('client cards keep provider usage and API actions distinct',async()=>{
  const claude=fixture({provider:'claude'});claude.panel.accept(snapshot([row('current'),row('api','api')]));
  assert.match(claude.panel.rows.children[0].children[1].className,/accounts-client-usage/);
  assert.equal(claude.panel.rows.children[0].children.find(n=>n.className==='accounts-card-footer').children.at(-1).children[0].attributes['aria-label'],'查看剩余额度');
  assert.equal(claude.panel.rows.children[1].children.length,2,'API card has identity and actions, no invented quota');
  const dsh=fixture();dsh.panel.accept(snapshot([row('current'),row('api','api')]));
  assert.equal(dsh.panel.rows.children[1].children.length,2);
  dsh.english();assert.ok(dsh.all().some(node=>node.attributes['aria-label']==='Refresh balance'));assert.doesNotMatch(dsh.text(),/[\u4e00-\u9fff]/);
  await dsh.panel.rows.children[0].children.find(n=>n.className==='accounts-card-footer').children.at(-1).children[0].onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(dsh.calls)),[{action:'details',id:'current',refresh:true}]);
});
test('DSH API wallet appears only when the backend supplies a balance',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('api','api',{status:'ready',balance:{wallets:[{currency:'CNY',remaining:'9.50'}]}})],['api']));
  assert.match(ui.text(),/CNY 9.50/);assert.equal(ui.panel.rows.children[0].children.length,5);
  ui.panel.accept(snapshot([row('api','api',{status:'unsupported'})],['api']));
  assert.equal(ui.panel.rows.children[0].children.length,4);assert.doesNotMatch(ui.text(),/CNY 9.50|余额信息暂不可用/);
});
test('current account models share footer with quota time and refresh',async()=>{
  const ui=fixture({toolbar:true});ui.setValue(snapshot([row('current')]));ui.panel.accept(snapshot([row('current')]));
  assert.equal(ui.outerToolbar.children.length,1);assert.equal(ui.outerToolbar.children[0].textContent,'刷新');
  assert.equal(ui.root.children.some(node=>node.className==='accounts-toolbar'),false);
  assert.equal(ui.panel.modelsButton.parent.className,'accounts-card-footer');assert.equal(ui.panel.modelsButton.parent.children[0],ui.panel.modelsButton);assert.equal(ui.panel.modelsBox.parent,ui.panel.rows.children[0]);
  await ui.outerToolbar.children[0].onclick();assert.deepEqual(ui.calls.map(call=>call.action),['list','details']);
});
test('Claude enrollment confirms restart while DSH API import remains a read-only native snapshot',async()=>{
  const ui=fixture({desktop:true,provider:'claude'});ui.setValue(snapshot([row('current')]));ui.panel.accept(snapshot([row('current')]));ui.confirm(false);await ui.panel.importCurrent();assert.equal(ui.calls.length,0);
  assert.match(ui.prompts[0],/解锁状态/);ui.confirm(true);ui.panel.nameInput.value='Work account';await ui.panel.importCurrent();assert.equal(ui.calls[0].action,'import-current');assert.equal(ui.calls[0].name,'Work account');assert.equal(ui.panel.nameInput.value,'');assert.ok(ui.prompts[0].includes('短暂重启'));
  const dsh=fixture({desktop:true});dsh.setValue(snapshot([]));await dsh.panel.importCurrent('api');assert.equal(dsh.calls[0].kind,'api');assert.equal(dsh.prompts.length,0);
  const web=fixture();await web.panel.importCurrent();assert.equal(web.calls.length,0);assert.equal(web.panel.importButton,undefined);
});
test('Claude account switch explains unlock requirement in the same confirmation and cancel submits nothing',async()=>{
 const ui=fixture({provider:'claude'});ui.panel.accept(snapshot([row('current'),row('other')]));ui.confirm(false);await ui.panel.choose(row('other'));assert.equal(ui.calls.length,0);assert.equal(ui.prompts.length,1);assert.match(ui.prompts[0],/切换会重启/);assert.match(ui.prompts[0],/解锁状态/);
 ui.english();await ui.panel.choose(row('other'));assert.equal(ui.calls.length,0);assert.match(ui.prompts[1],/unlocked/);assert.doesNotMatch(ui.prompts[1],/[\u4e00-\u9fff]/);
 ui.confirm(true);await ui.panel.choose(row('other'));assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].action,'switch');assert.equal(ui.prompts.length,3,'each attempt has only one confirmation');
 const dsh=fixture();dsh.panel.accept(snapshot([row('current'),row('other')]));dsh.confirm(false);await dsh.panel.choose(row('other'));assert.doesNotMatch(dsh.prompts[0],/解锁|Claude/);assert.equal(dsh.calls.length,0);
});
test('Claude API switch sends local CLI choice only after checkbox selection',async()=>{
 const ui=fixture({provider:'claude'});ui.panel.accept(snapshot([row('current'),row('other','api')]));
 const box=ui.all().find(node=>node.tag==='input'&&node.type==='checkbox');
 assert.ok(box);box.checked=true;box.onchange();ui.confirm(true);await ui.panel.choose(row('other','api'));
 assert.equal(ui.calls[0].syncLocalCli,true);
});
test('independent Claude account cards never offer or retain default CLI synchronization',async()=>{
 const ui=fixture({provider:'claude'}),other=row('other','api');
 ui.panel.syncLocalCliIds.add(other.id);
 ui.panel.accept({...snapshot([row('current'),other]),canSyncLocalCli:false});
 assert.equal(ui.all().some(node=>node.tag==='input'&&node.type==='checkbox'),false);
 ui.confirm(true);await ui.panel.choose(other);assert.equal(ui.calls[0].syncLocalCli,undefined);
});
test('switch remains pending until the backend finishes and rejects duplicate clicks',async()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current'),row('other')]));let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.choose(row('other'));assert.equal(ui.panel.busy,true);await ui.panel.choose(row('other'));assert.equal(ui.calls.length,1);
  finish(snapshot([row('current'),row('other')],['other']));await pending;assert.equal(ui.panel.busy,false);assert.equal(ui.panel.value.activeId,'other');
});
test('late model results cannot leak across account changes',async()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current'),row('other')]));let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.toggleModels();ui.panel.accept(snapshot([row('current'),row('other')],['other']));finish({models:[{id:'old-model'}]});await pending;assert.equal(ui.panel.models,null);assert.equal(ui.panel.modelsLoading,false);assert.doesNotMatch(ui.text(),/old-model/);
});
test('English account content uses translated state, wallet labels and restart confirmation',async()=>{
  const ui=fixture({desktop:true,provider:'claude'});ui.english();ui.panel.accept(snapshot([row('current','account',{status:'unsupported',limits:[]})]));
  assert.doesNotMatch(ui.text(),/[\u4e00-\u9fff]/);ui.confirm(false);await ui.panel.importCurrent();assert.doesNotMatch(ui.prompts[0],/[\u4e00-\u9fff]/);
});

test('leaving during quota refresh stops further upstream account queries',async()=>{
 const ui=fixture();ui.setValue(snapshot([row('current'),row('saved')]));let finish,started;const querying=new Promise(resolve=>started=resolve);ui.setHandler(()=>{started();return new Promise(resolve=>finish=resolve);});const loading=ui.panel.resume();await querying;assert.equal(ui.calls.filter(call=>call.action==='details').length,1);ui.setVisible(false);finish(snapshot([row('current'),row('saved')]));await loading;assert.equal(ui.calls.filter(call=>call.action==='details').length,1);
});

test('account model catalog renders native provider groups and reasoning options without a chat',async()=>{
 const ui=fixture();ui.english();ui.panel.accept(snapshot([]));ui.setHandler(async()=>({groups:[{id:'deepseek-account',name:'Official access',models:[{id:'deepseek-account/deepseek-v4-pro',name:'DeepSeek Pro',efforts:['low','high']}]},{id:'custom',name:'Configured API',models:[{id:'custom/model-x',name:'Custom model'}]}]}));await ui.panel.toggleModels();assert.match(ui.text(),/Official access/);assert.match(ui.text(),/DeepSeek Pro/);assert.match(ui.text(),/low.*high/);assert.match(ui.text(),/Custom model/);assert.doesNotMatch(ui.text(),/进入会话|可用模型/);
});
test('flat model responses are preserved when the backend has no groups',async()=>{
 const ui=fixture();ui.setHandler(async()=>({groups:[],models:[{id:'flat/model',name:'Flat model'}]}));await ui.panel.toggleModels();assert.match(ui.text(),/Flat model/);
});
test('Claude configured and unavailable account catalogs keep source labels and identity',async()=>{
 const ui=fixture({provider:'claude'}),identity='a'.repeat(64);
 ui.panel.accept({...snapshot([row('current')]),modelCatalogIdentity:identity});
 ui.setHandler(async()=>({cache:{identity,state:'unavailable',updatedAt:null},models:[],groups:[],notice:'Claude 官方账号未提供可离线读取的模型目录；连接 Desktop 后已有的缓存仍可查看'}));
 await ui.panel.toggleModels();assert.match(ui.text(),/未提供可离线读取/);assert.doesNotMatch(ui.text(),/账号已切换/);
 ui.setHandler(async()=>({cache:{identity,state:'unavailable',updatedAt:null},models:[{id:'configured-model'}],groups:[{name:'Provider',source:'desktop-config',models:[{id:'configured-model'}]}]}));
 await ui.panel.loadModels(true);assert.match(ui.text(),/配置模型/);assert.match(ui.text(),/configured-model/);
 ui.english();assert.match(ui.text(),/Configured models/);
});
test('model read errors are retryable and are not presented as a successful empty catalog',async()=>{
 const ui=fixture();ui.setHandler(async()=>{throw Error('model catalog unavailable');});await ui.panel.toggleModels();assert.match(ui.text(),/model catalog unavailable/);assert.doesNotMatch(ui.text(),/未返回可用模型|进入会话/);ui.setHandler(async()=>({models:[{id:'recovered-model'}]}));await ui.all().find(node=>node.tag==='button'&&node.textContent==='刷新模型').onclick();assert.match(ui.text(),/recovered-model/);assert.doesNotMatch(ui.text(),/model catalog unavailable/);
});

test('API editor exists only in desktop and sends a native gateway draft without applying it',async()=>{
 const web=fixture();assert.equal(web.panel.editor,undefined);assert.equal(web.panel.addApiButton,undefined);await web.panel.editAccount(row('one'));await web.panel.removeAccount(row('one'));assert.equal(web.calls.length,0);
 const ui=fixture({desktop:true,provider:'claude'});ui.setValue(snapshot([]));ui.panel.addApiButton.onclick();ui.panel.editName.input.value='Upstream';ui.panel.editUrl.input.value='https://gateway.test';ui.panel.editKey.input.value='private-key';ui.panel.editModels.input.value='model-one\nmodel-two';ui.panel.editAuth.value='x-api-key';await ui.panel.saveEditor();
 assert.deepEqual(JSON.parse(JSON.stringify(ui.calls)),[{action:'save-api',name:'Upstream',apiKey:'private-key',baseUrl:'https://gateway.test',authScheme:'x-api-key',models:['model-one','model-two']}]);assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.panel.editor.hidden,true);assert.doesNotMatch(ui.text(),/private-key/);
});
test('API edits keep the existing key blank and DSH does not expose unsupported custom URL fields',async()=>{
 const ui=fixture({desktop:true});ui.panel.accept(snapshot([row('saved','api')]));ui.setHandler(async value=>value.action==='edit'?{id:'saved',name:'Saved API',kind:'api',api:{hasKey:true}}:snapshot([row('saved','api')]));await ui.panel.editAccount(row('saved','api'));
 assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.panel.editKey.input.required,false);assert.equal(ui.panel.editUrl.wrap.hidden,true);assert.equal(ui.panel.editModels.wrap.hidden,true);await ui.panel.saveEditor();assert.deepEqual(JSON.parse(JSON.stringify(ui.calls[1])),{action:'save-api',id:'saved',name:'Saved API',apiKey:''});assert.equal(ui.calls.some(call=>call.action==='switch'),false);
});
test('official account edit is a name-only operation and deletion requires confirmation',async()=>{
 const ui=fixture({desktop:true});ui.panel.accept(snapshot([row('saved')]));ui.setHandler(async value=>value.action==='edit'?{id:'saved',name:'Saved',kind:'official'}:snapshot([]));await ui.panel.editAccount(row('saved'));assert.equal(ui.panel.editKey.wrap.hidden,true);ui.panel.editName.input.value='Renamed';await ui.panel.saveEditor();assert.deepEqual(JSON.parse(JSON.stringify(ui.calls[1])),{action:'rename',id:'saved',name:'Renamed'});
 ui.confirm(false);await ui.panel.removeAccount(row('saved'));assert.equal(ui.calls.length,2);ui.confirm(true);await ui.panel.removeAccount(row('saved'));assert.equal(ui.calls[2].action,'remove');assert.match(ui.prompts.at(-1),/不会退出电脑上的账号/);
});
test('closing account panel discards a late edit response and erases typed key',async()=>{
 const ui=fixture({desktop:true});ui.panel.openEditor({kind:'api',api:{}});ui.panel.editKey.input.value='private-key';ui.panel.closeEditor();assert.equal(ui.panel.editKey.input.value,'');let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.editAccount(row('saved'));ui.panel.dispose();finish({id:'saved',name:'Saved',kind:'api',api:{hasKey:true}});await pending;assert.equal(ui.panel.editor.hidden,true);assert.equal(ui.panel.editing,null);
});

test('model catalog reopens from cache; only explicit refresh forces upstream and preserves old rows',async()=>{
 const ui=fixture({provider:'claude'}),calls=[],identity='a'.repeat(64);
 ui.panel.accept({...snapshot([row('current')]),modelCatalogIdentity:identity});ui.setHandler(async body=>{calls.push(body);return {cache:{identity,updatedAt:1000},models:[{id:'concrete-model',efforts:['low','high']}]};});
 await ui.panel.toggleModels();await ui.panel.toggleModels();await ui.panel.toggleModels();assert.equal(calls.length,2);assert.equal(calls[0].refresh,false);assert.equal(calls[1].refresh,false);assert.match(ui.text(),/concrete-model/);assert.match(ui.text(),/更新于：/);
 ui.setHandler(async body=>{calls.push(body);throw Error('upstream temporarily unavailable');});await ui.all().find(n=>n.tag==='button'&&n.textContent==='刷新模型').onclick();assert.equal(calls.at(-1).refresh,true);assert.match(ui.text(),/concrete-model/);assert.match(ui.text(),/upstream temporarily unavailable/);assert.equal(ui.calls.some(c=>c.action==='details'),false);
 ui.english();assert.match(ui.text(),/Refresh models/);
});
test('same saved account ID with changed model identity clears rows and rejects old reads',async()=>{
 const ui=fixture({provider:'claude'}),a='a'.repeat(64),b='b'.repeat(64),account=s=>({...snapshot([row('current')]),modelCatalogIdentity:s});
 ui.panel.accept(account(a));ui.setHandler(async()=>({cache:{identity:a,updatedAt:1000},models:[{id:'model-a'}]}));await ui.panel.toggleModels();assert.match(ui.text(),/model-a/);
 let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.loadModels();ui.panel.accept(account(b));assert.equal(ui.panel.models,null);assert.doesNotMatch(ui.text(),/model-a/);
 finish({cache:{identity:a,updatedAt:1000},models:[{id:'model-a'}]});await pending;assert.equal(ui.panel.models,null);
 ui.panel.modelsOpen=true;ui.setHandler(async()=>({cache:{identity:b,updatedAt:2000},models:[{id:'model-b'}]}));await ui.panel.loadModels();assert.match(ui.text(),/model-b/);
 ui.setHandler(async()=>({cache:{identity:a,updatedAt:1000},models:[{id:'model-a'}]}));await ui.panel.loadModels();assert.equal(ui.panel.models,null);assert.doesNotMatch(ui.text(),/model-a|model-b/);
});

for(const provider of ['claude','deepseek'])test(`${provider} model scroller survives status reads and manual refresh`,async()=>{
 const ui=fixture({provider}),identity='a'.repeat(64),value={...snapshot([row('current')]),modelCatalogIdentity:identity};
 ui.setValue(value);ui.panel.accept(value);ui.setHandler(async()=>({cache:{identity,updatedAt:1000},models:[{id:'model-a'},{id:'model-b'}]}));
 await ui.panel.toggleModels();const box=ui.panel.modelsBox,card=box.parent;box.scrollTop=83;
 await ui.panel.refresh(false);assert.equal(ui.panel.modelsBox,box);assert.equal(box.parent,card);assert.equal(box.scrollTop,83);
 ui.setHandler(async()=>({cache:{identity,updatedAt:2000},models:[{id:'model-c'},{id:'model-d'}]}));
 await ui.all().find(n=>n.tag==='button'&&n.textContent==='刷新模型').onclick();assert.equal(box.scrollTop,83);assert.match(ui.text(),/model-d/);
 let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));ui.focus(ui.panel.modelsRefreshButton);const pending=ui.panel.modelsRefreshButton.onclick();ui.blur();finish({cache:{identity,updatedAt:3000},models:[{id:'model-e'}]});await pending;assert.equal(ui.active(),ui.panel.modelsRefreshButton);assert.equal(box.scrollTop,83);
});

test('current ZCode daily model quota shows exact native token quantities and checks only the active row',async()=>{
 const ui=fixture({provider:'zcode'}),usage={status:'ok',source:'nativeSnapshot',checkedAt:1000,
  limits:[{name:'GLM-5.3',windows:[{unit:'tokens',remaining:0,total:10,remainingPercent:0}]},
   {name:'GLM-5.3-Flash',windows:[{unit:'tokens',remaining:9,total:20,remainingPercent:45}]}]};
 ui.setValue(snapshot([{...row('current','official',usage),canReadQuota:true},{...row('other','official'),canReadQuota:false}]));
 await ui.panel.resume();assert.deepEqual(ui.calls.map(c=>c.action),['list','details']);assert.equal(ui.calls[1].id,'current');
 assert.match(ui.text(),/今日剩余额度/);assert.match(ui.text(),/GLM-5.3/);assert.match(ui.text(),/GLM-5.3-Flash/);
 assert.match(ui.text(),/0 \/ 10 tokens/);assert.match(ui.text(),/9 \/ 20 tokens/);assert.match(ui.text(),/原 App 查询时间/);
});
