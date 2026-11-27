'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync(require.resolve('../shared/web/mobile-ui.js'),'utf8').split('// Private display storage')[1];
function page(store,local={}){
 const handlers=new Map(),events=[],storage=new Map(Object.entries(local));let context;
 const document={addEventListener(name,fn){const list=handlers.get(name)||[];list.push(fn);handlers.set(name,list);},dispatchEvent(event){events.push(event);for(const fn of handlers.get(event.type)||[])fn(event);}};
 const window={prompt(_,raw){const c=JSON.parse(raw);queueMicrotask(()=>{let value;if(c.op==='read')value=store.get(c.key)??null;if(c.op==='write'){store.set(c.key,c.value);value=true;}window.BridgeMobileStorage.receive('__BRIDGE_CLIPBOARD_TOKEN__',{id:c.id,ok:true,value});});return 'queued';}};window.top=window;window.addEventListener=document.addEventListener;window.dispatchEvent=document.dispatchEvent;
 context={window,document,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},Event:class{constructor(type){this.type=type;}},setTimeout,clearTimeout};vm.runInNewContext('/*'+code.slice(0,code.indexOf('\n'))+'*/'+code.slice(code.indexOf('\n')),context);return {window,document,events,storage};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('separate LAN and public documents reuse preferences under one native binding',async()=>{
 const store=new Map(),lan=page(store,{'bridge-appearance':JSON.stringify({fontSize:18})});await tick();
 lan.document.dispatchEvent({type:'bridge-appearance-change',detail:{fontSize:23,density:'compact'}});await tick();
 const remote=page(store,{'bridge-appearance':JSON.stringify({fontSize:12})});await tick();
 assert.equal(JSON.parse(remote.storage.get('bridge-appearance')).fontSize,23);
 assert.equal(remote.events.find(e=>e.type==='bridge-appearance-restored').detail.density,'compact');
 const other=page(new Map());await tick();assert.equal(other.events.some(e=>e.type==='bridge-appearance-restored'),false);
});
test('a delayed restore cannot overwrite a user edit',async()=>{
 const p=page(new Map([['appearance',{fontSize:12}]]));p.document.dispatchEvent({type:'bridge-appearance-change',detail:{fontSize:27}});await tick();
 assert.equal(p.events.some(e=>e.type==='bridge-appearance-restored'),false);
});

test('toolbar layout persists across a fresh transfer document and ignores a late restore after editing',async()=>{
 const store=new Map(),first=page(store);await tick();
 first.window.dispatchEvent({type:'bridge-composer-preferences-change',detail:{layout:{bottomLeft:['quota','model']}}});await tick();
 const transfer=page(store);await tick();
 assert.deepEqual(JSON.parse(transfer.storage.get('bridge-composer-preferences')).layout.bottomLeft,['quota','model']);
 const changed=page(store);changed.window.dispatchEvent({type:'bridge-composer-preferences-change',detail:{layout:{bottomLeft:['context']}}});await tick();
 assert.equal(changed.events.some(e=>e.type==='bridge-composer-preferences-restored'),false);
});
