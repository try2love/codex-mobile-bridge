'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../android/assets/mobile-session.js'),'utf8');
function fixture({authenticated=false,frame=false}={}){
 const events={},calls=[],window={prompt:(...args)=>calls.push(args)};window.top=frame?{}:window;
 vm.runInNewContext(source,{window,document:{addEventListener:(name,fn)=>events[name]=fn,getElementById:()=>({hidden:!authenticated})}});
 return {events,calls};
}
test('Android persists session immediately after login without exposing credentials',()=>{
 const f=fixture();assert.equal(f.calls.length,0);f.events['bridge-authenticated']();
 assert.deepEqual(f.calls,[['codexbridge-session:__BRIDGE_CLIPBOARD_TOKEN__','']]);
});
test('an already authenticated page persists its renewed session; embedded frames cannot',()=>{
 assert.equal(fixture({authenticated:true}).calls.length,1);const f=fixture({frame:true});assert.equal(f.calls.length,0);assert.equal(f.events['bridge-authenticated'],undefined);
});
