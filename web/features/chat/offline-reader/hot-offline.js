'use strict';
(() => {
  const reader=window.OfflineChatReader;
  if(!reader||reader.cold)return;
  let visit,opening,proof,retry,networkAvailable;

  function route(){return typeof browserNavigation==='undefined'?null:browserNavigation.read();}
  function timeline(selected){
    if(!selected)return null;
    return selected.provider==='codex'?chatTimeline:desktopSessions.syncTimeline;
  }
  function sessionPath(selected){
    if(selected.provider==='codex')return sessionUrl(selected.id,'sync',selected.host);
    return desktopSessions.path('sync',selected.id,selected.provider);
  }
  const query=(path,params)=>path+(path.includes('?')?'&':'?')+new URLSearchParams(params);
  const stillHere=v=>visit===v&&window.BridgeChatSyncCache?.owner===v.owner;
  function keepReadingLayout(selected){
    if(!selected)return;
    const codex=selected.provider==='codex';
    const form=codex?document.getElementById('composer'):desktopSessions.form;
    const notice=codex?document.getElementById('notice'):desktopSessions.notice;
    if(!form?.offsetHeight||!notice)return;
    const hidden=notice.hidden;
    const controls=[...form.children].filter(node=>!node.inert);
    controls.forEach(node=>node.inert=true);
    form.dataset.offlineInPlace='';
    notice.classList.add('offline-readonly-notice');
    const reconnect=codex?document.getElementById('reconnect'):null;
    const reconnectHidden=reconnect?.hidden;
    if(reconnect)reconnect.hidden=false;
    return (restoreNotice=true)=>{
      notice.classList.remove('offline-readonly-notice');
      if(restoreNotice){
        notice.hidden=hidden;
        if(reconnect)reconnect.hidden=reconnectHidden;
      }
      delete form.dataset.offlineInPlace;
      controls.forEach(node=>node.inert=false);
    };
  }
  async function transport(v,action,fields){
    if(!stillHere(v))throw Error('Offline identity changed');
    if(action==='reconnect')return reconnect();
    if(action==='close'){window.BridgeNavigation?.back();return Promise.resolve({});}
    const type={attachment:'transferAttachment',attachmentOpen:'transferAttachmentOpen',list:'list',session:'session'}[action];
    if(!type)throw Error('Offline history is read only');
    const value=await window.CodexBridgeNativeOffline.request({type,...fields,gatewayId:v.owner.gatewayId,identity:v.owner.identity});
    if(!stillHere(v))throw Error('Offline identity changed');
    return value;
  }
  function finished(){
    clearTimeout(retry);retry=null;
    visit?.release?.();
    visit=null;reader.disableHot();
    document.dispatchEvent(new CustomEvent('bridge-hot-offline',{detail:{active:false}}));
  }
  function leaveLayout(){
    if(!visit?.release)return;
    visit.release(false);visit.release=null;
  }
  function schedule(){
    clearTimeout(retry);
    if(visit&&!document.hidden&&networkAvailable!==false&&navigator.onLine!==false)
      retry=setTimeout(()=>{void reconnect().catch(()=>{});},8000);
  }
  async function enterOffline(){
    if(opening)return opening;
    if(visit)return true;
    const owner=window.BridgeChatSyncCache?.owner;
    if(!owner||!window.CodexBridgeNativeOffline||document.getElementById('app')?.hidden!==false)return false;
    const v=visit={owner};
    const selected=route(),view=timeline(selected);
    v.route=selected;v.release=keepReadingLayout(selected);
    window.BridgeChatSyncCache.requireValidation();
    reader.enableHot((action,fields)=>transport(v,action,fields));
    view?.cancelWatch();
    const start=async()=>{
      try{
        const list=await reader.refreshList();
        if(!stillHere(v))return false;
        let syncedAt;
        if(selected&&view?.syncTarget&&view===timeline(route())){
          const target=view.syncTarget;
          target.offline=true;
          // Prime offline pagination without replacing the messages already on screen.
          const page=await reader.request(query(sessionPath(selected),{mode:'recent'})).catch(()=>null);
          if(stillHere(v)&&view===timeline(route())&&page){
            v.original={view,before:view.before,hasMore:view.hasMore,offlineBefore:page.before};
            const anchor=view.capture(),following=view.following&&view.atBottom();
            view.before=page.before;view.hasMore=page.hasMore;view.historyStatus();
            view.following=following;view.restore(anchor,following);
            syncedAt=page.meta?.offlineSync;
          }
        }
        if(!stillHere(v))return false;
        syncedAt??=(list.sessions||[]).reduce((latest,s)=>Math.max(latest,Number(s.syncedAt)||0),0);
        reader.showStatus?.(syncedAt);
        document.dispatchEvent(new CustomEvent('bridge-hot-offline',{detail:{active:true,syncedAt}}));
        schedule();return true;
      }catch{
        if(stillHere(v))finished();
        return false;
      }
    };
    opening=start().finally(()=>{opening=null;});
    return opening;
  }
  async function fallback(path,body,signal,error){
    if(signal?.aborted||!await enterOffline()||signal?.aborted||body!==undefined)throw error;
    return reader.request(path);
  }
  async function reconnect(){
    if(proof)return proof;
    const v=visit;
    if(!v||document.hidden)return false;
    const attempt=async()=>{
      const selected=route(),view=timeline(selected);
      const current=()=>stillHere(v)&&!document.hidden&&JSON.stringify(route())===JSON.stringify(selected);
      const abort=new AbortController(),deadline=setTimeout(()=>abort.abort(),7000);
      try{
        const auth=await api('/api/auth',undefined,abort.signal,current,true);
        if(!current())return false;
        if(!auth.authenticated){finished();authLost(auth.authStatus);return false;}
        if(auth.sessionSync?.version!==1||auth.sessionSync.gatewayId!==v.owner.gatewayId||auth.sessionSync.identity!==v.owner.identity){
          finished();showLogin();await enter(auth);return false;
        }
        if(selected){
          const page=await api(query(sessionPath(selected),{mode:'recent',indexOnly:1,limit:1}),undefined,abort.signal,current,true);
          if(!current()||!Array.isArray(page.index))return false;
        }
        finished();
        if(view&&view===timeline(route())&&view.syncTarget){
          view.syncTarget.offline=false;
          const fresh=await view.syncRead('recent',{},'manual');
          if(visit||window.BridgeChatSyncCache.owner!==v.owner)return false;
          if(fresh&&!view.abort.signal.aborted&&view===timeline(route())){
            // The archive and the gateway issue different history cursors.
            // Restore the online boundary if the reader stayed in the original window.
            const original=v.original;
            const stayed=original?.view===view&&view.before===original.offlineBefore;
            view.before=stayed?original.before:fresh.before;
            view.hasMore=stayed?original.hasMore:fresh.hasMore;
            await view.mergeSyncRecent(fresh,'manual');
          }
          if(!view.syncLoop&&!view.abort.signal.aborted)void view.syncUpdates();
          view.wakeSync?.();
        }else if(!selected){
          if(isCodexClient(clientNavigation.provider))await loadList(true);else await desktopSessions.refresh();
        }
        return true;
      }catch{return false;}finally{clearTimeout(deadline);if(visit===v)schedule();}
    };
    proof=attempt().finally(()=>{proof=null;});
    return proof;
  }
  function change(available){
    networkAvailable=available;
    if(document.hidden)return;
    if(!available){clearTimeout(retry);void enterOffline();}
    else if(visit)void reconnect();
  }
  window.addEventListener('offline',()=>change(false));
  window.addEventListener('online',()=>change(true));
  window.addEventListener('codexbridge-native-offline-state',event=>{
    const available=event.detail?.networkAvailable;
    if(typeof available==='boolean'&&available!==networkAvailable)change(available);
  });
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden){clearTimeout(retry);return;}
    if(networkAvailable===false||navigator.onLine===false)void enterOffline();
    else if(visit)void reconnect();
  });
  document.addEventListener('bridge-auth-lost',()=>{if(visit)finished();});
  document.addEventListener('bridge-offline-leave',leaveLayout);
  document.addEventListener('bridge-chat-visible',()=>{
    if(visit?.release&&JSON.stringify(route())!==JSON.stringify(visit.route))leaveLayout();
  });
  window.BridgeHotOffline={fallback,reconnect};
})();
