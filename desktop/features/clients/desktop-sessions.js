'use strict';
class DesktopConnectionsPanel {
  constructor({root,api,feedback}) {
    Object.assign(this,{root,api,feedback,clientsRevision:0,statusRevision:0});
    this.deepseekHome=root.querySelector('[data-deepseek-home]');
    const add=document.getElementById('add-client');if(add)add.onclick=()=>this.addClient();
    const refresh=document.getElementById('refresh-clients');if(refresh)refresh.onclick=()=>this.scan(true);
    setInterval(()=>this.pollClients(),15000);setInterval(()=>this.pollClients(true),2000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)this.pollClients();});
    root.querySelectorAll('[data-desktop-action]').forEach(button=>button.onclick=()=>button.dataset.desktopAction==='scan'?this.scan(true):this.action(button.dataset.desktopAction));
    root.querySelector('[data-desktop-choose]').onclick=async()=>{try{const path=await api.choose('folder');if(path)this.deepseekHome.value=path;}catch(error){feedback(error.message,true);}};
    document.addEventListener('bridge-language',()=>{this.renderClients();this.renderScan();});
    this.accountPanels={};
    if(typeof DesktopClientAccountsPanel!=='undefined')for(const provider of ['claude','deepseek']){
      const article=root.querySelector('[data-client-config="'+provider+'"]');if(!article)continue;
      const content=document.createElement('div');article.querySelector('.client-auto-setup').after(content);
      this.accountPanels[provider]=new DesktopClientAccountsPanel({root:content,provider,desktop:true,
        read:()=>api.desktopSessions({action:'accounts',provider,operation:'list'}),
        request:({action,...value})=>api.desktopSessions({action:'accounts',provider,operation:action,...value}),
        readModels:(refresh=false)=>api.desktopSessions({action:'account',provider,refresh}),
        visible:()=>!document.hidden&&!document.getElementById('clients-page').hidden&&(!document.getElementById('clients-page').classList.contains('configuration-open')||!article.classList.contains('client-config-hidden')),
        onUpdate:value=>this.renderClientAccount(provider,value),onChanged:()=>this.refreshClients(true)});
    }
    document.addEventListener('bridge-client-config',()=>this.resumeAccounts());
  }
  renderDetails(){
    const value=this.connectionValue;
    const binding=this.root.querySelector('[data-deepseek-binding]');if(binding)binding.textContent=value?.deepseekBinding?.file||'';
    for(const provider of ['deepseek','claude']){
      const client=this.clients?.find(client=>client.id===provider),backend=value?.backends?.[provider],node=this.root.querySelector('[data-desktop-status='+provider+']');
      const reason=(client?this.connection(client).reason:'')||this.statusError||backend?.reason||(backend?(backend.connected?'已连接桌面':'尚未连接'):'正在检查接入…');
      node.dataset.i18n=reason;node.textContent=BridgeI18n.t(reason);
    }
    this.root.querySelector('[data-claude-workspace]').textContent=value?.claudeWorkspace||'';
    const home=value?.backends?.claude?.dataHome||'';
    this.root.querySelector('[data-claude-home]').textContent=home;
    this.root.querySelector('[data-claude-data]').hidden=!home;
    const container=this.root.querySelector('[data-client-config="deepseek"] .client-auto-setup');
    if(container){
      let actions=container.querySelector('.deepseek-recovery-actions');if(!actions){actions=document.createElement('div');actions.className='actions deepseek-recovery-actions';container.append(actions);}actions.replaceChildren();
      const client=this.clients?.find(row=>row.id==='deepseek');
      if(client?.backgroundRunning){const reconnect=document.createElement('button');reconnect.type='button';reconnect.textContent=BridgeI18n.t('恢复并重新接入');reconnect.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();reconnect.onclick=()=>this.recoverDeepseek(true);actions.append(reconnect);}
      if(client?.running){const quit=document.createElement('button');quit.type='button';quit.textContent=BridgeI18n.t('完整退出');quit.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();quit.onclick=()=>this.recoverDeepseek(false);actions.append(quit);}
    }
  }
  async refresh(){
    const revision=++this.statusRevision;
    try{
      const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'status'}),{timeout:this.statusTimeout??10000,uncertain:false,message:'客户端状态读取超时，请重试。'});if(revision!==this.statusRevision)return;
      this.connectionValue=value;this.statusError='';if(document.activeElement!==this.deepseekHome)this.deepseekHome.value=value.deepseekHome||'';
      this.renderDetails();
    }catch(error){if(revision!==this.statusRevision)return;this.statusError=error.message;this.scanMessage=error.message;this.scanError=true;this.renderScan();this.renderDetails();}
  }
  async refreshClients(refresh=false){
    const root=document.getElementById('client-overview');if(!root||this.scanning||this.choosingClient||(this.busy&&!refresh))return;
    if(this.clientsLoading){await this.clientsLoading;if(refresh)return this.refreshClients(true);return;}
    const revision=this.clientsRevision;
    const pending=(async()=>{
      try{const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'clients',refresh}),{timeout:this.statusTimeout??10000,uncertain:false,message:'客户端状态读取超时，请重试。'});if(revision!==this.clientsRevision)return;ClientLifecycle.reconcile(this.operationFailures,this.managementClients(value.clients));this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.windowsSession=value.windowsSession??this.windowsSession;this.renderClients();this.renderScan();}
      catch(error){if(revision!==this.clientsRevision)return;this.scanMessage=error.message;this.scanError=true;this.renderScan();}
    })();
    this.clientsLoading=pending;try{await pending;}finally{if(this.clientsLoading===pending)this.clientsLoading=null;this.renderClients();}
  }
  connection(client){return ClientLifecycle.connection({...client,pendingEnable:this.gatewayRunning!==false&&this.pendingClient?.id===client.id&&this.pendingClient.enabled});}
  managementClients(clients=this.clients||[]){return clients.flatMap(client=>client.id==='codex'&&client.entries?.length?client.entries.map(entry=>({...entry,id:entry.id==='desktop'?'codex':entry.id,parentId:'codex'})):client.id==='claude'&&client.entries?.length?[{...client,enabled:client.nativeEnabled??client.enabled,parentId:'claude'},...client.entries.map(entry=>({...entry,parentId:'claude'}))]:[client]);}
  withClientEnabled(id,enabled){return this.clients.map(client=>{if(client.id==='codex'&&client.entries?.length){const entries=client.entries.map(entry=>(entry.id==='desktop'?'codex':entry.id)===id?{...entry,enabled}:entry);return {...client,entries,desktopEnabled:entries.find(entry=>entry.id==='desktop')?.enabled,enabled:entries.some(entry=>entry.enabled)};}if(client.id==='claude'&&client.entries?.length){const entries=client.entries.map(entry=>entry.id===id?{...entry,enabled}:entry),nativeEnabled=client.id===id?enabled:client.nativeEnabled??client.enabled;return {...client,entries,nativeEnabled,enabled:nativeEnabled||entries.some(entry=>entry.enabled)};}return client.id===id?{...client,enabled}:client;});}
  hasUncertain(){return [...(this.operationFailures?.values()||[])].some(value=>value.uncertain);}
  retryOperation(id){const failure=this.operationFailures?.get(id);if(!failure||this.hasUncertain())return;if(typeof failure.recoveryRestart==='boolean')return this.recoverDeepseek(failure.recoveryRestart);if(failure.desktopAction)return this.action(failure.desktopAction);return failure.action==='initialize'?this.action('connect-claude'):this.toggleClient(id,failure.action==='enable');}
  pollClients(fast=false){
    if(document.hidden||document.getElementById('clients-page').hidden||this.hasUncertain()||(fast&&!this.managementClients().some(client=>this.connection(client).waiting)))return;
    return this.refreshClients();
  }
  async scan(manual=false){
    if(this.scanning||this.busy||this.choosingClient)return;this.scanning=true;this.clientsRevision++;this.statusRevision++;this.scanMessage='正在扫描本机客户端…';this.scanError=false;this.renderScan();
    try{const value=await this.api.desktopSessions({action:'scan',setup:false});if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.renderClients();}this.scanMessage='扫描完成；各客户端的接入状态如下。';}
    catch(error){this.scanMessage=error.message;this.scanError=true;if(manual)this.feedback(error.message,true);}
    finally{this.scanning=false;this.renderScan();await this.refreshClients(true);await this.refresh();}
    await this.showClaudeSetup(true);
  }
  async showClaudeSetup(automatic=false){
    const client=this.clients?.find(row=>row.id==='claude');
    if(this.claudeSetupOpen||this.busy||this.scanning||this.choosingClient||this.hasUncertain()||!client?.installed||client.setupStatus==='unsupported')return;
    const workspace=this.connectionValue?.claudeWorkspace;if(!workspace)return;
    const key='claude:setup-guide:'+workspace;
    if(automatic&&(client.connected||this.claudeSetupOffered||localStorage.getItem(key)==='seen'))return;
    this.claudeSetupOpen=true;this.claudeSetupOffered=true;this.choosingClient='claude';this.renderClients();this.renderScan();
    const release=()=>{this.claudeSetupOpen=false;this.choosingClient=null;this.renderClients();this.renderScan();};
    try{
      let value=await this.api.desktopSessions({action:'claude-prerequisites'});
      if(!value.supported){release();return;}
      const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined){n.dataset.i18n=text;n.textContent=BridgeI18n.t(text);}return n;};
      const dialog=node('dialog');dialog.className='client-disable-dialog';
      const title=node('h2','Claude 接入准备');title.id='claude-setup-title';dialog.setAttribute('aria-labelledby',title.id);
      const intro=node('p','首次接入需要完成以下准备。辅助功能与目录信任分别确认，网关不会代替你授予目录信任。');
      const permission=node('p'),developer=node('p'),trust=node('p'),reason=node('p');reason.setAttribute('role','status');
      const path=node('p');path.textContent=value.workspace;path.className='mono';
      const actions=node('div');actions.className='client-disable-options';
      const authorize=node('button','检查并申请辅助功能权限'),copy=node('button','复制连接目录'),check=node('button','重新检查'),start=node('button','初始化连接'),close=node('button','稍后设置');
      let busy=false,closed=false,initialize=false;
      const render=()=>{
        permission.hidden=value.platform!=='darwin';authorize.hidden=value.platform!=='darwin';
        permission.textContent=BridgeI18n.t(value.accessibility?.setupState==='ready'?'辅助功能：已授权':'辅助功能：请授权当前网关 App，不是 Claude');
        developer.textContent=BridgeI18n.t(value.developerMode?'开发者模式：已开启':'开发者模式：请在 Claude 中确认开启');
        trust.textContent=BridgeI18n.t(value.connected?'目录信任：当前连接已验证':'目录信任：在 Claude 的 Code 页面选择下方目录，并亲自确认信任；完成后点击“初始化连接”。');
        for(const button of [authorize,copy,check,start])button.disabled=busy;
        authorize.disabled=busy||value.accessibility?.setupState==='ready';
        start.disabled=busy||!value.gatewayRunning||value.connected||(value.platform==='darwin'&&value.accessibility?.setupState!=='ready');
        reason.textContent=BridgeI18n.t(!value.gatewayRunning?'可先完成授权准备；启动网关后再初始化连接。':value.accessibility?.reason||'');
      };
      const refresh=async requestPermission=>{
        if(busy||closed)return;busy=true;render();
        try{const result=await this.api.desktopSessions({action:'claude-prerequisites',requestPermission});if(!closed)value=result;}
        catch(error){if(!closed)this.feedback(error.message,true);}
        finally{busy=false;if(!closed)render();}
      };
      authorize.onclick=()=>refresh(true);check.onclick=()=>refresh(false);
      copy.onclick=async()=>{try{await this.api.desktopSessions({action:'copy-claude-workspace'});if(!closed)reason.textContent=BridgeI18n.t('连接目录已复制');}catch(error){if(!closed)this.feedback(error.message,true);}};
      start.onclick=()=>{initialize=true;dialog.close();};close.onclick=()=>dialog.close();
      dialog.addEventListener('close',()=>{closed=true;localStorage.setItem(key,'seen');dialog.remove();release();if(initialize)this.action('connect-claude');},{once:true});
      for(const button of [authorize,copy,check,start,close])button.type='button';
      actions.append(authorize,copy,check,start,close);dialog.append(title,intro,permission,developer,trust,path,reason,actions);document.body.append(dialog);render();dialog.showModal();close.focus();
    }catch(error){release();this.feedback(error.message,true);}
  }
  renderScan(){
    const node=document.getElementById('client-scan-status');if(node){node.textContent=BridgeI18n.t(this.scanMessage||(this.visibleClients().some(client=>client.id==='claude')?'选择需要接入的应用。macOS Claude 默认后台启动并尝试恢复已有连接；“初始化连接”经确认后使用电脑前台。Windows 优先尝试后台初始化。':'选择已有接入，或通过“添加接入”配置客户端。'))+' '+BridgeI18n.t(ClientLifecycle.sessionNotice(this.windowsSession));node.classList.toggle('error',!!this.scanError);}
    const button=document.getElementById('refresh-clients');if(button){button.disabled=!!this.scanning||!!this.busy||!!this.choosingClient;button.textContent=BridgeI18n.t(this.scanning?'正在扫描…':'重新扫描');}
    this.root.querySelectorAll('[data-desktop-action]').forEach(button=>button.disabled=!!this.scanning||!!this.busy||!!this.choosingClient||(button.dataset.desktopAction!=='scan'&&this.hasUncertain()));
  }
  async toggleClient(id,enabled){
    const client=this.managementClients().find(row=>row.id===id);
    if(this.busy||this.scanning||this.choosingClient||this.hasUncertain()||!client||(enabled&&!(client.selectable??client.configured)))return;
    let quitDesktop=false;
    if(!enabled&&this.gatewayRunning!==false&&client.canQuitDesktop!==false){
      this.choosingClient=id;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();
      try{quitDesktop=await ClientLifecycle.chooseDisable(client);}
      finally{this.choosingClient=null;this.renderClients();this.renderScan();}
      if(quitDesktop===null)return;
    }
    const acknowledgeUnavailable=quitDesktop==='recovery',forceDesktop=quitDesktop==='force';quitDesktop=quitDesktop===true||forceDesktop||acknowledgeUnavailable;
    const before=this.clients;
    this.operationFailures??=new Map();this.operationFailures.delete(id);
    this.busy=true;this.pendingClient={id,enabled,quitDesktop,forceDesktop};this.clientsRevision++;this.statusRevision++;
    this.clients=this.withClientEnabled(id,enabled);
    this.renderClients();this.renderScan();
    try{const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'toggle-client',provider:id,enabled,...(!enabled?{quitDesktop}:{}),...(forceDesktop?{forceDesktop:true,forceConfirmed:true}:{}),...(acknowledgeUnavailable?{acknowledgeUnavailable:true}:{})}),{timeout:this.lifecycleTimeout??45000});if(value.clients)this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.windowsSession=value.windowsSession??this.windowsSession;}
    catch(error){this.operationFailures.set(id,ClientLifecycle.failure(enabled?'enable':forceDesktop?'force':quitDesktop?'quit':'disable',error,'desktop'));this.clients=before;this.feedback(error.message,true);}
    finally{this.busy=false;this.pendingClient=null;this.renderClients();this.renderScan();await this.refreshClients(true);}
  }
  addClient(){
    const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';dialog.setAttribute('aria-labelledby','client-add-title');
    const heading=document.createElement('h2');heading.id='client-add-title';heading.textContent=BridgeI18n.t('添加接入');dialog.append(heading);
    const actions=document.createElement('div');actions.className='actions';
    for(const [id,label] of [['codex','Codex'],['claude','Claude'],['deepseek','DSH / 自定义 Harness']]){
      const button=document.createElement('button');button.type='button';button.textContent=BridgeI18n.t(label);
      button.onclick=()=>{dialog.close();window.GatewayLayout.selectClient(id);if(id==='deepseek'){const manual=this.root.querySelector('[data-deepseek-manual]');if(manual)manual.open=true;}};actions.append(button);
    }
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent=BridgeI18n.t('取消');cancel.onclick=()=>dialog.close();actions.append(cancel);dialog.append(actions);
    dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();cancel.focus();
  }
  visibleClients(){
    return this.managementClients().filter(client=>client.installed||client.configured||client.enabled||client.connected||client.running||client.bindingMode==='manual');
  }
  renderClients(){
    this.renderDetails();
    const root=document.getElementById('client-overview'),t=BridgeI18n.t;if(!root||!this.clients)return;
    const node=(tag,text,cls='')=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=t(text);return n;};
    const table=node('table',undefined,'connection-table'),head=node('thead'),tr=node('tr'),body=node('tbody');
    for(const title of ['客户端','应用开关','连接状态','当前接入 / 剩余额度','管理'])tr.append(node('th',title));head.append(tr);table.append(head,body);
    for(const client of this.visibleClients()){
      const row=node('tr'),identity=node('td'),name=node('div',undefined,'connection-name'),logo=node('span',undefined,'client-logo');row.dataset.clientId=client.id;if(client.parentId)row.dataset.clientParent=client.parentId;
      const img=node('img');img.src='../web/client-icons/'+(client.icon||client.id)+'.png';img.alt='';logo.append(img);
      const label=node('div');label.append(node('strong',client.name),node('small',client.description|| (client.kind==='wsl-claude-cli'?'WSL Claude Code':client.kind==='wsl-cli'?'WSL Codex CLI':({codex:'Codex Desktop',claude:'Claude Desktop',deepseek:'DeepSeek Harness'})[client.id])));name.append(logo,label);identity.append(name);
      const access=node('td'),toggle=node('input');toggle.type='checkbox';toggle.className='client-toggle';toggle.checked=client.enabled;toggle.disabled=(!client.enabled&&!(client.selectable??client.configured))||this.scanning||this.busy||!!this.choosingClient;toggle.setAttribute('aria-label',t('启用')+' '+client.name);toggle.title=t(client.reason);toggle.onchange=()=>this.toggleClient(client.id,toggle.checked);access.append(toggle,node('small',this.pendingClient?.id===client.id?(this.gatewayRunning===false?'正在保存…':this.pendingClient.enabled?(client.id==='claude'?'正在连接…':'正在打开应用…'):this.pendingClient.forceDesktop?'正在后台强制结束…':this.pendingClient.quitDesktop?'正在退出应用…':'正在停用接入…'):client.setupStatus==='unsupported'?'暂不可用':client.enabled?(this.gatewayRunning===false?'随网关启动':'已启用'):!client.installed&&!client.configured?'待配置':'未启用'));
      toggle.disabled=toggle.disabled||this.hasUncertain();const state=this.connection(client),failure=this.operationFailures?.get(client.id),status=node('td'),stateLabel=node('span',state.label,state.waiting?'state-pending client-connection-waiting':client.connected?'state-ready':state.error?'state-error':state.action?'state-pending':'');stateLabel.setAttribute('role','status');stateLabel.setAttribute('aria-busy',String(state.waiting));status.append(stateLabel,node('small',failure?ClientLifecycle.failureMessage(failure):state.reason));
      const account=node('td');account.dataset.clientAccount=client.id;
      if(client.id!=='codex')account.append(node('span',client.configured?'已配置':'待配置'),node('small',client.accountNotice|| (['wsl-cli','wsl-claude-cli'].includes(client.kind)?'在网关中管理 WSL 账号与 API':client.id==='claude'?'在 Claude 中管理账号':'在 Harness 中管理账号 / API')));
      const actions=node('td'),buttons=node('div',undefined,'connection-actions'),manage=node('button',client.setupStatus==='unsupported'?'查看详情':client.configured?'管理':'配置');
      if(failure){const retry=node('button',failure.uncertain?'刷新状态':ClientLifecycle.retryLabel(failure.action),'primary');retry.type='button';retry.disabled=this.busy||this.scanning||!!this.choosingClient||!!this.clientsLoading||(!failure.uncertain&&this.hasUncertain());retry.onclick=()=>failure.uncertain?this.refreshClients(true):this.retryOperation(client.id);buttons.append(retry);}
      if(!failure&&state.retryable&&this.gatewayRunning!==false){const retry=node('button','重试连接','primary');retry.type='button';retry.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();retry.onclick=()=>this.toggleClient(client.id,true);buttons.append(retry);}
      if(!failure&&client.id==='deepseek'&&client.backgroundRunning&&(!client.mainRunning||['restart-required','failed'].includes(client.setupStatus))){
        const recover=node('button','恢复并重新接入','primary');recover.type='button';recover.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();recover.onclick=()=>this.recoverDeepseek(true);buttons.append(recover);
      }else if(!failure&&this.gatewayRunning!==false&&client.id==='deepseek'&&['restart-required','needs-first-launch'].includes(client.setupStatus)){
        const restart=client.setupStatus==='restart-required',connect=node('button',restart?'重启并接入':'打开并接入','primary');connect.type='button';connect.disabled=!!this.busy||!!this.scanning||!!this.choosingClient||this.hasUncertain();connect.onclick=()=>this.action(restart?'restart-deepseek':'connect-deepseek');buttons.append(connect);manage.textContent=t('管理');manage.className='connection-secondary';
      }
      if(!failure&&this.gatewayRunning!==false&&client.id==='claude'&&client.installed&&!client.connected&&client.setupStatus!=='unsupported'){
        const restart=client.setupStatus==='restart-required',pending=state.waiting;
        const connect=node('button',pending?'取消连接':restart?'重启并接入':client.setupStatus==='needs-trust'?'Claude 接入准备':client.setupStatus==='needs-permission'?'授权并连接':['needs-developer-mode','needs-trust','needs-unlock','needs-desktop','needs-retry'].includes(client.setupStatus)?'重试初始化':'初始化连接',pending?'':'primary');
        connect.type='button';connect.disabled=!!this.busy||!!this.scanning||!!this.choosingClient||this.hasUncertain();connect.onclick=()=>this.action(pending?'cancel-claude':restart?'restart-claude':client.setupStatus==='needs-trust'?'setup-claude':'connect-claude');buttons.append(connect);manage.textContent=t('查看详情');manage.className='connection-secondary';
      }
      if(client.id==='claude'&&!client.connected&&client.canConfirmQuit){
        const quit=node('button','退出 Claude…');quit.type='button';quit.disabled=!!this.busy||!!this.scanning;quit.onclick=()=>this.quitClaude();buttons.append(quit);
      }
      if(!client.enabled&&client.running&&client.canQuitDesktop!==false&&this.gatewayRunning!==false){const quit=node('button','关闭电脑 App…');quit.type='button';quit.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();quit.onclick=()=>this.toggleClient(client.id,false);buttons.append(quit);}
      manage.type='button';manage.onclick=()=>client.configurationNotice?this.clientDetails(client):['wsl-cli','wsl-claude-cli'].includes(client.kind)?this.wslDetails(client):window.GatewayLayout.selectClient(client.id);buttons.append(manage);actions.append(buttons);row.append(identity,access,status,account,actions);body.append(row);
    }
    root.replaceChildren(this.visibleClients().length?table:node('p','尚未发现客户端，请添加接入或重新扫描。','hint'));this.renderAccount(this.accountValue);
    for(const [provider,panel] of Object.entries(this.accountPanels)){this.renderClientAccount(provider,panel.value);}
    this.resumeAccounts();
  }
  clientDetails(client){
    const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
    const title=document.createElement('h2'),description=document.createElement('p'),notice=document.createElement('p'),close=document.createElement('button');
    title.textContent=client.name;description.textContent=BridgeI18n.t(client.description);notice.textContent=BridgeI18n.t(client.configurationNotice);
    close.type='button';close.textContent=BridgeI18n.t('关闭');close.onclick=()=>dialog.close();dialog.append(title,description,notice,close);
    let panel;if(client.capabilities?.accounts===true){const content=document.createElement('div');dialog.insertBefore(content,close);const provider=client.id;panel=new DesktopClientAccountsPanel({root:content,provider,desktop:true,read:()=>this.api.desktopSessions({action:'accounts',provider,operation:'list'}),request:({action,...value})=>this.api.desktopSessions({action:'accounts',provider,operation:action,...value}),readModels:refresh=>this.api.desktopSessions({action:'account',provider,refresh}),onChanged:()=>this.refreshClients(true)});}
    dialog.addEventListener('close',()=>{panel?.dispose();dialog.remove();},{once:true});document.body.append(dialog);dialog.showModal();panel?.refresh();close.focus();
  }
  async wslDetails(client){
    try{if(typeof WslAccountsPanel==='undefined')await window.loadWslAccounts();}
    catch(error){this.feedback(error.message,true);return;}
    const provider=client.kind==='wsl-claude-cli'?'claude':'codex',entry=client.id;
    const dialog=document.createElement('dialog'),header=document.createElement('div'),title=document.createElement('h2'),root=document.createElement('div'),close=document.createElement('button');
    dialog.id='wsl-account-management-dialog';dialog.className='wsl-accounts-dialog';dialog.dataset.provider=provider;dialog.dataset.entry=entry;
    header.className='picker-head';title.textContent=BridgeI18n.t('WSL 账号与接入');close.type='button';close.textContent=BridgeI18n.t('关闭');close.onclick=()=>dialog.close();header.append(title,close);dialog.append(header,root);
    const panel=new WslAccountsPanel({root,provider,desktop:true,
      read:selected=>this.api.cliAccess({action:'wslAccounts',provider,entry:selected,operation:'list'}),
      request:({action,provider:_provider,entry:_entry,...fields})=>this.api.cliAccess({action:'wslAccounts',provider,entry,operation:action,...fields})});
    dialog.addEventListener('close',()=>{panel.dispose();dialog.remove();});document.body.append(dialog);dialog.showModal();panel.select(entry);
    return panel;
  }
  resumeAccounts(){for(const panel of Object.values(this.accountPanels))panel.resume();}
  renderClientAccount(provider,value){
    const cell=document.querySelector('[data-client-account="'+provider+'"]');if(!cell||!value)return;
    const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=BridgeI18n.t(text);return n;};
    const activeIds=new Set(value.activeIds||[value.activeId]),accounts=value.accounts.filter(row=>activeIds.has(row.id));cell.replaceChildren();
    if(!accounts.length)cell.append(node('span',value.current?.name||value.current?.label||'暂未识别'));
    for(const row of accounts){
      cell.append(node('span',row.name));const usage=row.usage||row.quota;
      for(const bucket of usage?.limits||[])for(const window of bucket.windows||[]){cell.append(node('small',bucket.name+' · '+(window.remainingPercent==null?BridgeI18n.t('暂未提供'):BridgeI18n.t('剩余 ')+Math.round(window.remainingPercent)+'%')));if(Number.isFinite(window.remainingPercent)){const bar=node('progress','');bar.max=100;bar.value=window.remainingPercent;cell.append(bar);}}
      for(const wallet of usage?.balance?.wallets||[])cell.append(node('small',BridgeI18n.t('账户余额')+' · '+wallet.currency+' '+wallet.remaining));
      if(row.kind!=='api'||usage?.balance){const refresh=node('button','查看剩余额度');refresh.type='button';refresh.className='quota-link';refresh.disabled=this.accountPanels[provider].busy||this.accountPanels[provider].loading;refresh.onclick=async()=>{refresh.disabled=true;await this.accountPanels[provider].perform({action:'details',id:row.id,refresh:true});this.renderClientAccount(provider,this.accountPanels[provider].value);};cell.append(refresh);}
    }
  }
  renderAccount(value){
    if(value)this.accountValue=value;else value=this.accountValue;
    const cell=document.querySelector('[data-client-account="codex"]');if(!cell)return;cell.replaceChildren();const t=BridgeI18n.t,name=document.createElement('span');name.textContent=value?.current?.name||t('暂未识别');cell.append(name);
    const row=value?.loginAccount||value?.accounts?.find(row=>row.id===value.activeId);if(row?.kind!=='chatgpt')return;
    if(row.name&&row.name!==value?.current?.name){const login=document.createElement('small');login.textContent=t('已登录')+' · '+row.name;cell.append(login);}
    if(!row.details?.usage||row.details.usage.status==='loading'){const loading=document.createElement('small');loading.textContent=t('正在读取额度…');cell.append(loading);}
    else if(row.details.usage.status==='error'||!row.details.usage.limits?.length){const notice=document.createElement('small');notice.textContent=t(row.details.usage.error||'暂未提供额度信息');cell.append(notice);}
    const usage=row.details?.usage;if(usage?.status==='ready'){const credits=document.createElement('small');credits.textContent=t('重置卡')+' · '+(usage.resetCredits?.availableCount??t('暂未提供'));cell.append(credits);}for(const bucket of usage?.limits||[])for(const window of bucket.windows||[]){const note=document.createElement('small');note.textContent=bucket.name+' · '+(window.remainingPercent==null?t('暂未提供'):t('剩余 ')+Math.round(window.remainingPercent)+'%');cell.append(note);if(window.remainingPercent!=null){const bar=document.createElement('progress');bar.max=100;bar.value=window.remainingPercent;cell.append(bar);}}
    const refresh=document.createElement('button');refresh.type='button';refresh.className='quota-link';refresh.textContent=t('查看剩余额度');refresh.disabled=usage?.status==='loading';refresh.onclick=async()=>{refresh.disabled=true;try{this.renderAccount(await this.api.accounts({action:'details',id:row.id,section:'usage',refresh:true}));}catch(error){this.feedback(error.message,true);refresh.disabled=false;}};cell.append(refresh);
  }
  async recoverDeepseek(restart){
    if(this.busy||this.scanning||this.choosingClient||this.hasUncertain())return;this.busy=true;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();let attempted=false;
    try{
      const preview=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'deepseek-recovery-preview',restart}),{timeout:this.statusTimeout??10000,uncertain:false,message:'客户端状态读取超时，请重试。'});
      if(preview.busy)throw Error(preview.message||'DSH 仍有任务运行或等待确认，请先在桌面结束任务');
      if(!preview.canRecover)throw Error(preview.message||'当前 DSH 进程无法安全恢复，请在桌面检查后重新扫描');
      const t=BridgeI18n.t,prompt=[t(restart?'将完整退出 DSH 的桌面和后台进程，更新接入后重新打开。':'将完整退出 DSH 的桌面和后台进程。'),t('桌面进程：')+(preview.guiCount||0)+' · '+t('后台进程：')+(preview.backgroundCount||0),preview.requiresUnknownConfirmation?t('部分后台进程无法核验任务状态。确认继续表示你已检查并结束任务、保存工作。'):t('请确认已保存工作。')].join('\n\n');
      if(!window.confirm(prompt))return;
      attempted=true;this.operationFailures??=new Map();this.operationFailures.delete('deepseek');
      const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'deepseek-recovery-confirm',token:preview.token,confirmed:true,...(preview.requiresUnknownConfirmation?{acknowledgeUnknown:true}:{})}),{timeout:this.lifecycleTimeout??45000});
      if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;}this.feedback(restart?(value.clients?.find(row=>row.id==='deepseek')?.connected?'DSH 已重新接入':'正在等待 Harness 连接'):'DSH 已完整退出');
    }catch(error){if(attempted)this.operationFailures.set('deepseek',{...ClientLifecycle.failure('recover',error,'desktop'),recoveryRestart:restart});this.feedback(error.message,true);}
    finally{this.busy=false;this.renderClients();this.renderScan();await this.refreshClients(true);await this.refresh();}
  }
  async quitClaude(){
    if(this.busy||this.scanning)return;this.busy=true;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();
    try{
      const preview=await this.api.desktopSessions({action:'claude-quit-preview'}),t=BridgeI18n.t;
      if(!preview.canQuit)throw Error(t(preview.busy?'有任务运行或等待确认，请先结束任务再关闭或重启客户端':'Claude 进程无法确认，请在电脑端检查后重试'));
      const prompt=t(preview.requiresUnknownConfirmation?'无法核验 Claude 的任务状态，退出可能中断正在执行的任务。请确认已检查 Claude 并保存工作。':'退出 Claude 会关闭桌面应用。请确认已保存工作。')+'\n\n'+t('重新启动 Claude Desktop 后，初始化连接需要电脑处于解锁状态。是否确认退出？');
      if(!window.confirm(prompt))return;
      const value=await this.api.desktopSessions({action:'claude-quit-confirm',token:preview.token,confirmed:true,...(preview.requiresUnknownConfirmation?{acknowledgeUnknown:true}:{})});
      if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;}this.feedback('Claude 已退出');
    }catch(error){this.feedback(error.message,true);}
    finally{this.busy=false;this.renderClients();this.renderScan();await this.refreshClients(true);await this.refresh();}
  }
  async action(action){
    if(this.busy||this.scanning||this.choosingClient||this.hasUncertain())return;
    if(['export-deepseek-plugin','scan-deepseek-plugins','bind-deepseek-plugin','unbind-deepseek-plugin'].includes(action))return this.manualDeepseek(action);
    if(action==='setup-claude')return this.showClaudeSetup();
    if(action==='restart-deepseek'&&!window.confirm(BridgeI18n.t('重启 Harness 会断开当前会话。请先在 Harness 中结束所有任务并保存工作，再确认重启并接入。')))return;
    if(action==='restart-claude'&&!window.confirm(BridgeI18n.t('重启 Claude 会断开当前会话。请先结束任务并保存工作，再确认重启并接入。')))return;
    if(['connect-claude','restart-claude'].includes(action)){
      const client=this.clients?.find(row=>row.id==='claude');if(!client)return;
      this.choosingClient='claude';this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();let confirmed=false;
      try{confirmed=await ClientLifecycle.chooseInitialize(client);}finally{this.choosingClient=null;this.renderClients();this.renderScan();}
      if(!confirmed)return;
    }
    const provider=action.endsWith('-claude')?'claude':action.endsWith('-deepseek')?'deepseek':null,operation=['connect-claude','restart-claude'].includes(action)?'initialize':action==='connect-deepseek'?'enable':action;this.operationFailures??=new Map();if(provider)this.operationFailures.delete(provider);
    this.busy=true;this.clientsRevision++;this.statusRevision++;const buttons=this.root.querySelectorAll('button');buttons.forEach(button=>button.disabled=true);this.renderClients();this.renderScan();let rescan=false;
    try{
      const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action,...(['install-deepseek','remove-deepseek'].includes(action)?{home:this.deepseekHome.value.trim()}:{})}),{timeout:this.lifecycleTimeout??45000});
      if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.renderClients();}
      if(['connect-deepseek','restart-deepseek'].includes(action))this.feedback('正在等待 Harness 连接');
      rescan=action==='connect-deepseek'||action==='install-deepseek';
      await this.refreshClients(true);await this.refresh();
    }catch(error){if(provider)this.operationFailures.set(provider,{...ClientLifecycle.failure(operation,error,'desktop'),desktopAction:action});this.feedback(error.message,true);}
    finally{this.busy=false;buttons.forEach(button=>button.disabled=false);this.renderClients();this.renderScan();if(provider&&this.operationFailures.has(provider))await this.refreshClients(true);}
    if(rescan)await this.scan();
  }
  renderPluginCandidates(candidates){
    const root=this.root.querySelector('[data-deepseek-candidates]');root.replaceChildren();
    if(!candidates.length){root.textContent=BridgeI18n.t('未发现接入插件，请先在此 Harness 核心中安装并加载插件。');return;}
    for(const item of candidates){
      const row=document.createElement('p'),button=document.createElement('button'),path=document.createElement('small');
      button.type='button';button.textContent=BridgeI18n.t('绑定档案：')+item.profile;
      button.onclick=()=>{if(!this.busy&&!this.scanning&&!this.hasUncertain())this.manualDeepseek('bind-deepseek-plugin',item.file);};
      path.textContent=item.file;row.append(button,path);root.append(row);
    }
  }
  async manualDeepseek(action,file){
    this.busy=true;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();
    try{
      const payload={action};
      if(action==='scan-deepseek-plugins'){
        payload.home=await this.api.choose('folder');if(!payload.home)return;
      }else if(action==='export-deepseek-plugin'){
        payload.destination=await this.api.choose('folder');if(!payload.destination)return;
      }else if(action==='bind-deepseek-plugin'){
        payload.file=file||await this.api.choose('file');if(!payload.file)return;
        const preview=await this.api.desktopSessions({action:'preview-deepseek-plugin',file:payload.file});
        if(!window.confirm(BridgeI18n.t('连接这个 Harness 档案？')+'\n'+preview.profile+'\n'+preview.home+'\n'+BridgeI18n.t('插件版本：')+(preview.plugin?.version||BridgeI18n.t('旧版'))+'\n'+BridgeI18n.t('仅绑定当前网关，不退出或修改原客户端。')))return;
        payload.instanceId=preview.instanceId;
      }
      const value=await this.api.desktopSessions(payload);
      if(action==='scan-deepseek-plugins'){this.renderPluginCandidates(value.candidates||[]);return;}
      this.feedback(value.path?BridgeI18n.t('插件包已保存：')+value.path+(value.tarPath?' · '+value.tarPath:''):BridgeI18n.t(value.bound?'已绑定，请开启 DSH 接入':'已解除手动绑定'));
    }catch(error){this.feedback(error.message,true);}
    finally{this.busy=false;await this.refreshClients(true);await this.refresh();this.renderClients();this.renderScan();}
  }
}

if(typeof module!=='undefined')module.exports={DesktopConnectionsPanel};
