'use strict';
const $=id=>document.getElementById(id),api=window.bridgeDesktop,t=BridgeI18n.t;
let connectionDraft=[],savedConnections='[]',lanDraft=[],savedLan='[]';
let snapshot,dirty=false,loading=false,startingUntil=0,activeTab='overview',savedFields={},feedbackKind='',lastFeedback;
let networkAdaptationState,networkAdaptationBusy=false,networkAdaptationRevision=0,networkAdaptationError='';
const defaultWebhookBodyTemplate=JSON.stringify({event:'{{event}}',title:'{{title}}',body:'{{body}}',session_name:'{{session_name}}',url:'{{url}}'},null,2);
let webhookTemplates={},revealedWebhookUrl='',webhookRevealRequest=0;
let webhookSelectedId='legacy',webhookCreating=false,webhookSavedFields=null,webhookSaving=false,webhookRenderedRowSignature='';
function webhookRows(){const config=snapshot?.notifications||{};return config.webhookEndpoints||[{...config,webhookId:'legacy',webhookName:t('默认 Webhook')}];}
function webhookRow(){return webhookRows().find(row=>row.webhookId===webhookSelectedId)||{};}
function webhookFields(){return {webhookName:webhookSelectedId==='legacy'?'默认 Webhook':$('webhook-name').value.trim(),webhookEnabled:$('webhook-enabled').checked,webhookProvider:$('webhook-provider').value,webhookSignEnabled:$('webhook-sign-enabled').checked,webhookBodyTemplate:$('webhook-body-template').value,clearWebhookUrl:$('clear-webhook-url').checked,clearWebhookHeaders:$('clear-webhook-headers').checked,clearWebhookSignSecret:$('clear-webhook-sign-secret').checked};}
function webhookDirty(){return webhookCreating||!webhookSavedFields||JSON.stringify(webhookFields())!==JSON.stringify(webhookSavedFields)||!!(webhookUrlDraft()||$('webhook-headers').value||$('webhook-sign-secret').value);}
function webhookDisplayName(row){return row.webhookId==='legacy'&&(!row.webhookName||row.webhookName==='默认 Webhook')?t('默认 Webhook'):row.webhookName||'';}
function renderWebhookList(){const list=$('webhook-endpoint-list');list.replaceChildren();const rows=[...webhookRows()];if(!rows.some(row=>row.webhookId==='legacy'))rows.unshift({webhookId:'legacy',webhookName:'默认 Webhook'});for(const row of rows){const button=document.createElement('button');button.type='button';button.dataset.webhookId=row.webhookId;button.setAttribute('aria-current',String(!webhookCreating&&webhookSelectedId===row.webhookId));button.textContent=[webhookDisplayName(row),row.webhookUrlDisplay||'',t(row.webhookEnabled?'已启用':'未启用')].filter(Boolean).join(' · ');button.onclick=()=>selectWebhook(row.webhookId);list.append(button);}$('webhook-delete').hidden=webhookCreating||!webhookRows().some(row=>row.webhookId===webhookSelectedId);}
function renderWebhookCopy(row){
  if(webhookSelectedId==='legacy'&&!webhookCreating)$('webhook-name').value=webhookDisplayName({...row,webhookId:'legacy'});
  $('webhook-url-saved').textContent=row.hasWebhookUrl?t('地址已保存：')+(row.webhookUrlDisplay||''):t('尚未保存地址');
  $('webhook-url').placeholder=t(row.hasWebhookUrl?'已保存，留空保留':'输入 HTTPS 地址');
  $('webhook-headers-saved').textContent=t(row.hasWebhookHeaders?'已保存请求头；留空保留':'尚未保存请求头');
  $('webhook-sign-saved').textContent=t(row.hasWebhookSignSecret?'已保存签名密钥；留空保留':'尚未保存签名密钥');
  $('webhook-provider-hint').textContent=webhookProviderHint(row);
  const visible=$('webhook-url').type==='text';$('webhook-url-eye').textContent=t(visible?'隐藏地址':'显示地址');$('webhook-url-eye').setAttribute('aria-label',t(visible?'隐藏地址':'显示地址'));
}
function renderWebhookEditor(row){
  hideWebhookUrl();webhookTemplates=row.webhookTemplates||snapshot?.notifications?.webhookTemplates||webhookTemplates;
  input('webhook-name',webhookDisplayName({...row,webhookId:webhookSelectedId}));$('webhook-name').disabled=webhookSelectedId==='legacy'&&!webhookCreating;
  $('webhook-enabled').checked=!!row.webhookEnabled;input('webhook-provider',row.webhookProvider||'auto');$('webhook-sign-enabled').checked=!!row.webhookSignEnabled;
  input('webhook-body-template',row.webhookBodyTemplate||webhookTemplates.generic||defaultWebhookBodyTemplate);
  for(const id of ['webhook-url','webhook-headers','webhook-sign-secret'])input(id,'');
  for(const id of ['clear-webhook-url','clear-webhook-headers','clear-webhook-sign-secret'])$(id).checked=false;
  renderWebhookCopy(row);webhookSavedFields=webhookFields();webhookRenderedRowSignature=JSON.stringify(row);
}
function selectWebhook(id){if(webhookSaving)return;if(webhookDirty()){feedback(t('请先保存或取消当前修改。'),true);return;}webhookSelectedId=id;webhookCreating=false;renderWebhookEditor(webhookRow());renderWebhookList();}
function webhookUrlDraft(){return $('webhook-url').value===revealedWebhookUrl?'':$('webhook-url').value.trim();}
function selectedWebhookTemplate(config=webhookRow()){const provider=$('webhook-provider').value;return webhookTemplates[provider==='auto'?config.webhookResolvedProvider||'generic':provider]||webhookTemplates.generic||defaultWebhookBodyTemplate;}
function switchWebhookTemplate(){const current=$('webhook-body-template').value;if(!current.trim()||(current===defaultWebhookBodyTemplate||Object.values(webhookTemplates).includes(current)))input('webhook-body-template',selectedWebhookTemplate());updateDirty();}
function hideWebhookUrl(){webhookRevealRequest++;if(revealedWebhookUrl&&$('webhook-url').value===revealedWebhookUrl)$('webhook-url').value='';revealedWebhookUrl='';$('webhook-url').type='password';$('webhook-url-eye').textContent=t('显示地址');$('webhook-url-eye').setAttribute('aria-label',t('显示地址'));}
async function toggleWebhookUrl(){
  if($('webhook-url').type==='text'){hideWebhookUrl();updateDirty();return;}
  if($('webhook-url').value||!webhookRow().hasWebhookUrl){$('webhook-url').type='text';$('webhook-url-eye').textContent=t('隐藏地址');$('webhook-url-eye').setAttribute('aria-label',t('隐藏地址'));return;}
  const request=++webhookRevealRequest,directory=snapshot.dataDir,before=$('webhook-url').value,id=webhookSelectedId;
  try{const result=await api.readCredentials({channel:'webhook',webhookId:id});if(request!==webhookRevealRequest||webhookSelectedId!==id||snapshot?.dataDir!==directory||activeTab!=='notifications'||$('webhook-url').value!==before)return;revealedWebhookUrl=result.webhookUrl||'';$('webhook-url').value=revealedWebhookUrl;$('webhook-url').type='text';$('webhook-url-eye').textContent=t('隐藏地址');$('webhook-url-eye').setAttribute('aria-label',t('隐藏地址'));updateDirty();}catch(error){feedback(error.message,true);}
}
function webhookProviderHint(config){
  const provider=$('webhook-provider').value;
  return t(({auto:config.webhookResolvedProvider==='feishu'?'已识别飞书地址；默认模板自动使用飞书格式。':'按地址自动识别服务类型，并选择相应默认模板。',generic:'自定义 HTTPS Webhook，可编辑 JSON 正文。',feishu:'飞书群机器人地址形如 open.feishu.cn/open-apis/bot/v2/hook/…；签名可选。',dingtalk:'钉钉群机器人地址形如 oapi.dingtalk.com/robot/send?access_token=…；签名可选。',discord:'Discord 地址形如 discord.com/api/webhooks/…；content 最多 2000 字符。',telegram:'Telegram 地址形如 api.telegram.org/bot…/sendMessage；在 JSON 中填写 chat_id，text 最多 4096 字符。'})[provider]||'');
}
function webhookHeaders(){
  const raw=$('webhook-headers').value.trim();if(!raw)return {};
  let value;try{value=JSON.parse(raw);}catch{throw Object.assign(Error(t('请求头必须是有效的 JSON 对象。')),{validation:{field:'webhook-headers'}});}
  if(!value||Array.isArray(value)||typeof value!=='object'||Object.entries(value).some(([key,item])=>!key||typeof item!=='string'))throw Object.assign(Error(t('请求头必须是字符串键值组成的 JSON 对象。')),{validation:{field:'webhook-headers'}});
  return value;
}
const busyActions=new Set();
let saving=false,startPending=false,stopPending=false,cloudflaredBusy=false,cloudflaredResult=null,cloudflaredProgress=null;
const titles={'desktop-sessions':'桌面会话',harness:'Harness 官方 Web',overview:t('概览'),network:t('网络访问'),devices:t('登录与设备'),notifications:t('消息通知'),advanced:t('应用与维护'),accounts:t('客户端与接入'),account:t('账户与额度'),updates:t('应用更新'),logs:t('运行日志')};
const watchPanel=new WatchPanel({root:$('watches'),change:value=>api.notificationWatches(value)});
const harnessPanel=typeof HarnessPanel==='undefined'?null:new HarnessPanel({root:$('harness-panel'),api,t,feedback});
const desktopConnections=typeof DesktopConnectionsPanel==='undefined'?null:new DesktopConnectionsPanel({root:$('desktop-connections'),api,feedback});
function fields(){return [...$('settings').querySelectorAll('input,textarea,select')].filter(node=>!node.closest('#watches')&&!node.closest('#connections')&&!node.closest('#lan-addresses')&&!node.closest('#file-transfer-settings')&&!node.closest('#webhook-editor')&&node.id!=='connection-kind'&&node.id!=='network-adaptation-enabled');}
function fieldValues(){return Object.fromEntries(fields().map(node=>[node.id,node.id==='webhook-sign-secret'?!!node.value:node.id==='webhook-url'?webhookUrlDraft():node.type==='checkbox'?node.checked:node.value]));}
function updateDirty(){
  const changed=new Set();
  for(const node of fields())if((node.id==='webhook-sign-secret'?!!node.value:node.id==='webhook-url'?webhookUrlDraft():node.type==='checkbox'?node.checked:node.value)!==savedFields[node.id])changed.add(moduleName(node.closest('[data-panel]').dataset.panel));
  if(JSON.stringify(connectionDraft)!==savedConnections||JSON.stringify(lanDraft)!==savedLan)changed.add('network');
  dirty=changed.size>0;
  for(const button of document.querySelectorAll('[data-tab]')){
    const unsaved=changed.has(button.dataset.tab);
    button.dataset.dirty=String(unsaved);
    button.setAttribute('aria-label',t(titles[button.dataset.tab])+(unsaved?t('，有未保存的修改'):''));
  }
  document.querySelectorAll('#connections input,#connections select,[data-remove-connection]').forEach(node=>node.disabled=!!snapshot?.runtime.running||busyActions.size>0);
  $('save').disabled=saving||busyActions.size>0;
  $('dirty-dot').hidden=!dirty;
  $('dirty-label').textContent=dirty?t('有未保存的修改'):t('配置已保存');
  $('save-bar').hidden=!dirty;
  for(const [id,key] of [['lan','lan'],['local-access','localAccess']]){
    const status=$(id+'-saved-state');if(!status)continue;
    const saved=key==='localAccess'?snapshot?.preferences?.localAccess!==false:!!snapshot?.preferences?.[key];
    status.textContent=t($(id).checked!==saved?'待保存':saved?'已启用':'已配置但未启用');
  }
  document.querySelectorAll('[data-connection-action]').forEach(button=>button.disabled=dirty||busyActions.size>0||(!['inspect','diagnostics','credentials','clearCredentials','tailscaleInspect','tailscaleAuthorize'].includes(button.dataset.connectionAction)&&!connectionDraft.find(c=>c.id===button.dataset.connection)?.enabled));
}
let toastTimer;
function dismissToast(){clearTimeout(toastTimer);$('toast').hidden=true;}
function feedback(text,error=false,kind=''){
  lastFeedback=[text,error,kind];text=t(text.replace(/^Error invoking remote method '[^']+': (?:Error: )?/,''));
  if(kind==='gateway'){
    feedbackKind=kind;$('error').hidden=!error;$('feedback').hidden=error;
    const target=$(error?'error':'feedback');if(target.textContent!==text)target.textContent=text;return;
  }
  clearTimeout(toastTimer);$('toast').hidden=false;$('toast').classList.toggle('is-error',error);
  $('toast-message').textContent=text;$('toast-message').setAttribute('role',error?'alert':'status');
  $('toast-close').setAttribute('aria-label',t('关闭通知'));
  $('toast-progress').getAnimations().forEach(animation=>animation.cancel());
  $('toast-progress').animate([{transform:'scaleX(1)'},{transform:'scaleX(0)'}],{duration:5000,fill:'forwards'});
  toastTimer=setTimeout(dismissToast,5000);
}
$('toast-close').onclick=dismissToast;
function focusField(node){
  if(!node)return;
  const panel=node.closest('[data-panel]');if(panel)tab(panel.dataset.panel);
  for(let parent=node.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  document.querySelectorAll('[aria-invalid="true"]').forEach(item=>item.removeAttribute('aria-invalid'));
  node.setAttribute('aria-invalid','true');node.scrollIntoView({block:'center',behavior:'instant'});node.focus({preventScroll:true});
  node.addEventListener('input',()=>node.removeAttribute('aria-invalid'),{once:true});
}
function unwrapValidation(value){if(value?.validationError)throw Object.assign(Error(value.validationError.message),{validation:value.validationError});return value;}
function showError(error){
  const location=error.validation;
  if(location)focusField($(location.connectionId?'connection-'+location.connectionId+'-'+location.field:location.field));
  feedback(error.message,true);
}

function moduleName(name){return ({account:'accounts','desktop-sessions':'accounts',harness:'accounts',updates:'advanced',logs:'advanced'})[name]||name;}
function tab(name){const requested=name;name=moduleName(name);activeTab=name;
  if(name!=='notifications')hideWebhookUrl();
  if(name!=='accounts'&&typeof claudeCliAccessPanel!=='undefined')claudeCliAccessPanel?.clear();
  if(name==='accounts'){desktopConnections?.refresh();desktopConnections?.refreshClients();harnessPanel?.refresh();if(typeof wslAccountScopes!=='undefined')for(const scope of wslAccountScopes)scope.load();}
  document.querySelectorAll('[data-panel]').forEach(node=>node.hidden=moduleName(node.dataset.panel)!==name);
  if(name==='accounts'&&!$('accounts-content').hidden)accountsPanel?.refresh();else accountsPanel?.resume();
  document.querySelectorAll('[data-tab]').forEach(node=>node.classList.toggle('active',node.dataset.tab===name));
  window.GatewayLayout?.tab(name);$('page-title').textContent=t(titles[name]);if(snapshot)updateDirty();if(name==='advanced')loadLogs();if(name==='devices')loadDevices();if(snapshot)renderUpdate();
  if(['desktop-sessions','harness'].includes(requested))document.querySelectorAll('[data-panel="'+requested+'"] > details').forEach(node=>node.open=true);
}
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>tab(button.dataset.tab));
document.querySelectorAll('[data-jump]').forEach(button=>button.onclick=()=>tab(button.dataset.jump));
$('settings').oninput=$('settings').onchange=()=>{if(snapshot){$('revoke-bound-devices-option').hidden=!$('password').value;updateDirty();}};
function renderLan(){
  const rows=[...(snapshot?.networkInterfaces||[])];
  for(const id of lanDraft)if(!rows.some(row=>row.id===id))rows.push({id,name:t('当前不可用'),addresses:[]});
  $('lan-addresses').replaceChildren();
  const choosing=$('lan-scope').value==='selected';
  for(const row of rows){
    const label=document.createElement('label'),box=document.createElement('input'),text=document.createElement('span');
    label.className='check lan-address';box.type='checkbox';box.dataset.lanInterface=row.id;
    box.checked=!choosing||lanDraft.includes(row.id);
    box.disabled=!!snapshot?.runtime.running||!$('lan').checked||!choosing;
    text.textContent=row.name+' · '+(row.addresses?.length?row.addresses.join(', '):t('当前不可用'));
    box.onchange=()=>{lanDraft=box.checked?[...lanDraft,row.id]:lanDraft.filter(id=>id!==row.id);updateDirty();};
    label.append(box,text);$('lan-addresses').append(label);
  }
  if(!rows.length){const note=document.createElement('p');note.className='hint';note.textContent=t('未发现可用的局域网网卡');$('lan-addresses').append(note);}
  $('lan-scope').disabled=!!snapshot?.runtime.running||!$('lan').checked;
}
$('login-startup').onchange=()=>{if($('login-startup').checked)$('auto-start').checked=true;$('auto-start').disabled=$('login-startup').checked;updateDirty();};
$('lan').onchange=()=>{renderLan();updateDirty();};
$('lan-scope').onchange=()=>{renderLan();updateDirty();};
function renderNetworkAdaptation(value=networkAdaptationState){
  if(value!==undefined)networkAdaptationState=value;
  const box=$('network-adaptation-enabled'),known=typeof networkAdaptationState?.enabled==='boolean';
  box.checked=networkAdaptationState?.enabled===true;
  box.disabled=networkAdaptationBusy||!snapshot||!known||typeof api.networkAdaptation!=='function';
  box.setAttribute('aria-busy',String(networkAdaptationBusy));
  $('network-adaptation-state').textContent=t(networkAdaptationBusy?'正在保存…':networkAdaptationError||(!known?'请更新网关以使用网络变化自动恢复。':networkAdaptationState.enabled?'已启用':'未启用'));
}
$('network-adaptation-enabled').onchange=async event=>{
  event?.stopPropagation();
  if(networkAdaptationBusy)return;
  const enabled=$('network-adaptation-enabled').checked;
  networkAdaptationBusy=true;networkAdaptationRevision++;networkAdaptationError='';renderNetworkAdaptation();
  try{networkAdaptationState=unwrapValidation(await api.networkAdaptation({action:'set',enabled}));}
  catch(error){
    // A timeout may follow a successful save. Read the result; never replay it.
    try{networkAdaptationState=await api.networkAdaptation({action:'status'});}
    catch{networkAdaptationState=undefined;networkAdaptationError='网络恢复设置尚未确认，请刷新状态后重试。';}
    if(networkAdaptationState?.enabled!==enabled)feedback(error.message,true);
  }finally{
    networkAdaptationBusy=false;networkAdaptationRevision++;
    if(snapshot)snapshot.networkAdaptation=networkAdaptationState;
    renderNetworkAdaptation();
  }
};
function input(id,value){$(id).value=value??'';}
function renderGatewayActions(){
  const running=!!snapshot?.runtime.running,starting=startPending||Date.now()<startingUntil;
  for(const id of ['start','clients-start']){const button=$(id);if(button){button.disabled=!snapshot||running||starting||stopPending||cloudflaredBusy;button.setAttribute('aria-busy',String(startPending));}}
  for(const id of ['stop','clients-stop']){const button=$(id);if(button){button.disabled=!running||startPending||stopPending;button.setAttribute('aria-busy',String(stopPending));}}
  if(snapshot)window.GatewayLayout?.update(snapshot,{starting,stopping:stopPending});
}
function render(value,watchRevision=watchPanel.revision){
  $('login-startup-setting').hidden=$('login-startup-hint').hidden=!value.loginStartup?.supported;
  $('login-startup-hint').textContent=t(value.loginStartup?.requiresApproval?'请在系统设置的登录项中允许此应用。':'开启后，登录电脑时自动启动网关。');
  if(snapshot&&(snapshot.runtime.instanceId!==value.runtime.instanceId||snapshot.dataDir!==value.dataDir)){resetPairing();connectionSetupState.clear();}
  if(snapshot&&snapshot.dataDir!==value.dataDir){hideWebhookUrl();webhookSavedFields=null;webhookCreating=false;webhookSelectedId='legacy';resetSecretFields();harnessPanel?.clear();if(activeTab==='accounts')harnessPanel?.refresh();watchPanel.clear();watchRevision=watchPanel.revision;devicesState=null;devicesDirty=false;$('device-list').replaceChildren();$('auto-blocks').replaceChildren();}
  snapshot=value;const running=value.runtime.running;
  if(!networkAdaptationBusy){if(typeof value.networkAdaptation?.enabled==='boolean')networkAdaptationError='';renderNetworkAdaptation(value.networkAdaptation??null);}
  if(running||value.runtime.portOccupied)startingUntil=0;
  const starting=Date.now()<startingUntil;
  if(feedbackKind==='gateway'){
    if(running)feedback(t('网关已启动，正在运行。'),false,'gateway');
    else if(value.runtime.portOccupied)feedback(t('端口已被其他网关占用，请检查运行配置。'),true,'gateway');
    else if(starting)feedback(t('正在启动网关'),false,'gateway');
    else if(startingUntil)feedback(t('网关启动超时，请查看运行日志。'),true,'gateway');
    else feedback(t('网关已停止'),false,'gateway');
  }
  $('status').textContent=running?t('运行中'):starting?t('启动中'):value.runtime.portOccupied?t('端口已占用'):t('未启动');
  $('status').classList.toggle('running',running);$('sidebar-status').textContent=$('status').textContent;
  renderGatewayActions();
  $('login-summary').textContent=value.auth.mode==='none'?t('免密访问'):t('账号：')+value.auth.username;
  $('credentials').disabled=!value.credentialsAvailable;
  const notificationsEnabled=value.notifications.enabled||value.notifications.barkEnabled||value.notifications.pushplusEnabled||value.notifications.webhookEndpoints?.some(row=>row.webhookEnabled)||value.notifications.webhookEnabled;
  const channelKeys={'bark-enabled':'barkEnabled','ntfy-enabled':'enabled','pushplus-enabled':'pushplusEnabled','webhook-enabled':'webhookEnabled','mobile-enabled':'mobileEnabled'};
  for(const status of document.querySelectorAll('[data-notification-status]'))status.textContent=t((status.dataset.notificationStatus==='webhook-enabled'?value.notifications.webhookEndpoints?.some(row=>row.webhookEnabled):value.notifications[channelKeys[status.dataset.notificationStatus]])?'已启用':'未启用');
  const addressReady=value.notifications.addressEnabled&&notificationsEnabled;
  const addressHint=!value.notifications.addressEnabled?t('入口通知未开启，网关启动或地址变化时不会自动通知你。'):!notificationsEnabled?t('请启用至少一个通知通道，并发送测试通知。'):t('入口通知已开启，每次启动网关都会发送地址，入口变化后补发更新。请先确认手机能收到测试通知。');
  const addressRecords=Object.values(value.addressNotificationStatus?.deliveries||{});
  const addressFailed=addressRecords.some(record=>record.error);
  const securityFailed=Object.values(value.securityNotificationStatus||{}).some(row=>Object.values(row).some(delivery=>delivery?.error));
  $('security-notification-state').textContent=securityFailed?t('封禁通知发送失败，将自动重试。'):!notificationsEnabled?t('请启用至少一个通知通道，并发送测试通知。'):'';
  $('address-notification-state').textContent=addressHint+(addressReady&&addressFailed?' '+t('部分入口通知发送失败，将自动重试。'):'');
  $('update-address-state').hidden=!value.preferences.tunnel;
  $('update-address-state').textContent=addressHint;
  $('notification-summary').textContent=notificationsEnabled?t('已开启 · ')+value.watches.length+t(' 个关注聊天'):t('未开启');
  $('notification-state').textContent=value.runtime.running&&!value.runtime.supportsNotifications?t('当前网关版本较旧，重启后启用通知能力。'):t(value.notificationStatus.error)||t('手机关闭网页后，已关注聊天仍会继续提醒。');
  for(const [channel,id] of [['ntfy','notification-detail'],['bark','bark-detail'],['pushplus','pushplus-detail'],['webhook','webhook-detail']]){
    const status=value.notificationStatus[channel]||(channel==='ntfy'?value.notificationStatus:{});
    $(id).textContent=t(status.error)||(status.lastSent?t('最近一次发送：')+new Date(status.lastSent*1000).toLocaleString(BridgeI18n.locale()):t('尚无发送记录'));
  }
  $('notification-readiness').textContent=!notificationsEnabled?t('尚未开启手机通知：填写并保存后，先发送测试通知。'):!running?t('网关尚未启动：可以先测试通知接收，聊天提醒需要启动网关。'):!value.runtime.supportsNotifications?t('当前网关版本不支持聊天提醒，请在首页停止后重新启动网关，再刷新手机网页。'):t('网关已就绪：在手机聊天中点击“提醒”，勾选“开启聊天提醒”并保存。');
  $('data-dir').textContent=value.dataDir;
  $('addresses').replaceChildren();
  for(const url of value.urls){const card=document.createElement('div');card.className='address';const text=document.createElement('div'),label=document.createElement('small'),address=document.createElement('strong');const fixed=(value.preferences.connections||[]).some(c=>c.enabled&&url===c.publicUrl+'/');label.textContent=(fixed?t('固定 HTTPS · 请检测入口'):url.startsWith('https:')?t('临时外网 HTTPS'):url.includes('127.0.0.1')?t('此电脑'):t('局域网'))+(running?'':t(' · 网关未启动'));address.textContent=url;text.append(label,address);card.append(text);for(const [name,action] of [[t('复制'),()=>api.copy(url)],[t('打开'),()=>api.open(url)]]){const button=document.createElement('button');button.textContent=name;button.onclick=()=>action().catch(e=>feedback(e.message,true));card.append(button);}if(!['127.0.0.1','localhost','[::1]'].includes(new URL(url).hostname))appendPairing(card,url,running);$('addresses').append(card);}
  watchPanel.render(value,watchRevision);
  if(!dirty){
    const p=value.preferences,n=value.notifications;
    for(const [id,key] of [['port','port'],['cloudflared','cloudflared'],['codex-home','codexHome'],['ipc-path','ipcPath'],['codex-bin','codexBin']])input(id,p[key]);
    $('login-startup').checked=!!(value.loginStartup?.enabled||value.loginStartup?.requiresApproval);$('auto-start').checked=p.autoStart;$('auto-start').disabled=!!(value.loginStartup?.enabled||value.loginStartup?.requiresApproval);$('lan').checked=p.lan;
    $('local-access').checked=p.localAccess!==false;input('lan-scope',p.lanInterfaces==null?'all':'selected');
    lanDraft=p.lanInterfaces==null?(value.networkInterfaces||[]).map(row=>row.id):[...(p.lanInterfaces||[])];savedLan=JSON.stringify(lanDraft);
    if(!document.activeElement?.closest('#connections')||JSON.stringify(p.connections||[])!==savedConnections){const next=JSON.parse(JSON.stringify(p.connections||[]));for(const row of connectionDraft)if(JSON.stringify(row)!==JSON.stringify(next.find(c=>c.id===row.id)))connectionSetupState.delete(row.id);connectionDraft=next;savedConnections=JSON.stringify(connectionDraft);renderConnections();}
    const fixed=connectionDraft.filter(c=>c.enabled).map(c=>c.publicUrl);
    input('origins',value.origins.filter(o=>!fixed.includes(o)).join('\n'));input('auth-mode',value.auth.mode);input('session-hours',value.auth.sessionHours??12);input('username',value.auth.username);$('revoke-bound-devices').checked=true;$('revoke-bound-devices-option').hidden=!$('password').value;
    $('mobile-enabled').checked=!!n.mobileEnabled;$('mobile-app-links').checked=!!n.mobileAppLinks;
    $('ntfy-enabled').checked=n.enabled;input('ntfy-server',n.server);input('ntfy-topic',n.topic);input('click-base',n.clickBase);$('include-title').checked=n.includeTitle;
    $('bark-enabled').checked=!!n.barkEnabled;input('bark-server',n.barkServer||'https://api.day.app');
    $('pushplus-enabled').checked=!!n.pushplusEnabled;
    $('security-enabled').checked=n.securityEnabled!==false;$('address-enabled').checked=!!n.addressEnabled;input('address-name',n.addressName||'');
    savedFields=fieldValues();
  }
  $('ntfy-token').placeholder=value.notifications.hasToken?t('已保存；留空保留，服务地址变化时清除'):t('如服务需要认证，在这里填写');
  $('bark-key').placeholder=value.notifications.hasBarkKey?t('已保存；留空保留，服务地址变化时清除'):t('填写 Bark App 中的 Device Key');
  $('pushplus-token').placeholder=value.notifications.hasPushplusToken?t('已保存，留空保留'):t('填写 PushPlus Token');
  if(!webhookCreating){if(!webhookRows().some(row=>row.webhookId===webhookSelectedId)&&(!webhookSavedFields||!webhookDirty()))webhookSelectedId=webhookRows()[0]?.webhookId||'legacy';const row=webhookRow();if(!webhookSavedFields||(JSON.stringify(row)!==webhookRenderedRowSignature&&!webhookDirty()))renderWebhookEditor(row);}
  renderWebhookCopy(webhookCreating?{}:webhookRow());
  renderWebhookList();
  updateDirty();
  $('network-lock').hidden=!running;
  for(const id of ['lan','local-access','port','connection-kind','add-connection','origins','cloudflared','codex-home','ipc-path','codex-bin'])$(id).disabled=running;
  document.querySelectorAll('[data-connection-field],[data-remove-connection]').forEach(node=>node.disabled=running);
  for(const node of document.querySelectorAll('[data-connection-status]')){
    const row=connectionDraft.find(c=>c.id===node.dataset.connectionStatus),mode=row?.accessMode;
    node.textContent=!row?.enabled?t('已配置但未启用'):t([connectionSetupState.get(node.dataset.connectionStatus),value.externalStatus?.[node.dataset.connectionStatus]?.message].filter(Boolean).map(t).join(' ')||(!running?t(mode==='tailscale'?'先保存连接配置，然后检测 Tailscale。':mode==='external'?'保存并启动网关后可检测入口。':'网关未启动。可先保存配置并导出部署包。'):t('网关正在运行；固定入口是否可用，请点击检测。')));
    const summary=node.closest('.connection-card')?.querySelector('.connection-summary');if(summary&&row)summary.textContent=connectionSummary(row);
  }
  document.querySelectorAll('[data-pick]').forEach(button=>button.disabled=running);
  renderLan();
  renderCloudflared();
  renderUpdate();
  loadSavedSecrets();
}
let renderedSnapshot='',renderedDirty=false,renderedRevision=-1;
async function refresh(force=false){if(loading||(document.hidden&&force!==true))return;loading=true;try{const revision=watchPanel.revision,networkRevision=networkAdaptationRevision,value=await api.snapshot();if(networkRevision!==networkAdaptationRevision)value.networkAdaptation=networkAdaptationState;const signature=JSON.stringify(value);if(signature!==renderedSnapshot||renderedDirty!==dirty||renderedRevision!==revision||startingUntil){render(value,revision);renderedSnapshot=signature;renderedDirty=dirty;renderedRevision=revision;}}catch(e){feedback(e.message,true);}finally{loading=false;}}
function collect(){const rows=snapshot?.networkInterfaces||[],selected=rows.filter(row=>lanDraft.includes(row.id));return {...(snapshot?.loginStartup?.supported?{loginStartup:$('login-startup').checked}:{}),preferences:{autoStart:$('auto-start').checked,port:Number($('port').value),lan:$('lan').checked,lanInterfaces:$('lan-scope').value==='all'?null:[...lanDraft],lanAddresses:$('lan-scope').value==='all'?null:[...new Set(selected.flatMap(row=>row.addresses||[]))],localAccess:$('local-access').checked,connections:connectionDraft.map(row=>row.accessMode==='external'||row.accessMode==='nas'?{...row,publicUrl:normalizeExternalUrl(row.publicUrl||'')}:row),cloudflared:$('cloudflared').value.trim(),codexHome:$('codex-home').value.trim(),ipcPath:$('ipc-path').value.trim(),codexBin:$('codex-bin').value.trim()},auth:{sessionHours:Number($('session-hours').value),mode:$('auth-mode').value,username:$('username').value.trim(),password:submittedSecret('password'),revokeBoundDevices:$('revoke-bound-devices').checked},origins:$('origins').value.split('\n').map(s=>s.trim()).filter(Boolean),notifications:{mobileEnabled:$('mobile-enabled').checked,mobileAppLinks:$('mobile-app-links').checked,securityEnabled:$('security-enabled').checked,addressEnabled:$('address-enabled').checked,addressName:$('address-name').value.trim(),pushplusEnabled:$('pushplus-enabled').checked,pushplusToken:submittedSecret('pushplus-token'),clearPushplusToken:$('clear-pushplus-token').checked,enabled:$('ntfy-enabled').checked,server:$('ntfy-server').value.trim(),topic:$('ntfy-topic').value.trim(),token:submittedSecret('ntfy-token'),clearToken:$('clear-token').checked,barkEnabled:$('bark-enabled').checked,barkServer:$('bark-server').value.trim(),barkKey:submittedSecret('bark-key'),clearBarkKey:$('clear-bark-key').checked,clickBase:$('click-base').value.trim(),includeTitle:$('include-title').checked}};}
$('settings').onsubmit=async event=>{
  event.preventDefault();if(saving||busyActions.size)return;const invalid=[...$('settings').querySelectorAll('input,textarea,select')].find(node=>!node.closest('#file-transfer-settings')&&!node.closest('#webhook-editor')&&!node.disabled&&!node.validity.valid&&(!node.closest('.connection-card')||connectionDraft.find(row=>row.id===node.closest('.connection-card').dataset.connectionId)?.enabled));if(invalid){focusField(invalid);feedback(invalid.validationMessage,true);return;}saving=true;$('save').disabled=true;const submitted=fieldValues(),submittedConnections=JSON.stringify(connectionDraft),submittedLan=JSON.stringify(lanDraft);
  try{
    const value=unwrapValidation(await api.save(collect()));savedFields={...submitted};savedConnections=submittedConnections;savedLan=submittedLan;
    // Edits made while saving must remain visibly unsaved.
    for(const id of ['password','ntfy-token','bark-key','pushplus-token'])savedSecretValues.set(id,submitted[id]);
    for(const id of ['clear-token','clear-bark-key','clear-pushplus-token']){if($(id).checked===submitted[id])$(id).checked=false;savedFields[id]=false;}
    await saveAllConnectionCredentials();
    updateDirty();render(value);await loadSavedSecrets(true);
    feedback(dirty?t('已保存提交的配置，仍有新修改待保存。'):t('配置已保存。通知设置由新版网关自动读取，登录设置在下次启动生效。'));
  }catch(e){showError(e);}finally{saving=false;updateDirty();}
};
async function startGateway(){if(cloudflaredBusy||startPending||stopPending||snapshot?.runtime.running||Date.now()<startingUntil)return;if(dirty){feedback(t('请先保存配置，再启动网关。'),true);return;}startPending=true;renderGatewayActions();try{if(snapshot?.preferences.tunnel){try{await api.checkCloudflared(snapshot.preferences.cloudflared);}catch(error){tab('network');$('cloudflared-repair').open=true;throw error;}}await saveAllConnectionCredentials(true);const result=unwrapValidation(await api.start());startingUntil=result.started?Date.now()+70000:0;feedback(result.message,false,'gateway');await refresh();}catch(e){startingUntil=0;showError(e);}finally{startPending=false;renderGatewayActions();}}
async function stopGateway(){if(startPending||stopPending||!snapshot?.runtime.running)return;stopPending=true;renderGatewayActions();try{feedback((await api.stop()).message,false,'gateway');startingUntil=0;await refresh();}catch(e){feedback(e.message,true);}finally{stopPending=false;renderGatewayActions();}}
for(const id of ['start','clients-start'])if($(id))$(id).onclick=startGateway;
for(const id of ['stop','clients-stop'])if($(id))$(id).onclick=stopGateway;
for(const [id,channel] of [['test-notification','ntfy'],['test-bark','bark'],['test-pushplus','pushplus'],['test-address','address']])$(id).onclick=async()=>{if(dirty){feedback(t('请先保存通知配置，再发送测试通知。'),true);return;}$(id).disabled=true;try{feedback((await api.testNotification({channel})).message);}catch(e){feedback(e.message,true);}finally{$(id).disabled=false;}};
$('webhook-add').onclick=()=>{if(webhookSaving)return;if(webhookDirty()){feedback(t('请先保存或取消当前修改。'),true);return;}webhookCreating=true;webhookSelectedId='';renderWebhookEditor({webhookProvider:'auto',webhookBodyTemplate:webhookTemplates.generic,webhookTemplates});renderWebhookList();$('webhook-name').focus();};
$('webhook-cancel').onclick=()=>{if(webhookSaving)return;webhookCreating=false;webhookSelectedId=webhookRows().some(row=>row.webhookId===webhookSelectedId)?webhookSelectedId:webhookRows()[0]?.webhookId||'legacy';renderWebhookEditor(webhookRow());renderWebhookList();};
$('webhook-save').onclick=async()=>{
  if(webhookSaving)return;
  let headers;try{headers=webhookHeaders();}catch(error){showError(error);return;}
  webhookSaving=true;
  const submitted=webhookFields(),url=$('webhook-url').value,rawHeaders=$('webhook-headers').value,secret=$('webhook-sign-secret').value,directory=snapshot?.dataDir;
  try{
    const body={action:'upsert',...(webhookCreating?{}:{webhookId:webhookSelectedId}),...submitted,webhookUrl:webhookUrlDraft(),webhookHeaders:headers,webhookSignSecret:secret};
    const result=unwrapValidation(await api.webhookEndpoint(body));
    if(snapshot?.dataDir!==directory)return;
    const edited=JSON.stringify(webhookFields())!==JSON.stringify(submitted)||$('webhook-url').value!==url||$('webhook-headers').value!==rawHeaders||$('webhook-sign-secret').value!==secret;
    webhookSelectedId=result.selectedWebhookId||webhookSelectedId;webhookCreating=false;
    if(edited){
      for(const [id,value] of [['webhook-url',url],['webhook-headers',rawHeaders],['webhook-sign-secret',secret]])if($(id).value===value)$(id).value='';
      for(const [id,key] of [['clear-webhook-url','clearWebhookUrl'],['clear-webhook-headers','clearWebhookHeaders'],['clear-webhook-sign-secret','clearWebhookSignSecret']])if($(id).checked===submitted[key])$(id).checked=false;
      webhookSavedFields={...submitted,clearWebhookUrl:false,clearWebhookHeaders:false,clearWebhookSignSecret:false};
      webhookRenderedRowSignature=JSON.stringify(result.webhookEndpoints?.find(row=>row.webhookId===webhookSelectedId)||{});
    }else webhookSavedFields=null;
    render({...snapshot,notifications:{...snapshot.notifications,...result}});
    feedback(t(edited?'已保存提交的配置，仍有新修改待保存。':'Webhook 配置已保存'));
  }catch(error){showError(error);}finally{webhookSaving=false;}
};
$('test-webhook').onclick=async()=>{if(webhookDirty()){feedback(t('请先保存通知配置，再发送测试通知。'),true);return;}$('test-webhook').disabled=true;try{feedback((await api.testNotification({channel:'webhook',webhookId:webhookSelectedId})).message);}catch(error){showError(error);}finally{$('test-webhook').disabled=false;}};
$('webhook-delete').onclick=async()=>{if(webhookSaving||!window.confirm(t('确定删除此 Webhook 配置？')))return;webhookSaving=true;try{const result=unwrapValidation(await api.webhookEndpoint({action:'delete',webhookId:webhookSelectedId}));webhookSelectedId=result.selectedWebhookId||'legacy';webhookCreating=false;webhookSavedFields=null;render({...snapshot,notifications:{...snapshot.notifications,...result}});feedback(t('Webhook 配置已删除'));}catch(error){showError(error);}finally{webhookSaving=false;}};
$('webhook-provider').addEventListener('change',()=>{$('webhook-provider-hint').textContent=webhookProviderHint(webhookRow());switchWebhookTemplate();});
$('webhook-use-template').onclick=()=>{input('webhook-body-template',selectedWebhookTemplate());updateDirty();};
$('webhook-url-eye').onclick=toggleWebhookUrl;
$('webhook-url').addEventListener('input',()=>{if(revealedWebhookUrl&&$('webhook-url').value!==revealedWebhookUrl)revealedWebhookUrl='';webhookRevealRequest++;});
$('ntfy-help').onclick=()=>api.open('ntfy-help').catch(e=>feedback(e.message,true));
$('bark-help').onclick=()=>api.open('bark-help').catch(e=>feedback(e.message,true));
for(const id of ['project-home'])$(id).onclick=()=>api.open(id).catch(e=>feedback(e.message,true));
for(const id of ['pushplus-home','pushplus-verify','pushplus-limits'])$(id).onclick=()=>api.open(id).catch(e=>feedback(e.message,true));
$('generate-topic').onclick=()=>{input('ntfy-topic','codex-'+crypto.randomUUID().replaceAll('-',''));updateDirty();};
$('credentials').onclick=()=>api.open('credentials').catch(e=>feedback(e.message,true));
$('open-data').onclick=()=>api.open('data').catch(e=>feedback(e.message,true));
$('choose-data').onclick=async()=>{try{if(await api.choose('data')){dirty=false;await refresh();feedback(t('已切换网关目录，原来的网关进程继续运行。'));}}catch(e){feedback(e.message,true);}};
for(const button of document.querySelectorAll('[data-pick]'))button.onclick=async()=>{try{const selected=await api.choose(button.dataset.kind);if(selected){input(button.dataset.pick,selected);updateDirty();}}catch(e){feedback(e.message,true);}};
function quickTunnelMessage(){
  if(snapshot?.quickTunnel?.message&&snapshot.runtime.running)return t(snapshot.quickTunnel.message);
  return snapshot?.cloudflared?.available?t('程序已找到；保存配置并启动网关后建立临时 HTTPS。'):t('未找到 cloudflared，请先完成安装与配置。');
}
function renderCloudflared(){
  const current=$('cloudflared').value.trim(),verified=cloudflaredResult?.path===current?cloudflaredResult:null;
  $('cloudflared-state').textContent=verified?verified.version:current===snapshot?.cloudflared?.path&&snapshot?.cloudflared?.available?t('已找到 Cloudflare 组件，无需额外安装。需要排查时可展开高级设置检测。'):t('未找到 Cloudflare 组件，请展开高级设置检测路径或修复安装。');
  const progress=cloudflaredProgress||snapshot?.cloudflaredInstall;
  $('cloudflared-progress').hidden=!progress?.message;
  $('cloudflared-progress').textContent=progress?.message?t(progress.message)+(progress.total?' '+Math.floor(100*progress.received/progress.total)+'%':''):'';
  $('install-cloudflared').disabled=cloudflaredBusy||!!snapshot?.runtime.running;
  $('check-cloudflared').disabled=cloudflaredBusy;
  $('choose-data').disabled=cloudflaredBusy;
  const enabled=connectionDraft.some(c=>c.enabled&&c.accessMode==='quick');
  $('quick-tunnel-state').hidden=!enabled;$('quick-setup').hidden=!enabled;
  $('quick-tunnel-state').textContent=quickTunnelMessage();
  document.querySelectorAll('[data-quick-status]').forEach(node=>node.textContent=quickTunnelMessage());
  renderGatewayActions();
}
async function setupCloudflared(install){
  if(cloudflaredBusy)return;
  cloudflaredBusy=true;cloudflaredProgress={message:install?'正在获取 Cloudflare 官方版本…':'正在检测 cloudflared…'};renderCloudflared();
  try{
    // Only fill this field. Other unsaved settings, passwords and tokens remain in the form.
    cloudflaredProgress=null;
    const result=install?await api.installCloudflared():await api.checkCloudflared($('cloudflared').value.trim());
    cloudflaredResult=result;input('cloudflared',result.path);updateDirty();
    cloudflaredProgress={message:install?'已安装并验证 cloudflared，请保存配置后启动网关。':'cloudflared 检测通过。路径有变更时请保存配置。'};
    feedback(cloudflaredProgress.message);
  }catch(error){cloudflaredProgress={message:error.message};feedback(error.message,true);}
  finally{cloudflaredBusy=false;renderCloudflared();await refresh();}
}
$('install-cloudflared').onclick=()=>setupCloudflared(true);
$('check-cloudflared').onclick=()=>setupCloudflared(false);
$('cloudflare-help').onclick=()=>api.open('cloudflare-help').catch(error=>feedback(error.message,true));
$('quick-setup').onclick=()=>{tab('network');$('cloudflared-repair').open=true;};
$('cloudflared').oninput=()=>{renderCloudflared();updateDirty();};
async function loadLogs(){try{$('log-output').textContent=(await api.logs()).text||t('暂无运行日志');$('log-output').scrollTop=0;}catch(e){feedback(e.message,true);}}
$('refresh-logs').onclick=loadLogs;
for(const id of ['password','ntfy-token','bark-key','pushplus-token'])secretControl($(id));
api.language().then(applyLanguage).catch(error=>feedback(error.message,true)).then(()=>refresh(true)).then(()=>desktopConnections?.scan()).then(()=>{if(snapshot?.preferences.autoStart&&!snapshot.updateManaged&&!snapshot.runtime.running&&!snapshot.runtime.portOccupied)$('start').click();});
let refreshTimer=setInterval(refresh,3000);
document.addEventListener('visibilitychange',()=>{clearInterval(refreshTimer);refreshTimer=null;if(!document.hidden){refresh();refreshTimer=setInterval(refresh,3000);}});

function applyLanguage(value){
  BridgeI18n.setLanguage(value==='en'?'en':'zh');BridgeI18n.apply();if(typeof accountsPanel!=="undefined")accountsPanel?.render();
  $('language').value=BridgeI18n.language();
  document.title=t('Codex 手机网关');
  if(snapshot){const pending=dirty;dirty=true;render(snapshot);dirty=pending;renderConnections();updateDirty();}
  if(lastFeedback)feedback(...lastFeedback);
  tab(activeTab);
  renderPairing();if(devicesState)renderDevices(devicesState);
}
$('language').value=BridgeI18n.language();
$('language').onchange=async()=>{
  $('language').disabled=true;
  try{applyLanguage(await api.setLanguage($('language').value==='en'?'en':'zh-CN'));}
  catch(error){$('language').value=BridgeI18n.language();feedback(error.message,true);}
  finally{$('language').disabled=false;}
};
BridgeI18n.apply();
$('add-connection').onclick=addConnection;
$('connection-kind').onchange=()=>setupGuide($('connection-guide'),$('connection-kind').value);

const updateMessages={idle:'检查是否有新版本。',checking:'正在检查更新…',current:'当前已是最新可用版本。',available:'有新版本可用。',skipped:'已跳过此版本。手动检查仍可更新。',downloading:'正在下载更新…',preparing:'正在验证并准备更新…',restarting:'即将重启应用…',unsupported:'当前系统或运行方式不支持应用内更新，请从发布页面下载新版。'};
let updateBusy=false;
function renderUpdate(){
  const value=snapshot?.update;if(!value)return;
  const busy=['downloading','preparing','restarting'].includes(value.state)||updateBusy;
  $('update-version').textContent=t('当前版本：')+value.current+(value.version?' → '+value.version:'');
  $('update-state').textContent=value.message?t(value.message):t(value.skipped?'已跳过此版本。手动检查仍可更新。':updateMessages[value.state]||'');
  $('update-badge').hidden=value.state!=='available';
  $('update-banner').hidden=value.state!=='available'||activeTab==='advanced';
  $('update-banner').textContent=t('发现新版本，点击查看：')+(value.version||'');
  $('check-update').disabled=busy||value.state==='checking'||value.state==='unsupported';
  $('skip-update').hidden=value.state!=='available'||value.skipped===true;
  $('skip-update').disabled=busy||value.state==='checking';
  $('install-update').hidden=!value.version||['current','unsupported'].includes(value.state);
  $('install-update').disabled=busy||value.state==='checking';
  $('update-notes').hidden=!value.notes;$('update-notes').textContent=value.notes||'';
  $('update-progress').hidden=value.state!=='downloading';
  $('update-progress').value=value.total?100*value.received/value.total:0;
  let result=snapshot.updateResult;
  // A successful record describes the app that was installed at that time.
  // Manual app replacement preserves this data file, so an old success cannot
  // claim that the newly deployed binary is still that older release.
  if(result?.state==='updated'&&result.version!==value.current)result=null;
  $('update-result').hidden=!result;
  $('update-result').textContent=result?(result.state==='updated'?t('已更新到 ')+result.version:result.recovered?t('上次更新未完成，已恢复原版本。')+' '+t(result.message):t('更新恢复未完成，请查看数据目录中的 desktop-update.log。')):'';
  if(busy){
    $('settings').inert=true;
    for(const id of ['start','stop','choose-data','install-cloudflared'])$(id).disabled=true;
  }else $('settings').inert=false;
}
$('check-update').onclick=async()=>{
  if(updateBusy)return;
  updateBusy=true;renderUpdate();
  try{await api.checkUpdate();await refresh();}
  catch(error){feedback(error.message,true);}
  finally{updateBusy=false;renderUpdate();}
};
$('skip-update').onclick=async()=>{
  if(updateBusy)return;
  updateBusy=true;renderUpdate();
  try{await api.skipUpdate();await refresh();}
  catch(error){feedback(error.message,true);}
  finally{updateBusy=false;renderUpdate();}
};
$('install-update').onclick=async()=>{
  if(updateBusy)return;
  if(dirty||devicesDirty){feedback('请先保存配置，再更新应用。',true);return;}
  if(cloudflaredBusy||startPending||$('save').disabled||busyActions.size){feedback('请等待当前操作完成后再更新。',true);return;}
  updateBusy=true;renderUpdate();
  try{await api.installUpdate();await refresh();}catch(error){feedback(error.message,true);}
  finally{updateBusy=false;renderUpdate();}
};
$('update-releases').onclick=()=>api.open('releases').catch(error=>feedback(error.message,true));

let devicesState=null,devicesDirty=false,devicesBusy=false;
function devicePolicy(){
  const lines=id=>$(id).value.split('\n').map(value=>value.trim()).filter(Boolean);
  return {allowlistEnabled:$('allowlist-enabled').checked,allowlist:lines('ip-allowlist'),blocklist:lines('ip-blocklist'),trustedProxies:lines('trusted-proxies')};
}
function renderDevices(value){
  devicesState=value;
  if(!devicesDirty){
    $('allowlist-enabled').checked=value.policy.allowlistEnabled;
    for(const [id,key] of [['ip-allowlist','allowlist'],['ip-blocklist','blocklist'],['trusted-proxies','trustedProxies']])input(id,value.policy[key].join('\n'));
  }
  $('auto-blocks').replaceChildren();
  if(!value.autoBlocks?.length)$('auto-blocks').textContent=t('暂无自动封禁的 IP。');
  for(const block of value.autoBlocks||[]){
    const row=document.createElement('div');row.className='device-row';
    const text=document.createElement('p');text.textContent=block.ip+' · '+new Date(block.blockedAt*1000).toLocaleString(BridgeI18n.locale());
    const button=document.createElement('button');button.type='button';button.textContent=t('解除封禁并重置次数');button.disabled=devicesBusy;
    button.onclick=()=>{if(devicesDirty){feedback(t('请先保存 IP 规则。'),true);return;}return changeDevices({action:'unblock',ip:block.ip});};row.append(text,button);$('auto-blocks').append(row);
  }
  $('device-list').replaceChildren();
  if(!value.sessions.length)$('device-list').textContent=t('暂无有效登录记录。');
  const date=value=>new Date(value*1000).toLocaleString(BridgeI18n.locale());
  for(const session of value.sessions){
    const row=document.createElement('div');row.className='device-row';
    const address=document.createElement('strong');address.textContent=session.ip;
    const browser=document.createElement('p');browser.textContent=(session.trustedDevice?t('已绑定手机')+' · ':'')+(session.userAgent||t('未知浏览器'));
    const detail=document.createElement('p');detail.className='hint';
    detail.textContent=t(session.source==='proxy'?'代理 IP（未提供客户端地址）':session.source==='forwarded'?'经可信代理转发':'直连地址')+' · '+t('登录时间：')+date(session.created)+' · '+t('最近访问：')+date(session.lastSeen)+' · '+t('有效期至：')+(session.expires?date(session.expires):t('不自动过期'));
    const actions=document.createElement('div');actions.className='actions';
    for(const [label,action] of [['撤销登录','revoke'],['封禁此 IP','block'],['加入白名单','allow']]){
      const button=document.createElement('button');button.type='button';button.textContent=t(action==='revoke'&&session.trustedDevice?'解除绑定':label);button.disabled=devicesBusy;
      button.onclick=()=>{
        if(action==='allow'){
          input('ip-allowlist',[...new Set([...devicePolicy().allowlist,session.ip])].join('\n'));
          devicesDirty=true;$('device-feedback').textContent=t('白名单有未保存的修改。');return;
        }
        if(devicesDirty){feedback(t('请先保存 IP 规则。'),true);return;}
        return changeDevices({action,id:session.id});
      };
      actions.append(button);
    }
    row.append(address,browser,detail,actions);$('device-list').append(row);
  }
}
async function loadDevices(){
  if(devicesBusy)return;
  devicesBusy=true;$('refresh-devices').disabled=true;$('device-policy').inert=true;
  try{renderDevices(await api.devices({action:'list'}));}
  catch(error){feedback(error.message,true);}
  finally{devicesBusy=false;$('refresh-devices').disabled=false;$('device-policy').inert=false;$('save-device-policy').disabled=!devicesState;if(devicesState)renderDevices(devicesState);}
}
async function changeDevices(payload){
  if(devicesBusy)return;
  devicesBusy=true;busyActions.add('devices');$('device-policy').inert=true;$('refresh-devices').disabled=true;
  if(devicesState)renderDevices(devicesState);
  try{
    const value=await api.devices(payload);
    devicesDirty=false;renderDevices(value);
    $('device-feedback').textContent=t(payload.action==='save'?'IP 规则已保存并生效。':payload.action==='block'?'IP 已封禁，关联登录已撤销。':payload.action==='unblock'?'IP 已解封，剩余尝试次数已重置。':'登录已撤销。');
  }catch(error){feedback(error.message,true);}
  finally{devicesBusy=false;busyActions.delete('devices');$('device-policy').inert=false;$('refresh-devices').disabled=false;if(devicesState)renderDevices(devicesState);}
}
$('refresh-devices').onclick=loadDevices;
$('device-policy').oninput=$('device-policy').onchange=()=>{devicesDirty=true;$('device-feedback').textContent=t('IP 规则有未保存的修改。');};
$('device-policy').onsubmit=event=>{event.preventDefault();return changeDevices({action:'save',policy:devicePolicy()});};

const claudeCliAccessPanel=typeof CliAccessPanel==='undefined'?null:new CliAccessPanel({root:$('cli-access-claude-content'),provider:'claude',read:()=>api.cliAccess({action:'list'}),request:value=>api.cliAccess(value)});
document.addEventListener('bridge-client-config',event=>{claudeCliAccessPanel?.clear();if(event.detail==='claude'&&!$('cli-access-claude-content').hidden)claudeCliAccessPanel?.refresh();});
let codexAccountEntry='desktop';
document.addEventListener('bridge-client-config',event=>{const next=event.detail?.startsWith('codex:')?event.detail:'desktop';if(codexAccountEntry!==next){codexAccountEntry=next;accountsPanel?.clear();accountsPanel?.resetForm();}if(event.detail==='codex'||next!=='desktop')accountsPanel?.refresh();});
const accountsPanel=typeof AccountsPanel==='undefined'?null:new AccountsPanel({root:$('accounts-content'),desktop:true,read:force=>api.accounts({entry:codexAccountEntry,action:'list',resetForecast:accountsPanel?.visible()&&accountsPanel?.value?.resetDynamicEnabled?(force?'refresh':true):false}),request:value=>api.accounts({...value,entry:codexAccountEntry}),onUpdate:value=>{if(codexAccountEntry==='desktop')desktopConnections?.renderAccount(value);}});
if(accountsPanel){
  const visible=accountsPanel.visible.bind(accountsPanel);
  accountsPanel.visible=()=>visible()||(!document.hidden&&!$('clients-page').hidden&&!$('clients-page').classList.contains('configuration-open'));
}

let wslAccountsLoading;
window.loadWslAccounts=()=>{
  if(api.platform!=='win32')return Promise.reject(Error(t('当前电脑不支持 WSL 应用')));
  if(!wslAccountsLoading)wslAccountsLoading=new Promise((resolve,reject)=>{
    const script=document.createElement('script');script.src='../web/features/accounts/wsl-accounts.js';
    script.onload=()=>resolve();script.onerror=()=>{script.remove();wslAccountsLoading=null;reject(Error(t('WSL 账号管理面板尚未加载，请重新打开网关窗口')));};document.head.append(script);
  });
  return wslAccountsLoading;
};

// WSL access has its own scope; local panels keep their existing data and callbacks.
const wslAccountScopes=[];
function createWslAccountScopes(){
for(const provider of ['codex','claude']){
  const localRoot=provider==='codex'?$('accounts-content'):desktopConnections?.accountPanels?.claude?.root;
  if(!localRoot)continue;
  const wrapper=document.createElement('div'),label=document.createElement('label'),select=document.createElement('select'),caption=document.createElement('span'),root=document.createElement('div');
  caption.dataset.i18n='运行环境';caption.textContent=t('运行环境');label.append(caption,select);wrapper.append(label,root);localRoot.before(wrapper);
  const request=value=>api.cliAccess({action:'wslAccounts',provider,entry:select.value,operation:value.action,...Object.fromEntries(Object.entries(value).filter(([key])=>key!=='action'))});
  const panel=new WslAccountsPanel({root,provider,desktop:true,read:entry=>api.cliAccess({action:'wslAccounts',provider,entry,operation:'list'}),request});
  const localPanel=provider==='codex'?accountsPanel:desktopConnections?.accountPanels?.claude;
  if(localPanel){const visible=localPanel.visible.bind(localPanel);localPanel.visible=()=>!localRoot.hidden&&visible();}
  const localCli=provider==='codex'?null:$('cli-access-claude-content');
  let selectedWsl=false;
  const choose=()=>{const local=select.value==='local',returning=local&&selectedWsl;selectedWsl=!local;localRoot.hidden=!local;if(localCli)localCli.hidden=!local;if(local){panel.clear();if(returning){localPanel?.refresh();if(localCli&&!localCli.hidden)(provider==='claude'?claudeCliAccessPanel:null)?.refresh();}}else {(provider==='claude'?claudeCliAccessPanel:null)?.clear();if(localPanel){if(localPanel.clear)localPanel.clear();else{localPanel.generation++;localPanel.modelsRevision++;localPanel.loading=false;localPanel.busy=false;localPanel.closeEditor?.();clearTimeout(localPanel.timer);}}panel.select(select.value);}};
  let revision=0;
  const load=async()=>{const current=++revision;try{const value=await api.cliAccess({action:'wslAccounts',provider,operation:'environments'});if(current!==revision)return;const previous=select.value||'local';select.replaceChildren(new Option(t('此电脑'),'local'),...(value.environments||[]).map(row=>new Option([row.label||row.distribution,row.user,row.configDir].filter(Boolean).join(' · '),row.id)));select.value=[...select.options].some(row=>row.value===previous)?previous:'local';label.hidden=!(value.environments||[]).length;choose();}catch(error){if(current===revision){label.hidden=true;select.replaceChildren(new Option(t('此电脑'),'local'));choose();}}};
  select.onchange=choose;select.append(new Option(t('此电脑'),'local'));root.hidden=true;label.hidden=true;wslAccountScopes.push({panel,load});
}
}
if(api.platform==='win32')window.loadWslAccounts().then(()=>{createWslAccountScopes();if(activeTab==='accounts')for(const scope of wslAccountScopes)scope.load();}).catch(error=>feedback(error.message,true));
document.addEventListener('bridge-client-config',event=>{for(const scope of wslAccountScopes){scope.panel.clear();scope.load();}});
