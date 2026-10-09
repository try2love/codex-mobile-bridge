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
    this.home=document.createElement('a');this.home.className='plain';this.home.dataset.i18n='返回电脑列表';this.home.textContent=BridgeI18n.t('返回电脑列表');this.home.href='#computers';this.home.id='computers-button';this.home.onclick=event=>{if(BridgeHost.hasNativeLayout()){this.home.href='codexbridge://home';return;}event.preventDefault();this.homeDialog();};
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
    this.timer=setInterval(()=>{if(!document.hidden&&!document.getElementById('app').hidden)this.refresh();},15000);this.paint();
  }
  syncHome(){this.home.hidden=!BridgeHost.hasNativeLayout();}
  preference(key,value){if(value!==undefined)localStorage.setItem('navigation:'+key,String(value));return localStorage.getItem('navigation:'+key)!=='false';}
  icon(id){const n=document.createElement('span');n.className='client-icon client-'+id;n.setAttribute('aria-hidden','true');const img=document.createElement('img');img.src='/client-icons/'+id+'.png';img.alt='';n.append(img);return n;}
  applyClients(data){
    this.clients=data.clients;this.gatewayRunning=data.gatewayRunning??this.gatewayRunning;
    if(data.computer){this.computer.dataset.deviceName=data.computer;this.computerName.textContent=data.computer;document.dispatchEvent(new Event('bridge-computer'));}
    const enabled=this.clients.filter(c=>c.enabled);
    const id=enabled.some(c=>c.id===this.provider)?this.provider:enabled[0]?.id||null;
    if(id!==this.provider||this.view.provider!==(id||'codex'))this.choose(id).catch(error=>this.notify(error.message));
    else this.paint();
  }
  async refresh(){
    if(this.loading||this.pending.size||document.getElementById('app').hidden)return;
    this.loading=true;const revision=this.revision;
    try{const data=await this.request('/api/clients');if(revision===this.revision)this.applyClients(data);}
    catch(error){this.notify(error.message);}finally{this.loading=false;}
  }
  async choose(id){
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
  async toggle(id,enabled){
    const client=this.clients.find(row=>row.id===id);if(this.pending.size||!client||!(client.selectable??client.configured))return;
    const before=this.clients.map(c=>({...c})),previous=this.provider;
    this.pending.add(id);++this.revision;
    this.applyClients({clients:this.clients.map(c=>c.id===id?{...c,enabled}:c)});
    try{const data=await this.request('/api/clients',{provider:id,enabled});this.applyClients(data);}
    catch(error){this.provider=previous;this.applyClients({clients:before});throw error;}
    finally{this.pending.delete(id);++this.revision;this.paint();}
  }
  async reconnect(id){
    const client=this.clients.find(row=>row.id===id);if(this.pending.size||id!=='claude'||!client?.reconnectSupported||!client.canReconnect||client.setupStatus==='connecting')return;
    this.pending.add(id);this.reconnecting=id;++this.revision;this.paint();
    try{const data=await this.request('/api/clients/claude/reconnect',{});this.applyClients(data);const connected=data.clients.find(row=>row.id===id)?.connected;this.notify(connected?'Claude 已连接':'已发起重新连接');if(connected&&this.provider===id)await this.view.refresh();}
    finally{this.pending.delete(id);this.reconnecting=null;++this.revision;this.paint();}
  }
  paint(){
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
    if(this.reconnecting===client.id)return '正在连接…';
    if(this.pending.has(client.id))return this.gatewayRunning===false?'正在保存…':client.enabled?'开启中…':'关闭中…';
    if(this.gatewayRunning===false)return client.enabled?'已选择，下次启动生效':'未选择，下次启动生效';
    return client.enabled?'已启用':'未启用';
  }
  runtimeState(client){
    if(client.backgroundRunning&&client.mainRunning===false)return '后台运行，桌面未打开';
    if(client.connected)return '已连接';
    if(client.setupStatus==='connecting')return '正在连接…';
    if(client.running===true)return '应用运行中，尚未连接';
    if(client.running===false)return '应用未运行';
    return '尚未连接';
  }
  renderManager(){
    const manager=this.manager;if(!manager?.dialog.open)return;
    const {list,note}=manager;list.replaceChildren();
    for(const client of this.clients){
      const row=document.createElement('div');row.className='client-management-row';row.dataset.clientId=client.id;
      const text=document.createElement('span'),name=document.createElement('strong'),status=document.createElement('small'),reason=document.createElement('small'),control=document.createElement('span'),label=document.createElement('span'),toggle=document.createElement('button');
      name.textContent=client.name;status.textContent=BridgeI18n.t(this.runtimeState(client));reason.textContent=BridgeI18n.t(client.reason||'');reason.hidden=!client.reason||reason.textContent===status.textContent;text.append(name,status,reason);
      control.className='client-management-control';label.className='client-toggle-status';label.setAttribute('role','status');label.textContent=BridgeI18n.t(this.managementState(client));
      toggle.type='button';toggle.className='client-management-switch';toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(client.enabled));toggle.setAttribute('aria-label',BridgeI18n.t('启用')+' '+client.name);toggle.setAttribute('aria-busy',String(this.pending.has(client.id)));toggle.disabled=!(client.selectable??client.configured)||this.pending.size>0;
      toggle.append(document.createElement('span'));control.append(toggle,label);row.append(this.icon(client.id),text,control);list.append(row);
      toggle.onclick=()=>this.toggle(client.id,!client.enabled).catch(error=>this.notify(error.message));
      if(client.id==='claude'&&client.reconnectSupported){const reconnect=document.createElement('button');reconnect.type='button';reconnect.className='plain client-reconnect';reconnect.textContent=BridgeI18n.t(this.reconnecting===client.id||client.setupStatus==='connecting'?'正在连接…':'重新连接');reconnect.disabled=!client.canReconnect||this.pending.size>0||client.setupStatus==='connecting';reconnect.setAttribute('aria-busy',String(this.reconnecting===client.id));reconnect.onclick=()=>this.reconnect(client.id).catch(error=>this.notify(error.message));control.append(reconnect);}
    }
    note.textContent=BridgeI18n.t(this.gatewayRunning===false?'网关未启动，开关仅保存下次启动时的选择，不会打开或退出应用。':'安装和登录请在电脑端完成。开启会打开桌面应用，关闭会完全退出；有任务运行或等待确认时无法关闭。');
  }
  openManager(){
    if(this.manager?.dialog.open)return;
    const dialog=this.view.dialog('应用管理'),list=document.createElement('div'),note=document.createElement('p');note.className='muted';dialog.append(list,note);this.manager={dialog,list,note};
    dialog.addEventListener('close',()=>{if(this.manager?.dialog===dialog)this.manager=null;});dialog.showModal();this.renderManager();
  }

  homeDialog(){
    if(BridgeHost.hasNativeLayout()){location.href='codexbridge://home';return;}
    // Web gateways do not share cookies; remember only explicit gateway URLs.
    const dialog=this.view.dialog('电脑列表'),current=document.createElement('p');current.textContent=this.computerName.textContent||location.host;dialog.append(current);
    const input=document.createElement('input');input.type='url';input.placeholder=BridgeI18n.t('https://你的网关地址');input.setAttribute('aria-label',BridgeI18n.t('电脑网关地址'));
    const button=this.view.button('连接电脑',()=>{let url;try{url=new URL(input.value);}catch{throw Error('请输入完整的网关地址');}if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('请输入 HTTP 或 HTTPS 网关地址');location.assign(url.href);});
    const logout=this.view.button('退出当前登录',()=>{dialog.close();document.getElementById('logout').click();});dialog.append(input,button,logout);dialog.showModal();
  }
}
