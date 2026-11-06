// This document has a fresh, app-owned loopback origin. Never forward the
// per-view capability to another origin, to a redirect or to an asset URL.
(()=>{
  'use strict';
  const prefix='#cmb-cloud=';
  let grant='';
  if(location.hash.startsWith(prefix)){
    grant=location.hash.slice(prefix.length);
    history.replaceState(null,'','/');
    sessionStorage.setItem('cmb-cloud-view',grant);
  }else grant=sessionStorage.getItem('cmb-cloud-view')||'';
  const request=window.fetch.bind(window);
  window.fetch=(input,options)=>{
    const outgoing=new Request(input,options), url=new URL(outgoing.url);
    if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))return request(outgoing);
    const headers=new Headers(outgoing.headers);headers.set('X-CMB-Cloud-View',grant);
    return request(new Request(outgoing,{headers,redirect:'error',credentials:'omit'}));
  };
})();
