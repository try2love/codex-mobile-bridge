'use strict';
function messageTime(value, label){
  if(value===null||value===undefined||value==='')return null;
  const numeric=typeof value==='number'||/^\d+(\.\d+)?$/.test(value);
  const date=new Date(numeric?(Number(value)<1e12?Number(value)*1000:Number(value)):value);
  if(!Number.isFinite(date.getTime())||date.getTime()<=0)return null;
  const now=new Date(),sameDay=date.toDateString()===now.toDateString();
  const clock=date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  const day=date.toLocaleDateString('zh-CN',{...(date.getFullYear()!==now.getFullYear()?{year:'numeric'}:{}),month:'2-digit',day:'2-digit'});
  const node=document.createElement('time');node.className='message-time';node.dateTime=date.toISOString();
  node.textContent=(sameDay?'':day+' ')+clock;
  node.title=label+' '+date.toLocaleString('zh-CN',{hourCycle:'h23'});
  node.setAttribute('aria-label',node.title);return node;
}
// History and live changes share stable keys, but never share a request queue.
class ChatTimeline {
  constructor({url, request, renderMeta, renderText, status, isCurrent=()=>true, id, host}) {
    Object.assign(this,{url,request,renderMeta,renderText,status,isCurrent,id,host});
    this.viewport=document.getElementById('timeline');
    this.container=document.getElementById('messages');
    this.older=document.getElementById('older');
    this.newer=document.getElementById('newer');
    this.abort=new AbortController();this.rows=new Map();this.nodes=new Map();this.details=new Map();this.detailRequests=new Map();
    this.files=new Map();this.sequence=-1;this.epoch='';this.before=null;this.hasMore=false;
    this.busy=false;this.generation=0;this.windowRevision=0;this.following=true;this.lastTop=0;this.retryCount=0;
    this.viewport.scrollTop=0;
    this.onScroll=()=>{
      if(!this.active()||this.viewport.scrollTop===this.lastTop)return;
      const upward=this.viewport.scrollTop<this.lastTop;
      this.lastTop=this.viewport.scrollTop;this.following=this.atBottom();this.anchor=this.capture();
      if(this.following)this.newer.hidden=true;
      if(upward&&this.nearTop()&&!this.retryCount)this.loadOlder(100);
    };
    this.viewport.addEventListener('scroll',this.onScroll,{passive:true});
    this.older.onclick=()=>this.sequence<0?this.start():this.loadOlder(this.retryCount||100);
    this.newer.onclick=()=>{this.jumpBottom();this.anchor=this.capture();};
    this.resize=new ResizeObserver(()=>{
      if(!this.active())return;
      // A touch/momentum scroll can precede its scroll event. Never restore over it.
      this.syncScroll();this.restore(this.anchor,this.following);
    });
    this.resize.observe(this.container);
  }
  dispose(){this.abort.abort();this.resize.disconnect();this.viewport.removeEventListener('scroll',this.onScroll);this.newer.hidden=true;}
  active(){return !this.abort.signal.aborted&&this.isCurrent();}
  syncScroll(){
    if(this.viewport.scrollTop===this.lastTop)return;
    this.lastTop=this.viewport.scrollTop;this.following=this.atBottom();this.anchor=this.capture();
  }
  atBottom(){return this.viewport.scrollHeight-this.viewport.scrollTop-this.viewport.clientHeight<110;}
  nearTop(){const nodes=[...this.container.children];return nodes.length>0&&nodes.slice(0,11).some(n=>n.getBoundingClientRect().bottom>=this.viewport.getBoundingClientRect().top);}
  capture(){const top=this.viewport.getBoundingClientRect().top;const node=[...this.container.children].find(n=>n.getBoundingClientRect().bottom>top);return node?{key:node.dataset.key,offset:node.getBoundingClientRect().top-top}:null;}
  jumpBottom(){if(!this.active())return;this.viewport.scrollTop=this.viewport.scrollHeight;this.lastTop=this.viewport.scrollTop;this.following=true;this.newer.hidden=true;}
  restore(anchor,bottom=false){
    if(!this.active())return;
    const node=anchor&&this.nodes.get(anchor.key)?.node;
    if(bottom){if(this.viewport.scrollHeight-this.viewport.scrollTop-this.viewport.clientHeight>1)this.jumpBottom();}
    else if(node){const delta=node.getBoundingClientRect().top-this.viewport.getBoundingClientRect().top-anchor.offset;if(Math.abs(delta)>1)this.viewport.scrollTop+=delta;}
    this.lastTop=this.viewport.scrollTop;
    this.anchor=this.capture();
  }
  read(action,params={}){
    const url=this.url(action)+'&'+new URLSearchParams(params);
    if(action!=='detail')return this.request(url,undefined,this.abort.signal);
    const controller=new AbortController(),cancel=()=>controller.abort();
    if(this.abort.signal.aborted)cancel();else this.abort.signal.addEventListener('abort',cancel,{once:true});
    const timer=setTimeout(cancel,12000);
    return this.request(url,undefined,controller.signal).finally(()=>{clearTimeout(timer);this.abort.signal.removeEventListener('abort',cancel);});
  }
  async start(){
    if(!this.active())return;
    this.older.hidden=false;this.older.disabled=true;this.older.textContent='正在读取最近内容…';
    try{
      const page=await this.read('timeline',{limit:20});
      if(this.abort.signal.aborted)return;
      this.apply(page,true);this.updates();
      // Give the first 20 records a paint before quietly filling the 100-row window.
      requestAnimationFrame(()=>requestAnimationFrame(()=>this.fillRecent()));
    }catch(e){if(this.abort.signal.aborted)return;this.older.disabled=false;this.older.textContent='读取失败，点击重试';this.status(e.message);}
  }
  fillRecent(){if(!this.abort.signal.aborted&&this.rows.size>0&&this.rows.size<100&&!this.retryCount)this.loadOlder(100-this.rows.size);}
  historyStatus(){this.older.hidden=!this.hasMore;this.older.disabled=this.busy;this.older.textContent=this.busy?'正在读取更早内容…':this.retryCount?'历史加载失败，点击重试':'查看更早内容';}
  async loadOlder(count){
    if(this.busy||!this.hasMore||this.abort.signal.aborted)return;
    const generation=this.generation,before=this.before;
    this.busy=true;this.retryCount=0;this.historyStatus();
    try{
      const page=await this.read('timeline',{before,limit:count});
      if(this.abort.signal.aborted||generation!==this.generation)return;
      if(page.reset||page.epoch!==this.epoch){this.apply(page,true);return;}
      this.apply(page,false,true);
    }catch(e){if(this.abort.signal.aborted||generation!==this.generation)return;this.retryCount=count;}
    finally{this.busy=false;if(!this.abort.signal.aborted){this.historyStatus();this.fillRecent();}}
  }
  apply(page,reset=false,history=false){
    if(!this.active())return;
    if((this.id&&page.meta?.id!==this.id)||(this.host&&page.meta?.host!==this.host))throw Error('会话数据不匹配，请重新连接');
    this.syncScroll();
    const anchor=this.capture(),bottom=this.following;
    if(reset){this.generation++;this.rows.clear();this.nodes.clear();this.details.clear();this.files.clear();this.container.replaceChildren();this.epoch=page.epoch;this.retryCount=0;}
    for(const file of page.files||[])this.files.set(file.id,file);
    if((!history&&page.sequence>=this.sequence)||reset){this.sequence=page.sequence;this.renderMeta({...page.meta,files:[...this.files.values()]});}
    let changed=false;
    for(const row of page.rows){
      const previous=this.rows.get(row.key);
      if(previous&&previous.sequence>page.sequence)continue;
      this.rows.set(row.key,{row,sequence:page.sequence});
      const stamp=JSON.stringify(row),old=this.nodes.get(row.key);
      if(old?.stamp===stamp)continue;
      changed=true;
      const open=old?.node.tagName==='DETAILS'&&old.node.open;
      const rawOpen=!!old?.node.querySelector('.activity-raw')?.open;
      const node=this.renderRow(row,open,rawOpen);node.dataset.key=row.key;
      if(old)old.node.replaceWith(node);
      this.nodes.set(row.key,{node,stamp});
    }
    const ordered=[...this.rows.values()].map(x=>x.row).sort((a,b)=>a.order-b.order);
    let position=this.container.firstChild;
    for(const row of ordered){const node=this.nodes.get(row.key).node;if(node===position)position=position.nextSibling;else this.container.insertBefore(node,position);}
    if(reset||history){this.before=page.before;this.hasMore=page.hasMore;this.windowRevision++;}
    this.historyStatus();
    if(reset||history||changed)this.restore(anchor,bottom);
    refreshChatSearch();
    if(!history&&!bottom&&(changed||page.meta.requests?.length)){this.newer.textContent=page.meta.requests?.length?'有待确认请求 ↓':'有新内容 ↓';this.newer.hidden=false;}
    if(reset)requestAnimationFrame(()=>this.fillRecent());
  }
  renderRow(row,open,rawOpen=false){
    const activity=row.role==='activity',reasoning=activity&&row.kind==='reasoning',node=document.createElement(activity?'details':'div');
    node.className=activity?'activity timeline-row':'message timeline-row '+row.role;
    const heading=document.createElement(activity?'summary':'span');heading.className=activity?'':'who';
    heading.textContent=activity?(row.title||row.kind)+(row.status==='inProgress'?' · 进行中':''):row.role==='user'?'你':row.role==='error'?'执行错误':'CODEX';node.append(heading);
    const body=document.createElement(activity&&!reasoning?'pre':'div');body.className=activity?(reasoning?'activity-reasoning':'activity-body'):'message-body';
    let rawDetails;
    if(activity&&!reasoning){
      const description=document.createElement('p');description.className='activity-description';
      description.textContent=row.activitySummary||(row.kind==='commandExecution'?'执行终端命令。':'工具活动，原始内容可在下方查看。');
      node.append(description);
      const labels=[];
      if(row.cwd)labels.push('目录：'+String(row.cwd).replace(/\\/g,'/').replace(/\/$/,'').split('/').pop());
      if(typeof row.exitCode==='number')labels.push(row.exitCode===0?'执行完成':'执行失败（退出码 '+row.exitCode+'）');
      else if(row.status==='inProgress')labels.push('正在执行');
      else if(row.status==='completed')labels.push('已结束');
      if(typeof row.durationMs==='number'&&row.durationMs>=0)labels.push('耗时 '+(row.durationMs/1000).toFixed(1)+' 秒');
      if(labels.length){const meta=document.createElement('p');meta.className='activity-meta';meta.textContent=labels.join(' · ');if(row.cwd)meta.title=row.cwd;node.append(meta);}
      rawDetails=document.createElement('details');rawDetails.className='activity-raw';
      const rawHeading=document.createElement('summary');rawHeading.textContent=row.kind==='commandExecution'?'查看完整命令与输出':row.kind==='fileChange'?'查看文件改动':'查看工具参数与结果';
      rawDetails.append(rawHeading);node.append(rawDetails);
    }
    const more=document.createElement('button');more.className='plain detail-more';more.type='button';
    const cached=this.details.get(row.key);
    if(cached&&cached.version!==row.version)this.details.delete(row.key);
    const actions=document.createElement('div');actions.className='message-actions';
    const actionButtons=[];
    if(!activity){
      const copy=document.createElement('button');copy.type='button';copy.className='plain';copy.textContent='复制';
      copy.onclick=()=>copyChatText(this.details.get(row.key)?.text??row.text??'');
      actions.append(copy);actionButtons.push(copy);
      if(row.role==='user'){
        const edit=document.createElement('button');edit.type='button';edit.className='plain';edit.textContent='编辑后重发';
        edit.onclick=()=>editChatMessage(this.details.get(row.key)?.text??row.text??'',!!row.attachments?.length);
        actions.append(edit);actionButtons.push(edit);
      }
      if(row.role==='error'){
        const resume=document.createElement('button');resume.type='button';resume.className='plain continue-task';resume.textContent='继续完成当前任务';
        const targetId=currentId,targetHost=currentHost;
        resume.onclick=()=>{if(!this.abort.signal.aborted)continueCurrentTask(resume,targetId,targetHost,row.turnId);};
        actions.append(resume);
      }
      const time=row.role==='user'?messageTime(row.sentAt,'发送于'):row.role==='assistant'?messageTime(row.completedAt,'生成完成于'):null;
      if(time)actions.append(time);
    }
    const paint=()=>{
      const detail=this.details.get(row.key),text=detail?.text??row.text;
      if(row.role==='error')renderChatError(body,text);
      else{body.replaceChildren();if(activity&&!reasoning)body.textContent=text||'暂时没有可显示的内容。';else this.renderText(body,text||(reasoning?'尚未提供可显示的思考摘要。':''),[...this.files.values()]);}
      if(reasoning)for(const pre of [...body.querySelectorAll('pre')]){
        const code=document.createElement('details'),label=document.createElement('summary');code.className='activity-code';label.textContent='查看代码片段';
        pre.before(code);code.append(label,pre);
      }
      more.hidden=detail?detail.next===null:!row.truncated;
      more.textContent=detail?'继续加载正文':'展开完整内容';
      const incomplete=detail?detail.next!==null:row.truncated;
      for(const button of actionButtons){button.disabled=!!incomplete||!text;button.title=incomplete?'请先展开完整内容':'';}
    };
    const load=async()=>{
      if(more.disabled||this.abort.signal.aborted)return;
      const prior=this.details.get(row.key);if(prior?.next===null)return;
      const epoch=this.epoch,requestKey=epoch+'.'+row.key;
      more.disabled=true;more.hidden=false;more.textContent='正在读取…';
      let pending=this.detailRequests.get(requestKey);
      if(!pending){pending=this.read('detail',{key:requestKey,offset:prior?.next||0,version:prior?.version||row.version});this.detailRequests.set(requestKey,pending);}
      try{
        const detail=await pending;
        if(this.abort.signal.aborted||epoch!==this.epoch||!node.isConnected)return;
        if(this.rows.get(row.key)?.row.version!==detail.version){more.textContent='内容已更新，点击重新加载';return;}
        for(const file of detail.files||[])this.files.set(file.id,file);
        this.details.set(row.key,{version:detail.version,text:(detail.offset?prior?.text||'':'')+detail.text,next:detail.next});paint();refreshChatSearch();
      }catch(e){if(!this.abort.signal.aborted&&node.isConnected){more.hidden=false;more.textContent=e.name==='AbortError'?'读取超时，点击重试':'读取失败，点击重试';}}
      finally{if(this.detailRequests.get(requestKey)===pending)this.detailRequests.delete(requestKey);more.disabled=false;}
    };
    more.onclick=load;(rawDetails||node).append(body,more);paint();
    if(rawDetails){rawDetails.open=rawOpen;rawDetails.ontoggle=()=>{if(rawDetails.open&&row.truncated&&!this.details.has(row.key))void load();};}
    if(row.attachments?.length){
      const attachments=document.createElement('div');attachments.className='message-images';
      for(const attachment of row.attachments){
        if(/^[a-f0-9]{64}$/.test(attachment.imageId||'')){
          const image=document.createElement('img');image.alt=attachment.name||'已发送的图片';image.loading='lazy';image.src=this.url('images/'+attachment.imageId);
          enableImagePreview(image);
          image.onerror=()=>{const label=document.createElement('small');label.className='muted';label.textContent='图片附件（此处无法预览）';image.replaceWith(label);};attachments.append(image);
        }else{const label=document.createElement('small');label.className='muted';label.textContent=attachment.name||attachment.path||(['image','input_image','localImage'].includes(attachment.type)?'图片附件':attachment.type);attachments.append(label);}
      }node.append(attachments);
    }
    if(!activity)node.append(actions);
    if(activity)node.open=!!open;
    return node;
  }
  async updates(){
    while(!this.abort.signal.aborted){
      try{
        const generation=this.generation,windowRevision=this.windowRevision;
        const page=await this.read('changes',{after:this.sequence,epoch:this.epoch,start:this.before||''});
        if(this.abort.signal.aborted)return;
        if(generation!==this.generation||windowRevision!==this.windowRevision)continue;
        // History may have expanded the window while this poll was in flight.
        // Polls never replace the oldest loaded cursor.
        this.apply(page,!!page.reset);this.fillRecent();
        await this.pause(200);
      }catch(e){if(this.abort.signal.aborted)return;this.status('重新连接中');await this.pause(3000);}
    }
  }
  pause(ms){return new Promise(resolve=>{const done=()=>{clearTimeout(timer);this.abort.signal.removeEventListener('abort',done);resolve();};const timer=setTimeout(done,ms);this.abort.signal.addEventListener('abort',done,{once:true});});}
}
