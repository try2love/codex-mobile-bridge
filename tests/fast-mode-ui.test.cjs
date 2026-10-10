'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function fixture(){
 const root={},input={},status={},ctx=vm.createContext({root,input,status,BridgeI18n:{t:s=>s}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/features/chat/fast-mode.js'),'utf8'),ctx);
 return {root,input,status,control:vm.runInContext('new FastModeControl({root,input,status})',ctx)};
}
const model={id:'official-model',fastTier:'priority',defaultServiceTier:'priority'},catalog={models:[model,{id:'local-model'}],fastMode:{allowed:true,defaultServiceTier:'priority'}},view={provider:'openai',connected:true};
test('official supported model reflects inherited Fast and explicit Standard/null',()=>{
 const ui=fixture();ui.control.open(catalog,view,model.id);assert.equal(ui.root.hidden,false);assert.equal(ui.input.checked,true);
 for(const tier of ['default',null]){ui.control.sync({...view,serviceTier:tier});assert.equal(ui.input.checked,false);}
 ui.control.sync({...view,serviceTier:'fast'});assert.equal(ui.input.checked,true);
});
test('API/custom/policy and unsupported model never expose a speed write',()=>{
 const ui=fixture();
 for(const [c,v,m] of [[catalog,{...view,provider:'custom'},model.id],[{...catalog,fastMode:{allowed:false}},view,model.id],[catalog,view,'local-model']]){
  ui.control.open(c,v,m);assert.equal(ui.root.hidden,true);assert.equal(Object.keys(ui.control.payload()).length,0);
 }
});
test('desktop changes sync until the user edits; busy and disconnected cannot toggle',()=>{
 const ui=fixture();ui.control.open(catalog,{...view,serviceTier:'default'},model.id);
 ui.control.sync({...view,serviceTier:'priority'});assert.equal(ui.input.checked,true);
 ui.input.checked=false;ui.input.onchange();ui.control.sync({...view,serviceTier:'priority'});
 assert.equal(ui.input.checked,false);assert.equal(ui.control.payload().fastMode,false);assert.match(ui.status.textContent,/下一轮/);
 ui.control.setBusy(true);assert.equal(ui.input.disabled,true);ui.control.setBusy(false);ui.control.sync({...view,connected:false});assert.equal(ui.input.disabled,true);
});
test('untouched Ultra stays untouched and switching model/chat clears a pending choice',()=>{
 const ui=fixture();ui.control.open(catalog,{...view,serviceTier:'ultrafast'},model.id);
 assert.equal(ui.input.indeterminate,true);assert.equal(Object.keys(ui.control.payload()).length,0);
 ui.input.checked=true;ui.input.onchange();assert.equal(ui.input.indeterminate,false);assert.equal(ui.control.payload().fastMode,true);
 ui.control.select('local-model');assert.equal(Object.keys(ui.control.payload()).length,0);
 ui.control.reset();assert.equal(ui.root.hidden,true);assert.equal(ui.control.dirty,false);
});
