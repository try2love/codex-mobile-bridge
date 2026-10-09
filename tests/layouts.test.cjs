'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');

function layout({width=1440,coarse=false,containerWidth=1000,native=false}={}){
  const context=vm.createContext({
    matchMedia:query=>({matches:query==='(pointer:coarse)'?coarse:width<=720}),
    document:{documentElement:{classList:{contains:()=>native}}},
    navigator:{userAgent:native?'BridgeMobile/0.1-iOS':'Browser'},
  });
  for(const file of ['web/layouts/viewport.js','web/features/workspace/workbench.js'])vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context);
  return {api:vm.runInContext('BridgeLayout',context),split:()=>vm.runInContext('Workbench.prototype.canSplit',context).call({chat:{clientWidth:containerWidth}})};
}

test('a workbench splits by its own available width on browser and native hosts',()=>{
  for(const native of [false,true])for(const width of [390,844,1440]){
    assert.equal(layout({native,width,containerWidth:719}).split(),false);
    assert.equal(layout({native,width,containerWidth:720}).split(),true);
  }
});

test('viewport and touch queries preserve their separate existing boundaries',()=>{
  for(const width of [390,720,720.5,721,1440]){
    const {api}=layout({width});
    assert.equal(api.isCompactViewport(),width<=720);
    assert.equal(api.isCompactViewport(720),true);
    assert.equal(api.isCompactViewport(721),false);
    assert.equal(api.prefersTouchInput(),false);
  }
  assert.equal(layout({width:1440,coarse:true}).api.prefersTouchInput(),true);
  assert.equal(layout({width:390,coarse:false}).api.prefersTouchInput(),false);
});

test('public styles preserve base, viewport and host cascade positions',()=>{
  const assets=require('../web/assets.json');
  function before(url,first,last){
    const files=assets[url];assert.ok(Array.isArray(files),url);
    assert.ok(files.indexOf(first)>=0&&files.indexOf(last)>files.indexOf(first),url+': '+first+' before '+last);
  }
  before('/style.css','shell/style.css','layouts/compact/shell.css');
  before('/style.css','layouts/wide/shell.css','layouts/compact/shell.css');
  before('/desktop-sessions.css','layouts/wide/desktop-sessions.css','layouts/compact/desktop-sessions.css');
  before('/style.css','layouts/compact/shell.css','shell/content.css');
  before('/presentation.css','hosts/mobile-header.css','layouts/compact/presentation.css');
  before('/presentation.css','layouts/compact/presentation.css','hosts/mobile-footer.css');
  before('/client-navigation.css','layouts/compact/client-navigation.css','hosts/mobile-navigation-compact.css');
  before('/workbench.css','layouts/medium/workbench.css','features/workspace/workbench-input.css');
  for(const files of Object.values(assets))for(const file of Array.isArray(files)?files:[files])if(file.startsWith('layouts/')){
    const source=fs.readFileSync(path.join(root,'web',file),'utf8');
    assert.doesNotMatch(source,/bridge-mobile|BridgeHost|userAgent/,file+' must not depend on the host');
  }
});
