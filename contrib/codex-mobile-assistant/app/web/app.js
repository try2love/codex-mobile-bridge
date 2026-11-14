'use strict';
const $ = id => document.getElementById(id);
let csrf='', currentId=null, state=null, listOffset=0, listQuery='', approvalStamp='', sending=false;
let currentHost='local', listRows=[], listGeneration=0;
let creatingChat=false, newChatProjects=[];
let chatTimeline=null,chatGeneration=0;
function disposeChatTimeline(){chatGeneration++;chatTimeline?.dispose();chatTimeline=null;}
let preparingImages=false;
const imageDrafts=new Map();
function projectFolderIcon(){const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('class','folder-icon');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.innerHTML='<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>';return icon;}
function notifyNativeChatHeader(open){
  const payload={chat:open,connection:connectionState,authenticated:!!csrf&&!$('app').hidden,sound:taskSound};
  window.webkit?.messageHandlers?.assistantChrome?.postMessage(payload);
  window.assistantAndroidChrome?.postMessage(JSON.stringify(payload));
}
window.addEventListener('message',event=>{
  if(event.data!=='assistantChrome'||!event.ports?.[0]||window.top!==window)return;
  window.assistantAndroidChrome?.close();window.assistantAndroidChrome=event.ports[0];
  notifyNativeChatHeader(!$('app').hidden&&$('app').classList.contains('chat-open'));
});
function updatePageTitle(){const open=!$('app').hidden&&$('app').classList.contains('chat-open');notifyNativeChatHeader(open);const title=open?(state?.title||'正在连接…'):'Codex 手机助手';const unread=taskEvents.filter(e=>e.unread).length;document.title=(unread?'('+unread+') ':'')+title;}
document.documentElement.classList.toggle('native-app',/CodexMobileNative/.test(navigator.userAgent));
function chatKey(id=currentId,host=currentHost){return host+'|'+id;}
function sessionUrl(id,action='',host=currentHost){return '/api/sessions/'+id+(action?'/'+action:'')+'?host='+encodeURIComponent(host);}
function hostUrl(path,host=currentHost){return /^\/api\/sessions\/[0-9a-f-]{36}/.test(path)&&!/[?&]host=/.test(path)?path+(path.includes('?')?'&':'?')+'host='+encodeURIComponent(host):path;}
let catalogData=null, selectedSkills=new Set();
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function toast(text){
  const notice=$('toast'),inChat=!$('app').hidden&&$('app').classList.contains('chat-open');
  (inChat?$('composer'):document.body).append(notice);
  notice.classList.toggle('composer-toast',inChat);notice.textContent=text;notice.hidden=false;
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>notice.hidden=true,4200);
}
async function api(path,body,signal){
  const controller=new AbortController(),abort=()=>controller.abort();
  if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  const timeout=/^\/api\/(auth|diagnostics|creations(?:$|\/))/.test(path)?(body?.action==='recover'?40000:12000):120000;
  const timer=setTimeout(()=>controller.abort(),timeout);
  const options={signal:controller.signal,credentials:'same-origin',cache:'no-store',headers:{}};
  if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(body);}
  try{
    let response;
    try{response=await fetch(hostUrl(path),options);}catch(cause){
      if(signal?.aborted)throw cause;
      const error=Error(controller.signal.aborted?'请求超时，请检查连接后重试':'无法连接助手入口，请检查网络和电脑');error.code='network';networkFailure(error);throw error;
    }
    let data;
    try{data=await response.json();}catch{const error=Error('入口未返回有效数据，请检查隧道或反向代理');error.code='network';networkFailure(error);throw error;}
    if(response.status<500)networkRestored();
    if(!response.ok){
      if(response.status===401)showLogin();
      const error=Error(data.error||'请求失败');error.code=data.code||'http_'+response.status;
      if([502,503,504].includes(response.status))networkFailure(error);
      throw error;
    }
    networkRestored();return data;
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
function showLogin(passwordless=false){csrf='';clearTimeout(connectionRetryTimer);connectionStatus('idle','请先登录助手');notifyNativeChatHeader(false);document.title='Codex 手机助手';disposeChatTimeline();document.querySelectorAll('dialog[open]').forEach(d=>d.close());$('app').hidden=true;$('login').hidden=false;$('credentials').hidden=passwordless;$('noauth').hidden=!passwordless;$('username').required=!passwordless;$('password').required=!passwordless;$('password').value='';}
async function start(){const auth=await api('/api/auth');if(!auth.authenticated){showLogin(auth.passwordless);return;}csrf=auth.csrf;await enter();}
async function enter(){$('login').hidden=true;$('app').hidden=false;void diagnoseConnection();void pollWatchedTasks();const list=loadList(true).catch(e=>toast(e.message));const [id,host='local']=location.hash.slice(1).split('~');if(/^[0-9a-f-]{36}$/.test(id))await openChat(id,decodeURIComponent(host));await list;}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();$('login-button').disabled=true;$('login-error').textContent='';try{const result=await api('/api/login',{username:$('username').value,password:$('password').value,remember:$('remember-login').checked});csrf=result.csrf;$('password').value='';await enter();}catch(error){$('login-error').textContent=error.message;}finally{$('login-button').disabled=false;}});
$('logout').onclick=requestLogout;
$('logout-confirm').onclick=async()=>{$('logout-confirm').disabled=true;try{await api('/api/logout',{});await clearAttachmentDrafts();taskWatch.clear();taskEvents.length=0;clearConversationSwitcher();renderTaskReminders();csrf='';state=null;currentId=null;$('messages').replaceChildren();$('approvals').replaceChildren();$('queued').replaceChildren();$('sessions').replaceChildren();$('message').value='';sessionStorage.clear();imageDrafts.clear();renderImagePreviews();showLogin();}catch(error){$('logout-error').textContent=error.message;}finally{$('logout-confirm').disabled=false;}};
function dateText(value){if(!value)return '';return new Date(value*1000).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});}
$('list-mode').value=localStorage.getItem('list-mode-v2')||'project';
let workingOnly=false,workingRefreshBusy=false;
function selectListMode(mode){workingOnly=mode==='working';if(!workingOnly)$('list-mode').value=mode;localStorage.setItem('list-mode-v2',workingOnly?'project':mode);$('archived').disabled=workingOnly;
 for(const [id,value] of [['project-filter','project'],['recent-filter','recent'],['working-filter','working']])$(id).setAttribute('aria-pressed',String(mode===value));
 loadList(true).catch(error=>{$('sessions').replaceChildren(el('p','error',error.message));});}
