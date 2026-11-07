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
  let confirmAt=0;
  let nextLanCheck=0, lastProof=-Infinity, lastTraffic=-Infinity;
  let measurement=null, measured=null, fallback=0;
  function healthy(){
    failures=0;backoff=3000;lastRace=0;confirmAt=0;
    if(down)state(false);
  }
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
      if(call.peer===current && m.status>=200 && m.status<300)lastTraffic=performance.now();
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
    const req=new Request(input,options),method=req.method;
    if(!['GET','HEAD','POST'].includes(method))throw new TypeError('Unsupported gateway method');
    if(!current){
      if(method==='POST')return direct(input,options);
      const abort=new AbortController(), cancel=()=>abort.abort();reads.add(abort);
      req.signal.addEventListener('abort',cancel,{once:true});if(req.signal.aborted)cancel();
      try{const response=await direct(input,{...options,signal:abort.signal});
        if(response.ok && !current && !abort.signal.aborted)lastTraffic=performance.now();
        return response;}
      finally{reads.delete(abort);req.signal.removeEventListener('abort',cancel);}
    }
    const peer=current,id=++seq,body=method==='POST'?await req.arrayBuffer():null;
    if(req.signal.aborted || peer!==current)throw signalError();
    return new Promise((resolve,reject)=>{
      const abort=()=>{peer.port.postMessage({type:'abort',id});finish(id,signalError());};
      requests.set(id,{resolve,reject,abort,peer,method,signal:req.signal});peer.pending++;
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
    get latency(){return !down && measured?.target===target && performance.now()-measured.at<90000 ? measured.ms : null;},
    measure(){
      if(document.hidden || preview || typeof csrf!=='string' || !csrf)return Promise.resolve(false);
      if(measurement)return measurement;
      const peer=current,address=target,expected=csrf,started=performance.now(),abort=new AbortController();
      const deadline=setTimeout(()=>abort.abort(),5000);
      measurement=(async()=>{
        try{
          // A native probe and a WebView can use different network paths. Only
          // the already authenticated current route may use this fallback; it
          // never authorizes another origin or supplies credentials to one.
          const response=await routed(new URL('/api/auth',location.origin).href,{credentials:'same-origin',cache:'no-store',redirect:'error',signal:abort.signal});
          if(response.status!==200 || response.redirected)return false;
          const reader=response.body?.getReader();if(!reader)return false;
          const cancel=()=>{reader.cancel().catch(()=>{});};
          abort.signal.addEventListener('abort',cancel,{once:true});
          let text='',bytes=0;
          try{
            const decoder=new TextDecoder();
            while(!abort.signal.aborted){
              const chunk=await reader.read();if(chunk.done)break;
              bytes+=chunk.value.byteLength;if(bytes>32768)return false;
              text+=decoder.decode(chunk.value,{stream:true});
            }
            text+=decoder.decode();
          }finally{abort.signal.removeEventListener('abort',cancel);cancel();}
          const auth=JSON.parse(text);
          if(abort.signal.aborted || document.hidden || preview || peer!==current || address!==target || csrf!==expected ||
             auth.authenticated!==true || auth.trustedDevice!==true || auth.csrf!==expected)return false;
          measured={target:address,ms:Math.max(0,Math.round(performance.now()-started)),at:performance.now()};
          healthy();window.dispatchEvent(new Event('bridge-channel-sample'));return true;
        }catch{return false;}
        finally{clearTimeout(deadline);measurement=null;}
      })();
      return measurement;
    },
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
      const id=++cycle;verifying=id;
      clearTimeout(timer);timer=setTimeout(()=>this.result(id,false),6000);
      if(native('verify:'+id)!=='started'){clearTimeout(timer);verifying=0;}
    },
    result(id,ok){
      if(id!==verifying)return;
      if(document.hidden){clearTimeout(timer);verifying=0;return;}
      if(!ok && typeof csrf==='string' && csrf){
        if(fallback===id)return;
        fallback=id;clearTimeout(timer);
        return this.measure().then(verified=>{if(id===verifying)this.complete(id,verified);});
      }
      this.complete(id,ok);
    },
    complete(id,ok){
      if(id!==verifying)return;
      clearTimeout(timer);verifying=0;
      if(document.hidden)return;
      if(ok){healthy();lastProof=performance.now();preferLan();return;}
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
        ++cycle;verifying=0;clearTimeout(timer);measured=null;failures=0;backoff=3000;lastRace=0;lastTraffic=-Infinity;lastProof=performance.now();nextLanCheck=0;
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
