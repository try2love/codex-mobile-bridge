'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function fixture(){
 const root={},input={},ctx=vm.createContext({root,input});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/chat/fast-mode.js'),'utf8'),ctx);
 return {root,input,control:vm.runInContext('new FastModeControl({root,input})',ctx)};
}
const model={id:'official-model',fastTier:'priority',defaultServiceTier:'priority'},catalog={models:[model,{id:'local-model'}],fastMode:{allowed:true,defaultServiceTier:'priority'}},view={provider:'openai',connected:true};
test('official supported model reflects inherited Fast and explicit Standard/null',()=>{
 const ui=fixture();ui.control.open(catalog,view,model.id);assert.equal(ui.root.hidden,false);assert.equal(ui.input.checked,true);
 for(const tier of ['default',null]){ui.control.sync({...view,serviceTier:tier});assert.equal(ui.input.checked,false);}
 ui.control.sync({...view,serviceTier:'fast'});assert.equal(ui.input.checked,true);
});
test('unscoped custom access, policy restrictions and unsupported models keep speed writes unavailable',()=>{
 const ui=fixture();
 for(const [c,v,m] of [[catalog,{...view,provider:'custom'},model.id],[{...catalog,fastMode:{allowed:false}},view,model.id],[catalog,view,'local-model']]){
  ui.control.open(c,v,m);assert.equal(ui.root.hidden,true);assert.equal(Object.keys(ui.control.payload()).length,0);
 }
});

test('provider-scoped route models expose Fast and retain pending settings only for that provider',()=>{
 const ui=fixture(),route={...model,id:'route/official-model'},scoped={...catalog,models:[route],fastMode:{allowed:true,provider:'custom',defaultServiceTier:'default'}};
 ui.control.open(scoped,{provider:'custom',connected:true,serviceTier:'default'},route.id);
 assert.equal(ui.root.hidden,false);assert.equal(ui.input.checked,false);
 ui.input.checked=true;ui.input.onchange();assert.equal(ui.control.payload().fastMode,true);
 ui.control.sync({provider:'other',connected:true});assert.equal(ui.root.hidden,true);assert.equal(Object.keys(ui.control.payload()).length,0);
 ui.control.open({...scoped,fastMode:{...scoped.fastMode,allowed:false}},{provider:'custom',connected:true},route.id);assert.equal(ui.root.hidden,true);
});
test('desktop changes sync until the user edits; busy and disconnected cannot toggle',()=>{
 const ui=fixture();ui.control.open(catalog,{...view,serviceTier:'default'},model.id);
 ui.control.sync({...view,serviceTier:'priority'});assert.equal(ui.input.checked,true);
 ui.input.checked=false;ui.input.onchange();ui.control.sync({...view,serviceTier:'priority'});
 assert.equal(ui.input.checked,false);assert.equal(ui.control.payload().fastMode,false);
 ui.control.setBusy(true);assert.equal(ui.input.disabled,true);ui.control.setBusy(false);ui.control.sync({...view,connected:false});assert.equal(ui.input.disabled,true);
});
test('untouched Ultra stays untouched and switching model/chat clears a pending choice',()=>{
 const ui=fixture();ui.control.open(catalog,{...view,serviceTier:'ultrafast'},model.id);
 assert.equal(ui.input.indeterminate,true);assert.equal(Object.keys(ui.control.payload()).length,0);
 ui.input.checked=true;ui.input.onchange();assert.equal(ui.input.indeterminate,false);assert.equal(ui.control.payload().fastMode,true);
 ui.control.select('local-model');assert.equal(Object.keys(ui.control.payload()).length,0);
 ui.control.reset();assert.equal(ui.root.hidden,true);assert.equal(ui.control.dirty,false);
});
