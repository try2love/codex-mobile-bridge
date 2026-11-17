'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {ComposerDrafts}=require('../web/features/chat/composer-drafts.js');
const {ComposerQuotes}=require('../web/features/chat/composer-quotes.js');

for(const status of ['accepted','unknown'])test(`Codex fallback ${status} preserves newer quote-only edits when durable storage fails`,async()=>{
 const nodes=new Map(['composer','message','send-mode','send-error','send'].map(id=>[id,{value:id==='send-mode'?'send':'',textContent:''}]));
 const failedStorage={getItem:()=>null,setItem:()=>{throw Error('quota');},removeItem(){}};
 const scope={gateway:'gateway',provider:'codex',account:'account',host:'local',session:'chat'};
 const drafts=new ComposerDrafts(failedStorage),quotes={quotes:[{text:'first',source:'reply',provider:'codex'}],serialize(text){return ComposerQuotes.serialize(text,this.quotes);},reset(){this.quotes=[];}};
 const temporary=new Map(),provisionals=new Map();let resolve,submitted,entered;const requestStarted=new Promise(done=>entered=done);
 const context={composerToolbar:{actionMode:()=>'send'},
  $:id=>nodes.get(id),t:text=>text,sending:false,currentId:'chat',currentHost:'local',state:{},currentAccess:()=>({canSend:true}),
  codexQuotes:quotes,codexDraftScope:scope,composerDrafts:drafts,draftGateway:'gateway',codexProvisionalDrafts:provisionals,messageManagement:null,
  attachments:{ready:()=>true,ids:()=>[],references:()=>[],rows:[],setLocked(){},clear(){}},workModes:{value:()=> 'default',render(){},submitted(){}},
  invalidSkills:new Set(),selectedSkills:new Set(),BridgeI18n:{locale:()=> 'zh'},chatKey:()=> 'local|chat',uuid:()=> 'send-id',
  saveDraft(){const fields={text:nodes.get('message').value,quotes:quotes.quotes};const saved=drafts.save(scope,fields);if(!saved)provisionals.set('local|chat',JSON.parse(JSON.stringify(fields)));return saved;},
  sessionStorage:{getItem:key=>temporary.get(key)||null,setItem:(key,value)=>temporary.set(key,value),removeItem:key=>temporary.delete(key)},
  sessionUrl:()=>'/send',api:async(url,body)=>{submitted=body;entered();return new Promise(done=>resolve=done);},bumpRecency(){},toast(){},
  renderSkillPills(){},restoringComposer:false,window:{BridgePresentation:{resizeMessage(){},showError(){}}}
 };
 const source=fs.readFileSync(require.resolve('../web/shell/app.js'),'utf8'),start=source.indexOf("$('composer').onsubmit="),end=source.indexOf("$('message').onkeydown=",start);
 vm.runInNewContext(source.slice(start,end),context);
 const sending=nodes.get('composer').onsubmit({preventDefault(){}});await requestStarted;assert.equal(submitted.text,'> first');assert.equal(drafts.pending(scope),null);
 quotes.quotes.push({text:'later',source:'reply-2',provider:'codex'});context.saveDraft();assert.equal(nodes.get('message').value,'');resolve({status});await sending;
 assert.deepEqual(quotes.quotes.map(row=>row.text),['first','later']);assert.deepEqual(provisionals.get('local|chat').quotes.map(row=>row.text),['first','later']);assert.equal(nodes.get('message').value,'');
 assert.equal(JSON.parse(temporary.get('pending:local|chat')||'null')?.id||null,status==='unknown'?'send-id':null,'Unknown submission retains its original private ID');
});
