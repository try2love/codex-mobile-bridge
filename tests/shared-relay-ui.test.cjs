'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Node{
  constructor(tag){this.tag=tag;this.childNodes=[];this.dataset={};this.attributes={};this.value='';}
  get children(){return this.childNodes.filter(n=>n.nodeType!==3);}
  get textContent(){return this.childNodes.map(n=>n.textContent).join('');}
  set textContent(text){this.childNodes=[{nodeType:3,textContent:text}];}
  append(...nodes){for(const node of nodes)node.parent=this;this.childNodes.push(...nodes);}
  replaceChildren(...nodes){this.childNodes=[];this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=value;}
  getAttribute(key){return this.attributes[key];}
  querySelectorAll(selector){return this.children.flatMap(n=>[...(selector==='button'?n.tag==='button':selector==='[data-i18n]'?!!n.dataset.i18n:false)?[n]:[],...n.querySelectorAll(selector)]);}
  closest(){return this.dataset.panel?this:this.parent?.closest();}
}
async function fixture(initialState={registered:false}){
  const panel=new Node('section'),listeners={},calls=[],intervals=new Map(),timeouts=new Map();panel.dataset.panel='network';let state=initialState,handler=()=>state,timer=0;
  const document={documentElement:new Node('html'),hidden:false,createElement:tag=>new Node(tag),querySelector:selector=>{assert.equal(selector,'[data-panel="network"]');return panel;},querySelectorAll:()=>[],
    addEventListener:(name,fn)=>{listeners[name]=fn;},dispatchEvent:event=>listeners[event.type]?.(event)};
  const context=vm.createContext({document,localStorage:{getItem:()=> 'en',setItem(){}},CustomEvent:class{constructor(type){this.type=type;}},
    window:{bridgeDesktop:{sharedRelay:async value=>{calls.push(value);return handler(value);}}},setInterval(fn){intervals.set(++timer,fn);return timer;},clearInterval(id){intervals.delete(id);},setTimeout(fn){timeouts.set(++timer,fn);return timer;},clearTimeout(id){timeouts.delete(id);},confirm:()=>true});
  const run=code=>vm.runInContext(code,context);
  for(const file of ['web/shared/i18n.js','desktop/features/connections/shared-relay.js'])run(fs.readFileSync(path.join(__dirname,'..',file),'utf8'));
  await new Promise(setImmediate);
  const root=panel.children[0],all=()=>[root,...walk(root)];
  function walk(node){return node.children.flatMap(n=>[n,...walk(n)]);}
  return {root,panel,calls,all,run,intervals,timeouts,setState:value=>state=value,setHandler:value=>handler=value,language:value=>run(`BridgeI18n.setLanguage('${value}');BridgeI18n.apply();`)};
}
test('relay follows asynchronous desktop language and keeps typed registration fields',async()=>{
  const ui=await fixture();assert.equal(ui.root.children[0].textContent,'Self-hosted relay · Preview');
  const input=ui.all().find(n=>n.id==='relay-invitation');input.value='unsaved-invitation';
  ui.language('zh');assert.equal(ui.root.children[0].textContent,'自建中继 · 内测');
  assert.match(ui.root.textContent,/尚未注册自建中继/);assert.equal(input.value,'unsaved-invitation');
  ui.language('en');assert.equal(ui.root.children[0].textContent,'Self-hosted relay · Preview');assert.match(ui.root.textContent,/No self-hosted relay registered/);
  assert.deepEqual(ui.calls.map(v=>v.action),['status']);
});
test('the optional network entry starts unregistered with no registration, relay controls or polling enabled',async()=>{
  const ui=await fixture(),url=ui.all().find(n=>n.id==='relay-url'),consent=ui.all().find(n=>n.id==='relay-consent');
  assert.equal(ui.root.closest().dataset.panel,'network');assert.notEqual(ui.root.hidden,true);assert.equal(url.parent.parent.hidden,false);
  assert.equal(ui.all().find(n=>n.tag==='button'&&n.textContent==='Pair a phone').parent.hidden,true);
  assert.equal(url.value,'');assert.notEqual(consent.checked,true);assert.equal(ui.intervals.size,0);assert.equal(ui.timeouts.size,0);
  assert.match(ui.root.textContent,/Deploy a relay on your own server.*optional/);assert.match(ui.root.textContent,/relay can read conversations/);assert.doesNotMatch(ui.root.textContent,/[\u4e00-\u9fff]/);
  await ui.all().find(n=>n.tag==='button'&&n.textContent==='Register this computer').onclick();
  assert.deepEqual(ui.calls.map(v=>v.action),['status']);assert.match(ui.root.textContent,/Please confirm authorization/);
});
test('registration requires explicit trust and directs the user to the current Overview module',async()=>{
  const ui=await fixture();ui.all().find(n=>n.id==='relay-consent').checked=true;ui.all().find(n=>n.id==='relay-url').value='https://relay.example.com';ui.all().find(n=>n.id==='relay-invitation').value='fixture-invite';ui.all().find(n=>n.id==='relay-name').value='Fixture computer';
  ui.setHandler(value=>({registered:value.action==='register'||value.action==='status',enabled:true,online:false,deviceName:'Fixture computer'}));
  await ui.all().find(n=>n.tag==='button'&&n.textContent==='Register this computer').onclick();
  assert.deepEqual(ui.calls.map(v=>v.action),['status','register','status']);assert.equal(ui.calls[1].consent,true);assert.equal(ui.calls[1].url,'https://relay.example.com');
  assert.equal(ui.all().find(n=>n.id==='relay-invitation').value,'');assert.match(ui.root.textContent,/Restart the gateway in Overview/);assert.doesNotMatch(ui.root.textContent,/Connection & status/);
});
test('relay language changes relabel registered status and phone actions without another request',async()=>{
  const ui=await fixture();ui.setState({registered:true,enabled:true,online:true,deviceName:'Fixture computer',pending:[{id:'p',name:'Fixture phone'}],phones:[]});
  await ui.all().find(n=>n.tag==='button'&&n.textContent==='Refresh status').onclick();
  ui.language('zh');assert.match(ui.root.textContent,/Fixture computer · 在线/);assert.match(ui.root.textContent,/等待批准：Fixture phone/);
  assert.ok(ui.all().some(n=>n.tag==='button'&&n.textContent==='批准'));
  ui.language('en');assert.match(ui.root.textContent,/Fixture computer · Online/);assert.ok(ui.all().some(n=>n.tag==='button'&&n.textContent==='Approve'));
  assert.equal(ui.calls.length,2);
});
test('unregistered status clears pending phones, pairing codes and active timers before another registration',async()=>{
  for(const revoke of [false,true]){
    const registered={registered:true,enabled:true,online:true,deviceName:'Fixture computer',pending:[{id:'p',name:'Fixture phone'}],phones:[{id:'p2',name:'Approved phone'}]},ui=await fixture(registered);
    let state=registered;ui.setHandler(value=>value.action==='pair'?{image:'data:image/png;base64,fixture',url:'https://relay.example.com/#pair=fixture'}:value.action==='revoke'?(state={registered:false}):state);
    await ui.all().find(n=>n.tag==='button'&&n.textContent==='Pair a phone').onclick();
    const pair=ui.root.children.at(-2),phones=ui.root.children.at(-1);assert.equal(pair.children.length,3);assert.equal(phones.children.length,2);assert.equal(ui.intervals.size,1);assert.equal(ui.timeouts.size,1);
    if(!revoke)state={registered:false};
    await ui.all().find(n=>n.tag==='button'&&n.textContent===(revoke?'Revoke this computer':'Refresh status')).onclick();
    assert.equal(pair.children.length,0);assert.equal(phones.children.length,0);assert.equal(ui.intervals.size,0);assert.equal(ui.timeouts.size,0);assert.match(ui.root.textContent,/No self-hosted relay registered/);
    state=registered;await ui.all().find(n=>n.tag==='button'&&n.textContent==='Refresh status').onclick();assert.equal(phones.children.length,2,'identical phone records must render after a new registration');
  }
});
