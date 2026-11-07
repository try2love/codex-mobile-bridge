'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const script=fs.readFileSync(path.join(root,'mobile/shared/web/mobile-ui.js'),'utf8').replace(/\r\n/g,'\n');
function fixture(alias='My computer',mode=null,cold=false){
 const events={},clicks={},store={},name={textContent:''},computer={dataset:{deviceName:'Real Mac'},setAttribute(k,v){this[k]=v;},addEventListener(k,f){clicks[k]=f;}};
 const context={URL,location:{origin:'https://gateway.test',host:'gateway.test'},window:{prompt:()=>alias,addEventListener:()=>{}},MutationObserver:class{observe(){}},localStorage:{getItem:()=>mode,setItem:(k,v)=>store[k]=v},BridgeI18n:{t:s=>s},document:{styleSheets:[{href:'https://gateway.test/style.css',cssRules:[],insertRule(){}}],documentElement:{classList:{contains:()=>false,add(){},toggle(){}}},getElementById:id=>({'app':{hidden:false},'connected-computer':computer,'computer-name':name}[id]),querySelector:()=>null,addEventListener:(k,f)=>events[k]=f}};
 const markers=new Set(),rules=[];
 context.document.documentElement.classList={contains:key=>markers.has(key),add:key=>markers.add(key),toggle(){}};
 const sheet={href:'https://gateway.test/style.css',cssRules:rules,insertRule:rule=>rules.push(rule)};
 context.document.styleSheets=cold?[]:[sheet];
 context.document.removeEventListener=(name,fn)=>{if(events[name]===fn)delete events[name];};
 context.window.prompt=command=>{assert.ok(!command.startsWith('codexbridge-computer:'),'connection names are injected without a synchronous native request');return null;};
 vm.runInNewContext(script.replace("'__BRIDGE_CONNECTION_NAME__'",JSON.stringify(alias)),context);return {events,clicks,store,name,computer,markers,rules,context,sheet};
}
test('native header defaults to saved connection name and toggles real device name',()=>{
 const alias="办公室 'quoted' \\\n<Mac>";
 const f=fixture(alias);assert.equal(f.name.textContent,alias);assert.equal(f.computer.role,'button');
 f.clicks.click();assert.equal(f.name.textContent,'Real Mac');assert.equal(f.store['bridge-computer-name-mode'],'device');
 f.clicks.keydown({key:'Enter',preventDefault(){}});assert.equal(f.name.textContent,alias);
 f.computer.dataset.deviceName='Updated Mac';f.events['bridge-computer']();assert.equal(f.name.textContent,alias);
 f.clicks.click();assert.equal(f.name.textContent,'Updated Mac');
});
test('per-origin preference survives reload and renamed connection is read afresh',()=>{
 const f=fixture('Renamed', 'device');assert.equal(f.name.textContent,'Real Mac');f.clicks.click();assert.equal(f.name.textContent,'Renamed');
});
test('an empty connection name keeps the gateway device label',()=>{
 const f=fixture(null);assert.equal(f.computer.role,undefined);assert.equal(f.clicks.click,undefined);
});
test('Android and iOS inject shared mobile layout with the native connection token',()=>{
 const swift=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/App.swift'),'utf8').replace(/\r\n/g,'\n');
 const loader=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/WebScripts.swift'),'utf8');
 const android=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java'),'utf8');
 assert.match(loader,/appearance = try Self\.source\("mobile-ui", bundle: bundle\)/);
 assert.match(swift,/\(scripts\.route\+"\\n"\+scripts\.appearance\)\.replacingOccurrences\(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken\)/);
 assert.match(android,/MobileWebScripts\.appearance\(getAssets\(\)\)\.replace\("__BRIDGE_CLIPBOARD_TOKEN__",clipboardToken\)/);
 assert.match(android,/replace\("'__BRIDGE_CONNECTION_NAME__'",JSONObject\.quote\(computerName\(origin\)\)\)/);
 assert.match(swift,/replacingOccurrences\(of: "'__BRIDGE_CONNECTION_NAME__'", with: connectionName\)/);
 assert.match(android,/replace\("'__BRIDGE_NATIVE_LANGUAGE__'",JSONObject\.quote\(/);
 assert.match(swift,/replacingOccurrences\(of: "'__BRIDGE_NATIVE_LANGUAGE__'", with:/);
});

test('cold document-end registers native navigation before styles arrive, then installs menu rules exactly once',()=>{
 const f=fixture('Mac',null,true);
 assert.ok(f.markers.has('bridge-mobile'));
 assert.equal(f.rules.length,0);
 f.events.load(); // An earlier unrelated resource load must not swallow the stylesheet event.
 f.context.document.styleSheets=[f.sheet];f.events.load();
 assert.equal(f.events.load,undefined);
 assert.ok(f.rules.some(rule=>rule.includes('.list-more')&&rule.includes('display: none')));
 const count=f.rules.length;vm.runInNewContext(script,f.context);assert.equal(f.rules.length,count);
});
