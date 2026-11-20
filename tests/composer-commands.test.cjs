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
test('native slash commands insert on choose, preserve underscores, pass through submit, and surface busy disabledReason',()=>{
  const calls=[],notices=[],events=[];
  let selection=[],focused=false;
  const nativeActions=[
    {id:'boost',label:'Invoke Boost',category:'system',enabled:true,native:true,args:true},
    {id:'generative_ui',label:'Render widgets',category:'skill',enabled:true,native:true,args:true},
    {id:'deploy-flow',label:'Deploy workflow',category:'workflow',enabled:true,native:true,args:true},
    {id:'plan',label:'Plan carefully',category:'system',enabled:true,native:true,args:true},
    {id:'compact',label:'Compact conversation',category:'system',enabled:true,native:true,args:true},
  ];
  const input={
    value:'/bo',disabled:false,
    dispatchEvent:e=>events.push(e.type),
    setSelectionRange:(s,e)=>{selection=[s,e];},
    focus:()=>{focused=true;},
    setAttribute(){},removeAttribute(){},
  };
  const nodes=[];
  const makeNode=(tag)=>({
    tag,children:[],attributes:{},textContent:'',style:{},
    setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
    append(...items){this.children.push(...items);},
    replaceChildren(...items){this.children=[...items];},
    scrollIntoView(){},
  });
  global.document={activeElement:input,createElement:makeNode};
  const menu=makeNode('div');menu.hidden=true;
  const picker=Object.assign(Object.create(ComposerCommands.prototype),{
    input,menu,actions:()=>nativeActions,scope:()=>'agy-1',index:0,rows:[],onError:text=>notices.push(text),
  });
  picker.refresh();
  assert.equal(menu.hidden,false);
  assert.deepEqual(picker.rows.map(r=>r.id),['boost']);
  assert.equal(picker.choose('boost','agy-1'),true);
  assert.equal(input.value,'/boost ');
  assert.deepEqual(selection,[7,7]);
  assert.equal(focused,true);
  assert.equal(events.includes('input'),true);

  input.value='/';
  picker.refresh();
  assert.equal(menu.children[1].children[0].textContent,'/generative_ui');
  assert.equal(menu.children[1].children[1].textContent,'Skill · Render widgets');
  assert.equal(menu.children[2].children[1].textContent,'工作流 · Deploy workflow');

  for(const text of ['/boost refactor auth','/generative_ui chart','/plan api','/compact','/compact keep summary','/boost ']){
    input.value=text;
    assert.equal(picker.consume(),false,text);
    assert.equal(notices.length,0,text);
  }
  for(const row of nativeActions){row.enabled=false;row.disabledReason='原会话运行中，排队或插话暂不支持 /指令 与 Skill';}
  input.value='/boost wait';
  assert.equal(picker.consume(),true);
  assert.equal(input.value,'/boost wait');
  assert.equal(notices[0],'原会话运行中，排队或插话暂不支持 /指令 与 Skill');
  assert.deepEqual(calls,[]);
});
