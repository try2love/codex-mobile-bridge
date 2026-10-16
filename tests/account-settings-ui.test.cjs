'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture(desktop=false){
 function node(tag){return {tag,children:[],textContent:'',hidden:false,disabled:false,attributes:{},classList:{add(){}},
  setAttribute(k,v){this.attributes[k]=v;},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},closest(selector){return selector==='dialog'?null:this.hidden?this:null;}};}
 const remindersRoot=node('section'),updatesRoot=node('section'),requests=[],prompts=[],updates=[],timers=new Map();let timerId=0;
 let confirmed=true,value={accounts:[],reminders:{globalReset:false,globalResetSignal:false},desktopUpdate:{state:'idle'}},read=async()=>structuredClone(value),request=async()=>structuredClone(value);
 const context=vm.createContext({document:{createElement:node,addEventListener(){},hidden:false},window:{confirm:message=>{prompts.push(message);return confirmed;}},setTimeout:(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;},clearTimeout:id=>timers.delete(id),localStorage:{getItem(){},setItem(){}},Date});
 for(const file of ['web/shared/i18n.js','web/features/accounts/account-settings.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
 const Class=vm.runInContext('AccountSettingsPanel',context),panel=new Class({remindersRoot,updatesRoot,desktop,read:()=>read(),request:payload=>{requests.push(payload);return request(payload);},requestId:()=> 'fixture-id',onUpdate:value=>updates.push(value)});
 const all=(root)=>[root,...root.children.flatMap(all)];
 return {panel,requests,prompts,updates,timers,remindersRoot,updatesRoot,all:(root)=>root?all(root):[...all(remindersRoot),...all(updatesRoot)],text:()=>[...all(remindersRoot),...all(updatesRoot)].map(n=>n.textContent).join('\n'),value:()=>value,setValue:v=>value=v,setRequest:v=>request=v,setRead:v=>read=v,confirm:v=>confirmed=v,language:()=>{vm.runInContext("BridgeI18n.setLanguage('en')",context);panel.render();}};
}

test('idle notifications never schedule polling and update progress polls only while shown',async()=>{
 const ui=fixture();await ui.panel.refresh();assert.equal(ui.timers.size,0);
 await ui.panel.perform({action:'reminders',preferences:{lowQuota:true}});assert.equal(ui.timers.size,0);
 ui.setValue({...ui.value(),desktopUpdate:{state:'checking'}});await ui.panel.refresh();
 assert.equal(ui.timers.get(ui.panel.timer).delay,2000);
 ui.updatesRoot.hidden=true;ui.panel.schedule();assert.equal(ui.timers.size,0);
 ui.updatesRoot.hidden=false;ui.setValue({...ui.value(),desktopUpdate:{state:'checked'}});await ui.panel.refresh();assert.equal(ui.timers.size,0);
});

test('failed progress reads stop polling and expose an explicit retry',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),desktopUpdate:{state:'checking'}});await ui.panel.refresh();
 ui.setRead(async()=>{throw Error('Offline');});await ui.panel.refresh();assert.equal(ui.timers.size,0);
 assert.ok(ui.all().find(node=>node.textContent==='重试读取'));
});

test('notification groups use the existing preferences and only read account status',async()=>{
 const ui=fixture();await ui.panel.refresh();
 assert.match(ui.text(),/账号与额度提醒/);assert.match(ui.text(),/客户端更新提醒/);
 assert.equal(ui.all(ui.remindersRoot).filter(n=>n.tag==='input').length,6);
 const reset=ui.all(ui.remindersRoot).find(n=>n.textContent==='重置动态提醒'&&n.tag==='strong');assert.ok(reset);
 assert.equal(ui.requests.length,0);
});

test('combined reset setting persists both preferences and reports the shared state',async()=>{
 const ui=fixture();await ui.panel.refresh();
 const inputs=ui.all(ui.remindersRoot).filter(n=>n.tag==='input'),toggle=inputs[4];toggle.checked=true;
 ui.setRequest(async payload=>({...ui.value(),reminders:payload.preferences}));
 await toggle.onchange({stopPropagation(){}});
 assert.deepEqual(JSON.parse(JSON.stringify(ui.requests[0])),{action:'reminders',preferences:{globalReset:true,globalResetSignal:true}});
 assert.equal(ui.updates.at(-1).reminders.globalReset,true);
 assert.equal(ui.all(ui.remindersRoot).filter(n=>n.tag==='input')[4].checked,true);
});