$('project-filter').onclick=()=>selectListMode('project');$('recent-filter').onclick=()=>selectListMode('recent');$('working-filter').onclick=()=>selectListMode('working');
for(const [id,value] of [['project-filter','project'],['recent-filter','recent']])$(id).setAttribute('aria-pressed',String($('list-mode').value===value));
setInterval(async()=>{if(!workingOnly||workingRefreshBusy||$('app').hidden||document.hidden||$('app').classList.contains('chat-open'))return;workingRefreshBusy=true;try{await loadList(true);}catch(error){$('host-errors').textContent=error.message;$('host-errors').hidden=false;}finally{workingRefreshBusy=false;}},5000);
$('list-mode').onchange=()=>selectListMode($('list-mode').value);
function renderList(){
  $('sessions').replaceChildren();
  const grouped=!workingOnly&&$('list-mode').value==='project', groups=new Map();
  for(const chat of listRows.filter(row=>!workingOnly||row.working===true)){
    let parent=$('sessions');
    if(grouped&&!chat.pinned){
      if(!groups.has(chat.projectKey)){
        const details=el('details','project-group');
        details.open=localStorage.getItem('group:'+chat.projectKey)==='open';
        const summary=el('summary');summary.append(projectFolderIcon(),el('span','project-name',chat.projectName||'未分组'),el('span','project-host',chat.hostLabel||''));const files=el('button','project-files-button','文件');files.type='button';files.setAttribute('aria-label','浏览 '+(chat.projectName||'项目')+' 文件');files.onclick=event=>{event.preventDefault();event.stopPropagation();openProjectFiles(chat.id,chat.host);};summary.append(files);details.append(summary);
        const mounts=el('button','project-files-button','挂载');mounts.type='button';mounts.setAttribute('aria-label','管理 '+(chat.projectName||'项目')+' 的挂载项目');mounts.onclick=event=>{event.preventDefault();event.stopPropagation();openProjectMounts(chat.id,chat.host);};summary.append(mounts);
        details.ontoggle=()=>localStorage.setItem('group:'+chat.projectKey,details.open?'open':'closed');
        groups.set(chat.projectKey,details);$('sessions').append(details);
      }
      parent=groups.get(chat.projectKey);
    }
    const button=el('button','session'+(chat.id===currentId&&chat.host===currentHost?' selected':''));
    button.dataset.id=chat.id;button.dataset.host=chat.host;
    button.append(el('strong','',(chat.pinned?'置顶 · ':'')+chat.title));
    const meta=el('small');meta.append(el('span','',(grouped?'':chat.projectName+' · ')+chat.hostLabel),el('time','',dateText(chat.recency/1000)));
    button.append(meta);button.onclick=()=>openChat(chat.id,chat.host).catch(e=>toast(e.message));
    const row=el('div','session-row'),menu=el('button','session-menu','⋯');menu.type='button';menu.setAttribute('aria-label','管理 '+chat.title);menu.onclick=()=>openChatMenu(chat.id,chat.host);row.append(button,menu);parent.append(row);
  }
  if(!listRows.length)$('sessions').append(el('p','muted',workingOnly?'暂无正在工作的对话，状态会自动刷新。':'没有找到聊天。'));
  renderTaskReminders();
}
async function loadList(reset=false){
  const generation=++listGeneration;
  if(reset){listOffset=0;listRows=[];$('more').hidden=true;$('sessions').replaceChildren(el('p','muted',workingOnly?'正在确认运行中的任务…':'正在加载项目与聊天…'));}
  listQuery=$('search').value;
  const result=await api('/api/sessions?q='+encodeURIComponent(listQuery)+'&offset='+listOffset+'&archived='+(workingOnly?false:$('archived').checked)+'&working='+workingOnly);
  if(generation!==listGeneration)return;
  const rows=new Map(listRows.map(row=>[chatKey(row.id,row.host),row]));
  for(const row of result.sessions)rows.set(chatKey(row.id,row.host),row);
  listRows=[...rows.values()];listRows.sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b.recency-a.recency);
  listOffset+=result.sessions.length;$('more').hidden=result.sessions.length<100;
  $('host-errors').textContent=(result.unavailableHosts||[]).map(h=>h.label+'：'+h.error).join('\n');
  $('host-errors').hidden=!(result.unavailableHosts||[]).length;renderList();
}
$('search').oninput=()=>{clearTimeout(loadList.timer);loadList.timer=setTimeout(()=>loadList(true).catch(e=>toast(e.message)),300);};$('refresh').onclick=()=>loadList(true).catch(e=>toast(e.message));$('archived').onchange=$('refresh').onclick;$('more').onclick=()=>loadList().catch(e=>toast(e.message));
function saveDraft(){if(currentId)sessionStorage.setItem('draft:'+chatKey(),$('message').value);}
$('message').oninput=()=>{saveDraft();resizeMessage();};
async function openChat(id,host="local"){saveDraft();disposeChatTimeline();const generation=chatGeneration;if($('permissions-dialog').open)$('permissions-dialog').close();resetChatSearch();$('send-retry').hidden=true;$('edit-message-note').hidden=true;currentId=id;currentHost=host;state=null;catalogData=null;try{selectedSkills=new Set(JSON.parse(sessionStorage.getItem('skills:'+chatKey(id,host))||'[]'));}catch{selectedSkills=new Set();}renderSkillPills();approvalStamp='';$('messages').replaceChildren();$('approvals').replaceChildren();$('queued').replaceChildren();$('message').value=sessionStorage.getItem('draft:'+chatKey(id,host))||'';$('send-error').textContent='';await restoreImageDraft(chatKey(id,host));if(generation!==chatGeneration||currentId!==id||currentHost!==host)return;readTaskReminders(id,host);renderImagePreviews();resizeMessage();$('chat-title').textContent='正在连接…';$('chat-meta').textContent='';setChatStatus('连接中');$('welcome').hidden=true;$('chat').hidden=false;$('app').classList.add('chat-open');updatePageTitle();$('send').disabled=true;history.replaceState(null,'','#'+id+'~'+encodeURIComponent(host));document.querySelectorAll('.session').forEach(n=>n.classList.toggle('selected',n.dataset.id===id&&n.dataset.host===host));chatTimeline=new ChatTimeline({id,host,isCurrent:()=>generation===chatGeneration,url:action=>sessionUrl(id,action,host),request:api,renderMeta:renderState,renderText:(node,text,files)=>renderMarkdown(node,text,files,file=>sessionUrl(id,'files/'+file.id,host)),status:text=>{setChatStatus(text);}});await chatTimeline.start();}
function returnToChatList(){disposeChatTimeline();if(isTextField(document.activeElement))document.activeElement.blur();saveDraft();$('app').classList.remove('chat-open');updatePageTitle();history.replaceState(null,'',location.pathname);loadList(true).catch(e=>toast(e.message));}
$('back').onclick=returnToChatList;
function setChatStatus(text){
 const badge=$('status');badge.textContent=text;
 badge.dataset.state=text==='已连接'?'connected':text==='运行中'?'running':text==='等待回应'?'waiting':/连接.*中|读取.*中/.test(text)?'connecting':'offline';
}
function renderStateBase(view){state=view;renderPermissionState(view);updatePageTitle();$('execution-host').textContent='在 '+(view.host==='local'?'电脑':view.hostLabel||'SSH 主机')+' 运行';$('chat-title').textContent=view.loadingHistory?'正在读取聊天…':view.title;$('chat-meta').textContent='';$('model-button').textContent=[view.model||'模型',view.effort].filter(Boolean).join(' · ');$('provider').textContent=[view.model,view.provider].filter(Boolean).join(' · ');setChatStatus(!view.connected?(view.loadingHistory?'读取历史中':view.connecting?'连接桌面中':'历史记录'):view.requests.length?'等待回应':view.status==='active'?'运行中':'已连接');$('status').classList.toggle('offline',!view.connected);$('notice').hidden=view.connected;$('notice-text').textContent=view.connecting?'正在连接桌面，可先阅读已保存的历史。':view.connectionError||'当前显示已保存的历史。请在电脑 App 打开此聊天后重新连接。';$('history').hidden=view.historyComplete||!view.connected;$('working').hidden=view.status!=='active';$('stop').hidden=view.status!=='active'||!view.connected;$('send').disabled=!view.connected||sending;$('send-mode').querySelector('[value=steer]').disabled=view.status!=='active';if(view.status==='active'&&$('send-mode').value==='send')$('send-mode').value='queue';if(view.status!=='active'&&['steer','queue'].includes($('send-mode').value))$('send-mode').value='send';renderApprovals(view.requests);renderQueue(view.submissions||[]);}
function jsonPretty(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
function appendApprovalDetails(card,params,method){
 const fallback=method.includes('fileChange')?'Codex 请求修改文件。':method.includes('permissions')?'Codex 请求本轮额外权限。':'Codex 请求执行命令。';
 if(params.reason)card.append(el('p','approval-reason',params.reason));
 else if(method.includes('requestApproval'))card.append(el('p','approval-reason',fallback));
 const fields=[['command','完整命令'],['cwd','工作目录'],['changes','文件改动'],['grantRoot','授权目录'],['permissions','申请的权限'],['networkApprovalContext','网络访问']].filter(([key])=>params[key]!=null);
 if(!fields.length)return;
 const details=el('details','approval-details');details.append(el('summary','','查看操作详情'));
 for(const [key,label] of fields){const section=el('div','approval-detail-section');section.append(el('strong','',label),el('pre','',jsonPretty(params[key])));details.append(section);}
 const copy=el('button','plain','复制详情');copy.type='button';copy.onclick=()=>copyChatText(fields.map(([key,label])=>label+'\n'+jsonPretty(params[key])).join('\n\n'));details.append(copy);
 card.append(details);
}
function renderApprovals(requests){const stamp=JSON.stringify([requests,state?.permissionMode,state?.effectivePermissionMode]);if(stamp===approvalStamp)return;approvalStamp=stamp;$('approvals').replaceChildren();for(const request of requests){const card=el('section','approval');card.dataset.request=String(request.id);const params=request.params||{}, method=request.method;const heading=method.includes('requestUserInput')?'Codex 需要你的回复':method.includes('fileChange')?'允许修改文件？':method.includes('commandExecution')?'允许运行此命令？':method.includes('permissions')?'允许本轮权限？':'待确认请求';card.append(el('h3','',heading));const controls={};if(!request.supported){card.append(el('p','',params.message||'请在桌面 App 处理此请求'));$('approvals').append(card);continue;}appendApprovalDetails(card,params,method);if(method.includes('requestApproval')){const note=permissionApprovalNote(state);if(note)card.append(el('p','approval-permission-note',note));}const form=el('form');if(method.includes('requestUserInput')){
  card.classList.add('response-card');
  for(const question of params.questions||[]){
    const title=question.question||question.header||question.id;
    if(question.options?.length){
      const group=el('fieldset','response-question');
      group.append(el('legend','',title));
      const choices=el('div','response-options'), name='answer-'+uuid();
      const custom=el('textarea','response-custom');
      custom.rows=2;
      custom.hidden=true;
      custom.id=name+'-custom';
      custom.placeholder='输入你的回复';
      custom.setAttribute('aria-label',title+'：自行填写');
      let selectedValue='', customChoice=false;
      for(const [index,option] of [...question.options,{label:'自行填写'}].entries()){
        const manual=index===question.options.length;
        const label=el('label','response-option'), radio=el('input');
        radio.type='radio';
        radio.name=name;
        radio.value=String(index);
        const surface=el('span','response-option-surface');
        const copy=el('span','response-option-copy');
        const optionTitle=el('span','response-option-title',option.label);
        optionTitle.id=name+'-option-'+index;
        radio.setAttribute('aria-labelledby',optionTitle.id);
        copy.append(optionTitle);
        if(option.description){
          const description=el('span','response-option-description',option.description);
          description.id=optionTitle.id+'-description';
          radio.setAttribute('aria-describedby',description.id);
          copy.append(description);
        }
        const mark=el('span','response-option-mark','✓');
        mark.setAttribute('aria-hidden','true');
        surface.append(copy,mark);
        label.append(radio,surface);
        radio.onchange=()=>{
          selectedValue=option.label;
          customChoice=manual;
          custom.hidden=!manual;
          if(manual)custom.focus();
        };
        if(manual)radio.setAttribute('aria-controls',custom.id);
        choices.append(label);
      }
      group.append(choices,custom);
      controls[question.id]=()=>customChoice?custom.value:selectedValue;
      form.append(group);
    }else{
      const label=el('label','',title), input=el('textarea');
      input.rows=2;
      input.required=true;
      label.append(input);
      controls[question.id]=()=>input.value;
      form.append(label);
    }
  }
}else if(method==='mcpServer/elicitation/request'){if(params.message)form.append(el('p','',params.message));const schema=params.requestedSchema||{};for(const [key,field] of Object.entries(schema.properties||{})){const label=el('label','',field.title||key);let input;if(field.enum){input=el('select');input.append(new Option('请选择…',''));for(const [i,value] of field.enum.entries())input.append(new Option(field.enumNames?.[i]||String(value),String(value)));}else{input=el('input');input.type=field.type==='boolean'?'checkbox':['number','integer'].includes(field.type)?'number':'text';if(input.type==='number')input.step=field.type==='integer'?'1':'any';}input.required=(schema.required||[]).includes(key)&&input.type!=='checkbox';if(field.description)label.append(el('p','muted',field.description));label.append(input);form.append(label);controls[key]=()=>input.type==='checkbox'?input.checked:input.value===''?undefined:['number','integer'].includes(field.type)?Number(input.value):input.value;}}const actions=el('div','actions'),error=el('p','error');async function submit(response){const buttons=card.querySelectorAll('button,.response-question input,.response-question textarea');buttons.forEach(b=>b.disabled=true);error.textContent='';try{await api('/api/sessions/'+currentId+'/respond',{requestId:request.id,response});toast('已发送回应');card.append(el('p','muted','已发送，等待桌面确认…'));}catch(e){error.textContent=e.message;buttons.forEach(b=>b.disabled=false);}}const isInput=method.includes('requestUserInput'),isMcp=method==='mcpServer/elicitation/request';const yes=el('button','primary',isInput?'提交回复':'本次允许');yes.type='submit';const available=params.availableDecisions;const canAccept=!available||available.includes('accept');if(isInput||isMcp||canAccept)actions.append(yes);if(!isInput){const no=el('button','secondary','拒绝');no.type='button';no.onclick=()=>submit(isMcp?{action:'decline'}:{decision:'decline'});if(!available||available.includes('decline'))actions.append(no);if(method.includes('commandExecution')||method.includes('fileChange')||isMcp){const cancel=el('button','secondary','取消');cancel.type='button';cancel.onclick=()=>submit(isMcp?{action:'cancel'}:{decision:'cancel'});if(!available||available.includes('cancel'))actions.append(cancel);}}form.onsubmit=event=>{event.preventDefault();if(isInput){const answers={};for(const [key,get] of Object.entries(controls)){const value=get();if(!value.trim()){error.textContent='请回答所有问题';return;}answers[key]=[value];}submit({answers});}else if(isMcp){const content={};for(const [key,get] of Object.entries(controls)){const value=get();if(value!==undefined)content[key]=value;}submit({action:'accept',content});}else submit({decision:'accept'});};form.append(actions,error);card.append(form);$('approvals').append(card);}}
function renderQueue(rows){$('queued').replaceChildren();for(const row of rows){const card=el('div','queue-card');const reply=row.text.startsWith('<send_user_message_question_reply>');card.append(el('strong','',row.status==='queued'?'等待当前任务完成':'发送结果待确认'),el('p','',reply?'问题回复':(row.text+(row.imageCount?'\n['+row.imageCount+' 张图片]':'')+(row.fileCount?'\n['+row.fileCount+' 个文件]':''))));if(row.status==='queued'){const cancel=el('button','plain','撤回待发送消息');cancel.onclick=async()=>{try{await api('/api/sessions/'+currentId+'/queue',{id:row.id});toast('已撤回');}catch(e){toast(e.message);}};card.append(cancel);}else card.append(el('p','muted','请检查聊天记录。网关不会自动重发这条消息。'));$('queued').append(card);}}
function uuid(){const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
function resizeMessage(){const box=$('message');box.style.height='auto';box.style.height=Math.min(box.scrollHeight,160)+'px';}
function enableImagePreview(image){
  image.classList.add('previewable-image');image.tabIndex=0;
  image.setAttribute('role','button');image.setAttribute('aria-haspopup','dialog');
  image.setAttribute('aria-label','查看大图：'+(image.alt||'图片'));
  const open=event=>{event.preventDefault();event.stopPropagation();openImageViewer(image.currentSrc||image.src,image.alt);};
  image.addEventListener('click',open);
  image.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' ')open(event);});
}
const imageView={scale:1,x:0,y:0};
function applyImageTransform(){
  const image=$('image-viewer-picture');
  const fit=Math.min(image.clientWidth/(image.naturalWidth||1),image.clientHeight/(image.naturalHeight||1));
  const maxX=Math.max(0,((image.naturalWidth||0)*fit*imageView.scale-image.clientWidth)/2);
  const maxY=Math.max(0,((image.naturalHeight||0)*fit*imageView.scale-image.clientHeight)/2);
  imageView.x=Math.max(-maxX,Math.min(maxX,imageView.x));imageView.y=Math.max(-maxY,Math.min(maxY,imageView.y));
  image.style.transform=`translate(${imageView.x}px,${imageView.y}px) scale(${imageView.scale})`;
}
function resetImageTransform(){Object.assign(imageView,{scale:1,x:0,y:0});applyImageTransform();}
function openImageViewer(source,title='图片'){
  if(!source)return;
  const dialog=$('image-viewer'),image=$('image-viewer-picture'),status=$('image-viewer-status');
  if(isTextField(document.activeElement))document.activeElement.blur();
  $('image-viewer-title').textContent=title||'查看图片';
  status.textContent='正在加载图片…';status.hidden=false;image.hidden=true;image.alt=title||'图片';
  resetImageTransform();
  image.onload=()=>{if(dialog.open){image.hidden=false;status.hidden=true;resetImageTransform();}};
  image.onerror=()=>{if(dialog.open){image.hidden=true;status.textContent='图片加载失败，请关闭后重试。';status.hidden=false;}};
  if(!dialog.open)dialog.showModal();
  image.src=source;
}
function initImageViewer(){
  const dialog=$('image-viewer'),stage=dialog.querySelector('.image-viewer-stage');
  let gesture=null,lastTouch=0;
  const point=touch=>({x:touch.clientX,y:touch.clientY});
  const middle=touches=>({x:(touches[0].clientX+touches[1].clientX)/2,y:(touches[0].clientY+touches[1].clientY)/2});
  const distance=touches=>Math.hypot(touches[1].clientX-touches[0].clientX,touches[1].clientY-touches[0].clientY);
  const stop=event=>{if(event.cancelable)event.preventDefault();event.stopPropagation();lastTouch=performance.now();};
  const rebase=touches=>{
    if(touches.length>=2){
      const rect=stage.getBoundingClientRect(),style=getComputedStyle(stage);
      gesture.multi=true;gesture.pinch={...imageView,mid:middle(touches),distance:Math.max(1,distance(touches)),
        center:{x:rect.left+(stage.clientWidth+parseFloat(style.paddingLeft)-parseFloat(style.paddingRight))/2,
                y:rect.top+(stage.clientHeight+parseFloat(style.paddingTop)-parseFloat(style.paddingBottom))/2}};
    }else{gesture.pinch=null;gesture.start=point(touches[0]);gesture.x=imageView.x;gesture.y=imageView.y;}
  };
  stage.addEventListener('touchstart',event=>{
    stop(event);if(!gesture)gesture={at:performance.now(),moved:false,multi:false};rebase(event.touches);
  },{passive:false});
  stage.addEventListener('touchmove',event=>{
    stop(event);if(!gesture||!event.touches.length)return;
    if(event.touches.length>=2){
      if(!gesture.pinch)rebase(event.touches);
      const base=gesture.pinch,mid=middle(event.touches);
      imageView.scale=Math.max(1,Math.min(6,base.scale*distance(event.touches)/base.distance));
      const ratio=imageView.scale/base.scale;
      imageView.x=mid.x-base.center.x-(base.mid.x-base.center.x-base.x)*ratio;
      imageView.y=mid.y-base.center.y-(base.mid.y-base.center.y-base.y)*ratio;
    }else{
      if(gesture.pinch)rebase(event.touches);
      const current=point(event.touches[0]),dx=current.x-gesture.start.x,dy=current.y-gesture.start.y;
      if(Math.hypot(dx,dy)>8)gesture.moved=true;
      imageView.x=gesture.x+dx;imageView.y=gesture.y+dy;
    }
    applyImageTransform();
  },{passive:false});
  stage.addEventListener('touchend',event=>{
    stop(event);if(!gesture)return;
    if(event.touches.length){rebase(event.touches);return;}
    const tap=!gesture.moved&&!gesture.multi&&performance.now()-gesture.at<500;
    gesture=null;if(tap)dialog.close();
  },{passive:false});
  stage.addEventListener('touchcancel',event=>{stop(event);gesture=null;},{passive:false});
  for(const type of ['gesturestart','gesturechange','gestureend'])stage.addEventListener(type,event=>{stop(event);if(gesture)gesture.multi=true;},{passive:false});
  stage.addEventListener('click',event=>{if(performance.now()-lastTouch>600){event.preventDefault();dialog.close();}});
  window.addEventListener('resize',()=>{if(dialog.open){gesture=null;resetImageTransform();}});
  $('image-viewer').addEventListener('close',()=>{
    const image=$('image-viewer-picture');image.onload=null;image.onerror=null;image.removeAttribute('src');image.hidden=true;
    gesture=null;resetImageTransform();
  });
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[data-preview-image]');if(!link)return;
    event.preventDefault();openImageViewer(link.dataset.previewImage,link.textContent||'图片');
  });
}
function renderImagePreviews(){
  const list=imageDrafts.get(chatKey())||[],container=$('image-previews');container.replaceChildren();container.hidden=!list.length;
  $('image-button').disabled=preparingImages||sending||list.length>=4;
  $('send').disabled=preparingImages||sending||!state?.connected;
  for(const image of list){
    const card=el('div',image.type==='file'?'image-preview file-attachment-preview':'image-preview');let preview;
    if(image.type==='file'){
      preview=el('div','file-attachment-info');const extension=image.name.includes('.')?image.name.split('.').pop().slice(0,6).toUpperCase():'FILE';
      preview.append(el('span','file-attachment-icon',extension),el('strong','',image.name),el('small','',fileSize(image.size)+' · 已上传'));preview.title=image.name;
    }else{preview=el('img');preview.src=image.url;preview.alt=image.name;enableImagePreview(preview);}
    const remove=el('button','remove-image','×');remove.type='button';remove.setAttribute('aria-label','移除 '+image.name);remove.disabled=sending;
    remove.onclick=()=>{imageDrafts.set(chatKey(),list.filter(item=>item.id!==image.id));persistImageDraft();renderImagePreviews();};card.append(preview,remove);container.append(card);
  }
}
function prepareImage(file){
  return new Promise((resolve,reject)=>{
    if(file.size>20*1024*1024){reject(Error('原图请控制在 20 MB 以内'));return;}
    const reader=new FileReader();reader.onerror=()=>reject(Error('图片读取失败'));
    reader.onload=()=>{
      const image=new Image();image.onerror=()=>reject(Error('无法读取这张图片，请尝试 JPEG 或 PNG 格式'));
      image.onload=()=>{
        try{
          const ratio=Math.min(1,1600/Math.max(image.naturalWidth,image.naturalHeight));
          const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.naturalWidth*ratio));canvas.height=Math.max(1,Math.round(image.naturalHeight*ratio));
          const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(image,0,0,canvas.width,canvas.height);
          let url=canvas.toDataURL('image/jpeg',.88);if(url.length>2700000)url=canvas.toDataURL('image/jpeg',.65);
          if(url.length>2700000)throw Error('图片仍然过大，请裁剪后重试');
          resolve({id:uuid(),name:file.name||'图片',url});
        }catch(error){reject(error);}
      };image.src=reader.result;
    };reader.readAsDataURL(file);
  });
}
async function addImages(files){
  if(!currentId||preparingImages||sending)return;
  const key=chatKey(),target=currentId,host=currentHost,existing=imageDrafts.get(key)||[],selected=[...files];
  if(!selected.length)return;
  if(selected.length+existing.length>4){toast('每条消息最多添加 4 个附件（含图片）');return;}
  if(selected.some(file=>file.size>20*1024*1024)){toast('单个附件不能超过 20 MB');return;}
  preparingImages=true;renderImagePreviews();
  try{for(const [index,file] of selected.entries()){
    $('attachment-progress').hidden=false;$('attachment-progress').textContent='正在处理附件 '+(index+1)+' / '+selected.length;
    let attachment;
    if((file.type.startsWith('image/')&&!/svg/i.test(file.type))||/\.(png|jpe?g|webp|gif|bmp|heic|heif)$/i.test(file.name)){
      try{attachment=await prepareImage(file);}catch{attachment=await uploadChatFile(file,target,host);}
    }else attachment=await uploadChatFile(file,target,host);
    imageDrafts.set(key,[...(imageDrafts.get(key)||[]),attachment]);await persistImageDraft(key);
    if(chatKey()===key)renderImagePreviews();
  }}
  catch(error){toast(error.message);}
  finally{preparingImages=false;$('attachment-progress').hidden=true;renderImagePreviews();$('image-input').value='';}
}
function uploadChatFile(file,target,host){
 if(host!=='local')return Promise.reject(Error('普通文件附件目前支持此电脑上的对话，SSH 对话仍可添加图片'));
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('POST',sessionUrl(target,'attachments',host)+'&name='+encodeURIComponent(file.name));
  xhr.setRequestHeader('X-CSRF-Token',csrf);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=300000;
  xhr.upload.onprogress=event=>{$('attachment-progress').textContent='上传 '+file.name+' · '+(event.lengthComputable?Math.floor(event.loaded/event.total*100)+'%':fileSize(event.loaded));};
  xhr.onload=()=>{let data={};try{data=JSON.parse(xhr.responseText);}catch{}
   if(xhr.status>=200&&xhr.status<300&&data.id)resolve({...data,type:'file'});
   else reject(Error(data.error||(xhr.status===413?'附件超过服务器上传限制，请减小文件后重试':'附件上传失败，请检查登录和连接后重试')));
  };
  xhr.onerror=xhr.ontimeout=()=>reject(Error('附件上传中断或超时，请重新添加'));xhr.onabort=()=>reject(Error('附件上传已取消'));xhr.send(file);
 });
}
$('image-button').onclick=()=>$('image-input').click();
$('image-input').onchange=()=>addImages($('image-input').files);
$('message').addEventListener('paste',event=>{const files=[...(event.clipboardData?.files||[])];if(files.length){event.preventDefault();addImages(files);}});
$('composer').addEventListener('dragover',event=>{if([...event.dataTransfer.types].includes('Files'))event.preventDefault();});
$('composer').addEventListener('drop',event=>{if(event.dataTransfer?.files.length){event.preventDefault();addImages(event.dataTransfer.files);}});
const viewportMeta=document.querySelector('meta[name="viewport"]');
const defaultViewport='width=device-width,initial-scale=1,minimum-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover';
const isIOS=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
let viewportRestoreTimers=[];
function isTextField(node){return node instanceof Element&&node.matches('textarea,select,input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=hidden]):not([type=button]):not([type=submit]),[contenteditable="true"]');}
function updateViewport(){
  const viewport=window.visualViewport,scale=viewport?.scale||1;
  // A previously zoomed viewport must not leave a stale keyboard-sized layout.
  const height=viewport?viewport.height*scale:window.innerHeight;
  document.documentElement.style.setProperty('--app-height',height+'px');
  document.documentElement.style.setProperty('--viewport-top',(isTextField(document.activeElement)&&Math.abs(scale-1)<.02?(viewport?.offsetTop||0):0)+'px');
}
function prepareInputViewport(event){
  if(!isTextField(event.target))return;
  viewportRestoreTimers.forEach(clearTimeout);viewportRestoreTimers=[];
  if(isIOS)viewportMeta.setAttribute('content',defaultViewport);
  updateViewport();
}
function restoreInputViewport(){
  if(isTextField(document.activeElement))return;
  if(isIOS)viewportMeta.setAttribute('content',defaultViewport);
  window.scrollTo(0,0);
  updateViewport();
  viewportRestoreTimers.push(setTimeout(()=>{
    if(isTextField(document.activeElement))return;
    if(isIOS)viewportMeta.setAttribute('content',defaultViewport);
    window.scrollTo(0,0);
    updateViewport();
  },180));
}
document.addEventListener('touchstart',prepareInputViewport,{passive:true,capture:true});
document.addEventListener('focusin',prepareInputViewport);
document.addEventListener('focusout',()=>{
  viewportRestoreTimers.forEach(clearTimeout);
  // WebKit resizes in stages while dismissing the keyboard.
  viewportRestoreTimers=[setTimeout(restoreInputViewport,100),setTimeout(restoreInputViewport,500)];
});
window.addEventListener('pageshow',()=>{if(!isTextField(document.activeElement))restoreInputViewport();});
window.visualViewport?.addEventListener('resize',updateViewport);
window.visualViewport?.addEventListener('scroll',updateViewport);
window.addEventListener('resize',updateViewport);
updateViewport();
function initTouchNavigation(){
 let swipe=null,lastTap=null;
 const hint=el('span','edge-back-hint','‹');hint.hidden=true;hint.setAttribute('aria-hidden','true');document.body.append(hint);
 const reset=()=>{swipe=null;hint.hidden=true;};
 const canGoBack=()=>!$('app').hidden&&$('app').classList.contains('chat-open')&&!document.querySelector('dialog[open]')&&getComputedStyle($('back')).display!=='none';
 const interactive=node=>node instanceof Element&&!!node.closest('input,textarea,select,button,a,label,[contenteditable],pre,code,table');
 document.addEventListener('touchstart',event=>{
  if(event.touches.length!==1){if(event.cancelable)event.preventDefault();reset();lastTap=null;return;}
  reset();const point=event.touches[0];
  if(!canGoBack()||point.clientX>24||interactive(event.target)||window.getSelection()?.toString())return;
  swipe={id:point.identifier,x:point.clientX,y:point.clientY,at:performance.now(),chat:chatKey(),active:false};
 },{passive:false});
 document.addEventListener('touchmove',event=>{
  if(event.touches.length>1){if(event.cancelable)event.preventDefault();reset();lastTap=null;return;}
  if(!swipe)return;
  const point=[...event.touches].find(touch=>touch.identifier===swipe.id);
  if(!point||!canGoBack()||swipe.chat!==chatKey()){reset();return;}
  const dx=point.clientX-swipe.x,dy=point.clientY-swipe.y;
  if(!swipe.active){
   if(dx < -8||(Math.abs(dy)>12&&Math.abs(dy)>Math.abs(dx))){reset();return;}
   if(dx<12||dx<Math.abs(dy)*1.5)return;
   swipe.active=true;lastTap=null;
  }
  if(event.cancelable)event.preventDefault();
  hint.hidden=false;hint.style.top=Math.max(64,Math.min(window.innerHeight-64,swipe.y))+'px';
  hint.style.transform='translate('+Math.min(70,Math.max(0,dx)*.5)+'px,-50%)';
  hint.classList.toggle('ready',dx>=72&&Math.abs(dy)<dx*.5);
 },{passive:false});
 document.addEventListener('touchend',event=>{
  if(swipe){
   const gesture=swipe,point=[...event.changedTouches].find(touch=>touch.identifier===gesture.id);
   if(!point)return;
   const dx=point.clientX-gesture.x,dy=point.clientY-gesture.y;
   const goBack=gesture.active&&event.touches.length===0&&canGoBack()&&gesture.chat===chatKey()&&dx>=72&&Math.abs(dy)<dx*.5&&performance.now()-gesture.at<1200;
   if(gesture.active&&event.cancelable)event.preventDefault();
   reset();lastTap=null;if(goBack)returnToChatList();return;
  }
  if(event.touches.length||event.changedTouches.length!==1||interactive(event.target)){lastTap=null;return;}
  const point=event.changedTouches[0],now=performance.now();
  if(lastTap&&lastTap.target===event.target&&now-lastTap.at<280&&Math.abs(point.clientX-lastTap.x)<20&&Math.abs(point.clientY-lastTap.y)<20){if(event.cancelable)event.preventDefault();lastTap=null;}
  else lastTap={target:event.target,at:now,x:point.clientX,y:point.clientY};
 },{passive:false});
 document.addEventListener('touchcancel',()=>{reset();lastTap=null;},{passive:true});
 for(const type of ['gesturestart','gesturechange'])document.addEventListener(type,event=>{if(event.cancelable)event.preventDefault();reset();},{passive:false});
 document.addEventListener('wheel',event=>{if(event.ctrlKey&&event.cancelable)event.preventDefault();},{passive:false});
 document.addEventListener('visibilitychange',reset);
}
initTouchNavigation();

