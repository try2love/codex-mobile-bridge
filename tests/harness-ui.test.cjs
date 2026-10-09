'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {HarnessPanel}=require('../desktop/features/clients/harness.js');
function fixture(){
  const node=()=>({value:'',disabled:false,textContent:'',addEventListener(){}});
  const inputs=['executable','workspace','home'].map(name=>({...node(),name,value:'/fixture/'+name}));
  const actions=['start','stop','open','status','detect'].map(action=>({...node(),dataset:{harnessAction:action}}));
  const form=node(),save=node(),state=node(),logs=node(),calls=[],errors=[];
  const root={querySelector(selector){
    if(selector==='form')return form;if(selector==='pre')return logs;if(selector==='[data-harness-state]')return state;
    if(selector.startsWith('[name='))return inputs.find(x=>selector==='[name='+x.name+']');
    return actions.find(x=>selector==='[data-harness-action='+x.dataset.harnessAction+']');
  },querySelectorAll(selector){
    if(selector==='[data-harness-choose]')return [];
    if(selector==='[data-harness-action]')return actions;
    if(selector==='button')return [...actions,save];
    if(selector==='input[name]')return inputs;
    return [...inputs,save];
  }};
  let handler=async()=>value;
  const value={config:Object.fromEntries(inputs.map(x=>[x.name,x.value])),state:'stopped',running:false,logs:[]};
  const api={harness:body=>{calls.push(body);return handler(body);},open:async target=>calls.push(target)};
  const panel=new HarnessPanel({root,api,t:x=>x,feedback:error=>errors.push(error)});panel.render(value);
  return {panel,calls,inputs,errors,value,setHandler(fn){handler=fn;},button:action=>actions.find(x=>x.dataset.harnessAction===action)};
}
test('Harness running state disables edits and duplicate starts, permits stop and open',async()=>{
 const f=fixture();f.panel.render({...f.value,state:'running',running:true});
 assert.ok(f.inputs.every(x=>x.disabled));assert.ok(f.button('start').disabled);assert.ok(!f.button('stop').disabled);assert.ok(!f.button('open').disabled);
 await f.panel.action('open');assert.deepEqual(f.calls,['harness']);
});
test('unsaved configuration cannot start; save uses only Harness fields',async()=>{
 const f=fixture();f.panel.dirty=true;await f.panel.action('start');assert.equal(f.calls.length,0);assert.match(f.errors[0],/保存/);
 await f.panel.action('save');assert.deepEqual(f.calls,[{action:'save',config:f.value.config}]);assert.equal(f.panel.dirty,false);
});
test('pending mutation ignores repeated clicks and protects edits',async()=>{
 const f=fixture();let finish;f.setHandler(()=>new Promise(resolve=>finish=resolve));
 const pending=f.panel.action('start');await f.panel.action('start');assert.equal(f.calls.length,1);assert.ok(f.button('start').disabled);
 finish({...f.value,state:'running',running:true});await pending;assert.ok(f.panel.value.running);
});
test('status from an old data directory cannot populate the new form',async()=>{
 const f=fixture();let finish;f.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=f.panel.refresh();f.panel.clear();finish({...f.value,state:'running',running:true});await pending;assert.equal(f.panel.value,null);assert.ok(f.inputs.every(input=>input.value===''));
});
