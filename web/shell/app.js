'use strict';
// Capture once and remove the bearer fragment before any API request or chat routing.
function takePairingToken(){
  if(!location.hash.startsWith('#pair='))return null;
  const token=location.hash.slice(6);
  history.replaceState(null,'',location.pathname+location.search);
  return token;
}
let initialPairingToken=takePairingToken();
const $ = id => document.getElementById(id),t=BridgeI18n.t;
let csrf='', currentId=null, state=null, listOffset=0, listQuery='', approvalStamp='', sending=false;
let claudeCliAccessPanel=null;
let currentHost='local', listRows=[], listGeneration=0, listHasMore=false, listArchived=false, listSource='all', availableSources=[], listRenderStamp='';
let listComplete=false, listReadFinished=false, listIdentity='', listFilterKey='';
const composerDrafts=typeof ComposerDrafts==='function'?new ComposerDrafts():null;
let codexQuotes=null;
let draftGateway='',codexDraftScope=null,codexDraftTouched=false,restoringComposer=false,codexActiveAccountId;const codexProvisionalDrafts=new Map();
let listHostIds=[];
let sourceFilterStamp='';
let chatTimeline=null,reconnectFailure=null;
const dismissedHostFaults=new Map();
const submittedRequestIds=new Set();
const sessionActivity=new SessionActivity();
const recentInteractions=new Map();
function bumpRecency(id,host,at=Date.now()){
  const key=chatKey(id,host);recentInteractions.set(key,Math.max(at,recentInteractions.get(key)||0));
  const row=listRows.find(row=>row.id===id&&row.host===host);
  if(row&&at>row.recency){row.recency=at;listRows.sort((a,b)=>b.recency-a.recency);renderList();}
}
let activityBusy=false;
const fastModeControl=new FastModeControl({root:$('fast-mode-control'),input:$('fast-mode-enabled')});
let modelRequest=0,modelTarget=null,modelSaving=false;
async function goalAction(action,payload) {
  const id=currentId,host=currentHost;
  const result=await api(sessionUrl(id,'goal/'+action,host),payload);
  if(!result.confirmed)throw Error('目标操作结果尚未确认，请查看目标状态');
  if(currentId===id&&currentHost===host){state={...state,goal:action==='cancel'?null:(result.result?.goal||result.result)};workModes.render(state,$('send-mode').value,false);}
  toast(action==='cancel'?'目标已关闭':action==='edit'?'目标已修改，保持暂停':payload.status==='paused'?'目标已暂停':'目标已恢复');
}
const workModes=new WorkModes({select:$('work-mode'),hint:$('mode-hint'),goalRoot:$('goal-state'),goalToggle:$('goal-toggle'),newId:uuid,
  onCancel:payload=>goalAction('cancel',payload),onStatus:payload=>goalAction('status',payload),onEdit:payload=>goalAction('edit',payload)});
window.addEventListener('focus',()=>{if(!$('app').hidden)accountsPanel?.refresh();});
function chatKey(id=currentId,host=currentHost){return host+'|'+id;}
function sessionUrl(id,action='',host=currentHost){const path='/api/sessions/'+id+(action?'/'+action:'')+'?host='+encodeURIComponent(host);return window.BridgeRelayURL?.(path)||path;}
function hostUrl(path,host=currentHost){const value=/^\/api\/sessions\/[0-9a-f-]{36}/.test(path)&&!/[?&]host=/.test(path)?path+(path.includes('?')?'&':'?')+'host='+encodeURIComponent(host):path;return window.BridgeRelayURL?.(value)||value;}
let listLoading=0;
let modelCatalogGeneration=0;const modelCatalogs=new Map();let catalogData={models:null,skills:null};let catalogResults={models:null,skills:null};let catalogTasks={models:null,skills:null};let selectedSkills=new Set(),invalidSkills=new Set(),skillIndex=new Map(),skillTotal=0,skillPages=[],skillQuery={q:'',offset:0,limit:200},skillSearchTimer,skillRequest=0;
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function toast(text){
  const original=$('toast'),dialog=[...document.querySelectorAll('dialog[open]')].filter(node=>!node.inert).at(-1);
  clearTimeout(toast.timer);if(toast.active&&toast.active!==original)toast.active.remove();original.hidden=true;
  const node=dialog?document.createElement('div'):original;
  if(dialog){node.className=original.className;node.setAttribute('role','status');dialog.append(node);}
  node.textContent=t(text);node.hidden=false;toast.active=node;
  toast.timer=setTimeout(()=>{if(node===original)node.hidden=true;else node.remove();},4200);
}
function renderHostErrors(hosts){
  const root=$('host-errors'),gateway=(window.BridgeRelayBase||location.origin||'')+'|';
  const active=new Set(hosts.map(host=>gateway+(host.host||host.label)));
  for(const key of dismissedHostFaults.keys())if(key.startsWith(gateway)&&!active.has(key))dismissedHostFaults.delete(key);
  root.replaceChildren();
  for(const host of hosts){
    const key=gateway+(host.host||host.label),fault=String(host.error||'');
    if(dismissedHostFaults.get(key)===fault)continue;
    const row=el('div','host-error-row');row.append(el('span','',host.label+'：'+fault));
    const retry=el('button','plain',t('重试'));retry.type='button';
    retry.onclick=()=>listSync.update(true);row.append(retry);
    const close=el('button','plain','×');close.type='button';close.setAttribute('aria-label',t('关闭连接错误'));
    close.onclick=()=>{dismissedHostFaults.set(key,fault);row.remove();root.hidden=!root.childElementCount;};
    row.append(close);root.append(row);
  }
  root.hidden=!root.childElementCount;
}
function clearListSnapshot(){
  browserNavigation.invalidate();
  for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('codex-list-v1:')||key?.startsWith('desktop-list-v1:'))sessionStorage.removeItem(key);}
  for(let i=localStorage.length-1;i>=0;i--){const key=localStorage.key(i);if(key?.startsWith('codex-list-v1:')||key?.startsWith('desktop-list-v1:'))localStorage.removeItem(key);}
  listIdentity='';listFilterKey='';listHostIds=[];listRows=[];listOffset=0;listHasMore=false;listComplete=false;listReadFinished=false;availableSources=[];listRenderStamp='';listGeneration++;
}
function authLost(){hideWebhookUrl();clearListSnapshot();if(!$('app').hidden)document.dispatchEvent(new Event('bridge-auth-lost'));showLogin();}
async function api(path,body,signal,authCurrent){const options={signal,credentials:'same-origin',cache:'no-store',headers:{}};if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-CSRF-Token':csrf};options.body=JSON.stringify(body);}let response;try{response=await fetch(hostUrl(path),options);}catch(error){if(!signal?.aborted&&error.name!=='AbortError')window.BridgeMobileHandoff?.unreachable();throw error;}if([502,503,504,522,523,524,530].includes(response.status))window.BridgeMobileHandoff?.unreachable();let data;try{data=await response.json();}catch(error){throw Object.assign(error,{status:response.status});}if(!response.ok){if(response.status===401&&(!authCurrent||authCurrent()))authLost();throw Object.assign(Error(data.error||t('请求失败')),{loginStatus:data.loginStatus,status:response.status,code:data.code});}return data;}
const workbench=window.BridgeWorkbench=new Workbench({chat:$('chat'),request:api,csrf:()=>csrf,notify:toast,onUnauthorized:authLost,openChat,composer:$('message'),onReference:()=>window.BridgePresentation?.openChat()});
async function sendSessionPin(identity,body,token){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{return await api(identity.provider==='codex'?sessionUrl(identity.id,'pin',identity.host):ClientIdentity.path(identity.provider,'pin'),identity.provider==='codex'?body:{...body,sessionId:identity.id},controller.signal,()=>sessionPins.current(token));}finally{clearTimeout(timer);}
}
const sessionPins=new LocalSessionPins({newId:uuid,send:sendSessionPin,onChange:()=>{listRenderStamp='';renderList();desktopSessions.listStamp='';desktopSessions.renderList();if(listFilterKey)saveListSnapshot(listFilterKey);desktopSessions.rememberView();}});
function pinRow(row,provider='codex'){const identity={provider,id:row.id,host:row.host||'local'};return {...row,bridgePinned:sessionPins.value(identity,row.bridgePinned),pinPending:sessionPins.pending(identity)};}
const desktopSessions=new DesktopSessionsView({root:$('app'),pinPreferences:sessionPins,onSessionMenu:(button,row,provider)=>bindSessionMenu(button,row,provider),onOpenSessionMenu:(row,provider,button)=>getSessionMenu().show(sessionMenuContext(row,provider),{element:button}),select:$('desktop-backend'),request:api,notify:toast,csrf:()=>csrf,onUnauthorized:authLost,onSelect:provider=>{
  sharedSessionMenu?.close();if(provider!=='codex'){saveDraft();chatTimeline?.dispose();chatTimeline=null;currentId=null;state=null;workbench.reset();$('chat').hidden=true;$('welcome').hidden=false;$('app').classList.remove('chat-open');if(!browserNavigation.enabled())history.replaceState(null,'',location.pathname);}
}});
const clientNavigation=new ClientNavigation({view:desktopSessions,request:api,notify:toast});
const browserNavigation=new BrowserNavigation({
  read:()=>isCodexClient(clientNavigation.provider)?($('app').classList.contains('chat-open')&&currentId?{provider:'codex',id:currentId,host:currentHost}:null):(desktopSessions.isChatOpen?{provider:desktopSessions.provider,id:desktopSessions.sid,host:'local'}:null),
  restore:async(route,isCurrent=()=>true)=>{
    if($('app').hidden)return;
    document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
    if(!route){if(!isCodexClient(clientNavigation.provider))desktopSessions.backToList();else if($('app').classList.contains('chat-open'))$('back').click();return;}
    const auth=await api('/api/auth');
    if(!isCurrent())return;
    if(!auth.authenticated||$('app').hidden){history.replaceState(browserNavigation.entry(null),'',browserNavigation.url(null));return;}
    if(listIdentity&&listAuthIdentity(auth)!==listIdentity){browserNavigation.invalidate();return;}
    const entry=route.provider==='codex'?(isCodexEntry(route.host)?route.host:'codex'):route.provider;
    if(!clientNavigation.managementClients().some(client=>client.id===entry&&client.enabled)){
      toast('尚未启用应用，请打开应用管理。');history.replaceState(browserNavigation.entry(browserNavigation.read()),'',browserNavigation.url(browserNavigation.read()));return;
    }
    await clientNavigation.choose(entry,{activate:false});
    if(!isCurrent())return;
    if(route.provider==='codex')await openChat(route.id,route.host);
    else await desktopSessions.open(route.id);
  },onError:error=>toast(error.message)
});
browserNavigation.paused=true;
desktopSessions.onNavigation=()=>browserNavigation.sync();
desktopSessions.onHistoryInvalidated=()=>browserNavigation.invalidate();

let codexAccountEntry='desktop',preferredWslEntry=clientNavigation.initialHost||'',selectedCodexEntry=clientNavigation.initialHost?.startsWith('codex:')?clientNavigation.initialHost:null;
currentHost=clientNavigation.initialHost||currentHost;
function codexEntries(){return [...(clientNavigation.clients.find(row=>row.id==='codex')?.entries||[]),...clientNavigation.clients.filter(row=>row.kind==='codex-local')];}
function wslEntries(){return clientNavigation.wslSupported===true?codexEntries().filter(row=>row.kind==='wsl-cli'&&row.enabled):[];}
function accountUrl(path){return codexAccountEntry==='desktop'?path:path+(path.includes('?')?'&':'?')+'entry='+encodeURIComponent(codexAccountEntry);}
function refreshEntryAccounts(){
  const entries=codexEntries();
  codexAccountEntry=isCodexEntry(currentHost)&&entries.some(row=>row.id===currentHost)?currentHost:'desktop';
  accountsPanel?.clear();if($('accounts-dialog').open){renderAccountEntries();refreshSelectedAccounts();}
}
clientNavigation.onApplicationChange=(previous,provider,{host,loadList:readList=true}={})=>{
  if(provider==='codex')selectedCodexEntry=host?.startsWith('codex:')?host:null;
  if(host?.startsWith('codex:')){preferredWslEntry=host;listSource='all';}
  if(host?.startsWith('wsl:')){preferredWslEntry=host;listSource='wsl';localStorage.setItem('codex-source-filter',listSource);}
  listGeneration++;saveDraft();if(host==='local'||isCodexEntry(host))currentHost=host;listRows=[];availableSources=[];listOffset=0;listHasMore=true;listComplete=false;listReadFinished=false;listFilterKey='';listRenderStamp='';sourceFilterStamp='';
  $('sessions').replaceChildren();$('host-errors').hidden=true;chatTimeline?.dispose();chatTimeline=null;currentId=null;state=null;workbench.reset();$('chat').hidden=true;$('welcome').hidden=false;$('app').classList.remove('chat-open');if(!browserNavigation.enabled())history.replaceState(null,'',location.pathname);browserNavigation.sync();
  $('new-chat').hidden=false;refreshEntryAccounts();renderSourceFilter();
  if(provider==='codex'&&readList&&!$('app').hidden)return loadList(true).catch(e=>toast(e.message));
};
clientNavigation.onClientStates=clients=>{
  const previousSource=listSource;renderSourceFilter();
  if(previousSource!==listSource&&clientNavigation.provider==='codex'&&!$('app').hidden)loadList(true).catch(error=>toast(error.message));
  if(!currentId||clientNavigation.provider!=='codex')return;
  const codex=clients.find(row=>row.id==='codex'),entry=isCodexEntry(currentHost)?codexEntries().find(row=>row.id===currentHost):codex?.entries?.find(row=>row.id==='desktop')||codex;
  if(!entry?.enabled)clientNavigation.onApplicationChange('codex','codex');
};
desktopSessions.onListEnter=()=>{clientNavigation.listReadDone=false;clientNavigation.listVisit=(clientNavigation.listVisit||0)+1;if(!isCodexClient(clientNavigation.provider))desktopSessions.refresh();};
desktopSessions.onListReady=(provider,value)=>{clientNavigation.acknowledgeList(provider,value);window.BridgeFeatures?.prefetchChat();};
async function uploadAttachment(key,id,file){const separator=key.indexOf('|'),host=key.slice(0,separator),thread=key.slice(separator+1);const response=await fetch(sessionUrl(thread,'uploads',host)+'&id='+encodeURIComponent(id)+'&name='+encodeURIComponent(file.name),{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf},body:file});const result=await response.json();if(!response.ok){if(response.status===401)authLost();const error=Error(result.error||t('附件上传失败，请重试'));error.retryable=response.status>=500||response.status===429;throw error;}return result;}
async function uploadAttachmentWithRetry(key,id,file){if(window.BridgeSharedRelay)return uploadAttachment(key,id,file);let last;for(let attempt=0;attempt<3;attempt++){try{return await uploadAttachment(key,id,file);}catch(error){last=error;if(error.name==='AbortError'||error.retryable===false||attempt===2)throw error;await new Promise(resolve=>setTimeout(resolve,400*(attempt+1)));}}if(last instanceof TypeError)last.message=t('附件上传中断，请重试');throw last;}
const attachments=new ChatAttachments({root:$('attachment-list'),button:$('attach-button'),input:$('attachment-input'),chooseReference:()=>window.BridgeFileActions.pickReference(workbench),paste:$('message'),drop:$('composer'),preview:(key,id)=>{const at=key.indexOf('|'),host=key.slice(0,at),thread=key.slice(at+1);return hostUrl(sessionUrl(thread,'uploads/'+encodeURIComponent(id)+'/preview',host));},onChange:()=>{if(currentId)$('send').disabled=sending||!attachments.ready()||!state||state.loadingHistory||state.activating||!currentAccess().canSend;},thumbnail:async(key,id,file)=>{
  const separator=key.indexOf('|'),host=key.slice(0,separator),thread=key.slice(separator+1);
  const image=await attachments.thumbnailBlob(file);
  const response=await fetch(sessionUrl(thread,'uploads/'+encodeURIComponent(id)+'/thumb',host)+'&width='+image.width+'&height='+image.height,{method:'POST',credentials:'same-origin',headers:{'Content-Type':image.mime,'X-CSRF-Token':csrf},body:image.data});
  const result=await response.json();if(!response.ok){if(response.status===401)authLost();throw Error(result.error||t('缩略图生成失败'));}return result;
},upload:uploadAttachmentWithRetry});
workbench.attachReference=info=>attachments.addReference(info);
function visibleChat(){return currentId&&!workbench.isFileVisible&&!document.hidden&&$('app').classList.contains('chat-open')?chatKey():null;}
function reportVisibleChat(){
  if(document.hidden||$('app').hidden)return;
  const codex=isCodexClient(clientNavigation.provider);
  if(codex?(!visibleChat()||!chatTimeline||chatTimeline.cached||!chatTimeline.meta||chatTimeline.meta.loadingHistory):(!desktopSessions.visibleActivity()||!desktopSessions.state))return;
  document.dispatchEvent(new CustomEvent('bridge-chat-visible',{detail:{threadId:codex?currentId:desktopSessions.sid,host:codex?currentHost:'desktop:'+desktopSessions.provider}}));
}
desktopSessions.onDetailReady=reportVisibleChat;
window.addEventListener('focus',reportVisibleChat);
document.addEventListener('visibilitychange',reportVisibleChat);
function renderActivity(){for(const button of $('sessions').querySelectorAll('.session')){
  button.querySelector('.session-indicator')?.remove();const indicator=sessionActivity.indicator(chatKey(button.dataset.id,button.dataset.host));
  if(indicator){const dot=el('span','session-indicator '+indicator.kind);dot.title=t(indicator.label);dot.setAttribute('aria-label',t(indicator.label));button.querySelector('strong').prepend(dot);}
}}
async function refreshActivity(){
  if(activityBusy||$('app').hidden||document.hidden||!listRows.length)return;activityBusy=true;
  const hosts={};for(const row of listRows)(hosts[row.host]??=[]).push(row.id);
  try{const result=await api('/api/activity',{hosts});if(!$('app').hidden){sessionActivity.update(result.sessions,visibleChat());for(const row of result.sessions)if(row.recency)bumpRecency(row.id,row.host,row.recency);renderActivity();}}catch{}finally{activityBusy=false;}
}
setInterval(refreshActivity,5000);
window.addEventListener('focus',refreshActivity);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshActivity();});
let loginStatus={attemptsRemaining:5,attemptLimit:5,blocked:false};
function renderLoginStatus(status=loginStatus){
  loginStatus=status||loginStatus;
  $('login-attempts').textContent=loginStatus.blocked?t('此 IP 已被封禁，请在电脑网关 App 的“登录设备”中解除。'):t('剩余尝试次数：')+loginStatus.attemptsRemaining+'/'+loginStatus.attemptLimit+t('；连续输错 5 次将封禁此 IP。');
  $('login-button').disabled=loginStatus.blocked;
}
function clearCodexProvisionalDrafts(){codexProvisionalDrafts.clear();for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('draft:')||key?.startsWith('skills:'))sessionStorage.removeItem(key);}}
function clearQuoteComposer(){if(typeof codexQuotes!=='undefined')codexQuotes?.reset();}
function showLogin(passwordless=false,status){sessionPins.suspend();clearQuoteComposer();if(typeof ChatTimeline!=='undefined')ChatTimeline.clearCache?.();draftGateway='';codexDraftScope=null;clearCodexProvisionalDrafts();codexActiveAccountId=undefined;window.BridgeDownloads?.clear();$('mobile-binding-status').hidden=true;if(typeof codexCliAccessPanel!=='undefined')codexCliAccessPanel?.clear();claudeCliAccessPanel?.clear();if(window.BridgeSharedRelay){location.replace((window.BridgeRelayBase||'')+'/relay/');return;}desktopSessions.clear();clientNavigation.clear?.();modelCatalogGeneration++;modelCatalogs.clear();catalogData.models=null;catalogResults.models=null;catalogTasks.models=null;workbench.reset();if(typeof accountsPanel!=='undefined')accountsPanel?.clear();chatTimeline?.dispose();chatTimeline=null;document.querySelectorAll('dialog[open]').forEach(d=>d.close());$('app').hidden=true;$('login').hidden=false;$('credentials').hidden=passwordless;$('noauth').hidden=!passwordless;$('username').required=!passwordless;$('password').value='';$('remember-login').parentElement.hidden=passwordless||BridgeHost.isNativeApp();$('login-attempts').hidden=passwordless;renderLoginStatus(status);if(window.BridgeBootstrap)window.BridgeBootstrap.ready();else $('bootstrap-status').hidden=true;}
async function start(pairingToken=null){
  window.BridgeBootstrap?.waitingForAuth();
  const auth=window.BridgeBootstrap?.readAuth?await window.BridgeBootstrap.readAuth(signal=>api('/api/auth',undefined,signal)):await api('/api/auth');
  if(['en','zh'].includes(auth.uiLanguage)){$('phone-language').value=auth.uiLanguage;$('phone-language').onchange();}
  if(pairingToken!==null){
    try{window.BridgeBootstrap?.pairingSubmitted();const result=await api('/api/pair',{token:pairingToken});csrf=result.csrf;await enter();return;}
    catch(error){
      if(auth.authenticated){csrf=auth.csrf;await enter(auth);toast(error.message);}
      else{showLogin(auth.passwordless,auth.loginStatus);$('login-error').textContent=t(error.message);}
      return;
    }finally{pairingToken=null;}
  }
  if(!auth.authenticated){clearListSnapshot();showLogin(auth.passwordless,auth.loginStatus);return;}csrf=auth.csrf;await enter(auth);
}
async function refreshComputer(){
  $('connected-computer').dataset.deviceName=location.host;
  $('computer-name').textContent=location.host;$('connected-computer').title=location.host;
  try{const auth=await api('/api/auth');if(!auth.authenticated||$('app').hidden)return;
    const binding=$('mobile-binding-status');binding.hidden=!auth.trustedDevice;binding.setAttribute('data-i18n','此手机已绑定，可在电脑网关的“登录设备”中解除绑定。');binding.textContent=t('此手机已绑定，可在电脑网关的“登录设备”中解除绑定。');
    const name=auth.computer?.name||location.host;$('connected-computer').dataset.deviceName=name;$('computer-name').textContent=name;
    $('connected-computer').title=[name,auth.computer?.platform,location.host].filter(Boolean).join(' · ');
  }catch{/* Older or temporarily unavailable gateways keep the connected address. */}
  finally{document.dispatchEvent(new Event('bridge-computer'));}
}
async function openHarness(){
  try{const state=await api('/api/harness');if(state.running)location.assign('/harness/');else toast('请在电脑 App 中启动 DeepSeek Harness');}
  catch(error){toast(error.message);}
}
$('harness-open').onclick=openHarness;
async function enter(authInfo){
  desktopSessions.onListEnter();const chatLink=location.hash;$('desktop-backend').hidden=false;
  if(/(?:^|[?&])open=harness(?:&|$)/.test(location.search||'')){history.replaceState(null,'','/');await openHarness();}
  const verified=authInfo?.authenticated?authInfo:await api('/api/auth');
  if(!verified.authenticated)throw Error(t('登录状态暂不可用'));
  if(draftGateway&&draftGateway!==verified.draftIdentity){clearCodexProvisionalDrafts();if(typeof ChatTimeline!=='undefined')ChatTimeline.clearCache?.();}
  draftGateway=verified.draftIdentity||'';
  sessionPins.bind(draftGateway?JSON.stringify([window.BridgeRelayBase||location.origin,draftGateway]):'');
  desktopSessions.bindDraftGateway?.(draftGateway);
  listIdentity=await listAuthIdentity(verified);
  desktopSessions.bindListCache(listIdentity,window.BridgeRelayBase||location.host);
  restoreListSnapshot();
  const target=clientNavigation.chatTarget?.(chatLink),direct=!!target&&Array.isArray(verified.enabledClients);
  clientNavigation.enabledClients=Array.isArray(verified.enabledClients)?verified.enabledClients.slice():null;
  if(!direct&&desktopSessions.previewCachedList(clientNavigation.provider))clientNavigation.paint();
  $('login').hidden=true;$('app').hidden=false;if(window.BridgeBootstrap)window.BridgeBootstrap.ready();else $('bootstrap-status').hidden=true;document.dispatchEvent(new Event('bridge-authenticated'));
  const identity=listIdentity,session=csrf;
  const refresh=()=>{
    if($('app').hidden||listIdentity!==identity||csrf!==session)return Promise.resolve();
    refreshComputer();const clientsReady=clientNavigation.refresh();accountsPanel?.refresh();
    if(isCodexClient(clientNavigation.provider))loadList(true).catch(async e=>{await clientsReady.catch(()=>{});if(isCodexClient(clientNavigation.provider))toast(e.message);});
    return clientsReady;
  };
  if(direct){
    browserNavigation.paused=false;
    try{await clientNavigation.openChatLink(chatLink,openChat);}finally{setTimeout(refresh,0);}
    return;
  }
  await refresh();
  browserNavigation.paused=false;
  const restored=history.state?.bridgeNavigation===1&&history.state.scope===browserNavigation.scope&&history.state.route;
  if(restored&&restored.provider!=='codex'){browserNavigation.restoring=true;try{await browserNavigation.restore(restored);}finally{browserNavigation.restoring=false;}}
  else await clientNavigation.openChatLink(chatLink,openChat);
}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();if(loginStatus.blocked)return;$('login-button').disabled=true;$('login-error').textContent='';try{const result=await api('/api/login',{username:$('username').value,password:$('password').value,remember:$('remember-login').checked});renderLoginStatus({attemptsRemaining:5,attemptLimit:5,blocked:false});csrf=result.csrf;$('password').value='';await enter();}catch(error){$('login-error').textContent=t(error.message);renderLoginStatus(error.loginStatus);}finally{$('login-button').disabled=loginStatus.blocked;}});
$('logout').onclick=async()=>{await api('/api/logout',{});hideWebhookUrl();document.dispatchEvent(new Event('bridge-logged-out'));csrf='';state=null;currentId=null;$('messages').replaceChildren();$('approvals').replaceChildren();$('queued').replaceChildren();$('sessions').replaceChildren();$('message').value='';sessionStorage.clear();clearListSnapshot();attachments.reset();sessionActivity.clear();recentInteractions.clear();showLogin();};
function dateText(value){if(!value)return '';return new Date(value*1000).toLocaleDateString(BridgeI18n.locale(),{month:'numeric',day:'numeric'});}
function listAuthIdentity(auth){return auth?.authenticated&&/^[0-9a-f]{64}$/.test(auth.listCacheIdentity||'')?auth.listCacheIdentity:'';}
function listSnapshotKey(filter){return listIdentity?'codex-list-v1:'+listIdentity+':'+filter:'';}
function currentListFilter(){
  const fields=[$('search').value,$('archived').checked,listSource,$('list-mode').value];
  if(selectedCodexEntry)fields.push(selectedCodexEntry);
  return JSON.stringify(fields);
}
function restoreListSnapshot(filter=currentListFilter()){
  const key=listSnapshotKey(filter);if(!key)return false;
  let saved,legacy=false;
  for(const storage of [localStorage,sessionStorage]){
    try{
      const snapshot=storage.getItem(key);if(!snapshot||snapshot.length>512*1024)continue;
      const value=JSON.parse(snapshot);
      if(!value||!Array.isArray(value.rows)||value.rows.length>200||!value.rows.every(row=>row&&typeof row.id==='string'&&typeof row.host==='string')||
          !Array.isArray(value.sources)||!value.sources.every(source=>typeof source==='string')||
          !Array.isArray(value.hosts)||!value.hosts.every(host=>typeof host==='string'))continue;
      saved=value;legacy=storage===sessionStorage;break;
    }catch{}
  }
  if(!saved)return false;
  listFilterKey=filter;listRows=saved.rows;availableSources=saved.sources;listHostIds=saved.hosts;
  listOffset=saved.rows.length;listHasMore=saved.hasMore===true;listComplete=false;listReadFinished=false;
  listQuery=$('search').value;listArchived=$('archived').checked;renderSourceFilter();renderList();if(legacy)saveListSnapshot(filter);return true;
}
function saveListSnapshot(filter){
  const key=listSnapshotKey(filter);if(!key)return;
  const rows=(JSON.parse(filter)[3]==='recent'?[...listRows].sort((a,b)=>Number(pinRow(b).bridgePinned)-Number(pinRow(a).bridgePinned)||(b.recency-a.recency)||b.id.localeCompare(a.id)):listRows).slice(0,200),snapshot=JSON.stringify({rows,sources:availableSources,hosts:listHostIds,offset:rows.length,hasMore:listHasMore||listRows.length>rows.length});
  if(snapshot.length>512*1024)return;
  try{
    localStorage.setItem(key,snapshot);sessionStorage.removeItem(key);
    const keys=[];for(let i=0;i<localStorage.length;i++){const item=localStorage.key(i);if(item?.startsWith('codex-list-v1:')&&item!==key)keys.push(item);}
    for(const old of keys.slice(0,-7))localStorage.removeItem(old);
  }catch{try{sessionStorage.setItem(key,snapshot);}catch{}}
}
$('list-mode').value=localStorage.getItem('list-mode')||'recent';
$('list-mode').onchange=()=>{localStorage.setItem('list-mode',$('list-mode').value);loadList(true).catch(e=>toast(e.message));};
listSource=clientNavigation.initialHost?.startsWith('wsl:')?'wsl':localStorage.getItem('codex-source-filter')||'all';
if(!['all','desktop','cli','ide','wsl'].includes(listSource))listSource='all';
$('source-filter').onchange=()=>{listSource=$('source-filter').value;localStorage.setItem('codex-source-filter',listSource);return loadList(true).catch(e=>toast(e.message));};
function renderSourceFilter(){
  $('source-filter-label').hidden=false;
  const wsl=clientNavigation.wslSupported===true,stamp=BridgeI18n.locale()+':'+wsl;
  if(clientNavigation.wslSupported===false&&listSource==='wsl'){listSource='all';localStorage.setItem('codex-source-filter',listSource);}
  if(stamp!==sourceFilterStamp){
    const options=[['all',t('全部会话')],['cli','CLI'],['ide','VS Code'],['desktop','APP'],...(wsl?[['wsl','WSL']]:[])];
    $('source-filter').replaceChildren(...options.map(([value,label])=>{const option=el('option','',label);option.value=value;return option;}));
    sourceFilterStamp=stamp;
  }
  if($('source-filter').value!==listSource)$('source-filter').value=listSource;
  return true;
}
if(typeof bindChatListToolbar==='function')bindChatListToolbar(document.querySelector('#sidebar .chat-list-toolbar'));
function sessionAccess(access){
  if(!access)return {source:'desktop',runtime:'desktop',canSend:true,canInterrupt:true,canApprove:true,canReconnect:true,canConfigure:true,canChangePermissions:true,canChangeModel:true,canSelectSkills:true,canUseModes:true,canUseGoal:true,canUseSideChat:true,canEditMessages:true,readOnly:false};
  const legacy=access.source==='desktop';
  const allowed=name=>(name==='canReconnect'||!access.readOnly)&&(access[name]===undefined?legacy:access[name]===true);
  const specific=name=>access[name]===undefined?allowed('canConfigure'):allowed(name);
  return {...access,source:access.source||'unknown',runtime:access.runtime||null,canSend:allowed('canSend'),canInterrupt:allowed('canInterrupt'),canApprove:allowed('canApprove'),canReconnect:allowed('canReconnect'),canConfigure:allowed('canConfigure'),canChangePermissions:specific('canChangePermissions'),canChangeModel:specific('canChangeModel'),canSelectSkills:specific('canSelectSkills'),canUseModes:allowed('canUseModes'),canUseGoal:allowed('canUseGoal'),canUseSideChat:allowed('canUseSideChat'),canEditMessages:allowed('canEditMessages'),readOnly:access.readOnly===true};
}
function currentAccess(){return sessionAccess(state?.access||listRows.find(row=>row.id===currentId&&row.host===currentHost)?.access);}
function sourceLabel(access){return access?.runtime==='wsl'?'WSL':access?.source==='cli'?'CLI':access?.source==='ide'?'VS Code':access?.source==='unknown'?t('其他来源'):'APP';}
function renderList(){
  const displayedRows=listRows.map(row=>pinRow(row));
  const stamp=JSON.stringify([$('list-mode').value,currentId,currentHost,listHasMore,BridgeI18n.locale(),availableSources,listRows.length?null:[listComplete,listReadFinished],displayedRows.map(row=>[row.id,row.host,row.title,row.projectKey,row.projectName,row.hostLabel,row.recency,row.bridgePinned,row.pinPending,row.archived,row.access,row.orphanedSideChat])]);
  if(stamp===listRenderStamp&&$('sessions').children.length){renderActivity();return;}
  listRenderStamp=stamp;
  const scrollTop=$('sessions').scrollTop;
  $('sessions').replaceChildren();
  const grouped=$('list-mode').value==='project', groups=new Map();
  let pinSection=null;
  const unassigned=chat=>chat.projectKey===(chat.host||'local')+'|unassigned';
  const ordered=grouped?[...displayedRows].sort((a,b)=>Number(unassigned(a))-Number(unassigned(b))||(b.recency-a.recency)||b.id.localeCompare(a.id)):[...displayedRows].sort((a,b)=>Number(!!b.bridgePinned)-Number(!!a.bridgePinned)||(b.recency-a.recency)||b.id.localeCompare(a.id));
  const hasPins=!grouped&&ordered.some(chat=>chat.bridgePinned);
  for(const chat of ordered){
    let parent=$('sessions');
    if(hasPins&&pinSection!==!!chat.bridgePinned){pinSection=!!chat.bridgePinned;const label=el('h3','muted',t(pinSection?'置顶聊天':'最近聊天'));$('sessions').append(label);}
    if(grouped){
      if(!groups.has(chat.projectKey)){
        const details=el('details','project-group');
        details.open=localStorage.getItem('group:'+chat.projectKey)!=='closed';
        details.append(el('summary','',chat.projectName+' · '+(chat.host==='local'?t('此电脑'):chat.hostLabel)));
        details.ontoggle=()=>localStorage.setItem('group:'+chat.projectKey,details.open?'open':'closed');
        groups.set(chat.projectKey,details);$('sessions').append(details);
      }
      parent=groups.get(chat.projectKey);
    }
    const button=el('button','session'+(chat.id===currentId&&chat.host===currentHost?' selected':''));
    button.dataset.id=chat.id;button.dataset.host=chat.host;
    button.append(el('strong','',chat.title));
    button.append(el('span','session-source',sourceLabel(sessionAccess(chat.access))||t('其他来源')));
    const meta=el('small');meta.append(el('span','',(chat.orphanedSideChat?t('侧边聊天（主聊天不可用）')+' · ':'')+chat.projectName+' · '+(chat.host==='local'?t('此电脑'):chat.hostLabel)),el('time','',dateText(chat.recency/1000)));
    if(chat.pinPending)button.append(el('span','session-source',t('置顶设置待同步')));
    button.append(meta);button.onclick=()=>openChat(chat.id,chat.host).catch(e=>toast(e.message));bindSessionMenu(button,chat);parent.append(button);
  }
  if(listHasMore){const more=el('button','plain',t('加载更多聊天'));more.id='sessions-more';more.type='button';more.onclick=()=>loadList().catch(error=>toast(error.message));$('sessions').append(more);}
  if(!listRows.length)$('sessions').append(el('p','muted',t(listComplete?'没有找到聊天。':listReadFinished?'暂不可用':'正在读取…')));renderActivity();$('sessions').scrollTop=scrollTop;
}
let sharedSessionMenu;
function getSessionMenu(){return sharedSessionMenu||(sharedSessionMenu=new SessionMenu({translate:t,notify:toast,newId:uuid}));}
workbench.openSessionMenu=(chat,element)=>getSessionMenu().show(sessionMenuContext(chat),{element});
function bindSessionMenu(button,chat,provider='codex'){
  getSessionMenu().bind(button,()=>sessionMenuContext(chat,provider));
}
const cliConnectionsPanel=new CliConnectionsPanel({request:api,notify:toast,translate:t,
  identity:()=>csrf+'|'+listIdentity,currentClient:()=>clientNavigation.activeClient?.()||clientNavigation.provider,newId:uuid});
