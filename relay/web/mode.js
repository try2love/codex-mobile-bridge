'use strict';
window.BridgeSharedRelay=true;
if(/^#pair=[A-Za-z0-9_-]{43}$/.test(location.hash))location.replace('/relay/'+location.hash);
document.addEventListener('DOMContentLoaded',()=>{
  for(const id of ['accounts-button','account-button','account-status','pushplus-settings','settings-accounts','settings-pushplus']){
    const node=document.getElementById(id);if(node)node.hidden=true;
  }
  document.getElementById('notification-requests')?.closest('fieldset')?.setAttribute('hidden','');
});
