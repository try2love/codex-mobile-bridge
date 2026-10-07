'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Node{
  constructor(tag){this.tag=tag;this.childNodes=[];this.dataset={};this.attributes={};this.value='';}
  get children(){return this.childNodes.filter(n=>n.nodeType!==3);}
  get textContent(){return this.childNodes.map(n=>n.textContent).join('');}
  set textContent(text){this.childNodes=[{nodeType:3,textContent:text}];}
  append(...nodes){this.childNodes.push(...nodes);}
  replaceChildren(...nodes){this.childNodes=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}
  getAttribute(key){return this.attributes[key];}
  querySelectorAll(selector){return this.children.flatMap(n=>[...(selector==='button'?n.tag==='button':selector==='[data-i18n]'?!!n.dataset.i18n:false)?[n]:[],...n.querySelectorAll(selector)]);}
  closest(){return {hidden:false};}
}
async function fixture(){
  const panel=new Node('section'),listeners={},calls=[];let state={registered:false};
  const document={documentElement:new Node('html'),hidden:false,createElement:tag=>new Node(tag),querySelector:()=>panel,querySelectorAll:()=>[],
    addEventListener:(name,fn)=>{listeners[name]=fn;},dispatchEvent:event=>listeners[event.type]?.(event)};
  const context=vm.createContext({document,localStorage:{getItem:()=> 'en',setItem(){}},CustomEvent:class{constructor(type){this.type=type;}},
    window:{bridgeDesktop:{sharedRelay:async value=>{calls.push(value);return state;}}},setInterval(){},clearInterval(){},setTimeout(){},clearTimeout(){},confirm:()=>true});
  const run=code=>vm.runInContext(code,context);
  for(const file of ['web/i18n.js','desktop/shared-relay.js'])run(fs.readFileSync(path.join(__dirname,'..',file),'utf8'));
  await new Promise(setImmediate);
  const root=panel.children[0],all=()=>[root,...walk(root)];
  function walk(node){return node.children.flatMap(n=>[n,...walk(n)]);}
  return {root,calls,all,run,setState:value=>state=value,language:value=>run(`BridgeI18n.setLanguage('${value}');BridgeI18n.apply();`)};
}
test('relay follows asynchronous desktop language and keeps typed registration fields',async()=>{
  const ui=await fixture();assert.equal(ui.root.children[0].textContent,'Shared relay · Preview');
  const input=ui.all().find(n=>n.id==='relay-invitation');input.value='unsaved-invitation';
  ui.language('zh');assert.equal(ui.root.children[0].textContent,'共享中继 · 内测');
  assert.match(ui.root.textContent,/尚未连接共享中继/);assert.equal(input.value,'unsaved-invitation');
  ui.language('en');assert.equal(ui.root.children[0].textContent,'Shared relay · Preview');assert.match(ui.root.textContent,/No shared relay registered/);
  assert.deepEqual(ui.calls.map(v=>v.action),['status']);
});
test('relay language changes relabel registered status and phone actions without another request',async()=>{
  const ui=await fixture();ui.setState({registered:true,enabled:true,online:true,deviceName:'Fixture computer',pending:[{id:'p',name:'Fixture phone'}],phones:[]});
  await ui.all().find(n=>n.tag==='button'&&n.textContent==='Refresh status').onclick();
  ui.language('zh');assert.match(ui.root.textContent,/Fixture computer · 在线/);assert.match(ui.root.textContent,/等待批准：Fixture phone/);
  assert.ok(ui.all().some(n=>n.tag==='button'&&n.textContent==='批准'));
  ui.language('en');assert.match(ui.root.textContent,/Fixture computer · Online/);assert.ok(ui.all().some(n=>n.tag==='button'&&n.textContent==='Approve'));
  assert.equal(ui.calls.length,2);
});