$('cli-connections-button').onclick=()=>{ $('appearance-dialog').close();cliConnectionsPanel.open(); };
function sessionMenuContext(chat,provider='codex'){
  const identity={provider,id:chat.id,host:chat.host||'local',runtime:chat.access?.runtime||chat.runtime};
  const scope=listIdentity,token=csrf,isCurrent=()=>!!scope&&scope===listIdentity&&token===csrf;
  const current=()=>provider==='codex'?clientNavigation.provider==='codex'&&currentId===chat.id&&currentHost===identity.host:
    desktopSessions.provider===provider&&desktopSessions.sid===chat.id;
  const detail=current()?(provider==='codex'?state:desktopSessions.state):null;
  const view={...chat,...detail},access=sessionAccess(view.access),caps=view.capabilities||{},nativeId=view.nativeId||(ClientIdentity.family(provider)==='claude'?null:chat.id);
  const ensure=async()=>{
    if(!isCurrent())throw Error('连接或账号已变化，请重新打开会话菜单');
    if(!current()){
      await clientNavigation.choose(provider,{activate:false});
      if(!isCurrent())throw Error('连接或账号已变化，请重新打开会话菜单');
      if(provider==='codex')await openChat(chat.id,identity.host);else await desktopSessions.open(chat.id);
    }
    if(!isCurrent()||!current())throw Error('聊天已切换，请重新打开会话菜单');
  };
  const context={title:view.title,subtitle:[clientNavigation.clients.find(client=>client.id===provider)?.name||provider,view.hostLabel||t('此电脑')].join(' · '),
    isCurrent,sessionId:nativeId,pinned:sessionPins.value(identity,chat.bridgePinned),archived:!!view.archived,
    pin:pinned=>{sessionPins.set(identity,pinned);toast(t('已在此设备保存，等待同步'));},
    link:provider==='codex'?window.BridgeSessionLinks.text(identity):null,
    nativeOrganization:true,projectName:view.projectName,
    organizationScope:provider==='codex'||caps.organization===true?'native':'bridge',
    renameScope:provider==='codex'||caps.rename===true?'native':'bridge',
    organization:async change=>{
      if(!isCurrent())throw Error('连接或账号已变化，请重新打开会话菜单');
      const result=await api(provider==='codex'?sessionUrl(chat.id,'organization',identity.host):
        ClientIdentity.path(provider,'organization',change?null:chat.id),
        provider==='codex'||!change?change:{...change,sessionId:chat.id},undefined,isCurrent);
      if(!isCurrent())throw Error('连接或账号已变化，请重新打开会话菜单');
      if(change)(provider==='codex'?loadList(true):desktopSessions.refresh()).catch(error=>toast(t(error.message)));
      return result;
    },
    details:[['客户端',clientNavigation.clients.find(client=>client.id===provider)?.name||provider],['会话 ID',nativeId,true],
      ['运行位置',view.hostLabel||t('此电脑')],['会话类型',provider==='codex'?sourceLabel(view.access):(view.backend||view.runtime||'Desktop')],
      ['工作目录',view.cwd,true],['模型',[view.model,view.effort].filter(Boolean).join(' · ')],['提供商',view.provider],['项目',view.projectName]],
    notifications:async()=>{await ensure();if(provider==='codex')return openChatNotifications();return desktopSessions.notifications();},
    manage:async()=>{try{await ensure();await openMessageManagement();}catch(error){toast(t(error.message));}}};
  if(['cli','ide'].includes(access.source)||view.backend==='cli'||view.runtime==='wsl')context.releaseCli=()=>cliConnectionsPanel.open({provider,sessionId:chat.id,host:identity.host});
  if(provider==='codex' && ['cli','ide'].includes(access.source) && (identity.host==='local'||identity.host.startsWith('codex:'))){
    const selection=JSON.stringify([clientNavigation.provider,currentId,currentHost]);
    context.cliNetworkIsCurrent=()=>isCurrent()&&selection===JSON.stringify([clientNavigation.provider,currentId,currentHost]);
    context.cliNetwork=async body=>{
      if(!context.cliNetworkIsCurrent())throw Error('连接或聊天已变化，请重新打开会话菜单');
      const result=await api(sessionUrl(chat.id,'cli-network',identity.host),body,undefined,context.cliNetworkIsCurrent);
      if(!context.cliNetworkIsCurrent())throw Error('连接或聊天已变化，请重新打开会话菜单');
      return result;
    };
  }
  const renameIds=new Map();
  if(provider!=='codex'||access.canConfigure)context.rename=async title=>{
    if(!isCurrent())throw Error('连接或账号已变化，请重新打开会话菜单');
    if(!renameIds.has(title))renameIds.set(title,uuid());
    const result=await api(provider==='codex'?sessionUrl(chat.id,'rename',identity.host):ClientIdentity.path(provider,'rename'),
      provider==='codex'?{title}:{title,id:renameIds.get(title),sessionId:chat.id},undefined,isCurrent);
    if(provider!=='codex'&&result.status!=='confirmed')throw Error('改名结果待核对；不会重复提交，请先在原客户端核对');
    if(!isCurrent())return;
    if(provider==='codex'){
      if(current()&&state){state.title=result.title;$('chat-title').textContent=result.title;}
      await loadList(true);
    }else{
      if(current()&&desktopSessions.state){desktopSessions.state.title=result.title;desktopSessions.title.textContent=result.title;}
      await desktopSessions.refresh();
    }
  };
  if(provider==='codex'&&access.canEditMessages||provider!=='codex'&&caps.fork===true)context.fork=async()=>{
    await ensure();
    if(provider==='codex'){
      if(!currentAccess().canEditMessages)throw Error('当前接入尚未开放聊天分支');
      const timeline=chatTimeline,row=[...timeline.rows.values()].map(item=>item.row).filter(row=>row.forkable).sort((a,b)=>b.order-a.order)[0];
      if(!row)throw Error('请等待聊天历史加载完成，并选择可创建分支的消息');
      return messageActions.open('fork',row,timeline);
    }
    const message=[...(desktopSessions.lastMessages||[])].reverse().find(row=>row.forkMessageId);
    if(!message)throw Error('当前聊天暂无可创建分支的消息');
    return desktopSessions.fork(message.forkMessageId);
  };
  const archiveId=uuid();
  if(provider==='codex'||caps[view.archived?'unarchive':'archive']===true)context.archive=async()=>{
    const body={archived:!view.archived,id:archiveId,...(!view.archived?{disconnect:true}:{})};
    const result=await api(provider==='codex'?sessionUrl(chat.id,'archive',identity.host):ClientIdentity.path(provider,'archive'),
      provider==='codex'?body:{...body,sessionId:chat.id},undefined,isCurrent);
    if(result.status!=='confirmed')throw Error(t('归档结果待核对，请检查原聊天；不会重复提交'));
    if(!isCurrent())return;
    if(!view.archived&&current()){
      if(provider==='codex')$('back').click();else desktopSessions.backToList();
    }
    if(provider==='codex')await loadList(true);else await desktopSessions.refresh();
  };
  if(provider==='codex'&&(identity.host==='local'||identity.host.startsWith('codex:'))){
    const url=sessionUrl(chat.id,'delete',identity.host);let deleteId=uuid();
    context.deletion=async snapshot=>{
      if(!isCurrent())throw Error('连接或账号已变化，请重新打开会话菜单');
      const result=await api(url,snapshot?{id:deleteId,scope:snapshot.scope,confirmed:true,...(snapshot.retry?{retry:snapshot.retry}:{})}:undefined,undefined,isCurrent);
      if(!snapshot&&result.retry?.includes(deleteId))deleteId=uuid();
      if(snapshot){
        if(result.status!=='confirmed')throw Error('删除结果待核对；不会重复提交，请在原客户端检查');
        if(!isCurrent())return result;
        if(currentHost===identity.host&&result.scope.includes(currentId))$('back').click();
        await loadList(true);
      }
      return result;
    };
  }
  if(view.forkedFrom)context.source={title:view.forkedFrom.title,open:()=>openChat(view.forkedFrom.id,view.forkedFrom.host)};
  if(view.attachedParent?.available)context.source={title:view.attachedParent.title,open:()=>openChat(view.attachedParent.id,view.attachedParent.host)};
  return context;
}
window.openCodexSessionMenu=button=>{
  if(!currentId||!state)return;
  getSessionMenu().show(sessionMenuContext({...listRows.find(row=>row.id===currentId&&row.host===currentHost),...state,id:currentId,host:currentHost}),{element:button});
};
async function loadList(reset=false){
  if(!reset&&(listLoading||!listHasMore))return;
  const generation=++listGeneration,offset=reset?0:listOffset,pinRead=sessionPins.capture();
  const query=$('search').value,archived=$('archived').checked,source=listSource,mode=$('list-mode').value;
  const filter=currentListFilter();
  if(reset&&filter!==listFilterKey){
    if(!restoreListSnapshot(filter)){listRows=[];availableSources=[];listOffset=0;listHasMore=false;listHostIds=[];listFilterKey=filter;}
    $('sessions').scrollTop=0;
  }
  const targetCount=reset?Math.max(100,listRows.length):offset+100;
  listComplete=false;listReadFinished=false;renderList();listLoading++;if($('sessions-more'))$('sessions-more').disabled=true;
  const groups=new Map();for(const row of listRows)(groups.get(row.host)||groups.set(row.host,[]).get(row.host)).push(row);
  const sources=new Set(availableSources),freshSources=new Map(),errors=[];
  const publish=()=>{
    if(generation!==listGeneration)return [];
    const merged=[...groups.values()].flat().sort((a,b)=>(mode==='recent'?Number(pinRow(b).bridgePinned)-Number(pinRow(a).bridgePinned):0)||(b.recency-a.recency)||b.id.localeCompare(a.id)||b.host.localeCompare(a.host));
    listRows=merged.slice(0,targetCount);availableSources=[...sources].sort();renderList();
    if(freshSources.size){
      listOffset=listRows.length;listHasMore=merged.length>=targetCount;
      listQuery=query;listArchived=archived;listFilterKey=filter;
      saveListSnapshot(filter);
    }
    return merged;
  };
  const read=async host=>{
    const url='/api/sessions?scope=host&host='+encodeURIComponent(host)+'&q='+encodeURIComponent(query)+'&offset='+(targetCount-100)+'&archived='+archived+'&source='+source+'&order='+encodeURIComponent(mode);
    try{
      const result=await api(url);if(generation!==listGeneration)return null;
      if(host==='local'){
        listHostIds=result.remoteHosts||[];
        for(const known of groups.keys())if(known!=='local'&&!listHostIds.includes(known))groups.delete(known);
      }
      if(result.complete===false||result.unavailableHosts?.length)errors.push(...(result.unavailableHosts||[{host,label:host,error:t('暂不可用')}]));
      else{
        for(const row of result.sessions){const key=chatKey(row.id,row.host);row.recency=Math.max(row.recency,recentInteractions.get(key)||0);}
        for(const row of result.sessions)sessionPins.observe({provider:'codex',host:row.host,id:row.id},row.bridgePinned,pinRead);
        const retained=mode==='recent'?(groups.get(host)||[]).filter(row=>sessionPins.pending({provider:'codex',host:row.host,id:row.id})&&pinRow(row).bridgePinned&&!result.sessions.some(fresh=>fresh.id===row.id)):[];
        groups.set(host,[...result.sessions,...retained]);freshSources.set(host,result.availableSources||[]);
        for(const item of result.availableSources||[])sources.add(item);
      }
      publish();return result;
    }catch(error){if(generation===listGeneration){errors.push({host,label:host,error:error.message});publish();}return null;}
  };
  try{
    const entry=selectedCodexEntry,local=await read(entry||'local');
    if(generation!==listGeneration)return;
    if(!entry)await Promise.all(listHostIds.map(read));
    if(generation!==listGeneration)return;
    const merged=publish();listReadFinished=true;listComplete=errors.length===0;
    if(listComplete){
      availableSources=[...new Set([...freshSources.values()].flat())].sort();
      listRows=merged.slice(0,targetCount);
      if(!renderSourceFilter()){loadList(true).catch(e=>toast(e.message));return;}
      saveListSnapshot(filter);clientNavigation.acknowledgeList('codex',{...local,unavailableHosts:[],complete:true});listSync.acknowledgeSuccess();
    }
    renderHostErrors(errors);renderList();refreshActivity();if(!errors.length)window.BridgeFeatures?.prefetchChat();
    return listComplete;
  }finally{listLoading--;if($('sessions-more'))$('sessions-more').disabled=false;}
}
function loadMoreSessions(){
  const root=$('sessions');
  if(document.hidden||$('app').hidden||!isCodexClient(clientNavigation.provider)||!root.clientHeight||listQuery!==$('search').value||listArchived!==$('archived').checked)return;
  if(root.scrollHeight-root.clientHeight-root.scrollTop<240)loadList().catch(e=>toast(e.message));
}
$('sessions').addEventListener('scroll',loadMoreSessions,{passive:true});
// Collapsed project groups may leave too little content for a scroll event.
$('sessions').addEventListener('wheel',event=>{if(event.deltaY>0)loadMoreSessions();},{passive:true});
let listTouch=null;
$('sessions').addEventListener('touchstart',event=>{listTouch=event.touches.length===1?{x:event.touches[0].clientX,y:event.touches[0].clientY}:null;},{passive:true});
$('sessions').addEventListener('touchmove',event=>{
  if(!listTouch)return;
  if(event.touches.length!==1){listTouch=null;return;}
  const dx=event.touches[0].clientX-listTouch.x,dy=listTouch.y-event.touches[0].clientY;
  if(Math.abs(dx)>Math.max(10,Math.abs(dy))){listTouch=null;return;}
  if(dy>40){listTouch=null;loadMoreSessions();}
},{passive:true});
$('sessions').addEventListener('touchend',()=>{listTouch=null;},{passive:true});
$('sessions').addEventListener('touchcancel',()=>{listTouch=null;},{passive:true});
$('search').oninput=()=>{clearTimeout(loadList.timer);loadList.timer=setTimeout(()=>loadList(true).catch(e=>toast(e.message)),300);};$('refresh').onclick=()=>loadList(true).catch(e=>toast(e.message));
let sessionDiagnosticsText='';
const diagnosticsPath='/api/sessions/diagnostics';
function diagnosticsSummary(value){
  const lines=[t('SSH 连接诊断')];
  for(const host of value.hosts||[]){
    const location=host.host==='local'?t('此电脑'):t('远端电脑')+' '+(String(host.host).match(/^remote-\d+$/)?.[0].slice(7)||'?'),status=host.status==='ok'?t('可用'):t('不可用');
    lines.push(location+' · '+status+' · '+t(host.phase==='ssh'?'SSH 连接':host.phase==='store'?'历史读取':'会话发现'));
    if(host.status==='ok'){
      const counts=host.counts||{};
      lines.push([['CLI',counts.cli],['Desktop',counts.desktop],[t('其他来源'),counts.other],[t('可见会话'),counts.visible],[t('已归档'),counts.archived]].map(([name,count])=>name+': '+(Number.isInteger(count)?count:0)).join(' · '));
    }else if(host.reason)lines.push(t(host.reason==='remote_read_failed'?'远端读取失败':'本机历史读取失败'));
  }
  return lines.join('\n');
}
async function loadSessionDiagnostics(){
  const status=$('session-diagnostics-status'),summary=$('session-diagnostics-summary'),error=$('session-diagnostics-error');
  status.textContent=t('正在读取 SSH 连接诊断…');summary.textContent='';error.textContent='';sessionDiagnosticsText='';
  $('session-diagnostics-copy').disabled=true;
  try{
    const value=await api(diagnosticsPath);
    sessionDiagnosticsText=diagnosticsSummary(value);summary.textContent=sessionDiagnosticsText;
    status.textContent=t('诊断已更新');$('session-diagnostics-copy').disabled=false;
  }catch(e){status.textContent='';error.textContent=t(e.message);}
}
$('open-session-diagnostics').onclick=()=>{
  $('appearance-dialog').close();$('session-diagnostics-dialog').showModal();
  const exportLink=$('session-diagnostics-export');
  const nativeUnsupported=BridgeHost.isNativeApp()&&window.BridgeMobileCapabilities?.diagnosticsExport!==true;
  exportLink.hidden=nativeUnsupported;
  $('session-diagnostics-export-note').hidden=!nativeUnsupported;
  $('session-diagnostics-export-note').textContent=nativeUnsupported?t('此版本 App 不支持导出文件，请复制摘要或更新 App。'):'';
  loadSessionDiagnostics();
};
$('session-diagnostics-refresh').onclick=loadSessionDiagnostics;
$('session-diagnostics-copy').onclick=()=>window.BridgeClipboard.copyWithFeedback(()=>sessionDiagnosticsText,$('session-diagnostics-copy'),toast);
$('session-diagnostics-export').onclick=event=>{if(BridgeHost.isNativeApp()&&window.BridgeMobileCapabilities?.diagnosticsExport!==true){event.preventDefault();return;}event.currentTarget.href=window.BridgeRelayURL?.(diagnosticsPath)||diagnosticsPath;};
$('archived').onchange=()=>loadList(true).catch(e=>toast(e.message));
const listSync=window.BridgeListSync=new ListSync({root:$('sessions'),status:$('list-sync'),refresh:()=>loadList(true),visible:()=>!$('app').hidden&&$('sidebar').getBoundingClientRect().width>0,ready:()=>!listLoading&&document.activeElement!==$('search')});
const contextCompactions=new Map(),contextCompacting=new Set();
const contextUsageControl=new ContextUsageControl({onCompact:async()=>{
  const id=currentId,host=currentHost,key=chatKey(id,host);if(contextCompacting.has(key)||!state?.connected)return false;
  const requestId=contextCompactions.get(key)||uuid();contextCompactions.set(key,requestId);contextCompacting.add(key);contextUsageControl.update({...state,busy:true});
  try{const result=await api(sessionUrl(id,'compact',host),{id:requestId});if(result.status!=='accepted')throw Error(result.message||t('结果待核对，请检查桌面原会话'));contextCompactions.delete(key);toast(t('已提交上下文压缩'));return true;}
  catch(error){toast(error.message);return false;}
  finally{contextCompacting.delete(key);if(currentId===id&&currentHost===host)contextUsageControl.update(state);}
}});$('permissions-button').after(contextUsageControl.button);
let newChatProjects=[],newChatEnvironments=[],newChatCanBrowse=false,newChatEntry='desktop',newChatPreferredHost='local',creatingChat=false,approvalBusy=0;
const newChatUrl=path=>path+(isCodexEntry(newChatEntry)?'?entry='+encodeURIComponent(newChatEntry):'');
const independentProject=()=>newChatEntry.startsWith('wsl:')||newChatEntry.startsWith('codex:')?newChatEntry+'|independent':'local|independent';
$('new-chat-title').value=t('新聊天');
$('new-chat-title').oninput=()=>{$('new-chat-title').dataset.edited='true';};
let newChatRequest=0,newChatModelsEnabled=false,newChatModelPicker=null;
function updateNewChatSubmit(){const selected=$('new-chat-project').value;$('new-chat-submit').disabled=creatingChat||!selected||selected==='__directory__'&&!$('new-chat-directory-path').value||newChatModelsEnabled&&(!newChatModelPicker||!newChatModelPicker.valid());}
function syncNewChatModels(){
  if(!newChatModelsEnabled){if(newChatModelPicker){newChatModelPicker.dispose();newChatModelPicker.root.remove();newChatModelPicker=null;}return;}
  if(!newChatModelPicker){const root=el('section');$('new-chat-form').insertBefore(root,$('new-chat-title').parentElement);newChatModelPicker=new window.BridgeCreationModelPicker({root,cache:modelCatalogs,namespace:()=>[listIdentity,newChatEnvironment().host,'codex'],isCurrent:()=>$('new-chat-dialog').open,onChange:updateNewChatSubmit});}
  const project=$('new-chat-project').value,path=$('new-chat-directory-path').value,kind=$('new-chat-type').value==='wsl'?'cli':$('new-chat-type').value;
  if(!project||project==='__directory__'&&!path){newChatModelPicker.clear();return;}
  const query=new URLSearchParams({kind,...(project==='__directory__'?{path,host:$('new-chat-directory-host').value}:{project})});
  const base=newChatUrl('/api/projects/models'),scope=JSON.stringify([listIdentity,codexActiveAccountId,newChatEntry,query.toString()]);
  newChatModelPicker.load(scope,refresh=>api(base+(base.includes('?')?'&':'?')+query+(refresh?'&refresh=true':'')));
}
$('new-chat-dialog').addEventListener('close',()=>{newChatModelPicker?.dispose();newChatModelPicker?.root.remove();newChatModelPicker=null;newChatModelsEnabled=false;});
function newChatEnvironment(){return newChatEnvironments.find(row=>row.key===$('new-chat-entry').value)||{host:isCodexEntry(newChatEntry)?newChatEntry:'local',name:newChatEntry.startsWith('wsl:')?wslEntries().find(row=>row.id===newChatEntry)?.name:t('此电脑')};}
function renderNewChatProjects(){
  const kind=$('new-chat-type').value,wsl=kind==='wsl',environment=newChatEnvironment(),host=environment.host;
  $('new-chat-project').disabled=false;
  $('new-chat-directory-host').replaceChildren(new Option(environment.name||environment.hostLabel||host,host));
  $('new-chat-directory').hidden=true;$('new-chat-directory-path').value='';
  const projects=newChatProjects.filter(row=>(wsl||(row.type||'desktop')===kind)&&row.host===host);
  $('new-chat-project').replaceChildren();
  if(wsl||newChatEntry.startsWith('codex:')||kind==='desktop'&&host==='local')$('new-chat-project').append(new Option(t('不选择项目'),independentProject()));
  if(newChatCanBrowse)$('new-chat-project').append(new Option(t('选择其他目录…'),'__directory__'));
  $('new-chat-directory-browse').hidden=!newChatCanBrowse;$('new-chat-directory-browse').disabled=false;
  for(const project of projects)$('new-chat-project').append(new Option(project.name,project.key));
  const selected=listRows.find(row=>row.id===currentId&&row.host===currentHost)?.projectKey;
  if(projects.some(row=>row.key===selected))$('new-chat-project').value=selected;
  else if(projects.length&&!wsl)$('new-chat-project').value=projects[0].key;
  $('new-chat-project').onchange();
  if(!wsl&&!newChatEnvironments.length){$('new-chat-directory-browse').hidden=true;$('new-chat-project').replaceChildren();$('new-chat-submit').disabled=true;}
}
async function loadSelectedNewChatProjects(){
  const generation=++newChatRequest,host=newChatEnvironment().host;
  renderNewChatProjects();
  if($('new-chat-type').value!=='cli'||host==='local'||newChatEntry.startsWith('codex:'))return;
  $('new-chat-submit').disabled=true;$('new-chat-directory-browse').disabled=true;$('new-chat-project').disabled=true;
  $('new-chat-path').textContent=t('正在读取电脑项目…');$('new-chat-error').textContent='';
  try{
    const value=await api('/api/projects?host='+encodeURIComponent(host));
    if(generation!==newChatRequest||!$('new-chat-dialog').open)return;
    newChatProjects=newChatProjects.filter(row=>row.host!==host).concat(value.projects||[]);
    renderNewChatProjects();
  }catch(error){if(generation===newChatRequest&&$('new-chat-dialog').open){renderNewChatProjects();$('new-chat-error').textContent=t(error.message);}}
}
async function loadNewChatProjects(){
  const generation=++newChatRequest,kind=$('new-chat-type').value,wsl=kind==='wsl';
  newChatEntry=wsl?$('new-chat-entry').value:currentHost.startsWith('codex:')?currentHost:'desktop';
  const entry=newChatEntry;
  $('new-chat-description').textContent=wsl?'Codex · WSL':kind==='cli'?'Codex CLI':'Codex App';
  $('new-chat-error').textContent='';$('new-chat-submit').disabled=true;$('new-chat-directory-browse').disabled=true;
  $('new-chat-project').replaceChildren();$('new-chat-path').textContent=t('正在读取电脑项目…');
  $('new-chat-location-summary').textContent=t('正在读取电脑项目…');$('new-chat-location').hidden=false;
  $('new-chat-submit').textContent=t(wsl||kind==='cli'?'创建聊天':'创建并打开');
  if(wsl&&!entry)return;
  if(wsl){newChatProjects=[];newChatEnvironments=[];newChatCanBrowse=true;$('new-chat-entry-label').hidden=false;renderNewChatProjects();}
  try{
    const value=await api(newChatUrl('/api/projects'));
    if(generation!==newChatRequest||entry!==newChatEntry||!$('new-chat-dialog').open)return;
    newChatModelsEnabled=value.createModels===true;newChatProjects=value.projects||[];newChatCanBrowse=wsl||kind==='desktop'||Array.isArray(value.environments);
    if(!wsl){
      const environments=Array.isArray(value.environments)?value.environments.filter(row=>(row.type||row.surface)===kind):[];
      if(!Array.isArray(value.environments)){const hosts=new Map([['local',t('此电脑')]]);for(const row of newChatProjects)hosts.set(row.host,row.host==='local'?t('此电脑'):row.hostLabel||row.host);for(const [host,name] of hosts)environments.push({key:kind+'|'+host,host,name});}
      newChatEnvironments=environments;const previous=$('new-chat-entry').value;
      $('new-chat-entry').replaceChildren(...environments.map(row=>new Option(row.host==='local'?(row.name||row.hostLabel||t('此电脑')).replace('此电脑',t('此电脑')):row.name||row.hostLabel,row.key)));
      const selected=environments.find(row=>row.key===previous)||environments.find(row=>row.host===newChatPreferredHost);
      if(selected)$('new-chat-entry').value=selected.key;
      $('new-chat-entry-label').hidden=environments.length<2;
    }
    renderNewChatProjects();
    if(!wsl&&kind==='cli'&&newChatEnvironment().host!=='local')await loadSelectedNewChatProjects();
  }catch(error){if(generation===newChatRequest){$('new-chat-error').textContent=t(error.message);$('new-chat-path').textContent='';}}
}
$('new-chat').onclick=async()=>{
  const entries=wslEntries(),native=codexEntries().find(row=>row.id==='desktop'),type=$('new-chat-type');
  const wslOption=[...type.options].find(option=>option.value==='wsl');
  if(clientNavigation.wslSupported!==true)wslOption?.remove();
  else if(!wslOption)type.append(new Option('WSL','wsl'));
  for(const option of type.options)option.disabled=option.value==='wsl'?!entries.length:native?.enabled===false;
  $('new-chat-entry').replaceChildren(...entries.map(row=>new Option(row.name||row.label,row.id)));
  const preferred=currentHost.startsWith('wsl:')?currentHost:preferredWslEntry;
  if(entries.some(row=>row.id===preferred))$('new-chat-entry').value=preferred;
  newChatPreferredHost=currentHost;newChatEnvironments=[];
  type.disabled=currentHost.startsWith('codex:');
  type.value=currentHost.startsWith('codex:')?'cli':native?.enabled===false&&entries.length?'wsl':listSource==='wsl'&&entries.length?'wsl':listSource==='cli'?'cli':listSource==='all'&&currentHost.startsWith('wsl:')&&entries.length?'wsl':'desktop';
  $('new-chat-dialog').showModal();await loadNewChatProjects();
};
$('new-chat-type').onchange=()=>{
  if($('new-chat-type').value==='wsl'){$('new-chat-entry').replaceChildren(...wslEntries().map(row=>new Option(row.name||row.label,row.id)));if(wslEntries().some(row=>row.id===preferredWslEntry))$('new-chat-entry').value=preferredWslEntry;}
  return loadNewChatProjects();
};
$('new-chat-entry').onchange=()=>{if($('new-chat-type').value==='wsl'){preferredWslEntry=$('new-chat-entry').value;return loadNewChatProjects();}return loadSelectedNewChatProjects();};
$('new-chat-project').onchange=()=>{
  const value=$('new-chat-project').value,custom=value==='__directory__',path=custom?$('new-chat-directory-path').value:newChatProjects.find(row=>row.key===value)?.cwd||'';
  $('new-chat-directory').hidden=!custom;$('new-chat-directory-path').required=custom;
  $('new-chat-path').textContent=path;
  $('new-chat-location').hidden=!path;
  $('new-chat-location-summary').textContent=path.split(/[\\/]/).filter(Boolean).slice(-2).join(' / ')||path;
  $('new-chat-location').open=false;
  const browseLabel=$('new-chat-directory-browse').querySelector('[data-i18n]');browseLabel.dataset.i18n=path?'更换目录':'选择目录';browseLabel.textContent=t(browseLabel.dataset.i18n);
  syncNewChatModels();updateNewChatSubmit();
};
let folderPickerData=null;
$('new-chat-directory-host').onchange=()=>{$('new-chat-directory-path').value='';$('new-chat-project').onchange();};
$('new-chat-directory-browse').onclick=async()=>{
  if(!newChatCanBrowse)return;
  const request=newChatRequest,entry=newChatEntry,host=$('new-chat-directory-host').value,url=newChatUrl('/api/projects/browse');
  folderPickerData=null;
  return window.BridgeProjectPicker.choose({environment:host==='local'?(newChatEnvironment().name||'此电脑').replace('此电脑',t('此电脑')):newChatEnvironment().name,path:$('new-chat-directory-path').value||newChatProjects.find(row=>row.key===$('new-chat-project').value)?.cwd||null,
    request:body=>api(url,{host,...body}),isCurrent:()=>request===newChatRequest&&entry===newChatEntry&&$('new-chat-dialog').open,
    onChange:value=>folderPickerData=value,onSelect:value=>{$('new-chat-project').value='__directory__';$('new-chat-directory-path').value=value.path;$('new-chat-project').onchange();}});
};
$('new-chat-dialog').addEventListener('cancel',event=>{if(creatingChat)event.preventDefault();});
$('new-chat-form').onsubmit=async event=>{
  event.preventDefault();if(creatingChat)return;
  const project=$('new-chat-project').value,title=$('new-chat-title').value.trim(),kind=newChatEntry.startsWith('wsl:')?'wsl':newChatEntry.startsWith('codex:')?'cli':$('new-chat-type').value;
  const directory=project==='__directory__'?{path:$('new-chat-directory-path').value.trim(),createDirectory:false,host:$('new-chat-directory-host').value,...(kind==='cli'?{kind:'cli'}:{})}:null;
  if(directory&&!directory.path){$('new-chat-error').textContent=t('请输入完整的项目目录');return;}
  if(newChatModelsEnabled&&(!newChatModelPicker||!newChatModelPicker.valid()))return;
  const modelOptions=newChatModelsEnabled?newChatModelPicker.selection():{};
  const selection=directory?JSON.stringify(directory):project;
  if(!project||!title){$('new-chat-error').textContent=t('请选择项目或不选择项目，并填写聊天名称。');return;}
  let attempt;
  try{attempt=JSON.parse(sessionStorage.getItem('new-chat-attempt')||'null');}catch{}
  const attemptKind=kind==='wsl'?undefined:kind;
  if(!attempt||attempt.selection!==selection||attempt.title!==title||attempt.entry!==newChatEntry||attempt.kind!==attemptKind||JSON.stringify(window.BridgeCreationModelPicker?.intent(attempt.modelOptions||{})||attempt.modelOptions||{})!==JSON.stringify(window.BridgeCreationModelPicker?.intent(modelOptions)||modelOptions))attempt={id:uuid(),selection,project,title,entry:newChatEntry,modelOptions,...(attemptKind?{kind:attemptKind}:{})};
  attempt.modelOptions=modelOptions;sessionStorage.setItem('new-chat-attempt',JSON.stringify(attempt));
  newChatRequest++;creatingChat=true;newChatModelPicker?.setBusy(true);setLanguageBusy();$('new-chat-error').textContent='';$('new-chat-submit').textContent=t('正在创建…');
  $('new-chat-form').querySelectorAll('input,select,button').forEach(n=>n.disabled=true);
  document.querySelector('[data-close="new-chat-dialog"]').disabled=true;
  try{
    if(directory&&attempt.project==='__directory__'){attempt.project=(await api(newChatUrl('/api/projects'),directory)).project.key;sessionStorage.setItem('new-chat-attempt',JSON.stringify(attempt));}
    if(kind==='desktop'&&attempt.project!=='local|independent'){
      const trust=await api('/api/projects/trust',{key:attempt.project,confirmed:false});
      if(trust.required){
        const accepted=await BridgeProjectPicker.confirmTrust({path:trust.path,environment:trust.environment,isCurrent:()=>$('new-chat-dialog').open&&creatingChat});
        if(!accepted)return;
        await api('/api/projects/trust',{key:attempt.project,confirmed:true,confirmation:trust.confirmation});
      }
    }
    const result=await api(newChatUrl('/api/sessions'),{id:attempt.id,project:attempt.project,title:attempt.title,...attempt.modelOptions,...(attempt.kind?{kind:attempt.kind}:{})});
    sessionStorage.removeItem('new-chat-attempt');$('new-chat-dialog').close();
    $('search').value='';$('archived').value='false';
    loadList(true).catch(e=>toast(e.message));toast(result.message);
    await openChat(result.id,result.host);
  }catch(e){
    if($('new-chat-dialog').open)$('new-chat-error').textContent=t(e.message)+t('；若结果不确定，请先刷新列表检查，避免重复创建。');
    else toast(t('聊天已创建，连接暂未完成：')+t(e.message));
  }finally{
    creatingChat=false;setLanguageBusy();$('new-chat-submit').textContent=t(kind==='desktop'?'创建并打开':'创建聊天');
    $('new-chat-form').querySelectorAll('input,select,button').forEach(n=>n.disabled=false);
    const selected=$('new-chat-project').value;
    newChatModelPicker?.setBusy(false);updateNewChatSubmit();
    document.querySelector('[data-close="new-chat-dialog"]').disabled=false;
  }
};
const messageActions=new MessageActions({request:api,openChat,refresh:()=>loadList(true).catch(e=>toast(e.message)),uuid,notify:toast,busy:value=>{approvalBusy+=value?1:-1;setLanguageBusy();}});
desktopSessions.messageActions=messageActions;
document.addEventListener('bridge-message-error',event=>toast(event.detail));
function messageManagementTarget(){
  if(clientNavigation.provider!=='codex'){
    return desktopSessions.sid&&desktopSessions.draftScope?{scope:desktopSessions.draftScope,drafts:desktopSessions.composerDrafts,uuid}:null;
  }
  return currentId&&codexDraftScope?{scope:codexDraftScope,drafts:composerDrafts,temporary:sessionStorage,temporaryKey:'pending:'+chatKey(),
    url:sessionUrl(currentId,'message-management',currentHost),sendUrl:sessionUrl(currentId,'send',currentHost),uuid}:null;
}
function messageManagementChanged(recovery){
  if(currentId&&clientNavigation.provider==='codex'){
    const cleared=recovery?.clearedDraft;
    if(cleared&&composerDrafts.key(cleared.scope)===composerDrafts.key(codexDraftScope)&&!composerDrafts.load(codexDraftScope)&&
       $('message').value===cleared.draft.text&&JSON.stringify(codexQuotes?.quotes||[])===JSON.stringify(cleared.draft.quotes||[])){
      restoringComposer=true;$('message').value='';codexQuotes?.reset();selectedSkills.clear();renderSkillPills();restoringComposer=false;
      const references=cleared.body.references||[],ids=[...(cleared.body.attachments||[]),...attachments.rows.filter(row=>row.reference&&references.some(ref=>JSON.stringify(ref)===JSON.stringify(row.reference))).map(row=>row.id)];
      attachments.clear(chatKey(),ids);workModes.submitted(chatKey(),cleared.body.workMode);
      codexProvisionalDrafts.delete(chatKey());try{sessionStorage.removeItem('draft:'+chatKey());sessionStorage.removeItem('skills:'+chatKey());}catch{}
      window.BridgePresentation?.resizeMessage();
    }
    if(recovery?.resolved?.some(row=>['accepted','queued'].includes(row.status))&&!composerDrafts.pendingSend(codexDraftScope))$('send-error').textContent='';
    renderQueue(state?.submissions||[]);
    if(!composerDrafts.pendingSend(codexDraftScope)&&$('send-error').textContent===t('上次发送结果尚未确认，请检查聊天记录或先处理未确认记录。'))$('send-error').textContent='';
  }
}
const messageManagement=typeof MessageManagementBinding==='function'?new MessageManagementBinding({target:messageManagementTarget,request:api,notify:toast,translate:t,
  beforeOpen:()=>{document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());},onChanged:messageManagementChanged}):null;
