'use strict';
// Native clocks are epoch milliseconds or ISO instants with an explicit zone.
// Missing historical evidence stays missing; never substitute receipt/wall time.
function chatMessageDate(value){
  if(typeof value==='number'&&Number.isFinite(value)&&value>0)return new Date(value);
  if(typeof value==='string'&&/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value))return new Date(value);
  return null;
}
function chatListTime(value){
  const date=chatMessageDate(value);if(!date||!Number.isFinite(date.getTime()))return '';
  const pad=n=>String(n).padStart(2,'0');
  return pad(date.getHours())+':'+pad(date.getMinutes())+' '+pad(date.getMonth()+1)+'/'+pad(date.getDate());
}
function renderChatMessageTime(container,value,now=new Date()){
  const date=chatMessageDate(value);if(!date||!Number.isFinite(date.getTime()))return;
  const locale=typeof BridgeI18n==='undefined'?'zh-CN':BridgeI18n.locale();
  const clock=[date.getHours(),date.getMinutes()].map(n=>String(n).padStart(2,'0')).join(':');
  const sameYear=date.getFullYear()===now.getFullYear(),sameDay=sameYear&&date.getMonth()===now.getMonth()&&date.getDate()===now.getDate();
  const prefix=sameDay?'':(/^zh/.test(locale)?`${sameYear?'':date.getFullYear()+'年'}${date.getMonth()+1}月${date.getDate()}日`:new Intl.DateTimeFormat(locale,{...(sameYear?{}:{year:'numeric'}),month:'numeric',day:'numeric'}).format(date))+' ';
  const time=document.createElement('time');time.className='message-time';time.dateTime=date.toISOString();time.textContent=prefix+clock;
  time.title=new Intl.DateTimeFormat(locale,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZoneName:'short'}).format(date);
  time.setAttribute('aria-label',time.title);container.append(time);
}
function chatTurnStopped(status){return ['failed','interrupted','cancelled','canceled'].includes(status);}
function dismissibleChatNotice(notice,scope){
  const close=document.createElement('button');close.type='button';close.className='plain chat-notice-close';close.textContent='×';
  let dismissed=null,hidden=notice.hidden;
  const key=()=>JSON.stringify([scope(),[...notice.childNodes].filter(node=>node!==close&&node.nodeName!=='BUTTON')
    .map(node=>node.textContent).join('').trim()]);
  const refresh=()=>{
    if(notice.hidden&&!hidden)dismissed=null;
    hidden=notice.hidden;
    const current=key();
    if(dismissed!==current){dismissed=null;delete notice.dataset.noticeDismissed;}
    const label=typeof BridgeI18n==='undefined'?'关闭提示':BridgeI18n.t('关闭提示');
    if(close.getAttribute('aria-label')!==label)close.setAttribute('aria-label',label);
    if(!notice.hidden&&close.parentElement!==notice)notice.append(close);
  };
  close.onclick=()=>{dismissed=key();notice.dataset.noticeDismissed='true';};
  notice.classList.add('chat-notice-dismissible');
  new MutationObserver(refresh).observe(notice,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['hidden']});
}
function chatTurnCompletionLabel(turn,{live=false,now=Date.now()}={}){
  const t=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
  if(chatTurnStopped(turn.status))return t(turn.status==='failed'?'运行失败':'已停止');
  if(!['completed','inProgress'].includes(turn.status))return '';
  const done=turn.status==='completed';
  const start=chatMessageDate(turn.start),end=chatMessageDate(done?turn.end:live?now:turn.answerAt);
  if(!start||!end||!Number.isFinite(start.getTime())||!Number.isFinite(end.getTime())||end<start)return done?t('已完成'):'';
  const seconds=Math.floor((end-start)/1000),hours=Math.floor(seconds/3600),minutes=Math.floor(seconds/60)%60;
  const doneLabel=hours?'已完成，用时 {hours} 小时 {minutes} 分钟 {seconds} 秒':minutes?'已完成，用时 {minutes} 分钟 {seconds} 秒':'已完成，用时 {seconds} 秒';
  const liveLabel=hours?'用时 {hours} 小时 {minutes} 分钟 {seconds} 秒':minutes?'用时 {minutes} 分钟 {seconds} 秒':'用时 {seconds} 秒';
  return t(done?doneLabel:liveLabel).replace('{hours}',hours).replace('{minutes}',minutes).replace('{seconds}',seconds%60);
}
// One display clock for visible, verified running turns. It never polls the
// gateway, advances a cached/offline turn, or changes the native start instant.
const chatTurnClocks=(()=>{
  const controls=new Map();let timer,installed=false;
  const visible=()=>!globalThis.document?.hidden&&globalThis.window?.BridgeChatForeground!==false&&
    globalThis.navigator?.onLine!==false&&!globalThis.window?.OfflineChatReader?.enabled;
  const tick=()=>{
    clearTimeout(timer);timer=null;
    for(const [node,update] of controls){if(!node.isConnected)controls.delete(node);else if(visible())update();}
    if(controls.size&&visible())timer=setTimeout(tick,1000);
  };
  return (node,update)=>{
    if(update)controls.set(node,update);else controls.delete(node);
    if(!installed&&globalThis.document?.addEventListener){
      installed=true;document.addEventListener('visibilitychange',tick);
      for(const name of ['bridge-chat-foreground','online','offline','pageshow','pagehide'])window.addEventListener(name,tick);
    }
    if(!timer&&controls.size&&visible()&&typeof setTimeout==='function')timer=setTimeout(tick,1000);
  };
})();
function chatMessageHeading(row,agent){
  const t=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
  if(row.role==='user')return t('你');
  if(row.role==='error')return t('执行错误');
  const model=typeof row.model==='string'?row.model.trim():'',effort=typeof row.effort==='string'?row.effort.trim():'';
  const label=model?[model,effort&&!/^(none|off|disabled|false|0|关闭|无)$/i.test(effort)?effort:''].filter(Boolean).join(' '):'';
  return [agent,label].filter(Boolean).join(' · ');
}
function chatProcessPagerLabel(row){
  const t=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
  return t(row.pageError?'读取失败，点击重试':row.pageDirection==='earlier'?'加载更早过程':'加载后续过程');
}
function renderChatPartialOfflineNote(){
  const node=document.createElement('p');node.className='muted';node.textContent=BridgeI18n.t('本机仅保存了部分内容');
  return node;
}
function renderChatProcessPager(row,activate){
  const node=document.createElement(row.pageLoading?'span':'button');
  if(row.pageLoading){node.className='muted timeline-row chat-process-page';node.textContent=typeof BridgeI18n==='undefined'?'正在读取中间过程…':BridgeI18n.t('正在读取中间过程…');node.setAttribute('role','status');return node;}
  node.type='button';node.className='plain timeline-row chat-process-page';
  node.textContent=chatProcessPagerLabel(row);node.onclick=()=>activate(row.processKey,row.pageDirection);
  return node;
}
function chatFinalAnswer(row){
  return row.role==='assistant'&&(row.phase==='final_answer'||row.forkable===true&&row.phase!=='commentary')&&
    (row.text?.trim()||row.attachments?.length);
}
// Use native turn and answer identities. Missing evidence keeps the flat rows visible.
function chatTurnProcessRow(row){
  return !row.fileSummary&&(row.role==='user'&&row.interaction==='response'||row.role==='activity'||chatActivityKind(row)==='reasoning'||
    row.role==='assistant'&&row.phase==='commentary');
}
function chatNodesWithTurnCompletions(container,nodes,rows,{viewport,following=true,onDisclosure,live=false}={}){
  const turns=new Map(),nodeByKey=new Map(nodes.map(node=>[node.dataset.key,node])),managed=new Set();
  for(const [index,row] of rows.entries()){
    const key=String(row.key??row.id??index);
    if(row.turnId===undefined||row.turnId===null||row.turnId==='history'||row.turnCompletionKnown===false&&row.turnStatus!=='inProgress')continue;
    const id=String(row.turnId),turn=turns.get(id)||{id,rows:[]};
    turn.last=key;turn.rows.push({row,key,index});
    if(row.turnStatus)turn.status=row.turnStatus;
    if(row.turnStartedAt!==undefined)turn.start=row.turnStartedAt;
    if(row.turnCompletedAt!==undefined)turn.end=row.turnCompletedAt;
    if(row.turnError||row.role==='error'&&row.text)turn.error=String(row.turnError||row.text).slice(0,500);
    if(chatFinalAnswer(row)){
      turn.answerKey=key;turn.answerIndex=index;turn.answerAt=row.timestamp;
    }
    turns.set(id,turn);
  }
  const existing=new Map([...container.children].map(node=>[node.dataset.key,node]));
  const before=new Map(),after=new Map(),outcomes=new Set();
  for(const turn of turns.values()){
    const process=turn.rows.filter(({row,index})=>chatTurnProcessRow(row)&&
      (turn.answerIndex===undefined||index<turn.answerIndex));
    if(turn.status==='completed'&&!process.length)continue;
    const running=turn.status==='inProgress',stopped=chatTurnStopped(turn.status);
    const locate=running&&!!turn.answerKey&&process.length>0;
    const duration=chatTurnCompletionLabel(turn,{live});
    const runningLabel=typeof BridgeI18n==='undefined'?'正在思考与执行…':BridgeI18n.t('正在思考与执行…');
    let label=running&&process.length?((live||locate)&&duration||runningLabel):duration;
    if(!label)continue;
    const key='turn-completion:'+turn.id;
    const foldable=process.length>0&&(turn.status==='completed'&&!!turn.answerKey||stopped&&!turn.answerKey);
    const control=existing.get(key)||document.createElement('button');
    const finished=control.__chatTurn?.status==='inProgress'&&turn.status==='completed';
    control.type='button';control.className='chat-turn-completion';control.dataset.key=key;control.__chatTurn=turn;
    control.classList.toggle('chat-turn-locator',locate);
    if(control.__chatAnswer!==turn.answerKey||running)control.__chatProcessOpen=undefined;
    control.__chatAnswer=turn.answerKey;
    const processNodes=process.map(({key})=>nodeByKey.get(key)).filter(Boolean);
    for(const node of processNodes)managed.add(node);
    let outcome;
    if(stopped&&!turn.answerKey&&process.length){
      const tail=process.findLast(({row,key})=>row.kind!=='syncProcessPage'&&!nodeByKey.get(key)?.hidden)||process.at(-1);
      const error=turn.rows.findLast(({row})=>row.role==='error'&&row.text?.slice(0,500)===turn.error);
      const native=error&&nodeByKey.get(error.key),key='turn-outcome:'+turn.id;
      outcome=native||existing.get(key)||document.createElement('p');
      if(!native){
        outcome.dataset.key=key;
        const text=turn.error||label;
        if(outcome.textContent!==text)outcome.textContent=text;
      }
      outcome.classList.add('chat-turn-outcome');outcomes.add(outcome);managed.add(outcome);
      after.set(tail.key,outcome);
    }
    const selected=document.getSelection?.();
    const selecting=selected&&!selected.isCollapsed&&processNodes.some(node=>(existing.get(node.dataset.key)||node).contains(selected.anchorNode));
    const region=finished&&!following&&viewport?.getBoundingClientRect();
    const reading=region&&processNodes.some(node=>{
      const prior=existing.get(node.dataset.key);
      if(!prior||prior.hidden)return false;
      const rect=prior.getBoundingClientRect();
      return rect.bottom>region.top&&rect.top<region.bottom;
    });
    if(selecting||reading)control.__chatProcessOpen=true;
    if(finished&&(reading||selecting))control.__chatRetained=true;
    const update=()=>{
      const open=!foldable||(control.__chatProcessOpen??stopped);
      for(const node of processNodes)node.classList.toggle('chat-process-collapsed',!open);
      outcome?.classList.toggle('chat-process-collapsed',!open);
      control.disabled=!foldable&&!locate;
      if(foldable)control.setAttribute('aria-expanded',String(open));else control.removeAttribute('aria-expanded');
      const text=label+(foldable?(open?' ▴':' ▾'):locate?' ▾':'');
      if(control.textContent!==text)control.textContent=text;
      if(control.getAttribute('aria-label')!==label)control.setAttribute('aria-label',label);
    };
    const toggle=open=>{
      control.__chatProcessOpen=open;control.__chatRetained=false;update();onDisclosure?.(turn.id,open);
    };
    control.__chatCollapse=()=>toggle(false);
    control.onclick=()=>{
      if(locate){processNodes.find(node=>!node.hidden)?.scrollIntoView({block:'start'});return;}
      toggle(!(control.__chatProcessOpen??stopped));
    };
    update();
    chatTurnClocks(control,running&&live?()=>{label=chatTurnCompletionLabel(turn,{live:true})||runningLabel;update();}:null);
    if(finished&&foldable&&!control.__chatProcessOpen)onDisclosure?.(turn.id,false);
    if(process.length)before.set(process[0].key,control);else after.set(turn.last,control);
  }
  for(const node of nodes){
    if(!managed.has(node))node.classList?.remove('chat-process-collapsed');
    if(!outcomes.has(node))node.classList?.remove('chat-turn-outcome');
  }
  const result=[];
  for(const node of nodes){
    if(before.has(node.dataset.key))result.push(before.get(node.dataset.key));
    if(!outcomes.has(node))result.push(node);
    if(after.has(node.dataset.key))result.push(after.get(node.dataset.key));
  }
  return result;
}
function chatFollowsLatest(viewport){
  return viewport.scrollHeight-viewport.scrollTop-viewport.clientHeight<110;
}
function chatShouldSlideHistory(count,following){
  return following&&count>240&&document.getSelection?.()?.isCollapsed!==false;
}
function captureChatScrollAnchor(viewport,container,accept=()=>true){
  const top=viewport.getBoundingClientRect().top;
  let anchor=null;
  for(const node of container.children){
    if(node.hidden||node.dataset.key===undefined||node.classList?.contains('chat-turn-completion')||node.classList?.contains('chat-turn-outcome')||node.classList?.contains('chat-process-collapsed')||!accept(node))continue;
    const rect=node.getBoundingClientRect();
    anchor={key:node.dataset.key,offset:rect.top-top};
    if(rect.bottom>top)break;
  }
  return anchor;
}
function restoreChatScrollAnchor(viewport,node,anchor){
  if(!node||node.hidden||!anchor)return false;
  viewport.scrollTop+=node.getBoundingClientRect().top-viewport.getBoundingClientRect().top-anchor.offset;
  return true;
}
function captureChatActivityState(node){
  const details=node?.tagName==='DETAILS'?node:node?.querySelector('details.activity');
  if(!details)return null;
  const body=details.querySelector('.activity-body');
  return {open:details.open,top:body?.scrollTop||0,left:body?.scrollLeft||0};
}
function restoreChatActivityState(node,state){
  if(!state)return;
  const details=node.tagName==='DETAILS'?node:node.querySelector('details.activity');
  if(!details)return;
  details.open=state.open;
  if(state.open)details.__loadActivityBody?.();
  const body=details.querySelector('.activity-body');
  if(body){body.scrollTop=state.top;body.scrollLeft=state.left;}
}
// Desktop adapters already delivered bounded native pages. Reuse that text
// only when the reader opens the activity; do not build hidden output bodies.
function deferChatActivityBody(details,body,text){
  details.__loadActivityBody=()=>{if(body.textContent!==text)body.textContent=text;};
  details.addEventListener('toggle',()=>{if(details.open)details.__loadActivityBody();});
}
function renderChatFileSummary(content,row,onFile){
  const translate=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source),heading=content.children[0];
      content.classList.add('edited-files-card');
      heading.replaceChildren();
      const label=document.createElement('span');label.className='edited-files-label';label.textContent=translate('已编辑 {count} 个文件').replace('{count}',row.fileSummary.count);
      const stats=document.createElement('span');stats.className='edited-files-stats';
      const added=document.createElement('span');added.className='edited-files-added';added.textContent='+'+row.fileSummary.added;
      const removed=document.createElement('span');removed.className='edited-files-removed';removed.textContent='−'+row.fileSummary.removed;
      stats.append(added,removed);heading.append(label);
      if(row.fileSummary.basis==='recorded'){
        const basis=document.createElement('small');basis.className='edited-files-basis';basis.textContent=translate('累计修改');heading.append(basis);
      }
      heading.append(stats);
      const list=document.createElement('ul');list.className='edited-files-list';
      for(const file of row.fileSummary.files){
        const item=document.createElement('li'),path=document.createElement(onFile?'button':'span'),counts=document.createElement('span');
        if(onFile){path.type='button';path.onclick=()=>onFile(file.path);}
        path.textContent=file.path;path.className='edited-files-path';counts.textContent='+'+file.added+' −'+file.removed;counts.className='edited-files-stats';item.append(path,counts);list.append(item);
        if(onFile)path.classList.add('plain');
      }
      content.append(list);
}
// Classification is presentation-only: a user supplement stays a user message.
function chatActivityKind(row){
  if(row.fileSummary||row.role==='fileChanges')return '';
  if(row.role==='user')return row.interaction==='response'?'tool':'';
  if(row.role==='reasoning'||row.kind==='reasoning')return 'reasoning';
  return row.role==='activity'?'tool':'';
}
function chatActivityLabel(row){
  const t=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
  if(row.interaction==='response')return t('已回答问题');
  if(chatActivityKind(row)==='reasoning')return t('思考摘要');
  const text=(row.text||'').split('\n')[0];
  if(row.activityFiles?.length)return t('已编辑')+' '+row.activityFiles.map(file=>file.path).join('、');
  if(row.kind==='commandExecution'&&text)return t('已运行')+' '+text;
  if(['fileChange','FileChange'].includes(row.kind)&&text)return t('已编辑')+' '+text;
  return t(row.title||'执行过程');
}
function renderChatActivityLabel(heading,row){
  heading.replaceChildren();
  const label=document.createElement('span');label.className='chat-activity-text';
  label.textContent=chatActivityLabel(row);label.title=label.textContent;
  if(row.activityFiles?.length){
    label.replaceChildren(typeof BridgeI18n==='undefined'?'已编辑 ':BridgeI18n.t('已编辑')+' ');
    for(const [index,file] of row.activityFiles.entries()){
      if(index)label.append('、');
      const path=document.createElement('span');path.className='chat-activity-file';path.textContent=file.path;label.append(path);
    }
    const stats=document.createElement('span');stats.className='chat-activity-stats';
    stats.textContent='+'+row.activityFiles.reduce((sum,file)=>sum+file.added,0)+' −'+row.activityFiles.reduce((sum,file)=>sum+file.removed,0);
    heading.append(label,stats);
  }else heading.append(label);
}
// One history gesture backfills bounded native rows until ordinary dialogue appears.
// Native pages stay bounded; collect before rendering once, preserving a manual
// continuation after four pages or a transport failure.
async function readChatHistoryBatch(read,before,{known=new Set(),current=()=>true}={}){
  const pages=[],seen=new Set();let cursor=before;
  for(let count=0;count<4;count++){
    let page;
    try{page=await read(cursor);}catch(error){return current()?{pages,error}:{pages:[],cancelled:true};}
    if(!current())return {pages:[],cancelled:true};
    if(page.reset||pages.length&&page.epoch!==pages[0].epoch)return {pages:[],reset:page};
    if(page.hasMore&&(!page.rows.length||page.before===cursor||seen.has(page.before)))return {pages,stalled:true};
    pages.push(page);seen.add(page.before);
    if(!page.hasMore||page.rows.some(row=>!known.has(row.key??row.id)&&!chatActivityKind(row)))break;
    cursor=page.before;
  }
  return {pages};
}
// Stable row keys are local to this conversation container. Do not detach
// unchanged rows: even reattaching the same node resets nested scroll areas.
function syncChatMessageNodes(container,nodes){
  const previous=new Map([...container.children].filter(node=>node.dataset.key!==undefined).map(node=>[node.dataset.key,node]));
  const reading=new Map(),wanted=new Set(nodes);
  for(const node of nodes){
    const old=previous.get(node.dataset.key);
    if(old&&old!==node)reading.set(node,captureChatActivityState(old));
  }
  for(const node of [...container.children])if(!wanted.has(node))node.remove();
  let position=container.firstChild;
  for(const node of nodes){
    if(node===position)position=position.nextSibling;
    else{
      if(!reading.has(node))reading.set(node,captureChatActivityState(node));
      container.insertBefore(node,position);
    }
  }
  // Restore only changed/moved rows, after their new layout exists. Browsers
  // clamp offsets naturally when an updated activity becomes shorter.
  for(const [node,state] of reading)restoreChatActivityState(node,state);
}
// All chat surfaces render native task evidence through this shared indicator.
function renderChatProgress(node,{agentName='',active=false,waiting=false,blocked=false,connected=true,fresh=true}={}){
  const state=!connected||!fresh?'unknown':waiting?'waiting':active?'working':blocked?'blocked':'idle';
  const text={unknown:'运行状态暂不可用',waiting:'等待你的确认',blocked:'任务受阻，请查看提示',working:'正在工作…',idle:''}[state];
  node.hidden=state==='idle';node.dataset.state=state;node.setAttribute('role','status');node.setAttribute('aria-live','polite');
  const key=JSON.stringify([state,agentName,BridgeI18n.language()]);if(node.dataset.progress===key)return state;node.dataset.progress=key;
  node.replaceChildren();if(state==='working'){const dot=document.createElement('i');dot.setAttribute('aria-hidden','true');node.append(dot);}
  const label=document.createElement('span');label.textContent=[agentName,BridgeI18n.t(text)].filter(Boolean).join(' ');node.append(label);
  return state;
}
class SessionActivity {
  constructor({storage=localStorage,key='bridge-session-activity'}={}){this.storage=storage;this.key=key;try{this.rows=JSON.parse(storage.getItem(key)||'{}');}catch{this.rows={};}}
  id(row){return row.host+'|'+row.id;}
  save(){try{this.storage.setItem(this.key,JSON.stringify(this.rows));}catch{}}
  update(rows,visible=null){
    for(const row of rows){
      const key=this.id(row),previous=this.rows[key],terminal=['completed','failed','interrupted'].includes(row.turnStatus);
      if(!row.connected){if(previous)previous.offline=true;continue;}
      const token=terminal&&row.turnId?row.turnId+':'+row.turnStatus:previous?.token||null;
      const pending=token&&previous&&(previous.turnKnown||previous.token||previous.status==='active')&&(token!==previous.token||previous.pending)?token:null;
      this.rows[key]={status:row.status,turnId:row.turnId||previous?.turnId,turnStatus:row.turnStatus||previous?.turnStatus,turnKnown:!!row.turnStatus||previous?.turnKnown||false,token,pending:visible===key?null:pending,offline:false};
    }
    this.save();
  }
  read(key){if(this.rows[key]){this.rows[key].pending=null;this.save();}}
  indicator(key){
    const row=this.rows[key];if(!row)return null;
    if(row.offline)return {kind:'unknown',label:'运行状态暂不可用'};
    if(row.status==='active')return {kind:row.offline?'unknown':'running',label:row.offline?'运行状态暂不可用':'运行中'};
    if(row.pending)return {kind:row.turnStatus==='completed'?'completed':'ended',label:row.turnStatus==='completed'?'已完成，未查看':row.turnStatus==='failed'?'运行失败，未查看':'已停止，未查看'};
    return null;
  }
  clear(){this.rows={};this.storage.removeItem(this.key);}
}
if(typeof module!=='undefined')module.exports={SessionActivity};