test('closed views stop status polling and a new bell snapshot supersedes an old read',async()=>{
 const ui=fixture();ui.remindersRoot.hidden=true;ui.updatesRoot.hidden=true;let calls=0;ui.setRead(async()=>{calls++;return ui.value();});await ui.panel.refresh();assert.equal(calls,0);
 ui.remindersRoot.hidden=false;let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.refresh();
 ui.panel.accept({...ui.value(),reminders:{globalReset:true,globalResetSignal:true}});finish(ui.value());await pending;
 assert.equal(ui.all(ui.remindersRoot).filter(n=>n.tag==='input')[4].checked,true);
});

test('save failures keep preferences, show their error and allow retry',async()=>{
 const ui=fixture();await ui.panel.refresh();ui.setRequest(async()=>{throw Error('保存失败');});
 await ui.panel.perform({action:'reminders',preferences:{lowQuota:true}});
 assert.equal(ui.panel.value.reminders.lowQuota,undefined);assert.match(ui.text(),/保存失败/);assert.equal(ui.panel.busy,false);
 ui.setRequest(async()=>({...ui.value(),reminders:{lowQuota:true}}));await ui.panel.perform({action:'reminders',preferences:{lowQuota:true}});assert.equal(ui.panel.value.reminders.lowQuota,true);
});

test('successful reads clear errors after connection recovery',async()=>{
 const ui=fixture();ui.setRead(async()=>{throw Error('连接失败');});await ui.panel.refresh();
 assert.match(ui.text(),/连接失败/);
 ui.setRead(async()=>ui.value());await ui.panel.refresh();
 assert.equal(ui.panel.error,'');assert.doesNotMatch(ui.text(),/连接失败|读取失败/);
 assert.match(ui.text(),/账号与额度提醒/);
});

test('clear discards late settings reads',async()=>{
 const ui=fixture();let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.refresh();ui.panel.clear();finish({...ui.value(),reminders:{globalReset:true}});await pending;assert.equal(ui.panel.value,null);assert.equal(ui.updates.length,0);
});

test('passwordless update controls stay disabled after saving notification preferences',async()=>{
 const ui=fixture();ui.setValue({...ui.value(),canSwitch:false,desktopUpdate:{state:'available',canRequest:true,currentBuild:'1'}});await ui.panel.refresh();
 ui.setRequest(async()=>({...ui.value(),canSwitch:undefined}));await ui.panel.perform({action:'reminders',preferences:{globalReset:true,globalResetSignal:true}});
 assert.equal(ui.all(ui.updatesRoot).find(n=>n.textContent==='查看 Codex 新版本').disabled,true);
 assert.equal(ui.all(ui.updatesRoot).find(n=>n.textContent==='打开电脑端更新窗口').disabled,true);
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

test('a healthy shared snapshot clears read errors and resumes visible update polling',async()=>{
 const ui=fixture();ui.setRead(async()=>{throw Error('Offline');});await ui.panel.refresh();
 assert.match(ui.text(),/Offline/);
 ui.panel.accept({...ui.value(),desktopUpdate:{state:'checking'}});
 assert.equal(ui.panel.error,'');assert.doesNotMatch(ui.text(),/Offline/);
 assert.equal(ui.timers.get(ui.panel.timer).delay,2000);
});

test('passive account snapshots retain a failed settings write until an explicit retry',async()=>{
 const ui=fixture();await ui.panel.refresh();ui.setRequest(async()=>{throw Error('Save failed');});
 await ui.panel.perform({action:'reminders',preferences:{lowQuota:true}});
 ui.panel.accept({...ui.value(),reminders:{lowQuota:false}});assert.match(ui.text(),/Save failed/);
 ui.setRead(async()=>ui.value());await ui.panel.refresh();assert.equal(ui.panel.error,'');
});