function openMessageManagement(){if(!messageManagementTarget()){toast('请先打开聊天并等待账号确认');return;}return messageManagement?.open();}
$('open-message-management').onclick=openMessageManagement;
desktopSessions.onManageMessages=openMessageManagement;

codexQuotes=typeof ComposerQuotes==='function'?new ComposerQuotes({messages:$('messages'),input:$('message'),container:$('message').parentElement,scope:()=>desktopSessions.provider==='codex'&&currentId&&codexDraftScope?.gateway===draftGateway&&codexDraftScope?.session===currentId&&codexDraftScope?.host===currentHost?codexDraftScope:null,onChange:()=>{codexDraftTouched=true;saveDraft();},onError:text=>{$('send-error').textContent=text;},onPrompt:text=>{const input=$('message'),start=input.selectionStart,end=input.selectionEnd;input.value+=(input.value?'\n\n':'')+text;input.setSelectionRange(start,end);input.oninput();window.BridgePresentation?.resizeMessage();},translate:t}):null;
workbench.quoteMessage=(text,source,target)=>{if(target.provider==='codex'&&target.id===currentId&&target.host===currentHost&&desktopSessions.provider==='codex')codexQuotes?.add(text,source);};

function imageComposerTarget(){
  if($('app').hidden||!$('app').classList.contains('chat-open'))return null;
  const codex=isCodexClient(clientNavigation.provider),view=desktopSessions;
  const quotes=codex?codexQuotes:view.quotes,panel=codex?attachments:view.attachments,workspace=codex?workbench:view.workbench;
  const id=codex?currentId:view.sid,provider=codex?'codex':view.provider,host=codex?currentHost:'local';
  if(!id||!quotes?.available()||!panel||panel.locked)return null;
  const key=codex?chatKey(id,host):JSON.stringify([provider,id]);
  if(panel.key!==key||!workspace?.current||(workspace.current.chatId||workspace.current.id)!==id||workspace.current.host!==host||(workspace.current.provider||'codex')!==provider)return null;
  if(codex){if(view.provider!=='codex'||$('chat').hidden||sending||!state||state.loadingHistory||state.activating||!currentAccess().canSend)return null;}
  else if(clientNavigation.provider!==provider||view.container.hidden||view.conversation.hidden||view.busy||!view.canMutate()||view.state?.capabilities?.attachments!==true)return null;
  return {provider,id,host,key,panel,workspace,scope:quotes.identity()};
}
function composerImageSource(value,target,capture=false){
  if(!value||!target)return null;
  if(value.context){
    const context=value.context;
    if(context.provider!==target.provider||context.id!==target.id||context.host!==target.host)return null;
    if(capture&&!value.url)return true;
    return value.composerScope===target.scope&&window.BridgeImageViewer.isRegisteredSource(value)&&/^data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(value.url)?value.url:null;
  }
  let url;try{url=new URL(value.url,location.href);}catch{return null;}
  if(url.origin!==location.origin||url.username||url.password||url.hash)return null;
  const path=window.BridgeRelayPath?.(url.pathname)??url.pathname;
  const codex=/^\/api\/sessions\/([0-9a-f-]{36})\/(uploads\/[0-9a-f-]{36}\/preview|desktop-images\/[a-f0-9]{64}|files\/[a-f0-9]{64})$/.exec(path);
  const desktop=/^\/api\/desktop-sessions\/(claude|deepseek|zcode|antigravity)\/uploads\/[0-9a-f-]{36}\/preview$/.exec(path);
  const identityKey=codex?'host':'sessionId',identity=codex?target.host:target.id;
  if(codex?target.provider!=='codex'||codex[1]!==target.id:!desktop||desktop[1]!==target.provider)return null;
  if(url.searchParams.getAll(identityKey).length!==1||url.searchParams.get(identityKey)!==identity)return null;
  const variants=url.searchParams.getAll('variant');
  if(variants.length>1||variants.some(value=>!['thumb','original'].includes(value))||codex&&!codex[2].startsWith('uploads/')&&variants.length)return null;
  if([...url.searchParams.keys()].some(key=>key!==identityKey&&key!=='variant'))return null;
  return url.href;
}
window.BridgeImageViewer?.configureComposer({
  scope:()=>imageComposerTarget()?.scope??null,
  canReuse:value=>!!composerImageSource(value,imageComposerTarget(),true),
  async add(value,scope){
    const target=imageComposerTarget(),source=composerImageSource(value,target);
    if(!source||scope!==target.scope)return false;
    const response=await fetch(source,{credentials:'same-origin',redirect:'error'});
    if(!response.ok||response.redirected)throw Error(t('附件上传失败，请重试'));
    if(Number(response.headers.get('Content-Length'))>20*1024*1024)throw Error(t('每个附件需为 1 字节至 20 MB'));
    const blob=await response.blob();
    if(!/^image\/(?:png|jpeg|gif|webp)$/.test(blob.type))throw Error(t('此应用的消息附件仅支持图片；其他文件请使用文件传输。'));
    if(!blob.size||blob.size>20*1024*1024)throw Error(t('每个附件需为 1 字节至 20 MB'));
    const current=imageComposerTarget();
    if(!current||current.scope!==scope||current.panel!==target.panel||current.key!==target.key||!composerImageSource(value,current))return false;
    const file=new File([blob],value.name||t('图片附件'),{type:blob.type}),count=current.panel.rows.length;
    current.panel.add([file]);
    if(current.panel.rows.length===count){if(current.panel.error)throw Error(t(current.panel.error));return false;}
    current.workspace.select('chat');
    if(current.provider==='codex')window.BridgePresentation?.openChat();else desktopSessions.collapseComposer(false);
    return true;
  }
});

