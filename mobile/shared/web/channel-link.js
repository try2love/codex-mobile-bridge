// A route peer is a tiny, verified top-level WebView. Only native code can arm
// one; the chat document, its origin, composer and cached timeline stay alive.
(() => {
  if (window.BridgeChannelLink) return;
  const direct = window.fetch.bind(window);
  const apiPath = path => {
    const url = new URL(path,location.origin);
    if (url.origin!==location.origin || !url.pathname.startsWith('/api/') || url.username || url.password) throw Error('Invalid gateway request');
    return url.pathname+url.search;
  };
  const kept = new Set(['accept','content-type','x-csrf-token','range','if-range','if-none-match']);
  function headers(input) {return [...new Headers(input)].filter(([name])=>kept.has(name));}
  function api(input) {try {return apiPath(typeof input==='string'||input instanceof URL?String(input):input.url);} catch {return null;}}
  function signalError(){return new DOMException('Channel changed','AbortError');}
  let current=null, preview=null, target=location.origin, seq=0, down=false, failures=0, cycle=0, verifying=0, lastRace=0, backoff=3000, timer=0;
  const reads=new Set(), requests=new Map(), peers=new Set();
  const native = action => window.prompt('codexbridge-channels:__BRIDGE_CLIPBOARD_TOKEN__',action);
  let confirmAt=0, trafficVersion=0, proofTrafficVersion=0;
  let nextLanCheck=0, lastProof=-Infinity, lastTraffic=-Infinity, lastReport=-Infinity;
  // The gateway publishes private LAN listeners as HTTP and public entries as HTTPS.
  function autoRoute(reason){
    let menu;try{menu=JSON.parse(native('read'));}catch{return;}
    if(!menu?.rows || !menu.current)return;
    const first=reason==='outage',prefer=reason==='prefer',lan=url=>url.startsWith('http://');
    const targets=menu.mode==='manual'?[]:menu.rows.map(row=>row.url).filter(url=>
      url!==menu.current && (lan(url)?menu.localNetwork:!prefer&&lan(menu.current)));
    if(targets.length)native(reason+':'+JSON.stringify(targets));
    else if(first)native('unreachable');
  }
  function preferLan(){
    if(performance.now()<nextLanCheck || down || preview || target.startsWith('http://'))return;
    nextLanCheck=performance.now()+90000;
    autoRoute('prefer');
  }
  function state(lost){
    down=lost;document.documentElement.classList.toggle('bridge-route-down',lost);
    window.dispatchEvent(new Event('bridge-channel-sample'));
  }
  function traffic(path, started){
    // Public auth/health replies do not prove that this page can use the gateway.
    if(/^\/api\/(?:auth|health|login|pair|connection\/prove)(?:[/?]|$)/.test(path))return;
    const now=performance.now(), recovered=down||failures>0;
    lastTraffic=now;trafficVersion++;failures=0;confirmAt=0;backoff=3000;lastRace=0;
    if(!verifying)clearTimeout(timer);
    if(down)state(false);
    if(recovered||now-lastReport>=30000){lastReport=now;native('traffic:'+Math.min(120000,Math.max(0,Math.round(now-started))));}
    preferLan();
  }
  function reap(peer) {
    if(!peer || peer===current || peer===preview || peer.pending)return;
    peer.port?.close();try{peer.popup?.close();}catch{}peers.delete(peer);
  }
  function finish(id,error){
    const call=requests.get(id);if(!call)return;
    requests.delete(id);call.signal?.removeEventListener('abort',call.abort);call.peer.pending--;
    if(error){call.reject(error);try{call.stream?.error(error);}catch{}}
    reap(call.peer);
  }
  function receive(event){
    const m=event.data||{},call=requests.get(m.id);if(!call || call.peer.port!==event.target)return;
    if(m.type==='error'){finish(m.id,new TypeError('Gateway connection failed'));return;}
    if(m.type==='head'){
      if(call.peer===current && m.status>=200 && m.status<300)traffic(call.path,call.started);
      const stream=m.body?new ReadableStream({
        start(controller){call.stream=controller;},
        pull(){call.peer.port.postMessage({type:'pull',id:m.id});},
        cancel(){call.abort();}
      }):null;
      call.resolve(new Response(stream,{status:m.status,headers:m.headers}));
      if(!m.body)finish(m.id);
    }else if(m.type==='data')call.stream?.enqueue(new Uint8Array(m.bytes));
    else if(m.type==='end'){call.stream?.close();finish(m.id);}
  }
  async function routed(input, options){
    const path=api(input);if(!path)return direct(input,options);
    const req=new Request(input,options),method=req.method,started=performance.now();
    if(!['GET','HEAD','POST'].includes(method))throw new TypeError('Unsupported gateway method');
    if(!current){
      if(method==='POST'){const response=await direct(input,options);if(response.ok&&!response.redirected&&!current)traffic(path,started);return response;}
      const abort=new AbortController(), cancel=()=>abort.abort();reads.add(abort);
      req.signal.addEventListener('abort',cancel,{once:true});if(req.signal.aborted)cancel();
      try{const response=await direct(input,{...options,signal:abort.signal});
        if(response.ok && !response.redirected && !current && !abort.signal.aborted)traffic(path,started);
        return response;}
      finally{reads.delete(abort);req.signal.removeEventListener('abort',cancel);}
    }
    const peer=current,id=++seq,body=method==='POST'?await req.arrayBuffer():null;
    if(req.signal.aborted || peer!==current)throw signalError();
    return new Promise((resolve,reject)=>{
      const abort=()=>{peer.port.postMessage({type:'abort',id});finish(id,signalError());};
      requests.set(id,{resolve,reject,abort,peer,method,path,started,signal:req.signal});peer.pending++;
      req.signal.addEventListener('abort',abort,{once:true});
      try{peer.port.postMessage({type:'fetch',id,path,method,headers:headers(req.headers),body},body?[body]:[]);}
      catch(e){finish(id,e);}
    });
  }
  function worker(parent,nonce,device) {
    if(location.pathname!=='/mobile-channel' || !window.opener)return;
    const pair=new MessageChannel(),port=pair.port1,active=new Map();
    port.onmessage=async ({data:m})=>{
      if(!m || !Number.isSafeInteger(m.id))return;
      const id=m.id,call=active.get(id);
      try{
        if(m.type==='abort'){call?.abort.abort();active.delete(id);return;}
        if(m.type==='pull'){
          if(!call?.reader || call.pulling)return;
          call.pulling=true;const {done,value}=await call.reader.read();call.pulling=false;
          if(done){port.postMessage({type:'end',id});active.delete(id);}
          else {const buf=value.buffer.slice(value.byteOffset,value.byteOffset+value.byteLength);port.postMessage({type:'data',id,bytes:buf},[buf]);}
          return;
        }
        if(m.type!=='fetch'||call||!['GET','HEAD','POST'].includes(m.method))return;
        const abort=new AbortController();active.set(id,{abort});
        const res=await direct(apiPath(m.path),{method:m.method,headers:headers(m.headers),body:m.body??undefined,credentials:'same-origin',redirect:'error',cache:'no-store',signal:abort.signal});
        const row=active.get(id);if(!row)return;
        row.reader=res.body?.getReader();port.postMessage({type:'head',id,status:res.status,headers:[...res.headers],body:!!row.reader});
        if(!row.reader)active.delete(id);
      }catch{active.delete(id);port.postMessage({type:'error',id});}
    };
    direct('/api/auth',{credentials:'same-origin',cache:'no-store',redirect:'error'}).then(r=>r.json()).then(a=>{
      if(a.authenticated && a.trustedDevice && a.mobileRoutes?.deviceId===device){
        window.opener.postMessage({kind:'bridge-route-ready',nonce,csrf:a.csrf},parent,[pair.port2]);
      }
    }).catch(()=>{});
  }
  window.BridgeChannelLink={
    worker,
    get address(){return target;},
    get alternate(){return !!current;},
    get down(){return down;},
    start(){
      if(window.fetch===routed)return;
      window.fetch=routed;
      document.addEventListener('error', event=>{
        const img=event.target;
        if(!current||!(img instanceof HTMLImageElement)||!api(img.src))return;
        event.stopImmediatePropagation();
        const original=img.src;
        routed(original).then(async response=>{
          if(!response.ok)throw Error();const blob=await response.blob();
          const reader=new FileReader();
          reader.onload=()=>{if(img.src===original)img.src=reader.result;};reader.readAsDataURL(blob);
        }).catch(()=>{if(img.src===original)img.onerror?.(event);});
      },true);
    },
    check(reason){
      if(reason==='network')nextLanCheck=0;
      if(document.hidden || verifying || preview)return;
      if(reason==='tick' && !failures && performance.now()-lastTraffic<30000 && performance.now()-lastProof<90000){preferLan();return;}
      if(performance.now()<confirmAt)return;
      const id=++cycle;verifying=id;proofTrafficVersion=trafficVersion;
      clearTimeout(timer);timer=setTimeout(()=>this.result(id,false),12000);
      if(native('verify:'+id)!=='started'){clearTimeout(timer);verifying=0;}
    },
    result(id,ok){
      if(id!==verifying)return;
      clearTimeout(timer);verifying=0;
      if(!ok && trafficVersion>proofTrafficVersion)return;
      if(ok){failures=0;backoff=3000;lastRace=0;lastProof=performance.now();if(down)state(false);preferLan();return;}
      if(++failures===1){confirmAt=performance.now()+1150;timer=setTimeout(()=>this.check(),1200);return;}
      const first=!down;if(first)state(true);
      const now=performance.now();if(first || now-lastRace>=backoff){
        lastRace=now;autoRoute(first?'outage':'retry');backoff=Math.min(60000,backoff*2);
      }
      timer=setTimeout(()=>this.check(),backoff);
    },
    prepare(next,nonce,device){
      if(preview){preview.cancel();preview=null;}
      const peer={pending:0,next,nonce};preview=peer;peers.add(peer);
      const matches=a=>typeof csrf!=='undefined'&&csrf&&a.csrf===csrf;
      let deadline;const detach=()=>{clearTimeout(deadline);window.removeEventListener('message',ready);};
      const reject=()=>{if(preview!==peer)return;detach();preview=null;reap(peer);native('failed:'+nonce);};
      const commit=()=>{
        if(preview!==peer)return;
        if(native('ready:'+nonce)!=='accepted'){reject();return;}
        detach();const prev=current;current=peer.port?peer:null;preview=null;target=next;
        ++cycle;verifying=0;clearTimeout(timer);failures=0;backoff=3000;lastRace=0;lastTraffic=-Infinity;lastProof=performance.now();nextLanCheck=0;
        for(const a of reads)a.abort();
        for(const c of [...requests.values()])if(c.peer===prev && c.method!=='POST')c.abort();
        reap(prev);if(!current)reap(peer);state(false);
        window.dispatchEvent(new Event('bridge-channel-changed'));
      };
      function ready(e){
        if(e.source!==peer.popup||e.origin!==next||e.data?.kind!=='bridge-route-ready'||e.data.nonce!==nonce||!e.ports[0])return;
        if(!matches(e.data)){reject();return;}
        peer.port=e.ports[0];peer.port.onmessage=receive;commit();
      }
      peer.cancel=()=>{detach();if(preview===peer)preview=null;reap(peer);};
      deadline=setTimeout(reject,10000);
      if(next===location.origin){
        direct('/api/auth',{credentials:'same-origin',cache:'no-store',redirect:'error'}).then(r=>r.json()).then(a=>{
          if(a.authenticated&&a.mobileRoutes?.deviceId===device&&matches(a))commit();else reject();
        }).catch(reject);return;
      }
      window.addEventListener('message',ready);
      peer.popup=window.open(next+'/mobile-channel#'+nonce,'_blank');
      if(!peer.popup)reject();
    }
  };
})();
