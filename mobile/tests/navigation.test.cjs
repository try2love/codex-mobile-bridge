'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const android=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java'),'utf8');
const scripts=[...android.matchAll(/evaluateJavascript\("((?:\\.|[^"\\])*)"/g)].map(match=>JSON.parse('"'+match[1]+'"'));
const back=scripts.find(value=>value.includes("const dialog=document.querySelector('dialog[open]')"));
function fixture({navigation,dialog,chatOpen=false,toolOpen=false}={}){
  const calls=[],window={BridgeWorkbench:{back(){calls.push('legacy-tool');return toolOpen;}}};
  if(navigation)window.BridgeNavigation=navigation(calls);
  const document={querySelector:()=>dialog?{close(){calls.push('dialog');}}:null,getElementById:id=>id==='app'?{classList:{contains:()=>chatOpen}}:{click(){calls.push(id);}}};
  return {calls,run:script=>vm.runInNewContext(script,{window,document})};
}
test('native Android back consumes current provider panels and chat before leaving the computer',()=>{
  for(const provider of ['codex','claude','deepseek']){
    let depth=2;
    const f=fixture({navigation:calls=>({back(){calls.push(provider);return depth-->0;}})});
    assert.equal(f.run(back),true);assert.equal(f.run(back),true);assert.equal(f.run(back),false);
    assert.deepEqual(f.calls,[provider,provider,provider]);
  }
});
test('a dialog closes before provider navigation and old gateways retain preview.3 back behavior',()=>{
  const modal=fixture({dialog:true,navigation:()=>({back(){throw Error('must close dialog first');}})});
  assert.equal(modal.run(back),true);assert.deepEqual(modal.calls,['dialog']);
  const tool=fixture({toolOpen:true});assert.equal(tool.run(back),true);assert.deepEqual(tool.calls,['legacy-tool']);
  const chat=fixture({chatOpen:true});assert.equal(chat.run(back),true);assert.deepEqual(chat.calls,['legacy-tool','back']);
  assert.equal(fixture().run(back),false);
});
test('native account menu routes by current provider and falls back only for old gateways',()=>{
  const script=scripts.find(value=>value.includes('BridgeNavigation?.accounts'));
  assert.ok(script,'native account action must use provider navigation');
  for(const provider of ['codex','claude','deepseek']){
    const f=fixture({navigation:calls=>({accounts(){calls.push(provider);return true;}})});
    assert.equal(f.run(script),true);assert.deepEqual(f.calls,[provider]);
  }
  const refused=fixture({navigation:()=>({accounts(){return false;}})});
  assert.equal(refused.run(script),false);assert.deepEqual(refused.calls,[]);
  const legacy=fixture();assert.equal(legacy.run(script),true);assert.deepEqual(legacy.calls,['accounts-button']);
  const swift=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/App.swift'),'utf8');
  const account=swift.match(/evaluateJavaScript\("((?:\\.|[^"\\])*)"\)/g)?.find(value=>value.includes('BridgeNavigation?.accounts'));
  assert.ok(account,'iOS account menu must use the same provider navigation');
  assert.equal(JSON.parse(account.slice('evaluateJavaScript('.length,-1)),script);
});

test('native app-bar menu reads the shared toggle without creating one for old or logged-out gateways',()=>{
 const script=scripts.find(value=>value.includes("button.getAttribute('aria-expanded')"));assert.ok(script);
 const swift=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/App.swift'),'utf8');
 const match=[...swift.matchAll(/evaluateJavaScript\("((?:\\.|[^"\\])*)"\)/g)].find(value=>value[1].includes("button.getAttribute('aria-expanded')"));
 assert.ok(match);assert.equal(JSON.parse('"'+match[1]+'"'),script);
 for(const [app,button,expected] of [[{hidden:false},{hidden:false,getAttribute:()=> 'true'},false],[{hidden:false},{hidden:false,getAttribute:()=> 'false'},true],[{hidden:false},{hidden:true},null],[{hidden:true},{hidden:false},null],[{hidden:false},null,null],[null,null,null]]){
  assert.equal(vm.runInNewContext(script,{document:{getElementById:id=>id==='app'?app:button}}),expected);
 }
});
