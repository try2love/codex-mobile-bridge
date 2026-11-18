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
  const candidates=[];
  const visit=node=>{
    if(node.hidden||(!node.open&&node.classList.contains('chat-process-continuation'))||node.dataset.key===undefined||!accept(node))return;
    if(node.classList.contains('chat-activity-turn')&&!node.classList.contains('chat-process-page')&&node.open&&node.firstChild.getBoundingClientRect().bottom<=top){
      for(const child of node.lastChild.children)visit(child);
    }else candidates.push(node);
  };
  for(const node of container.children)visit(node);
  for(const node of candidates){
    const rect=node.getBoundingClientRect();
    anchor={key:node.dataset.key,offset:rect.top-top,group:node.classList.contains('chat-activity-turn')?'turn':node.classList.contains('chat-activity-tools')?'tools':null};
    if(rect.bottom>top)break;
  }
  return anchor;
}
function restoreChatScrollAnchor(viewport,node,anchor){
  if(!node||node.hidden||!anchor)return false;
  if(anchor.group)node=node.closest('.chat-activity-'+anchor.group)||node;
  for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent.classList?.contains('chat-activity-group')&&!parent.open)node=parent;
  if(!node.open&&node.classList?.contains('chat-process-continuation'))node=node.nextElementSibling||node.previousElementSibling||node;
  viewport.scrollTop+=node.getBoundingClientRect().top-viewport.getBoundingClientRect().top-anchor.offset;
  return true;
}
function captureChatActivityState(node){
  const details=node?.tagName==='DETAILS'?node:node?.querySelector('details.activity');
  if(!details)return null;
  const body=details.classList?.contains('chat-activity-group')?details.lastChild:details.querySelector('.activity-body');
  return {open:details.open,top:body?.scrollTop||0,left:body?.scrollLeft||0,children:details.classList?.contains('chat-activity-group')?[...details.querySelectorAll('details.activity,details.chat-activity-group')].map(node=>[node,captureChatActivityState(node)]):[]};
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
function chatActivityIcon(row){
  if(chatActivityKind(row)==='reasoning')return 'reasoning';
  if(row.kind==='commandExecution')return 'command';
  if(['fileChange','FileChange'].includes(row.kind)||row.kind==='code'&&['write','edit'].includes(row.changeType))return 'file';
  if(['ImageView','imageView'].includes(row.kind))return 'read';
  const tool=row.toolName||row.tool;
  if(['Bash','bash'].includes(tool))return 'command';
  if(['Write','Edit','MultiEdit'].includes(tool))return 'file';
  if(tool==='Read')return 'read';
  if(['Grep','Glob','WebSearch'].includes(tool))return 'search';
  if(tool==='WebFetch')return 'browser';
  if(['cua_repl.js','mcp__cua_repl__js'].includes(tool))return 'computer';
  if(row.kind==='todo/write'||['TaskCreate','TaskUpdate','TodoWrite'].includes(tool))return 'task';
  return 'tool';
}
function chatActivityLabel(row){
  const t=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
  if(row.interaction==='response')return t('已回答问题');
  const text=(row.text||'').split('\n')[0];
  if(row.activityFiles?.length)return t('已编辑')+' '+row.activityFiles.map(file=>file.path).join('、');
  if(row.kind==='commandExecution'&&text)return t('已运行')+' '+text;
  if(['fileChange','FileChange'].includes(row.kind)&&text)return t('已编辑')+' '+text;
  return t(row.title||(row.kind==='reasoning'||row.role==='reasoning'?'思考摘要':'执行过程'));
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
  else{
    // A native reorder can move an already displayed row before the page
    // anchor. Keep it in the page so its detail and reading state survive.
    const visible=new Set([...body.children].map(node=>node.dataset.key));
    const first=rows.findIndex(node=>visible.has(node.dataset.key));
    if(first>=0)start=Math.min(start,first);
  }
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
// Task boundaries come from native turn IDs when available. Older adapters
// without them use ordinary user messages; response envelopes are never a task.
function chatTaskSegments(nodes,state={}){
  const tasks=new Map();let current='history';
  for(const node of nodes){
    const row=node.__chatMessage;if(!row)continue;
    // Legacy saved history uses a placeholder, not a native task boundary.
    if(row.turnId&&row.turnId!=='history')current='turn:'+row.turnId;
    else if(row.role==='user'&&row.interaction!=='response')current='user:'+node.dataset.key;
    let task=tasks.get(current);if(!task){task={key:current,nodes:[],finals:new Set()};tasks.set(current,task);}
    task.nodes.push(node);
  }
  const latest=[...tasks.values()].at(-1),byNode=new Map();
  for(const task of tasks.values()){
    const rows=task.nodes.map(node=>node.__chatMessage),status=[...rows].reverse().find(row=>row.turnStatus)?.turnStatus;
    task.active=state.fresh!==false&&(status?status==='inProgress':task===latest&&state.status==='active');
    task.terminal=status?['completed','failed','interrupted'].includes(status):state.fresh!==false&&!task.active;
    // Native asynchronous questions may also carry final_answer before the
    // same task resumes. Only its latest final is the task result.
    const final=[...task.nodes].reverse().find(node=>node.__chatMessage.role==='assistant'&&node.__chatMessage.phase==='final_answer');
    if(final)task.finals.add(final);
    if(!task.active&&!task.finals.size){
      const last=[...task.nodes].reverse().find(node=>node.__chatMessage.role==='assistant'&&node.__chatMessage.phase!=='commentary');
      if(last)task.finals.add(last);
    }
    // Only native message/turn clocks; no wall-clock estimate for old history.
    const first=rows.find(row=>row.role==='user'&&row.interaction!=='response');
    const projected=rows.some(row=>row.processCursor);
    const start=rows.find(row=>row.turnStartedAt)?.turnStartedAt??(projected?null:first?.timestamp);
    const end=rows.find(row=>row.turnCompletedAt)?.turnCompletedAt??(projected?null:[...task.finals].at(-1)?.__chatMessage.timestamp);
    const a=chatMessageDate(start),b=chatMessageDate(end);
    task.seconds=!task.active&&a&&b&&b>=a?Math.floor((b-a)/1000):null;
    const last=task.nodes.at(-1);
    for(const node of task.nodes){
      const row=node.__chatMessage;
      if(chatActivityKind(row)){
        node.dataset.icon=chatActivityIcon(row);
        if(node.firstElementChild?.classList?.contains('activity'))node.firstElementChild.dataset.icon=node.dataset.icon;
      }
      node.__chatRunning=task.active&&node===last&&!!chatActivityKind(row)&&!['completed','failed','interrupted','accepted'].includes(row.status);
      node.dataset.running=String(node.__chatRunning);byNode.set(node,task);
    }
  }
  return byNode;
}
function chatTaskLabel(task){
  const t=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
  if(task.active)return t('正在处理');
  if(task.seconds===null)return t('执行过程');
  const minutes=Math.floor(task.seconds/60),seconds=task.seconds%60;
  return minutes?t('用时 {minutes} 分钟 {seconds} 秒').replace('{minutes}',minutes).replace('{seconds}',seconds):t('用时 {seconds} 秒').replace('{seconds}',seconds);
}
function suspendChatLiveProcess(container){
  if(!container.__chatTaskState?.fresh)return;
  container.__chatTaskState.fresh=false;
  for(const group of container.__chatTaskGroups?.values()||[]){
    if(group.__processMode!=='live')continue;
    group.__processMode=null;
    group.__cancelProcessReads();
    group.__syncProcess();
  }
}
// Each fragment keeps its own directory and detail reads, controlled by the shared turn disclosure.
function renderChatProcessPage(existing,row,{readDirectory,readDetail,renderText,renderAttachments,current=()=>true,signal,onFiles=()=>{}}){
  const group=existing||document.createElement('details'),t=text=>typeof BridgeI18n==='undefined'?text:BridgeI18n.t(text);
  if(!existing){
    group.className='chat-activity-group chat-activity-turn timeline-row chat-process-page';
    group.append(document.createElement('summary'),document.createElement('div'));
    group.lastChild.className='chat-activity-items chat-process-items';group.__records=new Map();group.__recordKeys=[];group.__pages=new Map();
  }
  group.__chatMessage=row;group.dataset.key=row.key;group.dataset.chatTurnId=row.turnId||'';
  const valid=()=>current()&&!signal?.aborted&&group.isConnected&&!group.hidden;
  const cancel=()=>{group.__directoryController?.abort();group.__directoryController=null;for(const entry of group.__records.values()){entry.controller?.abort();entry.controller=null;}};
  group.__cancelProcessReads=cancel;
  const paint=()=>{
    const nodes=group.__recordKeys.map(key=>group.__records.get(key).node);
    if(group.__next){
      const more=document.createElement('button');more.type='button';more.className='plain chat-process-more';more.textContent=t('继续加载过程');more.onclick=()=>load(group.__next);nodes.push(more);
    }
    syncChatMessageNodes(group.lastChild,groupChatActivities(group.lastChild,nodes),false);
  };
  const record=metadata=>{
    let entry=group.__records.get(metadata.key);
    if(!entry){
      const leaf=metadata.role==='assistant'||metadata.hasDetail===false;
      const node=document.createElement(leaf?'section':'details'),heading=document.createElement(leaf?'div':'summary'),body=document.createElement('div');
      node.className='activity';body.className='activity-body';node.append(heading,body);
      entry={node,body};group.__records.set(metadata.key,entry);
    }
    const node=entry.node;
    if(entry.row?.version!==metadata.version)entry.detail=null;
    entry.row=metadata;node.dataset.key=metadata.key;node.dataset.icon=chatActivityIcon(metadata);node.__chatMessage=metadata;
    node.dataset.chatActivity=chatActivityKind(metadata);
    node.__chatRunning=group.__processMode==='live'&&group.__canAnimate&&metadata.status==='inProgress';
    renderChatActivityLabel(node.firstChild,metadata);
    node.firstChild.hidden=metadata.role==='assistant';
    const paintBody=()=>{
      const detail=entry.detail;if(!detail)return;
      const content=document.createElement(metadata.role==='assistant'?'div':'pre');content.className=metadata.role==='assistant'?'message-body':'activity-text';
      if(metadata.role==='assistant'&&renderText)renderText(content,detail.text);else content.textContent=detail.text;
      entry.body.replaceChildren(content);renderAttachments?.(entry.body,detail.attachments);
      if(detail.text){const copy=document.createElement('button');copy.type='button';copy.className='plain activity-copy';copy.textContent=t('复制');copy.onclick=()=>window.BridgeClipboard?.copy(()=>entry.detail.text,copy);entry.body.append(copy);}
      if(detail.next!==null){const more=document.createElement('button');more.type='button';more.className='plain detail-more';more.textContent=t('继续加载正文');more.onclick=()=>loadBody();entry.body.append(more);}
    };
    const loadBody=async()=>{
      if(!valid()||!group.open||!node.open||entry.controller||entry.detail?.next===null)return;
      const controller=entry.controller=new AbortController(),target=entry.row,prior=entry.detail;
      const offset=prior?.next||0;
      try{
        if(controller.signal.aborted||!valid()||!group.open||!node.open)return;
        const detail=await readDetail(target,offset,controller.signal);
        if(controller.signal.aborted||!valid()||!group.open||!node.open||entry.row!==target)return;
        if(detail.version!==target.version||detail.offset!==offset)throw Error(t('消息内容已变化，请重新打开编辑'));
        onFiles(detail.files||[]);
        entry.detail={...detail,text:(offset?prior.text:'')+detail.text,attachments:detail.attachments||prior?.attachments};paintBody();
      }catch(error){if(!controller.signal.aborted&&valid()&&group.open&&node.open){const retry=document.createElement('button');retry.type='button';retry.className='plain';retry.textContent=t('读取失败，点击重试');retry.title=error.message;retry.onclick=()=>loadBody();entry.body.replaceChildren(retry);}}
      finally{if(entry.controller===controller)entry.controller=null;}
    };
    node.ontoggle=()=>{if(node.open){if(entry.detail)paintBody();else loadBody();}else{entry.controller?.abort();entry.controller=null;}};
    if(metadata.role==='assistant'){
      const content=document.createElement('div');content.className='message-body';if(renderText)renderText(content,metadata.text||'');else content.textContent=metadata.text||'';entry.body.replaceChildren(content);
      if((metadata.text||'').length>=8000){node.open=true;const more=document.createElement('button');more.type='button';more.className='plain';more.textContent=t('展开完整内容');more.onclick=()=>loadBody();entry.body.append(more);}
    }
    if(node.open&&metadata.role!=='assistant'){if(entry.detail)paintBody();else loadBody();}
  };
  const load=async after=>{
    if(!valid()||!group.open||group.__directoryController)return;
    const target=group.__chatMessage;
    const latest=after==='latest',slot=after||'';
    if(group.__cacheVersion!==target.version){group.__pages.clear();group.__failedSlot=null;group.__cacheVersion=target.version;}
    if(group.__failedSlot===slot)return;
    group.__retry?.remove();group.__retry=null;
    const controller=group.__directoryController=new AbortController();
    try{
      const saved=group.__pages.get(slot);
      if(controller.signal.aborted||!valid()||!group.open)return;
      const page=saved||await readDirectory(target.processCursor,after,controller.signal);
      if(controller.signal.aborted||!valid()||!group.open||group.__chatMessage.version!==target.version)return;
      onFiles(page.files||[]);
      const rows=page.rows||[],keys=rows.map(metadata=>metadata.key);
      for(const metadata of rows)record(metadata);
      if(latest){
        group.__recordKeys=keys;group.__hasEarlier=Boolean(page.hasEarlier);
        for(const [key,entry] of group.__records)if(!keys.includes(key)){entry.controller?.abort();group.__records.delete(key);}
      }else{
        const loaded=group.__hasEarlier?[]:group.__recordKeys;
        group.__recordKeys=[...new Set(after?[...loaded,...keys]:[...keys,...loaded])];group.__hasEarlier=false;
      }
      group.__next=page.next;group.__loadedVersion=target.version;group.__pages.set(slot,page);paint();
    }catch(error){
      if(!controller.signal.aborted&&valid()&&group.open){
        group.__failedSlot=slot;
        const retry=group.__retry=document.createElement('button');
        retry.type='button';retry.className='plain';retry.textContent=t('读取失败，点击重试');retry.title=error.message;
        retry.onclick=()=>{group.__failedSlot=null;load(after);};group.lastChild.append(retry);
      }
    }
    finally{
      if(group.__directoryController===controller){
        group.__directoryController=null;
        if(group.__chatMessage.version!==target.version)group.__syncProcess?.();
      }
    }
  };
  group.__syncProcess=()=>{
    if(!group.open||group.hidden){cancel();return;}
    if(group.__processMode==='live'&&group.__loadedVersion!==group.__chatMessage.version)load('latest');
    if(group.__processMode==='history'&&(group.__hasEarlier||group.__loadedVersion!==group.__chatMessage.version))load();
    let animationChanged=false;
    for(const key of group.__recordKeys){
      const {node,row}=group.__records.get(key);
      const running=group.__processMode==='live'&&group.__canAnimate&&row.status==='inProgress';
      if(node.__chatRunning!==running){node.__chatRunning=running;animationChanged=true;}
    }
    if(animationChanged)paint();
  };
  group.ontoggle=group.__syncProcess;
  if(group.__processSignal!==signal){group.__processSignal?.removeEventListener('abort',group.__abortProcessListener);group.__processSignal=signal;group.__abortProcessListener=cancel;signal?.addEventListener('abort',cancel,{once:true});}
  return group;
}
function groupChatTasks(container,nodes){
  const previous=container.__chatTaskGroups||new Map(),next=new Map(),used=new Set(),result=[],tasks=chatTaskSegments(nodes,container.__chatTaskState);
  const priorDisclosure=new Map([...previous.values()].map(group=>[group.__taskKey,group.__disclosure]));
  const fragments=new Map();
  const add=(group,task)=>{
    if(!fragments.has(task))fragments.set(task,[]);
    fragments.get(task).push(group);group.__taskKey=task.key;
    used.add(group);next.set(group.dataset.key,group);result.push(group);
  };
  let cluster=[],current=null;
  const flush=()=>{
    if(!cluster.length)return;
    const key=cluster[0].dataset.key;
    let group=previous.get(key);
    if(!group||used.has(group))group=[...previous.values()].find(old=>!used.has(old)&&cluster.some(node=>old.__memberKeys?.has(node.dataset.key)));
    if(!group){
      group=document.createElement('details');group.className='chat-activity-group chat-activity-turn';
      const summary=document.createElement('summary'),body=document.createElement('div');body.className='chat-task-items';group.append(summary,body);
    }
    group.__memberKeys=new Set(cluster.map(node=>node.dataset.key));
    group.dataset.key=key;group.hidden=cluster.every(node=>node.hidden);
    // Keep the independently scrollable tool batches inside the task disclosure.
    syncChatMessageNodes(group.lastChild,groupChatActivities(group.lastChild,cluster),false);
    add(group,current);cluster=[];
  };
  for(const node of nodes){
    const row=node.__chatMessage,task=tasks.get(node);
    if(row?.processCursor){flush();add(node,task);current=null;continue;}
    const process=row&&(chatActivityKind(row)||row.role==='assistant'&&!task.finals.has(node))&&!row.fileSummary;
    if(!process){flush();result.push(node);current=null;continue;}
    if(current&&current!==task)flush();current=task;cluster.push(node);
  }
  flush();
  for(const [task,groups] of fragments){
    const prior=priorDisclosure.get(task.key);
    const open=task.active||Boolean(prior?.open&&!(prior.wasLive&&task.terminal));
    const disclosure={open,wasLive:task.active||Boolean(prior?.wasLive&&!task.terminal)};
    const heading=groups.find(group=>!group.hidden);
    // Refresh just the current fragment. Previous fragments remain in place
    // and complete their own historical pages when the reader opens the turn.
    const latest=task.active?[...groups].reverse().find(group=>!group.hidden&&group.__chatMessage?.processCursor):null;
    for(const group of groups){
      group.__disclosure=disclosure;group.open=open;
      group.dataset.state=task.active?'active':'complete';
      group.classList.toggle('chat-process-continuation',group!==heading);
      group.__processMode=disclosure.wasLive?(group===latest?'live':null):'history';
      group.__canAnimate=!container.__chatTaskState?.requests?.length;
      const summary=group.firstChild;
      summary.hidden=group!==heading||task.active;
      const label=group===heading?chatTaskLabel(task):'';
      if(summary.textContent!==label)summary.textContent=label;
      summary.onclick=event=>{
        event.preventDefault();
        if(summary.hidden)return;
        disclosure.open=!disclosure.open;
        for(const peer of groups){peer.open=disclosure.open;peer.__syncProcess?.();}
      };
    }
  }
  for(const [key,group] of previous)if(!next.has(key)&&!used.has(group))group.__cancelProcessReads?.();
  container.__chatTaskGroups=next;return result;
}
// Consecutive tools and reasoning share the second disclosure level. Each
// native tool still owns its original third-level detail and lazy body.
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
    const icons=cluster.map(node=>node.dataset.icon),files=icons.includes('file'),commands=icons.includes('command');
    const reasoningOnly=cluster.every(node=>node.dataset.chatActivity==='reasoning');
    const label=files&&commands?'编辑了文件，运行了命令':files?'编辑了文件':commands?'运行了命令':reasoningOnly?'思考摘要':'执行了操作';
    const translated=typeof BridgeI18n==='undefined'?label:BridgeI18n.t(label);
    group.dataset.running=String(cluster.some(node=>node.__chatRunning));
    group.dataset.icon=files?'file':commands?'command':icons.every(icon=>icon===icons[0])?icons[0]:'tool';
    if(group.firstChild.firstChild.textContent!==translated)group.firstChild.firstChild.textContent=translated;
    group.__rows=cluster;syncChatActivityPage(group);result.push(group);cluster=[];
  };
  for(const node of nodes){
    if(['tool','reasoning'].includes(node.dataset?.chatActivity)){
      if(cluster.length&&node.dataset.chatTurnId&&cluster[0].dataset.chatTurnId&&node.dataset.chatTurnId!==cluster[0].dataset.chatTurnId)flush();
      cluster.push(node);
    }else{flush();result.push(node);}
  }
  flush();container.__chatActivityGroups=next;return result;
}
// Stable row keys are local to this conversation container. Do not detach
// unchanged rows: even reattaching the same node resets nested scroll areas.
function syncChatMessageNodes(container,nodes,group=true){
  if(group)nodes=groupChatTasks(container,nodes);
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
  if(group){
    for(const node of nodes){
      if(node.__disclosure)node.open=node.__disclosure.open;
      node.__syncProcess?.();
    }
  }
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
