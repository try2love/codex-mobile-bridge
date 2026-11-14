'use strict';
const switcherRows=new Map();
let switcherSeen={},switcherReady=false,switcherBusy=false,switcherError='',switcherAccount='',switcherFilter='all';
try{switcherSeen=JSON.parse(sessionStorage.getItem('conversation-seen-v1')||'{}');}catch{}
function saveSwitcherSeen(){try{sessionStorage.setItem('conversation-seen-v1',JSON.stringify(switcherSeen));}catch{}}
function switcherErrorOnly(row){return row.phase==='attention'&&row.error&&!row.requests?.length&&!row.actionKind;}
function switcherNeedsAttention(row){return row.phase==='attention'&&(!switcherErrorOnly(row)||switcherSeen[chatKey(row.id,row.host)]!==row.token);}
function markSwitcherRead(id,host){
 const row=switcherRows.get(chatKey(id,host));
 if(row&&(row.phase==='completed'||switcherErrorOnly(row))){switcherSeen[chatKey(id,host)]=row.token;saveSwitcherSeen();}
 renderConversationSwitcher();
}
function clearConversationSwitcher(){
 clearTaskReminders();
 switcherRows.clear();switcherSeen={};switcherReady=false;switcherError='';switcherAccount='';
 $('conversation-switcher')?.close();renderConversationSwitcher();
}
async function pollConversationSwitcher(){
 if(switcherBusy||!csrf||$('app').hidden||document.hidden||navigator.onLine===false)return;
 const token=csrf;switcherBusy=true;
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{
  const response=await fetch('/api/task-status?switcher=true',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
  if(!response.ok)throw Error(response.status===401?'请重新登录后查看对话':'暂时无法获取对话状态');
  const data=await response.json();if(token!==csrf||$('app').hidden)return;
  if(!Array.isArray(data.tasks))throw Error('对话状态数据无效');
  if(switcherAccount&&switcherAccount!==token)clearConversationSwitcher();
  const first=!switcherReady;switcherAccount=token;
  switcherRows.clear();
  for(const task of data.tasks){
   if(!task.id||!task.host||!['attention','running','completed'].includes(task.phase))continue;
   const key=chatKey(task.id,task.host);
   switcherRows.set(key,task);
   if(task.phase==='completed'&&((first&&!Object.hasOwn(switcherSeen,key))||isTaskOpen(task.id,task.host)))switcherSeen[key]=task.token;
   if(switcherErrorOnly(task)&&isTaskOpen(task.id,task.host))switcherSeen[key]=task.token;
   if(task.connected)observeTask({connected:true,title:task.title,status:task.status,phase:task.phase,turnId:String(task.token||'').split(':')[0],actionKind:task.actionKind||(task.detail==='等你回复'?'reply':''),requests:(task.requests||[]).map(id=>({id})),latestError:task.error},task.id,task.host);
  }
  switcherReady=true;saveSwitcherSeen();
  switcherError=(data.unavailableHosts||[]).length?'部分电脑暂时无法连接，列表可能不完整。':'';
 }catch(error){if(token===csrf&&!$('app').hidden)switcherError=error.name==='AbortError'?'获取状态超时，稍后自动重试。':error.message;}
 finally{clearTimeout(timer);switcherBusy=false;if(token===csrf)renderConversationSwitcher();}
}
function positionConversationSwitcher(){
 const dialog=$('conversation-switcher');if(!dialog?.open)return;
 const top=Math.max(8,Math.min(document.querySelector('.chat-head').getBoundingClientRect().bottom+6,window.innerHeight-180));
 dialog.style.top=top+'px';dialog.style.maxHeight=Math.max(120,window.innerHeight-top-16)+'px';
}
function switcherStateLabel(row){
 if(switcherErrorOnly(row)&&!switcherNeedsAttention(row))return '';
 if(row.phase==='attention')return row.actionKind==='reply'||row.detail==='等你回复'?'待回复':row.actionKind==='approval'?'待授权':row.error?'需查看':'待确认';
 if(row.phase==='running')return '运行中';
 if(row.phase==='completed')return switcherSeen[chatKey(row.id,row.host)]!==row.token?'新完成':'已完成';
 return row.detail||'已连接';
}
function makeSwitcherRow(row,current=false){
 const key=chatKey(row.id,row.host),button=el('button','switcher-row'+(current?' current':''));button.type='button';button.dataset.switchKey=key;
 if(current)button.setAttribute('aria-current','page');
 const icon=el('span','switcher-row-icon');icon.setAttribute('aria-hidden','true');
 const bubble=document.querySelector('.chat-topic-icon').cloneNode(true);bubble.setAttribute('class','switcher-topic-icon');icon.append(bubble);
 const copy=el('span','switcher-row-copy'),project=row.projectName||row.hostLabel||'此电脑';
 copy.append(el('strong','',row.title||'未命名聊天'),el('small','',current?'当前对话 · '+project:project));
 const label=switcherStateLabel(row),tag=el('span','switcher-state-tag '+(row.phase||'idle'),label);
 tag.hidden=!label;
 tag.classList.toggle('unread',row.phase==='completed'&&label==='新完成');
 if(current&&row.phase==='running'){tag.textContent='';tag.setAttribute('aria-label','运行中');}
 button.append(icon,copy,tag);
 button.onclick=async()=>{
  $('conversation-switcher').close();if(current){markSwitcherRead(row.id,row.host);return;}
  try{await openChat(row.id,row.host);markSwitcherRead(row.id,row.host);}catch(error){toast(error.message);}
 };
 return button;
}
function renderConversationSwitcher(){
 const trigger=$('chat-switcher'),dialog=$('conversation-switcher');if(!trigger||!dialog)return;
 const rows=[...switcherRows.values()],pending=rows.filter(switcherNeedsAttention).length;
 const unread=rows.filter(r=>r.phase==='completed'&&switcherSeen[chatKey(r.id,r.host)]!==r.token).length;
 const badge=$('chat-switcher-badge');badge.hidden=!pending&&!unread;badge.textContent=pending?(pending>99?'99+':String(pending)):'';
 badge.classList.toggle('completion-dot',!pending&&!!unread);
 trigger.setAttribute('aria-label','切换对话'+(pending?'，'+pending+' 条需要处理':'')+(unread?'，'+unread+' 条完成后未查看':''));
 if(!dialog.open)return;
 const list=$('conversation-switcher-list'),scroll=list.scrollTop,focused=document.activeElement?.dataset.switchKey;
 const countByPhase={all:rows.length,attention:pending,running:rows.filter(r=>r.phase==='running').length,completed:rows.filter(r=>r.phase==='completed').length};
 for(const button of $('conversation-switcher-filters').querySelectorAll('button')){
  button.textContent=button.dataset.label+' '+countByPhase[button.dataset.filter];
  button.setAttribute('aria-pressed',String(button.dataset.filter===switcherFilter));
 }
 const current=switcherRows.get(chatKey(currentId,currentHost))||(currentId?{id:currentId,host:currentHost,title:state?.title||$('chat-title').textContent,projectName:listRows.find(r=>r.id===currentId&&r.host===currentHost)?.projectName,detail:state?.connected?'已连接':'连接中'}:null);
 const currentPanel=$('conversation-switcher-current');currentPanel.replaceChildren();currentPanel.hidden=!current;
 if(current)currentPanel.append(makeSwitcherRow(current,true));
 list.replaceChildren();
 const order={attention:0,running:1,completed:2};
 const rank=row=>switcherErrorOnly(row)&&!switcherNeedsAttention(row)?3:order[row.phase];
 const visible=rows.filter(row=>(row.id!==currentId||row.host!==currentHost)&&(switcherFilter==='all'||(switcherFilter==='attention'?switcherNeedsAttention(row):row.phase===switcherFilter))).sort((a,b)=>rank(a)-rank(b)||(b.at||0)-(a.at||0));
 for(const row of visible)list.append(makeSwitcherRow(row));
 if(!visible.length)list.append(el('p','switcher-empty',!switcherReady?'正在读取对话状态…':switcherFilter==='all'?'暂无其他进行中、已完成或需要处理的对话。':'此分类暂无其他对话。'));
 $('conversation-switcher-note').textContent=switcherError||'已完成显示最近 20 条';
 list.scrollTop=scroll;
 if(focused){const target=[...dialog.querySelectorAll('[data-switch-key]')].find(b=>b.dataset.switchKey===focused);target?.focus({preventScroll:true});}
 positionConversationSwitcher();
}
function initConversationSwitcher(){
 const info=document.querySelector('.chat-info'),trigger=el('button','chat-switcher-hit');trigger.id='chat-switcher';trigger.type='button';trigger.setAttribute('aria-label','切换对话');trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-controls','conversation-switcher');
 const icon=document.querySelector('.chat-topic-icon'),mark=el('span','chat-topic-mark');icon.before(mark);mark.append(icon);
 const badge=el('span','chat-switcher-badge');badge.id='chat-switcher-badge';badge.hidden=true;badge.setAttribute('aria-hidden','true');mark.append(badge);info.append(trigger);
 const dialog=el('dialog','conversation-switcher');dialog.id='conversation-switcher';dialog.setAttribute('aria-labelledby','conversation-switcher-title');
 const header=el('header','switcher-head'),title=el('h2','','切换对话');title.id='conversation-switcher-title';
 const close=el('button','switcher-close','×');close.type='button';close.setAttribute('aria-label','关闭对话切换');close.onclick=()=>dialog.close();header.append(title,close);
 const filters=el('div','switcher-filters');filters.id='conversation-switcher-filters';filters.setAttribute('role','group');filters.setAttribute('aria-label','对话状态筛选');
 for(const [value,label] of [['all','全部'],['attention','待处理'],['running','进行中'],['completed','已完成']]){
  const button=el('button','switcher-filter',label+' 0');button.type='button';button.dataset.filter=value;button.dataset.label=label;
  button.setAttribute('aria-pressed',String(value===switcherFilter));button.setAttribute('aria-controls','conversation-switcher-list');
  button.onclick=()=>{switcherFilter=value;$('conversation-switcher-list').scrollTop=0;renderConversationSwitcher();};filters.append(button);
 }
 const current=el('div','switcher-current-panel');current.id='conversation-switcher-current';current.hidden=true;
 const list=el('div','switcher-list');list.id='conversation-switcher-list';
 const note=el('p','switcher-note');note.id='conversation-switcher-note';note.setAttribute('role','status');dialog.append(header,filters,current,list,note);document.body.append(dialog);
 trigger.onclick=()=>{if(dialog.open){dialog.close();return;}switcherFilter='all';dialog.showModal();trigger.setAttribute('aria-expanded','true');info.classList.add('switcher-open');renderConversationSwitcher();void pollConversationSwitcher();};
 dialog.addEventListener('close',()=>{trigger.setAttribute('aria-expanded','false');info.classList.remove('switcher-open');});
 dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
 window.addEventListener('resize',positionConversationSwitcher);window.visualViewport?.addEventListener('resize',positionConversationSwitcher);
 renderConversationSwitcher();
}
