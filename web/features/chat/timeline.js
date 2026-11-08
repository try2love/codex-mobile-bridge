'use strict';
const timelineText=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
const fileChangeLabel=row=>timelineText('已编辑 {count} 个文件').replace('{count}',row.fileSummary?.count||0);
const recentTimelinePages=new Map();
function timelineFilePath(path,cwd){
  if(typeof path!=='string'||!path||typeof cwd!=='string'||!cwd||/[\0\r\n]/.test(path))throw Error(timelineText('文件路径无法定位'));
  const windows=/^(?:[A-Za-z]:[\\/]|\\\\|\/\/)/.test(cwd);
  const normalize=value=>windows?value.replace(/\\/g,'/'):value;
  path=normalize(path);cwd=normalize(cwd).replace(/\/+$/,'');
  const absolute=/^(?:\/|[A-Za-z]:\/)/.test(path);
  if(absolute){
    const prefix=cwd+'/',match=windows?path.toLowerCase().startsWith(prefix.toLowerCase()):path.startsWith(prefix);
    if(!match)throw Error(timelineText('此文件位于聊天项目之外，无法在当前文件标签页中定位。'));
    path=path.slice(prefix.length);
  }
  const parts=[];
  for(const part of path.split('/')){
    if(!part||part==='.')continue;
    if(part==='..'){if(!parts.length)throw Error(timelineText('文件路径无法定位'));parts.pop();}
    else parts.push(part);
  }
  if(!parts.length||parts.some(part=>part.includes('\\')||part.includes(':')))throw Error(timelineText('文件路径无法定位'));
  return parts.join('/');
}

