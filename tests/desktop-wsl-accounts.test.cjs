'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node {
  constructor(tag,text=''){this.tagName=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.dataset={};this.classList={add(){}};}
  append(...nodes){this.children.push(...nodes);}prepend(...nodes){this.children.unshift(...nodes);}replaceChildren(...nodes){this.children=nodes;}
  before(node){this.wrapper=node;}setAttribute(){}focus(){}remove(){}
  get options(){return this.children;}
}
function all(node){return[node,...node.children.flatMap(child=>child instanceof Node?all(child):[])];}
const renderer=fs.readFileSync('desktop/shell/renderer.js','utf8');
const wiring=renderer.slice(renderer.indexOf('const wslAccountScopes=[];'),renderer.lastIndexOf("document.addEventListener('bridge-client-config',"));
async function fixture(){
  const local=new Node('article'),cli=new Node('div'),calls=[];let accounts=[];
  const state=()=>({provider:'codex',entry:'wsl-test',identity:'fixture',available:true,target:{distribution:'Fixture',user:'test'},blockers:['自定义 provider 路由','runningCLI'],capabilities:{save:true,import:false,switch:false,models:false},accounts:accounts.map(row=>({...row}))});
  const context={window:{loadWslAccounts:async()=>{}},activeTab:'overview',document:{createElement:tag=>new Node(tag),addEventListener(){},removeEventListener(){}},Option:class extends Node{constructor(text,value){super('option',text);this.value=value;}},$:id=>id==='accounts-content'?local:id==='cli-access-content'?cli:null,t:key=>key,BridgeI18n:{t:key=>key,locale:()=> 'en'},Date,clearTimeout,desktopConnections:null,accountsPanel:{visible:()=>true,clear(){},refresh(){}},codexCliAccessPanel:{clear(){},refresh(){}},claudeCliAccessPanel:null,api:{platform:'win32',cliAccess:async value=>{
    calls.push({...value});assert.equal(value.action,'wslAccounts');assert.equal(value.provider,'codex');
    if(value.operation==='environments')return{environments:[{id:'wsl-test',distribution:'Fixture',user:'test'}]};
    assert.equal(value.entry,'wsl-test');
    if(value.operation==='save-api'){assert.equal(value.apiKey,'fake-key-for-isolated-test');accounts=[{id:value.id||'saved-fixture',kind:'api',name:value.name,baseUrl:value.baseUrl,model:value.model}];}
    else assert.equal(value.operation,'list');
    return state();
  }}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('web/features/accounts/wsl-accounts.js','utf8')+'\n'+wiring+'\nthis.scopes=wslAccountScopes;',context);
  await new Promise(setImmediate);await context.scopes[0].load();const select=all(local.wrapper).find(node=>node.tagName==='select');select.value='wsl-test';select.onchange();
  await new Promise(resolve=>setImmediate(resolve));return{context,local,cli,calls,panel:context.scopes[0].panel};
}
async function submit(panel,name){
  const form=all(panel.root).find(node=>node.tagName==='form'),inputs=all(form).filter(node=>node.tagName==='input');
  assert.equal(inputs.length,4);[name,'https://example.invalid/v1','fake-model','fake-key-for-isolated-test'].forEach((value,index)=>inputs[index].value=value);
  form.onsubmit({preventDefault(){}});await new Promise(resolve=>setImmediate(resolve));
}
test('desktop WSL wiring can add and edit isolated API credentials despite switch blockers',async()=>{
  const {panel,calls,local,cli}=await fixture();assert.equal(panel.desktop,true);assert.equal(local.hidden,true);assert.equal(cli.hidden,false,'Codex no longer has a separate API editor');
  await submit(panel,'Added fixture');assert.equal(panel.value.accounts[0].name,'Added fixture');assert.equal(panel.key.value,'');
  const edit=all(panel.root).find(node=>node.tagName==='button'&&node.textContent==='编辑');assert.ok(edit);edit.onclick();
  await submit(panel,'Edited fixture');assert.equal(panel.value.accounts[0].name,'Edited fixture');
  const saves=calls.filter(value=>value.operation==='save-api');assert.equal(saves.length,2);assert.equal(saves[0].id,undefined);assert.equal(saves[1].id,'saved-fixture');
  assert.ok(saves.every(value=>value.entry==='wsl-test'&&value.action==='wslAccounts'));assert.equal(calls.some(value=>['switch','models','import-current'].includes(value.operation)),false);
});
