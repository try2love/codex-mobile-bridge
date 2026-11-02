const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../shared/web/mobile-ui.js'),'utf8').split('// Only a proven route')[1];
const code=source.slice(source.indexOf('(() =>'));
function page(route, local={},temporary={}) {
 const storage=values=>Object.defineProperties({...values},{setItem:{value(k,v){this[k]=v;}},getItem:{value(k){return this[k]??null;}},removeItem:{value(k){delete this[k];}}});
 const localStorage=storage(local),sessionStorage=storage(temporary),window={},history={replaceState(a,b,url){history.url=url.href;}}, location={href:'https://gateway.test/',reload(){location.reloaded=true;}};
 vm.runInNewContext(code,{window,document:{querySelectorAll:()=>[]},localStorage,sessionStorage,location,history,URL,TextEncoder,browserNavigation:{read:()=>route}});
 return {handoff:window.BridgeMobileHandoff,localStorage,sessionStorage,location,history};
}
test('verified route handoff restores independent Codex and ZCode chat, draft and unresolved send without replay',()=>{
 for(const [provider,host,expect] of [['codex','codex:'+ '1'.repeat(32),'codex:'+ '1'.repeat(32)],['zcode','local','desktop:zcode']]){
  const route={id:'fixture-chat',host,provider},draft='composer-v1:draft:scope',pending='pending:local|fixture-chat';
  const before=page(route,{[draft]:'unsent','bridge-client':provider,'unrelated':'local-only'},{[pending]:'original-operation-id'});
  const data=JSON.parse(before.handoff.capture());const after=page(null);after.handoff.restore(data);
  assert.equal(decodeURIComponent(new URL(after.history.url).hash),'#fixture-chat~'+expect);
  assert.equal(after.localStorage[draft],'unsent');assert.equal(after.localStorage.unrelated,undefined);
  assert.equal(after.sessionStorage[pending],'original-operation-id');assert.equal(after.location.reloaded,true);
 }
});
