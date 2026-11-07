// This document has a fresh, app-owned loopback origin. Never forward the
// per-view capability to another origin, to a redirect or to an asset URL.
(()=>{
  'use strict';
  window.BridgeCloudWorkbench=true;
  const prefix='#cmb-cloud=';
  let grant='';
  if(location.hash.startsWith(prefix)){
    grant=location.hash.slice(prefix.length);
    history.replaceState(null,'','/');
    sessionStorage.setItem('cmb-cloud-view',grant);
  }else grant=sessionStorage.getItem('cmb-cloud-view')||'';
  // The loopback port changes on every entry. Persist drafts and read markers in
  // the app sandbox, scoped by the verified cloud account and desktop identity.
  // A synchronous local acknowledgement preserves the existing save/begin-send contract.
  function storageCall(value){
    const xhr=new XMLHttpRequest();
    xhr.open(value===undefined?'GET':'POST','/cmb-cloud-state',false);
    xhr.setRequestHeader('X-CMB-Cloud-View',grant);
    xhr.send(value===undefined?null:JSON.stringify(value));
    if(xhr.status!==200)throw new Error('Cloud draft storage unavailable');
    return JSON.parse(xhr.responseText);
  }
  let saved;
  const snapshot=()=>saved??(saved=storageCall());
  window.BridgeLocalStateStorage={
    getItem:key=>snapshot()[key]??null,
    setItem(key,value){if(snapshot()[key]===value)return;storageCall({key,value});saved[key]=value;},
    removeItem(key){if(!(key in snapshot()))return;storageCall({key,value:null});delete saved[key];}
  };
  const request=window.fetch.bind(window);
  window.fetch=(input,options)=>{
    const outgoing=new Request(input,options), url=new URL(outgoing.url);
    if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))return request(outgoing);
    const headers=new Headers(outgoing.headers);headers.set('X-CMB-Cloud-View',grant);
    return request(new Request(outgoing,{headers,redirect:'error',credentials:'omit'}));
  };
})();
