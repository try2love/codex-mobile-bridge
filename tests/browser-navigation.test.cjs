'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({native=false,state=null}={}){
  const entries=[{state,url:'/'}],listeners={};let index=0,route=null,restores=0;
  if(state)entries[0].state={...state,scope:'fixture'};
  const location={pathname:'/',search:'',hash:''};
  const update=url=>{const value=new URL(url,'https://fixture.test/');location.pathname=value.pathname;location.search=value.search;location.hash=value.hash;};
  const history={get state(){return entries[index].state;},replaceState(state,unused,url){entries[index]={state,url};update(url);},pushState(state,unused,url){entries.splice(++index);entries[index]={state,url};update(url);},back(){if(index){index--;update(entries[index].url);queueMicrotask(()=>listeners.popstate({state:history.state}));}},forward(){if(index+1<entries.length){index++;update(entries[index].url);queueMicrotask(()=>listeners.popstate({state:history.state}));}}};
  const context=vm.createContext({sessionStorage:{getItem:()=> 'fixture',setItem(){}},BridgeHost:{isNativeApp:()=>native,hasNativeLayout:()=>false},history,location,window:{addEventListener:(name,fn)=>listeners[name]=fn}});
  vm.runInContext(fs.readFileSync('web/hosts/browser-navigation.js','utf8'),context);
  const navigation=vm.runInContext('new BrowserNavigation({read:()=>null,restore:()=>{}})',context);
  navigation.read=()=>route;navigation.restore=async value=>{route=value;restores++;navigation.sync(value);};
  return {navigation,history,entries,location,setRoute:value=>route=value,get route(){return route;},get restores(){return restores;}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
for(const [provider,host] of [['codex','local'],['codex','ssh:fixture'],['codex','wsl:Ubuntu'],['claude','local'],['deepseek','local']])test(`browser back/forward preserves ${provider}/${host} identity with a finite list predecessor`,async()=>{
  const f=fixture(),route={provider,id:'fixture',host};f.navigation.sync(route);assert.equal(f.entries.length,2);f.history.back();await tick();assert.equal(f.route,null);f.history.forward();await tick();assert.deepEqual(JSON.parse(JSON.stringify(f.route)),route);assert.equal(f.entries.length,2);f.navigation.sync(null);await tick();assert.equal(f.route,null);assert.equal(f.entries.length,2);
});
test('native host does not change history',()=>{const f=fixture({native:true});f.navigation.sync({provider:'codex',id:'fixture',host:'local'});assert.equal(f.entries.length,1);});
test('refresh reuses its detail entry',()=>{const f=fixture({state:{bridgeNavigation:1,route:{provider:'codex',id:'fixture',host:'local'}}});f.navigation.sync({provider:'codex',id:'fixture',host:'local'});assert.equal(f.entries.length,1);});

test('a slower Forward restoration cannot reopen a chat after a second Back',async()=>{
  const f=fixture(),route={provider:'codex',id:'slow',host:'local'};let release;
  f.navigation.sync(route);f.history.back();await tick();
  f.navigation.restore=async(value,current)=>{if(value)await new Promise(resolve=>release=resolve);if(current())f.setRoute(value);};
  f.history.forward();await tick();f.history.back();await tick();release();await tick();assert.equal(f.route,null);assert.equal(f.navigation.restoring,false);
});
test('authentication/account invalidation rejects old Forward entries',async()=>{
  const f=fixture();f.navigation.sync({provider:'claude',id:'old-account',host:'local'});f.history.back();await tick();
  f.navigation.invalidate();f.history.forward();await tick();assert.equal(f.route,null);assert.equal(f.history.state.route,null);assert.equal(f.restores,1);
});
test('a restoration failure repairs history to the visible view',async()=>{
  const f=fixture();f.navigation.sync({provider:'codex',id:'failed',host:'ssh:fixture'});f.history.back();await tick();
  let reported=false;f.navigation.restore=async()=>{throw Error('fixture failure');};f.navigation.onError=()=>reported=true;
  f.history.forward();await tick();assert.equal(reported,true);assert.equal(f.history.state.route,null);assert.equal(f.location.hash,'');assert.equal(f.navigation.restoring,false);
});
test('bootstrap pause preserves a reload route until client discovery finishes',()=>{
  const route={provider:'deepseek',id:'refresh',host:'local'},f=fixture({state:{bridgeNavigation:1,route}});
  f.navigation.paused=true;f.navigation.sync(null);assert.deepEqual(f.history.state.route,route);f.navigation.paused=false;f.navigation.sync(route);assert.equal(f.entries.length,1);
});

test('opening a new chat before the header Back popstate does not close it',async()=>{
  const f=fixture(),first={provider:'codex',id:'first',host:'local'},next={provider:'codex',id:'next',host:'local'};
  f.navigation.sync(first);f.navigation.sync(null);f.setRoute(next);f.navigation.sync(next);await tick();assert.deepEqual(f.route,next);assert.equal(f.history.state.route.id,'next');assert.equal(f.entries.length,2);
});

test('production history restore respects disabled client and WSL entry gates',async()=>{
  const source=fs.readFileSync('web/shell/app.js','utf8'),start=source.indexOf('  restore:async('),end=source.indexOf('  },onError:',start);
  const restoreSource='('+source.slice(start,end+3).trim().replace(/^restore:/,'')+')';
  for(const [provider,host,entry] of [['codex','local','codex'],['codex','fixture-ssh','codex'],['codex','wsl:'+'a'.repeat(32),'wsl:'+'a'.repeat(32)],['claude','local','claude'],['deepseek','local','deepseek']]){
    for(const enabled of [false,true]){
      let opened=0,chosen=0,repaired=false,notified=false;
      const context=vm.createContext({$:()=>({hidden:false}),document:{querySelectorAll:()=>[]},api:async()=>({authenticated:true}),listIdentity:'',listAuthIdentity:()=>'',history:{replaceState:()=>repaired=true},toast:()=>notified=true,
        browserNavigation:{entry:route=>({route}),read:()=>null,url:()=> '/',invalidate(){}},
        clientNavigation:{managementClients:()=>[{id:entry,enabled},...(entry.startsWith('wsl:')?[{id:'codex',enabled:true}]:[])],choose:async()=>chosen++},
        openChat:async()=>opened++,desktopSessions:{open:async()=>opened++}});
      require('./fixtures/codex-entry-predicates.cjs')(context);
      await vm.runInContext(restoreSource,context)({provider,id:'fixture',host});
      assert.equal(opened,enabled?1:0,provider+'/'+host);assert.equal(chosen,enabled?1:0);assert.equal(repaired,!enabled);assert.equal(notified,!enabled);
    }
  }
});