function saveDraft(){
  if(!currentId||restoringComposer)return null;
  const fields={quotes:codexQuotes?.quotes.map(quote=>({...quote}))||[],text:$('message').value,selectionStart:$('message').selectionStart,selectionEnd:$('message').selectionEnd,sendMode:$('send-mode').value,workMode:workModes.selection||$('work-mode').value,skills:[...selectedSkills]};
  if(!codexDraftScope||!composerDrafts){if(draftGateway&&composerDrafts){if(codexDraftTouched)codexProvisionalDrafts.set(chatKey(),{gateway:draftGateway,account:'',...fields});}else sessionStorage.setItem('draft:'+chatKey(),fields.text);return null;}
  const saved=composerDrafts.save(codexDraftScope,fields);
  if(saved){codexProvisionalDrafts.delete(chatKey());sessionStorage.removeItem('draft:'+chatKey());sessionStorage.removeItem('skills:'+chatKey());}
  else{codexProvisionalDrafts.set(chatKey(),{gateway:draftGateway,account:codexDraftScope.account,...fields});sessionStorage.setItem('draft:'+chatKey(),fields.text);}
  return saved;
}
function bindCodexDraft(view){
  const account=view?.draftAccountIdentity||'';
  if(!account&&codexDraftScope){saveDraft();sessionStorage.removeItem('draft:'+chatKey());sessionStorage.removeItem('skills:'+chatKey());codexDraftScope=null;codexDraftTouched=false;codexQuotes?.reset();$('message').value='';selectedSkills.clear();renderSkillPills();return;}
  if(!draftGateway||!account||!currentId||!composerDrafts)return;
  const scope={gateway:draftGateway,provider:'codex',account,host:currentHost,session:currentId};
  const continuation=view?.draftContinuation;
  if(/^wsl:[0-9a-f]{32}$/.test(currentHost)&&view?.id===currentId&&view.host===currentHost&&
     continuation?.id===currentId&&continuation.entry===currentHost&&continuation.accountIdentity===account&&
     /^[a-f0-9]{64}$/.test(account)&&Array.isArray(continuation.previousAccountIdentities)&&continuation.previousAccountIdentities.length<=32){
    if(codexDraftScope&&codexDraftScope.account!==account)saveDraft();
    const options={temporary:sessionStorage,temporaryKey:'pending:'+chatKey()};
    if(!composerDrafts.pendingRecords(scope,options).length)for(const previousAccount of [...continuation.previousAccountIdentities].reverse()){
      if(!/^[a-f0-9]{64}$/.test(previousAccount)||previousAccount===account)continue;
      const previous={...scope,account:previousAccount},draft=composerDrafts.load(previous);
      if(!draft||!(draft.text||draft.quotes?.length)||composerDrafts.pendingRecords(previous,options).length)continue;
      if(composerDrafts.continueAccount(previous,scope))break;
    }
  }
  if(composerDrafts.key(scope)===composerDrafts.key(codexDraftScope))return;
  const switching=!!codexDraftScope;
  if(switching){saveDraft();sessionStorage.removeItem('draft:'+chatKey());sessionStorage.removeItem('skills:'+chatKey());let oldPending;try{oldPending=JSON.parse(sessionStorage.getItem('pending:'+chatKey()));}catch{}if(!oldPending?.account)sessionStorage.removeItem('pending:'+chatKey());}
  codexDraftScope=scope;
  const pending=codexProvisionalDrafts.get(chatKey());
  if(pending&&(pending.gateway!==draftGateway||pending.account&&pending.account!==account)){codexProvisionalDrafts.delete(chatKey());sessionStorage.removeItem('draft:'+chatKey());sessionStorage.removeItem('skills:'+chatKey());}
  const provisional=codexProvisionalDrafts.get(chatKey());
  const promoted=provisional&&composerDrafts.save(scope,provisional);
  if(promoted){codexProvisionalDrafts.delete(chatKey());sessionStorage.removeItem('draft:'+chatKey());sessionStorage.removeItem('skills:'+chatKey());}
  const stored=composerDrafts.load(scope);
  const legacy=(stored||provisional||window.BridgeSharedRelay)?null:composerDrafts.migrate(scope,sessionStorage,{text:'draft:'+chatKey(),skills:'skills:'+chatKey()});
  const draft=promoted||provisional||stored||legacy;
  if(draft&&(switching||!codexDraftTouched)){
    restoringComposer=true;
    $('message').value=draft.text;codexQuotes?.restore(draft.quotes);
    $('message').setSelectionRange(draft.selectionStart,draft.selectionEnd);
    $('send-mode').value=draft.sendMode;
    workModes.selection=draft.workMode;workModes.render(state,$('send-mode').value,sending);
    selectedSkills=new Set(draft.skills);renderSkillPills();
    restoringComposer=false;
    window.BridgePresentation?.resizeMessage();
  }else if(switching||!codexDraftTouched){codexQuotes?.reset();$('message').value='';$('send-mode').value='send';workModes.selection=null;workModes.render(state,'send',sending);selectedSkills.clear();renderSkillPills();}
  if(codexDraftTouched&&!switching)saveDraft();
  codexDraftTouched=false;
  messageManagement?.reconcile();
}
$('message').oninput=()=>{codexDraftTouched=true;saveDraft();};
$('message').addEventListener('keyup',saveDraft);
$('message').addEventListener('click',saveDraft);
$('work-mode').addEventListener('change',()=>{codexDraftTouched=true;saveDraft();});

