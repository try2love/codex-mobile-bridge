'use strict';
class ClientNavigation {
  constructor({view,request,notify}) {
    Object.assign(this,{view,request,notify});this.clients=[];this.revision=0;this.pending=new Set();this.provider=localStorage.getItem('bridge-client')||'codex';
    this.heading=document.querySelector('#sidebar .list-heading');this.sidebar=document.getElementById('sidebar');
    this.footer=document.querySelector('.sidebar-foot');this.footer.classList.add('client-functions');
    this.navigation=document.createElement('div');this.navigation.className='client-navigation';this.sidebar.append(this.navigation);this.navigation.append(this.footer);
    this.switcher=document.createElement('nav');this.switcher.className='client-switch';this.switcher.dataset.i18nAriaLabel='切换应用';this.switcher.setAttribute('aria-label',BridgeI18n.t('切换应用'));this.navigation.append(this.switcher);
    document.getElementById('harness-open').hidden=true;
    this.footer.querySelector(':scope > span')?.remove();
    const make=(label,action)=>{const b=document.createElement('button');b.type='button';b.className='plain';b.dataset.i18n=label;b.textContent=BridgeI18n.t(label);b.onclick=action;return b;};
    this.manage=make('应用管理',()=>this.openManager());this.manage.id='clients-button';
    this.home=document.createElement('a');this.home.className='plain';this.home.dataset.i18n='返回电脑列表';this.home.textContent=BridgeI18n.t('返回电脑列表');this.home.href='#computers';this.home.id='computers-button';this.home.onclick=event=>{if(document.documentElement.classList.contains('bridge-mobile')){this.home.href='codexbridge://home';return;}event.preventDefault();this.homeDialog();};
    this.footer.append(this.manage,this.home);this.syncHome();new MutationObserver(()=>this.syncHome()).observe(document.documentElement,{attributes:true,attributeFilter:['class']});
    document.getElementById('logout').hidden=true;
    this.badge=document.createElement('span');this.badge.className='client-badge';this.heading.querySelector('h2').append(this.badge);
    this.computer=document.getElementById('connected-computer');this.computerName=document.getElementById('computer-name');
    const more=this.heading.querySelector('.appearance-button')||make('•••',()=>{});more.textContent='•••';more.className='list-more appearance-button plain';more.dataset.i18nAriaLabel='详细设置';more.setAttribute('aria-label',BridgeI18n.t('详细设置'));more.onclick=()=>{document.getElementById('appearance-dialog').showModal();window.loadNotificationDefaults?.();};this.heading.append(more);
    this.heading.addEventListener('click',e=>{if(e.target.closest('#new-chat')&&this.provider!=='codex'){e.preventDefault();e.stopImmediatePropagation();if(this.provider)this.view.create().catch(error=>notify(error.message));}},true);
    const preferences=document.getElementById('appearance-activity').closest('fieldset');
    for(const [key,label] of [['clients','应用管理']]){const row=document.createElement('label'),box=document.createElement('input');row.className='display-option';box.type='checkbox';box.id='appearance-'+key;box.checked=this.preference(key);box.onchange=()=>{this.preference(key,box.checked);this.paint();};const caption=document.createElement('span');caption.dataset.i18n=label;caption.textContent=BridgeI18n.t(label);row.append(caption,box);preferences.append(row);}
    const oldShortcuts=document.getElementById('appearance-accounts').closest('fieldset');
    for(const [id,label] of [['appearance-accounts','账号管理'],['appearance-pushplus','通知管理']]){const row=document.getElementById(id).closest('label'),caption=row.querySelector('span');caption.dataset.i18n=label;caption.textContent=BridgeI18n.t(label);preferences.append(row);}
    oldShortcuts.hidden=true;
    const settings=document.getElementById('appearance-dialog');
    const language=document.getElementById('language');if(language)preferences.append(language.closest('label'));
    const applications=make('应用管理',()=>{settings.close();this.openManager();});settings.querySelector('.appearance-actions').insertBefore(applications,document.getElementById('appearance-reset').nextSibling);
    this.account=document.getElementById('accounts-button');this.notifications=document.getElementById('pushplus-settings');
    // Older native shells move these controls after page load. Keep the new
    // two-row navigation intact while preserving their native home action.
    const restore=()=>{
      if(this.footer.parentElement!==this.navigation)this.navigation.prepend(this.footer);
      this.footer.querySelector('a[href="codexbridge://home"]:not(#computers-button)')?.remove();
      if(this.notifications.parentElement!==this.footer)this.footer.insertBefore(this.notifications,this.manage);
    };
    new MutationObserver(restore).observe(document.body,{childList:true});
    new MutationObserver(restore).observe(settings,{childList:true});
    document.querySelector('#chat .chat-info').before(this.icon('codex'));
    document.getElementById('new-chat-form').prepend(Object.assign(document.createElement('p'),{className:'muted',textContent:'Codex'}));
    new MutationObserver(()=>document.body.classList.toggle('gateway-authenticated',!document.getElementById('app').hidden)).observe(document.getElementById('app'),{attributes:true,attributeFilter:['hidden']});
    document.addEventListener('bridge-language',()=>{this.paint();BridgeI18n.apply();});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)this.refresh();});
    this.timer=setInterval(()=>this.poll(),15000);this.connectingTimer=setInterval(()=>this.poll(true),2000);this.paint();
  }
  syncHome(){this.home.hidden=!document.documentElement.classList.contains('bridge-mobile');}
  preference(key,value){if(value!==undefined)localStorage.setItem('navigation:'+key,String(value));return localStorage.getItem('navigation:'+key)!=='false';}
  icon(id){const n=document.createElement('span');n.className='client-icon client-'+id;n.setAttribute('aria-hidden','true');const img=document.createElement('img');img.src='/client-icons/'+id+'.png';img.alt='';n.append(img);return n;}
  applyClients(data){
    this.clients=data.clients;this.gatewayRunning=data.gatewayRunning??this.gatewayRunning;
    this.windowsSession=data.windowsSession??this.windowsSession;
    this.syncViewClients();
    if(data.computer){this.computer.dataset.deviceName=data.computer;this.computerName.textContent=data.computer;document.dispatchEvent(new Event('bridge-computer'));}
    const enabled=this.clients.filter(c=>c.enabled);
    const id=enabled.some(c=>c.id===this.provider)?this.provider:enabled[0]?.id||null;
    if(id!==this.provider||this.view.provider!==(id||'codex'))this.choose(id).catch(error=>this.notify(error.message));
    else this.paint();
  }
  async refresh(){
    if(this.loading||this.pending.size||this.choosing||document.getElementById('app').hidden)return;
    this.loading=true;const revision=this.revision;
    try{const data=await ClientLifecycle.request(signal=>this.request('/api/clients',undefined,signal),{timeout:this.statusTimeout??10000,uncertain:false,message:'客户端状态读取超时，请重试。'});if(revision===this.revision){ClientLifecycle.reconcile(this.operationFailures,data.clients);this.applyClients(data);}}
    catch(error){this.notify(error.message);}finally{this.loading=false;this.paint();}
  }
  poll(fast=false){
    if(document.hidden||document.getElementById('app').hidden||this.hasUncertain()||(fast&&!this.clients.some(client=>ClientLifecycle.connection(client).waiting)))return;
    return this.refresh();
  }
  connection(client){return ClientLifecycle.connection({...client,pendingEnable:this.gatewayRunning!==false&&this.pending.has(client.id)&&client.enabled});}
  hasUncertain(){return [...(this.operationFailures?.values()||[])].some(value=>value.uncertain);}
  syncViewClients(){this.view.setClientStates?.(this.clients.map(client=>({...client,operationFailure:this.operationFailures?.get(client.id),operationUncertain:this.hasUncertain(),pendingEnable:this.gatewayRunning!==false&&this.pending.has(client.id)&&client.enabled})),id=>this.toggle(id,true),id=>this.initialize(id),()=>this.refresh());}
  retryOperation(id){const failure=this.operationFailures?.get(id);if(!failure||this.hasUncertain())return;return failure.action==='initialize'?this.initialize(id):this.toggle(id,failure.action==='enable');}
  async choose(id){
    if(id===this.provider&&this.view.provider===(id||'codex'))return;
    this.provider=id;if(id)localStorage.setItem('bridge-client',id);
    const ready=this.view.choose(id||'codex');this.paint();await ready;
  }
  chatTarget(hash){
    try{
      const value=hash.replace(/^#/,''),at=value.indexOf('~'),id=decodeURIComponent(at<0?value:value.slice(0,at)),host=at<0?'local':decodeURIComponent(value.slice(at+1));
      if(!id||id.length>512||host.length>256||/[\x00-\x1f\x7f]/.test(id+host))return null;
      if(['desktop:claude','desktop:deepseek'].includes(host))return {provider:host.slice(8),id,host};
      if(host.startsWith('desktop:')||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))return null;
      return {provider:'codex',id,host};
    }catch{return null;}
  }
  async openChatLink(hash,openCodex){
    const target=this.chatTarget(hash);if(!target)return false;
    if(!this.clients.some(client=>client.id===target.provider&&client.enabled)){this.notify('尚未启用应用，请打开应用管理。');return true;}
    const ready=this.choose(target.provider),generation=this.view.generation;await ready;
    if(this.provider!==target.provider||generation!==this.view.generation)return true;
    if(target.provider==='codex')await openCodex(target.id,target.host);else await this.view.open(target.id);
    return true;
  }
  async initialize(id){
    const client=this.clients.find(row=>row.id===id);if(this.gatewayRunning===false||this.pending.size||this.choosing||this.hasUncertain()||!client||!this.connection(client).canInitialize)return;
    this.choosing=id;++this.revision;this.paint();let confirmed=false;
    try{confirmed=await ClientLifecycle.chooseInitialize(client);}finally{this.choosing=null;this.paint();}
    if(confirmed)return this.toggle(id,true,{initializeDesktop:true});
  }
  async toggle(id,enabled,{initializeDesktop=false}={}){
    const client=this.clients.find(row=>row.id===id);if(this.pending.size||this.choosing||this.hasUncertain()||!client||(enabled&&!(client.selectable??client.configured)))return;
    let quitDesktop=false;
    if(!enabled&&this.gatewayRunning!==false){
      this.choosing=id;++this.revision;this.paint();
      try{quitDesktop=await ClientLifecycle.chooseDisable(client);}
      finally{this.choosing=null;this.paint();}
      if(quitDesktop===null)return;
    }
    const before=this.clients.map(c=>({...c})),previous=this.provider;
    this.operationFailures??=new Map();this.operationFailures.delete(id);let failed=false;
    this.pending.add(id);this.pendingQuitDesktop=quitDesktop;++this.revision;
    this.applyClients({clients:this.clients.map(c=>c.id===id?{...c,enabled}:c)});
    try{const data=await ClientLifecycle.request(signal=>this.request('/api/clients',{provider:id,enabled,...(!enabled?{quitDesktop}:{}),...(initializeDesktop?{initializeDesktop:true}:{})},signal),{timeout:this.lifecycleTimeout??45000});this.applyClients(data);}
    catch(error){failed=true;this.operationFailures.set(id,ClientLifecycle.failure(initializeDesktop?'initialize':enabled?'enable':quitDesktop?'quit':'disable',error));this.provider=previous;this.applyClients({clients:before});throw error;}
    finally{this.pending.delete(id);this.pendingQuitDesktop=false;++this.revision;this.paint();if(failed)await this.refresh();}
  }
  paint(){
    this.syncViewClients();
    const provider=this.provider,row=this.clients.find(c=>c.id===provider);this.badge.replaceChildren();if(provider)this.badge.append(this.icon(provider),document.createTextNode(row?.name||'Codex'));
    const target=provider&&provider!=='codex'?this.view.list:this.sidebar;target.prepend(this.heading);target.append(this.navigation);
    this.account.classList.toggle('navigation-hidden',!provider||(provider==='codex'&&!!window.BridgeSharedRelay));this.notifications.classList.toggle('navigation-hidden',!provider);this.manage.hidden=!this.preference('clients');
    this.account.textContent=BridgeI18n.t('账号管理');this.account.removeAttribute('data-i18n');this.notifications.textContent=BridgeI18n.t('通知管理');this.notifications.removeAttribute('data-i18n');
    this.switcher.replaceChildren();const enabled=this.clients.filter(c=>c.enabled);this.switcher.hidden=enabled.length<2;
    for(const client of enabled){const button=document.createElement('button');button.type='button';button.append(this.icon(client.id),document.createTextNode(client.name));button.classList.toggle('selected',client.id===provider);button.setAttribute('aria-pressed',String(client.id===provider));button.onclick=()=>this.choose(client.id).catch(e=>this.notify(e.message));this.switcher.append(button);}
    document.getElementById('desktop-backend').hidden=true;
    document.getElementById('new-chat').disabled=!provider;
    this.sidebar.classList.toggle('no-client',!provider);document.getElementById('app').classList.toggle('no-enabled-client',!provider);
    let empty=this.sidebar.querySelector('.no-client-message');if(!empty){empty=document.createElement('p');empty.className='no-client-message muted';empty.textContent=BridgeI18n.t('尚未启用应用，请打开应用管理。');this.heading.after(empty);}empty.hidden=!!provider;this.renderManager();
  }
  managementState(client){
    if(this.operationFailures?.get(client.id)?.uncertain)return '结果待确认';
    if(this.pending.has(client.id))return this.gatewayRunning===false?'正在保存…':client.enabled?'开启中…':this.pendingQuitDesktop?'正在退出应用…':'正在停用接入…';
    if(this.gatewayRunning===false)return client.enabled?'已选择，下次启动生效':'未选择，下次启动生效';
    return client.enabled?'已启用':'未启用';
  }
  runtimeState(client){
    return this.connection(client).label;
  }
  renderManager(){
    const manager=this.manager;if(!manager?.dialog.open)return;
    const {list,note}=manager;list.replaceChildren();
    for(const client of this.clients){
      const row=document.createElement('div');row.className='client-management-row';row.dataset.clientId=client.id;
      const text=document.createElement('span'),name=document.createElement('strong'),status=document.createElement('small'),reason=document.createElement('small'),control=document.createElement('span'),label=document.createElement('span'),toggle=document.createElement('button');
      const state=this.connection(client),failure=this.operationFailures?.get(client.id),message=failure?ClientLifecycle.failureMessage(failure):state.reason;name.textContent=client.name;status.textContent=BridgeI18n.t(state.label);status.setAttribute('role','status');status.setAttribute('aria-busy',String(state.waiting));status.classList.toggle('client-connection-waiting',state.waiting);reason.textContent=BridgeI18n.t(message);reason.hidden=!message||reason.textContent===status.textContent;text.append(name,status,reason);
      control.className='client-management-control';label.className='client-toggle-status';label.setAttribute('role','status');label.textContent=BridgeI18n.t(this.managementState(client));
      toggle.type='button';toggle.className='client-management-switch';toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(client.enabled));toggle.setAttribute('aria-label',BridgeI18n.t('启用')+' '+client.name);toggle.setAttribute('aria-busy',String(this.pending.has(client.id)));toggle.disabled=(!client.enabled&&!(client.selectable??client.configured))||this.pending.size>0||!!this.choosing||this.hasUncertain();
      toggle.append(document.createElement('span'));control.append(toggle,label);row.append(this.icon(client.id),text,control);list.append(row);
      toggle.onclick=()=>this.toggle(client.id,!client.enabled).catch(error=>this.notify(error.message));
      if(failure){const retry=document.createElement('button');retry.type='button';retry.textContent=BridgeI18n.t(failure.uncertain?'刷新状态':ClientLifecycle.retryLabel(failure.action));retry.disabled=this.pending.size>0||!!this.choosing||!!this.loading||(!failure.uncertain&&this.hasUncertain());retry.onclick=()=>Promise.resolve(failure.uncertain?this.refresh():this.retryOperation(client.id)).catch(error=>this.notify(error.message));control.append(retry);}
      if(!failure&&state.retryable&&this.gatewayRunning!==false){const retry=document.createElement('button');retry.type='button';retry.textContent=BridgeI18n.t('重试连接');retry.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();retry.onclick=()=>this.toggle(client.id,true).catch(error=>this.notify(error.message));control.append(retry);}
      if(!failure&&state.canInitialize&&this.gatewayRunning!==false){const initialize=document.createElement('button');initialize.type='button';initialize.textContent=BridgeI18n.t('初始化连接');initialize.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();initialize.onclick=()=>this.initialize(client.id).catch(error=>this.notify(error.message));control.append(initialize);}
    }
    note.textContent=BridgeI18n.t(this.gatewayRunning===false?'网关未启动，开关仅保存下次启动时的选择，不会打开或退出应用。':'安装和登录请在电脑端完成。关闭接入时可选择保留或退出电脑 App。Windows Claude 开启时优先后台初始化，开发者工具可能短暂出现。')+' '+BridgeI18n.t(ClientLifecycle.sessionNotice(this.windowsSession));
  }
  openManager(){
    if(this.manager?.dialog.open)return;
    const dialog=this.view.dialog('应用管理'),list=document.createElement('div'),note=document.createElement('p');note.className='muted';dialog.append(list,note);this.manager={dialog,list,note};
    dialog.addEventListener('close',()=>{if(this.manager?.dialog===dialog)this.manager=null;});dialog.showModal();this.renderManager();this.refresh();
  }

  homeDialog(){
    if(document.documentElement.classList.contains('bridge-mobile')){location.href='codexbridge://home';return;}
    // Web gateways do not share cookies; remember only explicit gateway URLs.
    const dialog=this.view.dialog('电脑列表'),current=document.createElement('p');current.textContent=this.computerName.textContent||location.host;dialog.append(current);
    const input=document.createElement('input');input.type='url';input.placeholder=BridgeI18n.t('https://你的网关地址');input.setAttribute('aria-label',BridgeI18n.t('电脑网关地址'));
    const button=this.view.button('连接电脑',()=>{let url;try{url=new URL(input.value);}catch{throw Error('请输入完整的网关地址');}if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('请输入 HTTP 或 HTTPS 网关地址');location.assign(url.href);});
    const logout=this.view.button('退出当前登录',()=>{dialog.close();document.getElementById('logout').click();});dialog.append(input,button,logout);dialog.showModal();
  }
}
