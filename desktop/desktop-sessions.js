'use strict';
class DesktopConnectionsPanel {
  constructor({root,api,feedback}) {
    Object.assign(this,{root,api,feedback,clientsRevision:0,statusRevision:0});
    this.deepseekHome=root.querySelector('[data-deepseek-home]');
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
        readModels:()=>api.desktopSessions({action:'account',provider}),
        visible:()=>!document.hidden&&!document.getElementById('clients-page').hidden&&(!document.getElementById('clients-page').classList.contains('configuration-open')||!article.classList.contains('client-config-hidden')),
        onUpdate:value=>this.renderClientAccount(provider,value),onChanged:()=>this.refreshClients(true)});
    }
    document.addEventListener('bridge-client-config',()=>this.resumeAccounts());
  }
  renderDetails(){
    const value=this.connectionValue;
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
      if(client?.backgroundRunning){const reconnect=document.createElement('button');reconnect.type='button';reconnect.textContent=BridgeI18n.t('恢复并重新接入');reconnect.disabled=this.busy||this.scanning||!!this.choosingClient;reconnect.onclick=()=>this.recoverDeepseek(true);actions.append(reconnect);}
      if(client?.running){const quit=document.createElement('button');quit.type='button';quit.textContent=BridgeI18n.t('完整退出');quit.disabled=this.busy||this.scanning||!!this.choosingClient;quit.onclick=()=>this.recoverDeepseek(false);actions.append(quit);}
    }
  }
  async refresh(){
    const revision=++this.statusRevision;
    try{
      const value=await this.api.desktopSessions({action:'status'});if(revision!==this.statusRevision)return;
      this.connectionValue=value;this.statusError='';if(document.activeElement!==this.deepseekHome)this.deepseekHome.value=value.deepseekHome||'';
      this.renderDetails();
    }catch(error){if(revision!==this.statusRevision)return;this.statusError=error.message;this.scanMessage=error.message;this.scanError=true;this.renderScan();this.renderDetails();}
  }
  async refreshClients(refresh=false){
    const root=document.getElementById('client-overview');if(!root||this.scanning||this.choosingClient||(this.busy&&!refresh))return;
    if(this.clientsLoading){await this.clientsLoading;if(refresh)return this.refreshClients(true);return;}
    const revision=this.clientsRevision;
    const pending=(async()=>{
      try{const value=await this.api.desktopSessions({action:'clients',refresh});if(revision!==this.clientsRevision)return;this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.renderClients();}
      catch(error){if(revision!==this.clientsRevision)return;this.scanMessage=error.message;this.scanError=true;this.renderScan();}
    })();
    this.clientsLoading=pending;try{await pending;}finally{if(this.clientsLoading===pending)this.clientsLoading=null;}
  }
  connection(client){return ClientLifecycle.connection({...client,pendingEnable:this.gatewayRunning!==false&&this.pendingClient?.id===client.id&&this.pendingClient.enabled});}
  pollClients(fast=false){
    if(document.hidden||document.getElementById('clients-page').hidden||(fast&&!this.clients?.some(client=>this.connection(client).waiting)))return;
    return this.refreshClients();
  }
  async scan(manual=false){
    if(this.scanning||this.busy||this.choosingClient)return;this.scanning=true;this.clientsRevision++;this.statusRevision++;this.scanMessage='正在扫描本机客户端…';this.scanError=false;this.renderScan();
    try{const value=await this.api.desktopSessions({action:'scan',setup:false});if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.renderClients();}this.scanMessage='扫描完成；各客户端的接入状态如下。';}
    catch(error){this.scanMessage=error.message;this.scanError=true;if(manual)this.feedback(error.message,true);}
    finally{this.scanning=false;this.renderScan();await this.refreshClients(true);await this.refresh();}
  }
  renderScan(){
    const node=document.getElementById('client-scan-status');if(node){node.textContent=BridgeI18n.t(this.scanMessage||'选择需要接入的应用；Claude 完全退出或重新加载后需在电脑端初始化。');node.classList.toggle('error',!!this.scanError);}
    const button=document.getElementById('refresh-clients');if(button){button.disabled=!!this.scanning||!!this.busy||!!this.choosingClient;button.textContent=BridgeI18n.t(this.scanning?'正在扫描…':'重新扫描');}
    this.root.querySelectorAll('[data-desktop-action="scan"]').forEach(button=>button.disabled=!!this.scanning||!!this.choosingClient);
  }
  async toggleClient(id,enabled){
    const client=this.clients?.find(row=>row.id===id);
    if(this.busy||this.scanning||this.choosingClient||!client||(enabled&&!(client.selectable??client.configured)))return;
    let quitDesktop=false;
    if(!enabled&&this.gatewayRunning!==false){
      this.choosingClient=id;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();
      try{quitDesktop=await ClientLifecycle.chooseDisable(client);}
      finally{this.choosingClient=null;this.renderClients();this.renderScan();}
      if(quitDesktop===null)return;
    }
    const before=this.clients;
    this.busy=true;this.pendingClient={id,enabled,quitDesktop};this.clientsRevision++;this.statusRevision++;
    this.clients=this.clients.map(client=>client.id===id?{...client,enabled}:client);
    this.renderClients();this.renderScan();
    try{const value=await this.api.desktopSessions({action:'toggle-client',provider:id,enabled,...(!enabled?{quitDesktop}:{})});if(value.clients)this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;}
    catch(error){this.clients=before;this.feedback(error.message,true);}
    finally{this.busy=false;this.pendingClient=null;this.renderClients();this.renderScan();await this.refreshClients(true);}
  }
  renderClients(){
    this.renderDetails();
    const root=document.getElementById('client-overview'),t=BridgeI18n.t;if(!root||!this.clients)return;
    const node=(tag,text,cls='')=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=t(text);return n;};
    const table=node('table',undefined,'connection-table'),head=node('thead'),tr=node('tr'),body=node('tbody');
    for(const title of ['客户端','应用开关','连接状态','当前接入 / 剩余额度','管理'])tr.append(node('th',title));head.append(tr);table.append(head,body);
    for(const client of this.clients){
      const row=node('tr'),identity=node('td'),name=node('div',undefined,'connection-name'),logo=node('span',undefined,'client-logo');
      const img=node('img');img.src='../web/client-icons/'+client.id+'.png';img.alt='';logo.append(img);
      const label=node('div');label.append(node('strong',client.name),node('small',({codex:'Codex Desktop',claude:'Claude Desktop',deepseek:'DeepSeek Harness'})[client.id]));name.append(logo,label);identity.append(name);
      const access=node('td'),toggle=node('input');toggle.type='checkbox';toggle.className='client-toggle';toggle.checked=client.enabled;toggle.disabled=(!client.enabled&&!(client.selectable??client.configured))||this.scanning||this.busy||!!this.choosingClient;toggle.setAttribute('aria-label',t('启用')+' '+client.name);toggle.title=t(client.reason);toggle.onchange=()=>this.toggleClient(client.id,toggle.checked);access.append(toggle,node('small',this.pendingClient?.id===client.id?(this.gatewayRunning===false?'正在保存…':this.pendingClient.enabled?(client.id==='claude'?'正在准备后台连接…':'正在打开应用…'):this.pendingClient.quitDesktop?'正在退出应用…':'正在停用接入…'):client.setupStatus==='unsupported'?'暂不可用':client.enabled?(this.gatewayRunning===false?'随网关启动':'已启用'):!client.installed&&!client.configured?'待配置':'未启用'));
      const state=this.connection(client),status=node('td'),stateLabel=node('span',state.label,state.waiting?'state-pending client-connection-waiting':client.connected?'state-ready':state.error?'state-error':state.action?'state-pending':'');stateLabel.setAttribute('role','status');stateLabel.setAttribute('aria-busy',String(state.waiting));status.append(stateLabel,node('small',state.reason));
      const account=node('td');account.dataset.clientAccount=client.id;
      if(client.id!=='codex')account.append(node('span',client.configured?'已配置':'待配置'),node('small',client.id==='claude'?'在 Claude 中管理账号':'在 Harness 中管理账号 / API'));
      const actions=node('td'),buttons=node('div',undefined,'connection-actions'),manage=node('button',client.setupStatus==='unsupported'?'查看详情':client.configured?'管理':'配置');
      if(state.retryable&&this.gatewayRunning!==false){const retry=node('button','重试连接','primary');retry.type='button';retry.disabled=this.busy||this.scanning||!!this.choosingClient;retry.onclick=()=>this.toggleClient(client.id,true);buttons.append(retry);}
      if(client.id==='deepseek'&&client.backgroundRunning&&(!client.mainRunning||['restart-required','failed'].includes(client.setupStatus))){
        const recover=node('button','恢复并重新接入','primary');recover.type='button';recover.disabled=this.busy||this.scanning||!!this.choosingClient;recover.onclick=()=>this.recoverDeepseek(true);buttons.append(recover);
      }else if(this.gatewayRunning!==false&&client.id==='deepseek'&&['restart-required','needs-first-launch'].includes(client.setupStatus)){
        const restart=client.setupStatus==='restart-required',connect=node('button',restart?'重启并接入':'打开并接入','primary');connect.type='button';connect.disabled=!!this.busy||!!this.scanning||!!this.choosingClient;connect.onclick=()=>this.action(restart?'restart-deepseek':'connect-deepseek');buttons.append(connect);manage.textContent=t('管理');manage.className='connection-secondary';
      }
      if(this.gatewayRunning!==false&&client.id==='claude'&&client.installed&&!client.connected&&client.setupStatus!=='unsupported'){
        const restart=client.setupStatus==='restart-required',pending=['connecting','needs-developer-mode'].includes(client.setupStatus);
        const connect=node('button',pending?'取消连接':restart?'重启并接入':client.setupStatus==='needs-permission'?'授权并连接':client.setupStatus==='needs-initialization'?'手动连接':'连接 Claude',pending?'':'primary');
        connect.type='button';connect.disabled=!!this.busy||!!this.scanning||!!this.choosingClient;connect.onclick=()=>this.action(pending?'cancel-claude':restart?'restart-claude':'connect-claude');buttons.append(connect);manage.textContent=t('查看详情');manage.className='connection-secondary';
      }
      manage.type='button';manage.onclick=()=>window.GatewayLayout.selectClient(client.id);buttons.append(manage);actions.append(buttons);row.append(identity,access,status,account,actions);body.append(row);
    }
    root.replaceChildren(table);this.renderAccount(this.accountValue);
    for(const [provider,panel] of Object.entries(this.accountPanels)){this.renderClientAccount(provider,panel.value);}
    this.resumeAccounts();
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
    const row=value?.accounts?.find(row=>row.id===value.activeId);if(row?.kind!=='chatgpt')return;
    const usage=row.details?.usage;if(usage?.status==='ready'){const credits=document.createElement('small');credits.textContent=t('重置卡')+' · '+(usage.resetCredits?.availableCount??t('暂未提供'));cell.append(credits);}for(const bucket of usage?.limits||[])for(const window of bucket.windows||[]){const note=document.createElement('small');note.textContent=bucket.name+' · '+(window.remainingPercent==null?t('暂未提供'):t('剩余 ')+Math.round(window.remainingPercent)+'%');cell.append(note);if(window.remainingPercent!=null){const bar=document.createElement('progress');bar.max=100;bar.value=window.remainingPercent;cell.append(bar);}}
    const refresh=document.createElement('button');refresh.type='button';refresh.className='quota-link';refresh.textContent=t('查看剩余额度');refresh.disabled=usage?.status==='loading';refresh.onclick=async()=>{refresh.disabled=true;try{this.renderAccount(await this.api.accounts({action:'details',id:row.id,section:'usage',refresh:true}));}catch(error){this.feedback(error.message,true);refresh.disabled=false;}};cell.append(refresh);
  }
  async recoverDeepseek(restart){
    if(this.busy||this.scanning||this.choosingClient)return;this.busy=true;this.clientsRevision++;this.statusRevision++;this.renderClients();this.renderScan();
    try{
      const preview=await this.api.desktopSessions({action:'deepseek-recovery-preview',restart});
      if(preview.busy)throw Error(preview.message||'DSH 仍有任务运行或等待确认，请先在桌面结束任务');
      if(!preview.canRecover)throw Error(preview.message||'当前 DSH 进程无法安全恢复，请在桌面检查后重新扫描');
      const t=BridgeI18n.t,prompt=[t(restart?'将完整退出 DSH 的桌面和后台进程，更新接入后重新打开。':'将完整退出 DSH 的桌面和后台进程。'),t('桌面进程：')+(preview.guiCount||0)+' · '+t('后台进程：')+(preview.backgroundCount||0),preview.requiresUnknownConfirmation?t('部分后台进程无法核验任务状态。确认继续表示你已检查并结束任务、保存工作。'):t('请确认已保存工作。')].join('\n\n');
      if(!window.confirm(prompt))return;
      const value=await this.api.desktopSessions({action:'deepseek-recovery-confirm',token:preview.token,confirmed:true,...(preview.requiresUnknownConfirmation?{acknowledgeUnknown:true}:{})});
      if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;}this.feedback(restart?(value.clients?.find(row=>row.id==='deepseek')?.connected?'DSH 已重新接入':'正在等待 Harness 连接'):'DSH 已完整退出');
    }catch(error){this.feedback(error.message,true);}
    finally{this.busy=false;this.renderClients();this.renderScan();await this.refreshClients(true);await this.refresh();}
  }
  async action(action){
    if(this.busy||this.scanning||this.choosingClient)return;
    if(action==='restart-deepseek'&&!window.confirm(BridgeI18n.t('重启 Harness 会断开当前会话。请先在 Harness 中结束所有任务并保存工作，再确认重启并接入。')))return;
    if(action==='restart-claude'&&!window.confirm(BridgeI18n.t('重启 Claude 会断开当前会话。请先结束任务并保存工作，再确认重启并接入。')))return;
    this.busy=true;this.clientsRevision++;this.statusRevision++;const buttons=this.root.querySelectorAll('button');buttons.forEach(button=>button.disabled=true);this.renderClients();this.renderScan();let rescan=false;
    try{
      const value=await this.api.desktopSessions({action,...(['install-deepseek','remove-deepseek'].includes(action)?{home:this.deepseekHome.value.trim()}:{})});
      if(value.clients){this.clients=value.clients;this.gatewayRunning=value.gatewayRunning??this.gatewayRunning;this.renderClients();}
      if(['connect-deepseek','restart-deepseek'].includes(action))this.feedback('正在等待 Harness 连接');
      rescan=action==='connect-deepseek'||action==='install-deepseek';
      await this.refreshClients(true);await this.refresh();
    }catch(error){this.feedback(error.message,true);}
    finally{this.busy=false;buttons.forEach(button=>button.disabled=false);this.renderClients();this.renderScan();}
    if(rescan)await this.scan();
  }
}

if(typeof module!=='undefined')module.exports={DesktopConnectionsPanel};