let catalogStarted=false;
let chatOpenGeneration=0;
async function openChat(id,host="local"){
  sharedSessionMenu?.close();
  if(messageActions.pending&&messageActions.dialog.open)return;
  const openGeneration=++chatOpenGeneration,routeProvider=clientNavigation.provider;
  if(currentId===id&&currentHost===host&&chatTimeline&&!chatTimeline.abort.signal.aborted){
    workbench.select(workbench.current?.chatId===workbench.current?.id?'chat':'child:'+id);$('app').classList.add('chat-open');window.BridgePresentation?.openChat();browserNavigation.sync();reportVisibleChat();return;
  }
  let route;
  const known=listRows.find(row=>row.id===id&&row.host===host);
  try {route=known&&!known.attachedParentId?{rootId:id,cwd:known.cwd}:await workbench.resolveChat(id,host,async target=>(await api(sessionUrl(target,'timeline',host)+'&limit=1')).meta);}
  catch(error) {if(openGeneration===chatOpenGeneration)toast(t(error.message));return;}
  if(openGeneration!==chatOpenGeneration||clientNavigation.provider!==routeProvider)return;
  messageActions.dialog.close();workbench.open(id,host,route);saveDraft();chatTimeline?.dispose();codexQuotes?.reset();currentId=id;currentHost=host;refreshEntryAccounts();codexDraftScope=null;codexDraftTouched=false;state=null;catalogStarted=false;workbench.current.access=sessionAccess(listRows.find(row=>row.id===id&&row.host===host)?.access);contextUsageControl.update(null);workModes.open(chatKey(id,host));$('send-mode').value='send';attachments.open(chatKey(id,host));modelCatalogGeneration++;catalogData={models:null,skills:null};catalogResults={models:null,skills:null};catalogTasks={models:null,skills:null};skillIndex=new Map();skillTotal=0;skillPages=[];skillQuery={q:'',offset:0,limit:200};invalidSkills=new Set();skillRequest++;modelRequest++;modelTarget=null;fastModeControl.reset();$('model-dialog').close();notificationState={available:false,watching:false,notifyOnCompletion:false};notificationRequest++;$('notify-dialog').close();try{selectedSkills=new Set(JSON.parse(!draftGateway?sessionStorage.getItem('skills:'+chatKey(id,host))||'[]':'[]'));}catch{selectedSkills=new Set();}renderSkillPills();approvalStamp='';submittedRequestIds.clear();$('messages').replaceChildren();$('approvals').replaceChildren();$('queued').replaceChildren();$('message').value=!draftGateway?sessionStorage.getItem('draft:'+chatKey(id,host))||'':'';$('send-error').textContent='';$('chat-title').textContent=t('正在连接…');$('status').textContent=t('连接中');$('welcome').hidden=true;$('chat').hidden=false;$('app').classList.add('chat-open');$('send').disabled=true;if(!browserNavigation.sync({provider:'codex',id,host}))history.replaceState(null,'','#'+id+'~'+encodeURIComponent(host));document.querySelectorAll('.session').forEach(n=>n.classList.toggle('selected',n.dataset.id===id&&n.dataset.host===host));const opening=workbench.current;
try{if(window.BridgeFeatures)await window.BridgeFeatures.load('chat');}catch(error){if(openGeneration===chatOpenGeneration&&workbench.current===opening&&$('app').classList.contains('chat-open')){$('status').textContent=t('连接失败');$('send-error').textContent=t(error.message);}return;}
if(openGeneration!==chatOpenGeneration||workbench.current!==opening||currentId!==id||currentHost!==host||!$('app').classList.contains('chat-open'))return;
const timeline=chatTimeline=new ChatTimeline({cacheKey:listIdentity?JSON.stringify([listIdentity,host,id]):'',url:action=>sessionUrl(id,action,host),request:api,renderMeta:renderState,onReady:reportVisibleChat,onFile:async path=>{const session=workbench.current;if(currentId!==id||currentHost!==host||!session||(session.chatId||session.id)!==id||session.host!==host)return;try{const childRelative=timelineFilePath(path,state?.cwd),relative=timelineFilePath(state.cwd.replace(/[\\/]$/,'')+'/'+childRelative,session.cwd||state.cwd);await workbench.request(workbench.url(session,'info',relative));if(workbench.current!==session||currentId!==id||currentHost!==host)return;await workbench.locate(session,relative);}catch(error){if(workbench.current===session)toast(t(error.message));}},canEdit:()=>currentAccess().canEditMessages,renderText:(node,text,files,fullText)=>renderMarkdown(node,text,files,file=>timeline.fileUrl(file),{fullText}),onAction:(action,row,timeline)=>{if(!currentAccess().canEditMessages)return;const pendingKey='fork-edit:'+host+'|'+id;let pending;try{pending=JSON.parse(sessionStorage.getItem(pendingKey));}catch{}if(pending&&pending.sourceTurnId===row.turnId&&action==='edit'){sessionStorage.setItem('message-edit:'+timeline.url('message-action')+':'+row.key,pending.text);sessionStorage.removeItem(pendingKey);}return messageActions.open(action,row,timeline);},status:text=>{$('status').textContent=t(text);}});
  reconnectFailure=null;
  window.BridgePresentation?.openChat();
  const reading=timeline.start();
  const openingAccess=sessionAccess(listRows.find(row=>row.id===id&&row.host===host)?.access);
  if(openingAccess.source==='desktop'&&openingAccess.canReconnect)api(sessionUrl(id,'reconnect',host),{activate:true},timeline.abort.signal).catch(error=>{if(!timeline.abort.signal.aborted)toast(error.message);});
  await reading;
  if(!timeline.abort.signal.aborted&&workbench.current?.chatScroll?.has(id))$('timeline').scrollTop=workbench.current.chatScroll.get(id);
  if(!timeline.abort.signal.aborted){scheduleCatalog();loadNotificationState(id,host);}
}
let notificationState={available:false,watching:false,notifyOnCompletion:false},notificationRequest=0;
async function loadNotificationState(id=currentId,host=currentHost){
  const request=++notificationRequest;
  try{const result=await api(sessionUrl(id,'notifications',host));if(request!==notificationRequest||id!==currentId||host!==currentHost)return;notificationState=result;return true;}
  catch(e){if(request===notificationRequest&&id===currentId&&host===currentHost){$('notify-error').textContent=t(e.message);notificationState={...notificationState,error:e.message};}return false;}
}
async function openChatNotifications(){
  const key=chatKey();
  $('notify-error').textContent='';$('notify-dialog').showModal();
  for(const id of ['notify-enabled','notify-completion','notify-save'])$(id).disabled=true;
  const loaded=await loadNotificationState();
  if(!$('notify-dialog').open||key!==chatKey()||!loaded)return;
  $('notify-enabled').value=notificationState.requests||'inherit';
  $('notify-completion').value=notificationState.completion||'inherit';
  for(const id of ['notify-enabled','notify-completion','notify-save'])$(id).disabled=false;
  $('notify-channel-status').textContent=t(notificationState.available?'通知通道已开启':'尚未配置通知通道，请在第三方通知中配置。');
}
$('notify-form').onsubmit=async event=>{
  event.preventDefault();
  const id=currentId,host=currentHost,request=++notificationRequest;
  const body={requests:$('notify-enabled').value,completion:$('notify-completion').value};
  $('notify-save').disabled=true;$('notify-error').textContent='';
  try{
    const result=await api(sessionUrl(id,'notifications',host),body);
    if(request!==notificationRequest||id!==currentId||host!==currentHost)return;
    notificationState=result;$('notify-dialog').close();toast('聊天提醒设置已保存');
  }catch(e){if(request===notificationRequest&&id===currentId&&host===currentHost)$('notify-error').textContent=t(e.message);}
  finally{if(request===notificationRequest&&id===currentId&&host===currentHost){$('notify-save').disabled=false;}}
};
async function loadResetDynamicSetting(){
  const input=$('appearance-reset-dynamic');input.disabled=true;
  try{const value=await api('/api/accounts');input.checked=!!value.resetDynamicEnabled;input.disabled=false;}
  catch(error){$('reset-dynamic-error').textContent=t(error.message);}
}
$('appearance-reset-dynamic').onchange=async event=>{
  const input=event.target;input.disabled=true;$('reset-dynamic-error').textContent='';
  try{const value=await api('/api/accounts/reset-dynamic',{enabled:input.checked});input.checked=value.resetDynamicEnabled;accountsPanel?.accept(value);if(value.resetDynamicEnabled&&accountsPanel?.visible())accountsPanel.refresh();}
  catch(error){$('reset-dynamic-error').textContent=t(error.message);await loadResetDynamicSetting();}
  finally{input.disabled=false;}
};
let notificationDefaultsRequest=0;
window.loadNotificationDefaults=async()=>{
  if(window.BridgeSharedRelay){$('appearance-reset-dynamic').closest('fieldset').hidden=true;return;}
  loadResetDynamicSetting();
  const request=++notificationDefaultsRequest;
  for(const id of ['notification-requests','notification-completions'])$(id).disabled=true;
  $('notification-settings-error').textContent='';
  if($('app').hidden)return;
  try{
    const result=await api('/api/notifications/defaults');if(request!==notificationDefaultsRequest)return;
    $('notification-requests').checked=result.requests;$('notification-completions').checked=result.completion;
    for(const id of ['notification-requests','notification-completions'])$(id).disabled=false;
  }catch(error){$('notification-settings-error').textContent=t(error.message);}
};
async function saveNotificationDefaults(){
  const body={requests:$('notification-requests').checked,completion:$('notification-completions').checked};
  for(const id of ['notification-requests','notification-completions'])$(id).disabled=true;
  try{await api('/api/notifications/defaults',body);if(currentId)await loadNotificationState();}
  catch(error){await window.loadNotificationDefaults();$('notification-settings-error').textContent=t(error.message);return;}
  await window.loadNotificationDefaults();
}
$('notification-requests').onchange=$('notification-completions').onchange=saveNotificationDefaults;
$('back').onclick=()=>{saveDraft();workbench.cancelLocate();desktopSessions.onListEnter();workbench.rememberScroll();workbench.stopThumbnails();$('app').classList.remove('chat-open');if(!browserNavigation.sync(null))history.replaceState(null,'',location.pathname);loadList(true).catch(e=>toast(e.message));};
function applyEntryCapabilities(view){
  const caps=view?.access?.runtime==='wsl'||view?.endpointId?.startsWith('wsl:')?view.capabilities||{}:{};
  for(const [id,capability] of [['permissions-button','permissions'],['skills-button','skills']])$(id).hidden=caps[capability]===false;
  const queue=$('send-mode').querySelector('[value=queue]');if(queue)queue.disabled=caps.queue===false;
  if(caps.queue===false&&$('send-mode').value==='queue')$('send-mode').value='send';
  if(caps.skills===false){selectedSkills.clear();invalidSkills.clear();renderSkillPills();}
  if(caps.plan===false){workModes.selection='default';$('work-mode').value='default';}
  $('work-mode').hidden=caps.plan===false;
}
function renderState(view){
  applyEntryCapabilities(view);
  contextUsageControl.update({...view,busy:contextCompacting.has(chatKey(currentId,currentHost))});state=view;if(typeof bindCodexDraft==='function')bindCodexDraft(view);
  if(typeof messageManagement!=='undefined')messageManagement?.reconcile();
  if(typeof document!=='undefined')for(const dialog of document.querySelectorAll('dialog[data-permission-runtime]')){
    if(!view.connected||dialog.dataset.permissionThread!==currentId||dialog.dataset.permissionHost!==currentHost||
       dialog.dataset.permissionRuntime!==(view.access?.runtime||'desktop'))dialog.close();
  }
  if(view.connected)reconnectFailure=null;
  const access=sessionAccess(view.access),limited=!!view.access;
  $('permissions-button').textContent=t({'ask':'请求批准','auto-review':'帮我批准','full-access':'完全访问'}[view.permissionMode]||'权限');
  $('permissions-button').disabled=!view.connected||!access.canChangePermissions;
  if($('model-dialog').open&&modelTarget===chatKey())fastModeControl.sync(view);
  if(visibleChat()){sessionActivity.read(chatKey());renderActivity();}
  $('chat-title').textContent=view.loadingHistory?t('正在读取聊天…'):view.title;
  $('model-name').textContent=view.model||t('模型');
  $('model-effort').textContent=view.effort?'· '+view.effort:'';$('model-effort').hidden=!view.effort;
  $('model-button').title=[view.model||t('模型'),view.effort].filter(Boolean).join(' · ');
  $('status').textContent=!view.connected?(view.loadingHistory?t('读取历史中'):view.activating?t('正在恢复会话…'):access.source==='cli'?t('历史记录'):view.connecting?t('连接桌面中'):t('历史记录')):view.requests.length?t('等待回应'):view.status==='active'?t('运行中'):t('已连接');
  $('status').classList.toggle('offline',!view.connected);
  $('notice').hidden=view.connected;
  $('notice-text').textContent=view.readOnlyReason?t(view.readOnlyReason):access.source==='cli'?(t(reconnectFailure||view.connectionError)||t(access.canReconnect?'可先阅读聊天历史；继续时将连接或恢复原 CLI 会话。':access.reason||'CLI 聊天历史可阅读；当前无法继续此会话。')):limited&&!access.canReconnect?(t(access.reason)||t('已保存的历史可阅读；当前来源未提供远程控制。')):view.desktopRestartRecommended?t('尚未连接原桌面会话。可再次重建连接；若仍失败，可在应用管理中核对任务后重启 Codex。'):view.activating?t('正在自动连接；必要时会在电脑 Codex 中打开此聊天。'):view.connecting?t('正在连接桌面，可先阅读已保存的历史。'):reconnectFailure?t(reconnectFailure):view.rebuildRecommended?t('桌面连接已失效，建议重建连接。聊天历史和草稿会保留。'):t(view.connectionError)||t('暂未连接，可先阅读历史或点击重新连接。');
  $('reconnect').hidden=limited&&!access.canReconnect;
  $('reconnect').textContent=t(access.source==='cli'?'继续会话':view.rebuildRecommended||view.desktopRestartRecommended?'重建连接':'重新连接');
  $('reconnect-manager').hidden=view.connected||!view.desktopRestartRecommended;
  $('history').hidden=view.historyComplete||!view.connected;
  renderChatProgress($('working'),{agentName:'Codex',active:view.status==='active',waiting:!!view.requests.length,connected:!!view.connected});if(!view.connected&&view.status!=='active')$('working').hidden=true;$('stop').hidden=view.status!=='active'||!view.connected||!access.canInterrupt;
  $('send').disabled=view.loadingHistory||view.activating||sending||!attachments.ready()||!access.canSend;
  $('message').disabled=limited&&!access.canSend;
  $('attach-button').disabled=limited&&!access.canSend;
  $('model-button').disabled=limited&&!access.canChangeModel;
  $('skills-button').disabled=limited&&!access.canSelectSkills;
  if(access.canChangeModel||access.canSelectSkills)scheduleCatalog();
  $('send-mode').disabled=limited&&!access.canSend;
  $('send-mode').querySelector('[value=steer]').disabled=view.status!=='active'||!access.canSend;
  if((view.status!=='active'||!access.canSend)&&$('send-mode').value==='steer')$('send-mode').value='send';
  const modesView=limited&&!access.canUseGoal?{...view,goalRuntimeAvailable:false}:view;
  workModes.render(modesView,$('send-mode').value,sending);
  $('work-mode').disabled=limited&&!access.canUseModes;
  if(limited&&!access.canUseModes)$('work-mode').value='default';
  workbench.current.access=access;
  // Parent workspace identity is resolved before opening the native child route.
  chatTimeline?.refreshActions();
  renderApprovals(access.canApprove?view.requests:[]);renderQueue(view.submissions||[]);
  if(typeof composerCommands!=='undefined')composerCommands.refresh();
}
$('send-mode').onchange=()=>{codexDraftTouched=true;workModes.render(state,$('send-mode').value,sending);saveDraft();};
function jsonPretty(value){return typeof value==='string'?value:JSON.stringify(value,null,2);}
function renderApprovals(requests){const stamp=JSON.stringify([BridgeI18n.language(),requests]);if(stamp===approvalStamp)return;approvalStamp=stamp;const container=$('approvals'),previous=new Map([...container.children].map(card=>[card.dataset.request,card])),kept=new Set();let position=0;for(const request of requests){const old=previous.get(String(request.id)),requestStamp=JSON.stringify([BridgeI18n.language(),request]),mount=card=>{card.approvalRequest=request;card.requestStamp=requestStamp;kept.add(card);const before=container.children[position++];if(before!==card)container.insertBefore(card,before||null);};if(old?.requestStamp===requestStamp){mount(old);continue;}if(request.method==='bridge/requestUserInputAsync'&&request.supported){mount(renderAsyncApproval(request,old));continue;}if(request.supported&&request.params?.computerUse){mount(renderComputerApproval(request));continue;}if(request.method==='item/plan/requestImplementation'&&request.supported){mount(renderPlanApproval(request));continue;}const card=el('section','approval');card.dataset.request=String(request.id);const params=request.params||{}, method=request.method;const heading=method.includes('requestUserInput')?t('Codex 需要你的回复'):method.includes('fileChange')?t('允许修改文件？'):method.includes('commandExecution')?t('允许运行此命令？'):method.includes('permissions')?t('允许本轮权限？'):t('待确认请求');card.append(el('h3','',heading));const controls={};if(!request.supported){card.append(el('p','',params.message||t('请在桌面 App 处理此请求')));mount(card);continue;}if(params.reason)card.append(el('p','',params.reason));if(params.command)card.append(el('pre','',jsonPretty(params.command)));if(params.cwd)card.append(el('p','muted',params.cwd));for(const key of ['changes','grantRoot','permissions','networkApprovalContext'])if(params[key])card.append(el('pre','',jsonPretty(params[key])));const form=el('form');if(method.includes('requestUserInput')){for(const question of params.questions||[]){const label=el('label','',question.question||question.header||question.id);if(question.options?.length){const select=el('select');select.append(new Option(t('请选择…'),''));for(const option of question.options)select.append(new Option(option.label+(option.description?' — '+option.description:''),option.label));select.append(new Option(t('自行填写'),'__custom__'));label.append(select);const custom=el('textarea');custom.rows=2;custom.hidden=true;custom.placeholder=t('输入你的回复');select.onchange=()=>custom.hidden=select.value!=='__custom__';label.append(custom);controls[question.id]=()=>select.value==='__custom__'?custom.value:select.value;}else{const input=el('textarea');input.rows=2;input.required=true;label.append(input);controls[question.id]=()=>input.value;}form.append(label);}}else if(method==='mcpServer/elicitation/request'){if(params.message)form.append(el('p','',params.message));const schema=params.requestedSchema||{};for(const [key,field] of Object.entries(schema.properties||{})){const label=el('label','',field.title||key);let input;if(field.enum){input=el('select');input.append(new Option(t('请选择…'),''));for(const [i,value] of field.enum.entries())input.append(new Option(field.enumNames?.[i]||String(value),String(value)));}else{input=el('input');input.type=field.type==='boolean'?'checkbox':['number','integer'].includes(field.type)?'number':'text';if(input.type==='number')input.step=field.type==='integer'?'1':'any';}input.required=(schema.required||[]).includes(key)&&input.type!=='checkbox';if(field.description)label.append(el('p','muted',field.description));label.append(input);form.append(label);controls[key]=()=>input.type==='checkbox'?input.checked:input.value===''?undefined:['number','integer'].includes(field.type)?Number(input.value):input.value;}}const actions=el('div','actions'),error=el('p','error');async function submit(response){const id=currentId,host=currentHost;approvalBusy++;setLanguageBusy();const buttons=card.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);error.textContent='';try{await api(sessionUrl(id,'respond',host),{requestId:request.id,response});submittedRequestIds.add(request.id);bumpRecency(id,host);toast('已发送回应');card.append(el('p','muted',t('已发送，等待桌面确认…')));}catch(e){error.textContent=t(e.message);buttons.forEach(b=>b.disabled=false);}finally{approvalBusy--;setLanguageBusy();}}const isInput=method.includes('requestUserInput'),isMcp=method==='mcpServer/elicitation/request';const yes=el('button','primary',isInput?t('提交回复'):t('本次允许'));yes.type='submit';const available=params.availableDecisions;const canAccept=!available||available.includes('accept');if(isInput||isMcp||canAccept)actions.append(yes);if(!isInput){const no=el('button','secondary',t('拒绝'));no.type='button';no.onclick=()=>submit(isMcp?{action:'decline'}:{decision:'decline'});if(!available||available.includes('decline'))actions.append(no);if(method.includes('commandExecution')||method.includes('fileChange')||isMcp){const cancel=el('button','secondary',t('取消'));cancel.type='button';cancel.onclick=()=>submit(isMcp?{action:'cancel'}:{decision:'cancel'});if(!available||available.includes('cancel'))actions.append(cancel);}}form.onsubmit=event=>{event.preventDefault();if(isInput){const answers={};for(const [key,get] of Object.entries(controls)){const value=get();if(!value.trim()){error.textContent=t('请回答所有问题');return;}answers[key]=[value];}submit({answers});}else if(isMcp){const content={};for(const [key,get] of Object.entries(controls)){const value=get();if(value!==undefined)content[key]=value;}submit({action:'accept',content});}else submit({decision:'accept'});};form.append(actions,error);card.append(form);if(submittedRequestIds.has(request.id)){card.querySelectorAll('button').forEach(button=>button.disabled=true);card.append(el('p','muted',t('已发送，等待桌面确认…')));}mount(card);}for(const card of [...container.children])if(!kept.has(card))card.remove();}
function renderAsyncApproval(request, previous){
  const id=currentId,host=currentHost,card=el('section','approval'),form=el('form'),error=el('p','error');
  card.dataset.request=String(request.id);card.append(el('h3','',t('Codex 需要你的回复')));
  const controls={},fields=new Map([...(previous?.querySelectorAll('[data-question]')||[])].map(node=>[node.dataset.question,node]));
  let busy=submittedRequestIds.has(request.id);
  function setBusy(value){busy=value;form.querySelectorAll('button,input,select,textarea').forEach(node=>node.disabled=value);}
  async function submit(response){
    if(busy)return;
    setBusy(true);error.textContent='';approvalBusy++;setLanguageBusy();
    try{
      const result=await api(sessionUrl(id,'respond',host),{requestId:request.id,response});
      if(id!==currentId||host!==currentHost)return;
      if(response.action==='skip'){
        if(result.status!=='skipped')throw Error('跳过结果尚未确认，请刷新后重试');
        // A late receipt must not replace newer SSE state or another card's result.
        const remaining=[...$('approvals').children].flatMap(node=>{
          const current=node.approvalRequest;if(!current)return [];
          if(current.id!==request.id)return [current];
          const questions=current.params.questions.filter(question=>question.id!==response.questionId);
          return questions.length?[{...current,params:{...current.params,questions}}]:[];
        });
        renderApprovals(remaining);toast('已在远程跳过');
      }else{
        submittedRequestIds.add(request.id);bumpRecency(id,host);toast('已发送回应');
        card.append(el('p','muted',t('已发送，等待桌面确认…')));
      }
    }catch(e){
      if(id===currentId&&host===currentHost){error.textContent=t(e.message);setBusy(false);}
    }finally{approvalBusy--;setLanguageBusy();}
  }
  for(const question of request.params.questions){
    let field=fields.get(question.id);
    if(!field){
      field=el('div');field.dataset.question=question.id;
      const label=el('label','',question.question||question.header||question.id);
      if(question.options?.length){
        const select=el('select');select.append(new Option(t('请选择…'),''));
        for(const option of question.options)select.append(new Option(option.label+(option.description?' — '+option.description:''),option.label));
        select.append(new Option(t('自行填写'),'__custom__'));label.append(select);
        const custom=el('textarea');custom.rows=2;custom.hidden=true;custom.placeholder=t('输入你的回复');
        select.onchange=()=>custom.hidden=select.value!=='__custom__';label.append(custom);
        field.answer=()=>select.value==='__custom__'?custom.value:select.value;
      }else{
        const input=el('textarea');input.rows=2;input.required=true;label.append(input);field.answer=()=>input.value;
      }
      const actions=el('div','actions'),skip=el('button','secondary',t('跳过'));skip.type='button';skip.dataset.action='skip-question';
      actions.append(skip);field.append(label,actions);
    }
    field.querySelector('button[type="submit"]')?.remove();
    const select=field.querySelector('select');
    if(select){select.options[0].textContent=t('请选择…');select.options[select.options.length-1].textContent=t('自行填写');field.querySelector('textarea').placeholder=t('输入你的回复');}
    field.querySelector('[data-action="skip-question"]').textContent=t('跳过');
    field.querySelector('[data-action="skip-question"]').onclick=()=>submit({action:'skip',questionId:question.id});
    controls[question.id]=field.answer;form.append(field);
  }
  const singleQuestion=request.params.questions.length===1;
  const actions=singleQuestion?form.querySelector('[data-question] .actions'):el('div','actions'),send=el('button','primary',t('提交回复'));send.type='submit';actions.append(send);
  form.onsubmit=event=>{
    event.preventDefault();const answers={};
    for(const [key,get] of Object.entries(controls)){
      const value=get();if(!value.trim()){error.textContent=t('请回答所有问题');return;}answers[key]=[value];
    }
    submit({answers});
  };
  if(!singleQuestion)form.append(actions);
  form.append(error);card.append(form);setBusy(busy);
  if(busy)card.append(el('p','muted',t('已发送，等待桌面确认…')));
  return card;
}

