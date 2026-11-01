'use strict';
// Attachment drafts are scoped to this browser tab's login session.
let draftDBPromise, draftWrites=Promise.resolve(), draftWarning=false;
function draftNamespace(){let value=sessionStorage.getItem('attachment-namespace');if(!value){value=uuid();sessionStorage.setItem('attachment-namespace',value);}return value;}
function draftDB(){
  if(!draftDBPromise)draftDBPromise=new Promise((resolve,reject)=>{
    const request=indexedDB.open('codex-mobile-drafts',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('attachments');
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
    request.onblocked=()=>reject(Error('草稿存储被占用'));
  });return draftDBPromise;
}
function draftFailure(){if(!draftWarning){draftWarning=true;toast('附件草稿无法保存到浏览器，刷新前请先发送或保留原文件。');}}
function persistImageDraft(key=chatKey()){
  let record;try{record=draftNamespace()+':'+key;}catch{draftFailure();return Promise.resolve();}
  const images=[...(imageDrafts.get(key)||[])];
  draftWrites=draftWrites.catch(()=>{}).then(async()=>{
    const db=await draftDB();await new Promise((resolve,reject)=>{
      const tx=db.transaction('attachments','readwrite'),store=tx.objectStore('attachments');
      if(images.length)store.put({images,at:Date.now()},record);else store.delete(record);
      tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
    });
  }).catch(draftFailure);return draftWrites;
}
async function restoreImageDraft(key){
  if(imageDrafts.has(key))return;
  try{
    const record=draftNamespace()+':'+key;await draftWrites;const db=await draftDB();
    const value=await new Promise((resolve,reject)=>{const req=db.transaction('attachments').objectStore('attachments').get(record);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});
    if(!imageDrafts.has(key))imageDrafts.set(key,Array.isArray(value?.images)?value.images:[]);
  }catch{draftFailure();}
}
async function clearAttachmentDrafts(){
  const prefix=draftNamespace()+':';await draftWrites;
  try{const db=await draftDB();await new Promise((resolve,reject)=>{
    const tx=db.transaction('attachments','readwrite'),store=tx.objectStore('attachments'),req=store.openCursor();
    req.onsuccess=()=>{const cursor=req.result;if(!cursor)return;if(String(cursor.key).startsWith(prefix))cursor.delete();cursor.continue();};
    tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);
  });}catch{draftFailure();}
}

const taskWatch=new Map(),taskEvents=[],taskReminderTimers=new Map();
// Updated iOS shells publish this capability; browsers and older IPAs keep in-app reminders.
let nativeTaskNotifications=false;
const pendingTaskNotifications=new Map();
window.setAssistantTaskNotifications=options=>{nativeTaskNotifications=options?.enabled===true;};
window.assistantTaskNotificationResult=(requestId,accepted)=>{
  const pending=pendingTaskNotifications.get(requestId);if(!pending)return;
  clearTimeout(pending.timer);pendingTaskNotifications.delete(requestId);
  if(!accepted){showTaskReminder(pending.item,pending.message);taskBeep();}
};
function dispatchTaskReminder(item,message,eventKey){
  const bridge=window.webkit?.messageHandlers?.assistantChrome;
  if(!nativeTaskNotifications||!bridge){showTaskReminder(item,message);taskBeep();return;}
  const requestId=uuid(),pending={item,message,timer:null};
  pending.timer=setTimeout(()=>window.assistantTaskNotificationResult(requestId,false),5000);
  pendingTaskNotifications.set(requestId,pending);
  try{bridge.postMessage({action:'taskNotification',requestId,id:item.id,host:item.host||'local',
    title:item.title,message,eventKey,sound:taskSound});}
  catch{window.assistantTaskNotificationResult(requestId,false);}
}
window.openAssistantTaskNotification=async target=>{
  if(!target||!csrf||$('app').hidden||document.hidden||!/^[0-9a-f-]{36}$/i.test(target.id)
    ||typeof target.host!=='string'||!target.host)return false;
  try{
    document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
    if(!isTaskOpen(target.id,target.host))await openChat(target.id,target.host);
    readTaskReminders(target.id,target.host);
    $('approvals').querySelector('.approval')?.scrollIntoView({block:'center'});
    return true;
  }catch(error){toast(error.message);return false;}
};

