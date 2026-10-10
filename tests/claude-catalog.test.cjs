const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const config={transport:'cdp',token:'fixture',generation:'fixture'};
let sent;
const surface=kind=>({
  getAll:async()=>[{sessionId:kind,model:'historical-model'}],
  getSession:async id=>({sessionId:id,title:kind,oauthToken:'DO_NOT_EXPORT'}),
  getTranscript:async()=>[{type:'assistant',message:{role:'assistant',content:'visible',model:'historical-model',oauthToken:'DO_NOT_EXPORT'}}],
  getSupportedCommands:async()=>[{name:'review',description:'Review this project'}],
  setModel(){},setEffort(){},setPermissionMode(){},start(){},
  sendMessage(...args){sent=args;assert.equal(args[2],undefined);assert.equal(args[3],undefined);assert.equal(args[4],undefined);assert.equal(args[6],undefined);},
});
const window={'claude.web':{LocalSessions:surface('code'),LocalAgentModeSessions:surface('cowork')},
  'claude.settings':{Custom3pSetup:{
    bootstrapStateStore:{getState:async()=>({modelSelectorCatalog:[
      {id:'code',models:[{id:'api/code',name:'Code model',thinking:{effort_options:[{id:'low'},{id:'high',recommended:true}]},secret:'DO_NOT_EXPORT'},
        {id:'unavailable',name:'Unavailable',disabled_reason:{type:'model_disabled'}}]},
      {id:'cowork',models:[{id:'api/cowork',name:'Cowork model',thinking:{effort_options:[{id:'medium',recommended:true}]}}]},
    ]})},
    getLoginDesktop3pStatus:async()=>({enabled:true,thirdPartyConfigured:true,provider:'gateway',hybridOrganizationName:'Fixture API',secret:'DO_NOT_EXPORT'}),
  }}};
const source=fs.readFileSync('bridge/clients/claude/connector.js','utf8').replace('__BRIDGE_CONFIG__',JSON.stringify(config));
(async()=>{
  await vm.runInNewContext(source,{window,crypto:webcrypto,TextEncoder,Uint8Array,console,setTimeout,clearTimeout});
  let seq=0;
  const invoke=(surface,method,args=[])=>window.__claudeMobileBridge.invoke({token:config.token,generation:config.generation,seq:++seq,expires:Date.now()/1000+10,surface,method,args});
  const listing=await invoke('code','mobileList');
  assert.deepEqual(Object.keys(listing.result),['code','cowork']);
  assert.equal(listing.result.code[0].sessionId,'code');assert.equal(listing.result.cowork[0].sessionId,'cowork');
  const detail=await invoke('cowork','mobileDetail',['native']);
  assert.equal(detail.result.session.sessionId,'native');assert.equal(detail.result.transcript[0].message.content,'visible');
  assert.ok(!JSON.stringify(detail).includes('DO_NOT_EXPORT'));
  const code=await invoke('code','mobileCatalog',['code']);
  assert.equal(code.error,undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(code.result.models)),[{id:'api/code',name:'Code model',efforts:['low','high'],defaultEffort:'high'}]);
  assert.equal(code.result.source,'desktop-catalog');
  assert.equal(code.result.capabilities.models,true);
  const cowork=await invoke('cowork','mobileCatalog',['cowork']);
  assert.equal(cowork.result.models[0].id,'api/cowork');
  assert.equal(cowork.result.capabilities.steer,false);
  const account=await invoke('code','mobileAccount');
  assert.equal(account.result.current.kind,'api');assert.equal(account.result.current.label,'Fixture API');
  assert.equal(account.result.canManage,false);assert.ok(!JSON.stringify(account).includes('DO_NOT_EXPORT'));
  assert.deepEqual(JSON.parse(JSON.stringify(account.result.groups.map(group=>[group.surface,group.models.map(model=>model.id)]))),
    [['code',['api/code']],['cowork',['api/cowork']]]);
  const result=await invoke('code','sendMessage',['code','hello',null,null,null,'next',null,'82e2abf7-2265-4d21-aea4-19fcc076b6e9']);
  assert.equal(result.error,undefined);assert.equal(sent[5],'next');
  window['claude.settings'].Custom3pSetup.bootstrapStateStore.getState=async()=>({modelSelectorCatalog:[{id:'code',models:[]}]});
  const empty=await invoke('code','mobileCatalog',['code']);
  assert.equal(empty.result.source,'desktop-catalog');assert.equal(empty.result.models.length,0);
  assert.equal(empty.result.capabilities.models,false);
  assert.equal(empty.result.maxRequestBytes,10485760);
  delete window['claude.settings'];
  const fallback=await invoke('code','mobileCatalog',['code']);
  assert.equal(fallback.result.source,'session-history');assert.equal(fallback.result.capabilities.models,false);
  const unknownAccount=await invoke('code','mobileAccount');
  assert.equal(unknownAccount.result.status,'unknown');assert.equal(unknownAccount.result.current,null);
  assert.equal(unknownAccount.result.groups[0].source,'session-history');
  assert.ok(unknownAccount.result.groups[0].notice);
  console.log('Claude actual selector schema, surface separation, safe account, optional IPC slots and historical fallback passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