function renderComputerApproval(request){
  const id=currentId,host=currentHost,card=el('section','approval'),computer=request.params.computerUse;
  card.dataset.request=String(request.id);card.append(el('h3','',t('允许使用电脑应用？')),el('p','',computer.app));
  if(request.params.message)card.append(el('p','',request.params.message));
  const actions=el('div','actions'),error=el('p','error');card.append(actions,error);
  let busy=submittedRequestIds.has(request.id);
  async function submit(response){
    if(busy)return;busy=true;approvalBusy++;setLanguageBusy();actions.querySelectorAll('button').forEach(b=>b.disabled=true);error.textContent='';
    try{await api(sessionUrl(id,'respond',host),{requestId:request.id,response});bumpRecency(id,host);if(id===currentId&&host===currentHost)submittedRequestIds.add(request.id);card.append(el('p','muted',t('已发送，等待桌面确认…')));}
    catch(e){error.textContent=t(e.message);busy=false;actions.querySelectorAll('button').forEach(b=>b.disabled=false);}
    finally{approvalBusy--;setLanguageBusy();}
  }
  function choice(label,response,primary=false){const button=el('button',primary?'primary':'secondary',t(label));button.type='button';button.disabled=busy;button.onclick=()=>submit(response);actions.append(button);}
  if(computer.persistModes.includes('always'))choice('始终允许',{action:'accept',persist:'always'});
  choice('拒绝',{action:'decline'});
  if(computer.persistModes.includes('session'))choice('允许此对话',{action:'accept',persist:'session'},true);
  else choice('本次允许',{action:'accept'},true);
  return card;
}
function renderPlanApproval(request){
  const id=currentId,host=currentHost,key=chatKey(id,host),card=el('section','approval plan-approval');
  card.dataset.request=String(request.id);card.append(el('h3','',t('计划已就绪')));
  const details=el('details'),summary=el('summary','',t('查看计划')),content=el('div','plan-content');
  renderMarkdown(content,request.params.planContent||'',[],()=>null);details.append(summary,content);card.append(details);
  const label=el('label','',t('修改意见（可选）')),input=el('textarea');input.rows=2;input.maxLength=20000;label.append(input);card.append(label);
  const actions=el('div','actions'),yes=el('button','primary',t('按照当前规划结果执行')),revise=el('button','secondary',t('按照修改意见继续规划')),error=el('p','error');
  yes.type=revise.type='button';actions.append(yes,revise);card.append(actions,error);
  let busy=submittedRequestIds.has(request.id);
  function updateActions(){
    const hasFeedback=!!input.value.trim();
    yes.disabled=busy||hasFeedback;revise.disabled=busy||!hasFeedback;
    yes.className=hasFeedback?'secondary':'primary';revise.className=hasFeedback?'primary':'secondary';
  }
  input.oninput=updateActions;updateActions();
  async function submit(action){
    if(busy)return;
    if(action==='implement'&&input.value.trim()){updateActions();return;}
    if(action==='revise'&&!input.value.trim()){error.textContent=t('请填写修改意见');return;}
    const response=action==='implement'?{action}:{action,text:input.value};
    busy=true;approvalBusy++;setLanguageBusy();yes.disabled=revise.disabled=true;error.textContent='';
    try{
      const result=await api(sessionUrl(id,'respond',host),{requestId:request.id,response});
      if(currentId!==id||currentHost!==host)return;
      bumpRecency(id,host);submittedRequestIds.add(request.id);
      if(result.status==='unknown'){error.textContent=t('发送结果尚未确认，请查看会话后再操作。');return;}
      workModes.selection=action==='implement'?'default':'plan';sessionStorage.setItem('work-mode:'+key,workModes.selection);workModes.render();
      card.append(el('p','muted',t('已发送，等待桌面确认…')));
    }catch(e){if(currentId===id&&currentHost===host){error.textContent=t(e.message);busy=false;updateActions();}}
    finally{approvalBusy--;setLanguageBusy();}
  }
  yes.onclick=()=>submit('implement');revise.onclick=()=>submit('revise');return card;
}
async function ignoreSubmission(value){
  const scope=currentId===value.threadId&&currentHost===value.host?codexDraftScope:null;
  const result=await api('/api/accounts/ignore-submission',value);
  const key='pending:'+chatKey(value.threadId,value.host);
  try{if(JSON.parse(sessionStorage.getItem(key))?.id===value.submissionId)sessionStorage.removeItem(key);}catch{}
  if(composerDrafts.temporaryPending.get(key)?.id===value.submissionId)composerDrafts.temporaryPending.delete(key);
  if(scope)await composerDrafts.settleSend(scope,value.submissionId,null,'ignore');
  if(currentId===value.threadId&&currentHost===value.host&&state){state.submissions=(state.submissions||[]).filter(row=>row.id!==value.submissionId);renderQueue(state.submissions);}
  return result;
}
function renderQueue(rows){
  $('queued').replaceChildren();
  const pending=codexDraftScope&&composerDrafts?.pendingRecords(codexDraftScope,{temporary:sessionStorage,temporaryKey:'pending:'+chatKey()})||[];
  if(rows.length||pending.length){const manage=el('button','plain',t('消息管理'));manage.type='button';manage.onclick=openMessageManagement;$('queued').append(manage);}
  const target=currentId,host=currentHost;
  for(const row of rows){
    const card=el('div','queue-card'),reply=row.text.startsWith('<send_user_message_question_reply>');
    card.append(el('strong','',row.status==='queued'?t('等待当前任务完成'):t('发送结果待确认')),el('p','',reply?t('问题回复'):row.text));
    if(row.attachments?.length)card.append(el('small','muted',t('附件：')+row.attachments.join('、')));
    if(row.status==='queued'){
      const cancel=el('button','plain',t('撤回待发送消息'));
      cancel.onclick=async()=>{try{await api(sessionUrl(target,'queue',host),{id:row.id});toast('已撤回');}catch(e){toast(e.message);}};card.append(cancel);
    }else{
      card.append(el('p','muted',t('请检查聊天记录。网关不会自动重发这条消息。')));
      const ignore=el('button','plain',t('忽略'));
      ignore.onclick=async()=>{
        if(!window.confirm(t('忽略只会移除未确认提示，不会撤回或重发消息。请先检查聊天记录。')))return;
        ignore.disabled=true;
        try{accountsPanel?.accept(await ignoreSubmission({threadId:target,submissionId:row.id,host}));toast('已忽略未确认记录');}
        catch(e){ignore.disabled=false;toast(e.message);}
      };if(!window.BridgeSharedRelay)card.append(ignore);
    }
    $('queued').append(card);
  }
}
function uuid(){const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
$('composer').onsubmit=async event=>{
  event.preventDefault();
  if(sending||!currentId||!currentAccess().canSend)return;
  const rawText=$('message').value,submittedQuotes=JSON.stringify(codexQuotes?.quotes||[]),text=codexQuotes?codexQuotes.serialize(rawText):rawText;
  if(text.length>100000){$('send-error').textContent=t('消息含引用超过 100000 字符，请缩短后发送');return;}
  if(!attachments.ready()){toast('请等待附件上传完成');return;}
  const attachmentIds=attachments.ids(),references=attachments.references(),submittedAttachmentIds=attachments.rows.map(row=>row.id);if(!text.trim()&&!submittedAttachmentIds.length)return;
  const mode=$('send-mode').value,workMode=workModes.value(mode);
  if(workMode==='goal'&&submittedAttachmentIds.length){$('send-error').textContent=t('目标模式暂不支持附件');return;}
  if(invalidSkills.size){$('send-error').textContent=t('已选 Skill 已失效，请刷新列表后重新选择');return;}
  const skills=[...selectedSkills].sort(),scope=codexDraftScope;
  const body={text,mode,skills,workMode,attachments:attachmentIds,uiLocale:BridgeI18n.locale()};if(references.length)body.references=references;
  const revision=saveDraft()?.revision,submittedProvisional=codexProvisionalDrafts.get(chatKey());
  const submittedKey=chatKey(),legacyKey='pending:'+submittedKey;let legacy=composerDrafts.temporaryPending.get(legacyKey);try{legacy=JSON.parse(sessionStorage.getItem(legacyKey))||legacy;}catch{}
  const matchingLegacy=legacy?.id&&legacy.gateway===draftGateway&&legacy.target===submittedKey&&(!legacy.account||legacy.account===scope?.account);
  const sameLegacy=matchingLegacy&&['text','mode','workMode','skills','attachments','references','uiLocale'].every(field=>JSON.stringify(legacy[field])===JSON.stringify(body[field]));
  const attempt=uuid(),target=currentId,targetHost=currentHost;
  let pending,usedDurable=false;
  sending=true;
  try{
    if(matchingLegacy&&legacy.text===body.text&&!sameLegacy){$('send-error').textContent=t('上次发送结果尚未确认，请检查聊天记录或先处理未确认记录。');renderQueue(state?.submissions||[]);messageManagement?.reconcile();return;}
    if(scope&&composerDrafts){pending=await composerDrafts.beginSend(scope,body,sameLegacy?legacy.id:uuid(),attempt,!!sameLegacy);usedDurable=!!pending;if(!pending&&composerDrafts.pendingSend(scope)){pending={blocked:true};}if(usedDurable&&sameLegacy&&!pending.blocked){composerDrafts.temporaryPending.delete(legacyKey);try{sessionStorage.removeItem(legacyKey);}catch{}}}
    if(pending?.blocked){$('send-error').textContent=t('上次发送结果尚未确认，请检查聊天记录或先处理未确认记录。');renderQueue(state?.submissions||[]);messageManagement?.reconcile();return;}
    if(!pending){pending=sameLegacy?legacy:{id:uuid(),gateway:draftGateway,target:submittedKey,account:scope?.account||'',...body};
      pending={...pending,attempts:[...(sameLegacy&&Array.isArray(legacy.attempts)?legacy.attempts:[]),attempt],untracked:!!(sameLegacy&&(legacy.untracked||!Array.isArray(legacy.attempts)))};composerDrafts.temporaryPending.set(legacyKey,pending);try{sessionStorage.setItem(legacyKey,JSON.stringify(pending));}catch{} }
    attachments.setLocked(true);workModes.render(state,mode,true);$('send').disabled=true;$('send-error').textContent='';
    const result=await api(sessionUrl(target,'send',targetHost),{...body,id:pending.id});
    if(result.status==='ignored')throw Error(t('这条未确认记录已忽略，请刷新后重新编辑消息。'));
    if(result.status==='unknown')throw Error(t('消息可能已送达，请检查聊天记录。再次点击不会重复提交。'));
    bumpRecency(target,targetHost);
    const sameRevision=usedDurable?composerDrafts.load(scope)?.revision===revision:$('message').value===rawText&&JSON.stringify(codexQuotes?.quotes||[])===submittedQuotes;
    let confirmed=usedDurable?await composerDrafts.settleSend(scope,pending.id,attempt,'confirm',revision):false;
    if(!usedDurable){let saved=composerDrafts.temporaryPending.get(legacyKey);try{saved=JSON.parse(sessionStorage.getItem(legacyKey))||saved;}catch{}confirmed=saved?.id===pending.id&&saved.attempts?.includes(attempt);if(confirmed){composerDrafts.temporaryPending.delete(legacyKey);try{sessionStorage.removeItem(legacyKey);}catch{}}}
    if(confirmed&&submittedProvisional&&JSON.stringify(codexProvisionalDrafts.get(chatKey(target,targetHost)))===JSON.stringify(submittedProvisional)){codexProvisionalDrafts.delete(chatKey(target,targetHost));try{sessionStorage.removeItem('draft:'+chatKey(target,targetHost));sessionStorage.removeItem('skills:'+chatKey(target,targetHost));}catch{}}
    if(confirmed)attachments.clear(chatKey(target,targetHost),submittedAttachmentIds);
    if(confirmed&&sameRevision&&$('message').value===rawText&&JSON.stringify(codexQuotes?.quotes||[])===submittedQuotes){
      if(!usedDurable)try{sessionStorage.removeItem('draft:'+chatKey(target,targetHost));}catch{}
      workModes.submitted(chatKey(target,targetHost),workMode);
      if(currentId===target&&currentHost===targetHost&&composerDrafts.key(codexDraftScope)===composerDrafts.key(scope)){
        restoringComposer=true;$('message').value='';codexQuotes?.reset();selectedSkills.clear();renderSkillPills();restoringComposer=false;
        window.BridgePresentation?.resizeMessage();
      }
    }
    toast(workMode==='goal'?(result.activation?.status==='queued'?t('目标已创建，启动已排队'):result.activation?t('目标已创建，正在启动'):t('已提交目标请求，等待桌面确认')):result.status==='queued'?t('已加入待发送队列'):t('已发送到会话'));
  }catch(error){
    // Only an explicit pre-submission rejection permits changing this request.
    // Unknown delivery and network failures retain their original pending ID.
    if(error.status===400&&error.code==='send_not_submitted'){
      if(usedDurable)await composerDrafts.settleSend(scope,pending.id,attempt,'reject');
      else {let saved=composerDrafts.temporaryPending.get(legacyKey);try{saved=JSON.parse(sessionStorage.getItem(legacyKey))||saved;}catch{}if(saved?.id===pending.id&&saved.attempts?.includes(attempt)){saved.attempts=saved.attempts.filter(value=>value!==attempt);if(!saved.untracked&&!saved.attempts.length){composerDrafts.temporaryPending.delete(legacyKey);try{sessionStorage.removeItem(legacyKey);}catch{}}else{composerDrafts.temporaryPending.set(legacyKey,saved);try{sessionStorage.setItem(legacyKey,JSON.stringify(saved));}catch{}}}}
    }
    if(currentId===target&&currentHost===targetHost){$('send-error').textContent=t(error.message);window.BridgePresentation?.showError();}
    if(error.code!=='send_not_submitted'&&currentId===target&&currentHost===targetHost)await messageManagement?.reconcile();
  }
  finally{sending=false;attachments.setLocked(false);workModes.render(state,$('send-mode').value,false);$('send').disabled=!state||state.loadingHistory||state.activating||!attachments.ready()||!currentAccess().canSend;}
};
$('message').onkeydown=event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();$('composer').requestSubmit();}};
function chatCommandActions(chat,provider,button){
  if(!chat?.id)return [];
  const context=sessionMenuContext(chat,provider);
  return ComposerCommands.session(context,view=>getSessionMenu().show(context,{element:button},view));
}
const composerCommands=new ComposerCommands({input:$('message'),
  scope:()=>currentId&&desktopSessions.provider==='codex'&&!$('chat').hidden?JSON.stringify([listIdentity,currentHost,currentId,codexActiveAccountId]):null,
  actions:()=>[...ComposerCommands.controls({model:$('model-button'),skills:$('skills-button'),permissions:$('permissions-button'),context:contextUsageControl,workMode:$('work-mode')}),
    ...chatCommandActions({...state,id:currentId,host:currentHost},'codex',$('message')),
    {id:'new',label:'新聊天',enabled:!$('new-chat').disabled,run:()=>$('new-chat').click()},
    {id:'side',label:'侧边聊天',enabled:!!state&&currentAccess().canUseSideChat,run:()=>workbench.sideChat()}],onError:toast});
desktopSessions.commandActions=()=>[...chatCommandActions({...desktopSessions.state,id:desktopSessions.sid},desktopSessions.provider,desktopSessions.input),
  {id:'new',label:'新聊天',enabled:!$('new-chat').disabled,run:()=>$('new-chat').click()}];

$('stop').onclick=async()=>{if(!currentAccess().canInterrupt)return;try{await api(sessionUrl(currentId,'stop'),{});toast('已请求停止');}catch(e){toast(e.message);}};
$('history').onclick=async()=>{$('history').disabled=true;try{await api('/api/sessions/'+currentId+'/history',{});}catch(e){toast(e.message);}finally{$('history').disabled=false;}};
$('reconnect').onclick=async()=>{
  const access=currentAccess();if(!access.canReconnect||$('reconnect').disabled)return;
  const id=currentId,host=currentHost,rebuild=state?.rebuildRecommended||state?.desktopRestartRecommended;
  $('reconnect').disabled=true;reconnectFailure=null;
  try{await api(sessionUrl(id,'reconnect',host),{activate:true,...(rebuild?{rebuild:true}:{})});}
  catch(e){if(id===currentId&&host===currentHost){reconnectFailure=e.message;$('notice').hidden=false;$('notice-text').textContent=t(e.message);}toast(e.message);}
  finally{$('reconnect').disabled=false;}
};
$('reconnect-manager').onclick=()=>clientNavigation.openManager();
document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>$(button.dataset.close).close());
async function loadCatalogKind(kind,refresh=false){
  if(kind!=='models')throw Error(t('未知目录类型'));
  if(refresh)modelCatalogGeneration++;
  const target=currentId,targetHost=currentHost,provider=state?.provider,generation=modelCatalogGeneration;
  const accountIdentity=state?.modelCatalogIdentity||'',gatewayIdentity=listIdentity;
  const identity=JSON.stringify([gatewayIdentity,target,targetHost,provider,accountIdentity]);
  if(!refresh&&catalogTasks[kind]?.identity===identity)return catalogTasks[kind];
  const request=api('/api/sessions/'+target+'/catalog?kind='+kind+(refresh?'&refresh=true':'')).then(value=>{
    if(currentId!==target||currentHost!==targetHost||state?.provider!==provider||(state?.modelCatalogIdentity||'')!==accountIdentity||listIdentity!==gatewayIdentity||generation!==modelCatalogGeneration)throw Error(t('聊天已切换'));
    if(accountIdentity&&value?.cache?.identity!==accountIdentity)throw Error(t('账号已切换，请重新打开模型设置'));
    const payload=value?.[kind]??value?.data??value;
    if(!Array.isArray(payload)){
      const actual=payload===null?'null':typeof payload;
      throw Error(t('模型目录返回无效')+' ('+kind+': '+actual+')');
    }
    catalogData[kind]=payload.map(row=>{
      if(typeof row==='string')return {id:row,name:row};
      const id=row?.id||row?.model;
      return typeof id==='string'&&id?{...row,id,name:row.name||row.displayName||id}:null;
    }).filter(Boolean);
    const result={...value,kind,[kind]:catalogData[kind],_scope:identity};
    catalogResults[kind]=result;
    if(accountIdentity&&value?.cache?.identity===accountIdentity){Object.assign(result,window.BridgeModelCatalogCache.write(modelCatalogs,[gatewayIdentity,targetHost,'codex'],result));catalogData[kind]=result[kind];}
    return result;
  }).finally(()=>{if(catalogTasks[kind]===request)catalogTasks[kind]=null;});
  request.identity=identity;catalogTasks[kind]=request;
  return request;
}
function resetSkillCatalog(){
  clearTimeout(skillSearchTimer);$('skill-search').value='';$('skill-list').scrollTop=0;skillRequest++;
  catalogData.skills=[];catalogResults.skills=null;catalogTasks.skills=null;
  skillIndex=new Map();skillTotal=0;skillPages=[];skillQuery={q:'',offset:0,limit:200};invalidSkills=new Set();
}
async function loadSkills({refresh=false,append=false}={}){
  const target=currentId,targetHost=currentHost;
  const query={...skillQuery};
  if(!refresh&&!append&&catalogResults.skills)return catalogResults.skills;
  if(!refresh&&!append&&catalogTasks.skills)return catalogTasks.skills;
  if(refresh){
    skillRequest++;catalogTasks.skills=null;catalogResults.skills=null;skillPages=[];skillQuery.offset=0;invalidSkills=new Set();
    $('skill-list').scrollTop=0;
  }
  query.offset=refresh?0:skillQuery.offset;
  const generation=skillRequest;
  const requestKey=chatKey(target,targetHost)+'|'+query.q+'|'+query.offset;
  if(!append&&catalogTasks.skills?.key===requestKey&&catalogTasks.skills?.generation===generation)return catalogTasks.skills;
  const ids=[...selectedSkills].slice(0,8);
  const path='/api/sessions/'+target+'/catalog?kind=skills&q='+encodeURIComponent(query.q)+'&limit='+query.limit+'&offset='+query.offset+(refresh?'&refresh=true':'')+ids.map(id=>'&id='+encodeURIComponent(id)).join('');
  const request=api(path).then(value=>{
    if(currentId!==target||currentHost!==targetHost||generation!==skillRequest)return value;
    const rows=Array.isArray(value?.skills)?value.skills:[];
    if(!Array.isArray(value?.skills))throw Error(t('模型目录返回无效')+' (skills: '+(value?.skills===null?'null':typeof value?.skills)+')');
    if(refresh||!append)skillPages=[];
    const existing=refresh||!append?new Set():new Set(skillPages.map(row=>row.id));
    for(const row of rows){if(!existing.has(row.id)){skillPages.push(row);existing.add(row.id);}}
    catalogData.skills=skillPages;
    for(const row of [...rows,...(Array.isArray(value.selectedSkills)?value.selectedSkills:[])])if(row?.id)skillIndex.set(row.id,row);
    skillTotal=Math.max(0,Number(value.total)||0);
    skillQuery.offset=skillPages.length;
    catalogResults.skills=value;
    const valid=new Set((Array.isArray(value.selectedSkills)?value.selectedSkills:[]).map(row=>row.id));
    const checked=new Set(ids);
    invalidSkills=new Set([...selectedSkills].filter(id=>checked.has(id)?!valid.has(id):invalidSkills.has(id)));
    renderSkillPills();renderSkills();renderSkillCache(value);$('skills-error').textContent=[...(value.errors||[]),value.refreshError].filter(Boolean).join('\n');
    return value;
  }).finally(()=>{
    if(catalogTasks.skills===request&&generation===skillRequest)catalogTasks.skills=null;
  });
  request.key=requestKey;request.generation=generation;catalogTasks.skills=request;
  return request;
}
function renderSkillCache(value){
  const node=$('skills-cache');if(!node)return;
  const state=value?.cache?.state;
  node.textContent=state==='stale'?t('正在显示缓存 Skill，后台会自动刷新。'):state==='legacy'?t('正在显示旧版缓存 Skill，后台会自动刷新。'):'';
}
function scheduleCatalog(){
  const access=currentAccess();if(state?.loadingHistory||catalogStarted||!currentId||!(access.canChangeModel||access.canSelectSkills))return;
  catalogStarted=true;const timeline=chatTimeline,generation=chatOpenGeneration,catalogGeneration=modelCatalogGeneration;
  // Run after the current timeline render, and discard work for a replaced chat/account.
  setTimeout(()=>{if(generation===chatOpenGeneration&&catalogGeneration===modelCatalogGeneration&&timeline===chatTimeline&&!timeline?.abort.signal.aborted)startCatalog();},0);
}
function startCatalog(){
  loadCatalogKind('models').catch(()=>{});
  resetSkillCatalog();loadSkills().catch(()=>{});
}
function resetModelCatalog(){
  modelCatalogGeneration++;catalogData.models=null;catalogResults.models=null;catalogTasks.models=null;
  modelRequest++;modelTarget=null;fastModeControl.reset();$('model-dialog').close();
}
function updateModelScrollHint(){
  const list=$('model-options');
  $('model-list-field').classList.toggle('has-more',list.scrollHeight-list.clientHeight-list.scrollTop>1);
}
$('model-options').addEventListener('scroll',updateModelScrollHint,{passive:true});
if(typeof ResizeObserver!=='undefined')new ResizeObserver(updateModelScrollHint).observe($('model-options'));
function renderModelChoices(){
  const list=$('model-options');list.replaceChildren();
  const models=Array.isArray(catalogData?.models)?catalogData.models:[],byId=new Map(models.map(model=>[model.id,model]));
  for(const option of $('model-select').children){
    if(!option.value)continue;
    const row=el('label','model-choice'),input=el('input'),name=el('span','model-choice-name'),check=el('span','model-choice-check','✓');
    input.type='radio';input.name='model-choice';input.value=option.value;input.checked=option.value===$('model-select').value;input.disabled=modelSaving;
    const baseId=option.value.split('/').at(-1),base=byId.get(baseId),routed=option.value.includes('/')&&!!base;
    name.textContent=option.textContent.includes('\ufffd')?(base?.name?.includes('\ufffd')?baseId:base?.name||baseId):option.textContent;
    row.title=option.value==='__custom__'?name.textContent:option.value;
    check.setAttribute('aria-hidden','true');
    input.onchange=()=>{if(!input.checked)return;$('model-select').value=input.value;renderEfforts();};
    row.append(input,name);
    if(routed)row.append(el('small','model-choice-badge',t('聚合')));
    row.append(check);list.append(row);
  }
  const selected=list.querySelector('input:checked')?.closest('label');
  if(selected)list.scrollTop=selected.offsetTop-(list.clientHeight-selected.clientHeight)/2;
  updateModelScrollHint();
}
function renderEffortChoices(){
  const list=$('effort-options');list.replaceChildren();
  const names={none:'无',minimal:'最低',low:'低',medium:'中',high:'高',xhigh:'极高',max:'Max',ultra:'Ultra'};
  const count=$('effort-select').children.length;
  list.style.setProperty('--effort-columns',count<=4?Math.max(1,count):count<=6?3:4);
  for(const option of $('effort-select').children){
    const row=el('label','effort-choice'),input=el('input'),name=el('span','',t(names[option.value]||option.value));
    input.type='radio';input.name='effort-choice';input.value=option.value;input.checked=option.value===$('effort-select').value;input.disabled=modelSaving||$('effort-select').disabled;
    input.onchange=()=>{if(input.checked)$('effort-select').value=input.value;};
    row.append(input,name);list.append(row);
  }
}
function renderEfforts({effort=state?.effort,keepFast=false}={}){
  const id=$('model-select').value,models=Array.isArray(catalogData?.models)?catalogData.models:[];
  const model=models.find(m=>m.id===(id==='__custom__'?$('custom-model').value.trim():id));
  for(const input of $('model-options').querySelectorAll('input'))input.checked=input.value===id;
  $('custom-model-label').hidden=id!=='__custom__';$('custom-model').required=id==='__custom__';
  const options=model?.efforts?.length?model.efforts:catalogResults.models?.modelSource==='api'?['none','minimal','low','medium','high','xhigh','max','ultra']:[];
  $('effort-select').replaceChildren(...options.map(value=>new Option(value,value)));
  $('effort-select').value=options.includes(effort)?effort:options.includes(model?.defaultEffort)?model.defaultEffort:options[0]||'';
  $('effort-select').disabled=!options.length;
  renderEffortChoices();
  const effortError=t('未能读取此模型的推理能力，请重新打开模型设置');
  if($('model-error').textContent===effortError)$('model-error').textContent='';
  if(!options.length&&!$('model-error').textContent)$('model-error').textContent=effortError;
  if(!keepFast)fastModeControl.select(id);$('model-save').disabled=!state?.connected||!id||!options.length;
}
async function openModelDialog(forceRefresh=false){
  if(modelSaving)return;
  $('model-refresh').disabled=true;
  const request=++modelRequest,target=chatKey();modelTarget=target;fastModeControl.reset();
  $('model-form').querySelectorAll('input,select,button').forEach(n=>n.disabled=false);
  const identity=state?.modelCatalogIdentity||'',scope=JSON.stringify([listIdentity,currentId,currentHost,state?.provider,identity]),cached=window.BridgeModelCatalogCache.read(modelCatalogs,[listIdentity,currentHost,'codex'],identity)||(catalogResults.models?._scope===scope?catalogResults.models:null);
  const showCatalog=catalog=>{
    catalogData.models=catalog.models;catalogResults.models=catalog;
    $('model-select').replaceChildren(...(catalog.modelSource==='api'?[new Option(t('请选择上游模型'),'')]:[]),...(Array.isArray(catalog.models)?catalog.models:[]).map(m=>new Option(m.name,m.id)),new Option(t('自定义模型…'),'__custom__'));
    const current=state?.model||catalog.currentModel,models=Array.isArray(catalog.models)?catalog.models:[],known=models.some(m=>m.id===current);$('model-select').value=known?current:catalog.modelSource==='api'&&models.length?'':'__custom__';$('custom-model').value=current||'';
    $('model-error').textContent=catalog.modelError?t(catalog.modelError):catalog.modelSource==='api'&&!known&&models.length?t('当前聊天模型不在上游列表中，请选择可用模型后应用。'):'';
    const updated=catalog.cache?.updatedAt;$('model-updated').textContent=Number.isFinite(updated)&&updated>0?t('更新于：')+new Date(updated*1000).toLocaleString(BridgeI18n.locale()):'';
    renderModelChoices();fastModeControl.open(catalog,state,$('model-select').value);renderEfforts();
  };
  $('model-error').textContent='';if(cached?.models?.length)showCatalog(cached);else{$('model-updated').textContent='';$('model-select').replaceChildren(new Option(t('读取模型列表…'),''));$('model-options').textContent=t('读取模型列表…');$('effort-options').replaceChildren();$('model-save').disabled=true;}if(!$('model-dialog').open)$('model-dialog').showModal();
  try{
    const catalog=await loadCatalogKind('models',forceRefresh);
    if(request!==modelRequest||target!==chatKey())return;
    showCatalog(catalog);
  }catch(e){if(request===modelRequest&&target===chatKey()){
    if(!catalogResults.models?.models?.length){$('model-select').replaceChildren();$('model-options').textContent=t('模型目录读取失败，请刷新后重试');$('custom-model-label').hidden=true;$('model-save').disabled=true;}
    $('model-error').textContent=t(e.message);
  }}
  finally{if(request===modelRequest)$('model-refresh').disabled=false;}
}
$('model-button').onclick=()=>openModelDialog();
$('model-refresh').onclick=()=>openModelDialog(true);
$('model-select').onchange=renderEfforts;
$('custom-model').oninput=renderEfforts;
$('model-form').onsubmit=async event=>{
  event.preventDefault();if(modelSaving||modelTarget!==chatKey()||$('model-save').disabled)return;
  const request=modelRequest,target=currentId,host=currentHost;
  const model=$('model-select').value==='__custom__'?$('custom-model').value.trim():$('model-select').value;
  const body={model,effort:$('effort-select').value,...fastModeControl.payload()};
  modelSaving=true;fastModeControl.setBusy(true);$('model-form').querySelectorAll('input,select,button').forEach(n=>n.disabled=true);$('model-save').disabled=true;$('model-error').textContent='';
  try{
    const result=await api(sessionUrl(target,'settings',host),body);
    if(request===modelRequest&&chatKey(target,host)===chatKey()){$('model-dialog').close();toast(result.confirmed?t('桌面模型设置已同步'):t('桌面已接受设置，等待同步'));}
  }catch(e){if(request===modelRequest&&chatKey(target,host)===chatKey())$('model-error').textContent=t(e.message);}
  finally{modelSaving=false;if(request===modelRequest){$('model-form').querySelectorAll('input,select,button').forEach(n=>n.disabled=false);fastModeControl.setBusy(false);renderEfforts({effort:body.effort,keepFast:true});}}
};
function renderSkillPills(){
  $('skill-count').textContent=selectedSkills.size?'('+selectedSkills.size+')':'';
  $('skill-pills').replaceChildren();
  for(const id of selectedSkills){
    const skill=skillIndex.get(id),invalid=invalidSkills.has(id);
    const pill=el('button','skill-pill'+(invalid?' invalid':''),(skill?.displayName||(invalid?t('已失效 Skill'):t('已选 Skill')))+' ×');
    pill.type='button';pill.onclick=()=>{codexDraftTouched=true;selectedSkills.delete(id);invalidSkills.delete(id);renderSkillPills();renderSkills();};
    $('skill-pills').append(pill);
  }
  if(codexDraftScope||codexDraftTouched&&draftGateway)saveDraft();else if(currentId&&!draftGateway)sessionStorage.setItem('skills:'+chatKey(),JSON.stringify([...selectedSkills]));
}
function renderSkills(){
  const list=$('skill-list'),rows=Array.isArray(catalogData?.skills)?catalogData.skills:[];
  const search=$('skill-search').value.toLocaleLowerCase();
  const visible=search===skillQuery.q.toLocaleLowerCase()?rows:rows.filter(skill=>[skill.name,skill.displayName,skill.description].join(' ').toLocaleLowerCase().includes(search));
  list.replaceChildren();
  for(const skill of visible){
    const label=el('label','skill-option');
    const input=el('input');input.type='checkbox';input.value=skill.id;input.checked=selectedSkills.has(skill.id);
    input.onchange=()=>{
      if(input.checked&&selectedSkills.size>=8){input.checked=false;toast('最多选择 8 个 Skill');return;}
      codexDraftTouched=true;if(input.checked){selectedSkills.add(skill.id);invalidSkills.delete(skill.id);}else selectedSkills.delete(skill.id);
      renderSkillPills();
    };
    const content=el('span');content.append(el('strong','',skill.displayName),el('small','',skill.description));
    label.append(input,content);list.append(label);
  }
  if(!visible.length)list.append(el('p','muted',t('没有匹配的 Skill')));
}
async function openSkills(refresh=false){
  if(!currentId)return;$('skills-error').textContent='';if(!$('skills-dialog').open)$('skills-dialog').showModal();
  if(catalogData.skills?.length)renderSkills();
  else $('skill-list').textContent=t('正在读取已安装 Skill…');
  try{await loadSkills({refresh});renderSkills();renderSkillPills();}
  catch(e){
    if(catalogData.skills?.length)renderSkills();
    else $('skill-list').textContent=t('Skill 加载失败，请点击刷新重试');
    $('skills-error').textContent=t(e.message);renderSkillPills();
  }
}
function skillScroll(){
  const list=$('skill-list');
  if(skillTotal>skillPages.length&&list.scrollHeight-list.clientHeight-list.scrollTop<240&&!catalogTasks.skills)loadSkills({append:true}).catch(e=>toast(e.message));
}
$('skills-button').onclick=()=>openSkills();$('skills-refresh').onclick=()=>openSkills(true);
$('skill-list').onscroll=skillScroll;
$('skill-search').oninput=()=>{clearTimeout(skillSearchTimer);skillSearchTimer=setTimeout(()=>{skillRequest++;skillQuery.q=$('skill-search').value;skillQuery.offset=0;skillPages=[];catalogData.skills=[];catalogResults.skills=null;catalogTasks.skills=null;$('skill-list').scrollTop=0;renderSkills();loadSkills().catch(e=>{$('skills-error').textContent=t(e.message);});},200);};
window.addEventListener('pagehide',saveDraft);
function startFromLink(token){return start(token).catch(error=>{if(error.startupCancelled)return;if(window.BridgeBootstrap?.failed())return;showLogin();$('login-error').textContent=t('无法连接网关，请检查网络后重新加载。');});}
startFromLink(initialPairingToken);initialPairingToken=null;
window.BridgeOpenChatLink=hash=>{
  if($('app').hidden||!csrf||typeof hash!=='string'||!clientNavigation.chatTarget(hash))return false;
  clientNavigation.openChatLink(hash,openChat).catch(error=>toast(error.message));return true;
};
window.addEventListener('hashchange',()=>{const token=takePairingToken();if(token!==null)startFromLink(token);else if(!$('app').hidden&&(!browserNavigation.enabled()||history.state?.bridgeNavigation!==1))clientNavigation.openChatLink(location.hash,openChat).catch(error=>toast(error.message));});
function setLanguageBusy(){$('phone-language').disabled=!!(approvalBusy||creatingChat);$('appearance-language').disabled=$('phone-language').disabled;}
$('phone-language').value=BridgeI18n.language();
$('phone-language').onchange=()=>{
  if(approvalBusy||creatingChat)return;
  const replies=[...$('approvals').querySelectorAll('input,textarea,select')].map(node=>({value:node.value,checked:node.checked}));
  BridgeI18n.setLanguage($('phone-language').value);BridgeI18n.apply();if(typeof accountsPanel!=="undefined")accountsPanel?.render();renderSourceFilter();renderList();
  if(!$('new-chat-title').dataset.edited)$('new-chat-title').value=t('新聊天');
  $('new-chat-help').textContent=t($('new-chat-type').value==='cli'?'选择已有 CLI 项目目录，在此创建聊天。':'选择已有项目，或不选择项目创建独立聊天。电脑会打开这条聊天。');
  $('new-chat-submit').textContent=t($('new-chat-type').value==='desktop'?'创建并打开':'创建聊天');
  renderLoginStatus();
  for(const id of ['login-error','send-error','model-error','skills-error','new-chat-error','notify-error','pushplus-error','toast'])$(id).textContent=t($(id).textContent);
  for(const option of $('new-chat-project').options){const project=newChatProjects.find(p=>p.key===option.value);option.textContent=project?project.name:t(option.value==='__directory__'?'选择其他目录…':'不选择项目');}
  for(const option of $('new-chat-entry').options){const environment=newChatEnvironments.find(row=>row.key===option.value);if(environment?.host==='local')option.textContent=(environment.name||environment.hostLabel||'此电脑').replace('此电脑',t('此电脑'));}
  if($('model-select').querySelector('[value=__custom__]'))$('model-select').querySelector('[value=__custom__]').textContent=t('自定义模型…');
  if($('model-dialog').open){renderModelChoices();renderEffortChoices();}
  if(state){approvalStamp='';renderState(state);[...$('approvals').querySelectorAll('input,textarea,select')].forEach((node,i)=>{if(replies[i]){node.value=replies[i].value;node.checked=replies[i].checked;if(node.tagName==='SELECT')node.dispatchEvent(new Event('change'));}});}
  if(chatTimeline)chatTimeline.relabel();messageActions.relabel();
  window.BridgePresentation?.relabel();workbench.relabel();attachments.render();renderActivity();
  renderSkillPills();if(catalogData)renderSkills();
};
BridgeI18n.apply();