let taskSound=true,taskAudio,taskLastBeep=-Infinity;
try{taskSound=localStorage.getItem('task-sound-v1')!=='off';}catch{}
async function unlockTaskAudio(){
  if(!taskSound||document.hidden)return false;
  try{
    if(!taskAudio||taskAudio.state==='closed'){
      const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return false;
      taskAudio=new Audio();
    }
    if(taskAudio.state!=='running'){
      // Resume and start a silent buffer inside the touch/click that unlocks iOS audio.
      const resumed=taskAudio.resume(),source=taskAudio.createBufferSource();
      source.buffer=taskAudio.createBuffer(1,1,taskAudio.sampleRate);source.connect(taskAudio.destination);
      source.onended=()=>source.disconnect();source.start();await resumed;
    }
    return taskAudio.state==='running';
  }catch{return false;}
}
function taskBeep(preview=false){
  if(!taskSound||document.hidden||!taskAudio||taskAudio.state!=='running')return false;
  const now=performance.now();if(!preview&&now-taskLastBeep<800)return true;
  try{
    const oscillator=taskAudio.createOscillator(),gain=taskAudio.createGain(),start=taskAudio.currentTime;
    oscillator.connect(gain);gain.connect(taskAudio.destination);oscillator.frequency.setValueAtTime(660,start);
    oscillator.frequency.setValueAtTime(880,start+.16);gain.gain.setValueAtTime(.001,start);
    gain.gain.linearRampToValueAtTime(.12,start+.02);gain.gain.exponentialRampToValueAtTime(.001,start+.4);
    oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};oscillator.start(start);oscillator.stop(start+.42);
    taskLastBeep=now;return true;
  }catch{return false;}
}
function updateTaskSoundMenu(){
  const button=$('home-task-sound');if(!button)return;
  button.querySelector('span').textContent='任务提示音：'+(taskSound?'开':'关');
  button.setAttribute('aria-pressed',String(taskSound));
}
function dismissTaskReminder(card){
  clearTimeout(taskReminderTimers.get(card));taskReminderTimers.delete(card);card.remove();
}
function clearTaskReminders(){
  for(const card of taskReminderTimers.keys())dismissTaskReminder(card);
  for(const pending of pendingTaskNotifications.values())clearTimeout(pending.timer);
  pendingTaskNotifications.clear();taskWatch.clear();taskEvents.length=0;
}
function showTaskReminder(item,message){
  const stack=$('task-reminders');if(!stack||document.hidden||$('app').hidden)return;
  for(const card of [...stack.children])if(card.dataset.chatKey===chatKey(item.id,item.host))dismissTaskReminder(card);
  const card=el('section','task-reminder'),copy=el('div','task-reminder-copy'),actions=el('div','task-reminder-actions');
  card.dataset.chatKey=chatKey(item.id,item.host);copy.setAttribute('role','status');
  copy.append(el('strong','',message),el('span','',item.title));
  const open=el('button','task-reminder-open','查看'),close=el('button','task-reminder-close','×');open.type=close.type='button';
  open.setAttribute('aria-label','查看'+item.title);close.setAttribute('aria-label','关闭任务提醒');
  close.onclick=()=>dismissTaskReminder(card);
  open.onclick=async()=>{
    dismissTaskReminder(card);
    try{
      if(!isTaskOpen(item.id,item.host))await openChat(item.id,item.host);
      readTaskReminders(item.id,item.host);
      $('approvals').querySelector('.approval')?.scrollIntoView({block:'center'});
    }catch(error){toast(error.message);}
  };
  actions.append(open,close);card.append(copy,actions);stack.append(card);
  taskReminderTimers.set(card,setTimeout(()=>dismissTaskReminder(card),10000));
  while(stack.children.length>3)dismissTaskReminder(stack.firstElementChild);
}
function observeTask(view,id=currentId,host=currentHost){
  if(!id||!view?.connected||document.hidden||$('app').hidden)return;
  const key=chatKey(id,host),previous=taskWatch.get(key),requests=view.requests||[];
  const lastTurn=view.turns?.[view.turns.length-1],turnId=String(lastTurn?.id||view.turnId||previous?.turnId||'');
  const item={id,host,title:view.title||previous?.title||'聊天',status:view.status,turnId,announced:previous?.announced||new Set()};
  taskWatch.set(key,item);
  const opened=isTaskOpen(id,host);if(opened)readTaskReminders(id,host);
  const completed=view.phase==='completed'||lastTurn?.status==='completed';
  let message='',eventKey='';
  const newRequests=requests.filter(request=>!item.announced.has('request:'+request.id));
  if(newRequests.length){
    eventKey='requests:'+JSON.stringify(newRequests.map(request=>String(request.id)).sort());
    const methods=requests.map(request=>request.method||'');
    message=view.actionKind==='reply'||methods.some(method=>method.includes('requestUserInput'))?'等你回复':
      view.actionKind==='approval'||methods.every(method=>method.includes('requestApproval'))?'等待你的授权':'需要你的确认';
    for(const request of requests)item.announced.add('request:'+request.id);
  }else if(!requests.length&&view.status==='idle'){
    eventKey='end:'+turnId;
    if(previous&&(previous.status==='active'||(turnId!==previous.turnId&&(completed||view.latestError)))&&!item.announced.has(eventKey)){
      message=view.latestError?'任务结束，请查看错误':completed?'任务已完成':'任务已结束';
    }
    // Opening a finished conversation only establishes the initial state.
    if(message||(!previous&&(completed||view.latestError)))item.announced.add(eventKey);
  }
  while(item.announced.size>40)item.announced.delete(item.announced.values().next().value);
  if(!message)return;
  if(!opened){taskEvents.unshift({id,host,title:item.title,message,at:Date.now(),unread:true});taskEvents.splice(30);}
  dispatchTaskReminder(item,message,eventKey);renderTaskReminders();
}
function renderTaskReminders(){
  updatePageTitle();
  document.querySelectorAll('.session').forEach(button=>{
    button.querySelector('.task-unread')?.remove();
    if(taskEvents.some(e=>e.unread&&e.id===button.dataset.id&&e.host===button.dataset.host)){
      const badge=el('span','task-unread','未读');button.append(badge);
    }
  });
}
function isTaskOpen(id,host){
  return !document.hidden&&!$('app').hidden&&$('app').classList.contains('chat-open')&&id===currentId&&host===currentHost;
}
function readTaskReminders(id,host){
  for(let i=taskEvents.length-1;i>=0;i--)if(taskEvents[i].id===id&&taskEvents[i].host===host)taskEvents.splice(i,1);
  renderTaskReminders();
  markSwitcherRead(id,host);
}
function pollWatchedTasks(){return pollConversationSwitcher();}
function initChatExtras(){
  const stack=el('div','task-reminders');stack.id='task-reminders';stack.setAttribute('aria-label','任务提醒');$('app').append(stack);
  const toggle=el('button','home-menu-item'),preview=el('button','home-menu-item');
  toggle.id='home-task-sound';toggle.type=preview.type='button';
  for(const button of [toggle,preview]){
    const icon=$('home-notifications').querySelector('svg').cloneNode(true);button.append(icon,el('span'));
    $('home-notifications').before(button);
  }
  preview.querySelector('span').textContent='试听提示音';
  toggle.onclick=()=>{
    $('home-menu').close();
    taskSound=!taskSound;try{localStorage.setItem('task-sound-v1',taskSound?'on':'off');}catch{}
    notifyNativeChatHeader(!$('app').hidden&&$('app').classList.contains('chat-open'));
    updateTaskSoundMenu();toast(taskSound?'已开启任务提示音':'已关闭任务提示音');
    if(taskSound)void unlockTaskAudio().then(()=>taskBeep(true));
  };
  preview.onclick=()=>{
    $('home-menu').close();
    if(!taskSound){toast('请先开启任务提示音');return;}
    void unlockTaskAudio().then(ready=>{if(!ready||!taskBeep(true))toast('提示音未能播放，请再次点击试听');});
  };
  updateTaskSoundMenu();
  const unlock=event=>{if(event.isTrusted)void unlockTaskAudio();};
  document.addEventListener('touchend',unlock,{capture:true,passive:true});
  document.addEventListener('click',unlock,true);document.addEventListener('keydown',unlock,true);
  setInterval(pollWatchedTasks,5000);
  document.addEventListener('visibilitychange',()=>{
    for(const card of taskReminderTimers.keys())dismissTaskReminder(card);
    if(!document.hidden){if(taskAudio)void unlockTaskAudio();if(isTaskOpen(currentId,currentHost))readTaskReminders(currentId,currentHost);pollWatchedTasks();}
  });
  const progress=el('p','muted');progress.id='attachment-progress';progress.setAttribute('role','status');progress.hidden=true;$('image-previews').after(progress);
  renderTaskReminders();
}
