'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/features/accounts/api-editor.js'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'../web/features/accounts/cli-access.js'),'utf8');
const context={crypto:require('node:crypto').webcrypto,BridgeI18n:{t:key=>({'正在读取…':'Loading…','正在保存…':'Saving…','请填写有效 API Key':'Enter a valid API key.'}[key]||key)}};
vm.createContext(context);vm.runInContext(source+'\nglobalThis.CliAccessPanel=CliAccessPanel;',context);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function panel(){
  const controls=[{disabled:false},{disabled:false}],instance=Object.create(context.CliAccessPanel.prototype);
  instance.root={querySelectorAll:()=>controls};instance.value=null;instance.busy=false;instance.generation=0;instance.apiResults=new Map();instance.switchRequests=new Map();
  instance.provider='claude';instance.key={input:{value:'private-key'}};
  instance.cancel={hidden:false};instance.status={textContent:''};instance.error={textContent:''};
  instance.form={reset(){instance.key.input.value='';}};
  instance.render=()=>{instance.rendered=(instance.rendered||0)+1;};
  return {instance,controls};
}
test('clearing CLI access scrubs the key and ignores a late read',async()=>{
  const {instance,controls}=panel(),pending=deferred();instance.read=()=>pending.promise;
  const reading=instance.refresh();assert.equal(instance.busy,true);assert.equal(controls[0].disabled,true);
  instance.clear();assert.equal(instance.key.input.value,'');assert.equal(instance.busy,false);assert.equal(controls[0].disabled,false);
  pending.resolve({claude:{accounts:[{name:'stale'}]}});await reading;
  assert.equal(instance.value,null);assert.equal(instance.status.textContent,'');assert.equal(instance.error.textContent,'');
});
test('closing during save ignores its late result and translates a later error',async()=>{
  const {instance}=panel(),pending=deferred(),request={provider:'claude',action:'saveApi',apiKey:'private-key'};
  instance.request=()=>pending.promise;
  const saving=instance.perform(request);instance.clear();pending.resolve({savedId:'stale'});await saving;
  assert.equal(instance.value,null);assert.equal(instance.key.input.value,'');assert.equal(request.apiKey,'');
  instance.request=async()=>{throw Error('请填写有效 API Key');};
  await instance.perform({provider:'claude',action:'saveApi'});
  assert.equal(instance.error.textContent,'Enter a valid API key.');assert.equal(instance.busy,false);
});
test('scoped model probe never switches accounts and ignores a late response after closing',async()=>{
  const {instance}=panel(),pending=deferred(),calls=[];
  instance.request=value=>{calls.push(value);return pending.promise;};
  const row={id:'saved-api'},checking=instance.probe(row,'models');
  assert.equal(JSON.stringify(calls),JSON.stringify([{provider:'claude',action:'models',id:'saved-api'}]));
  instance.clear();pending.resolve({probe:{provider:'claude',id:'saved-api',status:'reachable',scope:'models',models:['model-a']}});
  await checking;assert.equal(instance.value,null);assert.equal(instance.apiResults.size,0);
});
test('CLI model probe keeps successful rows and update time during a failed refresh',async()=>{
  const {instance}=panel(),row={id:'saved-api',modelCatalogIdentity:'a'.repeat(64)},success={probe:{provider:'claude',id:row.id,status:'reachable',scope:'models',models:['model-a'],cache:{identity:'a'.repeat(64),updatedAt:1000}}};
  instance.request=async()=>success;await instance.probe(row,'models');assert.equal(instance.apiResults.get(row.id).cache.updatedAt,1000);
  const pending=deferred();let call;instance.request=value=>{call=value;return pending.promise;};const refreshing=instance.probe(row,'models',true);assert.equal(call.refresh,true);assert.deepEqual(instance.apiResults.get(row.id).models,['model-a']);assert.equal(instance.apiResults.get(row.id).cache.updatedAt,1000);
  pending.reject(Error('Offline'));await refreshing;assert.deepEqual(instance.apiResults.get(row.id).models,['model-a']);assert.equal(instance.apiResults.get(row.id).cache.updatedAt,1000);assert.equal(instance.apiResults.get(row.id).error,'Offline');
});
function renderedPanel(){
  class Node{constructor(tag='div'){this.tag=tag;this.children=[];this._text='';this.className='';this.hidden=false;this.disabled=false;this.classList={add:name=>this.className+=' '+name};}set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;this._text='';}}
  const {instance}=panel(),row={id:'saved-api',name:'Saved API',kind:'api',baseUrl:'https://api.example/v1',model:'model-a',modelCatalogIdentity:'a'.repeat(64)};
  instance.node=(tag,key,className='')=>{const result=new Node(tag);result.className=className;if(key)result.textContent=instance.t(key);return result;};
  instance.rows=new Node();instance.status=new Node();instance.schemeRow=new Node();instance.baseUrl={label:new Node()};instance.model={label:new Node()};instance.key.label=new Node();instance.save=new Node();
  instance.render=context.CliAccessPanel.prototype.render.bind(instance);context.BridgeI18n.locale=()=> 'en-US';
  const result=probe=>({claude:{accounts:[row],activeId:row.id},probe:{provider:'claude',id:row.id,status:'reachable',scope:'models',...probe}});
  instance.value=result({});instance.render();return {instance,row,result};
}
test('rendered CLI connectivity success and failure remain visible without catalog cache',async()=>{
  const {instance,row,result}=renderedPanel();instance.request=async()=>result({modelCount:1,models:null});
  await instance.probe(row,'test');assert.match(instance.rows.textContent,/模型接口连接成功/);
  instance.request=async()=>{throw Error('Connection failed');};await instance.probe(row,'test');assert.match(instance.rows.textContent,/Connection failed/);
});
test('rendered CLI model refresh shows retained rows, timestamp, and backend modelError',async()=>{
  const {instance,row,result}=renderedPanel(),cache={identity:row.modelCatalogIdentity,updatedAt:1000,state:'fresh'};
  instance.request=async()=>result({models:['model-a'],cache});await instance.probe(row,'models');assert.match(instance.rows.textContent,/更新于：/);
  instance.request=async()=>result({models:['model-a'],cache:{...cache,state:'stale'},modelError:'模型目录刷新失败，已保留上次结果'});await instance.probe(row,'models',true);
  assert.match(instance.rows.textContent,/model-a/);assert.match(instance.rows.textContent,/模型目录刷新失败，已保留上次结果/);assert.match(instance.rows.textContent,/更新于：/);
});