let notificationChannelBusy=false,selectedNotificationChannel='',notificationListRequest=0,notificationCommonLoaded=false;
const defaultWebhookBodyTemplate=JSON.stringify({event:'{{event}}',title:'{{title}}',body:'{{body}}',session_name:'{{session_name}}',url:'{{url}}'},null,2);
let savedWebhookFields=null,webhookTemplates={},webhookResolvedProvider='generic',revealedWebhookUrl='',webhookRevealRequest=0;
let webhookConfig={},selectedWebhookId='legacy',creatingWebhook=false;
function webhookRows(){return webhookConfig.webhookEndpoints||[{...webhookConfig,webhookId:'legacy',webhookName:t('默认 Webhook')}];}
function currentWebhook(){return webhookRows().find(row=>row.webhookId===selectedWebhookId)||{};}
function renderWebhookList(){
  $('webhook-endpoint-list').replaceChildren();
  const rows=[...webhookRows()];if(!rows.some(row=>row.webhookId==='legacy'))rows.unshift({webhookId:'legacy',webhookName:t('默认 Webhook')});
  $('webhook-endpoints').hidden=!(webhookConfig.webhookEndpoints||rows).length;
  for(const row of rows){const button=document.createElement('button');button.type='button';button.className='plain';button.dataset.webhookId=row.webhookId;button.setAttribute('aria-current',String(!creatingWebhook&&selectedWebhookId===row.webhookId));button.textContent=[row.webhookName||t('默认 Webhook'),row.webhookUrlDisplay||'',t(row.webhookEnabled?'已启用':'未启用')].filter(Boolean).join(' · ');button.onclick=()=>selectWebhook(row.webhookId);$('webhook-endpoint-list').append(button);}
  $('webhook-delete').hidden=creatingWebhook||!webhookRows().some(row=>row.webhookId===selectedWebhookId);
}
function selectWebhook(id){if(notificationChannelBusy)return;if(webhookDirty()){$('webhook-error').textContent=t('请先保存或取消当前修改。');return;}selectedWebhookId=id;creatingWebhook=false;renderWebhookChannel(currentWebhook());renderWebhookList();$('webhook-error').textContent='';}
function webhookUrlDraft(){return $('webhook-url').value===revealedWebhookUrl?'':$('webhook-url').value.trim();}
function selectedWebhookTemplate(){return webhookTemplates[$('webhook-provider').value==='auto'?webhookResolvedProvider:$('webhook-provider').value]||webhookTemplates.generic||defaultWebhookBodyTemplate;}
function switchWebhookTemplate(){const current=$('webhook-body-template').value;if(!current.trim()||(current===defaultWebhookBodyTemplate||Object.values(webhookTemplates).includes(current)))$('webhook-body-template').value=selectedWebhookTemplate();}
function hideWebhookUrl(){webhookRevealRequest++;if(revealedWebhookUrl&&$('webhook-url').value===revealedWebhookUrl)$('webhook-url').value='';revealedWebhookUrl='';$('webhook-url').type='password';$('webhook-url-eye').textContent=t('显示地址');$('webhook-url-eye').setAttribute('aria-label',t('显示地址'));}
async function toggleWebhookUrl(){
  if($('webhook-url').type==='text'){hideWebhookUrl();return;}
  if($('webhook-url').value||!savedWebhookHasUrl){$('webhook-url').type='text';$('webhook-url-eye').textContent=t('隐藏地址');$('webhook-url-eye').setAttribute('aria-label',t('隐藏地址'));return;}
  const request=++webhookRevealRequest,before=$('webhook-url').value,host=currentHost,chat=currentId,token=csrf;
  const id=selectedWebhookId;try{const result=await api('/api/notifications/channels/webhook/url',{webhookId:id});if(request!==webhookRevealRequest||selectedWebhookId!==id||!$('webhook-dialog').open||$('webhook-url').value!==before||currentHost!==host||currentId!==chat||csrf!==token)return;revealedWebhookUrl=result.webhookUrl||'';$('webhook-url').value=revealedWebhookUrl;$('webhook-url').type='text';$('webhook-url-eye').textContent=t('隐藏地址');$('webhook-url-eye').setAttribute('aria-label',t('隐藏地址'));}catch(error){$('webhook-error').textContent=t(error.message);}
}
let savedWebhookHasUrl=false;
function webhookFields(){return {webhookName:$('webhook-name').value.trim(),webhookEnabled:$('webhook-enabled').checked,webhookProvider:$('webhook-provider').value,webhookSignEnabled:$('webhook-sign-enabled').checked,webhookBodyTemplate:$('webhook-body-template').value,clearWebhookUrl:$('webhook-clear-url').checked,clearWebhookHeaders:$('webhook-clear-headers').checked,clearWebhookSignSecret:$('webhook-clear-sign-secret').checked};}
function webhookDirty(){return creatingWebhook||!savedWebhookFields||JSON.stringify(webhookFields())!==JSON.stringify(savedWebhookFields)||!!(webhookUrlDraft()||$('webhook-headers').value||$('webhook-sign-secret').value);}
function webhookProviderHint(config){const provider=$('webhook-provider').value;return t(({auto:config.webhookResolvedProvider==='feishu'?'已识别飞书地址；默认模板自动使用飞书格式。':'按地址自动识别服务类型，并选择相应默认模板。',generic:'自定义 HTTPS Webhook，可编辑 JSON 正文。',feishu:'飞书群机器人地址形如 open.feishu.cn/open-apis/bot/v2/hook/…；签名可选。',dingtalk:'钉钉群机器人地址形如 oapi.dingtalk.com/robot/send?access_token=…；签名可选。',discord:'Discord 地址形如 discord.com/api/webhooks/…；content 最多 2000 字符。',telegram:'Telegram 地址形如 api.telegram.org/bot…/sendMessage；在 JSON 中填写 chat_id，text 最多 4096 字符。'})[provider]||'');}
function renderWebhookChannel(config){
  hideWebhookUrl();webhookTemplates=config.webhookTemplates||webhookTemplates;webhookResolvedProvider=config.webhookResolvedProvider||'generic';savedWebhookHasUrl=!!config.hasWebhookUrl;
  $('webhook-name').value=config.webhookName|| (selectedWebhookId==='legacy'?t('默认 Webhook'):'');$('webhook-name').disabled=selectedWebhookId==='legacy'&&!creatingWebhook;
  $('webhook-enabled').checked=!!config.webhookEnabled;
  $('webhook-provider').value=config.webhookProvider||'auto';$('webhook-sign-enabled').checked=!!config.webhookSignEnabled;
  $('webhook-url').value='';$('webhook-headers').value='';$('webhook-sign-secret').value='';
  $('webhook-clear-url').checked=false;$('webhook-clear-headers').checked=false;$('webhook-clear-sign-secret').checked=false;
  $('webhook-body-template').value=config.webhookBodyTemplate||defaultWebhookBodyTemplate;
  $('webhook-url-saved').textContent=config.hasWebhookUrl?t('地址已保存：')+(config.webhookUrlDisplay||''):t('尚未保存地址');
  $('webhook-url').placeholder=t(config.hasWebhookUrl?'已保存，留空保留':'输入 HTTPS 地址');
  $('webhook-headers-saved').textContent=t(config.hasWebhookHeaders?'已保存请求头；留空保留':'尚未保存请求头');
  $('webhook-sign-saved').textContent=t(config.hasWebhookSignSecret?'已保存签名密钥；留空保留':'尚未保存签名密钥');
  $('webhook-provider-hint').textContent=webhookProviderHint(config);
  savedWebhookFields=webhookFields();
}
function webhookHeaders(){
  const raw=$('webhook-headers').value.trim();if(!raw)return {};
  let value;try{value=JSON.parse(raw);}catch{throw Error(t('请求头必须是有效的 JSON 对象。'));}
  if(!value||Array.isArray(value)||typeof value!=='object'||Object.entries(value).some(([key,item])=>!key||typeof item!=='string'))throw Error(t('请求头必须是字符串键值组成的 JSON 对象。'));
  return value;
}
async function webhookAction(test){
  if(notificationChannelBusy)return;
  if(test&&webhookDirty()){$('webhook-error').textContent=t('请先保存通知配置，再发送测试通知。');return;}
  if(!test){try{webhookHeaders();}catch(error){$('webhook-error').textContent=error.message;return;}}
  notificationChannelBusy=true;for(const id of ['webhook-save','webhook-test','webhook-delete'])$(id).disabled=true;$('webhook-error').textContent='';
  const identity=[currentHost,currentId,csrf,selectedWebhookId,creatingWebhook];
  const stillHere=()=>$('webhook-dialog').open&&currentHost===identity[0]&&currentId===identity[1]&&csrf===identity[2]&&selectedWebhookId===identity[3]&&creatingWebhook===identity[4];
  try{
    const url='/api/notifications/channels/webhook';
    if(test){const result=await api(url+'/test',{webhookId:selectedWebhookId});toast(result.message||'Webhook 已接受测试请求，请检查接收服务');}
    else{
      const submittedFields=webhookFields(),submittedUrl=$('webhook-url').value,submittedHeaders=$('webhook-headers').value,submittedSecret=$('webhook-sign-secret').value;
      const body={action:'upsert',...(creatingWebhook?{}:{webhookId:selectedWebhookId}),...submittedFields,webhookUrl:webhookUrlDraft(),webhookHeaders:webhookHeaders(),webhookSignSecret:submittedSecret};
      const saved=await api(url+'/endpoints',body);
      if(!stillHere())return;
      const unchanged=JSON.stringify(webhookFields())===JSON.stringify(submittedFields)&&$('webhook-url').value===submittedUrl&&$('webhook-headers').value===submittedHeaders&&$('webhook-sign-secret').value===submittedSecret;
      webhookConfig=saved;selectedWebhookId=saved.selectedWebhookId||selectedWebhookId;creatingWebhook=false;
      savedWebhookFields=submittedFields;
      if(unchanged)renderWebhookChannel(currentWebhook());renderWebhookList();
      toast('Webhook 配置已保存');if(currentId)await loadNotificationState(currentId,currentHost);
    }
  }catch(error){if(stillHere())$('webhook-error').textContent=t(error.message);}
  finally{notificationChannelBusy=false;for(const id of ['webhook-save','webhook-test','webhook-delete'])$(id).disabled=false;}
}
$('webhook-add').onclick=()=>{if(notificationChannelBusy)return;if(webhookDirty()){$('webhook-error').textContent=t('请先保存或取消当前修改。');return;}creatingWebhook=true;selectedWebhookId='';renderWebhookChannel({webhookProvider:'auto',webhookBodyTemplate:webhookTemplates.generic,webhookTemplates});renderWebhookList();$('webhook-name').focus();};
$('webhook-cancel').onclick=()=>{if(notificationChannelBusy)return;creatingWebhook=false;selectedWebhookId=webhookRows().some(row=>row.webhookId===selectedWebhookId)?selectedWebhookId:(webhookRows()[0]?.webhookId||'legacy');renderWebhookChannel(currentWebhook());renderWebhookList();$('webhook-error').textContent='';};
$('webhook-delete').onclick=async()=>{if(notificationChannelBusy||!confirm(t('确定删除此 Webhook 配置？')))return;notificationChannelBusy=true;const id=selectedWebhookId,host=currentHost,chat=currentId,token=csrf;try{const saved=await api('/api/notifications/channels/webhook/endpoints',{action:'delete',webhookId:id});if(!$('webhook-dialog').open||selectedWebhookId!==id||currentHost!==host||currentId!==chat||csrf!==token)return;webhookConfig=saved;selectedWebhookId=saved.selectedWebhookId||'legacy';creatingWebhook=false;renderWebhookChannel(currentWebhook());renderWebhookList();toast('Webhook 配置已删除');}catch(error){if($('webhook-dialog').open&&selectedWebhookId===id)$('webhook-error').textContent=t(error.message);}finally{notificationChannelBusy=false;}};
$('webhook-form').onsubmit=event=>{event.preventDefault();return webhookAction(false);};
$('webhook-test').onclick=()=>webhookAction(true);
$('webhook-provider').onchange=()=>{$('webhook-provider-hint').textContent=webhookProviderHint({webhookResolvedProvider});switchWebhookTemplate();};
$('webhook-use-template').onclick=()=>{$('webhook-body-template').value=selectedWebhookTemplate();};
$('webhook-url-eye').onclick=toggleWebhookUrl;
$('webhook-url').addEventListener('input',()=>{if(revealedWebhookUrl&&$('webhook-url').value!==revealedWebhookUrl)revealedWebhookUrl='';webhookRevealRequest++;});
$('webhook-dialog').addEventListener('close',()=>{hideWebhookUrl();savedWebhookFields=null;});
$('webhook-back').onclick=()=>{$('webhook-dialog').close();$('pushplus-settings').onclick();};
const notificationChannelDetails={
  bark:{name:'Bark',enabled:'barkEnabled',server:'barkServer',secret:'barkKey',hasSecret:'hasBarkKey',clear:'clearBarkKey',secretLabel:'Device Key',help:'在 iPhone 的 Bark 中获取 Device Key，并填写服务地址。'},
  ntfy:{name:'ntfy',enabled:'enabled',server:'server',secret:'token',hasSecret:'hasToken',clear:'clearToken',secretLabel:'Token',help:'在 Android 的 ntfy 中订阅相同服务和主题。公共主题可以留空 Token。'},
  pushplus:{name:'PushPlus',enabled:'pushplusEnabled',secret:'pushplusToken',hasSecret:'hasPushplusToken',clear:'clearPushplusToken',secretLabel:'Token',help:'通过微信获取 PushPlus Token，保存后可以发送测试通知。'}
};
function renderNotificationChannel(config){
  const channel=notificationChannelDetails[selectedNotificationChannel];
  $('pushplus-heading').textContent=channel.name;
  $('notification-channel-help').textContent=t(channel.help);
  $('pushplus-help').hidden=selectedNotificationChannel!=='pushplus';
  $('notification-channel-enable-label').textContent=t('启用 '+channel.name);
  $('pushplus-enabled').checked=!!config[channel.enabled];
  $('notification-server-row').hidden=!channel.server;
  $('notification-topic-row').hidden=selectedNotificationChannel!=='ntfy';
  if(channel.server){
    $('notification-server-label').textContent=t(channel.name+' 服务地址');
    $('notification-server').value=config[channel.server]||'';
  }
  if(selectedNotificationChannel==='ntfy')$('notification-topic').value=config.topic||'';
  $('notification-secret-label').textContent=channel.secretLabel;
  $('notification-clear-label').textContent=t('清除已保存的 '+channel.secretLabel);
  $('pushplus-token').value='';$('pushplus-clear').checked=false;
  $('pushplus-token').placeholder=t(config[channel.hasSecret]?'已保存，留空保留':'填写 '+channel.name+' '+channel.secretLabel);
}
function renderNotificationCommon(config){
  for(const id of ['security-enabled','address-enabled','include-title'])$('notification-'+id).checked=!!config[{'security-enabled':'securityEnabled','address-enabled':'addressEnabled','include-title':'includeTitle'}[id]];
  $('notification-address-name').value=config.addressName||'';
  $('notification-click-base').value=config.clickBase||'';
}
function notificationCommonControls(enabled){
  for(const id of ['security-enabled','address-enabled','address-name','include-title','click-base','common-save'])$('notification-'+id).disabled=!enabled;
}
async function loadNotificationCommon(request){
  notificationCommonLoaded=false;notificationCommonControls(false);
  $('notification-common-retry').hidden=true;$('notification-common-error').textContent='';
  try{
    const config=await api('/api/notifications/channels/common');
    if(request!==notificationListRequest||!$('notification-channels-dialog').open)return;
    renderNotificationCommon(config);notificationCommonLoaded=true;notificationCommonControls(true);
  }catch(error){
    if(request!==notificationListRequest||!$('notification-channels-dialog').open)return;
    $('notification-common-error').textContent=t(error.message);$('notification-common-retry').hidden=false;
  }
}
async function loadNotificationChannelStatuses(request){
  const names=[...Object.keys(notificationChannelDetails),'webhook'];
  for(const name of names)$('notification-status-'+name).textContent=t('读取中');
  const responses=await Promise.allSettled(names.map(name=>api('/api/notifications/channels/'+name)));
  if(request!==notificationListRequest||!$('notification-channels-dialog').open)return;
  for(const [index,name] of names.entries()){
    const result=responses[index],channel=notificationChannelDetails[name];
    const rows=name==='webhook'?(result.value?.webhookEndpoints||[result.value]):[];
    const configured=result.status==='fulfilled'&&(name==='webhook'?rows.some(row=>row?.hasWebhookUrl):name==='ntfy'?!!result.value.topic:!!result.value[channel.hasSecret]);
    const enabled=name==='webhook'?rows.some(row=>row?.webhookEnabled):result.value?.[channel.enabled];
    const status=result.status==='rejected'?'读取失败':configured?(enabled?'已开启':'已关闭'):'未配置';
    $('notification-status-'+name).textContent=t(status);
  }
}
$('pushplus-settings').onclick=async()=>{
  if(notificationChannelBusy)return;
  const request=++notificationListRequest;
  $('notification-channels-dialog').showModal();
  await Promise.all([loadNotificationCommon(request),loadNotificationChannelStatuses(request)]);
};
$('notification-channels-dialog').addEventListener('close',()=>{notificationListRequest++;notificationCommonLoaded=false;notificationCommonControls(false);});
$('notification-common-retry').onclick=()=>loadNotificationCommon(notificationListRequest);
for(const button of document.querySelectorAll('[data-notification-channel]'))button.onclick=async()=>{
  if(notificationChannelBusy)return;
  const channel=button.dataset.notificationChannel;
  const request=notificationListRequest;
  notificationChannelBusy=true;button.disabled=true;
  try{
    const config=await api('/api/notifications/channels/'+channel);
    if(request!==notificationListRequest||!$('notification-channels-dialog').open)return;
    if(channel==='webhook'){
      webhookConfig=config;webhookTemplates=config.webhookTemplates||webhookTemplates;selectedWebhookId=config.webhookEndpoints?.[0]?.webhookId||'legacy';creatingWebhook=false;renderWebhookChannel(currentWebhook());renderWebhookList();$('webhook-error').textContent='';$('notification-channels-dialog').close();$('webhook-dialog').showModal();return;
    }
    selectedNotificationChannel=channel;
    renderNotificationChannel(config);
    $('pushplus-error').textContent='';
    $('notification-channels-dialog').close();
    $('pushplus-dialog').showModal();
  }catch(error){if(request===notificationListRequest&&$('notification-channels-dialog').open)$('notification-common-error').textContent=t(error.message);}
  finally{notificationChannelBusy=false;button.disabled=false;}
};
$('notification-channel-back').onclick=()=>{$('pushplus-dialog').close();$('pushplus-settings').onclick();};
$('notification-common-save').onclick=async()=>{
  if(notificationChannelBusy||!notificationCommonLoaded||!$('notification-channels-dialog').open)return;
  notificationChannelBusy=true;$('notification-common-save').disabled=true;$('notification-common-error').textContent='';
  const request=notificationListRequest;
  try{
    const config=await api('/api/notifications/channels/common',{
      securityEnabled:$('notification-security-enabled').checked,
      addressEnabled:$('notification-address-enabled').checked,
      addressName:$('notification-address-name').value.trim(),
      includeTitle:$('notification-include-title').checked,
      clickBase:$('notification-click-base').value.trim()
    });
    if(request===notificationListRequest&&$('notification-channels-dialog').open){renderNotificationCommon(config);toast('通用通知设置已保存');}
  }catch(error){if(request===notificationListRequest&&$('notification-channels-dialog').open)$('notification-common-error').textContent=t(error.message);}
  finally{notificationChannelBusy=false;if(request===notificationListRequest&&notificationCommonLoaded)notificationCommonControls(true);}
};
async function notificationChannelAction(test){
  if(notificationChannelBusy||!selectedNotificationChannel)return;
  notificationChannelBusy=true;
  const ids=['pushplus-save','pushplus-test','pushplus-enabled','notification-server','notification-topic','pushplus-token','pushplus-clear'];
  for(const id of ids)$(id).disabled=true;
  $('pushplus-error').textContent='';
  const channel=notificationChannelDetails[selectedNotificationChannel];
  try{
    const url='/api/notifications/channels/'+selectedNotificationChannel;
    if(test){
      await api(url+'/test',{});
      toast(channel.name+' 已接受测试通知，请在手机确认是否收到');
    }else{
      const body={[channel.enabled]:$('pushplus-enabled').checked,[channel.secret]:$('pushplus-token').value.trim(),[channel.clear]:$('pushplus-clear').checked};
      if(channel.server)body[channel.server]=$('notification-server').value.trim();
      if(selectedNotificationChannel==='ntfy')body.topic=$('notification-topic').value.trim();
      renderNotificationChannel(await api(url,body));
      toast(channel.name+' 配置已保存');
      if(currentId)await loadNotificationState(currentId,currentHost);
    }
  }catch(error){$('pushplus-error').textContent=t(error.message);}
  finally{notificationChannelBusy=false;for(const id of ids)$(id).disabled=false;}
}
$('pushplus-form').onsubmit=event=>{event.preventDefault();return notificationChannelAction(false);};
$('pushplus-test').onclick=()=>notificationChannelAction(true);

