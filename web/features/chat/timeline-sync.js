'use strict';

// Sync pages have native order within one window. Rejoin windows by stable row
// identity so a changed process length cannot move older rows past a live turn.
function mergeChatSyncRows(loaded,incoming,{older=false,range=false}={}){
  const positions=new Map(loaded.map((row,index)=>[row.key,index]));
  const keys=new Set(incoming.map(row=>row.key));
  const retained=(start,end)=>loaded.slice(start,end).filter(row=>!keys.has(row.key));
  const merged=[];
  let boundary=0,pending=[];
  for(const row of incoming){
    const position=positions.get(row.key);
    if(position===undefined){pending.push(row);continue;}
    if(position>=boundary){
      merged.push(...retained(boundary,position));boundary=position+1;
    }
    merged.push(...pending,row);pending=[];
  }
  if(!boundary&&range&&loaded.length)return loaded;
  if(!boundary&&!older)merged.push(...loaded,...pending);
  else merged.push(...pending,...retained(boundary));
  return merged;
}

const chatSyncContextFields=['contextUsage','contextUsageStatus','lastPromptContextUsage'];

function chatSyncContextPreview(meta){
  return Object.fromEntries(chatSyncContextFields.filter(key=>Object.hasOwn(meta||{},key)).map(key=>[key,meta[key]]));
}

function carryChatSyncContext(current,previous){
  if(current.contextUsageStatus==='unsupported')return {...current,contextUsage:null};
  if(!previous||current.draftAccountIdentity&&previous.draftAccountIdentity&&current.draftAccountIdentity!==previous.draftAccountIdentity)return current;
  const next={...current};
  for(const key of chatSyncContextFields){
    if(key==='contextUsageStatus'&&current.contextUsage)continue;
    if(!Object.hasOwn(next,key)&&Object.hasOwn(previous,key))next[key]=previous[key];
  }
  if(next.contextUsageStatus==='unsupported')next.contextUsage=null;
  else if(!current.contextUsage&&previous.contextUsage&&(!Object.hasOwn(current,'contextUsage')||next.contextUsageStatus==='unavailable'||current.loadingHistory===true))
    next.contextUsage={...previous.contextUsage,cached:true};
  return next;
}