// History and live changes share stable keys, but never share a request queue.
class ChatTimeline {
  static clearCache(){recentTimelinePages.clear();}
  constructor({url, request, renderMeta, renderText, status, onAction, onFile, onReady, canEdit = () => true, elements = {}, active = () => true, cacheKey = ''}) {
    Object.assign(this,{url,request,renderMeta,renderText,status,onAction,onFile,onReady,active,cacheKey});
    this.canEdit=()=>!this.cached&&canEdit();
    this.viewport=elements.viewport||document.getElementById('timeline');
    this.container=elements.container||document.getElementById('messages');
    this.older=elements.older||document.getElementById('older');
    this.newer=elements.newer||document.getElementById('newer');
    this.abort=new AbortController();this.rows=new Map();this.nodes=new Map();this.details=new Map();
    this.mermaidScope=window.BridgeMermaidCards?.createScope();
    this.files=new Map();this.sequence=-1;this.epoch='';this.before=null;this.hasMore=false;
    this.busy=false;this.generation=0;this.windowRevision=0;this.following=true;this.lastTop=0;this.retryCount=0;
    this.showReasoning=document.documentElement.dataset.showReasoning!=='false';
    this.showProcess=document.documentElement.dataset.showProcess!=='false';
    this.showToolImages=document.documentElement.dataset.showToolImages!=='false';
    this.onScroll=()=>{
      if(this.suppress||!this.viewport.clientHeight)return;
      const upward=this.viewport.scrollTop<this.lastTop;
      this.lastTop=this.viewport.scrollTop;this.following=this.atBottom();this.anchor=this.capture();
      if(this.following)this.newer.hidden=true;
      if(upward&&this.nearTop()&&!this.retryCount)this.loadOlder(100);
    };
    this.viewport.addEventListener('scroll',this.onScroll,{passive:true});
    // At the scroll boundary wheel/touch intent produces no scroll event.
    this.atHistoryEdge=()=>!this.suppress&&this.viewport.clientHeight&&this.viewport.scrollTop<=1&&!this.retryCount;
    this.onWheel=event=>{if(event.deltaY<0&&this.atHistoryEdge())this.loadOlder(100);};
    this.onTouchStart=event=>{this.touchY=event.touches.length===1?event.touches[0].clientY:null;};
    this.onTouchMove=event=>{if(this.touchY===null||event.touches.length!==1)return;const y=event.touches[0].clientY;if(y-this.touchY>12&&this.atHistoryEdge()){this.touchY=y;this.loadOlder(100);}};
    this.viewport.addEventListener('wheel',this.onWheel,{passive:true});
    this.viewport.addEventListener('touchstart',this.onTouchStart,{passive:true});
    this.viewport.addEventListener('touchmove',this.onTouchMove,{passive:true});
    this.older.onclick=()=>this.sequence<0||this.cached?this.start():this.loadOlder(this.retryCount||100);
    this.newer.onclick=()=>{this.jumpBottom();this.anchor=this.capture();};
    this.resize=new ResizeObserver(()=>this.restore(this.anchor,this.following));
    this.resize.observe(this.container);
  }
  dispose(){this.abort.abort();window.BridgeMermaidCards?.clearScope(this.mermaidScope);clearTimeout(this.startRetry);this.resize.disconnect();this.viewport.removeEventListener('scroll',this.onScroll);this.viewport.removeEventListener('wheel',this.onWheel);this.viewport.removeEventListener('touchstart',this.onTouchStart);this.viewport.removeEventListener('touchmove',this.onTouchMove);this.newer.hidden=true;}
  atBottom(){return chatFollowsLatest(this.viewport);}
  nearTop(){const nodes=[...this.container.children].filter(n=>!n.hidden);return nodes.length>0&&nodes.slice(0,11).some(n=>n.getBoundingClientRect().bottom>=this.viewport.getBoundingClientRect().top);}
  capture(accept){return this.viewport.clientHeight?captureChatScrollAnchor(this.viewport,this.container,accept):this.anchor;}
  rowVisible(row){
    if(row.role==='user'||row.role==='error')return true;
    if(row.kind==='reasoning')return this.showReasoning;
    if(row.role==='activity'||row.phase==='commentary')return this.showProcess;
    return true;
  }
  setVisibility({showReasoning=this.showReasoning,showProcess=this.showProcess,showToolImages=this.showToolImages}){
    if(this.showReasoning===showReasoning&&this.showProcess===showProcess&&this.showToolImages===showToolImages)return;
    const imagesChanged=this.showToolImages!==showToolImages;
    this.showToolImages=showToolImages;
    this.showReasoning=showReasoning;this.showProcess=showProcess;
    const anchor=this.capture(node=>this.rowVisible(this.rows.get(node.dataset.key).row)),bottom=this.following;
    for(const [key,entry] of this.nodes){
      const row=this.rows.get(key).row;
      if(imagesChanged&&row.attachments?.some(item=>item.type==='toolImage')){
        const reading=captureChatActivityState(entry.node);
        const node=this.renderRow(row,reading?.open);node.dataset.key=key;
        entry.node.replaceWith(node);entry.node=node;restoreChatActivityState(node,reading);
      }
      entry.node.hidden=!this.rowVisible(row);
    }
    this.refreshActions();
    this.restore(anchor,bottom);
  }
  jumpBottom(){this.viewport.scrollTop=this.viewport.scrollHeight;this.following=true;this.newer.hidden=true;}
  restore(anchor,bottom=false){
    if(this.abort.signal.aborted||!this.viewport.clientHeight)return;
    const reading=window.BridgeMobileHandoff?.readingAnchor?.(this);
    if(reading && this.nodes.has(reading.key)){anchor=reading;bottom=false;this.following=false;}
    this.suppress=true;
    const node=anchor&&this.nodes.get(anchor.key)?.node;
    if(bottom)this.jumpBottom();
    else restoreChatScrollAnchor(this.viewport,node,anchor);
    this.lastTop=this.viewport.scrollTop;
    this.anchor=this.capture();
    requestAnimationFrame(()=>this.suppress=false);
  }
  read(action,params={}){return this.request(this.url(action)+'&'+new URLSearchParams(params),undefined,this.abort.signal);}
  async start(){
    if(this.starting||this.abort.signal.aborted)return;
    clearTimeout(this.startRetry);this.starting=true;
    if(this.sequence<0&&this.cacheKey&&recentTimelinePages.has(this.cacheKey)){
      this.apply(recentTimelinePages.get(this.cacheKey),true,false,true);
    }
    this.older.hidden=false;this.older.disabled=true;this.older.textContent=timelineText('正在读取最近内容…');
    try{
      const page=await this.read('timeline',{limit:20});
      if(this.abort.signal.aborted)return;
      this.apply(page,true);this.updates();
    }catch(e){if(this.abort.signal.aborted)return;this.older.disabled=false;this.older.textContent=timelineText('读取失败，点击重试');this.status(e.message);this.startRetry=setTimeout(()=>this.start(),3000);}
    finally{this.starting=false;}
  }
  historyStatus(){this.older.hidden=!this.hasMore;this.older.disabled=this.busy;this.older.textContent=this.busy?timelineText('正在读取更早内容…'):this.retryCount?timelineText('历史加载失败，点击重试'):timelineText('查看更早内容');}
  async loadOlder(count){
    if(this.cached||this.busy||!this.hasMore||this.abort.signal.aborted)return;
    this.busy=true;this.retryCount=0;this.historyStatus();
    try{
      // Keep the user's history intent across a concurrent live reset. A page
      // of hidden tool activity may otherwise leave scrollTop stuck at zero.
      for(let attempt=0;attempt<3;attempt++){
        const generation=this.generation,before=this.before;
        const page=await this.read('timeline',{before,limit:count});
        if(this.abort.signal.aborted)return;
        if(generation!==this.generation)continue;
        if(page.reset||page.epoch!==this.epoch){this.apply(page,true);if(!this.hasMore)return;continue;}
        if(page.hasMore&&(!page.rows.length||page.before===before)){this.retryCount=count;return;}
        const visible=page.rows.some(row=>!this.rows.has(row.key)&&this.rowVisible(row));
        this.apply(page,false,true);
        if(visible||!this.hasMore||!this.active()||!this.nearTop())return;
      }
    }catch(e){if(this.abort.signal.aborted)return;this.retryCount=count;}
    finally{this.busy=false;if(!this.abort.signal.aborted)this.historyStatus();}
  }
  apply(page,reset=false,history=false,cached=false){
    if(this.cached&&!cached){
      // An empty owner-discovery response is not an empty conversation.
      if(!page.rows.length&&page.meta.loadingHistory){
        this.sequence=page.sequence;this.epoch=page.epoch;
        this.renderMeta({...page.meta,files:page.files||[]});return;
      }
      reset=true;
    }
    this.cached=cached;
    const anchor=this.capture(),bottom=this.following;
    if(reset){this.generation++;window.BridgeMermaidCards?.clearScope(this.mermaidScope);this.mermaidScope=window.BridgeMermaidCards?.createScope();this.rows.clear();this.nodes.clear();this.details.clear();this.files.clear();this.container.replaceChildren();this.epoch=page.epoch;this.retryCount=0;}
    for(const file of page.files||[])this.files.set(file.id,file);
    if((!history&&page.sequence>=this.sequence)||reset){this.sequence=page.sequence;this.meta=page.meta;if(!cached)this.renderMeta({...page.meta,files:[...this.files.values()]});}
    if(!history)this.pendingRequests=!cached&&!!page.meta.requests?.length;
    let changed=false;
    for(const row of page.rows){
      const previous=this.rows.get(row.key);
      if(previous&&previous.sequence>page.sequence)continue;
      this.rows.set(row.key,{row,sequence:page.sequence});
      const stamp=JSON.stringify(row),old=this.nodes.get(row.key);
      if(old?.stamp===stamp)continue;
      const node=this.renderRow(row);node.dataset.key=row.key;
      node.hidden=!this.rowVisible(row);changed=changed||!node.hidden;
      this.nodes.set(row.key,{node,stamp});
    }
    const ordered=[...this.rows.values()].map(x=>x.row).sort((a,b)=>a.order-b.order);
    syncChatMessageNodes(this.container,ordered.map(row=>this.nodes.get(row.key).node));
    if(reset||history){this.before=page.before;this.hasMore=page.hasMore;this.windowRevision++;}
    this.refreshActions();this.historyStatus();this.restore(anchor,bottom);
    if(!history&&!bottom&&(changed||page.meta.requests?.length)){this.newer.textContent=page.meta.requests?.length?timelineText('有待确认请求 ↓'):timelineText('有新内容 ↓');this.newer.hidden=false;}
    if(!cached&&!history&&this.cacheKey){
      const rows=ordered.slice(-20);
      // Memory-only, bounded and gateway/auth/host/thread scoped by the caller.
      // Cached approvals and access permissions never become actionable state.
      recentTimelinePages.delete(this.cacheKey);
      if(rows.length)recentTimelinePages.set(this.cacheKey,JSON.parse(JSON.stringify({rows,sequence:this.sequence,epoch:this.epoch,
        before:this.epoch+'.'+rows[0].key,hasMore:this.hasMore||ordered.length>20,meta:{requests:[]},files:page.files||[]})));
      while(recentTimelinePages.size>8)recentTimelinePages.delete(recentTimelinePages.keys().next().value);
    }
    if(!cached&&!history&&!this.abort.signal.aborted&&!page.meta.loadingHistory)this.onReady?.();
  }
  renderRow(row,open){
    const activity=row.role==='activity'||row.role==='fileChanges',toolImages=activity&&row.attachments?.some(item=>item.type==='toolImage');
    const node=document.createElement(activity&&!toolImages?'details':'div');
    node.className=toolImages?'tool-images timeline-row':activity?'activity timeline-row':'message timeline-row '+row.role;
    const content=toolImages?document.createElement('details'):node;
    if(toolImages){content.className='activity';node.append(content);}
    const heading=document.createElement(activity?'summary':'span');heading.className=activity?'':'who';
    heading.textContent=activity?(row.title||row.kind)+(row.status==='inProgress'?timelineText(' · 进行中'):''):row.role==='user'?timelineText('你'):row.role==='error'?timelineText('执行错误'):['CODEX',row.model&&[row.model,row.effort].filter(Boolean).join(' ')].filter(Boolean).join(' · ');content.append(heading);
    if(row.fileSummary){
      node.classList.add('edited-files-card');
      heading.replaceChildren();
      const label=document.createElement('span');label.className='edited-files-label';label.textContent=fileChangeLabel(row);
      const stats=document.createElement('span');stats.className='edited-files-stats';
      const added=document.createElement('span');added.className='edited-files-added';added.textContent='+'+row.fileSummary.added;
      const removed=document.createElement('span');removed.className='edited-files-removed';removed.textContent='−'+row.fileSummary.removed;
      stats.append(added,removed);heading.append(label);
      if(row.fileSummary.basis==='recorded'){
        const basis=document.createElement('small');basis.className='edited-files-basis';basis.textContent=timelineText('累计修改');heading.append(basis);
      }
      heading.append(stats);
      const list=document.createElement('ul');list.className='edited-files-list';
      for(const file of row.fileSummary.files){
        const item=document.createElement('li'),path=document.createElement(this.onFile?'button':'span'),counts=document.createElement('span');
        if(this.onFile){path.type='button';path.onclick=()=>this.onFile(file.path);}
        path.textContent=file.path;path.className='edited-files-path';counts.textContent='+'+file.added+' −'+file.removed;counts.className='edited-files-stats';item.append(path,counts);list.append(item);
        if(this.onFile)path.classList.add('plain');
      }
      content.append(list);
      // File cards expand the supplied summary only, including old cached rows
      // which may still advertise a patch body. Never request their details.
      content.open=!!open;
      return node;
    }
    const body=document.createElement(activity?'pre':'div');body.className=activity?'activity-body':'message-body';
    body.__mermaidScope=this.mermaidScope;body.__mermaidIdentity=row.key;
    const more=document.createElement('button');more.className='plain detail-more';more.type='button';
    const cached=this.details.get(row.key);
    if(cached&&cached.version!==row.version)this.details.delete(row.key);
    const paint=()=>{
      const reading=captureChatActivityState(node);
      const detail=this.details.get(row.key),text=detail?.text??row.text;
      body.replaceChildren();if(activity)body.textContent=text;else this.renderText(body,text,[...this.files.values()],()=>this.fullText(row));
      if(activity)body.hidden=!text;
      more.hidden=detail?detail.next===null:!row.truncated;
      more.textContent=detail?timelineText('继续加载正文'):timelineText('展开完整内容');
      restoreChatActivityState(node,reading);
    };
    const load=async()=>{
      if(more.disabled||this.abort.signal.aborted)return;
      const prior=this.details.get(row.key);if(prior?.next===null)return;
      more.disabled=true;more.textContent=timelineText('正在读取…');
      try{
        const detail=await this.read('detail',{key:this.epoch+'.'+row.key,offset:prior?.next||0,version:prior?.version||row.version});
        if(this.abort.signal.aborted||!node.isConnected||this.rows.get(row.key)?.row.version!==detail.version)return;
        for(const file of detail.files||[])this.files.set(file.id,file);
        this.details.set(row.key,{version:detail.version,text:(detail.offset?prior?.text||'':'')+detail.text,next:detail.next});paint();
      }catch(e){if(!this.abort.signal.aborted){more.hidden=false;more.textContent=timelineText('读取失败，点击重试');}}
      finally{more.disabled=false;}
    };
    more.onclick=load;content.append(body,more);paint();
    if(row.role==='user'||row.role==='assistant'){
      const actions=document.createElement('div');actions.className='message-actions';
      const copy=document.createElement('button');copy.type='button';copy.className='plain message-copy';copy.textContent=timelineText('复制');copy.onclick=()=>window.BridgeClipboard?.copy(()=>this.fullText(row),copy);actions.append(copy);node.append(actions);
      const quote=document.createElement('button');quote.type='button';quote.className='plain message-quote';quote.textContent=timelineText('引用');quote.onclick=()=>node.dispatchEvent(new CustomEvent('bridge-compose-quote',{bubbles:true,detail:{getText:()=>this.fullText(row),source:row.key}}));actions.append(quote);
    }
    renderChatAttachments(node,row.attachments,{showToolImages:this.showToolImages,fileUrl:item=>{
      const file=[...this.files.values()].find(file=>file.reference===item.path||file.references?.includes(item.path));
      return file?this.url('files/'+file.id):null;
    },url:item=>{
      if(!item.uploadId&&!item.desktopId)return null;
      const source=item.uploadId?this.url('uploads/'+encodeURIComponent(item.uploadId)+'/preview'):this.url('desktop-images/'+encodeURIComponent(item.desktopId));
      return {preview:source,original:item.uploadId?source+'&variant=original':source};
    }});
    if(activity){content.open=!!open;content.ontoggle=()=>{if(content.open&&!this.details.has(row.key))load();};}
    return node;
  }
  fullText(row){
    const valid=()=>!this.abort.signal.aborted&&this.rows.get(row.key)?.row.version===row.version;
    if(!valid())return Promise.reject(Error(timelineText('消息内容已变化，请重新打开编辑')));
    if(!row.truncated)return row.text;
    return (async()=>{
      let text='',offset=0;
      do{
        const detail=await this.read('detail',{key:this.epoch+'.'+row.key,offset,version:row.version});
        if(!valid()||detail.version!==row.version||detail.offset!==offset||detail.key!==row.key)throw Error(timelineText('消息内容已变化，请重新打开编辑'));
        text+=detail.text;
        if(detail.next===null)return text;
        if(detail.next<=offset)throw Error(timelineText('读取完整正文失败，请重试'));
        offset=detail.next;
      }while(true);
    })();
  }
  refreshActions(){
    for(const [key,{node}] of this.nodes){
      const row=this.rows.get(key)?.row,bar=node.querySelector('.message-actions');if(!bar||!row)continue;
      if(!this.onAction)continue;
      const add=(action,label,disabled)=>{const cls=action==='fork'?'message-fork':'message-edit';let b=bar.querySelector('.'+cls);if(!b){b=document.createElement('button');b.type='button';b.className='plain '+cls;bar.append(b);}b.textContent=timelineText(label);b.disabled=disabled;b.onclick=()=>this.onAction(action,row,this);};
      for(const action of bar.querySelectorAll('.message-edit,.message-fork'))action.hidden=!this.canEdit();
      if(row.editable&&this.canEdit()){const latest=this.meta?.latestUserTurnId===row.turnId;add(latest?'edit':'edit-fork',latest?'编辑并重新发送':'编辑并新建分支',row.turnStatus==='inProgress'||latest&&this.meta?.status==='active');}
      if(row.forkable&&this.canEdit())add('fork','从这里分支',false);
    }
  }
  relabel(){
    this.refreshActions();
    this.container.querySelectorAll('.message-copy').forEach(n=>n.textContent=timelineText('复制'));
    this.container.querySelectorAll('.message-quote').forEach(n=>n.textContent=timelineText('引用'));
    this.container.querySelectorAll('.code-copy').forEach(n=>n.textContent=timelineText('复制代码'));
    this.historyStatus();
    for(const [key,entry] of this.nodes){
      const row=this.rows.get(key)?.row;if(!row)continue;
      const node=entry.node,heading=(row.role==='activity'||row.role==='fileChanges')?(node.tagName==='DETAILS'?node.firstElementChild:node.querySelector('details.activity > summary')):node.firstElementChild;
      if(row.fileSummary){const label=node.querySelector('.edited-files-label');if(label)label.textContent=fileChangeLabel(row);const basis=node.querySelector('.edited-files-basis');if(basis)basis.textContent=timelineText('累计修改');}
      else if(heading)heading.textContent=(row.role==='activity'||row.role==='fileChanges')?(row.title||row.kind)+(row.status==='inProgress'?timelineText(' · 进行中'):''):row.role==='user'?timelineText('你'):row.role==='error'?timelineText('执行错误'):['CODEX',row.model&&[row.model,row.effort].filter(Boolean).join(' ')].filter(Boolean).join(' · ');
      const more=node.querySelector('.detail-more');
      if(more&&!more.disabled)more.textContent=timelineText(this.details.has(key)?'继续加载正文':'展开完整内容');
      const attachment=node.querySelector('.timeline-attachments small.muted');
      if(attachment)attachment.textContent=timelineText('附件：')+row.attachments.map(a=>a.name||a.type).join('、');
    }
    this.newer.textContent=timelineText(this.pendingRequests?'有待确认请求 ↓':'有新内容 ↓');
  }
  async updates(){
    while(!this.abort.signal.aborted){
      if(!this.active()){await this.pause(1000);continue;}
      try{
        const generation=this.generation,windowRevision=this.windowRevision;
        const page=await this.read('changes',{after:this.sequence,epoch:this.epoch,start:this.before||''});
        if(this.abort.signal.aborted)return;
        if(generation!==this.generation||windowRevision!==this.windowRevision)continue;
        // History may have expanded the window while this poll was in flight.
        // Polls never replace the oldest loaded cursor.
        this.apply(page,!!page.reset);
        await this.pause(200);
      }catch(e){if(this.abort.signal.aborted)return;this.status(timelineText('重新连接中'));await this.pause(3000);}
    }
  }
  pause(ms){return new Promise(resolve=>{const done=()=>{clearTimeout(timer);this.abort.signal.removeEventListener('abort',done);resolve();};const timer=setTimeout(done,ms);this.abort.signal.addEventListener('abort',done,{once:true});});}
}
