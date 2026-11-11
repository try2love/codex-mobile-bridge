const test=require('node:test'),assert=require('node:assert/strict');
const {ComposerCommands}=require('../web/features/chat/composer-commands.js');
global.BridgeI18n={t:value=>value};

function fixture(text){
  let scope='original';const calls=[],notices=[];
  const action={id:'plan',label:'计划模式',enabled:true,args:true,run:()=>calls.push('plan')};
  const input={value:text,dispatchEvent:event=>calls.push(event.type),setAttribute(){},removeAttribute(){}};
  const picker=Object.assign(Object.create(ComposerCommands.prototype),{input,menu:{hidden:false},actions:()=>[action],scope:()=>scope,onError:text=>notices.push(text)});
  return {picker,input,action,calls,notices,changeScope:()=>scope='another'};
}
test('plan command preserves its prompt without sending and does not touch unrelated drafts',()=>{
  const f=fixture('/plan 保留这段输入');assert.equal(f.picker.consume(),true);
  assert.equal(f.input.value,'保留这段输入');assert.deepEqual(f.calls,['input','plan']);
});
test('unavailable known commands and unsupported arguments remain drafts instead of model messages',()=>{
  for(const text of ['/plan text','/compact','/model unsupported']){
    const f=fixture(text);f.action.enabled=false;assert.equal(f.picker.consume(),true);assert.equal(f.input.value,text);assert.deepEqual(f.calls,[]);assert.equal(f.notices.length,1);
  }
});
test('ordinary paths and unknown native custom commands retain the existing send path',()=>{
  for(const text of ['/Users/me/file','/plugin:custom arg','/custom-skill arg','请看 /plan','text\n/plan']){
    const f=fixture(text);assert.equal(f.picker.consume(),false);assert.equal(f.input.value,text);assert.deepEqual(f.calls,[]);
  }
});
test('stale menu callbacks cannot affect the next session or newly disabled capabilities',()=>{
  const f=fixture('/plan');f.changeScope();assert.equal(f.picker.choose('plan','original'),false);assert.equal(f.input.value,'/plan');assert.deepEqual(f.calls,[]);
  f.action.enabled=false;assert.equal(f.picker.choose('plan','another'),false);assert.deepEqual(f.calls,[]);
});
test('destructive shortcuts open the existing confirmation pages without mutating',()=>{
  const pages=[],context={rename(){throw Error('no write');},archive(){throw Error('no write');},deletion(){throw Error('no write');}};
  const actions=ComposerCommands.session(context,page=>pages.push(page));
  for(const id of ['rename','archive','delete'])actions.find(row=>row.id===id).run();
  assert.deepEqual(pages,['rename','archive','delete']);assert.equal(ComposerCommands.session({},()=>{}).some(row=>row.id==='delete'),false);
});
test('shared controls keep native capability and busy gates and only open compaction confirmation',()=>{
  const calls=[],context={state:{connected:true,capabilities:{compact:true}},open:()=>calls.push('open')};
  const model={hidden:false,disabled:true,click:()=>calls.push('model')};
  const rows=ComposerCommands.controls({model,context});assert.equal(rows.find(row=>row.id==='model').enabled,false);
  rows.find(row=>row.id==='compact').run();assert.deepEqual(calls,['open']);context.state.busy=true;
  assert.equal(ComposerCommands.controls({context}).find(row=>row.id==='compact').enabled,false);
});