$('composer').onsubmit=async event=>{
  event.preventDefault();
  if(sending||preparingImages||!currentId||!state?.connected)return;
  const text=$('message').value, images=[...(imageDrafts.get(chatKey())||[])];
  if(!text.trim()&&!images.length)return;
  const mode=$('send-mode').value, skills=[...selectedSkills].sort(), imageIds=images.map(image=>image.id);
  const target=currentId,targetHost=currentHost,key='pending:'+chatKey();
  let pending;
  try{pending=JSON.parse(sessionStorage.getItem(key));}catch{}
  if(!pending||pending.text!==text||pending.mode!==mode||JSON.stringify(pending.skills||[])!==JSON.stringify(skills)||JSON.stringify(pending.imageIds||[])!==JSON.stringify(imageIds))pending={id:uuid(),text,mode,skills,imageIds};
  try{sessionStorage.setItem(key,JSON.stringify(pending));}catch{toast('浏览器存储空间不足，请清理后再发送');return;}
  sending=true;$('attachment-progress').hidden=false;$('attachment-progress').textContent='正在发送'+(images.length?'消息和 '+images.length+' 个附件':'消息')+'…';$('send-retry').hidden=true;$('send').disabled=true;renderImagePreviews();$('send-error').textContent='';
  try{
    const result=await api(sessionUrl(target,'send',targetHost),{...pending,images:images.filter(image=>image.type!=='file').map(image=>({url:image.url})),files:images.filter(image=>image.type==='file').map(file=>({id:file.id}))});
    if(result.status==='unknown')throw Error('消息可能已送达，请检查聊天记录。再次点击不会重复提交。');
    sessionStorage.removeItem(key);
    if(currentId===target&&currentHost===targetHost&&$('message').value===text){$('message').value='';$('edit-message-note').hidden=true;sessionStorage.removeItem('draft:'+chatKey(target,targetHost));resizeMessage();}
    else if(currentId!==target||currentHost!==targetHost){if(sessionStorage.getItem('draft:'+chatKey(target,targetHost))===text)sessionStorage.removeItem('draft:'+chatKey(target,targetHost));}
    imageDrafts.set(chatKey(target,targetHost),(imageDrafts.get(chatKey(target,targetHost))||[]).filter(image=>!imageIds.includes(image.id)));
    await persistImageDraft(chatKey(target,targetHost));
    if(currentId===target&&currentHost===targetHost){selectedSkills.clear();renderSkillPills();renderImagePreviews();}
    toast(result.status==='queued'?'已加入待发送队列':'已发送到会话');
  }catch(error){if(currentId===target&&currentHost===targetHost){$('send-error').textContent=error.message;$('send-retry').hidden=false;}}
  finally{sending=false;$('attachment-progress').hidden=true;$('send').disabled=!state?.connected;renderImagePreviews();}
};
$('message').onkeydown=event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();$('composer').requestSubmit();}};
$('stop').onclick=async()=>{try{await api('/api/sessions/'+currentId+'/stop',{});toast('已请求停止');}catch(e){toast(e.message);}};
$('history').onclick=async()=>{$('history').disabled=true;try{await api('/api/sessions/'+currentId+'/history',{});}catch(e){toast(e.message);}finally{$('history').disabled=false;}};
$('reconnect').onclick=async()=>{try{await api('/api/sessions/'+currentId+'/reconnect',{progressive:true});}catch(e){toast(e.message);}};
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
async function loadCatalog(refresh=false){const target=currentId,targetHost=currentHost;const value=await api('/api/sessions/'+target+'/catalog'+(refresh?'?refresh=true':''));if(currentId!==target||currentHost!==targetHost)throw Error('聊天已切换');catalogData=value;return value;}
const effortNames={none:'关闭',minimal:'最少',low:'低',medium:'中',high:'高',xhigh:'超高',max:'最高',ultra:'极致'};
function renderEffortButtons(){const select=$('effort-select');$('effort-options').replaceChildren();for(const option of select.options){const button=el('button','effort-option',effortNames[option.value]||option.value);button.type='button';button.setAttribute('aria-pressed',String(option.value===select.value));button.onclick=()=>{select.value=option.value;renderEffortButtons();};$('effort-options').append(button);}}
const permissionOptions=[
 {id:'auto',name:'请求批准',description:'编辑外部文件和使用互联网时始终询问。'},
 {id:'guardian-approvals',name:'帮我批准',description:'仅对检测到的风险操作请求批准。'},
 {id:'full-access',name:'完全访问权限',shortName:'完全访问',description:'可不受限制地访问互联网和你电脑上的任何文件。'}
];
let permissionTarget=null,permissionSaving=false;
function permissionName(mode){return permissionOptions.find(option=>option.id===mode)?.name||({'read-only':'只读权限',custom:'桌面自定义',unknown:'尚未确认'}[mode]||'尚未读取');}
function permissionShortName(mode){return permissionOptions.find(option=>option.id===mode)?.shortName||permissionOptions.find(option=>option.id===mode)?.name||({'read-only':'只读',custom:'自定义',unknown:'未确认'}[mode]||'未确认');}
function permissionApprovalNote(view){
 const selected=view?.permissionMode,effective=view?.effectivePermissionMode;
 if(selected!=='full-access')return '';
 if(effective&&effective!=='unknown'&&effective!==selected)return '本轮实际为'+permissionName(effective)+'，因此仍会询问授权。下一条新任务会提交已选的完全访问权限，本次待授权操作仍需处理。';
 return '这是桌面发来的待授权请求；切换权限不会自动批准已经发出的请求，实际执行权限以桌面为准。';
}
function renderPermissionState(view){
 const selected=view.permissionMode,effective=view.effectivePermissionMode||'unknown',running=view.status==='active'||!!view.requests?.length;
 const mismatch=effective!=='unknown'&&effective!==selected;
 const name=permissionName(selected),button=$('permissions-button');
 button.textContent=running?(effective==='unknown'?'权限未确认':(mismatch?'本轮':'')+permissionShortName(effective)):permissionShortName(selected);
 button.title='已选权限：'+name+'；'+(running?'本轮实际：':'上轮实际：')+permissionName(effective);
 button.disabled=!view.connected||permissionSaving;
 if(permissionTarget?.id===view.id&&permissionTarget.host===(view.host||'local')&&$('permissions-dialog').open){
  $('permissions-current').textContent='已选：'+name+'。'+(running?'本轮实际：':'上轮实际：')+permissionName(effective)+(mismatch?'。下一条新任务将提交所选权限；若仍不一致，请检查电脑端限制。':'。');
  $('permissions-save').disabled=permissionSaving||!view.connected||!$('permissions-form').querySelector('input[name=permission-mode]:checked');
 }
}
$('permissions-button').onclick=()=>{
 if(!currentId||!state?.connected||permissionSaving)return;
 permissionTarget={id:currentId,host:currentHost};$('permissions-error').textContent='';$('permissions-options').replaceChildren();
 for(const option of permissionOptions){
  const row=el('label','permission-choice'),input=el('input');input.type='radio';input.name='permission-mode';input.value=option.id;input.required=true;input.checked=option.id===state.permissionMode;
  const body=el('span','permission-choice-body');body.append(el('strong','',option.name),el('small','',option.description));row.append(input,body);$('permissions-options').append(row);
 }
 $('permissions-dialog').showModal();renderPermissionState(state);
};
$('permissions-form').onchange=()=>{if(state)renderPermissionState(state);};
$('permissions-dialog').addEventListener('close',()=>{permissionTarget=null;});
$('permissions-form').onsubmit=async event=>{
 event.preventDefault();const target=permissionTarget,mode=$('permissions-form').querySelector('input[name=permission-mode]:checked')?.value;
 if(!target||!mode||permissionSaving)return;
 if(target.id!==currentId||target.host!==currentHost||!state?.connected){$('permissions-error').textContent='对话连接已变化，请重新打开权限设置。';return;}
 permissionSaving=true;$('permissions-error').textContent='';$('permissions-save').disabled=true;$('permissions-options').disabled=true;renderPermissionState(state);
 try{
  const result=await api(sessionUrl(target.id,'permissions',target.host),{mode});
  if(permissionTarget===target){
   if(result.confirmed&&result.currentMode===mode){$('permissions-dialog').close();toast('已保存“'+permissionName(mode)+'”，下一条新任务将提交此权限');}
   else $('permissions-error').textContent='设置已提交，但尚未确认桌面切换成功。当前读到：'+permissionName(result.currentMode)+'。请稍后查看当前权限，或在电脑端检查限制。';
  }
 }catch(error){if(permissionTarget===target)$('permissions-error').textContent=error.message;}
 finally{permissionSaving=false;$('permissions-options').disabled=false;if(state)renderPermissionState(state);}
};
function renderEfforts(){const id=$('model-select').value;const model=catalogData?.models.find(m=>m.id===id);$('custom-model-label').hidden=id!=='__custom__';$('custom-model').required=id==='__custom__';const options=model?.efforts||['none','minimal','low','medium','high','xhigh','max','ultra'];$('effort-select').replaceChildren(...options.map(value=>new Option(effortNames[value]||value,value)));const preferred=state?.effort||model?.defaultEffort;$('effort-select').value=options.includes(preferred)?preferred:options[0]||'';renderEffortButtons();$('model-description').textContent=id==='__custom__'?'请输入当前服务支持的模型 ID。':'强度越高，通常思考更深入，等待时间也更长。';}
$('model-button').onclick=async()=>{if(!currentId)return;$('model-error').textContent='';$('model-select').replaceChildren(new Option('读取模型列表…',''));$('model-save').disabled=true;$('model-dialog').showModal();try{const catalog=await loadCatalog();$('model-select').replaceChildren(...catalog.models.map(m=>new Option(m.name,m.id)),new Option('自定义模型…','__custom__'));const current=state?.model||catalog.currentModel;$('model-select').value=catalog.models.some(m=>m.id===current)?current:'__custom__';$('custom-model').value=current||'';renderEfforts();$('model-save').disabled=!state?.connected;}catch(e){$('model-error').textContent=e.message;}};
$('model-select').onchange=renderEfforts;
$('model-form').onsubmit=async event=>{event.preventDefault();$('model-save').disabled=true;$('model-error').textContent='';const model=$('model-select').value==='__custom__'?$('custom-model').value.trim():$('model-select').value;try{const result=await api('/api/sessions/'+currentId+'/settings',{model,effort:$('effort-select').value});$('model-dialog').close();toast(result.confirmed?'桌面模型设置已同步':'桌面已接受设置，等待同步');}catch(e){$('model-error').textContent=e.message;}finally{$('model-save').disabled=false;}};
function renderSkillPills(){$('skills-selected').textContent='已选 '+selectedSkills.size+' / 8';$('skill-count').textContent=selectedSkills.size?'('+selectedSkills.size+')':'';$('skill-pills').replaceChildren();for(const id of selectedSkills){const skill=catalogData?.skills.find(s=>s.id===id);const pill=el('button','skill-pill',(skill?.displayName||'已选 Skill')+' ×');pill.type='button';pill.onclick=()=>{selectedSkills.delete(id);renderSkillPills();renderSkills();};$('skill-pills').append(pill);}if(currentId)sessionStorage.setItem('skills:'+chatKey(),JSON.stringify([...selectedSkills]));}
const skillChinese={
'apk-reverse':'分析 Android 安装包，查看代码结构与资源，辅助排查问题。',
'ash12-elf-complete-flow':'分析特定 Android ELF 加载器、加密载荷和执行流程。',
'asm-analysis':'结合静态分析与动态调试，理解汇编代码和程序行为。',
'diagram-generator':'根据文字或代码生成流程图、架构图等图表。',
'imagegen':'生成新图片，或根据已有图片进行编辑。',
'openai-docs':'查阅 OpenAI 官方文档，了解模型、接口与产品配置。',
'skill-creator':'创建或完善可复用的技能说明及配套资源。',
'skill-installer':'安装和管理可用技能。',
'figma':'获取 Figma 设计信息，将设计转换为界面代码。',
'binary-diff':'对比不同版本的二进制文件，辅助迁移符号与分析结果。'
};
function renderSkills(){
  $('skill-list').replaceChildren();const search=$('skill-search').value.toLocaleLowerCase();
  for(const skill of catalogData?.skills||[]){
    const translated=skillChinese[skill.name]||skillChinese[skill.name.split(':').at(-1)];
    if(![skill.name,skill.displayName,skill.description,translated].join(' ').toLocaleLowerCase().includes(search))continue;
    const row=el('div','skill-option'),input=el('input');input.type='checkbox';input.value=skill.id;input.checked=selectedSkills.has(skill.id);input.setAttribute('aria-label','选择 '+(skill.displayName||skill.name));
    input.onchange=()=>{if(input.checked&&selectedSkills.size>=8){input.checked=false;toast('最多选择 8 个技能');return;}if(input.checked)selectedSkills.add(skill.id);else selectedSkills.delete(skill.id);renderSkillPills();};
    const detail=el('details','skill-detail'),summary=el('summary','',skill.displayName||skill.name);detail.append(summary);
    const description=skill.description||'暂无说明。';
    if(/[\u3400-\u9fff]/.test(description))detail.append(el('p','',description));
    else{detail.append(el('p','',translated||'此技能暂无中文简介，可展开下方原始说明查看。'));const original=el('details','skill-original');original.append(el('summary','','查看原始说明'),el('p','',description));detail.append(original);}
    row.append(input,detail);$('skill-list').append(row);
  }
  if(!$('skill-list').children.length)$('skill-list').append(el('p','muted','没有匹配的技能'));
}
async function openSkills(refresh=false){if(!currentId)return;$('skills-error').textContent='';$('skill-list').textContent='正在读取已安装 Skill…';if(!$('skills-dialog').open)$('skills-dialog').showModal();try{await loadCatalog(refresh);renderSkills();renderSkillPills();}catch(e){$('skills-error').textContent=e.message;$('skill-list').replaceChildren();}}
$('skills-button').onclick=()=>openSkills();$('skills-refresh').onclick=()=>openSkills(true);$('skill-search').oninput=renderSkills;
window.addEventListener('pagehide',saveDraft);
initImageViewer();
initImprovements();
ProjectMounts.init();
initChatTools();
initConversationSwitcher();
initChatExtras();
start().catch(error=>{showLogin();$('login-error').textContent=error.message;});

