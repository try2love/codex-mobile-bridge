'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element {
  constructor(){this.children=[];this.attributes={};this.listeners={};this.open=true;this.disabled=false;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(key,fn){(this.listeners[key]??=[]).push(fn);}
  close(){this.open=false;for(const fn of this.listeners.close||[])fn();}
}
const catalog={current:'ask',options:[{preset:'ask',available:true},{preset:'auto-review',available:false,reason:'Desktop setting required'},{preset:'full-access',available:true}]};
function fixture(){
  const dialog=new Element(),calls=[],saved=[],context=vm.createContext({document:{createElement:()=>new Element(),createElementNS:()=>new Element()},BridgeI18n:{t:s=>s},confirm:()=>true});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/permissions.js'),'utf8'),context);
  let current=true,load=async()=>catalog,save=async preset=>{calls.push(preset);return {confirmed:true};};
  return {dialog,calls,saved,open:()=>context.permissionPicker(dialog,{load:()=>load(),save:p=>save(p),isCurrent:()=>current,onSaved:r=>saved.push(r)}),
    buttons:()=>dialog.children[0].children,status:()=>dialog.children[1],refresh:()=>dialog.children[2],
    setCurrent:v=>current=v,setLoad:v=>load=v,setSave:v=>save=v};
}
test('unavailable options explain why and stay disabled after a failed or unconfirmed save',async()=>{
  const ui=fixture();await ui.open();const [ask,auto]=ui.buttons();
  assert.equal(ask.attributes['aria-pressed'],'true');assert.equal(auto.disabled,true);
  assert.equal(auto.children[1].children[1].textContent,'Desktop setting required');
  await auto.onclick();assert.equal(ui.calls.length,0);
  ui.setSave(async()=>{throw Error('Rejected');});await ask.onclick();
  assert.equal(ui.status().textContent,'Rejected');assert.equal(auto.disabled,true);assert.equal(ask.disabled,false);
  ui.setSave(async()=>({confirmed:false}));await ask.onclick();
  assert.equal(ui.saved.length,0);assert.equal(ui.dialog.open,true);assert.equal(auto.disabled,true);
});
test('a fresh catalog may enable a previously hidden option',async()=>{
  const ui=fixture();await ui.open();ui.setLoad(async()=>({current:'auto-review',options:[{preset:'auto-review',available:true}]}));
  await ui.refresh().onclick();assert.equal(ui.buttons().length,1);assert.equal(ui.buttons()[0].disabled,false);
  await ui.buttons()[0].onclick();assert.deepEqual(ui.calls,['auto-review']);assert.equal(ui.saved.length,1);assert.equal(ui.dialog.open,false);
});
test('stale host or child and closed dialogs ignore late capability results and writes',async()=>{
  for(const invalidate of [ui=>ui.setCurrent(false),ui=>ui.dialog.close()]){
    const ui=fixture();let resolve;ui.setLoad(()=>new Promise(done=>resolve=done));const pending=ui.open();
    invalidate(ui);resolve(catalog);await pending;assert.equal(ui.buttons().length,0);
  }
  const ui=fixture();await ui.open();ui.setCurrent(false);await ui.buttons()[0].onclick();assert.equal(ui.calls.length,0);
});
test('late save responses cannot apply state to a replacement side chat',async()=>{
  const ui=fixture();await ui.open();let resolve;ui.setSave(()=>new Promise(done=>resolve=done));
  const pending=ui.buttons()[0].onclick();ui.setCurrent(false);resolve({confirmed:true});await pending;assert.equal(ui.saved.length,0);
});
test('failed refresh clears stale options and duplicate clicks do not submit twice',async()=>{
  const ui=fixture();await ui.open();let resolve,count=0;ui.setSave(()=>{count++;return new Promise(done=>resolve=done);});
  const button=ui.buttons()[0],pending=button.onclick();await button.onclick();assert.equal(count,1);resolve({confirmed:false});await pending;
  ui.setLoad(async()=>{throw Error('Offline');});await ui.refresh().onclick();assert.equal(ui.buttons().length,0);assert.equal(ui.status().textContent,'Offline');
});
