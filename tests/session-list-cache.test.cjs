'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../web/shell/app.js'),'utf8');
const functions=source.slice(source.indexOf('function listSnapshotKey('),source.indexOf("$('list-mode').value="))+
  source.slice(source.indexOf('function renderHostErrors('),source.indexOf('function clearListSnapshot('))+
  source.slice(source.indexOf('async function loadList('),source.indexOf('function loadMoreSessions('));
const tick=()=>new Promise(setImmediate);
const row=(id,host='local')=>({id,host,title:id,recency:10});
const reply=(sessions,remoteHosts=[])=>({sessions,remoteHosts,availableSources:['desktop'],complete:true});
function storage(){const data=new Map();return {get length(){return data.size;},key:i=>[...data.keys()][i],getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key),data};}
function fixture(read,cache=storage()){
  const element=(tag,cls,textContent)=>({tag,className:cls,textContent,children:[],get childElementCount(){return this.children.length;},setAttribute(){},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;}});
  const nodes={search:{value:''},archived:{checked:false},'list-mode':{value:'recent'},sessions:{scrollTop:0},'sessions-more':{disabled:false},'host-errors':element('div')};
  const context=vm.createContext({localStorage:cache,sessionStorage:storage(),$:(id)=>nodes[id],
    listIdentity:'a'.repeat(64),selectedCodexEntry:null,listSource:'all',listLoading:0,listGeneration:0,listOffset:0,listHasMore:true,listRows:[],availableSources:[],listHostIds:[],listFilterKey:'',
    listQuery:'',listArchived:false,listComplete:false,listReadFinished:false,
    sessionPins:{capture:()=>({}),observe(){},pending:()=>false},pinRow:value=>value,chatKey:(id,host)=>host+'|'+id,recentInteractions:new Map(),
    renderList(){},renderSourceFilter:()=>true,refreshActivity(){},el:element,dismissedHostFaults:new Map(),location:{origin:'http://fixture'},
    clientNavigation:{acknowledgeList:()=>context.acknowledged++},listSync:{acknowledgeSuccess(){},update:()=>context.loadList(true)},acknowledged:0,
    t:value=>value,toast:message=>context.toasts.push(message),toasts:[],window:{},
    api:url=>read(new URL(url,'http://fixture').searchParams.get('host')),
  });
  vm.runInContext(functions,context);
  const render=context.renderHostErrors;context.renderHostErrors=errors=>{context.errors=errors;render(errors);};
  const refresh=()=>context.loadList(true).catch(error=>{context.toast(error.message);});
  const saved=()=>{const value=cache.getItem(context.listSnapshotKey(context.currentListFilter()));return value?JSON.parse(value):null;};
  return {context,cache,refresh,saved,nodes};
}

test('a successful local page is cached before a slow remote host finishes',async()=>{
  let finish;
  const ui=fixture(host=>host==='local'?Promise.resolve(reply([row('local-chat')],['remote'])):new Promise(resolve=>finish=resolve));
  const pending=ui.refresh();await tick();
  try{assert.equal(ui.context.listRows[0].id,'local-chat');assert.equal(ui.saved()?.rows[0]?.id,'local-chat');}
  finally{finish(reply([]));await pending;}
});

test('partial success survives reload and preserves the failed host cache',async()=>{
  const ui=fixture(async host=>host==='local'?reply([row('fresh-local')],['wsl:offline']):Promise.reject(Error('WSL unavailable')));
  ui.context.listRows=[row('old-local'),row('saved-wsl','wsl:offline')];
  ui.context.listFilterKey=ui.context.currentListFilter();
  await ui.refresh();
  const reopened=fixture(async()=>{throw Error('offline');},ui.cache);
  assert.equal(reopened.context.restoreListSnapshot(),true);
  assert.deepEqual(Array.from(reopened.context.listRows,value=>value.id).sort(),['fresh-local','saved-wsl']);
  assert.equal(ui.context.listComplete,false);assert.equal(ui.context.acknowledged,0);
});

test('failure without any fresh page does not overwrite a previous snapshot',async()=>{
  const ui=fixture(async()=>reply([row('cached')]));await ui.refresh();
  const before=JSON.stringify(ui.saved());
  ui.context.api=async()=>{throw Error('offline');};await ui.refresh();
  assert.equal(JSON.stringify(ui.saved()),before);
});

test('a late host result cannot replace a different list generation cache',async()=>{
  let finish;
  const ui=fixture(()=>new Promise(resolve=>finish=resolve));
  const pending=ui.refresh();await tick();ui.context.listGeneration++;
  finish(reply([row('stale')]));await pending;assert.equal(ui.saved(),null);
});

test('repeated host failures stay inline without rejecting navigation or claiming a full refresh',async()=>{
  const ui=fixture(async host=>host==='local'?reply([row('local')],['wsl:offline']):Promise.reject(Error('WSL unavailable')));
  for(let visit=0;visit<3;visit++){
    assert.equal(await ui.refresh(),false);
    assert.equal(ui.context.errors[0].host,'wsl:offline');
    assert.equal(ui.context.errors[0].error,'WSL unavailable');
    assert.equal(ui.context.listRows[0].id,'local');
  }
  assert.equal(ui.context.toasts.length,0);assert.equal(ui.context.acknowledged,0);
});

test('an entirely failed refresh keeps its visible host error and remains retryable',async()=>{
  const ui=fixture(async()=>{throw Error('local unavailable');});
  assert.equal(await ui.refresh(),false);
  assert.equal(ui.context.errors[0].error,'local unavailable');
  assert.equal(ui.context.listLoading,0);assert.equal(ui.context.toasts.length,0);
  assert.equal(ui.nodes['host-errors'].hidden,false);
  const retry=ui.nodes['host-errors'].children[0].children.find(node=>node.tag==='button'&&node.textContent==='重试');
  assert.ok(retry,'the visible host error must offer a retry button');
  ui.context.api=async()=>reply([row('recovered')]);
  assert.equal(await retry.onclick(),true);assert.equal(ui.context.errors.length,0);
  assert.equal(ui.context.acknowledged,1);
});