const codexCliRoot=el('div');
const codexCliDetails=el('details'),codexCliSummary=el('summary','',t('新建 Codex API 接入'));codexCliSummary.dataset.i18n='新建 Codex API 接入';codexCliDetails.append(codexCliSummary,codexCliRoot);$('accounts-dialog').append(codexCliDetails);
const codexCliAccessPanel=typeof CliAccessPanel==='undefined'?null:new CliAccessPanel({root:codexCliRoot,provider:'codex',formOnly:true,onChanged:()=>accountsPanel?.refresh(),read:()=>api('/api/cli-access'),request:value=>api('/api/cli-access',value)});
let codexWslRoot=null,codexWslAccounts=null,accountEntryControls=null,accountEntrySelect=null;
async function loadWslAccounts(){
  if(clientNavigation.wslSupported!==true)return false;
  await window.BridgeFeatures.load('wsl');
  return clientNavigation.wslSupported===true;
}
async function refreshSelectedAccounts(){
  if(codexAccountEntry==='desktop'||codexAccountEntry.startsWith('codex:')){codexWslAccounts?.clear();accountsPanel?.refresh();if(codexAccountEntry==='desktop')codexCliAccessPanel?.refresh();return;}
  const entry=codexAccountEntry;
  try{
    if(!await loadWslAccounts()||!$('accounts-dialog').open||codexAccountEntry!==entry)return;
    if(!codexWslAccounts)codexWslAccounts=new WslAccountsPanel({root:codexWslRoot,provider:'codex',read:entry=>api('/api/wsl-accounts?provider=codex&entry='+encodeURIComponent(entry)),request:value=>api('/api/wsl-accounts',value)});
    codexWslAccounts.select(entry);
  }catch(error){if($('accounts-dialog').open&&codexAccountEntry===entry)toast(error.message);}
}
$('accounts-dialog').addEventListener('close',()=>{codexCliAccessPanel?.clear();codexWslAccounts?.clear();});
function renderAccountEntries(){
  const independent=codexAccountEntry.startsWith('codex:'),entries=codexEntries().filter(row=>row.enabled&&(independent?row.id===codexAccountEntry:clientNavigation.wslSupported===true&&row.kind==='wsl-cli'));
  if(entries.length&&!accountEntryControls){
    accountEntryControls=el('div','accounts-runtime-controls');const label=el('label','',t('运行环境'));accountEntrySelect=el('select');accountEntrySelect.setAttribute('aria-label',t('运行环境'));label.append(accountEntrySelect);accountEntryControls.append(label);$('accounts-heading').closest('.picker-head').after(accountEntryControls);
    codexWslRoot=el('div');$('accounts-dialog').append(codexWslRoot);
    accountEntrySelect.onchange=()=>{codexAccountEntry=accountEntrySelect.value;accountsPanel?.clear();renderAccountEntries();refreshSelectedAccounts();};
  }
  if(codexAccountEntry!=='desktop'&&!entries.some(row=>row.id===codexAccountEntry))codexAccountEntry='desktop';
  if(accountEntrySelect){accountEntrySelect.replaceChildren(new Option(t('此电脑'),'desktop'),...entries.map(row=>new Option(row.name||row.label,row.id)));accountEntrySelect.value=codexAccountEntry;accountEntryControls.hidden=independent||!entries.length;}
  codexCliDetails.hidden=codexAccountEntry!=='desktop';$('accounts-content').hidden=codexAccountEntry.startsWith('wsl:');if(codexWslRoot)codexWslRoot.hidden=!codexAccountEntry.startsWith('wsl:');if(typeof accountsPanel!=='undefined'&&accountsPanel)accountsPanel.refreshButton.hidden=codexAccountEntry.startsWith('wsl:');
  if(codexCliDetails.hidden)codexCliAccessPanel?.clear();
}

const accountsPanel=window.BridgeSharedRelay||typeof AccountsPanel==='undefined'?null:new AccountsPanel({root:$('accounts-content'),toolbar:$('accounts-heading'),read:force=>api(accountUrl('/api/accounts'+(accountsPanel?.visible()&&accountsPanel?.value?.resetDynamicEnabled?'?resetForecast='+(force?'refresh':'1'):''))),request:({action,...value})=>action==='ignoreSubmission'?ignoreSubmission(value):api(accountUrl(action==='initializeDesktopRuntime'?'/api/accounts/desktop-runtime':action==='retrySsh'?'/api/accounts/ssh-retry':action==='account'?'/api/accounts/account':action==='details'?'/api/accounts/details':action==='reminders'?'/api/accounts/reminders':action==='checkDesktopUpdate'||action==='requestDesktopUpdate'?'/api/accounts/desktop-update':'/api/accounts/switch'),action==='checkDesktopUpdate'||action==='requestDesktopUpdate'?{action,...value}:value),onChanged:value=>{if(codexActiveAccountId!==undefined&&codexActiveAccountId!==value.activeId){browserNavigation.invalidate();if(typeof ChatTimeline!=='undefined')ChatTimeline.clearCache?.();clearCodexProvisionalDrafts();clearQuoteComposer();codexDraftScope=null;codexDraftTouched=false;}resetModelCatalog();},onUpdate:value=>{codexActiveAccountId=value.activeId;const label='账号管理';$('accounts-button').setAttribute('data-i18n',label);$('accounts-button').textContent=t(label);}});
accountsPanel?.attachApiAccess(codexCliAccessPanel,codexCliDetails);
function openCurrentAccounts(){
  const activeProvider=clientNavigation.provider,provider=ClientIdentity.family(activeProvider),entry=ClientIdentity.entry(activeProvider)?activeProvider:provider;
  if(!provider)return false;
  if(isCodexClient(provider)){
    if(!accountsPanel)return false;
    refreshEntryAccounts();renderAccountEntries();$('accounts-dialog').showModal();refreshSelectedAccounts();return true;
  }
  const name=clientNavigation.clients.find(row=>row.id===activeProvider)?.name||provider;
  const dialog=desktopSessions.dialog('账号与接入'),content=el('div','provider-account');
  const head=dialog.querySelector('.picker-head'),title=head.querySelector('h2'),toolbar=el('div','accounts-dialog-heading');
  title.classList.add('dialog-content-start');title.tabIndex=-1;title.autofocus=true;title.replaceWith(toolbar);toolbar.append(title);
  dialog.append(el('p','account-muted',name),content);
  const panel=new DesktopClientAccountsPanel({root:content,provider,toolbar,
    read:()=>api(ClientIdentity.path(entry,'accounts')),
    request:value=>api(ClientIdentity.path(entry,'accounts'),value),
    readModels:async(refresh=false)=>{const namespace=[desktopSessions.listCacheIdentity,desktopSessions.listCacheHost||'local',entry],value=await api(ClientIdentity.path(entry,'account',null,refresh?{refresh:'true'}:{}));return {...value,...window.BridgeModelCatalogCache.write(desktopSessions.modelCatalogs,namespace,value)};},
    onChanged:(value,previous)=>desktopSessions.accountChanged(entry,previous,value)
  });
  const unbindConnection=clientNavigation.bindAccountManagement(panel,entry);
  let claudeWslAccounts=null,selectClaudeAccounts=null;
  if(provider==='claude'&&!ClientIdentity.entry(entry)&&typeof CliAccessPanel!=='undefined'){
    const details=el('details','account-compact'),root=el('div'),summary=el('summary','',t('Claude Code CLI 接入'));summary.dataset.i18n='Claude Code CLI 接入';details.append(summary,root);dialog.append(details);
    claudeCliAccessPanel=new CliAccessPanel({root,provider:'claude',read:()=>api('/api/cli-access'),request:value=>api('/api/cli-access',value)});
    const initializeWsl=async()=>{
    if(!await loadWslAccounts()||!dialog.open)return;
    const selectorControls=el('div','accounts-runtime-controls'),selectorLabel=el('label','',t('运行环境')),selector=el('select'),wslRoot=el('div');selector.setAttribute('aria-label',t('运行环境'));selectorLabel.append(selector);selectorControls.append(selectorLabel);head.after(selectorControls);dialog.append(wslRoot);
    claudeWslAccounts=new WslAccountsPanel({root:wslRoot,provider:'claude',onSwitched:value=>desktopSessions.continueWslAccountDrafts(value),read:entry=>api('/api/wsl-accounts?provider=claude&entry='+encodeURIComponent(entry)),request:value=>api('/api/wsl-accounts',value)});
    const entries=clientNavigation.clients.find(row=>row.id==='claude')?.entries||[];
    const targets=entries.filter(row=>row.id?.startsWith('wsl-claude:'));
    selector.append(new Option(t('此电脑'),'local'),...targets.map(row=>new Option(row.name||row.label||row.id,row.id)));selectorControls.hidden=!targets.length;
    const select=()=>{const local=selector.value==='local';content.hidden=!local;details.hidden=!local;panel.refreshButton.hidden=!local;if(local){claudeWslAccounts.clear();panel.resume();claudeCliAccessPanel.refresh();}else{panel.generation++;panel.modelsRevision++;clearTimeout(panel.timer);panel.loading=false;panel.busy=false;claudeCliAccessPanel.clear();claudeWslAccounts.select(selector.value);}};
    selector.onchange=select;const activeEntry=desktopSessions.isChatOpen&&desktopSessions.state?.runtime==='wsl'?desktopSessions.state.host:activeProvider;if(targets.some(row=>row.id===activeEntry))selector.value=activeEntry;select();
    };
    selectClaudeAccounts=()=>{if(clientNavigation.wslSupported===true)initializeWsl().catch(error=>{if(dialog.open)toast(error.message);});else{panel.resume();claudeCliAccessPanel.refresh();}};
  }
  dialog.addEventListener('close',()=>{unbindConnection();panel.dispose();claudeWslAccounts?.dispose();claudeCliAccessPanel?.dispose();claudeCliAccessPanel=null;});dialog.showModal();if(selectClaudeAccounts)selectClaudeAccounts();else panel.resume();
  return true;
}
$('accounts-button').onclick=openCurrentAccounts;
window.BridgeNavigation={
  accounts:openCurrentAccounts,
  back(){
    const dialog=[...document.querySelectorAll('dialog[open]')].at(-1);if(dialog){dialog.close();return true;}
    if(!isCodexClient(clientNavigation.provider)){
      if(desktopSessions.workbench?.back())return true;
      return desktopSessions.backToList();
    }
    if(workbench.back())return true;
    if($('app').classList.contains('chat-open')){$('back').click();return true;}
    return false;
  }
};

$('permissions-button').onclick=()=>{
  if(!currentId||!state?.connected)return;
  const id=currentId,host=currentHost,runtime=state.access?.runtime||'desktop',dialog=el('dialog','picker'),head=el('div','picker-head');
  dialog.dataset.permissionThread=id;dialog.dataset.permissionHost=host;dialog.dataset.permissionRuntime=runtime;
  const isCurrent=()=>{
    const current=dialog.open&&currentId===id&&currentHost===host&&state?.connected===true&&
      (state.access?.runtime||'desktop')===runtime;
    if(!current&&dialog.open)dialog.close();
    return current;
  };
  head.append(el('h2','',t('会话权限')));const close=el('button','icon-button','×');close.type='button';close.setAttribute('aria-label',t('关闭'));close.onclick=()=>dialog.close();head.append(close);dialog.append(head,el('p','muted',t('保存后从下一轮生效')));
  if(state.linuxPermissionChecks===true||state.access?.runtime==='cli'){
    document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
    permissionPicker(dialog,{nativeCli:runtime==='cli',load:()=>api(sessionUrl(id,'permissions',host)),
      save:preset=>api(sessionUrl(id,'permissions',host),{preset,confirmed:preset==='full-access',expectedRuntime:runtime}),
      isCurrent,onSaved:()=>toast('权限已更新')});
    return;
  }
  const error=el('p','error');
  for(const [preset,label,help] of [['ask','请求批准','需要额外权限时询问你。'],['auto-review','帮我批准','由 Codex 审核需要额外权限的操作。'],['full-access','完全访问权限','允许访问工作区外的文件和网络，无需逐次批准。']]){
    const button=permissionOption(el('button'),preset,label,help,state.permissionMode===preset);
    button.onclick=async()=>{
      if(!isCurrent())return;
      if(preset==='full-access'&&!confirm(t('允许此会话完全访问电脑文件和网络？请仅在信任任务内容时开启。')))return;
      dialog.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{const result=await api(sessionUrl(id,'permissions',host),{preset,confirmed:preset==='full-access',expectedRuntime:runtime});if(!isCurrent())return;if(result.confirmed){dialog.close();toast('权限已更新');}else error.textContent=t('已提交权限设置，等待桌面确认。');}
      catch(e){if(isCurrent())error.textContent=t(e.message);}finally{dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}
    };dialog.append(button);
  }
  dialog.append(error);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
};


const fileTransferSettings=window.BridgeFileActions?.mountSettings($('appearance-dialog'),value=>api('/api/file-transfer',value));
if(fileTransferSettings)$('appearance-dialog').querySelector('.settings-action-row')?.after(fileTransferSettings);
