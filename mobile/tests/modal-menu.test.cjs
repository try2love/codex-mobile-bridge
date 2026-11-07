'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const source=fs.readFileSync(path.join(root,'mobile/shared/web/mobile-ui.js'),'utf8');

function fixture(initialDialogs=[],frame=false){
  const prompts=[],observers=[],windowEvents={},documentEvents={};
  const dialogs=[...initialDialogs];
  const app={hidden:false};
  const document={
    hidden:false,
    styleSheets:[{href:'https://gateway.test/style.css',cssRules:[],insertRule(){}}],
    documentElement:{classList:{contains:()=>false,add(){},toggle(){}}},
    getElementById:id=>id==='app'?app:null,
    querySelector:selector=>selector==='dialog[open]'?dialogs.find(dialog=>dialog.open)||null:null,
    addEventListener:(name,listener)=>{documentEvents[name]=listener;}
  };
  const window={
    prompt:(name,value)=>{prompts.push({name,value});return 'saved';},
    addEventListener:(name,listener)=>{windowEvents[name]=listener;}
  };
  window.top=frame?{}:window;
  class MutationObserver{
    constructor(callback){this.callback=callback;observers.push(this);}
    observe(target,options){this.target=target;this.options=options;}
  }
  vm.runInNewContext(source,{URL,location:{origin:'https://gateway.test',host:'gateway.test',href:'https://gateway.test/'},document,window,MutationObserver,
    BridgeDownload:require('../../web/features/workspace/downloads.js')});
  const modalObserver=observers.find(observer=>observer.target===document.documentElement);
  assert.ok(modalObserver,'shared script observes the document for modal changes');
  const changed=()=>modalObserver.callback();
  return {prompts,dialogs,document,window,windowEvents,documentEvents,changed};
}

test('native menu follows any open dialog through long scrolling, nesting, and close',()=>{
  const f=fixture();
  assert.deepEqual(f.prompts.map(call=>call.value),['closed']);
  assert.equal(f.prompts[0].name,'codexbridge-modal:__BRIDGE_CLIPBOARD_TOKEN__');
  const webhook={open:true},other={open:true};
  f.dialogs.push(webhook);f.changed();
  assert.deepEqual(f.prompts.map(call=>call.value),['closed','open']);
  for(let index=0;index<100;index++)f.changed();
  assert.equal(f.prompts.length,2,'scroll and subtree changes do not reveal the menu');
  f.dialogs.push(other);f.changed();webhook.open=false;f.changed();
  assert.equal(f.prompts.length,2,'menu stays hidden while another modal remains');
  other.open=false;f.changed();
  assert.deepEqual(f.prompts.map(call=>call.value),['closed','open','closed']);
  const late={open:true};f.dialogs.push(late);f.changed();
  assert.equal(f.prompts.at(-1).value,'open','new dialogs are covered without a dialog ID list');
  late.open=false;f.changed();
  assert.equal(f.prompts.at(-1).value,'closed');
});

test('foreground return resends the current modal state to both native shells',()=>{
  const f=fixture(),modal={open:true};f.dialogs.push(modal);f.changed();
  f.windowEvents.focus();
  assert.equal(f.prompts.at(-1).value,'open');
  f.document.hidden=true;f.documentEvents.visibilitychange();
  const before=f.prompts.length;
  f.document.hidden=false;f.documentEvents.visibilitychange();
  assert.equal(f.prompts.length,before+1);
  modal.open=false;f.changed();assert.equal(f.prompts.at(-1).value,'closed');
});
test('an already open modal is reported on initial injection',()=>{
  const f=fixture([{open:true}]);assert.deepEqual(f.prompts.map(call=>call.value),['open']);
});

