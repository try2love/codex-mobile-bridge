'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {ComposerDrafts}=require('../web/features/chat/composer-drafts.js');
function storage(){const rows=new Map();return{getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,String(value)),removeItem:key=>rows.delete(key)};}
function fixture(){
 const local=storage(),temporary=storage(),drafts=new ComposerDrafts(local),host='wsl:'+'a'.repeat(32),nodes={message:{value:'',selectionStart:0,selectionEnd:0,setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}},'send-mode':{value:'send'},'work-mode':{value:'default'}};
 const context={composerToolbar:undefined,currentId:'original-thread',currentHost:host,draftGateway:'gateway',codexDraftScope:null,codexDraftTouched:false,restoringComposer:false,composerDrafts:drafts,codexProvisionalDrafts:new Map(),selectedSkills:new Set(),codexQuotes:null,sessionStorage:temporary,window:{},renderCodexWorkModes(){},workModes:{selection:null,render(){}},state:{},sending:false,messageManagement:null,renderSkillPills(){},$:id=>nodes[id]};context.chatKey=()=>context.currentHost+'|'+context.currentId;
 const source=fs.readFileSync('web/shell/app.js','utf8'),start=source.indexOf('function saveDraft(){'),end=source.indexOf("$('message').oninput",start);assert.ok(start>0&&end>start);vm.runInNewContext(source.slice(start,end),context);
 const scope=account=>({gateway:'gateway',provider:'codex',account,host,session:'original-thread'}),old='1'.repeat(64),current='2'.repeat(64),view={id:'original-thread',host,draftAccountIdentity:current,draftContinuation:{id:'original-thread',entry:host,accountIdentity:current,previousAccountIdentities:[old]}};
 return{context,drafts,temporary,nodes,scope,old,current,view};
}
test('Codex WSL fresh detail continues the same original thread draft after another client switches accounts',()=>{
 const ui=fixture();ui.drafts.save(ui.scope(ui.old),{text:'phone unsent draft',selectionStart:2,selectionEnd:6,});ui.context.bindCodexDraft(ui.view);assert.equal(ui.nodes.message.value,'phone unsent draft');assert.equal(ui.nodes.message.selectionStart,2);assert.equal(ui.context.codexDraftScope.account,ui.current);assert.equal(ui.nodes['send-mode'].value,'send');assert.equal(ui.drafts.pendingSend(ui.scope(ui.current)),null);
});
test('Codex continuation cannot cross Desktop SSH gateway host session or account scopes',()=>{
 for(const mutation of [{host:'local'},{host:'ssh:other'},{gateway:'another'},{view:{id:'other'}},{hint:{entry:'wsl:'+'b'.repeat(32)}},{hint:{accountIdentity:'3'.repeat(64)}},{hint:null}]){
  const ui=fixture();ui.drafts.save(ui.scope(ui.old),{text:'private original draft'});if(mutation.host)ui.context.currentHost=mutation.host;if(mutation.gateway)ui.context.draftGateway=mutation.gateway;
  const view={...ui.view,...mutation.view,draftContinuation:mutation.hint===null?null:{...ui.view.draftContinuation,...mutation.hint}};ui.context.bindCodexDraft(view);assert.equal(ui.nodes.message.value,'');
 }
});
test('Codex WSL continuation never migrates durable or temporary unknown submission records',()=>{
 for(const kind of ['durable','temporary']){const ui=fixture();ui.drafts.save(ui.scope(ui.old),{text:'possibly submitted'});const record={id:'unknown-original',gateway:'gateway',account:ui.old,target:ui.view.host+'|original-thread',text:'possibly submitted'};
  if(kind==='durable')ui.drafts.begin(ui.scope(ui.old),{text:record.text},record.id);else ui.temporary.setItem('pending:'+record.target,JSON.stringify(record));
  ui.context.bindCodexDraft(ui.view);assert.equal(ui.nodes.message.value,'');assert.equal(ui.drafts.pendingSend(ui.scope(ui.current)),null);if(kind==='durable')assert.equal(ui.drafts.pendingSend(ui.scope(ui.old)).id,record.id);else assert.equal(JSON.parse(ui.temporary.getItem('pending:'+record.target)).id,record.id);
 }
});
