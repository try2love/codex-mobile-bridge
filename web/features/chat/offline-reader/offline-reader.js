'use strict';
(() => {
  const providers={codex:'Codex',claude:'Claude',deepseek:'DeepSeek',zcode:'ZCode',antigravity:'Antigravity'};
  const pending=new Map(),catalog=new Map();
  let serial=0,selected=null,listing,hot=null,composerWritable;
  const reads=new Map();
  const cold=globalThis.document?.documentElement?.hasAttribute?.('data-bridge-offline')===true;
  function host(action,fields={}){
    if(hot)return hot(action,fields);
    return new Promise((resolve,reject)=>{
      const id=String(++serial),message={id,action,...fields};
      const timer=setTimeout(()=>{pending.delete(id);reject(Error('Offline reader unavailable'));},10000);
      pending.set(id,{resolve,reject,timer});
      try{
        if(window.OfflineChatHost?.postMessage)window.OfflineChatHost.postMessage(JSON.stringify(message));
        else window.webkit.messageHandlers.offlineChat.postMessage(message);
      }catch(error){clearTimeout(timer);pending.delete(id);reject(error);}
    });
  }
  function receive(id,value){
    const task=pending.get(String(id));if(!task)return;
    pending.delete(String(id));clearTimeout(task.timer);
    try{
      const reply=typeof value==='string'?JSON.parse(value):value;
      if(!reply||reply.error)throw Error(reply?.error||'Offline reader unavailable');
      task.resolve(reply);
    }catch(error){task.reject(error);}
  }
  function list(refresh=false){
    if(refresh)listing=null;
    return listing||=(host('list').then(result=>{
      composerWritable=result.composerWritable;
      if(cold){
        if(result.nativeTheme)window.BridgeNativeTheme=result.nativeTheme;
        if(result.appearance){
          const appearance={...result.appearance,theme:result.nativeTheme?.theme||result.appearance.theme};
          try{localStorage.setItem('bridge-appearance',JSON.stringify(appearance));}catch{}
          document.dispatchEvent(new CustomEvent('bridge-appearance-restored',{detail:appearance}));
        }else if(result.nativeTheme)document.dispatchEvent(new Event('bridge-native-theme'));
      }
      catalog.clear();
      for(const [index,session] of (result.sessions||[]).entries()){
        if(typeof session.id!=='string'||!session.id)continue;
        let tuple;
        try{tuple=JSON.parse(session.id);}catch{}
        const entry=typeof tuple?.[0]==='string'?tuple[0]:'codex';
        const family=Object.hasOwn(providers,entry.split(':')[0])?entry.split(':')[0]:'codex';
        const provider=family==='codex'?'codex':entry;
        const id=typeof tuple?.[2]==='string'&&tuple[2]?tuple[2]:'cached-'+index;
        const hostId=typeof tuple?.[1]==='string'&&tuple[1]?tuple[1]:'local';
        catalog.set(JSON.stringify([provider,hostId,id]),{...session,archiveId:session.id,id,provider,family,host:hostId,title:session.title||'Chat',
          cwd:'',projectKey:'local|unassigned',projectName:'',source:'desktop',recency:Number(session.syncedAt)||0});
      }
      document.dispatchEvent(new CustomEvent('bridge-offline-list',{detail:{syncedAt:(result.sessions||[]).reduce((latest,row)=>Math.max(latest,Number(row.syncedAt)||0),0)}}));
      return result;
    }).catch(error=>{listing=null;throw error;}));
  }
  function raster(value){
    return typeof value==='string'&&/^data:image\/(?:png|jpeg|gif|webp);base64,[a-z0-9+/]+={0,2}$/i.test(value)?value:'';
  }
  function body(entry,row){
    const preview=entry.text??row.text??'',parts=entry.bodyParts;
    if(!parts)return {text:preview,partial:row.truncated===true};
    let offset=0,value='';
    while(Object.hasOwn(parts,offset)){
      const part=parts[offset];if(typeof part?.text!=='string')break;
      value+=part.text;
      if(part.next===null)return {text:value,partial:false};
      if(!Number.isSafeInteger(part.next)||part.next<=offset)break;
      offset=part.next;
    }
    return {text:value||preview,partial:true};
  }
  function appendTurns(state,turns){
    const fresh=[];
    for(const turn of turns||[]){
      if(state.turns.has(turn.id))continue;
      const content=turn.content||{},sorted=Object.entries(content.messages||{}).sort((a,b)=>(a[1].order||0)-(b[1].order||0));
      if(!sorted.length)for(const [field,role] of [['question','user'],['answer','assistant']])
        if(typeof content[field]==='string'&&content[field])sorted.push([turn.id+':'+field,{role,text:content[field]}]);
      const entries=sorted.map(([key,entry])=>{
        const row=entry.row||{},value=body(entry,row);
        const messageKey=row.key||entry.key||key;
        const valueRow={...row,key:messageKey,id:messageKey,turnId:turn.id,role:entry.role||row.role,
          text:value.text,version:entry.version||row.version||messageKey,
          offlinePartial:value.partial,
          phase:row.phase||(row.role==='assistant'&&row.forkable===true?'final_answer':undefined),
          truncated:false,editable:false,forkable:false,
          attachments:(entry.attachments||row.attachments||[]).map(item=>({...item,offlineImage:raster(item.dataUrl)||raster(item.previewData)}))};
        state.messages.set(messageKey,{row:valueRow,parts:entry.bodyParts});
        if(value.partial)state.meta.offlineGaps=true;
        return valueRow;
      });
      const rows=[],processes=[];
      let process;
      for(const row of entries){
        if(row.kind==='processPage'||row.phase==='commentary'||row.role==='user'&&row.interaction==='response'||!['assistant','user','error','fileChanges'].includes(row.role)){
          if(!process){
            const key=row.kind==='processPage'?row.key:'offline-process:'+row.key;
            process={key,version:row.version,processCursor:row.kind==='processPage'?(row.processCursor||key):key,rows:[]};
            processes.push(process);rows.push({process});
          }
          if(row.kind!=='processPage')process.rows.push(row);
        }else{process=null;rows.push(row);}
      }
      const last=[...entries].reverse().find(row=>row.turnStatus&&row.kind!=='processPage')||{};
      const turnState={id:turn.id,status:last.turnStatus,completionKnown:last.turnCompletionKnown,
        startedAt:last.turnStartedAt,completedAt:last.turnCompletedAt,error:last.turnError,processes};
      state.turns.set(turn.id,turnState);fresh.push({rows,turn:turnState});
    }
    return fresh;
  }
  function present(state,items,prepend){
    const rows=[],index=[],turns=[];
    let order=prepend?state.first-items.reduce((size,item)=>size+item.rows.length,0):0;
    if(prepend)state.first=order;else state.first=0;
    for(const item of items){
      for(const row of item.rows){
        if(row.process){row.process.order=order++;state.groups.set(row.process.processCursor,row.process);state.groups.set(row.process.key,row.process);continue;}
        rows.push({...row,order:order++});
      }
      turns.push(item.turn);
    }
    index.push(...rows);
    return {rows,index,turns,readOnly:true,connected:false,before:state.before,hasMore:!!state.before,files:[],meta:state.meta,session:state.meta};
  }
  function recordRow(row){
    return {...row,id:row.id||row.key,editable:false,forkable:false,detailCursor:row.detailCursor||(row.truncated?row.key:undefined),
      phase:row.phase||(row.role==='assistant'&&row.forkable===true?'final_answer':undefined),
      attachments:(row.attachments||[]).map(item=>({...item,offlineImage:raster(item.dataUrl)||raster(item.previewData)}))};
  }
  function projectPage(payload,state,prepend=false){
    if(payload?.recordPage!==1)return null;
    const entries=[...(payload.rows||[]),...(payload.turns||[]).flatMap(turn=>turn.processes||[])];
    const first=prepend?(state?.first||0)-entries.length:0;
    if(state)state.first=first;
    const rows=(payload.rows||[]).map(row=>recordRow({...row,order:first+row.order}));
    const turns=(payload.turns||[]).map(turn=>({...turn,processes:(turn.processes||[]).map(group=>({...group,order:first+group.order}))}));
    if(state){
      for(const row of rows)state.messages.set(row.key,{row});
      for(const turn of turns){
        state.turns.set(turn.id,turn);
        for(const group of turn.processes)state.groups.set(group.key,group);
      }
    }
    const meta=state?.meta||{title:payload.title,readOnly:true,connected:false,requests:[],offlineSync:payload.syncedAt,offlineGaps:payload.gaps===true};
    return {...payload,rows,index:rows,turns,files:[],readOnly:true,connected:false,meta,session:meta};
  }
  async function firstPage(archiveId){
    for(let attempt=0;attempt<30;attempt++){
      const page=await host('session',{sessionId:archiveId});
      if(page?.recordPage!==1||!page.migrating||page.rows?.length||page.turns?.some(turn=>turn.processes?.length))return page;
      await new Promise(resolve=>setTimeout(resolve,150));
    }
    throw Error('离线记录正在转换，请稍后重试。');
  }
  async function session(item,mode,params){
    if(mode==='recent'){
      const payload=await firstPage(item.archiveId);
      if(payload.id!==item.archiveId)throw Error('Offline session mismatch');
      const meta={id:item.id,title:payload.title||item.title,host:item.host,provider:item.provider,cwd:item.cwd||'',status:'idle',connected:false,readOnly:true,
        access:{source:'history'},capabilities:{},requests:[],offlineSync:payload.syncedAt,offlineGaps:payload.gaps===true};
      const reading={id:item.id,meta,groups:new Map(),messages:new Map(),turns:new Map(),before:payload.before};
      reads.set(item.archiveId,reading);
      selected=item.archiveId;
      if(payload.recordPage===1){reading.native=true;return projectPage(payload,reading);}
      return present(reading,appendTurns(reading,payload.turns),false);
    }
    const state=reads.get(item.archiveId);
    if(!state)throw Error('Offline session changed');
    if(mode==='older'){
      const payload=await host('session',{sessionId:item.archiveId,before:params.get('before')});
      if(payload.id!==item.archiveId)throw Error('Offline session mismatch');
      state.before=payload.before;
      if(payload.recordPage===1)return projectPage(payload,state,true);
      return present(state,appendTurns(state,payload.turns),true);
    }
    if(mode==='process'){
      const group=archived(state.groups,params.get('key'));
      if(!group)throw Error('此过程未保存到本机，请联网上看。');
      if(state.native){
        const after=params.get('after');
        const page=await host('session',{sessionId:item.archiveId,mode:'process',key:group.key,
          ...(after&&after!=='latest'?{after}:{})});
        if(page?.recordPage!==1)throw Error('此过程未保存到本机，请联网上看。');
        const rows=page.rows.map(recordRow);
        for(const row of rows)state.messages.set(row.key,{row});
        return {...page,rows,index:rows,items:rows,files:[],hasEarlier:false,hasLater:page.hasMore};
      }
      const rows=(group?.rows||[]).map((row,order)=>({...row,order}));
      return {rows,index:rows,hasMore:false,hasEarlier:false,files:[]};
    }
    if(mode==='body'){
      const saved=archived(state.messages,params.get('key')),offset=Number(params.get('offset')||0);
      if(!saved||saved.row.version!==params.get('version')||!Number.isSafeInteger(offset)||offset<0)
        throw Error('此段正文未保存到本机，请联网上看。');
      if(state.native){
        const part=await host('session',{sessionId:item.archiveId,mode:'body',key:saved.row.key,version:saved.row.version,offset});
        if(!part||part.version!==saved.row.version||part.offset!==offset)throw Error('此段正文未保存到本机，请联网上看。');
        return {...part,files:[],attachments:saved.row.attachments};
      }
      const part=saved.parts?.[offset]||(offset===0&&!saved.row.offlinePartial?{offset:0,text:saved.row.text,next:null}:null);
      if(!part)throw Error('此段正文未保存到本机，请联网上看。');
      return {...part,key:saved.row.key,version:saved.row.version,files:[],attachments:saved.row.attachments};
    }
    throw Error('Offline read unavailable');
  }
  function archived(records,cursor){
    if(typeof cursor!=='string')return;
    return records.get(cursor)||records.get(cursor.slice(cursor.indexOf('.')+1));
  }
  async function request(path,write){
    const url=new URL(path,'https://offline.invalid');
    if(write!==undefined)throw Error('Offline · Read only');
    const result=await list(),p=url.pathname,q=url.searchParams;
    const clients=[...new Map([...catalog.values()].map(row=>[row.provider,row.family]))]
      .map(([id,family])=>({id,name:providers[family],enabled:true,installed:true,available:true,selectable:true,connected:true}));
    if(p==='/api/auth')return {authenticated:true,csrf:'offline',uiLanguage:result.lang,computer:{name:result.name},enabledClients:clients.map(row=>row.id)};
    if(p==='/api/clients')return {clients};
    const codex=/^\/api\/sessions\/([^/]+)\/sync$/.exec(p),desktop=/^\/api\/desktop-sessions\/([^/]+)\/(list|sync)$/.exec(p);
    if(codex||desktop?.[2]==='sync'){
      const provider=q.get('entry')||desktop?.[1]||'codex';
      const id=codex?decodeURIComponent(codex[1]):q.get('sessionId');
      const item=catalog.get(JSON.stringify([provider,q.get('host')||'local',id]))||
        (desktop&&[...catalog.values()].find(row=>row.provider===provider&&row.id===id));
      if(!item)throw Error('Offline session unavailable');
      return session(item,q.get('mode')||'recent',q);
    }
    if(p==='/api/sessions'||desktop?.[2]==='list'){
      const provider=q.get('entry')||desktop?.[1]||'codex',search=(q.get('q')||'').toLocaleLowerCase();
      const sessions=[...catalog.values()].filter(row=>row.provider===provider);
      const requestedHost=q.get('host')||'local';
      const matched=sessions.filter(row=>(desktop||row.host===requestedHost)&&q.get('archived')!=='true'&&
        row.title.toLocaleLowerCase().includes(search)).sort((a,b)=>b.recency-a.recency||b.id.localeCompare(a.id));
      return {sessions:desktop?matched:matched.slice(Number(q.get('offset'))||0,(Number(q.get('offset'))||0)+100),
        hosts:[],remoteHosts:[...new Set(sessions.map(row=>row.host))].filter(host=>host!=='local'),availableSources:['desktop'],
        connected:true,readOnly:true,complete:true};
    }
    return {};
  }
  function attachmentFields(item,turnId){
    if(!selected||!turnId)return null;
    const key=item.uploadId||item.desktopId||item.path;
    if(/^[0-9a-f]{64}$/.test(item.offlineVersion||''))return {sessionId:selected,turnId,version:item.offlineVersion};
    return key?{sessionId:selected,turnId,attachmentKey:key}:null;
  }
  async function openAttachment(item,turnId,target){
    const fields=attachmentFields(item,turnId);
    const name=item.offlineName||item.name||item.path?.split(/[\\/]/).pop()||'';
    const t=value=>typeof BridgeI18n==='undefined'?value:BridgeI18n.t(value);
    const unavailable=()=>Error(t('此附件未保存到本机，请联网上看。'));
    const stillHere=()=>fields&&fields.sessionId===selected&&(cold||hot)&&target.isConnected;
    const fallback=()=>item.offlineImage&&window.BridgeImageViewer?.openOffline(target,{url:item.offlineImage,name});
    if(!fields){if(fallback())return;throw unavailable();}
    if(!stillHere())return;
    const meta=await host('attachment',fields);
    if(!stillHere())return;
    if(!/^[0-9a-f]{64}$/.test(meta?.version||'')||!Number.isSafeInteger(meta.bytes)||meta.bytes<=0||meta.chunkBytes!==524288){
      if(fallback())return;
      throw unavailable();
    }
    if(!/^image\/(?:png|jpeg|gif|webp|bmp|avif)$/.test(meta.mediaType)){
      const result=await host('attachmentOpen',{sessionId:fields.sessionId,turnId,version:meta.version,name});
      if(!result?.opened)throw unavailable();
      return;
    }
    const parts=[];
    for(let index=0,offset=0;offset<meta.bytes;index++,offset+=meta.chunkBytes){
      const part=await host('attachment',{sessionId:fields.sessionId,turnId,version:meta.version,index});
      if(!stillHere())return;
      if(typeof part?.data!=='string')throw unavailable();
      const binary=atob(part.data), bytes=Uint8Array.from(binary,byte=>byte.charCodeAt(0));
      if(bytes.length!==Math.min(meta.chunkBytes,meta.bytes-offset))throw unavailable();
      parts.push(bytes);
    }
    const url=URL.createObjectURL(new Blob(parts,{type:meta.mediaType}));
    const release=()=>URL.revokeObjectURL(url);
    if(!window.BridgeImageViewer?.openOffline(target,{url,name,release}))release();
  }
  function back(){
    if(window.OfflineChatReader.backToList?.())return true;
    void host('close').catch(()=>{});return true;
  }
  function reconnect(){
    const anchor=window.OfflineChatReader.capture?.();
    return host('reconnect',selected?{sessionId:selected,...(anchor?{anchor}:{})}:{}).catch(error=>{
      document.dispatchEvent(new CustomEvent('bridge-offline-error',{detail:error.message}));
    });
  }
  function screen(readonly){
    const dataset=globalThis.document?.documentElement?.dataset;if(!dataset)return;
    if(readonly)dataset.offlineReader='true';
    else delete dataset.offlineReader;
  }
  function leaveSession(){
    if(hot)document.dispatchEvent(new Event('bridge-offline-leave'));
    selected=null;reads.clear();
    void list(true).catch(()=>{});
  }
  window.OfflineChatReader={receive,request,projectPage,back,reconnect,raster,leaveSession,openAttachment,
    composer(action,fields){
      if(!['composerRead','composerWrite','composerPending'].includes(action))return Promise.reject(Error('Unsupported composer action'));
      return host(action,fields);
    },
    enableHot(transport){hot=transport;listing=null;screen(true);},
    disableHot(){hot=null;listing=null;selected=null;reads.clear();screen(cold);},
    refreshList:()=>list(true),
    get composerWritable(){return composerWritable;},
    get cold(){return cold;},get enabled(){return cold||!!hot;}};
  screen(cold);
})();
