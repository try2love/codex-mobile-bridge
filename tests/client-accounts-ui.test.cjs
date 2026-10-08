'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({desktop=false,provider='deepseek'}={}){
  function node(tag){return {tag,children:[],dataset:{},textContent:'',hidden:false,disabled:false,attributes:{},classList:{add(){}},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},setAttribute(key,value){this.attributes[key]=value;},closest(){return null;},querySelectorAll(selector){return this.children.flatMap(child=>[...(selector==='[data-i18n]'&&child.dataset.i18n?[child]:[]),...child.querySelectorAll(selector)]);}};}
  const root=node('div'),listeners={},timers=new Map(),calls=[],prompts=[],updates=[];let time=1000000,tid=0,visible=true,confirmed=true,value={accounts:[],activeId:null,canSwitch:true},handler=async()=>value,readHandler=async()=>value;
  const document={hidden:false,documentElement:{},createElement:node,querySelectorAll:()=>[],addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn),removeEventListener:(type,fn)=>listeners[type]=listeners[type].filter(row=>row!==fn),dispatchEvent(event){for(const fn of listeners[event.type]||[])fn(event);}};
  class Clock extends Date{static now(){return time;}}
  const context=vm.createContext({document,Date:Clock,CustomEvent:class{constructor(type){this.type=type;}},localStorage:{getItem(){return null;},setItem(){}},window:{confirm(message){prompts.push(message);return confirmed;}},setTimeout(fn,ms){timers.set(++tid,{fn,due:time+ms});return tid;},clearTimeout:id=>timers.delete(id)});
  for(const path of ['web/i18n.js','web/client-accounts.js'])vm.runInContext(fs.readFileSync(path,'utf8'),context);
  const Panel=vm.runInContext('DesktopClientAccountsPanel',context),panel=new Panel({root,provider,desktop,read:()=>{calls.push({action:'list'});return readHandler();},request:body=>{calls.push(body);return handler(body);},readModels:()=>handler({action:'models'}),visible:()=>visible,onUpdate:result=>updates.push(result)});
  const all=node=>[node,...node.children.flatMap(all)];
  return {panel,root,calls,prompts,updates,timers,setValue:next=>value=next,setHandler:next=>handler=next,setRead:next=>readHandler=next,setVisible:next=>visible=next,confirm:next=>confirmed=next,advance:async ms=>{time+=ms;for(const [id,timer]of [...timers])if(timer.due<=time){timers.delete(id);await timer.fn();}},tick:ms=>time+=ms,now:()=>time,all:()=>all(root),text:()=>all(root).map(row=>row.textContent).join('\n'),english:()=>vm.runInContext("BridgeI18n.setLanguage('en')",context)};
}
const row=(id,kind='account',usage)=>({id,name:id,kind,usage});
const snapshot=(accounts,activeIds=['current'])=>({accounts,activeId:activeIds[0],activeIds,current:{name:'Current desktop access'},canSwitch:true});
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
test('closing a panel discards its late account response and refresh timers',async()=>{
  const ui=fixture();let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.resume();ui.panel.dispose();finish(snapshot([row('current')]));await pending;assert.equal(ui.panel.value,null);assert.equal(ui.timers.size,0);assert.equal(ui.updates.length,0);
});
test('DSH wallet amounts stay separate from percent quota and never invent missing balances',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current','account',{status:'ready',limits:[],balance:{wallets:[{currency:'CNY',remaining:'12.30',toppedUp:'10.00',granted:'2.30'},{currency:'USD',remaining:'4.00'}]}})]));
  assert.match(ui.text(),/CNY 12.30/);assert.match(ui.text(),/USD 4.00/);assert.match(ui.text(),/10.00/);assert.equal(ui.all().filter(node=>node.tag==='progress').length,0);
  ui.panel.accept(snapshot([row('current','account',{status:'unsupported',limits:[]})]));assert.match(ui.text(),/暂未提供额度信息/);assert.doesNotMatch(ui.text(),/0%|12.30|USD/);
});
test('Claude enrollment confirms restart while DSH API import remains a read-only native snapshot',async()=>{
  const ui=fixture({desktop:true,provider:'claude'});ui.setValue(snapshot([row('current')]));ui.panel.accept(snapshot([row('current')]));ui.confirm(false);await ui.panel.importCurrent();assert.equal(ui.calls.length,0);
  ui.confirm(true);ui.panel.nameInput.value='Work account';await ui.panel.importCurrent();assert.equal(ui.calls[0].action,'import-current');assert.equal(ui.calls[0].name,'Work account');assert.equal(ui.panel.nameInput.value,'');assert.ok(ui.prompts[0].includes('短暂重启'));
  const dsh=fixture({desktop:true});dsh.setValue(snapshot([]));await dsh.panel.importCurrent('api');assert.equal(dsh.calls[0].kind,'api');assert.equal(dsh.prompts.length,0);
  const web=fixture();await web.panel.importCurrent();assert.equal(web.calls.length,0);assert.equal(web.panel.importButton,undefined);
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
test('model read errors are retryable and are not presented as a successful empty catalog',async()=>{
 const ui=fixture();ui.setHandler(async()=>{throw Error('model catalog unavailable');});await ui.panel.toggleModels();assert.match(ui.text(),/model catalog unavailable/);assert.doesNotMatch(ui.text(),/未返回可用模型|进入会话/);ui.setHandler(async()=>({models:[{id:'recovered-model'}]}));await ui.all().find(node=>node.tag==='button'&&node.textContent==='重试').onclick();assert.match(ui.text(),/recovered-model/);assert.doesNotMatch(ui.text(),/model catalog unavailable/);
});
