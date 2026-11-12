const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');

test('Code pages use native tail and UUID anchor while Cowork stays on its own transcript',async()=>{
  const calls=[];
  const code={
    getAll:async()=>[],getSession:async id=>({sessionId:id}),
    getTranscript:async()=>{throw Error('Code full transcript should not be read');},
    getTranscriptTail:async(id,limit)=>{calls.push(['tail',id,limit]);return {messages:[{type:'assistant',uuid:'new',message:{role:'assistant',content:'new'}}],hasMore:true};},
    getTranscriptPage:async(id,before,limit)=>{calls.push(['page',id,before,limit]);return {messages:[{type:'user',uuid:'old',entrypoint:'cli',promptId:'prompt',parentUuid:'parent',sessionId:id,cwd:'/private/unrelated',message:{role:'user',content:'old'}}],hasMore:false,found:true,source:'full'};}
  };
  const cowork={getAll:async()=>[],getSession:async id=>({sessionId:id}),getTranscript:async id=>{calls.push(['cowork',id]);return [{type:'user',uuid:'cowork',message:{role:'user',content:'cowork'}}];}};
  const window={'claude.web':{LocalSessions:code,LocalAgentModeSessions:cowork}};
  const source=fs.readFileSync('bridge/clients/claude/connector.js','utf8').replace('__BRIDGE_CONFIG__',JSON.stringify({transport:'cdp',token:'fixture',generation:'fixture'}));
  await vm.runInNewContext(source,{window,crypto:webcrypto,TextEncoder,Uint8Array,console,setTimeout,clearTimeout});
  let seq=0;
  const invoke=(surface,method,args)=>window.__claudeMobileBridge.invoke({token:'fixture',generation:'fixture',seq:++seq,expires:Date.now()/1000+10,surface,method,args});
  const recent=await invoke('code','mobileDetail',['session',{historyLimit:20}]);
  assert.equal(recent.result.history.before,'new');
  assert.equal(recent.result.history.hasMore,true);
  const older=await invoke('code','mobileDetail',['session',{historyLimit:20,historyBefore:'new'}]);
  assert.equal(older.result.history.hasMore,false);
  for(const [key,value] of Object.entries({entrypoint:'cli',promptId:'prompt',parentUuid:'parent',sessionId:'session'}))assert.equal(older.result.transcript[0][key],value);
  assert.equal(older.result.transcript[0].cwd,undefined);
  assert.deepEqual(calls.slice(0,2),[['tail','session',20],['page','session','new',20]]);
  const coworkPage=await invoke('cowork','mobileDetail',['cowork-session']);
  assert.equal(coworkPage.result.history.hasMore,false);
  assert.ok(calls.some(call=>call[0]==='cowork'));
});
