'use strict';
const chatSyncForeground=()=>!globalThis.document?.hidden&&globalThis.window?.BridgeChatForeground!==false&&
  (globalThis.navigator?.onLine!==false||!!globalThis.window?.OfflineChatReader?.enabled);
function chatSyncScope(provider,host,id,legacyScope){
  return window.BridgeChatSyncCache?.scope(provider,host,id,legacyScope)||'';
}
async function readChatSync(target,mode,params={},source='manual',active=chatSyncForeground,signal){
  const reader=window.OfflineChatReader;
  if(reader?.enabled){
    if(source!=='manual'||mode==='watch'||signal?.aborted||!active()||target.authorized?.()===false)return null;
    const query=new URLSearchParams({mode,...params});
    return reader.request(target.url+(target.url.includes('?')?'&':'?')+query);
  }
  if(mode==='watch'){
    if(signal?.aborted||!active()||target.authorized&&!target.authorized())return null;
    const query=new URLSearchParams({mode,...params});
    return target.request(target.url+(target.url.includes('?')?'&':'?')+query,undefined,signal,target.authorized);
  }
  const cache=window.BridgeChatSyncCache;
  const cached=mode==='body'&&target.scope?await cache?.read(target.scope,mode,params):null;
  if(!active())return null;
  if(mode==='body'&&cached?.version===params.version&&cached.offset===params.offset)return cached;
  const fetchPage=async known=>{
    let scan='',restarted=false;
    while(active()&&(!target.authorized||target.authorized())){
      const query=new URLSearchParams({mode,...params});
      for(const [key,value] of known||[])query.append(key,value);
      if(scan)query.set('scan',scan);
      const value=await target.request(target.url+(target.url.includes('?')?'&':'?')+query,undefined,undefined,target.authorized);
      if(!value?.pending)return value;
      if(value.scanReset&&!restarted){scan='';restarted=true;continue;}
      if(!value.scan||value.scan===scan)throw Error(BridgeI18n.t('读取失败，点击重试'));
      scan=value.scan;
    }
    return null;
  };
  let known;
  if(target.scope){
    known=await cache?.known(target.scope,mode,params,source,{legacyScope:target.legacyScope});
    if(known?.skip)return null;
  }
  if(!active())return null;
  let wire=await fetchPage(known);
  if(!wire)return null;
  // A network read can finish from the offline archive when its connection drops.
  if(window.OfflineChatReader?.enabled)return source==='manual'?wire:null;
  if(known?.probe){
    if(!active()||target.authorized&&!target.authorized())return null;
    known=await cache.afterProbe(known,wire);
    if(!known||known.skip||!active())return null;
    wire=await fetchPage(known);
    if(!wire)return null;
  }
  if(target.authorized&&!target.authorized())return null;
  if(!target.scope||!cache)return wire;
  const page=await cache.write(target.scope,mode,params,wire,{
    source,title:target.title,legacyScope:target.legacyScope,validation:known
  });
  if(page?.discarded)return null;
  if(page)return {...wire,...page,meta:wire.meta,session:wire.session};
  if(!wire.notModified&&!wire.unchanged?.length)return wire;
  if(source!=='manual'||!active())return null;
  // A cache record may have been evicted between conditional request and commit.
  const full=await fetchPage();
  if(!full)return null;
  if(target.authorized&&!target.authorized())return null;
  const written=await cache.write(target.scope,mode,params,full,{source,title:target.title,legacyScope:target.legacyScope,validation:known});
  return written?.discarded?null:full;
}
function beginSelectedChatSync(target,active){
  const token=target.focusToken=Symbol();
  let removedTurns,cancelled=false;
  const cleared=event=>{
    const affected=event.detail?.turnsByScope?.[target.scope];
    if(affected?.length)removedTurns=affected;
  };
  if(target.scope)window.addEventListener('bridge-cache-cleared',cleared);
  const close=()=>{if(target.scope)window.removeEventListener('bridge-cache-cleared',cleared);};
  window.BridgeSyncPrefetch?.focus(target.scope,token);
  const valid=()=>!cancelled&&!removedTurns&&(!target.authorized||target.authorized());
  const promise=readChatSync(target,'recent',{},'manual',()=>valid()&&active()&&chatSyncForeground())
    .then(page=>({page}),error=>({error}));
  const cache=window.BridgeChatSyncCache,epoch=cache?.epoch,generation=cache?.generation;
  // An already authorized tab can read its local preview while loading the renderer.
  const mayPreview=()=>valid()&&cache?.active(target.scope)&&cache.epoch===epoch&&
    cache.generation===generation&&cache.permitted(target.scope);
  const preview=target.scope&&active()&&chatSyncForeground()&&mayPreview()
    ?cache.read(target.scope,'recent',{}).catch(()=>null):null;
  return {
    promise,preview,mayPreview,close,get removedTurns(){return removedTurns;},
    valid,
    cancel(){cancelled=true;close();window.BridgeSyncPrefetch?.settled(target.scope,token);}
  };
}
window.BridgeChatSync={foreground:chatSyncForeground,scope:chatSyncScope,read:readChatSync,begin:beginSelectedChatSync,
  prefetch:target=>readChatSync(target,'recent',{},'prefetch',target.active||chatSyncForeground)
};
