'use strict';
let creationGeneration=0, managementGeneration=0, managementTarget=null, managementBusy=false;
function pendingNewChat(){try{return JSON.parse(sessionStorage.getItem('new-chat-request-v1')||'null');}catch{return null;}}
let connectionBusy=false, connectionRetryTimer=null;
let connectionState='idle', diagnosticReport='', diagnosticRequested=false;
function connectionStatus(status,message){
  connectionState=status;
  const badge=$('home-connection');badge.dataset.state=status;badge.hidden=status==='idle';
  badge.title=message;badge.setAttribute('aria-label',message+'，查看连接诊断');
  badge.querySelector('span').textContent={connecting:'连接中',connected:'已连接',failed:'未连接'}[status]||'';
  notifyNativeChatHeader(!$('app').hidden&&$('app').classList.contains('chat-open'));
}
function scheduleConnectionRetry(){
  clearTimeout(connectionRetryTimer);
  if(!$('app').hidden&&navigator.onLine!==false)connectionRetryTimer=setTimeout(()=>{if(!document.hidden)diagnoseConnection();},5000);
}
function networkFailure(error){
  if(error?.name==='AbortError')return;
  connectionStatus('failed',navigator.onLine===false?'网络已断开，恢复后将自动连接':'连接中断，正在等待重连；请确认电脑和连接地址可用');
  scheduleConnectionRetry();
}
function networkRestored(){
  if(!connectionBusy&&connectionState==='failed'&&!$('app').hidden)scheduleConnectionRetry();
}
async function diagnoseConnection(manual=false){
  if(manual===true){
    diagnosticRequested=true;
    $('diagnostics-retry').disabled=true;$('diagnostics-copy').disabled=true;
    $('diagnostics-output').textContent='正在诊断…';
  }
  if(connectionBusy||$('app').hidden)return;
  clearTimeout(connectionRetryTimer);connectionBusy=true;
  const started=Date.now();
  connectionStatus('connecting','正在连接电脑…');
  try{
    const result=await api('/api/diagnostics');
    if($('app').hidden)return;
    if(result.gateway==='online'&&result.desktop==='available')connectionStatus('connected','连接成功 · 电脑通道已就绪');
    else{connectionStatus('failed',result.message||'电脑通道未连接，将自动重试');scheduleConnectionRetry();}
    if(diagnosticRequested)setDiagnosticReport({gateway:result.gateway,desktop:result.desktop,message:result.message},started);
  }catch(error){
    if(!$('app').hidden)connectionStatus('failed',error.message||'连接失败，将自动重试');
    if(diagnosticRequested)setDiagnosticReport({errorCode:error.code||'unknown',message:error.message||'诊断失败'},started);
    if(error.code!=='http_401')scheduleConnectionRetry();
  }finally{connectionBusy=false;diagnosticRequested=false;$('diagnostics-retry').disabled=false;$('diagnostics-copy').disabled=!diagnosticReport;}
}
function setDiagnosticReport(result,started){
  diagnosticReport=JSON.stringify({time:new Date().toISOString(),endpoint:location.origin+'/api/diagnostics',network:navigator.onLine?'online':'offline',durationMs:Date.now()-started,...result},null,2);
  $('diagnostics-output').textContent=diagnosticReport;
  $('diagnostics-summary').textContent=result.message||'诊断完成';
}
function openConnectionDiagnostics(){
  $('home-menu').close();
  if(!$('diagnostics-dialog').open)$('diagnostics-dialog').showModal();
  $('diagnostics-summary').textContent='检查助手入口与电脑通道，结果可直接复制。';
  if($('app').hidden){setDiagnosticReport({errorCode:'login_required',message:'请先登录助手，再检查电脑通道。'},Date.now());$('diagnostics-copy').disabled=false;return;}
  void diagnoseConnection(true);
}
function requestLogout(){
  $('home-menu').close();$('logout-error').textContent='';
  if(!$('logout-dialog').open)$('logout-dialog').showModal();
}
let nativeHomeMenu=false;
window.openAssistantMenu=(options={})=>{
  const menu=$('home-menu');if(menu.open){menu.close();return true;}
  nativeHomeMenu=!!options.native;
  menu.classList.toggle('native-home-menu',nativeHomeMenu);
  menu.querySelectorAll('[data-native-action]').forEach(button=>button.hidden=!nativeHomeMenu);
  $('home-notifications').hidden=typeof options.notifications!=='boolean';
  $('home-notifications').querySelector('span').textContent=(options.notifications?'关闭':'开启')+(options.notificationKind==='system'?'系统任务通知':'后台任务通知');
  $('logout').hidden=$('app').hidden;
  menu.style.top=nativeHomeMenu?'8px':Math.round($('home-menu-toggle').getBoundingClientRect().bottom+6)+'px';
  menu.style.right=nativeHomeMenu?'12px':Math.max(12,innerWidth-$('home-menu-toggle').getBoundingClientRect().right)+'px';
  $('home-menu-toggle').setAttribute('aria-expanded','true');menu.showModal();return true;
};
function nativeMenuAction(action){
  $('home-menu').close();
  if(window.webkit?.messageHandlers?.assistantChrome)window.webkit.messageHandlers.assistantChrome.postMessage({action});
  else if(window.assistantAndroidChrome)window.assistantAndroidChrome.postMessage(JSON.stringify({action}));
}
function renderState(view){
  observeTask(view);
  renderStateBase(view);
  const failure=view.latestError;
  $('open-desktop').hidden=!!view.connected;
  $('inspect-error').hidden=!failure;$('error-model').hidden=!failure;
  $('reconnect').hidden=!!view.connected;
  if(failure&&view.connected){
    $('notice').hidden=false;
    $('notice-text').textContent=describeChatError(failure).title+'。下方可查看说明或继续任务。';
  }
  if(!view.connected&&desktopReconnects.has(chatKey()))$('notice-text').textContent='正在打开桌面聊天并自动连接，请稍候…';
}