test('unified Codex editor suppresses duplicate inventory while retaining model probe feedback',async()=>{
 const {instance,row,result}=renderedPanel();instance.provider='codex';instance.formOnly=true;
 instance.value={codex:{accounts:[row]}};instance.render();assert.equal(instance.rows.children.length,0);
 instance.request=async()=>({...result({models:['model-a']}),codex:{accounts:[row]},probe:{provider:'codex',id:row.id,status:'reachable',scope:'models',models:['model-a'],cache:{identity:row.modelCatalogIdentity,updatedAt:1000}}});
 await instance.probe(row,'models');assert.equal(instance.rows.children.length,0);assert.match(instance.status.textContent,/model-a/);
});

function draftPanel(){
 const {instance}=panel();instance.provider='codex';instance.draftRevision=0;
 instance.baseUrl={input:{value:'https://example.invalid/v1'}};instance.model={input:{value:'',id:'model'},label:{}};instance.modelCaption={};
 instance.modelSelect={id:'model-select',hidden:true,value:'',replaceChildren(...children){this.children=children;this.value=children[0]?.value||'';}};
 instance.fetchModels={};instance.save={};instance.manualModel={};instance.modelFeedback={};instance.node=()=>({});
 return instance;
}
test('draft model fetch fills the default without saving, and manual input survives a response',async()=>{
 const ui=draftPanel(),calls=[];ui.request=async request=>{calls.push({...request});return {models:['first','second']};};
 await ui.discoverModels();assert.equal(ui.model.input.value,'first');assert.equal(ui.modelSelect.value,'first');assert.equal(ui.modelSelect.hidden,false);assert.equal(ui.save.disabled,false);assert.equal(calls[0].action,'modelsDraft');assert.equal(calls.length,1);
 ui.clearDraftModels();assert.equal(ui.model.input.value,'');ui.model.input.value='custom-model';await ui.discoverModels();assert.equal(ui.model.input.value,'custom-model');assert.equal(ui.modelSelect.hidden,true);assert.equal(ui.manualModel.hidden,false);
});
test('URL or key changes invalidate draft models without allowing an old response to replace the new one',async()=>{
 const ui=draftPanel(),first=deferred(),second=deferred();let call=0;ui.request=()=>++call===1?first.promise:second.promise;
 const old=ui.discoverModels();assert.equal(ui.save.disabled,true);ui.baseUrl.input.value='https://new.invalid/v1';ui.clearDraftModels();const fresh=ui.discoverModels();
 first.resolve({models:['stale']});await old;assert.equal(ui.model.input.value,'');assert.equal(ui.fetchModels.disabled,true);
 second.resolve({models:['fresh']});await fresh;assert.equal(ui.model.input.value,'fresh');assert.equal(ui.fetchModels.disabled,false);
 ui.key.input.value='new-key';ui.clearDraftModels();assert.equal(ui.model.input.value,'');assert.equal(ui.modelSelect.hidden,true);
});
test('empty or failed model lookup keeps manual entry available and never saves',async()=>{
 const ui=draftPanel();ui.request=async()=>({models:[]});await ui.discoverModels();assert.match(ui.modelFeedback.textContent,/手动填写/);assert.equal(ui.modelSelect.hidden,true);assert.equal(ui.save.disabled,false);
 ui.request=async()=>{throw Error('Offline');};await ui.discoverModels();assert.equal(ui.modelFeedback.textContent,'Offline');assert.equal(ui.save.disabled,false);
});