let fileTarget=null,fileGeneration=0;
// Shared with the native navigation fallback; opening a file never starts a download.
window.openConversationFile=source=>{
 let url;try{url=new URL(source,location.href);}catch{return false;}
 const match=/^\/api\/sessions\/([0-9a-f-]{36})\/files\/([a-f0-9]{64})$/.exec(url.pathname);
 if(url.origin!==location.origin||!match)return false;
 openProjectFiles(match[1],url.searchParams.get('host')||'local','',false,{artifact:match[2],locate:true});
 return true;
};
function showFilePath(element){
 $('file-path-value').textContent=element.dataset.filePath;
 $('file-path-copy').textContent='复制路径';
 if(!$('file-path-dialog').open)$('file-path-dialog').showModal();
}
$('file-path-copy').onclick=async()=>{try{await navigator.clipboard.writeText($('file-path-value').textContent);$('file-path-copy').textContent='已复制';}catch{await copyChatText($('file-path-value').textContent);}};
(() => {
 let press=null,timer=null,suppressUntil=0;
 const clear=()=>{clearTimeout(timer);timer=null;press=null;};
 document.addEventListener('pointerdown',event=>{
  if(press){clear();return;}
  const element=event.target.closest('[data-file-path]');
  if(!element||!event.isPrimary||event.button!==0)return;
  press={element,x:event.clientX,y:event.clientY};
  timer=setTimeout(()=>{suppressUntil=Date.now()+1200;showFilePath(element);clear();},550);
 },{passive:true});
 document.addEventListener('pointermove',event=>{if(press&&Math.hypot(event.clientX-press.x,event.clientY-press.y)>10)clear();},{passive:true});
 for(const name of ['pointerup','pointercancel','scroll'])document.addEventListener(name,clear,{passive:true,capture:true});
 document.addEventListener('contextmenu',event=>{const element=event.target.closest('[data-file-path]');if(element){event.preventDefault();clear();suppressUntil=Date.now()+1200;showFilePath(element);}});
 document.addEventListener('click',event=>{
  if(Date.now()<suppressUntil&&!event.target.closest('#file-path-dialog')){event.preventDefault();event.stopImmediatePropagation();return;}
  const link=event.target.closest('[data-artifact-url],a[href]');if(!link)return;
  const opened=window.openConversationFile(link.dataset.artifactUrl||link.href);
  if(opened||link.hasAttribute('data-artifact-url')){
   event.preventDefault();event.stopImmediatePropagation();
   if(!opened)toast('无法定位此文件，请从项目文件列表中查看。');
  }
 },true);
})();
async function copyProjectPath(path,button){
  if(!path)return;
  const label=button.querySelector('span')||button;
  try{await navigator.clipboard.writeText(path);label.textContent='已复制项目路径';clearTimeout(button.copyTimer);button.copyTimer=setTimeout(()=>label.textContent='复制项目路径',2000);}
  catch{await copyChatText(path);}
}
function fileSize(size){return size<1024?size+' B':size<1048576?(size/1024).toFixed(1)+' KB':(size/1048576).toFixed(1)+' MB';}
async function openProjectFiles(id,host='local',path='',append=false,options={}){
  const generation=++fileGeneration;
  if(!append){fileTarget={id,host,path,offset:0,rootPath:'',artifact:options.artifact||''};$('project-file-list').replaceChildren(el('p','muted','正在读取文件列表…'));$('project-copy-path').disabled=true;$('project-upload').hidden=!!options.artifact;}
  const target={...fileTarget};$('project-files-error').textContent='';$('project-files-more').hidden=true;
  if(!$('project-files-dialog').open)$('project-files-dialog').showModal();
  const scope=target.artifact?'&artifact='+encodeURIComponent(target.artifact):'';
  try{const result=await api(sessionUrl(id,'project-files',host)+'&path='+encodeURIComponent(path)+'&offset='+target.offset+scope+(options.locate?'&locate=1':''));
    if(generation!==fileGeneration||!$('project-files-dialog').open)return;
    if(!append)$('project-file-list').replaceChildren();
    fileTarget.path=result.path;fileTarget.offset=(result.offset??target.offset)+result.entries.length;
    fileTarget.rootPath=result.rootPath||'';$('project-copy-path').disabled=!fileTarget.rootPath;
    $('project-upload').hidden=!!result.readOnly;
    $('project-files-up').hidden=result.parent===null;$('project-files-up').onclick=()=>openProjectFiles(id,host,result.parent||'',false,{artifact:target.artifact});
    if(result.focusPath&&result.offset>0){const all=el('button','plain','查看完整文件夹');all.type='button';all.onclick=()=>openProjectFiles(id,host,result.path,false,{artifact:target.artifact});$('project-file-list').append(all);}
    let focused=null;
    for(const entry of result.entries){const row=el('div','project-file-row');
      const name=el('button','project-file-name',entry.name);name.type='button';
      if(entry.directory){name.type='button';name.prepend(projectFolderIcon());name.onclick=()=>openProjectFiles(id,host,entry.path,false,{artifact:target.artifact});row.append(name,el('span','muted','›'));}
      else{const download=el('a','file-download','下载');download.href=sessionUrl(id,'project-files/download',host)+'&path='+encodeURIComponent(entry.path)+scope;download.download=entry.name;name.onclick=()=>{if(!openDownloadedFile(download.href))previewProjectFile(id,host,entry,target.artifact);};row.append(name,el('small','muted',fileSize(entry.size)),download);}
      if(entry.path===result.focusPath){row.classList.add('file-located');row.setAttribute('aria-current','true');row.tabIndex=-1;focused=row;}
      $('project-file-list').append(row);
    }
    if(!fileTarget.offset)$('project-file-list').append(el('p','muted','此文件夹为空。'));
    $('project-files-more').hidden=!result.more;
    renderFileDownloads();requestDownloadList();
    if(focused){focused.focus({preventScroll:true});focused.scrollIntoView({block:'center'});}
  }catch(error){if(generation===fileGeneration){if(!append)$('project-file-list').replaceChildren();$('project-files-error').textContent=error.message;}}
}
$('project-files-more').onclick=()=>{if(fileTarget)openProjectFiles(fileTarget.id,fileTarget.host,fileTarget.path,true);};
$('project-copy-path').onclick=()=>copyProjectPath(fileTarget?.rootPath,$('project-copy-path'));
$('project-files-dialog').addEventListener('close',()=>{fileGeneration++;});
$('manage-files').onclick=()=>{if(managementTarget){const {id,host}=managementTarget;$('manage-dialog').close();openProjectFiles(id,host);}};

