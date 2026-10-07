'use strict';
const $=id=>document.getElementById(id),api=window.bridgeDesktop,t=BridgeI18n.t;
let connectionDraft=[],savedConnections='[]',lanDraft=[],savedLan='[]';
let snapshot,dirty=false,loading=false,startingUntil=0,activeTab='overview',savedFields={},feedbackKind='',lastFeedback;
const busyActions=new Set();
let saving=false,startPending=false,cloudflaredBusy=false,cloudflaredResult=null,cloudflaredProgress=null;
const titles={overview:t('连接与状态'),network:t('网络与登录'),devices:t('登录设备'),notifications:t('手机通知'),advanced:t('运行配置'),accounts:t('账号与接入'),account:t('账户与额度'),updates:t('应用更新'),logs:t('运行日志')};
const accountPanel=new AccountPanel({root:$('account-content'),button:$('account-button'),read:refresh=>api.account({action:'read',refresh}),consume:value=>api.account({action:'consume',...value}),onHidden:()=>{$('account-details').open=false;},visible:()=>activeTab==='accounts'&&!document.hidden&&$('account-details').open});
const watchPanel=new WatchPanel({root:$('watches'),change:value=>api.notificationWatches(value)});
function fields(){return [...$('settings').querySelectorAll('input,textarea,select')].filter(node=>!node.closest('#shared-relay')&&!node.closest('#watches')&&!node.closest('#connections')&&!node.closest('#lan-addresses')&&node.id!=='connection-kind');}
function fieldValues(){return Object.fromEntries(fields().map(node=>[node.id,node.type==='checkbox'?node.checked:node.value]));}
function updateDirty(){
  const changed=new Set();
  for(const node of fields())if((node.type==='checkbox'?node.checked:node.value)!==savedFields[node.id])changed.add(node.closest('[data-panel]').dataset.panel);
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
  $('save-bar').hidden=!dirty&&!['network','notifications','advanced'].includes(activeTab);
  document.querySelectorAll('[data-connection-action]').forEach(button=>button.disabled=dirty||busyActions.size>0||(!['inspect','diagnostics','credentials','clearCredentials'].includes(button.dataset.connectionAction)&&!connectionDraft.find(c=>c.id===button.dataset.connection)?.enabled));
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

function tab(name){if(name==='account')name='accounts';activeTab=name;if(name==='accounts'){accountsPanel?.refresh();}document.querySelectorAll('[data-panel]').forEach(node=>node.hidden=node.dataset.panel!==name);document.querySelectorAll('[data-tab]').forEach(node=>node.classList.toggle('active',node.dataset.tab===name));$('page-title').textContent=t(titles[name]);if(snapshot)updateDirty();if(name==='logs')loadLogs();if(name==='devices')loadDevices();if(snapshot)renderUpdate();}
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>tab(button.dataset.tab));
document.querySelectorAll('[data-jump]').forEach(button=>button.onclick=()=>tab(button.dataset.jump));
$('settings').oninput=$('settings').onchange=()=>{if(snapshot){updateDirty();}};
function renderLan(){
  const rows=[...(snapshot?.networkInterfaces||[])];
  for(const address of lanDraft)if(!rows.some(row=>row.address===address))rows.push({address,name:t('当前不可用')});
  $('lan-addresses').replaceChildren();
  const selected=$('lan-scope').value==='selected';
  for(const row of rows){
    const label=document.createElement('label'),box=document.createElement('input'),text=document.createElement('span');
    label.className='check lan-address';box.type='checkbox';box.dataset.lanAddress=row.address;
    box.checked=!selected||lanDraft.includes(row.address);
    box.disabled=!!snapshot?.runtime.running||!$('lan').checked||!selected;
    text.textContent=row.name+' · '+row.address;
    box.onchange=()=>{lanDraft=box.checked?[...lanDraft,row.address]:lanDraft.filter(address=>address!==row.address);updateDirty();};
    label.append(box,text);$('lan-addresses').append(label);
  }
  if(!rows.length){const note=document.createElement('p');note.className='hint';note.textContent=t('未发现可用的局域网 IPv4 地址');$('lan-addresses').append(note);}
  $('lan-scope').disabled=!!snapshot?.runtime.running||!$('lan').checked;
}
$('lan').onchange=()=>{renderLan();updateDirty();};
$('lan-scope').onchange=()=>{renderLan();updateDirty();};
function input(id,value){$(id).value=value??'';}
function render(value,watchRevision=watchPanel.revision){
  if(snapshot&&(snapshot.runtime.instanceId!==value.runtime.instanceId||snapshot.dataDir!==value.dataDir))resetPairing();
  if(snapshot&&snapshot.dataDir!==value.dataDir){resetSecretFields();watchPanel.clear();watchRevision=watchPanel.revision;devicesState=null;devicesDirty=false;$('device-list').replaceChildren();$('auto-blocks').replaceChildren();}
  if(snapshot&&(snapshot.runtime.instanceId!==value.runtime.instanceId||snapshot.dataDir!==value.dataDir))accountPanel.clear();
  snapshot=value;const running=value.runtime.running;
  if(!running)accountPanel.clear();
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
  $('start').disabled=running||starting||cloudflaredBusy||startPending;$('stop').disabled=!running;
  $('login-summary').textContent=value.auth.mode==='none'?t('免密访问'):t('账号：')+value.auth.username;
  $('credentials').disabled=!value.credentialsAvailable;
  const notificationsEnabled=value.notifications.enabled||value.notifications.barkEnabled||value.notifications.pushplusEnabled;
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
  for(const [channel,id] of [['ntfy','notification-detail'],['bark','bark-detail'],['pushplus','pushplus-detail']]){
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
    $('auto-start').checked=p.autoStart;$('lan').checked=p.lan;
    $('local-access').checked=p.localAccess!==false;input('lan-scope',p.lanAddresses==null?'all':'selected');
    lanDraft=p.lanAddresses==null?(value.networkInterfaces||[]).map(row=>row.address):[...p.lanAddresses];savedLan=JSON.stringify(lanDraft);
    if(!document.activeElement?.closest('#connections')||JSON.stringify(p.connections||[])!==savedConnections){connectionDraft=JSON.parse(JSON.stringify(p.connections||[]));savedConnections=JSON.stringify(connectionDraft);renderConnections();}
    const fixed=connectionDraft.filter(c=>c.enabled).map(c=>c.publicUrl);
    input('origins',value.origins.filter(o=>!fixed.includes(o)).join('\n'));input('auth-mode',value.auth.mode);input('session-hours',value.auth.sessionHours??12);input('username',value.auth.username);
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
  updateDirty();
  $('network-lock').hidden=!running;
  for(const id of ['lan','local-access','port','connection-kind','add-connection','origins','cloudflared','codex-home','ipc-path','codex-bin'])$(id).disabled=running;
  document.querySelectorAll('[data-connection-field],[data-remove-connection]').forEach(node=>node.disabled=running);
  for(const node of document.querySelectorAll('[data-connection-status]'))node.textContent=t([connectionSetupState.get(node.dataset.connectionStatus),value.externalStatus?.[node.dataset.connectionStatus]?.message].filter(Boolean).map(t).join(' ')||(!running?t('网关未启动。可先保存配置并导出部署包。'):t('网关正在运行；固定入口是否可用，请点击检测。')));
  document.querySelectorAll('[data-pick]').forEach(button=>button.disabled=running);
  renderLan();
  renderCloudflared();
  renderUpdate();
  loadSavedSecrets();
}
let renderedSnapshot='',renderedDirty=false,renderedRevision=-1;
async function refresh(){if(loading||document.hidden)return;loading=true;try{const revision=watchPanel.revision,value=await api.snapshot(),signature=JSON.stringify(value);if(signature!==renderedSnapshot||renderedDirty!==dirty||renderedRevision!==revision||startingUntil){render(value,revision);renderedSnapshot=signature;renderedDirty=dirty;renderedRevision=revision;}}catch(e){feedback(e.message,true);}finally{loading=false;}}
function collect(){return {preferences:{autoStart:$('auto-start').checked,port:Number($('port').value),lan:$('lan').checked,lanAddresses:$('lan-scope').value==='all'?null:[...lanDraft],localAccess:$('local-access').checked,connections:connectionDraft,cloudflared:$('cloudflared').value.trim(),codexHome:$('codex-home').value.trim(),ipcPath:$('ipc-path').value.trim(),codexBin:$('codex-bin').value.trim()},auth:{sessionHours:Number($('session-hours').value),mode:$('auth-mode').value,username:$('username').value.trim(),password:submittedSecret('password')},origins:$('origins').value.split('\n').map(s=>s.trim()).filter(Boolean),notifications:{mobileEnabled:$('mobile-enabled').checked,mobileAppLinks:$('mobile-app-links').checked,securityEnabled:$('security-enabled').checked,addressEnabled:$('address-enabled').checked,addressName:$('address-name').value.trim(),pushplusEnabled:$('pushplus-enabled').checked,pushplusToken:submittedSecret('pushplus-token'),clearPushplusToken:$('clear-pushplus-token').checked,enabled:$('ntfy-enabled').checked,server:$('ntfy-server').value.trim(),topic:$('ntfy-topic').value.trim(),token:submittedSecret('ntfy-token'),clearToken:$('clear-token').checked,barkEnabled:$('bark-enabled').checked,barkServer:$('bark-server').value.trim(),barkKey:submittedSecret('bark-key'),clearBarkKey:$('clear-bark-key').checked,clickBase:$('click-base').value.trim(),includeTitle:$('include-title').checked}};}
$('settings').onsubmit=async event=>{
  event.preventDefault();if(saving||busyActions.size)return;const invalid=[...$('settings').querySelectorAll('input,textarea,select')].find(node=>!node.disabled&&!node.validity.valid&&(!node.closest('.connection-card')||connectionDraft.find(row=>row.id===node.closest('.connection-card').dataset.connectionId)?.enabled));if(invalid){focusField(invalid);feedback(invalid.validationMessage,true);return;}saving=true;$('save').disabled=true;const submitted=fieldValues(),submittedConnections=JSON.stringify(connectionDraft),submittedLan=JSON.stringify(lanDraft);
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
$('start').onclick=async()=>{if(cloudflaredBusy||startPending)return;if(dirty){feedback(t('请先保存配置，再启动网关。'),true);return;}$('start').disabled=true;startPending=true;try{if(snapshot?.preferences.tunnel){try{await api.checkCloudflared(snapshot.preferences.cloudflared);}catch(error){tab('advanced');$('cloudflared-repair').open=true;throw error;}}await saveAllConnectionCredentials(true);const result=unwrapValidation(await api.start());startingUntil=result.started?Date.now()+70000:0;feedback(result.message,false,'gateway');await refresh();}catch(e){startingUntil=0;showError(e);$('start').disabled=false;}finally{startPending=false;}};
$('stop').onclick=async()=>{$('stop').disabled=true;try{feedback((await api.stop()).message,false,'gateway');startingUntil=0;await refresh();}catch(e){feedback(e.message,true);$('stop').disabled=false;}};
for(const [id,channel] of [['test-notification','ntfy'],['test-bark','bark'],['test-pushplus','pushplus'],['test-address','address']])$(id).onclick=async()=>{if(dirty){feedback(t('请先保存通知配置，再发送测试通知。'),true);return;}$(id).disabled=true;try{feedback((await api.testNotification({channel})).message);}catch(e){feedback(e.message,true);}finally{$(id).disabled=false;}};
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
$('quick-setup').onclick=()=>{tab('advanced');$('cloudflared-repair').open=true;};
$('cloudflared').oninput=()=>{renderCloudflared();updateDirty();};
async function loadLogs(){try{$('log-output').textContent=(await api.logs()).text||t('暂无运行日志');$('log-output').scrollTop=0;}catch(e){feedback(e.message,true);}}
$('refresh-logs').onclick=loadLogs;
for(const id of ['password','ntfy-token','bark-key','pushplus-token'])secretControl($(id));
api.language().then(applyLanguage).catch(error=>feedback(error.message,true)).then(()=>refresh()).then(()=>{if(snapshot?.preferences.autoStart&&!snapshot.updateManaged&&!snapshot.runtime.running&&!snapshot.runtime.portOccupied)$('start').click();});
let refreshTimer=setInterval(refresh,3000);
document.addEventListener('visibilitychange',()=>{clearInterval(refreshTimer);refreshTimer=null;if(!document.hidden){refresh();refreshTimer=setInterval(refresh,3000);}});

function applyLanguage(value){
  BridgeI18n.setLanguage(value==='en'?'en':'zh');BridgeI18n.apply();accountPanel.render();if(typeof accountsPanel!=="undefined")accountsPanel?.render();
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

const updateMessages={idle:'检查是否有新版本。',checking:'正在检查更新…',current:'当前已是最新可用版本。',available:'有新版本可用。',downloading:'正在下载更新…',preparing:'正在验证并准备更新…',restarting:'即将重启应用…',unsupported:'当前系统或运行方式不支持应用内更新，请从发布页面下载新版。'};
let updateBusy=false;
function renderUpdate(){
  const value=snapshot?.update;if(!value)return;
  const busy=['downloading','preparing','restarting'].includes(value.state)||updateBusy;
  $('update-version').textContent=t('当前版本：')+value.current+(value.version?' → '+value.version:'');
  $('update-state').textContent=value.message?t(value.message):t(updateMessages[value.state]||'');
  $('update-badge').hidden=value.state!=='available';
  $('update-banner').hidden=value.state!=='available'||activeTab==='updates';
  $('update-banner').textContent=t('发现新版本，点击查看：')+(value.version||'');
  $('check-update').disabled=busy||value.state==='checking'||value.state==='unsupported';
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
    const browser=document.createElement('p');browser.textContent=session.userAgent||t('未知浏览器');
    const detail=document.createElement('p');detail.className='hint';
    detail.textContent=t(session.source==='proxy'?'代理 IP（未提供客户端地址）':session.source==='forwarded'?'经可信代理转发':'直连地址')+' · '+t('登录时间：')+date(session.created)+' · '+t('最近访问：')+date(session.lastSeen)+' · '+t('有效期至：')+(session.expires?date(session.expires):t('不自动过期'));
    const actions=document.createElement('div');actions.className='actions';
    for(const [label,action] of [['撤销登录','revoke'],['封禁此 IP','block'],['加入白名单','allow']]){
      const button=document.createElement('button');button.type='button';button.textContent=t(label);button.disabled=devicesBusy;
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

const accountsPanel=typeof AccountsPanel==='undefined'?null:new AccountsPanel({root:$('accounts-content'),desktop:true,read:()=>api.accounts({action:'list'}),request:value=>api.accounts(value),onReset:()=>{$('account-details').open=true;accountPanel.refresh();$('account-details').scrollIntoView({block:'nearest'});},onChanged:()=>accountPanel.clear(),onUpdate:value=>{accountPanel.schedule();$('account-button').hidden=value.current?.kind!=='chatgpt';if($('account-button').hidden)$('account-details').open=false;}});

$('account-details').ontoggle=()=>{if($('account-details').open)accountPanel.refresh();};