const desktopReconnects=new Map();
function openDesktopAndReconnect(id,host){
  const key=chatKey(id,host);
  if(desktopReconnects.has(key))return desktopReconnects.get(key);
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
  const operation=(async()=>{
    try{
      await api(sessionUrl(id,'open',host),{},controller.signal);
      while(!controller.signal.aborted){
        const info=await api(sessionUrl(id,'info',host),undefined,controller.signal);
        if(info.connected)return info;
        await api(sessionUrl(id,'reconnect',host),{progressive:true},controller.signal);
        await new Promise(resolve=>setTimeout(resolve,1500));
      }
      throw Error('自动连接超时，请确认电脑上的 Codex 已打开，再重试。');
    }catch(error){
      if(controller.signal.aborted)throw Error('自动连接超时，请确认电脑上的 Codex 已打开，再重试。');
      throw error;
    }finally{clearTimeout(timer);desktopReconnects.delete(key);}
  })();
  desktopReconnects.set(key,operation);return operation;
}

async function openDesktopFromButton(button,id,host,errorNode){
  if(button.disabled)return;
  const label=button.textContent;button.disabled=true;button.textContent='正在自动连接…';
  if(errorNode)errorNode.textContent='';
  try{await openDesktopAndReconnect(id,host);toast('桌面聊天已连接');}
  catch(error){if(errorNode)errorNode.textContent=error.message;else toast(error.message);}
  finally{button.disabled=false;button.textContent=label;}
}
const PROJECTLESS_KEY='local|projectless';
let creationLocked=false;
function selectedNewChatProject(){
  return $('new-chat-temporary').checked?PROJECTLESS_KEY:$('new-chat-project').value;
}
function syncNewChatMode(){
  const temporary=$('new-chat-temporary').checked;
  $('new-chat-project-fields').hidden=temporary;
  $('new-chat-project').disabled=creationLocked||temporary;
  $('new-chat-project').required=!temporary;
  $('new-chat-temporary').disabled=creationLocked;
  $('new-chat-in-project').disabled=creationLocked||!newChatProjects.some(p=>p.key!==PROJECTLESS_KEY);
  $('new-chat-path').textContent=temporary?'独立开始，不关联项目；聊天记录会保留。':
    newChatProjects.find(p=>p.key===$('new-chat-project').value)?.cwd||'请选择一个可用项目。';
  $('new-chat-submit').disabled=creationLocked||!newChatProjects.some(p=>p.key===selectedNewChatProject());
}
function setNewChatTarget(key=PROJECTLESS_KEY){
  const temporary=key===PROJECTLESS_KEY;
  $('new-chat-temporary').checked=temporary;$('new-chat-in-project').checked=!temporary;
  if(!temporary)$('new-chat-project').value=key;
  syncNewChatMode();
}
function creationControls(locked){
  creationLocked=locked;$('new-chat-title').disabled=locked;syncNewChatMode();
  $('new-chat-another').hidden=!locked;
}
function showCreationStatus(value){
  $('new-chat-progress').textContent=value.status==='failed'?'':value.message||'正在处理…';
  $('new-chat-submit').textContent=value.status==='failed'?'重试创建':'创建对话';
  $('new-chat-progress').dataset.status=value.status;
  $('new-chat-recover').hidden=!['unknown','paused'].includes(value.status);
  if(value.status==='failed'||value.status==='missing'){
    creatingChat=false;creationControls(false);$('new-chat-error').textContent=value.status==='failed'?value.message:'';
  }
}
async function finishCreation(result){
  creationGeneration++;creatingChat=false;sessionStorage.removeItem('new-chat-request-v1');
  $('new-chat-dialog').close();creationControls(false);
  clearTimeout(loadList.timer);$('search').value='';$('archived').checked=false;
  if(result.warning)toast(result.warning);
  const listing=loadList(true).catch(error=>toast(error.message));
  await openChat(result.id,result.host).catch(error=>toast('对话已创建，连接失败：'+error.message));
  await listing;
}
async function watchCreation(request,initial=null){
  const generation=++creationGeneration;
  setNewChatTarget(request.projectKey);
  creatingChat=true;creationControls(true);$('new-chat-error').textContent='';
  const deadline=Date.now()+100000;
  try{
    let value=initial;
    while(generation===creationGeneration&&$('new-chat-dialog').open){
      value=value||await api('/api/creations/'+request.id);
      if(generation!==creationGeneration||!$('new-chat-dialog').open)return;
      showCreationStatus(value);
      if(value.status==='ready'&&value.result){await finishCreation(value.result);return;}
      if(['failed','missing','unknown','deleted'].includes(value.status)){
        creatingChat=false;
        if(value.status==='deleted'){sessionStorage.removeItem('new-chat-request-v1');creationControls(false);}
        return;
      }
      if(Date.now()>deadline){showCreationStatus({status:'paused',message:'仍在处理中，可点击“找回对话”继续查看。'});creatingChat=false;return;}
      await new Promise(resolve=>setTimeout(resolve,1200));value=null;
    }
  }catch(error){
    if(generation===creationGeneration){creatingChat=false;$('new-chat-error').textContent=error.message;showCreationStatus({status:'paused',message:'连接中断。恢复后可找回本次创建结果。'});}
  }
}
async function openNewChat(){
  if($('new-chat-dialog').open)return;
  const generation=++creationGeneration;creatingChat=false;
  $('new-chat-error').textContent='';$('new-chat-progress').textContent='';$('new-chat-path').textContent='';
  $('new-chat-recover').hidden=true;$('creation-history').replaceChildren();
  newChatProjects=[];
  $('new-chat-project').replaceChildren(new Option('正在读取项目…',''));setNewChatTarget();creationControls(true);
  $('new-chat-dialog').showModal();
  const pending=pendingNewChat();$('new-chat-title').value=pending?.title||'';
  try{
    const result=await api('/api/projects');
    if(!$('new-chat-dialog').open||generation!==creationGeneration)return;
    newChatProjects=result.projects||[];
    const projects=newChatProjects.filter(p=>p.key!==PROJECTLESS_KEY);
    $('new-chat-project').replaceChildren(...projects.map(p=>new Option(p.name+' · '+p.hostLabel,p.key)));
    const current=listRows.find(r=>r.id===currentId&&r.host===currentHost);
    if(projects.some(p=>p.key===current?.projectKey))$('new-chat-project').value=current.projectKey;
    setNewChatTarget(pending?.projectKey||PROJECTLESS_KEY);creationControls(false);
    if(pending)await watchCreation(pending);
  }catch(error){$('new-chat-error').textContent=error.message;}
}
async function submitNewChat(event){
  event.preventDefault();if(creatingChat)return;
  const projectKey=selectedNewChatProject(),title=$('new-chat-title').value.trim();
  if(!newChatProjects.some(p=>p.key===projectKey))return;
  let request=pendingNewChat();
  if(!request||request.projectKey!==projectKey||request.title!==title)request={id:uuid(),projectKey,title};
  sessionStorage.setItem('new-chat-request-v1',JSON.stringify(request));
  const generation=++creationGeneration;
  creatingChat=true;creationControls(true);$('new-chat-error').textContent='';
  showCreationStatus({status:'pending',message:'正在提交创建请求…'});
  try{
    const status=await api('/api/creations',request);
    if(generation===creationGeneration&&$('new-chat-dialog').open)await watchCreation(request,status);
  }catch(error){if(generation===creationGeneration){creatingChat=false;$('new-chat-error').textContent=error.message;showCreationStatus({status:'paused',message:'提交结果未确认，可找回本次请求。'});}}
}
async function recoverCreation(){
  const request=pendingNewChat();if(!request)return;
  const generation=++creationGeneration;
  $('new-chat-recover').disabled=true;
  try{const status=await api('/api/creations/'+request.id,{action:'recover'});if(generation===creationGeneration&&$('new-chat-dialog').open)await watchCreation(request,status);}
  catch(error){if(generation===creationGeneration)$('new-chat-error').textContent=error.message;}
  finally{$('new-chat-recover').disabled=false;}
}
async function recentCreations(){
  $('creation-history').textContent='正在读取…';
  try{
    const value=await api('/api/creations');$('creation-history').replaceChildren();
    for(const item of value.creations){
      const button=el('button','plain creation-record',item.title+' · '+item.message);button.type='button';
      button.onclick=()=>{const request={id:item.requestId,projectKey:item.projectKey,title:item.title};sessionStorage.setItem('new-chat-request-v1',JSON.stringify(request));$('new-chat-title').value=item.title;setNewChatTarget(item.projectKey);recoverCreation();};
      $('creation-history').append(button);
    }
    if(!value.creations.length)$('creation-history').textContent='暂无新建记录';
  }catch(error){$('creation-history').textContent=error.message;}
}
async function openChatMenu(id,host){
  if(managementBusy)return;
  const generation=++managementGeneration;
  managementTarget=null;$('manage-error').textContent='';$('delete-confirmation').hidden=true;
  $('rename-form').hidden=true;$('manage-rename').setAttribute('aria-expanded','false');$('manage-context').textContent='正在读取…';$('manage-title').value='';$('delete-word').value='';
  const controls=$('manage-dialog').querySelectorAll('button,input');controls.forEach(n=>n.disabled=true);
  $('manage-dialog').showModal();
  try{
    const info=await api(sessionUrl(id,'info',host));
    if(!$('manage-dialog').open||generation!==managementGeneration)return;
    managementTarget={...info,id,host};$('manage-title').value=info.title;$('manage-context').textContent=info.title;
    $('manage-pin').textContent=info.pinned?'取消手机置顶':'手机置顶';
    $('manage-archive').textContent=info.archived?'恢复归档':'归档对话';$('manage-delete').hidden=!info.archived;
    controls.forEach(n=>n.disabled=false);
    $('manage-copy-path').disabled=!info.cwd;
  }catch(error){if(generation===managementGeneration){$('manage-error').textContent=error.message;$('manage-close').disabled=false;}}
}
async function manageChat(action){
  if(!managementTarget||managementBusy)return;
  const target={...managementTarget},body={action};
  if(action==='rename'){body.title=$('manage-title').value.trim();if(!body.title){$('manage-error').textContent='请输入标题';return;}}
  if(action==='pin')body.pinned=!target.pinned;
  if(action==='delete'){if($('delete-word').value!=='删除'){$('manage-error').textContent='请输入“删除”确认';return;}body.confirmId=target.id;}
  managementBusy=true;$('manage-error').textContent='';
  const controls=$('manage-dialog').querySelectorAll('button,input');controls.forEach(n=>n.disabled=true);
  try{
    const result=await api(sessionUrl(target.id,'manage',target.host),body);$('manage-dialog').close();
    if(['archive','delete'].includes(action)&&currentId===target.id&&currentHost===target.host){
      disposeChatTimeline();currentId=null;state=null;$('chat').hidden=true;$('welcome').hidden=false;$('app').classList.remove('chat-open');history.replaceState(null,'',location.pathname);
    }
    if(action==='rename'&&currentId===target.id&&currentHost===target.host)$('chat-title').textContent=body.title;
    await loadList(true);toast(result.warning||'已完成');
  }catch(error){$('manage-error').textContent=error.message;}
  finally{managementBusy=false;controls.forEach(n=>n.disabled=false);}
}
function initImprovements(){
  $('new-chat').onclick=openNewChat;$('chat-new').onclick=()=>{$('manage-dialog').close();openNewChat();};$('welcome-new').onclick=openNewChat;
  $('new-chat-form').onsubmit=submitNewChat;
  $('new-chat-project').onchange=syncNewChatMode;
  $('new-chat-temporary').onchange=$('new-chat-in-project').onchange=syncNewChatMode;
  $('new-chat-dialog').addEventListener('close',()=>{creationGeneration++;creatingChat=false;});
  $('new-chat-recover').onclick=recoverCreation;$('new-chat-history').onclick=recentCreations;
  $('new-chat-another').onclick=()=>{creationGeneration++;creatingChat=false;sessionStorage.removeItem('new-chat-request-v1');setNewChatTarget();creationControls(false);$('new-chat-error').textContent='';$('new-chat-progress').textContent='上一次请求保留在“最近新建记录”中。';$('new-chat-recover').hidden=true;$('new-chat-title').value='';};
  $('chat-menu').onclick=()=>{if(currentId)openChatMenu(currentId,currentHost);};
  $('manage-rename').onclick=()=>{const open=$('rename-form').hidden;$('rename-form').hidden=!open;$('manage-rename').setAttribute('aria-expanded',String(open));if(open)$('manage-title').focus();};
  $('rename-form').onsubmit=event=>{event.preventDefault();manageChat('rename');};
  $('manage-pin').onclick=()=>manageChat('pin');$('manage-archive').onclick=()=>manageChat(managementTarget?.archived?'unarchive':'archive');
  $('manage-copy-path').onclick=()=>copyProjectPath(managementTarget?.cwd,$('manage-copy-path'));
  $('manage-delete').onclick=()=>{$('delete-confirmation').hidden=false;$('delete-word').focus();};
  $('delete-submit').onclick=()=>manageChat('delete');
  $('manage-dialog').addEventListener('cancel',event=>{if(managementBusy)event.preventDefault();});
  $('manage-dialog').addEventListener('close',()=>{managementGeneration++;});
  $('manage-open').onclick=()=>{if(managementTarget)openDesktopFromButton($('manage-open'),managementTarget.id,managementTarget.host,$('manage-error'));};
  $('open-desktop').onclick=()=>{if(currentId)openDesktopFromButton($('open-desktop'),currentId,currentHost);};
  $('inspect-error').onclick=()=>{readTaskReminders(currentId,currentHost);Array.from(document.querySelectorAll('#messages .message.error')).at(-1)?.scrollIntoView({behavior:'smooth',block:'center'});};
  $('error-model').onclick=()=>$('model-button').click();
  $('diagnose').onclick=openConnectionDiagnostics;$('home-connection').onclick=openConnectionDiagnostics;
  $('diagnostics-retry').onclick=openConnectionDiagnostics;
  $('diagnostics-copy').onclick=async()=>{
    try{await navigator.clipboard.writeText(diagnosticReport);$('diagnostics-copy').textContent='已复制';setTimeout(()=>$('diagnostics-copy').textContent='复制诊断结果',2000);}
    catch{await copyChatText(diagnosticReport);}
  };
  $('home-menu-toggle').onclick=()=>window.openAssistantMenu();
  const homeMenu=$('home-menu');
  homeMenu.addEventListener('close',()=>$('home-menu-toggle').setAttribute('aria-expanded','false'));
  homeMenu.addEventListener('click',event=>{if(event.target!==homeMenu)return;const rect=homeMenu.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)homeMenu.close();});
  homeMenu.querySelectorAll('[data-native-action]').forEach(button=>button.onclick=()=>nativeMenuAction(button.dataset.nativeAction));
  $('home-reload').onclick=()=>{homeMenu.close();if(nativeHomeMenu)nativeMenuAction('reload');else if(confirm('重新加载会重建当前页面，是否继续？')){saveDraft();location.reload();}};
  window.addEventListener('offline',()=>networkFailure());window.addEventListener('online',diagnoseConnection);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('app').hidden)diagnoseConnection();});
}
