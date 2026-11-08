'use strict';
/* Loaded by the live desktop only when serving through the account-Web tunnel. */
(()=>{
  const script=document.currentScript,base=script?.dataset.base||'',account=script?.dataset.account||'';
  if(!/^\/d\/[0-9a-f-]{36}$/.test(base)||!/^[0-9a-f-]{36}$/.test(account))throw Error('Invalid cloud Web scope');
  window.BridgeCloudWeb=true;window.BridgeSharedRelay=true;window.BridgeRelayBase=base;
  window.BridgeRelayURL=path=>typeof path==='string'&&/^\/api(?:\/|\?|$)/.test(path)?base+path:path;
  window.BridgeRelayPath=path=>typeof path==='string'&&path.startsWith(base+'/')?path.slice(base.length):'';
  const prefix='bridge-cloud-web:'+account+':'+base+':';
  const proto=Storage.prototype,get=proto.getItem,set=proto.setItem,remove=proto.removeItem;
  const key=proto.key,length=Object.getOwnPropertyDescriptor(proto,'length').get;
  const keys=storage=>Array.from({length:length.call(storage)},(_,i)=>key.call(storage,i)).filter(value=>value?.startsWith(prefix));
  proto.key=function(index){return keys(this)[index]?.slice(prefix.length)??null;};
  Object.defineProperty(proto,'length',{configurable:true,get(){return keys(this).length;}});
  proto.clear=function(){for(const value of keys(this))remove.call(this,value);};
  proto.getItem=function(key){return get.call(this,prefix+key);};
  proto.setItem=function(key,value){return set.call(this,prefix+key,value);};
  proto.removeItem=function(key){return remove.call(this,prefix+key);};
  // Keep existing preference listeners working between tabs of this computer,
  // without delivering another account/computer's storage events to them.
  window.addEventListener('storage',event=>{
    if(typeof event.key==='string'&&event.key.startsWith(prefix))Object.defineProperty(event,'key',{value:event.key.slice(prefix.length)});
    else event.stopImmediatePropagation();
  },true);
  document.addEventListener('DOMContentLoaded',()=>{
    for(const id of ['accounts-button','account-button','account-status','pushplus-settings','settings-accounts','settings-pushplus','harness-open']){
      const node=document.getElementById(id);if(node)node.hidden=true;
    }
    document.getElementById('notification-requests')?.closest('fieldset')?.setAttribute('hidden','');
  });
})();