class ChatTimelineSync {
  constructor({target,initial,host}){
    Object.assign(this,{target,initial,host});
    this.abort=host.abort;this.rows=[];this.turns=new Map();this.processes=new Map();this.files=new Map();this.ranges=new Map();
    this.clearRevision=0;
    this.onClear=event=>{
      const turns=event.detail?.turnsByScope?.[target.scope];
      if(turns?.length)this.invalidate(turns);
    };
    window.addEventListener('bridge-cache-cleared',this.onClear);
  }
  active(){return !this.abort.signal.aborted&&!this.pageHidden&&this.host.active()&&window.BridgeChatSync.foreground()&&this.target.authorized?.()!==false;}
  dispose(){this.initial?.cancel();this.cancelWatch();this.wake?.();window.removeEventListener('bridge-cache-cleared',this.onClear);}
  unavailable(error){return [404,405,501].includes(error.status);}
  invalidate(turnIds){
    this.clearRevision++;this.cancelWatch();
    const removed=new Set(turnIds);
    this.rows=this.rows.filter(row=>!removed.has(row.turnId));
    for(const key of removed)this.turns.delete(key);
    for(const [key,group] of this.processes)if(removed.has(group.turnId))this.processes.delete(key);
    this.ranges.clear();this.deferred=null;this.recent=[];this.recentRange=null;this.version=null;
    this.needsRefill=true;this.hasMore=false;this.before=null;
    this.paint({history:true});this.host.syncNotice?.();this.wake?.();
  }
  async read(mode,params={},source='manual',signal){
    const revision=this.clearRevision;
    let page;
    try{page=await window.BridgeChatSync.read(this.target,mode,params,source,()=>this.active()&&revision===this.clearRevision,signal);}
    catch(error){if(mode==='body'&&error.code==='sync_cursor_changed'&&this.active())this.resume();throw error;}
    if(mode==='body'&&page&&page.version!==params.version&&revision===this.clearRevision&&this.active()){
      this.resume();throw Error(timelineText('消息内容已变化，请重试'));
    }
    return revision===this.clearRevision?page:null;
  }
  async start(source='manual'){
    if(this.starting||this.abort.signal.aborted)return this.starting;
    const {scope}=this.target,initial=this.initial,revision=this.clearRevision;
    let wire=false;
    if(scope&&!this.value&&!this.rows.length){
      const preview=initial?.preview||window.BridgeChatSyncCache?.localPreview(scope);
      preview?.then(page=>{
        if(page&&!wire&&this.active()&&revision===this.clearRevision&&(!initial?.preview||initial.mayPreview())){
          this.accept(page,{cached:true});this.recent=page.index||[];this.recentRange=page.range;
        }
      }).catch(()=>{});
    }
    this.starting=(async()=>{
      if(!this.active())return;
      const receipt=initial?await initial.promise:null,result=initial?.valid()?receipt:null;
      initial?.close();this.initial=null;
      if(initial?.removedTurns&&!this.abort.signal.aborted){
        wire=true;this.invalidate(initial.removedTurns);
        // Opening the chat explicitly requests its first screen. Reconcile the
        // cleanup before reading it again with the current deletion revision.
        this.needsRefill=false;
      }
      if(result?.error)throw result.error;
      const page=result?.page||await this.read('recent',{},source);
      if(!page||!this.active())return;
      if(!Array.isArray(page.index))throw Object.assign(Error('Session sync is unavailable'),{status:404});
      wire=page.index.length>0||!(page.meta||page.session)?.loadingHistory;this.enabled=true;
      await this.mergeRecent(page,source);
    })().finally(()=>{this.starting=null;window.BridgeSyncPrefetch?.settled(scope,this.target.focusToken);});
    return this.starting;
  }
  async mergeRecent(page,source){
    const index=page.index||[],recent=this.recent||[];
    const overlaps=(left,right)=>left.some(row=>right.some(other=>other.key===row.key));
    let joined=false;
    if(this.host.cached&&index.length){this.clearWindow();this.recent=[];this.recentRange=null;}
    else if(recent.length&&index.length&&!overlaps(recent,index)&&page.before){
      const bridge=await this.read('older',{before:page.before,limit:21},source);
      if(!this.active())return;
      if(bridge&&overlaps(recent,bridge.index||[])){
        this.accept(bridge,{history:true,range:true});this.keepRange(bridge.range);joined=true;
      }else if(this.host.following||this.host.cached){this.clearWindow();}
      else{this.deferred=page;this.host.syncNotice?.();return;}
    }
    if(this.recentRange&&recent.some(row=>!index.some(other=>other.key===row.key)))this.keepRange(this.recentRange);
    this.accept(page,{joined});this.recent=index;this.recentRange=page.range;
  }
  clearWindow(){this.rows=[];this.turns.clear();this.processes.clear();this.files.clear();this.ranges.clear();this.recentRange=null;this.before=null;this.hasMore=false;}
  keepRange(range){if(range?.start&&range?.end)this.ranges.set(range.start,range);}
  async readOlder(before,limit){
    const page=await this.read('older',{before,limit});
    if(!page||!this.active())return;
    this.accept(page,{history:true});this.keepRange(page.range);
  }
  async reconcile(){
    for(const [key,range] of this.ranges){
      const page=await this.read('range',{start:range.start,end:range.end,limit:100},'live');
      if(!page||!this.active())return;
      this.accept(page,{history:true,range:true});
      if(page.reset)this.ranges.delete(key);
    }
  }
  accept(page,{cached=false,history=false,range=false,joined=false}={}){
    if(cached&&!history){
      const current=this.value?.session||this.value?.meta;
      if(!current||current.loadingHistory&&!current.contextUsage&&current.contextUsageStatus!=='unsupported'){
        this.previewContext=chatSyncContextPreview(page.session||page.meta);
        if(this.previewContext.contextUsage||this.previewContext.lastPromptContextUsage)
          this.host.renderCachedContext?.(this.previewContext);
      }
    }
    if(!cached&&!history){
      const field=page.session?'session':'meta';
      const meta=page[field]||{};
      const account=page.draftAccountIdentity;
      const previous=this.value?.[field];
      const context=previous?.loadingHistory?carryChatSyncContext(previous,this.previewContext):previous||this.previewContext;
      const previousAccount=this.value?.draftAccountIdentity;
      this.value={...page,[field]:carryChatSyncContext(meta,account&&previousAccount&&account!==previousAccount?null:context)};
      this.version=page.sourceVersion||this.version;
    }
    const deleted=new Set(page.deleted||[]);
    // A local archive has its own range cursors. Its rows may still have the
    // same native identities, which are sufficient to preserve on-screen order.
    const input=(page.rows||[]).filter(row=>!deleted.has(row.key));
    if(this.target.offline||cached&&page.recordPage===1){
      // The local reader supplies process positions separately from its rows.
      const keys=new Set(input.map(row=>row.key));
      for(const turn of page.turns||[])for(const group of turn.processes||[]){
        if(!keys.has(group.key)&&!deleted.has(group.key)){
          input.push({...group,role:'activity',kind:'processPage',turnId:turn.id});keys.add(group.key);
        }
      }
      input.sort((a,b)=>a.order-b.order);
    }
    const prior=this.rows.filter(row=>!deleted.has(row.key));
    const overlap=input.some(row=>prior.some(previous=>previous.key===row.key));
    this.rows=mergeChatSyncRows(prior,input,{older:history,range});
    const activeKeys=new Set(this.rows.map(row=>row.key));
    for(const [key,group] of this.processes)if(!activeKeys.has(key))this.processes.delete(key);
    for(const turn of page.turns||[]){
      const previous=this.turns.get(turn.id);
      if(!history||!previous){
        const unknown=turn.completionKnown===false||input.some(row=>row.turnId===turn.id&&row.turnCompletionKnown===false);
        this.turns.set(turn.id,{...previous,...turn,completionKnown:unknown?false:turn.completionKnown});
      }
      for(const descriptor of turn.processes||[]){
        if(!activeKeys.has(descriptor.key))continue;
        const group=this.processes.get(descriptor.key)||{rows:[],turnId:turn.id};
        // Range refresh does not supersede a process on the current recent page.
        if(!history||!group.cursor||!this.recent?.some(row=>row.key===descriptor.key)){
          group.cursor=descriptor.processCursor;
          group.version=descriptor.version;
        }
        if(!cached&&!history&&group.recovering&&group.recoveredAt!==(page.sourceVersion||descriptor.version)){
          group.recoveredAt=page.sourceVersion||descriptor.version;
          group.recovering=false;group.error=null;
        }
        this.processes.set(descriptor.key,group);
      }
    }
    for(const file of page.files||[])this.files.set(file.id,file);
    if(!range&&!joined&&(history||!this.before||!overlap)){
      this.before=page.before;this.hasMore=!!page.hasMore;
    }
    this.paint({cached,history});
    if(!cached){
      this.refreshRunning();
      for(const [key,group] of this.processes)if(group.open)void this.fillProcess(key);
    }
  }
  project(){
    const rows=[];
    const append=row=>{
      const turn=this.turns.get(row.turnId);
      rows.push({...row,id:row.id??row.key,order:rows.length,truncated:row.truncated??row.hasDetail,
        turnStatus:turn?.status??row.turnStatus,turnStartedAt:turn?.startedAt??row.turnStartedAt,
        turnCompletedAt:turn?.completedAt??row.turnCompletedAt,turnError:turn?.error??row.turnError,
        turnCompletionKnown:turn?.completionKnown??row.turnCompletionKnown});
    };
    for(const row of this.rows){
      if(row.kind!=='processPage'){append(row);continue;}
      const group=this.processes.get(row.key);
      if(!group)continue;
      const marker={...row,role:'activity',kind:'syncProcessPage',id:undefined,processKey:row.key};
      const filling=group.open&&!group.error&&(group.loading||group.filling)&&!!(group.previous||group.next);
      append({...marker,key:'process-earlier:'+row.key,pageDirection:'earlier',pageVisible:!!group.previous&&!filling});
      for(const step of group.rows)append(step);
      append({...marker,key:'process-later:'+row.key,pageDirection:'later',pageVisible:!!group.next||!!group.error||!!filling,pageError:!!group.error,pageLoading:!!filling});
    }
    return rows;
  }
  paint(options={}){
    this.host.renderSync({rows:this.project(),files:[...this.files.values()],before:this.before,hasMore:this.hasMore,value:this.value,...options});
  }
  refreshRunning(){
    for(const [key,group] of this.processes){
      const turn=this.turns.get(group.turnId),status=turn?.completionKnown===false&&turn.status==='completed'?undefined:turn?.status;
      if(status==='inProgress')group.open=true;
      if((status==='inProgress'||chatTurnStopped(status)&&group.runningWindow)&&group.loadedVersion!==group.version){
        this.readProcess(key,'recent',this.target.offline?'manual':'live');
      }else if(status!=='inProgress'&&!group.loadedVersion&&(status!=='completed'||!this.rows.some(row=>
        row.turnId===group.turnId&&chatFinalAnswer(row)))){
        this.readProcess(key,'recent','manual');
      }
    }
  }
  async readProcess(key,direction,source='manual'){
    const group=this.processes.get(key);
    if(!group?.cursor||group.loading||group.recovering&&group.error||this.host.cached&&!this.target.offline||!this.active())return;
    const loading=group.loading={},version=group.version;
    const status=this.turns.get(group.turnId)?.status;
    const query={key:group.cursor,...(direction==='earlier'?{before:group.previous}:direction==='later'?{after:group.next}:
      status==='inProgress'||chatTurnStopped(status)&&group.runningWindow?{after:'latest'}:{})};
    try{
      const page=await this.read('process',query,source);
      if(!page||!this.active()||group.loading!==loading||group.version!==version||this.processes.get(key)!==group)return;
      const deleted=new Set(page.deleted||[]);
      let incoming=page.rows||[];
      if(this.target.offline){
        const retained=new Map(group.rows.map(row=>[row.key,row]));
        incoming=incoming.map(row=>{
          const current=retained.get(row.key);
          return current&&current.version!==row.version?current:row;
        });
      }
      group.rows=mergeChatSyncRows(group.rows.filter(row=>!deleted.has(row.key)),incoming,{older:direction==='earlier'});
      if(direction==='recent'||direction==='earlier')group.previous=page.previous;
      if(direction==='recent'||direction==='later')group.next=page.next;
      for(const file of page.files||[])this.files.set(file.id,file);
      group.loadedVersion=version;group.runningWindow||=query.after==='latest';group.error=null;
      this.paint({process:true});
    }catch(error){
      if(this.active()&&group.loading===loading){
        group.error=direction;
        if(error.code==='sync_cursor_changed'){
          group.previous=null;group.next=null;group.loadedVersion=null;group.recovering=true;
          if(group.recoveredAt!==(this.version||group.version)){
            this.needsRefresh=true;this.cancelWatch();this.wake?.();
          }
        }
        this.paint({process:true});
      }
    }finally{
      if(group.loading===loading)group.loading=null;
      if(group.open&&!group.filling&&!group.error)void this.fillProcess(key);
    }
  }
  async fillProcess(key){
    const group=this.processes.get(key);
    if(!group||group.filling||group.loading||group.error)return;
    const filling=group.filling={};
    try{
      while(group.open&&group.filling===filling&&this.processes.get(key)===group&&this.active()&&(!this.host.cached||this.target.offline)){
        const direction=group.loadedVersion!==group.version?'recent':group.previous?'earlier':group.next?'later':null;
        if(!direction||group.error||group.loading)break;
        const before=JSON.stringify([group.loadedVersion,group.previous,group.next]);
        await this.readProcess(key,direction);
        if(before===JSON.stringify([group.loadedVersion,group.previous,group.next]))break;
      }
    }finally{if(group.filling===filling)group.filling=null;}
  }
  processAction(key,direction){const group=this.processes.get(key);if(group){group.open=true;group.recovering=false;group.recoveredAt=null;this.readProcess(key,group.error||direction);}}
  disclosure(turnId,open){
    const status=this.turns.get(turnId)?.status;
    let reset=false;
    for(const [key,group] of this.processes){
      if(group.turnId!==turnId)continue;
      group.open=open;
      if(!open)group.filling=null;
      if(!open&&status==='completed'&&(group.runningWindow||group.loading)){
        group.rows=[];group.previous=null;group.next=null;group.runningWindow=false;group.loadedVersion=null;group.loading=null;
        reset=true;
      }
      if(open)void this.fillProcess(key);
    }
    if(reset)queueMicrotask(()=>{if(!this.abort.signal.aborted)this.paint({process:true});});
  }
  latest(){
    if(this.needsRefill){
      this.needsRefill=false;
      this.start('manual').catch(error=>{if(this.active()){this.needsRefill=true;this.host.syncNotice?.();this.host.status(error.message);}});
    }
    if(this.deferred){
      const page=this.deferred;this.deferred=null;this.clearWindow();this.accept(page);
      this.recent=page.index||[];this.recentRange=page.range;
    }
  }
  cancelWatch(){this.watch?.abort();}
  resume(){
    this.needsRefresh=true;
    for(const group of this.processes.values())if(group.open&&group.error){
      group.error=null;group.loadedVersion=null;group.previous=null;group.next=null;group.recoveredAt=null;
    }
    this.cancelWatch();this.wake?.();
  }
  pause(ms){return new Promise(resolve=>{const timer=setTimeout(()=>this.wake(),ms);this.wake=()=>{clearTimeout(timer);this.wake=null;resolve();};});}
  async updates(){
    if(this.loop||this.abort.signal.aborted)return;
    this.loop=true;
    let wasActive=this.active();
    const lifecycle=event=>{
      if(event.type==='beforeunload'||event.type==='pagehide')this.pageHidden=true;
      if(event.type==='pageshow')this.pageHidden=false;
      const active=this.active();
      if(!active)this.cancelWatch();
      else if(!wasActive||event.type==='online'||event.type==='pageshow')this.resume();
      wasActive=active;
      this.wake?.();
    };
    const events=['bridge-chat-foreground','offline','online','beforeunload','pagehide','pageshow'];
    for(const event of events)window.addEventListener(event,lifecycle);
    document.addEventListener('visibilitychange',lifecycle);
    try{
      while(!this.abort.signal.aborted){
        if(!this.active()||this.needsRefill||this.target.offline||window.OfflineChatReader?.enabled){await this.pause(350);continue;}
        const meta=this.value?.session||this.value?.meta;
        const live=meta?.status==='active'||meta?.requests?.length;
        let delay=live?500:2400;
        try{
          this.refreshRunning();
          if(!this.enabled||this.needsRefresh){
            this.needsRefresh=false;await this.start(this.enabled?'live':'manual');
            if(this.active())await this.reconcile();
          }else{
            const params=this.version?{ifVersion:this.version}:{},start=Date.now();
            if(!live&&this.version&&this.waitSupported!==false)params.waitMs=8000;
            const watch=this.watch=new AbortController();
            let update;
            try{update=await this.read('watch',params,'live',watch.signal);}
            catch(error){
              if(watch.signal.aborted)continue;
              if(params.waitMs&&[400,422].includes(error.status)){this.waitSupported=false;continue;}
              throw error;
            }finally{this.watch=null;}
            if(!watch.signal.aborted&&this.active()&&update&&!update.notModified&&update.version!==this.version){
              const page=await this.read('recent',{},'live');
              if(page&&this.active()){
                await this.mergeRecent(page,'live');
                if(this.active())await this.reconcile();
                this.version=page.sourceVersion||update.version;
              }
            }
            if(params.waitMs){this.waitSupported=update?.waitSupported===true;if(this.waitSupported)delay=Math.max(0,250-(Date.now()-start));}
          }
        }catch(error){
          if(this.abort.signal.aborted)return;
          if(this.unavailable(error)){this.host.syncUnavailable?.();return;}
          if(this.active())this.host.status(timelineText('重新连接中'));
          delay=3000;
        }
        if(delay)await this.pause(delay);
      }
    }finally{
      this.loop=false;
      for(const event of events)window.removeEventListener(event,lifecycle);
      document.removeEventListener('visibilitychange',lifecycle);
    }
  }
}
