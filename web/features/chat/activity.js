'use strict';
// Native clocks are epoch milliseconds or ISO instants with an explicit zone.
// Missing historical evidence stays missing; never substitute receipt/wall time.
function chatMessageDate(value){
  if(typeof value==='number'&&Number.isFinite(value)&&value>0)return new Date(value);
  if(typeof value==='string'&&/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value))return new Date(value);
  return null;
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
    if(node.hidden||node.dataset.key===undefined||!accept(node))continue;
    const rect=node.getBoundingClientRect();
    anchor={key:node.dataset.key,offset:rect.top-top};
    if(rect.bottom>top)break;
  }
  return anchor;
}
function restoreChatScrollAnchor(viewport,node,anchor){
  if(!node||node.hidden||!anchor)return false;
  for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent.classList?.contains('chat-activity-group'))node=parent;
  viewport.scrollTop+=node.getBoundingClientRect().top-viewport.getBoundingClientRect().top-anchor.offset;
  return true;
}
function captureChatActivityState(node){
  const details=node?.tagName==='DETAILS'?node:node?.querySelector('details.activity');
  if(!details)return null;
  const body=details.classList?.contains('chat-activity-group')?details.lastChild:details.querySelector('.activity-body');
  return {open:details.open,top:body?.scrollTop||0,left:body?.scrollLeft||0,children:details.classList?.contains('chat-activity-group')?[...details.querySelectorAll('details.activity')].map(node=>[node,captureChatActivityState(node)]):[]};
}
function restoreChatActivityState(node,state){
  if(!state)return;
  const details=node.tagName==='DETAILS'?node:node.querySelector('details.activity');
  if(!details)return;
  details.open=state.open;
  if(state.open)details.__loadActivityBody?.();
  const body=details.classList?.contains('chat-activity-group')?details.lastChild:details.querySelector('.activity-body');
  if(body){body.scrollTop=state.top;body.scrollLeft=state.left;}
  for(const [child,reading] of state.children||[])restoreChatActivityState(child,reading);
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
  const text=(row.text||'').split('\n')[0];
  if(row.kind==='commandExecution'&&text)return t('已运行')+' '+text;
  if(['fileChange','FileChange'].includes(row.kind)&&text)return t('已编辑')+' '+text;
  return t(row.title||(row.kind==='reasoning'||row.role==='reasoning'?'思考摘要':'执行过程'));
}
// One history gesture seeks ordinary conversation, not a page of folded tools.
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
function syncChatActivityPage(group){
  const body=group.lastChild,rows=group.__rows;
  let start=rows.findIndex(node=>node.dataset.key===group.__firstKey);
  if(start<0||!group.open&&!group.__readPage)start=Math.max(0,rows.length-20);
  const shown=rows.slice(start),top=body.scrollTop;
  if(!group.__older){
    const older=document.createElement('button');older.type='button';older.className='plain chat-activity-older';
    older.onclick=()=>{
      const rows=group.__rows,index=rows.findIndex(node=>node.dataset.key===group.__firstKey),anchor=rows[index],offset=anchor?.getBoundingClientRect().top;
      group.__readPage=true;group.__firstKey=rows[Math.max(0,index-20)].dataset.key;syncChatActivityPage(group);
      if(anchor&&offset!==undefined)body.scrollTop+=anchor.getBoundingClientRect().top-offset;
    };
    group.__older=older;
  }
  group.__older.textContent=typeof BridgeI18n==='undefined'?'加载更早过程':BridgeI18n.t('加载更早过程');
  group.__older.hidden=start===0;
  group.__firstKey=shown[0].dataset.key;group.dataset.key=group.__firstKey;
  syncChatMessageNodes(body,[group.__older,...shown],false);body.scrollTop=top;
}
// Consecutive tools share one lightweight disclosure. Commentary, thoughts,
// supplements and final results are siblings, never a catch-all outer group.
function groupChatActivities(container,nodes){
  const previous=container.__chatActivityGroups||new Map(),next=new Map(),used=new Set(),result=[];
  let cluster=[];
  const flush=()=>{
    if(!cluster.length)return;
    const key=cluster[0].dataset.key;
    let group=previous.get(key);
    if(!group)group=[...previous.values()].find(old=>!used.has(old)&&cluster.some(node=>old.__memberKeys?.has(node.dataset.key)));
    if(!group){
      group=document.createElement('details');group.className='chat-activity-group chat-activity-tools';
      const summary=document.createElement('summary'),label=document.createElement('span'),body=document.createElement('div');
      label.className='chat-activity-label';body.className='chat-activity-items';summary.append(label);group.append(summary,body);
    }
    used.add(group);next.set(key,group);group.__memberKeys=new Set(cluster.map(node=>node.dataset.key));
    group.dataset.key=key;group.hidden=cluster.every(node=>node.hidden);
    const kinds=cluster.map(node=>node.dataset.chatToolKind),files=kinds.some(kind=>['fileChange','FileChange'].includes(kind)),commands=kinds.includes('commandExecution');
    const label=files&&commands?'编辑了文件，运行了命令':files?'编辑了文件':commands?'运行了命令':'执行了操作';
    const translated=typeof BridgeI18n==='undefined'?label:BridgeI18n.t(label);
    group.dataset.icon=files?'file':commands?'command':'tool';
    if(group.firstChild.firstChild.textContent!==translated)group.firstChild.firstChild.textContent=translated;
    group.__rows=cluster;syncChatActivityPage(group);result.push(group);cluster=[];
  };
  for(const node of nodes){
    if(node.dataset?.chatActivity==='tool'){
      if(cluster.length&&node.dataset.chatTurnId&&cluster[0].dataset.chatTurnId&&node.dataset.chatTurnId!==cluster[0].dataset.chatTurnId)flush();
      cluster.push(node);
    }else{flush();result.push(node);}
  }
  flush();container.__chatActivityGroups=next;return result;
}
// Stable row keys are local to this conversation container. Do not detach
// unchanged rows: even reattaching the same node resets nested scroll areas.
function syncChatMessageNodes(container,nodes,group=true){
  if(group){if(nodes.some(node=>node.dataset?.chatActivity))nodes=groupChatActivities(container,nodes);else container.__chatActivityGroups=null;}
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
  const label=document.createElement('span');label.textContent=[agentName,BridgeI18n.t(text)].filter(Boolean).join(' ');node.append(label);return state;
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