let previewGeneration=0,uploadRequest=null,uploading=false,uploadCancelled=false;
async function previewProjectFile(id,host,entry,artifact=''){
 const generation=++previewGeneration;
 $('file-preview-title').textContent=entry.name;$('file-preview-error').textContent='';$('file-preview-content').textContent='正在读取…';
 const scope=artifact?'&artifact='+encodeURIComponent(artifact):'';
 $('file-preview-download').href=sessionUrl(id,'project-files/download',host)+'&path='+encodeURIComponent(entry.path)+scope;$('file-preview-download').download=entry.name;
 if(!$('file-preview-dialog').open)$('file-preview-dialog').showModal();
 try{const value=await api(sessionUrl(id,'project-files/preview',host)+'&path='+encodeURIComponent(entry.path)+scope);
  if(generation!==previewGeneration||!$('file-preview-dialog').open)return;
  $('file-preview-content').replaceChildren();
  if(value.kind==='image'){const img=el('img','project-image-preview');img.alt=entry.name;img.src=sessionUrl(id,'project-files/image',host)+'&path='+encodeURIComponent(entry.path)+scope;img.onerror=()=>{$('file-preview-error').textContent='图片无法显示，请下载查看。';};$('file-preview-content').append(img);}
  else{const text=el('pre','project-text-preview',value.text);$('file-preview-content').append(text);if(value.truncated)$('file-preview-content').append(el('p','muted','仅预览前 256 KB，完整内容请下载。'));}
 }catch(error){if(generation===previewGeneration){$('file-preview-content').replaceChildren();$('file-preview-error').textContent=error.message;}}
}
$('file-preview-dialog').addEventListener('close',()=>{previewGeneration++;$('file-preview-content').replaceChildren();});
$('project-upload').onclick=()=>{if(fileTarget&&!uploading)$('project-upload-input').click();};
$('project-upload-cancel').onclick=()=>{uploadCancelled=true;uploadRequest?.abort();};
$('project-upload-input').onchange=async()=>{
 const files=Array.from($('project-upload-input').files||[]),target={...fileTarget};$('project-upload-input').value='';
 if(uploading||!files.length||!target.id)return;
 if(files.length>20){$('project-files-error').textContent='一次最多上传 20 个文件';return;}
 if(files.some(f=>f.size>50*1024*1024)){$('project-files-error').textContent='单个文件不能超过 50 MB';return;}
 uploading=true;uploadCancelled=false;$('project-upload').disabled=true;$('project-upload-cancel').hidden=false;$('project-files-error').textContent='';let completed=0;
 try{for(const file of files){if(uploadCancelled)break;
  await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();uploadRequest=xhr;
   xhr.open('POST',sessionUrl(target.id,'project-files/upload',target.host)+'&path='+encodeURIComponent(target.path)+'&name='+encodeURIComponent(file.name));
   xhr.setRequestHeader('X-CSRF-Token',csrf);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.timeout=300000;
   xhr.upload.onprogress=e=>{$('project-upload-status').textContent='上传至 '+(target.path||'项目根目录')+' · '+file.name+' · '+(e.lengthComputable?Math.floor(e.loaded/e.total*100)+'%':'上传中');};
   xhr.onload=()=>{let data={};try{data=JSON.parse(xhr.responseText);}catch{}if(xhr.status>=200&&xhr.status<300)resolve();else reject(new Error(data.error||'上传失败，请重试'));};
   xhr.onerror=xhr.ontimeout=()=>reject(new Error('连接中断，请刷新目录确认文件是否已保存'));xhr.onabort=()=>reject(new Error('已取消上传，请刷新目录确认文件状态'));xhr.send(file);
  });completed++;
 }
 $('project-upload-status').textContent='已上传 '+completed+' 个文件';
 }catch(error){$('project-files-error').textContent=error.message;$('project-upload-status').textContent='已上传 '+completed+' 个文件';}
 finally{uploading=false;uploadRequest=null;$('project-upload').disabled=false;$('project-upload-cancel').hidden=true;
  if($('project-files-dialog').open&&fileTarget?.id===target.id&&fileTarget?.host===target.host&&fileTarget?.path===target.path){const message=$('project-files-error').textContent;await openProjectFiles(target.id,target.host,target.path);if(message)$('project-files-error').textContent=message;}
 }
};


