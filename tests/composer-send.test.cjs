'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {ComposerDrafts}=require('../web/features/chat/composer-drafts.js');

function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};}
function fixture({durable=true,request}={}){
  const local=storage(),temporary=storage(),drafts=new ComposerDrafts(local);
  const scope={gateway:'gateway',provider:'codex',account:'account',host:'local',session:'fixture'};
  const nodes={composer:{},message:{value:'same text'},'send-mode':{value:'send'},send:{},'send-error':{}};
  const calls=[];let sequence=0;
  const context={$:id=>nodes[id],t:value=>value,BridgeI18n:{locale:()=> 'zh-CN'},
    sending:false,currentId:'fixture',currentHost:'local',state:{},currentAccess:()=>({canSend:true}),
    attachments:{ready:()=>true,ids:()=>['attachment'],setLocked(){}},
    workModes:{value:()=> 'default',render(){}},invalidSkills:new Set(),selectedSkills:new Set(['skill']),
    codexProvisionalDrafts:new Map(),composerDrafts:drafts,codexDraftScope:durable?scope:null,
    sessionStorage:temporary,draftGateway:'gateway',chatKey:()=> 'local|fixture',uuid:()=> 'submission-'+(++sequence),
    saveDraft:()=>drafts.save(scope,{text:nodes.message.value,sendMode:nodes['send-mode'].value,skills:['skill']}),
    window:{BridgePresentation:{showError(){}}},sessionUrl:()=> '/synthetic/send',toast(){},
    api:async(url,body)=>{calls.push(body);return request?request(body):{status:'unknown'};}};
  const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
  const start=source.indexOf("$('composer').onsubmit=async event=>{"),end=source.indexOf("$('message').onkeydown",start);
  assert.ok(start>=0&&end>start,'Exercise the real composer submit handler');
  vm.runInNewContext(source.slice(start,end),context);
  return {nodes,calls,drafts,scope,context,temporary,
    submit:()=>nodes.composer.onsubmit({preventDefault(){}}),
    pending:()=>durable?drafts.pending(scope):JSON.parse(temporary.getItem('pending:local|fixture'))};
}

for(const durable of [true,false]){
  test(`explicit rejection releases ${durable?'durable':'legacy'} pending ID and allows queueing`,async()=>{
    let first=true;
    const ui=fixture({durable,request:()=>{if(first){first=false;throw Object.assign(Error('busy'),{status:400,code:'send_not_submitted'});}return {status:'unknown'};}});
    await ui.submit();assert.equal(ui.pending(),null);
    assert.equal(ui.nodes.message.value,'same text');
    assert.equal(ui.drafts.load(ui.scope).text,'same text');
    ui.nodes['send-mode'].value='queue';await ui.submit();
    assert.equal(ui.calls.length,2);assert.equal(ui.calls[1].mode,'queue');
    assert.notEqual(ui.calls[0].id,ui.calls[1].id);
    assert.deepEqual(Array.from(ui.calls[1].attachments),['attachment']);
    assert.deepEqual(Array.from(ui.calls[1].skills),['skill']);
    assert.equal(ui.pending().id,ui.calls[1].id,'Unknown delivery must still retain its ID');
  });

  for(const error of [{status:500,code:'send_not_submitted'},
    {status:400,message:'Codex 正在执行。请选择「排队发送」或「补充当前任务」。'},
    {status:409,code:'desktop_unavailable'},{message:'network disconnected'}]){
    test(`${durable?'durable':'legacy'} pending ID survives ${JSON.stringify(error)}`,async()=>{
      const ui=fixture({durable,request:()=>{throw Object.assign(Error('fixture failure'),error);}});
      await ui.submit();assert.equal(ui.pending().id,ui.calls[0].id);
      if(durable){ui.nodes['send-mode'].value='queue';await ui.submit();assert.equal(ui.calls.length,1);}
    });
  }

  test(`late rejection cannot discard a newer ${durable?'durable':'legacy'} pending request`,async()=>{
    let reject;
    const ui=fixture({durable,request:()=>new Promise((resolve,fail)=>reject=fail)});
    const sending=ui.submit();
    if(durable)ui.drafts.begin(ui.scope,{text:'new text'},'new-id');
    else ui.temporary.setItem('pending:local|fixture',JSON.stringify({id:'new-id'}));
    ui.context.currentId='another-chat';
    reject(Object.assign(Error('busy'),{status:400,code:'send_not_submitted'}));
    await sending;assert.equal(ui.pending().id,'new-id');
  });
}
