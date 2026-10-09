'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const script=fs.readFileSync(path.join(root,'mobile/shared/web/mobile-ui.js'),'utf8').replace(/\r\n/g,'\n');
function fixture(alias='My computer',mode=null){
 const events={},clicks={},store={},name={textContent:''},computer={dataset:{deviceName:'Real Mac'},setAttribute(k,v){this[k]=v;},addEventListener(k,f){clicks[k]=f;}};
 const context={URL,location:{origin:'https://gateway.test',host:'gateway.test'},window:{prompt:()=>alias},MutationObserver:class{observe(){}},localStorage:{getItem:()=>mode,setItem:(k,v)=>store[k]=v},BridgeI18n:{t:s=>s},document:{styleSheets:[{href:'https://gateway.test/style.css',cssRules:[],insertRule(){}}],documentElement:{classList:{contains:()=>false,add(){},toggle(){}}},getElementById:id=>({'app':{hidden:false},'connected-computer':computer,'computer-name':name}[id]),querySelector:()=>null,addEventListener:(k,f)=>events[k]=f}};
 vm.runInNewContext(script,context);return {events,clicks,store,name,computer};
}
test('native header defaults to saved connection name and toggles real device name',()=>{
 const f=fixture('办公室 <Mac>');assert.equal(f.name.textContent,'办公室 <Mac>');assert.equal(f.computer.role,'button');
 f.clicks.click();assert.equal(f.name.textContent,'Real Mac');assert.equal(f.store['bridge-computer-name-mode'],'device');
 f.clicks.keydown({key:'Enter',preventDefault(){}});assert.equal(f.name.textContent,'办公室 <Mac>');
 f.computer.dataset.deviceName='Updated Mac';f.events['bridge-computer']();assert.equal(f.name.textContent,'办公室 <Mac>');
 f.clicks.click();assert.equal(f.name.textContent,'Updated Mac');
});
test('per-origin preference survives reload and renamed connection is read afresh',()=>{
 const f=fixture('Renamed', 'device');assert.equal(f.name.textContent,'Real Mac');f.clicks.click();assert.equal(f.name.textContent,'Renamed');
});
test('older native clients without name support keep the gateway behavior',()=>{
 const f=fixture(null);assert.equal(f.computer.role,undefined);assert.equal(f.clicks.click,undefined);
});
test('Android and iOS inject shared mobile layout with the native connection token',()=>{
 const swift=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/App.swift'),'utf8').replace(/\r\n/g,'\n');
 const loader=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/WebScripts.swift'),'utf8');
 const android=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java'),'utf8');
 assert.match(loader,/appearance = try Self\.source\("mobile-ui", bundle: bundle\)/);
 assert.match(swift,/codexbridge-computer:/);assert.match(swift,/scripts\.appearance\.replacingOccurrences\(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken\)/);
 assert.match(android,/MobileWebScripts\.appearance\(getAssets\(\)\)\.replace\("__BRIDGE_CLIPBOARD_TOKEN__",clipboardToken\)/);
});
