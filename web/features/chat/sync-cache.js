'use strict';
(() => {
  const id=(scope,kind,key)=>JSON.stringify([scope,kind,key]);
  const record=(scope,kind,key,value,turn='',parent='')=>({id:id(scope,kind,key),scope,kind,key,turn,parent,value});
  const query=request=>new Promise((resolve,reject)=>{
    request.onsuccess=()=>resolve(request.result);
    request.onerror=()=>reject(request.error);
  });
  // All content and its manifest share one transaction, including range invalidations.
  class SyncDisk {
    constructor(database){this.database=database;this.volatile=new Map();}
    open(){
      if(!this.database)return Promise.resolve(null);
      if(!this.ready)this.ready=new Promise(resolve=>{
        try{
          const request=this.database.open('bridge-versioned-chat',1);
          request.onupgradeneeded=()=>{
            const store=request.result.createObjectStore('records',{keyPath:'id'});
            for(const field of ['kind','turn','parent'])store.createIndex(field,['scope',field]);
            store.createIndex('category','kind');
          };
          request.onerror=request.onblocked=()=>resolve(null);
          request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>{db.close();this.ready=null;};resolve(db);};
        }catch{resolve(null);}
      });
      return this.ready;
    }
    get(scope,kind,keys){return this.readRefs(scope,keys.map(key=>[kind,key]));}
    async readRefs(scope,refs){
      if(!refs.length)return [];
      const db=await this.open(),store=db?.transaction('records').objectStore('records');
      return Promise.all(refs.map(async ([kind,key])=>(store?await query(store.get(id(scope,kind,key))):this.volatile.get(id(scope,kind,key)))?.value));
    }
    async list(kind,scope){
      const db=await this.open();
      if(!db)return [...this.volatile.values()].filter(row=>row.kind===kind&&(!scope||row.scope===scope));
      const store=db.transaction('records').objectStore('records');
      return query(scope?store.index('kind').getAll([scope,kind]):store.index('category').getAll(kind));
    }
    async change(scope,ops,revision){
      const db=await this.open(),tx=db?.transaction('records','readwrite'),store=tx?.objectStore('records');
      const done=tx?new Promise((ok,no)=>{tx.oncomplete=ok;tx.onabort=tx.onerror=()=>no(tx.error||Error('Cache transaction failed'));}):null;
      if(typeof revision==='number'){
        const marker=store?await query(store.get(id(scope,'local','revision'))):this.volatile.get(id(scope,'local','revision'));
        if((marker?.value||0)!==revision){await done;return false;}
      }
      for(const op of ops){
        if(op.id){if(store)store.put(op);else this.volatile.set(op.id,op);continue;}
        if(op.remove){if(store)store.delete(op.remove);else this.volatile.delete(op.remove);continue;}
        const keys=store?await query(store.index(op.field).getAllKeys([scope,op.value])):
          [...this.volatile.values()].filter(row=>row.scope===scope&&row[op.field]===op.value).map(row=>row.id);
        for(const key of keys){if(store)store.delete(key);else this.volatile.delete(key);}
      }
      await done;return true;
    }
  }
  const fields=(value,names)=>Object.fromEntries(names.filter(name=>value?.[name]!==undefined).map(name=>[name,value[name]]));
  const rowFields='key version id role kind turnId turnStatus order status title text timestamp phase truncated totalChars textLength byteLength attachments fileSummary activityFiles toolName model effort turnStartedAt turnCompletedAt turnCompletionKnown turnError originalQuestion interaction hasDetail processCursor detailCursor processCount count firstKey lastKey'.split(' ');
  const safeRow=row=>fields({...row,phase:row.phase||(row.role==='assistant'&&row.forkable===true?'final_answer':undefined)},rowFields);
  const contextFields=['contextUsage','contextUsageStatus','lastPromptContextUsage'];
  const safePage=page=>({
    ...fields(page,['version','sourceVersion','key','group','before','after','next','hasMore','hasEarlier','hasLater','previous','range','turns','offset','limit','sequence','epoch','reset']),
    index:(page.index||[]).map(item=>safeRow(item)),
    meta:{...fields(page.meta,['title','model','effort','cwd','status','turnStatus','turnId','running','updatedAt','loadingHistory','temporary']),
      ...fields(page.session||page.meta,contextFields),requests:[]},files:[],
  });
  const dateOf=value=>{
    if(value===null||value===undefined||value==='')return 0;
    const n=Number(value),stamp=Number.isFinite(n)?(n<1e12?n*1000:n):Date.parse(value);
    return Number.isFinite(stamp)&&stamp>0?stamp:0;
  };
  const dayOf=stamp=>{if(!stamp)return '日期未知';const d=new Date(stamp);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');};
  const pageKey=(mode,p)=>JSON.stringify([mode,...Object.entries(p||{}).filter(([key])=>!['version','ifVersion','known','omitTurn'].includes(key)).sort()]);
  const native=()=>window.CodexBridgeNativeOffline;
  const isPhone=()=>!!(native()||typeof BridgeHost!=='undefined'&&BridgeHost.isNativeApp());
  class SessionCache {
    constructor(disk){
      this.disk=disk;this.epoch=0;
      if(typeof BroadcastChannel==='function'){
        this.channel=new BroadcastChannel('bridge-chat-cache-events');
        this.channel.onmessage=event=>{
          if(event.data?.turnsByScope)this.cleared(event.data.turnsByScope,false);
        };
      }
      try{this.epoch=Number(sessionStorage.getItem('bridge-cache-access-epoch'))||0;}catch{}
      this.verified=new Set();this.seeded=new Map();this.scopes=new Map();this.generation=0;this.pending=new Map();this.resident=new Map();
    }
    supported(){return !!this.owner;}
    requireValidation(){
      this.epoch++;this.verified.clear();
      try{sessionStorage.setItem('bridge-cache-access-epoch',String(this.epoch));}catch{}
    }
    permitted(scope){
      if(this.verified.has(scope))return true;
      if(!this.login||isPhone())return false;
      try{return sessionStorage.getItem('bridge-cache-access:'+scope)===this.login+':'+this.epoch;}catch{return false;}
    }
    confirm(scope){
      this.verified.add(scope);
      if(this.login&&!isPhone())try{sessionStorage.setItem('bridge-cache-access:'+scope,this.login+':'+this.epoch);}catch{}
    }
    scope(provider,host,session,legacyScope){
      if(!this.owner||!provider||!session)return '';
      const scope=JSON.stringify([this.owner.gatewayId,this.owner.identity,provider,host||'local',session]);
      const previous=this.scopes.get(scope)||{};
      this.scopes.set(scope,{...previous,sessionId:JSON.stringify([provider,host||'local',session]),legacyScope:legacyScope||previous.legacyScope});
      return scope;
    }
    async bind(auth){
      const sync=auth?.sessionSync,valid=sync?.version===1&&/^[a-f0-9]{32}$/.test(sync.gatewayId)&&/^[a-f0-9]{64}$/.test(sync.identity);
      const tag=auth?.listCacheIdentity||'';
      if(this.owner?.gatewayId===sync?.gatewayId&&this.owner?.identity===sync?.identity&&tag===this.login&&valid)return;
      this.generation++;this.verified.clear();this.seeded.clear();this.scopes.clear();this.resident.clear();
      this.owner=valid?{gatewayId:sync.gatewayId,identity:sync.identity}:null;this.login=tag;this.name=auth?.computer?.name||'';
      window.BridgeSyncPrefetch?.reset();
      if(this.owner){this.lastOwner=this.owner;this.nativeState=null;
        void this.disk.open();
        if(!this.hasLegacy&&globalThis.indexedDB?.databases)
          this.hasLegacy=indexedDB.databases().then(names=>names.some(db=>db.name==='bridge-chat-history')).catch(()=>true);
        try{sessionStorage.setItem('bridge-last-cache-owner',JSON.stringify(this.owner));}catch{}
        if(native()){
          const owner=this.owner;
          this.nativeReady=this.event('auth',{status:'valid'}).then(state=>{if(this.owner===owner)this.nativeState=state;return state;}).catch(()=>null);
        }
      }
    }
    async suspend(status){
      const owner=this.owner||this.lastOwner||(()=>{try{return JSON.parse(sessionStorage.getItem('bridge-last-cache-owner'));}catch{return null;}})();
      this.generation++;this.requireValidation();this.seeded.clear();this.scopes.clear();this.resident.clear();this.owner=null;this.nativeState=null;
      window.BridgeSyncPrefetch?.reset();
      if(owner&&native())await native().request({type:'auth',...owner,status}).catch(()=>{});
    }
    event(type,extra={}){return native().request({type,...this.owner,...extra});}
    active(scope){return !!this.owner&&this.scopes.has(scope);}
    memory(scope){
      let memory=this.resident.get(scope);
      if(!memory){
        memory={rows:new Map(),pages:new Map(),bans:new Set(),revision:0};this.resident.set(scope,memory);
        if(this.resident.size>20)this.resident.delete(this.resident.keys().next().value);
      }
      return memory;
    }
    onlineKnown(scope,mode,params={},source='manual'){
      const out=new URLSearchParams(),memory=this.memory(scope);
      Object.assign(out,{network:true,generation:this.generation,epoch:this.epoch,source,revision:memory.revision,
        clearRevision:this.nativeState?.clearRevision,nativeBinding:this.nativeReady,resident:new Map(memory.rows),bans:new Set(memory.bans)});
      if(!this.active(scope)){out.skip=true;return out;}
      if(source!=='manual')for(const turn of memory.bans)out.append('omitTurn',turn);
      const page=memory.pages.get(pageKey(mode,params));
      out.cold=!page;
      if(mode==='body'){out.body=page;return out;}
      let hints=[...memory.rows.values()].slice(-80);
      if(mode==='process'){
        const keys=new Map();
        for(const [key,page] of memory.pages){
          const [name,...pairs]=JSON.parse(key);
          if(name==='process'&&Object.fromEntries(pairs).key===params.key)
            for(const row of page.index||[])keys.set(row.key,row.order);
        }
        const direction=params.after==='latest'?-1:1;
        hints=[...keys].sort((a,b)=>direction*(a[1]-b[1])).slice(0,100).map(([key])=>memory.rows.get(key)).filter(Boolean);
      }
      for(const row of hints){
        if(source!=='manual'&&memory.bans.has(row.turnId))continue;
        out.append('known',row.key+':'+row.version);
      }
      if(page?.version&&!memory.bans.size&&page.index?.every(row=>memory.rows.get(row.key)?.version===row.version))out.set('ifVersion',page.version);
      // The next response may send a complete page if the resident hints exceed the URL budget.
      if(out.toString().length>6500){out.delete('known');out.delete('ifVersion');out.delete('omitTurn');}
      return out;
    }
    onlinePage(scope,mode,params,wire,validation){
      if(!this.active(scope)||validation.generation!==this.generation||validation.epoch!==this.epoch)return {discarded:true};
      const memory=this.memory(scope);
      if(validation.revision!==memory.revision)return {discarded:true};
      if(mode==='body'){
        if(wire.notModified)return memory.pages.get(pageKey(mode,params))||null;
        memory.pages.set(pageKey(mode,params),wire);
        while(memory.pages.size>30)memory.pages.delete(memory.pages.keys().next().value);
        return wire;
      }
      const prior=memory.pages.get(pageKey(mode,params)),index=wire.index||(wire.notModified?prior?.index:null);
      if(!Array.isArray(index))return wire;
      const updates=new Map((wire.rows||wire.items||[]).map(row=>[row.key,row])),rows=[],kept=[];
      for(const item of index){
        if(validation.source!=='manual'&&memory.bans.has(item.turnId))continue;
        const content=updates.get(item.key)||validation.resident.get(item.key);
        if(content?.version!==item.version||content.textRef)return null;
        const row={...content,...item};rows.push(row);kept.push(item);
      }
      for(const key of wire.deleted||[])memory.rows.delete(key);
      for(const row of rows){memory.rows.delete(row.key);memory.rows.set(row.key,safeRow(row));}
      while(memory.rows.size>400)memory.rows.delete(memory.rows.keys().next().value);
      if(validation.source==='manual')for(const row of rows)memory.bans.delete(row.turnId);
      const page={...wire,index:kept,rows,...(mode==='process'?{items:rows}:{})};
      memory.pages.set(pageKey(mode,params),safePage(page));
      while(memory.pages.size>30)memory.pages.delete(memory.pages.keys().next().value);
      return page;
    }
    async hydrateOnline(scope,wire,validation){
      const rows=await this.disk.get(scope,'row',(wire.index||[]).map(row=>row.key));
      return {...validation,resident:new Map([...validation.resident,...rows.filter(Boolean).map(row=>[row.key,row])])};
    }
    async localPreview(scope){
      if(!this.active(scope))return null;
      if(!native())return this.read(scope,'recent',{});
      const owner=this.owner,gen=this.generation,epoch=this.epoch;
      const memory=this.resident.get(scope),saved=memory?.pages.get(pageKey('recent',{}));
      if(saved?.index?.length&&saved.index.every(item=>memory.rows.get(item.key)?.version===item.version)){
        return {...saved,rows:saved.index.map(item=>({...memory.rows.get(item.key),...item})),files:[],
          meta:{...saved.meta,requests:[]}};
      }
      const state=await this.nativeReady;
      const current=()=>owner===this.owner&&gen===this.generation&&epoch===this.epoch&&this.active(scope);
      if(state?.recordPages!==1||!current())return null;
      for(let attempt=0;attempt<30&&current();attempt++){
        const payload=await this.event('read',{page:true,sessionId:this.scopes.get(scope)?.sessionId});
        if(!current())return null;
        const page=payload?.page;
        if(page?.recordPage!==1)return null;
        if(!page.migrating||page.rows?.length||page.turns?.some(turn=>turn.processes?.length))
          return window.OfflineChatReader?.projectPage(page)||null;
        await new Promise(resolve=>setTimeout(resolve,150));
      }
      return null;
    }

    async state(scope){
      if(!isPhone())return {};
      if(!native()||!this.active(scope))return null;
      try{
        const state=await this.event('state',{sessionId:this.scopes.get(scope).sessionId});
        if(!state||!this.active(scope))return null;
        const changed=this.nativeState?.clearRevision!==undefined&&this.nativeState.clearRevision!==state.clearRevision;
        this.nativeState=state;
        await this.applyNativeClear(scope,state,changed);
        return state;
      }catch{return null;}
    }
    async applyNativeClear(scope,state,announce=true){
      const session=this.scopes.get(scope)?.sessionId||JSON.stringify(JSON.parse(scope).slice(2));
      const gone=state?.tombstones?.[session]||[];
      if(!gone.length)return;
      const rev=await this.revision(scope),[removed]=await this.disk.get(scope,'local',['removed']);
      const fresh=gone.filter(turn=>!removed?.includes(turn));if(!fresh.length)return;
      const ops=await this.clearTurns(scope,fresh);
      ops.push(record(scope,'local','removed',[...new Set([...(removed||[]),...fresh])]),
        record(scope,'local','revision',rev+1));
      if(await this.disk.change(scope,ops,rev)){
        if(announce)this.cleared({[scope]:fresh});
        const [catalog]=await this.disk.get(scope,'catalog',['session']);
        const legacy=this.scopes.get(scope)?.legacyScope||catalog?.legacyScope;
        if(legacy)await window.BridgeChatHistoryCache?.forget(legacy);
      }
    }
    cleared(turnsByScope,broadcast=true){
      if(!Object.keys(turnsByScope).length)return;
      for(const [scope,turns] of Object.entries(turnsByScope)){
        const memory=this.resident.get(scope);if(!memory)continue;
        memory.revision++;for(const turn of turns)memory.bans.add(turn);
        for(const [key,row] of memory.rows)if(memory.bans.has(row.turnId))memory.rows.delete(key);
        memory.pages.clear();
      }
      const detail={turnsByScope};
      if(broadcast)this.channel?.postMessage(detail);
      window.dispatchEvent(new CustomEvent('bridge-cache-cleared',{detail}));
    }
    async clearTurns(scope,ids){
      const turns=new Set(ids),ops=ids.map(turn=>({field:'turn',value:turn}));
      for(const page of await this.disk.list('page',scope)){
        if(!page.value.index.some(item=>turns.has(item.turnId)))continue;
        const body={...page.value,version:null,index:page.value.index.filter(item=>!turns.has(item.turnId)),
          turns:(page.value.turns||[]).filter(turn=>!turns.has(turn.id))};
        ops.push(record(scope,'page',page.key,body));
      }
      return ops;
    }
    async nativeChanged(){
      if(!this.owner||!native())return;
      const owner=this.owner,state=await this.event('state');
      if(!state||this.owner!==owner)return;
      if(!Object.values(state.tombstones||{}).some(ids=>ids.length))return;
      const changed=this.nativeState?.clearRevision!==undefined&&this.nativeState.clearRevision!==state.clearRevision;
      this.nativeState=state;
      const catalogs=await this.disk.list('catalog');
      for(const {scope} of catalogs){
        const [gateway,identity]=JSON.parse(scope);
        if(gateway===owner.gatewayId&&identity===owner.identity)await this.applyNativeClear(scope,state,changed);
      }
    }
    legacy(scope,legacyScope){
      if(!this.active(scope))return;
      if(!this.seeded.has(scope))this.seeded.set(scope,this.importLegacy(scope,legacyScope));
      return this.seeded.get(scope);
    }
    async importLegacy(scope,legacyScope){
      const gen=this.generation;
      const ops=[];
      // Old cache keys were bound to the current gateway instance and login secret digest.
      let parts;try{parts=JSON.parse(legacyScope);}catch{}
      if(parts?.[0]===this.login&&parts.at(-1)===JSON.parse(scope).at(-1)&&(isPhone()||!this.hasLegacy||await this.hasLegacy)){
        const old=await window.BridgeChatHistoryCache?.read(legacyScope,'timeline-recent-v1');
        // Desktop snapshots contain native messages without timeline versions.
        // Offer only hashes of locally held text; the server can confirm an exact match.
        if(old?.messages&&globalThis.crypto?.subtle){
          const held=new Map();
          await Promise.all(old.messages.slice(-40).map(async row=>{
            if(!row.text||typeof row.text!=='string')return;
            const bytes=new TextEncoder().encode(row.text);
            const hash=new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
            const token=Array.from(hash.slice(0,12),b=>b.toString(16).padStart(2,'0')).join('');
            held.set(token,row.text);
          }));
          if(gen===this.generation&&this.active(scope))this.scopes.get(scope).held=held;
        }
        for(const row of old?.rows||[]){
          if(/^[a-f0-9]{24}-[0-9]+$/.test(row.key)&&/^[a-f0-9]{24}$/.test(row.version)&&row.turnId)
            ops.push(record(scope,'row',row.key,safeRow(row),row.turnId));
        }
      }
      if(native()){
        try{
          const seed=await this.event('read',{sessionId:this.scopes.get(scope).sessionId});
          if(seed?.turns?.length&&this.active(scope))this.scopes.get(scope).nativeHasHistory=true;
          for(const turn of seed?.turns||[])for(const message of turn.messages||[]){
            if(message.row?.key!==message.key||message.row?.version!==message.version)continue;
            ops.push(record(scope,'row',message.key,safeRow(message.row),turn.id));
            for(const body of message.bodyParts||[]){
              const part={...fields(body,['offset','text','next']),key:message.key,version:message.version,turnId:turn.id};
              ops.push(record(scope,'chunk',pageKey('body',{key:message.key,offset:body.offset}),part,turn.id,message.key));
            }
          }
        }catch{}
      }
      if(!ops.length||gen!==this.generation||!this.active(scope))return;
      const state=await this.state(scope),rev=await this.revision(scope),[hot]=await this.disk.get(scope,'local',['hot']);
      if(isPhone()&&!state)return;
      const bans=await this.bans(scope,state),kept=ops.filter(row=>!bans.has(row.turn));
      if(gen===this.generation&&this.active(scope))await this.disk.change(scope,[...kept,record(scope,'local','hot',
        [...new Set([...kept.filter(r=>r.kind==='row').map(r=>r.key),...(hot||[])])].slice(0,100))],rev);
    }
    async revision(scope){return (await this.disk.get(scope,'local',['revision']))[0]||0;}
    async bans(scope,state){
      const [local]=await this.disk.get(scope,'local',['removed']);
      return new Set([...(local||[]),...(state?.tombstones?.[this.scopes.get(scope)?.sessionId]||[])]);
    }
    async known(scope,mode,params={},source='manual'){
      const out=new URLSearchParams();out.generation=this.generation;out.epoch=this.epoch;out.source=source;
      if(!this.active(scope))return out;
      if(native())await this.legacy(scope,this.scopes.get(scope).legacyScope);
      const state=await this.state(scope),key=pageKey(mode,params);
      const [rev,removed,initialHot,page]=await this.disk.readRefs(scope,[['local','revision'],['local','removed'],['local','hot'],['page',key]]);
      let hot=initialHot;
      if(!page&&!native()&&mode==='recent'&&source==='manual'&&this.scopes.get(scope)?.legacyScope&&(!this.hasLegacy||await this.hasLegacy)){
        await this.legacy(scope,this.scopes.get(scope).legacyScope);
        [hot]=await this.disk.get(scope,'local',['hot']);
      }
      out.revision=rev||0;out.clearRevision=state?.clearRevision;
      out.bans=new Set([...(removed||[]),...(state?.tombstones?.[this.scopes.get(scope)?.sessionId]||[])]);
      if(isPhone()&&!state){out.skip=source==='prefetch';return out;}
      if(source==='prefetch'&&!state?.allowPrefetch){out.skip=true;return out;}
      if(source!=='manual'&&out.bans.size){
        if(['recent','older','range'].includes(mode)){
          if(out.bans.size>100){out.probe=true;out.append('indexOnly','1');}
          else for(const turn of out.bans)out.append('omitTurn',turn);
        }else if(mode==='process'){
          const [group]=await this.disk.get(scope,'row',[params.key]);
          if(!group||out.bans.has(group.turnId)){out.skip=true;return out;}
        }
      }
      if(mode==='recent'&&source==='manual'){
        out.held=this.scopes.get(scope)?.held;
        for(const token of out.held?.keys()||[])out.append('heldText',token);
      }
      if(mode==='body'){
        const [part]=await this.disk.get(scope,'chunk',[key]);
        if(part&&part.version===params.version)out.set('ifVersion',part.version);
        return out;
      }
      if(native()&&!page&&(state?.hasCachedSession||this.scopes.get(scope)?.nativeHasHistory)){
        out.probe=true;out.set('indexOnly','1');out.scope=scope;out.source=source;
      }
      let keys=(page?.index||[]).map(row=>row.key);
      if(mode==='range'&&!page){
        const ranges=await this.disk.list('page',scope);
        const anchors=ranges.flatMap(r=>r.value.index||[]);
        const a=anchors.find(row=>row.key===params.start),b=anchors.find(row=>row.key===params.end);
        if(a&&b)keys=anchors.filter(row=>row.order>=a.order&&row.order<=b.order).map(row=>row.key);
      }else if(mode==='process'){
        const pages=await this.disk.list('page',scope),group=new Map();
        for(const [key,value] of this.resident.get(scope)?.pages||[])pages.push({key,value});
        for(const r of pages){
          const [name,...pairs]=JSON.parse(r.key);
          if(name==='process'&&Object.fromEntries(pairs).key===params.key){
            for(const row of r.value.index)group.set(row.key,row.order);
          }
        }
        if(!params.before&&(!params.after||params.after==='latest')){
          // The opening direction determines which known keys fit in the cap.
          const direction=params.after==='latest'?-1:1;
          keys=[...group].sort((a,b)=>direction*(a[1]-b[1])).map(([key])=>key);
        }else{
          keys.push(...group.keys());
          if(!page&&group.size>100){
            out.probe=true;out.lookup=scope;out.set('indexOnly','1');
            return out;
          }
        }
      }else if(mode!=='range')keys.push(...(hot||[]));
      keys=[...new Set(keys)];
      // Primary messages and individual steps carry text. Group headers carry only an index.
      keys=[...keys.filter(key=>!key.startsWith('process-')),...keys.filter(key=>key.startsWith('process-'))].slice(0,100);
      const rows=await this.disk.get(scope,'row',keys),versions=new Map();
      out.resident=new Map(rows.filter(Boolean).map(row=>[row.key,row]));
      for(const row of rows){
        if(!row||out.bans.has(row.turnId))continue;
        versions.set(row.key,row.version);out.append('known',row.key+':'+row.version);
      }
      if(page?.version&&!out.bans.size){
        const overflow=page.index.filter(item=>!versions.has(item.key));
        const extra=await this.disk.get(scope,'row',overflow.map(item=>item.key));
        for(const row of extra)if(row){versions.set(row.key,row.version);out.resident.set(row.key,row);}
        if(page.index.every(item=>versions.get(item.key)===item.version))out.set('ifVersion',page.version);
      }
      if(out.toString().length>6500&&['recent','older','range','process'].includes(mode)){
        out.probe=true;out.lookup=scope;out.delete('omitTurn');out.delete('known');out.delete('ifVersion');
        out.set('indexOnly','1');
      }
      return out;
    }
    async afterProbe(known,page){
      const scope=known.scope;
      if(scope){
        if(!this.active(scope)||known.generation!==this.generation||known.epoch!==this.epoch)return null;
        const state=await this.state(scope);
        if(!state||state.clearRevision!==known.clearRevision||await this.revision(scope)!==known.revision)return null;
        const sessionId=this.scopes.get(scope).sessionId;
        await this.event('merge',{sessionId,source:known.source,clearRevision:state.clearRevision,complete:false,
          syncedAt:Date.now(),turns:[],deletedKeys:[]});
        const keys=page.index.map(item=>item.key),rows=[];
        for(let first=0;first<keys.length;first+=100){
          const seed=await this.event('read',{sessionId,keys:keys.slice(first,first+100)});
          for(const turn of seed?.turns||[])for(const m of turn.messages||[]){
            if(m.row?.key===m.key&&m.row?.version===m.version&&!known.bans.has(turn.id))
              rows.push(record(scope,'row',m.key,safeRow(m.row),turn.id));
          }
        }
        if(!this.active(scope)||known.generation!==this.generation||!await this.disk.change(scope,rows,known.revision))return null;
      }
      const out=new URLSearchParams(known);
      out.delete('indexOnly');out.delete('ifVersion');
      for(const turn of new Set((page.index||[]).map(row=>row.turnId))){
        if(known.source!=='manual'&&known.bans.has(turn))out.append('omitTurn',turn);
      }
      if(scope||known.lookup){
        out.delete('known');
        const ordered=[...page.index.filter(r=>!r.key.startsWith('process-')),...page.index.filter(r=>r.key.startsWith('process-'))];
        const rows=await this.disk.get(scope||known.lookup,'row',ordered.map(row=>row.key));
        if(known.network){
          const memory=this.memory(scope||known.lookup);
          for(let i=0;i<rows.length;i++){
            const row=memory.rows.get(ordered[i].key);
            if(row?.version===ordered[i].version)rows[i]=row;
          }
        }
        out.resident=new Map(rows.filter(Boolean).map(row=>[row.key,row]));
        let held=0;
        for(let i=0;i<rows.length;i++){
          const row=rows[i];
          if(row?.version!==ordered[i].version||known.bans.has(row.turnId))continue;
          if(held++<100)out.append('known',row.key+':'+row.version);
        }
        // Only claim a complete page after validating every stored row.
        if(held===page.index.length&&held&&!known.bans.size)out.set('ifVersion',page.version);
      }
      Object.assign(out,{generation:known.generation,epoch:known.epoch,revision:known.revision,clearRevision:known.clearRevision,bans:known.bans,held:known.held});
      return out;
    }
    async read(scope,mode,params={}){
      if(!this.active(scope))return null;
      const gen=this.generation,epoch=this.epoch;
      if(!this.permitted(scope))return null;
      const state=await this.state(scope);
      if(isPhone()&&!state)return null;
      const key=pageKey(mode,params);
      const [removed,saved]=await this.disk.readRefs(scope,[['local','removed'],[mode==='body'?'chunk':'page',key]]);
      const bans=new Set([...(removed||[]),...(state?.tombstones?.[this.scopes.get(scope)?.sessionId]||[])]);
      if(mode==='body'){
        let part=saved;
        if(!part&&native()&&params.version){
          const row=(await this.disk.get(scope,'row',[params.key]))[0];
          if(row&&!bans.has(row.turnId)){
            const seed=await this.event('read',{sessionId:this.scopes.get(scope).sessionId,body:fields(params,['key','version','offset'])}).catch(()=>null);
            if(seed?.body?.key===params.key&&seed.body.version===params.version&&seed.body.offset===params.offset)
              part={...seed.body,turnId:row.turnId};
          }
        }
        return this.active(scope)&&this.generation===gen&&this.epoch===epoch&&part&&(!params.version||part.version===params.version)&&!bans.has(part.turnId)?part:null;
      }
      const page=saved;if(!page)return null;
      const rows=await this.disk.get(scope,'row',page.index.map(item=>item.key));
      if(!this.active(scope)||this.generation!==gen||this.epoch!==epoch||page.index.some((item,i)=>rows[i]?.version!==item.version||bans.has(item.turnId)))return null;
      return {...page,rows:rows.map((row,i)=>({...row,...page.index[i]})),items:mode==='process'?rows:undefined};
    }
    write(scope,mode,params,wire,options={}){
      const prior=this.pending.get(scope)||Promise.resolve();
      const early=mode==='recent'&&options.source!=='prefetch'&&options.validation&&!options.validation.network
        ?this.preview(scope,wire,options).then(page=>page||job):null;
      const job=prior.catch(()=>{}).then(()=>this.commit(scope,mode,params,wire,options)).finally(()=>{
        if(this.pending.get(scope)===job)this.pending.delete(scope);
      });
      this.pending.set(scope,job);
      return early?Promise.race([early,job]):job;
    }
    async preview(scope,wire,{validation,source='manual'}){
      const gen=this.generation;
      if(!this.active(scope)||validation.generation!==gen||validation.epoch!==this.epoch||wire.deleted?.length)return null;
      const state=await this.state(scope),[revision,removed]=await this.disk.get(scope,'local',['revision','removed']);
      if(validation.revision!==(revision||0)||isPhone()&&(!state||validation.clearRevision!==state.clearRevision))return null;
      const sessionId=this.scopes.get(scope)?.sessionId;
      const bans=new Set([...(removed||[]),...(state?.tombstones?.[sessionId]||[])]);
      const updated=new Map((wire.rows||wire.items||[]).map(row=>[row.key,row])),rows=[],index=[];
      for(const item of wire.index||[]){
        if(source!=='manual'&&bans.has(item.turnId))continue;
        const content=updated.get(item.key)||validation.resident?.get(item.key);
        if(content?.version!==item.version)return null;
        const row={...content,...item};
        if(row.textRef){
          if(!validation.held?.has(row.textRef))return null;
          row.text=validation.held.get(row.textRef);delete row.textRef;
        }
        rows.push(row);index.push(item);
      }
      if(!Array.isArray(wire.index)||!this.active(scope)||this.generation!==gen||validation.epoch!==this.epoch)return null;
      this.verified.add(scope);
      return {...wire,index,rows};
    }
    async commit(scope,mode,params,wire,{validation,source='manual',title}){
      const discard={discarded:true,index:[],rows:[],items:[],files:[],turns:[],meta:{}};
      if(!this.active(scope)||validation&&(validation.generation!==this.generation||validation.epoch!==this.epoch))return discard;
      if(validation?.network&&isPhone()&&validation.clearRevision===undefined){
        const binding=await validation.nativeBinding;
        if(binding?.clearRevision===undefined)return discard;
        validation={...validation,clearRevision:binding.clearRevision};
      }
      if(!this.active(scope)||validation&&(validation.generation!==this.generation||validation.epoch!==this.epoch))return discard;
      const gen=this.generation,key=pageKey(mode,params),state=await this.state(scope);
      const [revision,removed]=await this.disk.get(scope,'local',['revision','removed']),rev=revision||0;
      if(validation&&(validation.network?validation.revision!==this.memory(scope).revision:validation.revision!==rev))return discard;
      if(validation&&isPhone()&&(validation.clearRevision===undefined||validation.clearRevision!==state?.clearRevision))return discard;
      if(isPhone()&&!state)return wire.notModified||wire.unchanged?.length?null:wire;
      const sessionId=this.scopes.get(scope).sessionId,bans=new Set([...(removed||[]),...(state?.tombstones?.[sessionId]||[])]);
      const options={source,legacyScope:this.scopes.get(scope)?.legacyScope,title:title||wire.session?.title||wire.meta?.title||'',sessionId,clearRevision:state?.clearRevision};
      if(mode==='body'){
        const [part]=await this.disk.get(scope,'chunk',[key]);
        if(wire.notModified)return part?.version===wire.version?part:null;
        const [owner]=await this.disk.get(scope,'row',[wire.key]);
        if(!owner||bans.has(owner.turnId))return wire;
        const data={...fields(wire,['key','version','offset','text','next','attachments','files']),turnId:owner.turnId};
        if(!await this.disk.change(scope,[record(scope,'chunk',pageKey(mode,{...params,offset:wire.offset}),data,owner.turnId,wire.key)],rev))return discard;
        if(native()&&gen===this.generation&&this.active(scope))await this.event('merge',{...options,syncedAt:Date.now(),complete:false,
          turns:[{id:owner.turnId,questionTime:0,messages:[{key:wire.key,version:wire.version,role:owner.role,text:owner.text||'',
            order:owner.order||0,row:safeRow(owner),bodyPart:fields(wire,['offset','text','next'])}]}],deletedKeys:[]}).catch(()=>{});
        return wire;
      }
      if(!Array.isArray(wire.index))return wire;
      const updated=new Map((wire.rows||wire.items||[]).map(row=>[row.key,row]));
      const keys=wire.index.map(item=>item.key);
      const ops=[],rows=[],turns=new Map(),turnIds=[...new Set(wire.index.map(row=>row.turnId).filter(Boolean))];
      const [old,[previous],oldTurns]=await Promise.all([this.disk.get(scope,'row',keys),this.disk.get(scope,'local',['hot']),this.disk.get(scope,'turn',turnIds)]);
      const time=new Map(turnIds.map((t,i)=>[t,oldTurns[i]?.questionTime||0]));
      for(const turn of wire.turns||[]){
        if(dateOf(turn.questionTime))time.set(turn.id,dateOf(turn.questionTime));
        else if(!time.get(turn.id)&&dateOf(turn.startedAt))time.set(turn.id,dateOf(turn.startedAt));
      }
      for(let i=0;i<wire.index.length;i++){
        const item=wire.index[i],fresh=updated.get(item.key),cached=old[i];
        if(source!=='manual'&&bans.has(item.turnId))continue;
        if(!fresh&&cached?.version!==item.version)return null;
        const row={...(fresh||cached),...item};
        if(fresh?.textRef){
          const held=validation?.held||this.scopes.get(scope)?.held;
          if(!held?.has(fresh.textRef))return null;
          row.text=held.get(fresh.textRef);delete row.textRef;
        }
        if(cached&&cached.version!==row.version)ops.push({field:'parent',value:row.key});
        rows.push(row);
        const body=safeRow(row);
        if(fresh||!cached||['model','effort','timestamp','turnStartedAt','turnCompletedAt','turnCompletionKnown','turnError','turnStatus'].some(field=>cached[field]!==body[field]))
          ops.push(record(scope,'row',row.key,body,row.turnId));
        if(!row.turnId)continue;
        const t=turns.get(row.turnId)||{id:row.turnId,questionTime:time.get(row.turnId)||0,messages:[]};
        if(row.role==='user'&&row.originalQuestion!==false&&row.interaction!=='response'&&!t.question){t.question=row.text;t.questionTime=dateOf(row.timestamp)||t.questionTime||dateOf(row.turnStartedAt);}
        if(row.role==='assistant'&&row.phase!=='commentary')t.answer=row.text;
        t.messages.push({key:row.key,version:row.version,role:row.role,text:row.text||'',order:row.order||0,row:safeRow(row)});
        turns.set(row.turnId,t);
      }
      for(const turn of turns.values())ops.push(record(scope,'turn',turn.id,{questionTime:turn.questionTime,day:dayOf(turn.questionTime)},turn.id));
      const deleted=new Set(wire.deleted||[]);
      if([...deleted].some(key=>key.startsWith('process-'))){
        const candidates=new Set(),kept=new Set(keys);
        for(const manifest of await this.disk.list('page',scope)){
          const [mode,...pairs]=JSON.parse(manifest.key);
          if(mode!=='process')continue;
          if(deleted.has(Object.fromEntries(pairs).key)){
            ops.push({remove:manifest.id});
            for(const entry of manifest.value.index)candidates.add(entry.key);
          }else for(const entry of manifest.value.index)kept.add(entry.key);
        }
        for(const key of candidates)if(!kept.has(key))deleted.add(key);
      }
      for(const key of deleted)ops.push({remove:id(scope,'row',key)},{field:'parent',value:key});
      const kept=new Set(rows.map(row=>row.key));
      const liveIndex=wire.index.filter(item=>kept.has(item.key)),manifest=safePage({...wire,index:liveIndex});
      ops.push(record(scope,'page',key,manifest),
        record(scope,'local','hot',[...new Set([...rows.map(row=>row.key),...(previous||[])])].slice(0,100)),
        record(scope,'catalog','session',{name:this.name,sessionId,legacyScope:this.scopes.get(scope)?.legacyScope}));
      if(source==='manual'&&bans.size)ops.push(record(scope,'local','removed',[...bans].filter(t=>!turns.has(t))));
      if(gen!==this.generation||!this.active(scope))return discard;
      let saved=true;
      try{
        if(!await this.disk.change(scope,ops,rev))return discard;
      }catch(error){
        // A full browser volume cannot prevent reading an already authorized response.
        if(error.name!=='QuotaExceededError')throw error;
        saved=false;
        try{sessionStorage.removeItem('bridge-cache-access:'+scope);}catch{}
      }
      if(native()){
        // An empty authenticated response also confirms access to this one session.
        await this.event('merge',{...options,syncedAt:Date.now(),complete:mode==='recent'&&!wire.hasMore,
          turns:[...turns.values()],deletedKeys:[...deleted]}).catch(()=>{});
      }
      if(gen!==this.generation||!this.active(scope))return discard;
      if(mode==='recent')delete this.scopes.get(scope).held;
      if(saved)this.confirm(scope);
      return {...wire,rows,...(mode==='process'?{items:rows}:{}),index:liveIndex,deleted:[...deleted]};
    }
    async inventory(){
      await Promise.allSettled([...this.pending.values()]);
      const sessions=await this.disk.list('catalog'),groups=new Map();
      for(const {scope,value} of sessions){
        const [gateway]=JSON.parse(scope),g=groups.get(gateway)||{id:gateway,name:value.name||gateway.slice(0,8),dates:new Set(),scopes:[]};
        const turns=await this.disk.list('turn',scope);
        if(!turns.length)continue;
        for(const row of turns)g.dates.add(row.value.day);
        g.scopes.push(scope);groups.set(gateway,g);
      }
      return [...groups.values()];
    }
    async erase(gateways,days){
      const turnsByScope={};
      for(const group of await this.inventory()){
        if(!gateways.has(group.id))continue;
        for(const scope of group.scopes){
          const rows=await this.disk.list('turn',scope),ids=rows.filter(row=>days===null||days.has(row.value.day)).map(row=>row.key);
          if(!ids.length)continue;
          // Compare and retry on concurrent removals from another tab.
          for(;;){
            const rev=await this.revision(scope),[old]=await this.disk.get(scope,'local',['removed']);
            const ops=await this.clearTurns(scope,ids);
            ops.push(record(scope,'local','removed',[...new Set([...(old||[]),...ids])]),record(scope,'local','revision',rev+1));
            if(await this.disk.change(scope,ops,rev))break;
          }
          turnsByScope[scope]=ids;
          const [catalog]=await this.disk.get(scope,'catalog',['session']);
          const legacy=this.scopes.get(scope)?.legacyScope||catalog?.legacyScope;if(legacy)await window.BridgeChatHistoryCache?.forget(legacy);
        }
      }
      this.cleared(turnsByScope);
    }
  }
  const cache=new SessionCache(new SyncDisk(isPhone()?null:globalThis.indexedDB));
  window.BridgeChatSyncCache=cache;
  window.addEventListener('offline',()=>cache.requireValidation());
  window.addEventListener('codexbridge-native-offline-state',()=>{void cache.nativeChanged().catch(()=>{});});
})();
