'use strict';
class DesktopConnectionsPanel {
  constructor({root,api,feedback}) {
    Object.assign(this,{root,api,feedback,clientsRevision:0,statusRevision:0});
    this.deepseekHome=root.querySelector('[data-deepseek-home]');
    this.deepseekHome.placeholder=this.configurationExample('deepseek');
    const add=document.getElementById('add-client');if(add)add.onclick=()=>this.addClient();
    const refresh=document.getElementById('refresh-clients');if(refresh)refresh.onclick=()=>this.scan(true);
    document.addEventListener('visibilitychange',()=>{this.syncPolling();if(!document.hidden)this.pollClients();});
    root.querySelectorAll('[data-desktop-action]').forEach(button=>button.onclick=()=>button.dataset.desktopAction==='scan'?this.scan(true):this.action(button.dataset.desktopAction));
    root.querySelector('[data-desktop-choose]').onclick=async()=>{try{const path=await api.choose('folder');if(path)this.deepseekHome.value=path;}catch(error){feedback(error.message,true);}};
    document.addEventListener('bridge-language',()=>{this.renderClients();this.renderScan();});
    this.accountPanels={};this.managementPages=new Map();this.removalControls=new Map();
    document.addEventListener('bridge-client-config',event=>{for(const [id,control] of this.removalControls)control.hidden=id!==event.detail;this.ensureAccountPanel(event.detail);this.resumeAccounts();});
  }
  configurationExample(family){
    const windows=this.api.platform==='win32',home=windows?'C:\\Users\\username':this.api.platform==='darwin'?'/Users/username':'/home/username';
    if(family==='claude')return home+(windows?'\\AppData\\Roaming\\Claude':this.api.platform==='darwin'?'/Library/Application Support/Claude':'/.config/Claude');
    return home+(windows?'\\':'/')+(family==='codex'?'.codex':'.dsh');
  }
  ensureAccountPanel(provider){
    if(!['claude','deepseek'].includes(provider)||typeof DesktopClientAccountsPanel==='undefined')return;
    if(this.accountPanels[provider])return this.accountPanels[provider];
    const article=this.root.querySelector('[data-client-config="'+provider+'"]');if(!article)return;
    const content=document.createElement('div');article.querySelector('.client-auto-setup').after(content);
    this.accountPanels[provider]=new DesktopClientAccountsPanel({root:content,provider,desktop:true,
      read:()=>this.api.desktopSessions({action:'accounts',provider,operation:'list'}),
      request:({action,...value})=>this.api.desktopSessions({action:'accounts',provider,operation:action,...value}),
      readModels:(refresh=false)=>this.api.desktopSessions({action:'account',provider,refresh}),
      visible:()=>{
        const page=document.getElementById('clients-page'),client=this.clients?.find(row=>row.id===provider);
        return !document.hidden&&!page.hidden&&(page.classList.contains('configuration-open')
          ?!article.classList.contains('client-config-hidden'):!!(client?.nativeEnabled??client?.enabled));
      },
      onUpdate:value=>this.renderClientAccount(provider,value),onChanged:()=>this.refreshClients(true)});
    return this.accountPanels[provider];
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
  applyClientSnapshot(value){
    ClientLifecycle.reconcile(this.operationFailures,this.managementClients(value.clients));
    this.clients=value.clients;this.instanceError=value.instanceError||'';
    this.inventoryAvailable=value.inventoryAvailable??this.inventoryAvailable;
    this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.windowsSession=value.windowsSession??this.windowsSession;
  }
  async loadClients(){
    await this.refreshClients();
    if(this.inventoryAvailable===false)await this.scan();
  }
  async refreshClients(refresh=false){
    const root=document.getElementById('client-overview');if(!root||this.scanning||this.choosingClient||(this.busy&&!refresh))return;
    if(this.clientsLoading){await this.clientsLoading;if(refresh)return this.refreshClients(true);return;}
    const revision=this.clientsRevision;
    const pending=(async()=>{
      try{const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'clients',refresh}),{timeout:this.statusTimeout??10000,uncertain:false,message:'客户端状态读取超时，请重试。'});if(revision!==this.clientsRevision)return;this.applyClientSnapshot(value);this.renderClients();this.renderScan();}
      catch(error){if(revision!==this.clientsRevision)return;this.scanMessage=error.message;this.scanError=true;this.renderScan();}
    })();
    this.clientsLoading=pending;try{await pending;}finally{if(this.clientsLoading===pending)this.clientsLoading=null;this.renderClients();}
  }
  connection(client){return ClientLifecycle.connection({...client,pendingEnable:this.gatewayRunning!==false&&this.pendingClient?.id===client.id&&this.pendingClient.enabled});}
  managementClients(clients=this.clients||[]){return clients.flatMap(client=>client.id==='codex'&&client.entries?.length?client.entries.map(entry=>({...entry,id:entry.id==='desktop'?'codex':entry.id,parentId:'codex'})):client.id==='claude'&&client.entries?.length?[{...client,enabled:client.nativeEnabled??client.enabled,parentId:'claude'},...client.entries.map(entry=>({...entry,parentId:'claude'}))]:[client]);}
  withClientEnabled(id,enabled){return this.clients.map(client=>{if(client.id==='codex'&&client.entries?.length){const entries=client.entries.map(entry=>(entry.id==='desktop'?'codex':entry.id)===id?{...entry,enabled}:entry);return {...client,entries,desktopEnabled:entries.find(entry=>entry.id==='desktop')?.enabled,enabled:entries.some(entry=>entry.enabled)};}if(client.id==='claude'&&client.entries?.length){const entries=client.entries.map(entry=>entry.id===id?{...entry,enabled}:entry),nativeEnabled=client.id===id?enabled:client.nativeEnabled??client.enabled;return {...client,entries,nativeEnabled,enabled:nativeEnabled||entries.some(entry=>entry.enabled)};}return client.id===id?{...client,enabled}:client;});}
  hasUncertain(){return [...(this.operationFailures?.values()||[])].some(value=>value.uncertain);}
  retryOperation(id){
    const failure=this.operationFailures?.get(id);if(!failure||this.hasUncertain())return;
    if(typeof failure.recoveryRestart==='boolean')return this.recoverDeepseek(failure.recoveryRestart);
    if(failure.desktopAction)return this.action(failure.desktopAction);
    if(failure.action==='initialize')return id==='claude'?this.action('connect-claude'):this.toggleClient(id,true,{initializeDesktop:true});
    if(id==='zcode'&&failure.action==='enable'&&failure.connectionMode!=='official-remote'&&this.connection(this.managementClients().find(client=>client.id===id)).canInitialize)return this.toggleClient(id,true,{initializeDesktop:true});
    return this.toggleClient(id,failure.action==='enable',{connectionMode:failure.connectionMode});
  }
  syncPolling(){
    const visible=!document.hidden&&!document.getElementById('clients-page').hidden;
    const interval=visible?(this.managementClients().some(client=>this.connection(client).waiting)?2000:15000):0;
    if(interval===(this.pollInterval||0))return;
    clearInterval(this.pollTimer);this.pollInterval=interval;
    if(interval)this.pollTimer=setInterval(()=>this.pollClients(),interval);
  }
  pollClients(fast=false){
    if(document.hidden||document.getElementById('clients-page').hidden||this.hasUncertain()||(fast&&!this.managementClients().some(client=>this.connection(client).waiting)))return;
    return this.refreshClients();
  }
  async scan(manual=false){
    if(this.scanning||this.busy||this.choosingClient)return;this.scanning=true;this.clientsRevision++;this.statusRevision++;this.scanMessage='正在扫描本机客户端…';this.scanError=false;this.renderScan();
    let gotSnapshot=false;
    try{const value=await this.api.desktopSessions({action:'scan',setup:false,inventoryOnly:!manual});if(value.clients){this.applyClientSnapshot(value);gotSnapshot=true;}this.scanError=!!value.errors?.length;this.scanMessage=this.scanError?value.errors.map(row=>row.distribution+'：'+BridgeI18n.t(row.error)).join('\n'):'扫描完成；各客户端的接入状态如下。';}
    catch(error){this.scanMessage=error.message;this.scanError=true;if(manual)this.feedback(error.message,true);}
    finally{this.scanning=false;this.renderScan();if(gotSnapshot)this.renderClients();else await this.refreshClients(true);await this.refresh();}
    await this.showClaudeSetup(true);
  }
  async showClaudeSetup(automatic=false){
    const client=this.clients?.find(row=>row.id==='claude');
    if(this.claudeSetupOpen||this.busy||this.scanning||this.choosingClient||this.hasUncertain()||!client?.installed||client.setupStatus==='unsupported')return;
    const workspace=this.connectionValue?.claudeWorkspace;if(!workspace)return;
    const key='claude:setup-guide:'+workspace;
    if(automatic&&(!(client.nativeEnabled??client.enabled)||client.connected||this.claudeSetupOffered||localStorage.getItem(key)==='seen'))return;
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
    const node=document.getElementById('client-scan-status');if(node){node.textContent=BridgeI18n.t(this.instanceError||this.scanMessage||(this.visibleClients().some(client=>client.id==='claude')?'选择需要接入的应用。macOS Claude 默认后台启动并尝试恢复已有连接；“初始化连接”经确认后使用电脑前台。Windows 优先尝试后台初始化。':'选择已有客户端，或通过“添加客户端”配置新的客户端。'))+' '+BridgeI18n.t(ClientLifecycle.sessionNotice(this.windowsSession));node.classList.toggle('error',!!this.scanError||!!this.instanceError);}
    const button=document.getElementById('refresh-clients');if(button){button.disabled=!!this.scanning||!!this.busy||!!this.choosingClient;button.dataset.i18n=this.scanning?'正在扫描…':'重新扫描';button.textContent=BridgeI18n.t(button.dataset.i18n);}
    this.root.querySelectorAll('[data-desktop-action]').forEach(button=>button.disabled=!!this.scanning||!!this.busy||!!this.choosingClient||(button.dataset.desktopAction!=='scan'&&this.hasUncertain()));
  }
  async toggleClient(id,enabled,{initializeDesktop=false,connectionMode}={}){
    const client=this.managementClients().find(row=>row.id===id);
    if(this.busy||this.scanning||this.choosingClient||this.hasUncertain()||!client||(enabled&&!(client.selectable??client.configured)))return;
    let choice;
    if(initializeDesktop&&(!enabled||this.gatewayRunning===false||!this.connection(client).canInitialize))return;
    if(initializeDesktop||(!enabled&&this.gatewayRunning!==false&&client.canQuitDesktop!==false)){
      this.choosingClient=id;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();
      try{choice=await (initializeDesktop?ClientLifecycle.chooseInitialize(client):ClientLifecycle.chooseDisable(client));}
      finally{this.choosingClient=null;this.renderClients();this.renderScan();}
      if(choice===null||initializeDesktop&&!choice)return;
    }
    const intent={...ClientLifecycle.initializationFields(initializeDesktop&&choice),...(connectionMode?{connectionMode}:{})};
    const acknowledgeUnavailable=!enabled&&choice==='recovery',forceDesktop=choice==='force';
    const quitDesktop=!enabled&&(choice===true||forceDesktop||acknowledgeUnavailable);
    const before=this.clients;
    this.operationFailures??=new Map();this.operationFailures.delete(id);
    this.busy=true;this.pendingClient={id,enabled,quitDesktop,forceDesktop};this.clientsRevision++;this.statusRevision++;
    this.clients=this.withClientEnabled(id,enabled);
    this.renderClients();this.renderScan();
    try{const value=await ClientLifecycle.request(()=>this.api.desktopSessions({action:'toggle-client',provider:id,enabled,...intent,...(!enabled?{quitDesktop}:{}),...(forceDesktop?{forceDesktop:true,forceConfirmed:true}:{}),...(acknowledgeUnavailable?{acknowledgeUnavailable:true}:{})}),{timeout:this.lifecycleTimeout??45000});if(value.clients)this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.windowsSession=value.windowsSession??this.windowsSession;}
    catch(error){this.operationFailures.set(id,{...ClientLifecycle.failure(initializeDesktop?'initialize':enabled?'enable':forceDesktop?'force':quitDesktop?'quit':'disable',error,'desktop'),connectionMode:intent.connectionMode});this.clients=before;this.feedback(error.message,true);}
    finally{this.busy=false;this.pendingClient=null;this.renderClients();this.renderScan();await this.refreshClients(true);}
  }
  async selectConnectionMode(id,mode){
    const client=this.managementClients().find(row=>row.id===id);
    if(!client||this.busy||this.scanning||this.choosingClient||this.hasUncertain())return;
    this.choosingClient=id;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();let accepted;
    try{accepted=await ClientLifecycle.confirmConnectionMode(client,mode);}finally{this.choosingClient=null;this.renderClients();this.renderScan();}
    if(accepted){await this.toggleClient(id,true,{connectionMode:mode});if(id==='zcode')await this.zcodeOfficial?.refresh();}
  }
  async addClient(){
    let value;try{value=await this.api.desktopSessions({action:'client-profiles',operation:'list'});}catch(error){this.feedback(error.message,true);return;}
    const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';dialog.setAttribute('aria-labelledby','client-add-title');
    const heading=document.createElement('h2');heading.id='client-add-title';heading.textContent=BridgeI18n.t('添加客户端');dialog.append(heading);
    const actions=document.createElement('div');actions.className='actions';
    for(const [family,label] of [['codex','Codex'],['claude','Claude'],['deepseek','DSH'],['zcode','ZCode'],['antigravity','Antigravity']]){
      const capability=value.capabilities?.find(row=>row.family===family),button=document.createElement('button');button.type='button';button.textContent=label;
      button.disabled=capability?.supported!==true;button.title=BridgeI18n.t(capability?.reason||'');
      if(family==='zcode'&&button.disabled)button.textContent+=' · '+BridgeI18n.t(capability?.kind==='default-application'?'已添加':'仅使用默认接入');
      button.onclick=()=>{if(button.disabled)return;dialog.close();return this.clientProfile(family,capability);};actions.append(button);
    }
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent=BridgeI18n.t('取消');cancel.onclick=()=>dialog.close();actions.append(cancel);dialog.append(actions);
    dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();cancel.focus();
  }
  codexProfile(){return this.clientProfile('codex');}
  async clientProfile(family,capability){
    const label=({codex:'Codex',claude:'Claude',deepseek:'DSH',zcode:'ZCode',antigravity:'Antigravity'})[family];if(!label)return;
    try{if(!capability)capability=(await this.api.desktopSessions({action:'client-profiles',operation:'list'})).capabilities?.find(row=>row.family===family);}
    catch(error){this.feedback(error.message,true);return;}
    if(capability?.supported!==true){this.feedback(BridgeI18n.t(capability?.reason||'暂不可用'),true);return;}
    const kind=capability.kind,application=kind==='default-application',t=BridgeI18n.t,dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
    const title=document.createElement('h2');title.textContent=t('添加 Codex 客户端').replace('Codex',label);
    const form=document.createElement('form'),nameLabel=document.createElement('label'),homeLabel=document.createElement('label');
    form.className='client-config-form';nameLabel.textContent=t('客户端名称');homeLabel.textContent=application?t('应用程序路径'):t('Codex 配置目录').replace('Codex',label);
    const name=document.createElement('input'),home=document.createElement('input');name.required=true;name.maxLength=80;name.value=t('Codex 独立配置').replace('Codex',label);home.required=true;home.placeholder=application?t('选择应用程序，或输入完整路径'):this.configurationExample(family);nameLabel.append(name);homeLabel.append(home);
    const help=document.createElement('p');help.className='hint';help.textContent=application?(family==='antigravity'?t('选择已安装的 Antigravity App、IDE 或官方 agy 程序，保留原客户端的账号和会话。'):t('选择已安装的 ZCode 应用，保存后在管理页选择连接方式并启用。')):t('选择此电脑上的独立 Codex 配置目录，保留其中的账号和会话。默认 Codex 接入保持不变。').replaceAll('Codex',label);
    const actions=document.createElement('div');actions.className='actions';
    const choose=document.createElement('button'),scan=document.createElement('button'),save=document.createElement('button'),cancel=document.createElement('button');
    choose.type=scan.type=cancel.type='button';save.type='submit';choose.textContent=t(application?'选择应用程序':'选择文件夹');scan.textContent=t('扫描配置');scan.hidden=application;save.textContent=t('保存配置');cancel.textContent=t('关闭');
    const error=document.createElement('p');error.className='error';error.setAttribute('role','alert');
    const candidates=document.createElement('div');candidates.className='client-config-candidates';let busy=false,closed=false;
    const run=async task=>{if(busy||closed)return;busy=true;error.textContent='';for(const b of [choose,scan,save])b.disabled=true;try{await task();}catch(e){if(!closed)error.textContent=t(e.message);}finally{busy=false;for(const b of [choose,scan,save])b.disabled=false;}};
    choose.onclick=()=>run(async()=>{const path=await this.api.choose(application?'file':'folder');if(path&&!closed)home.value=path;});
    scan.onclick=()=>run(async()=>{const value=await this.api.desktopSessions({action:'client-profiles',operation:'scan',family,kind});if(closed)return;candidates.replaceChildren();const rows=(value.candidates||[]).filter(row=>row.family===family&&row.kind===kind);if(!rows.length){candidates.textContent=t('未发现新的配置目录，可选择文件夹或手动输入路径。');return;}for(const row of rows){const button=document.createElement('button');button.type='button';button.textContent=row.home;button.onclick=()=>{home.value=row.home;name.value=row.label;};candidates.append(button);}});
    form.onsubmit=event=>{event.preventDefault();return run(async()=>{const value=await this.api.desktopSessions({action:'client-profiles',operation:'save',family,kind,...(application?{application:home.value}:{home:home.value,label:name.value})});dialog.close();await this.refreshClients(true);this.feedback(t('Codex 客户端已保存').replace('Codex',label));if(value.instance?.id)await this.manageClient(this.managementClients().find(row=>row.id===value.instance.id)||value.instance);});};
    cancel.onclick=()=>dialog.close();actions.append(choose,scan,save,cancel);form.append(...(application?[]:[nameLabel]),homeLabel,help,candidates,error,actions);dialog.append(title,form);dialog.addEventListener('close',()=>{closed=true;dialog.remove();});document.body.append(dialog);dialog.showModal();
    home.focus();
  }
  visibleClients(){
    return this.managementClients().filter(client=>client.installed||client.configured||client.enabled||client.connected||client.running||client.kind==='desktop-profile'||client.bindingMode==='manual');
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
      const label=node('div');label.append(node('strong',client.name),node('small',client.description|| (client.kind==='wsl-claude-cli'?'WSL Claude Code':client.kind==='wsl-cli'?'WSL Codex CLI':({codex:'Codex Desktop',claude:'Claude Desktop',deepseek:'DeepSeek Harness',antigravity:'Google Antigravity'})[client.id])));name.append(logo,label);identity.append(name);
      const access=node('td'),toggle=node('input');toggle.type='checkbox';toggle.className='client-toggle';toggle.checked=client.enabled;toggle.disabled=(!client.enabled&&!(client.selectable??client.configured))||this.scanning||this.busy||!!this.choosingClient;toggle.setAttribute('aria-label',t('启用')+' '+client.name);toggle.title=t(client.reason);toggle.onchange=()=>this.toggleClient(client.id,toggle.checked);access.append(toggle,node('small',this.pendingClient?.id===client.id?(this.gatewayRunning===false?'正在保存…':this.pendingClient.enabled?(client.id==='claude'?'正在连接…':'正在打开应用…'):this.pendingClient.forceDesktop?'正在后台强制结束…':this.pendingClient.quitDesktop?'正在退出应用…':'正在停用接入…'):client.setupStatus==='unsupported'?'暂不可用':client.enabled?(this.gatewayRunning===false?'随网关启动':'已启用'):!client.installed&&!client.configured?'待配置':'未启用'));
      toggle.disabled=toggle.disabled||this.hasUncertain();const state=this.connection(client),failure=this.operationFailures?.get(client.id),status=node('td'),stateLabel=node('span',state.label,state.waiting?'state-pending client-connection-waiting':state.ready?'state-ready':state.error?'state-error':state.action?'state-pending':'');if(state.modeLabel)stateLabel.textContent+=' · '+BridgeI18n.t(state.modeLabel);stateLabel.setAttribute('role','status');stateLabel.setAttribute('aria-busy',String(state.waiting));status.append(stateLabel,node('small',failure?ClientLifecycle.failureMessage(failure):state.reason));
      const account=node('td');account.dataset.clientAccount=client.id;
      if(client.id!=='codex')account.append(node('span',client.configured?'已配置':'待配置'),node('small',client.accountNotice|| (client.kind==='codex-local'?'独立管理账号与 API':['wsl-cli','wsl-claude-cli'].includes(client.kind)?'在网关中管理 WSL 账号与 API':'在网关管理账号与API')));
      const actions=node('td'),buttons=node('div',undefined,'connection-actions'),manage=node('button',client.setupStatus==='unsupported'?'查看详情':client.configured?'管理':'配置');
      if(failure){const retry=node('button',failure.uncertain?'刷新状态':ClientLifecycle.retryLabel(failure.action),'primary');retry.type='button';retry.disabled=this.busy||this.scanning||!!this.choosingClient||!!this.clientsLoading||(!failure.uncertain&&this.hasUncertain());retry.onclick=()=>failure.uncertain?this.refreshClients(true):this.retryOperation(client.id);buttons.append(retry);}
      if(!failure&&state.retryable&&this.gatewayRunning!==false){const retry=node('button','重试连接','primary');retry.type='button';retry.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();retry.onclick=()=>this.toggleClient(client.id,true);buttons.append(retry);}
      if(!failure?.uncertain&&client.id==='zcode'&&state.canInitialize&&this.gatewayRunning!==false){
        const initialize=node('button',state.initializeLabel);initialize.type='button';initialize.disabled=this.busy||this.scanning||!!this.choosingClient||this.hasUncertain();initialize.onclick=()=>this.toggleClient(client.id,true,{initializeDesktop:true});buttons.append(initialize);
      }
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
      manage.type='button';manage.onclick=()=>this.manageClient(client);buttons.append(manage);actions.append(buttons);row.append(identity,access,status,account,actions);body.append(row);
    }
    root.replaceChildren(this.visibleClients().length?table:node('p','尚未发现客户端，请添加客户端或重新扫描。','hint'));this.renderAccount(this.accountValue);
    for(const [provider,panel] of Object.entries(this.accountPanels)){this.renderClientAccount(provider,panel.value);}
    for(const [id,panel] of this.connectionModePanels||[])panel.render(this.managementClients().find(row=>row.id===id));
    this.resumeAccounts();this.onClientsRendered?.();this.syncPolling();
  }
  async manageClient(client){
    const layout=window.GatewayLayout,request=this.managementRequest=(this.managementRequest||0)+1;
    try{
      if(!layout.hasClient(client.id)){
        const wsl=['wsl-cli','wsl-claude-cli'].includes(client.kind);
        if(wsl&&typeof WslAccountsPanel==='undefined')await window.loadWslAccounts();
        if(request!==this.managementRequest)return;
        const page=layout.clientPage(client.id),article=document.createElement('article'),title=document.createElement('h2'),description=document.createElement('p'),notice=document.createElement('p'),content=document.createElement('div');
        title.textContent=client.name;description.textContent=BridgeI18n.t(client.description||'');notice.textContent=BridgeI18n.t(client.configurationNotice||'');notice.hidden=!client.configurationNotice;article.append(title,description,notice,content);page.append(article);
        if(ClientLifecycle.connectionModes(client).length>1)this.mountConnectionModes(article,content,client);
        if(client.id==='zcode')this.mountZcodeOfficial(article,content);
        if(wsl){
          const provider=client.kind==='wsl-claude-cli'?'claude':'codex',entry=client.id;
          const panel=new WslAccountsPanel({root:content,provider,desktop:true,
            read:selected=>this.api.cliAccess({action:'wslAccounts',provider,entry:selected,operation:'list'}),
            request:({action,provider:_provider,entry:_entry,...fields})=>this.api.cliAccess({action:'wslAccounts',provider,entry,operation:action,...fields})});
          this.managementPages.set(entry,{panel,open:()=>panel.select(entry)});
        }else if(client.id==='antigravity'&&client.capabilities?.management===true){
          this.mountAntigravityManagement(content,client);
        }else if(client.capabilities?.accounts===true){
          const provider=client.family||client.id,entry=client.kind==='desktop-profile'?client.id:null,scope=entry?{entry}:{},panel=new DesktopClientAccountsPanel({root:content,provider,desktop:true,
            read:()=>this.api.desktopSessions({action:'accounts',provider,...scope,operation:'list'}),
            request:({action,provider:_provider,entry:_entry,...value})=>this.api.desktopSessions({action:'accounts',provider,...scope,operation:action,...value}),
            readModels:refresh=>this.api.desktopSessions({action:'account',provider,...scope,refresh}),
            visible:()=>!document.hidden&&!page.hidden&&!page.classList.contains('client-config-hidden'),
            onUpdate:value=>this.renderClientAccount(client.id,value),onChanged:()=>this.refreshClients(true)});
          this.accountPanels[client.id]=panel;this.managementPages.set(client.id,{panel,open:()=>panel.resume()});
        }
      }
      if(['codex-local','desktop-profile'].includes(client.kind))this.mountClientRemoval(layout.clientPage(client.id),client);
      layout.selectClient(client.id);this.managementPages.get(client.id)?.open();
      if(client.id==='zcode')await this.zcodeOfficial?.refresh();
    }catch(error){this.feedback(error.message,true);}
  }
  mountClientRemoval(page,client){
    if(this.removalControls.has(client.id))return;
    const section=document.createElement('article'),hint=document.createElement('p'),button=document.createElement('button');
    hint.className='hint';hint.dataset.i18n='移除仅取消网关中的客户端登记，保留原应用、账号、聊天和配置文件。';hint.textContent=BridgeI18n.t(hint.dataset.i18n);
    button.type='button';button.dataset.i18n='移除此客户端配置';button.textContent=BridgeI18n.t(button.dataset.i18n);button.onclick=()=>this.removeClient(client.id);
    section.dataset.removeClient=client.id;section.append(hint,button);page.append(section);this.removalControls.set(client.id,section);
  }
  async removeClient(id){
    if(this.busy||this.scanning||this.removingClient)return;
    const client=this.managementClients().find(row=>row.id===id);if(!client||!['codex-local','desktop-profile'].includes(client.kind))return;
    if(client.enabled){this.feedback(BridgeI18n.t('请先停用此客户端的手机接入，再移除配置'),true);return;}
    if(!window.confirm(BridgeI18n.t('确认移除此客户端配置？原应用、账号、聊天和配置文件都会保留。')+'\n'+client.name))return;
    this.removingClient=id;const control=this.removalControls.get(id),button=control?.querySelector('button');if(button)button.disabled=true;
    try{
      await this.api.desktopSessions({action:'client-profiles',operation:'remove',family:client.family||'codex',entry:id,confirmed:true});
      this.accountPanels[id]?.dispose();delete this.accountPanels[id];this.managementPages.delete(id);control?.remove();this.removalControls.delete(id);
      window.GatewayLayout.forgetClient(id);await this.refreshClients(true);this.feedback(BridgeI18n.t('客户端配置已移除'));
    }catch(error){this.feedback(error.message,true);}
    finally{this.removingClient=null;if(button)button.disabled=false;}
  }
  mountConnectionModes(article,before,client){
    const section=document.createElement('section');section.className='accounts-panel';section.dataset.clientConnectionModes=client.id;article.insertBefore(section,before);
    const methods=ClientLifecycle.mountConnectionModes(section);
    const render=value=>methods.update(value,{disabled:this.busy||this.scanning||!!this.choosingClient||this.hasUncertain(),select:mode=>this.selectConnectionMode(client.id,mode)});
    this.connectionModePanels??=new Map();this.connectionModePanels.set(client.id,{render});render(client);
  }
  mountAntigravityManagement(root,client){
    const node=(tag,text,cls='')=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=BridgeI18n.t(text);return n;};
    root.className='accounts-panel';root.dataset.antigravityManagement='';
    const heading=node('div',undefined,'accounts-toolbar'),refresh=node('button','重新加载');refresh.type='button';heading.append(node('h3','聊天与模型'),refresh);
    const status=node('p',undefined,'account-muted'),error=node('p',undefined,'error'),plugin=node('p',undefined,'account-muted');status.setAttribute('role','status');error.setAttribute('role','alert');
    const form=node('div',undefined,'cli-access-form'),field=(title,control)=>{const label=node('label');label.append(node('span',title),control);form.append(label);control.setAttribute('aria-label',BridgeI18n.t(title));};
    const source=node('select'),session=node('select'),model=node('select'),permission=node('select');field('会话来源',source);field('聊天',session);field('发送模型',model);field('审批方式',permission);
    const modelActions=node('div',undefined,'actions'),models=node('button','刷新模型'),saveModel=node('button','应用模型'),savePermission=node('button','应用审批方式');for(const b of [models,saveModel,savePermission])b.type='button';modelActions.append(models,saveModel,savePermission);
    const help=node('p','配置仅应用于所选来源的这条聊天。模型来自原客户端；审批需要原会话加载网关 Hook。账号与登录在原客户端管理。','account-muted');root.append(heading,status,error,plugin,form,modelActions,help);
    let value=null,catalog=null,busy=false,stale=true;
    const currentSource=()=>value?.surfaces?.find(row=>row.id===source.value),currentSession=()=>currentSource()?.sessions?.find(row=>row.id===session.value);
    const option=(label,id)=>{const n=node('option',label);n.value=id;return n;};
    const render=()=>{
      const origin=currentSource(),row=currentSession(),enabled=value?.enabled===true;
      status.textContent=BridgeI18n.t(busy?'正在读取配置…':origin?(origin.historyAvailable?'本机历史可用':'未发现本机历史')+' · '+(origin.connected?'原生服务已连接':'原生服务未连接'):'');
      plugin.textContent=BridgeI18n.t(value?.plugin?.active?'网关 Hook 已启用；原会话加载后可使用审批与排队。':value?.plugin?.enabled?'网关 Hook 已安装；启用接入后生效。':'启用 Antigravity 接入后可安装并启用网关 Hook。');
      for(const control of [source,session,model,permission,models,saveModel,savePermission,refresh])control.disabled=busy;
      session.disabled=busy||!origin?.sessions?.length;model.disabled=busy||stale||!enabled||!origin?.connected||!catalog?.models?.length;
      models.disabled=busy||stale||!enabled||!origin?.connected||!row;
      saveModel.disabled=model.disabled||catalog?.capabilities?.models!==true||!catalog?.cache?.identity||!model.value||model.value===row?.model;
      permission.disabled=busy||stale||!enabled||!row||value?.plugin?.active!==true;
      savePermission.disabled=permission.disabled||permission.value===row?.permissionMode;
      if(!busy&&!error.textContent&&catalog?.notice)status.textContent+=' · '+BridgeI18n.t(catalog.notice);
    };
    const selectSession=()=>{catalog=null;const row=currentSession();model.replaceChildren(option(row?.model||'请先刷新模型',row?.model||''));model.value=row?.model||'';permission.replaceChildren(...(value?.permissionOptions||[]).map(o=>option(o.label,o.value)));permission.value=row?.permissionMode||'desktop';render();};
    const selectSource=()=>{const rows=currentSource()?.sessions||[],previous=session.value;session.replaceChildren(...rows.map(row=>option(row.title||'新会话',row.id)));session.value=rows.some(row=>row.id===previous)?previous:rows[0]?.id||'';selectSession();};
    const read=async()=>{value=await this.api.desktopSessions({action:'antigravity-management',operation:'list'});stale=false;const previous=source.value;source.replaceChildren(...(value.surfaces||[]).map(row=>option(row.name,row.id)));source.value=value.surfaces?.some(row=>row.id===previous)?previous:value.surfaces?.find(row=>row.historyAvailable)?.id||value.surfaces?.[0]?.id||'';selectSource();};
    const run=async task=>{if(busy)return;busy=true;error.textContent='';render();try{await task();}catch(e){stale=true;catalog=null;error.textContent=BridgeI18n.t(e.message);}finally{busy=false;render();}};
    refresh.onclick=()=>run(read);source.onchange=selectSource;session.onchange=selectSession;model.onchange=permission.onchange=render;
    models.onclick=()=>{if(models.disabled)return;return run(async()=>{const origin=source.value,sid=session.value;catalog=await this.api.desktopSessions({action:'antigravity-management',operation:'catalog',surface:origin,sessionId:sid,refresh:true});model.replaceChildren(option('选择模型',''),...(catalog.models||[]).map(row=>option(row.name||row.id,row.id)));model.value=(catalog.models||[]).some(row=>row.id===currentSession()?.model)?currentSession().model:'';});};
    const save=operation=>{const button=operation==='settings'?saveModel:savePermission;if(button.disabled)return;return run(async()=>{const result=await this.api.desktopSessions({action:'antigravity-management',operation,surface:source.value,sessionId:session.value,id:crypto.randomUUID(),...(operation==='settings'?{model:model.value,modelCatalogIdentity:catalog.cache.identity}:{mode:permission.value})});if(result.status!=='accepted')throw Error(result.message||'配置结果待核对，请重新加载');await read();this.feedback(BridgeI18n.t('配置已保存'));});};
    saveModel.onclick=()=>save('settings');savePermission.onclick=()=>save('access');
    this.managementPages.set(client.id,{open:()=>run(read)});render();
  }
  mountZcodeOfficial(article,before){
    const node=(tag,text,cls='')=>{const value=document.createElement(tag);value.className=cls;if(text!==undefined)value.textContent=BridgeI18n.t(text);return value;};
    const section=node('section',undefined,'accounts-panel'),heading=node('div',undefined,'account-heading');section.dataset.zcodeOfficial='';
    const title=node('h3','官方远控（实验）'),refresh=node('button','刷新状态','accounts-refresh');heading.append(title,refresh);
    const help=node('p','使用与此电脑 ZCode 相同的官方账号，并在原 App 中开启 Web 远程控制。会话数据经过 ZCode 官方中继。','account-muted');
    const guide=node('details',undefined,'notification-guide guide-more');guide.dataset.zcodeOfficialGuide='';guide.open=true;
    const steps=node('ol',undefined,'setup-steps');
    for(const [title,description] of [
      ['启动 ZCode 并登录','打开此电脑的 ZCode，登录官方账号。API Key 不能代替官方账号登录。'],
      ['打开移动端远程控制','在 ZCode 左下角打开“移动端远程控制”，开启远控并复制此电脑的连接链接。'],
      ['保存官方远控链接','将链接粘贴到下方“官方远控链接”，点击“保存连接配置”。'],
      ['在网关登录同一账号','点击“登录官方账号”，使用与原 App 相同的官方站点和登录方式，确认登录的是同一账号。'],
      ['连接官方远控','点击“连接官方远控”，在确认框中选择使用官方远控。仅完成登录还不等于已连接。'],
      ['启动网关并连接手机','网关尚未运行时，在“概览”点击“启动网关”；已运行则无需重启。随后用手机 App 或 Web 连接此网关。'],
      ['确认连接成功','网关应显示“官方远控已连接”，ZCode 左下角远控标识变为绿色。黄色表示启动、连接或等待接入，红色表示错误。']
    ]){const item=node('li');item.append(node('strong',title),node('p',description));steps.append(item);}
    guide.append(node('summary','首次连接步骤'),steps,node('p','原 App 的绿色标识可能来自其他已连接终端，请同时核对本网关的连接状态。','hint'));
    const status=node('p'),reason=node('p',undefined,'hint'),saved=node('p',undefined,'hint');status.setAttribute('role','status');reason.setAttribute('role','status');saved.setAttribute('role','status');
    const label=node('label'),link=node('input');link.type='url';link.autocomplete='off';link.spellcheck=false;link.maxLength=4096;link.placeholder=BridgeI18n.t('粘贴此电脑的 Web 远程控制链接');label.append(node('span','官方远控链接'),link);
    const actions=node('div',undefined,'actions'),buttons={};
    for(const [action,text] of [['save','保存连接配置'],['login','登录官方账号'],['connect','连接官方远控'],['disconnect','断开官方远控'],['forget','清除官方登录']]){
      const button=node('button',text);button.type='button';button.dataset.officialAction=action;buttons[action]=button;actions.append(button);
    }
    refresh.type='button';section.append(heading,help,guide,status,reason,label,saved,actions);article.insertBefore(section,before);
    let busy=false,pendingAction='',loadedLink='',error='',guidePending=true;
    const panel=this.zcodeOfficial={value:null};
    const render=()=>{
      const value=panel.value,unavailable=!value,edited=link.value.trim()!==loadedLink;
      if(value&&guidePending){guide.open=!value.configured;guidePending=false;}
      status.textContent=value?[(value.configured?BridgeI18n.t('已登录')+(value.accountName?' · '+value.accountName:'')+(value.provider?' · '+value.provider:''):BridgeI18n.t('尚未登录官方账号')),
        BridgeI18n.t(value.connected?'官方远控已连接':'官方远控未连接')].join('；'):'';
      reason.textContent=BridgeI18n.t(busy?({state:'正在刷新状态…',save:'正在保存连接配置…',login:'正在打开官方登录…',connect:'正在连接官方远控…',disconnect:'正在断开官方远控…',forget:'正在清除官方登录…'})[pendingAction]:error||value?.reason||'');reason.className=error?'error':'hint';
      saved.textContent=BridgeI18n.t(edited?'连接配置已修改，尚未保存。':loadedLink?'连接配置已保存。登录失效时需要重新验证。':'请保存此电脑的官方远控链接，再完成官方登录。');label.dataset.dirty=String(edited);
      link.disabled=busy;refresh.disabled=busy;for(const button of Object.values(buttons))button.disabled=busy||unavailable;
      buttons.save.disabled=busy||unavailable||!link.value.trim()||!edited;buttons.login.disabled=busy||unavailable||!loadedLink||edited;buttons.connect.disabled=busy||unavailable||!value?.configured||!loadedLink||edited||value?.connected===true;
      buttons.disconnect.disabled=busy||!value?.connected;buttons.forget.disabled=busy||!value?.configured;
    };
    const perform=async action=>{
      if(busy||this.busy||this.scanning||this.choosingClient)return;
      if(action!=='state'&&buttons[action].disabled)return;
      if(action==='connect')return this.selectConnectionMode('zcode','official-remote');
      if(action==='forget'&&!window.confirm(BridgeI18n.t('清除网关保存的官方登录并断开官方远控？已保存的链接、ZCode 原 App 的账号和任务会保留。')))return;
      const edited=link.value!==loadedLink;busy=true;pendingAction=action;error='';this.busy=true;render();this.renderClients();this.renderScan();
      try{
        const value=await this.api.desktopSessions({action:'zcode-official-'+action,...(['save','login','connect'].includes(action)?{link:link.value.trim()}:{})});
        panel.value=value;if(!edited||['save','forget'].includes(action))link.value=value.link||'';loadedLink=value.link||'';
      }catch(failure){error=failure.message;this.feedback(error,true);panel.value=null;if(action!=='state')try{panel.value=await this.api.desktopSessions({action:'zcode-official-state'});}catch(_){}}
      finally{busy=false;this.busy=false;render();this.renderClients();this.renderScan();}
      if(!['state','login'].includes(action))await this.refreshClients(true);
    };
    for(const [action,button] of Object.entries(buttons))button.onclick=()=>perform(action);
    refresh.onclick=panel.refresh=()=>perform('state');link.oninput=render;render();
  }
  resumeAccounts(){
    if(!document.hidden&&!document.getElementById('clients-page').hidden)
      for(const client of this.clients||[])if(client.nativeEnabled??client.enabled)this.ensureAccountPanel(client.id);
    for(const panel of Object.values(this.accountPanels))panel.resume();
  }
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
