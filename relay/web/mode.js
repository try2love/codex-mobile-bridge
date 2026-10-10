'use strict';
window.BridgeSharedRelay=true;
const relayScope=/^\/d\/[a-f0-9]{32}(?=\/)/.exec(location.pathname)?.[0]||'';
window.BridgeRelayBase=relayScope;
window.BridgeRelayURL=path=>typeof path==='string'&&path.startsWith('/api/')?relayScope+path:path;
window.BridgeRelayPath=path=>relayScope&&path.startsWith(relayScope+'/')?path.slice(relayScope.length):relayScope?'':path;
// Drafts, selected clients and notification preferences belong to one computer.
if(relayScope){
  const prefix=relayScope+':',rawKey=Storage.prototype.key,rawLength=Object.getOwnPropertyDescriptor(Storage.prototype,'length')?.get;
  if(rawKey&&rawLength){
    const keys=storage=>Array.from({length:rawLength.call(storage)},(_,i)=>rawKey.call(storage,i)).filter(key=>key.startsWith(prefix));
    Storage.prototype.key=function(i){return keys(this)[i]?.slice(prefix.length)??null;};
    Object.defineProperty(Storage.prototype,'length',{configurable:true,get(){return keys(this).length;}});
    const remove=Storage.prototype.removeItem;
    Storage.prototype.clear=function(){for(const key of keys(this))remove.call(this,key);};
  }
  for(const method of ['getItem','setItem','removeItem']){
    const original=Storage.prototype[method];
    Storage.prototype[method]=function(key,...args){return original.call(this,relayScope+':'+key,...args);};
  }
}
if(/^#pair=[A-Za-z0-9_-]{43}$/.test(location.hash))location.replace(relayScope+'/relay/'+location.hash);
document.addEventListener('DOMContentLoaded',()=>{
  for(const id of ['accounts-button','account-button','account-status','pushplus-settings','settings-accounts','settings-pushplus']){
    const node=document.getElementById(id);if(node)node.hidden=true;
  }
  document.getElementById('notification-requests')?.closest('fieldset')?.setAttribute('hidden','');
});