const downloadRows=new Map();
const nativeDownloads=/CodexMobileNative\/[23]/.test(navigator.userAgent);
const resumableNativeDownloads=/CodexMobileNative\/3/.test(navigator.userAgent);
const nativeIOSDownloads=nativeDownloads&&isIOS;
const iosDownloadReadyMessage='已下载到 App，可选择其他应用或“存储到文件”。';
let pendingNativeDownload=null,pendingDownloadTimer=null;
function downloadCommand(action,id=''){location.href='/__assistant_downloads?action='+encodeURIComponent(action)+'&id='+encodeURIComponent(id);}
function downloadKey(sourceURL){
 if(!sourceURL)return '';
 try{const url=new URL(sourceURL,location.href);url.hash='';if(url.searchParams.get('host')==='local')url.searchParams.delete('host');url.searchParams.sort();return url.href;}catch{return '';}
}
function orderedDownloads(){
 const rows=Array.from(downloadRows.values());if(pendingNativeDownload)rows.push(pendingNativeDownload);
 return rows.reverse().sort((a,b)=>(Number(b.createdAt)||0)-(Number(a.createdAt)||0));
}
function fileDownload(sourceURL){const key=downloadKey(sourceURL);return orderedDownloads().find(item=>downloadKey(item.sourceURL)===key);}
function openDownloadedFile(sourceURL){
 const item=fileDownload(sourceURL);if(item?.state!=='ready'&&item?.state!=='saved')return false;
 if(nativeDownloads)downloadCommand('save',item.id);else saveWebDownload(item);
 return true;
}
function renderFileDownloads(){
 const latest=new Map();for(const item of orderedDownloads()){const key=downloadKey(item.sourceURL);if(key&&!latest.has(key))latest.set(key,item);}
 for(const link of document.querySelectorAll('#project-file-list a[download]')){
  const item=latest.get(downloadKey(link.href)),running=['running','pausing'].includes(item?.state),ready=item?.state==='ready'||item?.state==='saved';
  const openLabel=nativeIOSDownloads?'用其他应用打开':'保存文件';
  link.classList.toggle('is-downloading',running);
  link.classList.toggle('is-downloaded',ready);
  const name=link.closest('.project-file-row')?.querySelector('.project-file-name');
  if(name){name.title=ready?'已下载，点击'+openLabel:'预览文件';name.setAttribute('aria-label',link.download+'，'+(ready?openLabel:'预览文件'));}
  if(running){
   const percent=Number.isFinite(item.percent)&&item.percent>=0?Math.min(99,Math.floor(item.percent)):-1;
   if(!link.querySelector('.file-download-ring'))link.innerHTML='<svg class="file-download-ring" viewBox="0 0 40 40" aria-hidden="true"><circle class="download-ring-track" cx="20" cy="20" r="16"/><circle class="download-ring-value" cx="20" cy="20" r="16" pathLength="100"/></svg><span class="file-download-percent" aria-hidden="true"></span>';
   link.classList.toggle('is-indeterminate',percent<0);
   link.querySelector('.download-ring-value').style.strokeDashoffset=String(100-(percent<0?25:percent));
   link.querySelector('.file-download-percent').textContent=percent<0?'':percent+'%';
   link.title=(item.message||'正在下载')+'；点击查看下载列表';
   link.setAttribute('aria-label',link.download+'，'+(percent<0?'正在准备下载':percent+'%')+'，点击查看下载列表');
  }else{
   link.classList.remove('is-indeterminate');
   link.textContent=ready?openLabel:item?.state==='paused'?(item.restartRequired?'重新下载':'继续下载'):item?.state==='failed'?'重试':'下载';
   link.title=ready?'已下载，点击'+openLabel:item?.message||'下载到应用';
   link.setAttribute('aria-label',link.textContent+' '+link.download);
  }
 }
}
// Wait for the native download to start before navigating to a list command.
function requestDownloadList(){if(nativeDownloads&&!pendingNativeDownload)downloadCommand('list');}
function renderDownloads(){
 renderFileDownloads();
 $('downloads-list').replaceChildren();
 for(const item of orderedDownloads()){
  const ready=item.state==='ready'||item.state==='saved';
  const row=el('section','download-row');row.append(el('strong','',item.name),el('p','muted',nativeIOSDownloads&&ready?iosDownloadReadyMessage:item.message||''));
  const progress=el('progress');progress.max=100;if(item.percent>=0)progress.value=item.percent;row.append(progress);
  if(ready){const save=el('button','plain',nativeIOSDownloads?'用其他应用打开':'保存文件');save.onclick=()=>nativeDownloads?downloadCommand('save',item.id):saveWebDownload(item);row.append(save);}
  if(item.state==='running'&&!item.pending){const cancel=el('button','plain',resumableNativeDownloads?'暂停下载':'取消下载');cancel.onclick=()=>{if(nativeDownloads)downloadCommand(resumableNativeDownloads?'pause':'cancel',item.id);else item.xhr?.abort();};row.append(cancel);}
  if(resumableNativeDownloads&&['paused','failed'].includes(item.state)){const resume=el('button','plain',item.restartRequired?'重新下载':'继续下载');resume.onclick=()=>downloadCommand(item.restartRequired?'restart':'resume',item.id);row.append(resume);}
  if(!['running','pausing'].includes(item.state)){const remove=el('button','plain','删除记录');remove.onclick=()=>{if(nativeDownloads){downloadCommand('delete',item.id);return;}if(item.url)URL.revokeObjectURL(item.url);downloadRows.delete(item.id);renderDownloads();};row.append(remove);}
  $('downloads-list').append(row);
 }
 if(!downloadRows.size&&!pendingNativeDownload)$('downloads-list').append(el('p','muted','暂无下载记录'));
}
window.assistantDownloads=rows=>{
 if(!nativeDownloads||!Array.isArray(rows))return;
 for(const row of rows){const previous=downloadRows.get(row.id);row.sourceURL=row.sourceURL||previous?.sourceURL;row.createdAt=row.createdAt||previous?.createdAt||0;}
 if(pendingNativeDownload){
  const pending=pendingNativeDownload,fresh=rows.filter(row=>!pending.priorIds.has(row.id));
  // Older apps omit the source URL; associate only a single, unambiguous new record.
  const started=rows.find(row=>downloadKey(row.sourceURL)===downloadKey(pending.sourceURL))||(fresh.length===1&&!fresh[0].sourceURL&&fresh[0].name===pending.name?fresh[0]:null);
  if(started){started.sourceURL=pending.sourceURL;started.createdAt=started.createdAt||pending.createdAt;clearTimeout(pendingDownloadTimer);pendingNativeDownload=null;}
 }
 downloadRows.clear();for(const row of rows)downloadRows.set(row.id,row);renderDownloads();
};
window.assistantDownloadStatus=(message,percent)=>{if(nativeDownloads){requestDownloadList();return;}const item=downloadRows.get('native-legacy');if(item){item.message=message;item.percent=percent;renderDownloads();}};
function openDownloads(){if(!$('download-dialog').open)$('download-dialog').showModal();$('downloads-hint').textContent=resumableNativeDownloads?'关闭列表会继续下载；离开 App 自动暂停，回来后点击“继续下载”。':nativeDownloads?'关闭列表或切换到其他页面，下载会继续。系统强制关闭和网络限制可能中断下载。':'关闭列表可以继续下载；网页版请保持页面打开。';renderDownloads();requestDownloadList();}
$('downloads-open').onclick=openDownloads;
function refreshVisibleDownloads(){if(!document.hidden&&($('download-dialog').open||$('project-files-dialog').open))requestDownloadList();}
setInterval(refreshVisibleDownloads,2000);
document.addEventListener('visibilitychange',refreshVisibleDownloads);
document.addEventListener('click',async event=>{
 const link=event.target.closest('a[download]');if(!link)return;
 const url=new URL(link.href,location.href);if(url.origin!==location.origin||!['http:','https:'].includes(url.protocol))return;
 const downloaded=fileDownload(url.href);
 if(downloaded?.state==='ready'||downloaded?.state==='saved'){event.preventDefault();openDownloadedFile(url.href);return;}
 if(['running','pausing'].includes(downloaded?.state)){event.preventDefault();openDownloads();return;}
 if(resumableNativeDownloads&&['paused','failed'].includes(downloaded?.state)){event.preventDefault();downloadCommand(downloaded.restartRequired?'restart':'resume',downloaded.id);return;}
 if(nativeDownloads){
  if(pendingNativeDownload){event.preventDefault();toast('正在准备下载，请稍候…');return;}
  pendingNativeDownload={id:'native-pending',name:link.download||'文件',sourceURL:url.href,createdAt:Date.now(),state:'running',message:'正在准备下载…',percent:-1,pending:true,priorIds:new Set(downloadRows.keys())};
  renderDownloads();
  pendingDownloadTimer=setTimeout(()=>{pendingNativeDownload=null;renderDownloads();requestDownloadList();toast('尚未收到下载状态，请查看下载列表后重试。');},60000);
  if(!link.closest('#project-file-list'))toast('已加入下载，可从“下载列表”查看进度');
  return;
 }
 if(/CodexMobileNative/.test(navigator.userAgent)){downloadRows.set('native-legacy',{id:'native-legacy',name:link.download||'文件',state:'running',message:'请更新手机 App，以使用完整下载列表和后台下载。',percent:-1});openDownloads();return;}
 event.preventDefault();
 const item={id:crypto.randomUUID(),name:link.download||'文件',sourceURL:url.href,createdAt:Date.now(),state:'running',message:'准备下载…',percent:-1};downloadRows.set(item.id,item);renderDownloads();if(!link.closest('#project-file-list'))openDownloads();
 try{const blob=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();item.xhr=xhr;xhr.open('GET',url);xhr.responseType='blob';xhr.timeout=300000;
 xhr.onprogress=e=>{item.message='正在下载 · '+fileSize(e.loaded)+(e.lengthComputable?' / '+fileSize(e.total):'');item.percent=e.lengthComputable?Math.floor(e.loaded/e.total*100):-1;renderDownloads();};
 xhr.onload=()=>xhr.status===200?resolve(xhr.response):reject(new Error('下载失败，请重新登录或重试'));xhr.onerror=xhr.ontimeout=()=>reject(new Error('网络中断或下载超时'));xhr.onabort=()=>reject(new Error('已取消下载'));xhr.send();});
 item.blob=blob;item.url=URL.createObjectURL(blob);item.state='ready';item.percent=100;item.message='下载完成，点击保存文件。';
 }catch(error){item.state='failed';item.message=error.message;}finally{item.xhr=null;renderDownloads();}
});
async function saveWebDownload(item){
 try{const file=new File([item.blob],item.name,{type:item.blob.type});
 if(navigator.canShare?.({files:[file]})){await navigator.share({files:[file],title:item.name});item.message='已交给系统保存/分享，请在选择的位置查看。';}
 else{const a=document.createElement('a');a.href=item.url;a.download=item.name;a.click();item.message='已交给浏览器保存，请查看浏览器下载列表或系统下载文件夹。';}
 }catch(error){item.message=error.name==='AbortError'?'已取消保存，可再次点击保存文件。':'保存失败，请重试。';}renderDownloads();
}
