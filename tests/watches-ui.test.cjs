'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function fixture(){
  const all=node=>[node,...node.children.flatMap(all)];
  function node(tag){return {tag,dataset:{},children:[],textContent:'',disabled:false,
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(){},querySelectorAll(selector){return all(this).filter(n=>selector.split(',').includes(n.tag));}};}
  const root=node('div'),calls=[];
  const value={runtime:{running:true},notifications:{barkEnabled:true},watches:[
    {id:'same-id',host:'local',title:'Local chat',cwd:'/local/path',notifyOnCompletion:false},
    {id:'same-id',host:'remote',hostLabel:'My server',title:'Remote chat',notifyOnCompletion:true}]};
  let change=async body=>({watches:value.watches.flatMap(w=>w.host!==body.host?[w]:body.action==='remove'?[]:[{...w,notifyOnCompletion:body.notifyOnCompletion}])});
  const context=vm.createContext({root,document:{createElement:node},localStorage:{getItem(){},setItem(){}},change:body=>{calls.push({...body});return change(body);}});
  for(const file of ['web/shared/i18n.js','desktop/features/notifications/watches.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
  const panel=vm.runInContext('new WatchPanel({root,change})',context);panel.render(value);
  return {panel,root,value,calls,all:()=>all(root),text:()=>all(root).map(n=>n.textContent).join('\n'),setChange:fn=>change=fn,
    language:()=>{vm.runInContext("BridgeI18n.setLanguage('en')",context);panel.render(panel.value);}};
}

test('watched chats show identity, completion preference and removal without an add action',()=>{
  const ui=fixture();assert.match(ui.text(),/Local chat/);assert.match(ui.text(),/My server/);assert.match(ui.text(),/same-id/);
  assert.deepEqual(ui.all().filter(n=>n.tag==='input').map(n=>n.checked),[false,true]);
  assert.equal(ui.all().filter(n=>n.tag==='button').length,2);
  ui.language();assert.match(ui.text(),/Remove watch/);assert.match(ui.text(),/Notify when a run completes/);
});
test('checkbox updates immediately and delete removes only the selected host',async()=>{
  const ui=fixture(),input=ui.all().find(n=>n.tag==='input');input.checked=true;
  input.onchange({stopPropagation(){}});await new Promise(setImmediate);
  assert.deepEqual(ui.calls[0],{id:'same-id',host:'local',action:'update',notifyOnCompletion:true});
  assert.equal(ui.all().find(n=>n.tag==='input').checked,true);
  await ui.all().filter(n=>n.tag==='button')[1].onclick();
  assert.deepEqual(ui.calls[1],{id:'same-id',host:'remote',action:'remove'});
  assert.match(ui.text(),/Local chat/);assert.doesNotMatch(ui.text(),/Remote chat/);
});
test('in-flight mutation blocks duplicate clicks and stale snapshots cannot resurrect a deleted row',async()=>{
  const ui=fixture(),revision=ui.panel.revision;let finish;
  ui.setChange(()=>new Promise(resolve=>finish=resolve));
  const button=ui.all().find(n=>n.tag==='button'),pending=button.onclick();
  await button.onclick();assert.equal(ui.calls.length,1);
  assert.ok(ui.all().filter(n=>['input','button'].includes(n.tag)).every(n=>n.disabled));
  ui.panel.render(ui.value);finish({watches:[ui.value.watches[1]]});await pending;
  ui.panel.render(ui.value,revision);assert.doesNotMatch(ui.text(),/Local chat/);
});
test('failed saves restore the checked value and surface translated error',async()=>{
  const ui=fixture();ui.setChange(()=>{throw Error("Error invoking remote method 'bridge:notification-watches': Error: 请重新启动网关以管理会话通知");});
  const input=ui.all().find(n=>n.tag==='input');input.checked=true;input.onchange({stopPropagation(){}});await new Promise(setImmediate);
  assert.equal(ui.all().find(n=>n.tag==='input').checked,false);assert.match(ui.text(),/重新启动/);
  ui.language();assert.match(ui.text(),/Restart the gateway/);
});
test('changing data directory ignores an old response; offline and disabled channels remain manageable',async()=>{
  const ui=fixture();let finish;ui.setChange(()=>new Promise(resolve=>finish=resolve));
  const pending=ui.all().find(n=>n.tag==='button').onclick();ui.panel.clear();
  ui.panel.render({...ui.value,runtime:{running:false},watches:[ui.value.watches[1]]});
  finish({watches:[]});await pending;assert.match(ui.text(),/Remote chat/);assert.match(ui.text(),/网关未运行/);
  ui.panel.render({...ui.value,notifications:{}});assert.match(ui.text(),/通知通道未开启/);
  assert.ok(ui.all().filter(n=>['input','button'].includes(n.tag)).every(n=>!n.disabled));
});