test('Codex shared auth confirmation retains a request identifier through unknown retries',()=>{
 const {instance}=panel();instance.provider='codex';const calls=[],nodes=[];
 instance.node=(tag,key)=>({tag,textContent:key||'',children:[],append(...children){this.children.push(...children);},remove(){}});
 instance.rows={prepend(node){nodes.push(node);}};
 instance.field=()=>({input:{checked:false},label:{}});
 const fields=[];instance.field=()=>{const field={input:{checked:false},label:{}};fields.push(field);return field;};
 instance.perform=value=>calls.push(value);
 instance.switchTo('codex','saved',{sharedWithDesktop:true});
 let dialog=nodes.at(-1),submit=dialog.children.at(-2);
 assert.match(dialog.children[0].textContent,/同步共享认证/);
 submit.onclick();assert.equal(calls.length,0);
 fields.slice(-2).forEach(field=>field.input.checked=true);submit.onclick();
 const first=calls[0];assert.equal(first.tasksConfirmed,true);assert.equal(first.desktopRestartConfirmed,true);
 assert.match(first.requestId,/^[0-9a-f-]{36}$/);
 instance.switchTo('codex','saved',{sharedWithDesktop:true});
 dialog=nodes.at(-1);fields.slice(-2).forEach(field=>field.input.checked=true);dialog.children.at(-2).onclick();
 assert.equal(calls[1].requestId,first.requestId);
});

test('Codex unknown read keeps its receipt while a verified completion releases it',async()=>{
 const {instance}=panel();instance.provider='codex';instance.switchRequests.set('saved','stable-request');
 instance.read=async()=>({codex:{switch:{phase:'authUnknown',targetId:'saved'},accounts:[]}});
 await instance.refresh();assert.equal(instance.switchRequests.get('saved'),'stable-request');
 instance.read=async()=>({codex:{switch:{phase:'complete',targetId:'saved'},accounts:[]}});
 await instance.refresh();assert.equal(instance.switchRequests.has('saved'),false);
});