test('cloud download clicks use the native transfer for every client, preserving direct navigation',()=>{
  const f=fixture();
  const paths=['/api/sessions/01a1867c-7fa0-7802-9bd0-c7635935e4a5/workspace/download?path=note.txt',
    ...['claude','deepseek','zcode'].map(p=>'/api/desktop-sessions/'+p+'/workspace/download?path=note.txt')];
  const click=(href,extra={})=>{
    const event={isTrusted:true,button:0,target:{closest:()=>({href,hasAttribute:()=>false})},
      preventDefault(){this.defaultPrevented=true;},...extra};
    f.documentEvents.click(event);return event;
  };
  f.window.BridgeMobileCapabilities={fileDownload:true};
  assert.ok(!click('https://gateway.test'+paths[0]).defaultPrevented,'direct WebView keeps its download path');
  f.window.BridgeCloudWorkbench=true;
  for(const path of paths)assert.ok(click('https://gateway.test'+path).defaultPrevented);
  assert.deepEqual(f.prompts.filter(v=>v.name.startsWith('codexbridge-download:')).map(v=>v.value),paths.map(p=>'https://gateway.test'+p));
  const before=f.prompts.length;
  for(const extra of [{isTrusted:false},{defaultPrevented:true},{button:1},{metaKey:true},{ctrlKey:true},{shiftKey:true},{altKey:true}])click('https://gateway.test'+paths[0],extra);
  click('https://elsewhere.test'+paths[0]);click('https://gateway.test/api/accounts');
  click('https://gateway.test'+paths[0],{target:{closest:()=>({href:'https://gateway.test'+paths[0],hasAttribute:()=>true})}});
  f.window.BridgeMobileCapabilities.fileDownload=false;click('https://gateway.test'+paths[0]);
  assert.equal(f.prompts.length,before,'preview, untrusted and unsupported actions do not reach the native downloader');
  assert.equal(fixture([],true).documentEvents.click,undefined);
});

test('both mobile apps forward only supported theme preferences from the main frame',()=>{
  const f=fixture();
  for(const theme of ['dark','light','system'])f.documentEvents['bridge-theme-change']({detail:{theme,private:'ignored'}});
  const calls=f.prompts.filter(call=>call.name.startsWith('codexbridge-theme:'));
  assert.deepEqual(calls.map(call=>call.value),['dark','light','system']);
  for(const theme of ['',null,'auto','dark\n'])f.documentEvents['bridge-theme-change']({detail:{theme}});
  assert.equal(f.prompts.filter(call=>call.name.startsWith('codexbridge-theme:')).length,3);
  assert.equal(fixture([],true).documentEvents['bridge-theme-change'],undefined);
});

test('both native shells accept only a validated current-page modal signal',()=>{
  const android=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java'),'utf8');
  const ios=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/App.swift'),'utf8');
  const a=android.slice(android.indexOf('if(prompt.equals("codexbridge-modal:"'),android.indexOf('if(prompt.equals("codexbridge-session:"'));
  const i=ios.slice(ios.indexOf('if prompt == "codexbridge-modal:"'),ios.indexOf('if prompt == "codexbridge-logout:"'));
  assert.match(a,/view==web&&view\.hasWindowFocus\(\)&&GatewayURL\.sameOrigin\(url,pageOrigin\)&&GatewayURL\.sameOrigin\(view\.getUrl\(\),pageOrigin\)/);
  assert.match(a,/"open"\.equals\(value\)\|\|"closed"\.equals\(value\)/);
  assert.match(a,/menu\.setVisibility\(open\?View\.GONE:View\.VISIBLE\);menu\.setEnabled\(!open\)/);
  assert.match(i,/webView === web, connectionError == nil, UIApplication\.shared\.applicationState == \.active/);
  assert.match(i,/frame\.isMainFrame.*GatewayURL\.same\(source, documentAddress\)/s);
  assert.match(i,/gatewayMenu\.isHidden = state == "open"; gatewayMenu\.isEnabled = state == "closed"/);
  assert.match(ios,/gatewayMenu\.isHidden = home\s+gatewayMenu\.isEnabled = true/);
});
