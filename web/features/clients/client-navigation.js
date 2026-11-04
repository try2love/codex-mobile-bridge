'use strict';
function bindChatListToolbar(toolbar){
  const selects=[...toolbar.querySelectorAll('select')];
  const measure=document.createElement('span');
  measure.style.cssText='position:absolute;visibility:hidden;white-space:pre;pointer-events:none';
  document.body.append(measure);
  const update=()=>{
    for(const select of selects){
      const style=getComputedStyle(select);
      measure.style.font=style.font;
      measure.style.letterSpacing=style.letterSpacing;
      measure.textContent=select.selectedOptions[0]?.textContent||'';
      // Reserve the native arrow and its spacing; only the selected label determines width.
      select.style.width=Math.ceil(measure.getBoundingClientRect().width+30)+'px';
    }
  };
  const later=()=>requestAnimationFrame(update);
  toolbar.addEventListener('change',update);
  document.addEventListener('bridge-language',later);
  window.addEventListener('resize',update);
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(update).observe(toolbar);
  later();
}
function isCodexEntry(id){return typeof id==='string'&&/^(?:wsl|codex):[0-9a-f]{32}$/.test(id);}
function isCodexClient(id){return id==='codex'||isCodexEntry(id);}

class ClientNavigation {
  constructor({view,request,notify}) {
    Object.assign(this,{view,request,notify});this.clients=[];this.revision=0;this.pending=new Set();this.restoreClientSelection();
    this.heading=document.querySelector('#sidebar .list-heading');this.sidebar=document.getElementById('sidebar');
    this.footer=document.querySelector('.sidebar-foot');this.footer.classList.add('client-functions');
    this.navigation=document.createElement('div');this.navigation.className='client-navigation';this.sidebar.append(this.navigation);this.navigation.append(this.footer);
    document.addEventListener('bridge-channels-ready',()=>{if(this.clientPicker)this.clientPicker.disabled=false;});
    this.switcher=document.createElement('nav');this.switcher.className='client-switch';this.switcher.dataset.i18nAriaLabel='切换应用';this.switcher.setAttribute('aria-label',BridgeI18n.t('切换应用'));this.switchDock=document.createElement('div');this.switchDock.className='client-switch-dock';this.switchToggle=document.createElement('button');this.switchToggle.type='button';this.switchToggle.className='plain';this.switchToggle.id='client-switch-toggle';this.switcher.id='client-switch';this.switchToggle.setAttribute('aria-controls',this.switcher.id);this.switchToggle.onclick=()=>{if(this.switchToggle.hidden)return;this.preference('switch-collapsed',!this.preference('switch-collapsed',undefined,false));this.paint();document.getElementById('appearance-dialog').close();};this.switchDock.append(this.switcher);this.navigation.append(this.switchDock);
    document.getElementById('harness-open').hidden=true;
    this.footer.querySelector(':scope > span')?.remove();
    const make=(label,action)=>{const b=document.createElement('button');b.type='button';b.className='plain';b.dataset.i18n=label;b.textContent=BridgeI18n.t(label);b.onclick=action;return b;};
    this.manage=make('应用管理',()=>this.openManager());this.manage.id='clients-button';
    this.home=document.createElement('a');this.home.className='plain';this.home.textContent=BridgeI18n.t('返回电脑列表');this.home.dataset.i18nAriaLabel='返回电脑列表';this.home.setAttribute('aria-label',BridgeI18n.t('返回电脑列表'));this.home.href='#computers';this.home.id='computers-button';this.home.onclick=event=>{if(BridgeHost.hasNativeLayout()){this.home.href='codexbridge://home';return;}event.preventDefault();this.homeDialog();};
    this.footer.append(this.manage,this.home);this.syncHome();new MutationObserver(()=>this.syncHome()).observe(document.documentElement,{attributes:true,attributeFilter:['class']});
    document.getElementById('logout').hidden=true;
    this.badge=document.createElement('span');this.badge.className='client-badge';
    const headingTitle=this.heading.querySelector('h2');this.clientPicker=document.createElement('button');this.clientPicker.type='button';this.clientPicker.className='client-picker plain';this.clientPicker.setAttribute('aria-haspopup','dialog');this.clientPicker.setAttribute('aria-expanded','false');this.clientPicker.append(...headingTitle.childNodes,this.badge);headingTitle.append(this.clientPicker);this.clientPicker.onclick=()=>this.openClientPicker();
    this.switchPosition=0;this.switchNodes=new Map();
    this.switcher.addEventListener('scroll',()=>{if(this.restoringSwitch||!this.switcher.clientWidth)return;this.switchPosition=this.switcher.scrollLeft;this.paintSwitchOverflow();},{passive:true});
    for(const [direction,label,symbol] of [[-1,'向左滚动应用','‹'],[1,'向右滚动应用','›']]){const arrow=document.createElement('button');arrow.type='button';arrow.className='client-switch-arrow '+(direction<0?'previous':'next');arrow.textContent=symbol;arrow.dataset.i18nAriaLabel=label;arrow.setAttribute('aria-label',BridgeI18n.t(label));arrow.hidden=true;arrow.onclick=()=>this.switcher.scrollBy({left:direction*this.switcher.clientWidth*.7,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});this.switchDock.append(arrow);}
    for(const event of ['pointerdown','touchstart','wheel','keydown'])this.switcher.addEventListener(event,()=>{cancelAnimationFrame(this.switchFrame);this.restoringSwitch=false;this.switchPosition=this.switcher.scrollLeft;},{passive:true});
    this.switchResize=typeof ResizeObserver==='undefined'?null:new ResizeObserver(()=>this.restoreSwitchPosition());this.switchResize?.observe(this.switcher);
    this.computer=document.getElementById('connected-computer');this.computerName=document.getElementById('computer-name');
    const more=this.heading.querySelector('.appearance-button')||make('•••',()=>{});more.textContent='•••';more.className='list-more appearance-button plain';more.dataset.i18nAriaLabel='详细设置';more.setAttribute('aria-label',BridgeI18n.t('详细设置'));more.onclick=()=>{document.getElementById('appearance-dialog').showModal();window.loadNotificationDefaults?.();};this.heading.append(more);
    this.heading.addEventListener('click',e=>{if(e.target.closest('#new-chat')&&!isCodexClient(this.provider)){e.preventDefault();e.stopImmediatePropagation();if(this.clients.find(row=>row.id===this.provider)?.capabilities?.create===false){notify('请先在原 App 中创建聊天');return;}if(this.provider)this.view.create().catch(error=>notify(error.message));}},true);
    const preferences=document.getElementById('appearance-activity').closest('fieldset');
    for(const [key,label] of [['clients','应用管理']]){const row=document.createElement('label'),box=document.createElement('input');row.className='display-option';box.type='checkbox';box.id='appearance-'+key;box.checked=this.preference(key);box.onchange=()=>{this.preference(key,box.checked);this.paint();};const caption=document.createElement('span');caption.dataset.i18n=label;caption.textContent=BridgeI18n.t(label);row.append(caption,box);preferences.append(row);}
    const oldShortcuts=document.getElementById('appearance-accounts').closest('fieldset');
    for(const [id,label] of [['appearance-accounts','账号管理'],['appearance-pushplus','显示第三方通知入口']]){const row=document.getElementById(id).closest('label'),caption=row.querySelector('span');caption.dataset.i18n=label;caption.textContent=BridgeI18n.t(label);preferences.append(row);}
    if(window.BridgeSharedRelay)document.getElementById('appearance-pushplus').closest('label').hidden=true;
    oldShortcuts.hidden=true;
    const settings=document.getElementById('appearance-dialog');this.settingsActions=document.createElement('div');this.settingsActions.className='settings-action-row';settings.querySelector('.picker-head').after(this.settingsActions);this.settingsActions.append(this.switchToggle);
    const switchPreferences=document.createElement('fieldset');switchPreferences.className='appearance-section';const switchTitle=document.createElement('legend');switchTitle.dataset.i18n='底部应用切换';switchTitle.textContent=BridgeI18n.t('底部应用切换');switchPreferences.append(switchTitle);
    const switchHint=document.createElement('p');switchHint.className='muted';switchHint.dataset.i18n='隐藏图标不关闭应用，仍可在应用管理中打开聊天。';switchHint.textContent=BridgeI18n.t('隐藏图标不关闭应用，仍可在应用管理中打开聊天。');switchPreferences.append(switchHint);preferences.after(switchPreferences);this.switchPreferences=switchPreferences;
    const swipeRow=document.createElement('label'),swipeBox=document.createElement('input'),swipeCaption=document.createElement('span');swipeRow.className='display-option';swipeBox.type='checkbox';swipeBox.id='appearance-swipe-clients';swipeBox.checked=this.preference('swipe-clients',undefined,true);swipeBox.onchange=()=>{this.preference('swipe-clients',swipeBox.checked);this.cancelListSwipe();};swipeCaption.dataset.i18n='左右滑动切换应用';swipeCaption.textContent=BridgeI18n.t('左右滑动切换应用');swipeRow.append(swipeCaption,swipeBox);switchPreferences.append(swipeRow);this.bindListGestures();
    const language=document.getElementById('language');if(language)preferences.append(language.closest('label'));
    const applications=make('应用管理',()=>{settings.close();this.openManager();});applications.dataset.i18nAriaLabel='应用管理';applications.setAttribute('aria-label',BridgeI18n.t('应用管理'));this.settingsNotifications=make('第三方通知',()=>{if(window.BridgeSharedRelay)return;settings.close();this.notifications.click();});this.settingsNotifications.removeAttribute('data-i18n');this.settingsNotifications.dataset.i18nAriaLabel='第三方通知';this.settingsNotifications.setAttribute('aria-label',BridgeI18n.t('第三方通知'));this.settingsActions.append(applications,this.settingsNotifications,document.getElementById('open-session-diagnostics'));
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
    new MutationObserver(()=>document.body.classList.toggle('gateway-authenticated',!document.getElementById('app').hidden)).observe(document.getElementById('app'),{attributes:true,attributeFilter:['hidden']});
    document.addEventListener('bridge-language',()=>{this.paint();BridgeI18n.apply();});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)this.refresh();});
    this.timer=setInterval(()=>this.poll(),15000);this.connectingTimer=setInterval(()=>this.poll(true),2000);this.paint();
  }
  syncHome(){this.home.hidden=!BridgeHost.hasNativeLayout();}
  restoreClientSelection(){
    this.provider=localStorage.getItem('bridge-client')||'codex';const requested=new URLSearchParams(location.search).get('client');
    if(requested&&/^[a-z][a-z0-9:-]*$/.test(requested))this.provider=requested;
    if(isCodexEntry(this.provider)){this.initialHost=this.provider;this.codexEntry=this.provider;this.provider='codex';if(this.initialHost.startsWith('wsl:'))localStorage.setItem('bridge-client','codex');}
  }
  paintSettingsActions(){
    this.settingsNotifications.hidden=!!window.BridgeSharedRelay;
    const count=2+Number(!this.switchToggle.hidden)+Number(!this.settingsNotifications.hidden);
    this.settingsActions.classList.toggle('three-actions',count===3);
    this.settingsActions.classList.toggle('two-actions',count===2);
  }
  preference(key,value,fallback=true){if(value!==undefined)localStorage.setItem('navigation:'+key,String(value));const saved=localStorage.getItem('navigation:'+key);return saved==null?fallback:saved!=='false';}
  bindListGestures(){
    this.swipeSnapshots=new Map();
    window.addEventListener('resize',()=>this.cancelListSwipe());
    document.addEventListener('visibilitychange',()=>this.cancelListSwipe());
    new MutationObserver(()=>{if(document.getElementById('app').hidden){this.cancelListSwipe();this.swipeSnapshots.clear();}}).observe(document.getElementById('app'),{attributes:true,attributeFilter:['hidden']});
    for(const list of [this.sidebar,this.view.list]){
      list.addEventListener('touchstart',event=>this.listTouch('start',event,list),{passive:true});
      list.addEventListener('touchmove',event=>this.listTouch('move',event,list),{passive:false});
      list.addEventListener('touchend',event=>this.listTouch('end',event,list),{passive:false});
      list.addEventListener('touchcancel',()=>this.cancelListSwipe(),{passive:true});
      list.addEventListener('click',event=>{if(Date.now()<(this.suppressListClickUntil||0)){event.preventDefault();event.stopImmediatePropagation();}},true);
    }
  }
  listTouch(phase,event,list){
    if(!this.preference('swipe-clients',undefined,true)||document.querySelector('dialog[open]')||this.clients.filter(c=>c.enabled).length<2){this.cancelListSwipe();return;}
    if(phase==='start'){
      const settling=this.swipePreview;
      if(settling?.commitTarget&&settling.provider===this.activeClient()&&this.clients.some(client=>client.id===settling.commitTarget&&client.enabled))this.choose(settling.commitTarget).catch(error=>this.notify(error.message));
      this.cancelListSwipe();const touch=event.touches[0],control=event.target.closest('input,select,textarea,button,a,[contenteditable="true"],summary');
      if(event.touches.length!==1||!touch||touch.clientX<28||touch.clientX>innerWidth-28||event.target.closest('.list-heading,.chat-list-toolbar,.client-navigation')||(control&&!control.closest('.session'))||window.getSelection()?.toString())return;
      this.listGesture={list,paintList:isCodexClient(this.provider)?this.sidebar:this.view.list,provider:this.activeClient(),id:touch.identifier,x:touch.clientX,y:touch.clientY,time:Date.now(),horizontal:false};return;
    }
    const gesture=this.listGesture;if(!gesture||gesture.list!==list)return;if(gesture.provider!==this.activeClient()){this.cancelListSwipe();return;}
    if(event.touches.length>(phase==='end'?0:1)){this.cancelListSwipe();return;}
    const touch=[...(phase==='end'?event.changedTouches:event.touches)].find(t=>t.identifier===gesture.id);if(!touch){this.cancelListSwipe();return;}
    const dx=touch.clientX-gesture.x,dy=touch.clientY-gesture.y;
    if(!gesture.horizontal){
      if(Math.abs(dy)>10&&Math.abs(dy)>=Math.abs(dx)/1.5){this.cancelListSwipe();return;}
      if(Math.abs(dx)>12&&Math.abs(dx)>Math.abs(dy)*1.5)gesture.horizontal=true;
    }
    if(gesture.horizontal&&event.cancelable)event.preventDefault();
    if(phase!=='end'){if(gesture.horizontal)this.dragListSwipe(gesture,dx);return;}this.listGesture=null;
    if(!gesture.horizontal)return;this.suppressListClickUntil=Date.now()+400;
    const enabled=this.clients.filter(c=>c.enabled),index=enabled.findIndex(c=>c.id===this.activeClient()),next=enabled[index+(dx<0?1:-1)];
    this.finishListSwipe(gesture,dx,Math.abs(dx)>=60?next?.id:null);
  }
  swipeRows(){return isCodexClient(this.provider)?document.getElementById('sessions'):this.view.rowsRoot;}
  copySwipeRows(source){
    const clone=source.cloneNode(true);clone.classList.add('client-swipe-rows');
    const gutter=source.offsetWidth-source.clientWidth;
    if(gutter>0)clone.style.paddingRight=(parseFloat(getComputedStyle(source).paddingRight)+gutter)+'px';
    // A preview is visual only: no duplicate IDs, focus targets or native actions.
    for(const node of [clone,...clone.querySelectorAll('*')]){
      for(const attr of [...node.attributes])if(['id','name','href','autofocus'].includes(attr.name)||attr.name.startsWith('on'))node.removeAttribute(attr.name);
    }
    return {clone,scroll:source.scrollTop};
  }
  rememberSwipeList(){
    const source=this.swipeRows();if(!source.getBoundingClientRect().width)return;
    this.swipeSnapshots.set(this.activeClient(),{...this.copySwipeRows(source),epoch:this.view.providerReadEpochs.get(this.provider),language:BridgeI18n.language()});
  }
  cancelListSwipe(){
    this.listGesture=null;const preview=this.swipePreview;if(!preview)return;
    this.swipePreview=null;for(const animation of preview.animations||[])animation.cancel();preview.root.remove();
  }
  dragListSwipe(gesture,dx){
    const enabled=this.clients.filter(c=>c.enabled),index=enabled.findIndex(c=>c.id===gesture.provider),next=enabled[index+(dx<0?1:-1)],direction=dx<0?1:-1;
    let preview=this.swipePreview;
    if(preview&&(preview.next!==next?.id||preview.direction!==direction)){this.cancelListSwipe();this.listGesture=gesture;preview=null;}
    if(!preview){
      const source=this.swipeRows(),rect=source.getBoundingClientRect(),listRect=(gesture.paintList||gesture.list).getBoundingClientRect();
      const root=document.createElement('div');root.className='client-swipe-preview';root.setAttribute('aria-hidden','true');root.inert=true;
      Object.assign(root.style,{left:listRect.left+'px',top:rect.top+'px',width:listRect.width+'px',height:Math.max(0,this.navigation.getBoundingClientRect().top-rect.top)+'px'});
      const current=document.createElement('div'),incoming=document.createElement('div');current.className=incoming.className='client-swipe-page';
      const currentRows=this.copySwipeRows(source);current.append(currentRows.clone);root.append(current,incoming);
      const title=document.createElement('div');title.className='client-swipe-caption';
      if(next){
        title.append(this.icon(next.id),document.createTextNode(next.name));incoming.append(title);
        const snapshot=this.swipeSnapshots.get(next.id),saved=snapshot?.language===BridgeI18n.language()&&(isCodexClient(next.id)||this.view.providerViews.has(next.id)&&snapshot?.epoch===this.view.providerReadEpochs.get(next.id))?snapshot:null;
        if(saved){const rows=saved.clone.cloneNode(true);incoming.append(rows);incoming._rows=rows;incoming._scroll=saved.scroll;}
        else{
          // Use only already-prefetched titles; previewing never starts a request.
          const rows=document.createElement('div');rows.className='sessions client-swipe-rows';
          const cached=isCodexClient(next.id)?null:this.view.providerViews.get(next.id);
          if(next.id===this.activeClient()&&isCodexClient(next.id)){const savedCodex=this.copySwipeRows(document.getElementById('sessions'));incoming._rows=savedCodex.clone;incoming._scroll=savedCodex.scroll;incoming.append(savedCodex.clone);}
          else{for(const row of (cached?.rows||[]).filter(row=>!row.archived).slice(0,30)){const item=document.createElement('div'),label=document.createElement('strong');item.className='session';label.textContent=row.title||BridgeI18n.t('新会话');item.append(label);rows.append(item);}incoming.append(rows);}
        }
      }
      document.body.append(root);currentRows.clone.scrollTop=currentRows.scroll;if(incoming._rows)incoming._rows.scrollTop=incoming._scroll;
      preview=this.swipePreview={root,current,incoming,next:next?.id,direction,width:listRect.width,provider:gesture.provider};
    }
    preview.offset=next?Math.max(-preview.width,Math.min(preview.width,dx)):Math.sign(dx)*Math.min(52,Math.abs(dx)*.22);
    preview.current.style.transform=`translate3d(${preview.offset}px,0,0)`;
    preview.incoming.style.transform=`translate3d(${preview.offset+preview.direction*preview.width}px,0,0)`;
  }
  async finishListSwipe(gesture,dx,next){
    this.dragListSwipe(gesture,dx);const preview=this.swipePreview,commit=next&&preview.next===next;
    preview.commitTarget=commit?next:null;
    const end=commit?-preview.direction*preview.width:0;
    try{
      if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
        preview.animations=[preview.current,preview.incoming].map((node,i)=>node.animate([{transform:node.style.transform},{transform:`translate3d(${end+i*preview.direction*preview.width}px,0,0)`}],{duration:commit?210:180,easing:'cubic-bezier(.22,.7,.22,1)',fill:'forwards'}));
        await Promise.all(preview.animations.map(animation=>animation.finished));
      }
      if(this.swipePreview!==preview)return;
      if(commit&&this.activeClient()===gesture.provider&&this.clients.some(client=>client.id===next&&client.enabled))this.choose(next).catch(error=>this.notify(error.message));
    }catch(error){if(error.name!=='AbortError')this.notify(error.message);}
    finally{if(this.swipePreview===preview)this.cancelListSwipe();}
  }
  activeClient(){return this.provider==='codex'&&this.codexEntry?.startsWith('codex:')?this.codexEntry:this.provider;}
  syncSwitchPreferences(){
    if(!this.switchPreferences)return;
    const signature=JSON.stringify(this.clients.map(({id,name})=>[id,name]));if(this.switchPreferencesSignature===signature)return;this.switchPreferencesSignature=signature;
    this.switchPreferences.querySelectorAll('[data-client-switch]').forEach(row=>row.remove());
    const anchor=this.switchPreferences.querySelector('p');
    for(const client of this.clients){const row=document.createElement('label'),box=document.createElement('input'),caption=document.createElement('span');row.className='display-option';row.dataset.clientSwitch=client.id;box.type='checkbox';box.id='appearance-switch-'+client.id;box.checked=this.preference('switch:'+client.id);box.onchange=()=>{this.preference('switch:'+client.id,box.checked);this.paint();};caption.textContent=client.name;row.append(caption,box);this.switchPreferences.insertBefore(row,anchor);}
  }
  visibleClients(){return this.clients.filter(client=>client.enabled&&this.preference('switch:'+client.id));}
  icon(id){const n=document.createElement('span');n.className='client-icon client-'+id;n.setAttribute('aria-hidden','true');const img=document.createElement('img');img.src='/client-icons/'+(isCodexClient(id)?'codex':id.startsWith('wsl-claude:')?'claude':id)+'.png';img.alt='';n.append(img);return n;}
  applyClients(data){
    this.clientsKnown=true;
    this.clients=data.clients;this.wslSupported=data.wslSupported??this.wslSupported;this.gatewayRunning=data.gatewayRunning??this.gatewayRunning;
    this.windowsSession=data.windowsSession??this.windowsSession;
    this.syncViewClients();
    this.syncSwitchPreferences();
    if(data.computer){this.computer.dataset.deviceName=data.computer;this.computerName.textContent=data.computer;document.dispatchEvent(new Event('bridge-computer'));}
    const enabled=this.clients.filter(c=>c.enabled);
    const active=this.activeClient(),id=enabled.some(c=>c.id===active)?active:enabled[0]?.id||null;
    if(this.pendingChatLink){this.paint();}
    else if(id!==active||this.view.provider!==(isCodexClient(id)?'codex':id||'codex'))this.choose(id,{activate:false,fromStatus:true}).catch(error=>this.notify(error.message));
    else{if(id&&this.view.previewOnly)this.view.confirmCachedList();this.paint();}
    this.onClientStates?.(this.clients);
  }
  async refresh(){
    if(this.loading||this.pending.size||this.choosing||document.getElementById('app').hidden)return;
    this.loading=true;const revision=this.revision;
    try{const data=await ClientLifecycle.request(signal=>this.request('/api/clients',undefined,signal),{timeout:this.statusTimeout??10000,uncertain:false,message:'客户端状态读取超时，请重试。'});if(revision===this.revision){this.statusPending=data.pending===true;if(data.refreshError)throw Error(data.refreshError);if(!this.statusPending){ClientLifecycle.reconcile(this.operationFailures,this.managementClients(data.clients));this.applyClients(data);}}}
    catch(error){this.notify(error.message);}finally{this.loading=false;this.paint();}
    if(this.clientsKnown&&this.pendingChatLink&&!document.getElementById('app').hidden){const {hash,openCodex}=this.pendingChatLink;this.pendingChatLink=null;try{await this.openChatLink(hash,openCodex);}catch(error){this.notify(error.message);}}
  }
  clear(){this.revision++;this.clients=[];this.clientsKnown=false;this.enabledClients=null;this.pendingChatLink=null;this.choosing=null;this.pending.clear();this.operationFailures?.clear();this.swipeSnapshots.clear();}
  async scanWsl(button){
    if(this.pending.size||this.choosing||this.hasUncertain())return;
    this.choosing='scan-wsl';const revision=++this.revision;button.disabled=true;this.paint();
    try{const value=await this.request('/api/clients',{action:'scan-wsl'});if(revision!==this.revision)return;this.applyClients(value);if(value.errors?.length)this.notify(value.errors.map(row=>row.distribution+'：'+row.error).join('\n'));}
    catch(error){if(revision===this.revision)this.notify(error.message);}
    finally{if(revision===this.revision){this.choosing=null;this.revision++;button.disabled=false;this.paint();}}
  }
  poll(fast=false){
    if(document.hidden||document.getElementById('app').hidden||this.hasUncertain()||(fast&&!this.statusPending&&!this.managementClients().some(client=>ClientLifecycle.connection(client).waiting)))return;
    return this.refresh();
  }
  connection(client){return ClientLifecycle.connection({...client,pendingEnable:this.gatewayRunning!==false&&this.pending.has(client.id)&&client.enabled});}
  managementClients(clients=this.clients){return clients.flatMap(client=>client.id==='codex'&&client.entries?.length?client.entries.map(entry=>({...entry,id:entry.id==='desktop'?'codex':entry.id,parentId:'codex'})):client.id==='claude'&&client.entries?.length?[{...client,enabled:client.nativeEnabled??client.enabled,parentId:'claude'},...client.entries.map(entry=>({...entry,parentId:'claude'}))]:[client]);}
  withClientEnabled(id,enabled){return this.clients.map(client=>{if(client.id==='codex'&&client.entries?.length){const entries=client.entries.map(entry=>(entry.id==='desktop'?'codex':entry.id)===id?{...entry,enabled}:entry);return {...client,entries,desktopEnabled:entries.find(entry=>entry.id==='desktop')?.enabled,enabled:entries.some(entry=>entry.enabled)};}if(client.id==='claude'&&client.entries?.length){const entries=client.entries.map(entry=>entry.id===id?{...entry,enabled}:entry),nativeEnabled=client.id===id?enabled:client.nativeEnabled??client.enabled;return {...client,entries,nativeEnabled,enabled:nativeEnabled||entries.some(entry=>entry.enabled)};}return client.id===id?{...client,enabled}:client;});}
  hasUncertain(){return [...(this.operationFailures?.values()||[])].some(value=>value.uncertain);}
  syncViewClients(){this.view.setClientStates?.(this.clients.map(client=>({...client,operationFailure:this.operationFailures?.get(client.id),operationUncertain:this.hasUncertain(),pendingEnable:this.gatewayRunning!==false&&this.pending.has(client.id)&&client.enabled})),id=>this.toggle(id,true),id=>this.initialize(id),()=>this.refresh(),id=>this.reconnect(id),id=>this.selectConnectionMode(id));}
  retryOperation(id){const failure=this.operationFailures?.get(id);if(!failure||this.hasUncertain())return;return failure.action==='initialize'?this.initialize(id):this.toggle(id,failure.action==='enable',{connectionMode:failure.connectionMode});}
  async choose(id,{activate=true,fromStatus=false,loadList=true}={}){
    if(!fromStatus)this.pendingChatLink=null;
    this.cancelListSwipe();
    const previousClient=this.activeClient(),host=isCodexEntry(id)?id:undefined;if(host)id='codex';
    const resetCodex=id==='codex'&&!host&&!!this.codexEntry;if(id==='codex')this.codexEntry=host||null;
    if(id)localStorage.setItem('bridge-client',host?.startsWith('codex:')?host:id);
    if(id===this.provider&&this.view.provider===(isCodexClient(id)?'codex':id||'codex')){
      if(host&&host!==previousClient||resetCodex){++this.revision;this.listReadDone=false;this.listVisit=(this.listVisit||0)+1;this.paint();await this.onApplicationChange?.(id,id,{activate,host:host||'local',loadList});}
      return;
    }
    ++this.revision;
    const restoreCodex=id==='codex'&&this.provider===null&&this.view.provider==='codex'&&!this.pending.has('codex');
    this.rememberSwipeList();
    const previous=this.provider;this.listReadDone=false;this.listVisit=(this.listVisit||0)+1;this.provider=id;
    const ready=this.view.choose(isCodexClient(id)?'codex':id||'codex',{loadList});if(restoreCodex)this.view.onSelect?.('codex');this.paint();
    await this.onApplicationChange?.(previous,id,{activate,loadList,...(id==='codex'?{host:host||'local'}:{})});await ready;
  }
  acknowledgeList(provider,value){
    if(provider?.startsWith('wsl:')||this.listReadDone||provider!==this.provider||document.hidden||document.getElementById('app').hidden||value.connected===false||value.complete===false||value.unavailableHosts?.length)return;
    const list=isCodexClient(provider)?this.sidebar:this.view.list;if(list?.getBoundingClientRect().width===0)return;
    const visit=this.listVisit;const read=value.notificationRead;if(!read||!Number.isSafeInteger(read.cursor))return;
    this.listReadDone=true;
    this.request('/api/mobile/events/read',{provider,streamId:read.streamId,through:read.cursor}).catch(()=>{if(this.provider===provider&&this.listVisit===visit)this.listReadDone=false;});
  }
  chatTarget(hash){
    try{
      const value=hash.replace(/^#/,''),at=value.indexOf('~'),id=decodeURIComponent(at<0?value:value.slice(0,at)),host=at<0?'local':decodeURIComponent(value.slice(at+1));
      if(!id||id.length>512||host.length>256||/[\x00-\x1f\x7f]/.test(id+host))return null;
      if(['desktop:claude','desktop:deepseek','desktop:zcode'].includes(host))return {provider:host.slice(8),id,host};
      if(host.startsWith('desktop:')||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))return null;
      if(host.startsWith('wsl:')&&!isCodexClient(host))return null;
      return {provider:'codex',id,host};
    }catch{return null;}
  }
  async openChatLink(hash,openCodex){
    const target=this.chatTarget(hash);if(!target)return false;
    if(!this.clientsKnown&&!this.clients.length&&!Array.isArray(this.enabledClients)){this.pendingChatLink={hash,openCodex};this.statusPending=true;return true;}
    this.pendingChatLink=null;
    const entry=target.provider==='codex'?(isCodexEntry(target.host)?target.host:'codex'):target.provider;
    const enabled=this.clientsKnown||this.clients.length?this.managementClients().some(client=>client.id===entry&&client.enabled):this.enabledClients.includes(entry);
    if(!enabled){this.notify('尚未启用应用，请打开应用管理。');return true;}
    const ready=this.choose(isCodexEntry(target.host)?target.host:target.provider,{loadList:false}),generation=this.view.generation,revision=this.revision;await ready;
    if(this.provider!==target.provider||generation!==this.view.generation||revision!==this.revision)return true;
    if(this.view.previewOnly)this.view.confirmCachedList();
    if(isCodexClient(target.provider))await openCodex(target.id,target.host);else await this.view.open(target.id);
    return true;
  }
  async initialize(id){
    const client=this.managementClients().find(row=>row.id===id),state=client&&this.connection(client);if(this.gatewayRunning===false||this.pending.size||this.choosing||this.hasUncertain()||!state||!(state.canInitialize||state.canStartDesktop))return;
    this.choosing=id;++this.revision;this.paint();let confirmed=false;
    try{confirmed=await ClientLifecycle.chooseInitialize(client);}finally{this.choosing=null;this.paint();}
    if(confirmed)return this.toggle(id,true,{initialization:confirmed});
  }
  async selectConnectionMode(id){
    const client=this.managementClients().find(row=>row.id===id);if(!client||this.pending.size||this.choosing||this.hasUncertain())return;
    this.choosing=id;++this.revision;this.paint();let mode;
    try{mode=await ClientLifecycle.chooseConnectionMode(client);}finally{this.choosing=null;this.paint();}
    if(mode){await this.toggle(id,true,{connectionMode:mode});this.notify('已选择连接方式');}
  }
  async toggle(id,enabled,{initialization=false,connectionMode}={}){
    const client=this.managementClients().find(row=>row.id===id);if(this.pending.size||this.choosing||this.hasUncertain()||!client||(enabled&&!(client.selectable??client.configured)))return;
    let quitDesktop=false;
    if(!enabled&&this.gatewayRunning!==false&&client.canQuitDesktop!==false){
      this.choosing=id;++this.revision;this.paint();
      try{quitDesktop=await ClientLifecycle.chooseDisable(client);}
      finally{this.choosing=null;this.paint();}
      if(quitDesktop===null)return;
    }
    const intent={...ClientLifecycle.initializationFields(initialization),...(connectionMode?{connectionMode}:{})};
    const acknowledgeUnavailable=quitDesktop==='recovery',forceDesktop=quitDesktop==='force';quitDesktop=quitDesktop===true||forceDesktop||acknowledgeUnavailable;
    const before=this.clients.map(c=>({...c})),previous=this.provider;
    this.operationFailures??=new Map();this.operationFailures.delete(id);let failed=false;
    this.pending.add(id);this.pendingQuitDesktop=quitDesktop;this.pendingForceDesktop=forceDesktop;++this.revision;
    this.applyClients({clients:this.withClientEnabled(id,enabled)});
    try{const data=await ClientLifecycle.request(signal=>this.request('/api/clients',{provider:id,enabled,...(!enabled?{quitDesktop}:{}),...(forceDesktop?{forceDesktop:true,forceConfirmed:true}:{}),...(acknowledgeUnavailable?{acknowledgeUnavailable:true}:{}),...intent},signal),{timeout:this.lifecycleTimeout??45000});this.applyClients(data);if(enabled&&id==='codex'&&previous===null)this.view.onSelect?.('codex');}
    catch(error){failed=true;this.operationFailures.set(id,{...ClientLifecycle.failure(initialization?'initialize':enabled?'enable':forceDesktop?'force':quitDesktop?'quit':'disable',error),connectionMode:intent.connectionMode});this.provider=previous;this.applyClients({clients:before});throw error;}
    finally{this.pending.delete(id);this.pendingQuitDesktop=false;this.pendingForceDesktop=false;++this.revision;this.paint();if(failed)await this.refresh();}
  }
  async reconnect(id){
    const client=this.clients.find(row=>row.id===id);if(this.pending.size||this.hasUncertain()||id!=='claude'||!this.connection(client||{}).canReconnect)return;
    this.pending.add(id);this.reconnecting=id;++this.revision;this.paint();
    try{const data=await this.request('/api/clients/claude/reconnect',{});this.applyClients(data);const connected=data.clients.find(row=>row.id===id)?.connected;this.notify(connected?'Claude 已连接':'已发起重新连接');if(connected&&this.provider===id)await this.view.refresh();}
    finally{this.pending.delete(id);this.reconnecting=null;++this.revision;this.paint();}
  }
  paint(){
    if((this.listGesture||this.swipePreview)&&(!this.clients.some(c=>c.id===this.activeClient()&&c.enabled)||this.swipePreview&&this.swipePreview.provider!==this.activeClient()))this.cancelListSwipe();
    this.syncViewClients();
    const provider=this.provider,row=this.clients.find(c=>c.id===this.activeClient()),badgeName=row?.name||'Codex';if(this.badge.dataset.provider!==provider||this.badge.dataset.name!==badgeName){this.badge.replaceChildren();if(provider)this.badge.append(this.icon(provider),document.createTextNode(badgeName));this.badge.dataset.provider=provider||'';this.badge.dataset.name=badgeName;}
    if(this.switcher.clientWidth&&!this.restoringSwitch)this.switchPosition=this.switcher.scrollLeft;
    const target=provider&&!isCodexClient(provider)?this.view.list:this.sidebar,navigationMoved=this.navigation.parentElement!==target;let switchChanged=navigationMoved;if(this.heading.parentElement!==target)target.prepend(this.heading);if(navigationMoved)target.append(this.navigation);
    this.account.classList.toggle('navigation-hidden',!provider||row?.capabilities?.accounts===false||(isCodexClient(provider)&&!!window.BridgeSharedRelay));this.notifications.classList.toggle('navigation-hidden',!provider||row?.capabilities?.notifications===false);this.manage.hidden=!this.preference('clients');
    this.account.textContent=BridgeI18n.t('账号管理');this.account.removeAttribute('data-i18n');this.notifications.textContent=BridgeI18n.language()==='en'?'Push alerts':'第三方通知';this.notifications.removeAttribute('data-i18n');this.notifications.setAttribute('aria-label',BridgeI18n.t('第三方通知'));this.home.textContent=BridgeI18n.language()==='en'?'Computers':'返回电脑列表';
    const enabled=this.clients.filter(c=>c.enabled),visible=this.visibleClients();const collapsed=this.preference('switch-collapsed',undefined,false),switchWasHidden=this.switchDock.hidden;this.switchToggle.hidden=enabled.length<2||!visible.length;this.paintSettingsActions();this.switchDock.hidden=this.switchToggle.hidden||collapsed;this.navigation.classList.toggle('switch-dock-hidden',this.switchDock.hidden);this.switchToggle.textContent=BridgeI18n.t(collapsed?'显示底部 APP 图标':'隐藏底部 APP 图标');this.switchToggle.setAttribute('aria-expanded',String(!collapsed));this.switchToggle.setAttribute('aria-label',BridgeI18n.t(collapsed?'展开应用图标':'收起应用图标'));this.switchToggle.title=this.switchToggle.getAttribute('aria-label');this.settingsNotifications.textContent=BridgeI18n.language()==='en'?'Push alerts':'第三方通知';
    this.clientPicker.disabled=enabled.length<2&&!window.BridgeMobileChannels;switchChanged ||= switchWasHidden!==this.switchDock.hidden;
    for(const [id,node] of this.switchNodes)if(!visible.some(client=>client.id===id)){node.remove();this.switchNodes.delete(id);switchChanged=true;}
    visible.forEach((client,index)=>{let button=this.switchNodes.get(client.id);if(!button){button=document.createElement('button');button.type='button';button.dataset.clientId=client.id;button.onclick=()=>this.choose(client.id).catch(e=>this.notify(e.message));this.switchNodes.set(client.id,button);}if(button.dataset.name!==client.name){button.replaceChildren(this.icon(client.id),document.createTextNode(client.name));button.dataset.name=client.name;switchChanged=true;}button.classList.toggle('selected',client.id===this.activeClient());button.setAttribute('aria-pressed',String(client.id===this.activeClient()));if(this.switcher.children[index]!==button){this.switcher.insertBefore(button,this.switcher.children[index]||null);switchChanged=true;}});
    if(switchChanged)this.restoreSwitchPosition();else this.paintSwitchOverflow();this.paintClientPicker();
    document.getElementById('desktop-backend').hidden=true;
    document.getElementById('new-chat').disabled=!provider||row?.capabilities?.create===false||this.view.previewOnly===true;document.getElementById('new-chat').hidden=false;
    this.sidebar.classList.toggle('no-client',!provider);document.getElementById('app').classList.toggle('no-enabled-client',!provider);
    let empty=this.sidebar.querySelector('.no-client-message');if(!empty){empty=document.createElement('p');empty.className='no-client-message muted';empty.textContent=BridgeI18n.t('尚未启用应用，请打开应用管理。');this.heading.after(empty);}empty.hidden=!!provider;this.renderManager();
  }
  restoreSwitchPosition(){
    this.restoringSwitch=true;cancelAnimationFrame(this.switchFrame);
    this.switchFrame=requestAnimationFrame(()=>{if(this.switcher.clientWidth){this.switcher.scrollLeft=this.switchPosition;this.switchPosition=this.switcher.scrollLeft;}this.paintSwitchOverflow();this.restoringSwitch=false;});
  }
  paintSwitchOverflow(){
    const width=this.switcher.clientWidth,left=this.switcher.scrollLeft;
    this.switchDock.querySelector('.previous').hidden=!width||left<=1;
    this.switchDock.querySelector('.next').hidden=!width||left+width>=this.switcher.scrollWidth-1;
  }
  openClientPicker(){
    if((this.clients.filter(client=>client.enabled).length<2&&!window.BridgeMobileChannels)||this.pickerDialog?.open)return;
    const dialog=this.view.dialog('切换应用');dialog.classList.add('client-picker-dialog');this.pickerDialog=dialog;this.pickerRows=document.createElement('div');this.pickerRows.className='client-picker-options';dialog.append(this.pickerRows);this.clientPicker.setAttribute('aria-expanded','true');
    dialog.addEventListener('close',()=>{this.clientPicker.setAttribute('aria-expanded','false');this.pickerDialog=null;this.pickerRows=null;this.clientPicker.focus();},{once:true});dialog.showModal();this.paintClientPicker();window.BridgeMobileChannels?.mount(dialog);
  }
  paintClientPicker(){
    if(!this.pickerDialog?.open)return;this.pickerRows.replaceChildren();
    for(const client of this.clients.filter(row=>row.enabled)){const button=document.createElement('button'),name=document.createElement('span'),check=document.createElement('span');button.type='button';button.dataset.clientId=client.id;name.textContent=client.name;check.textContent=client.id===this.activeClient()?'✓':'';check.setAttribute('aria-hidden','true');button.setAttribute('aria-pressed',String(client.id===this.activeClient()));button.append(this.icon(client.id),name,check);button.onclick=()=>{this.pickerDialog.close();this.choose(client.id).catch(error=>this.notify(error.message));};this.pickerRows.append(button);}
  }
  managementState(client){
    if(this.operationFailures?.get(client.id)?.uncertain)return '结果待确认';
    if(this.pending.has(client.id))return this.gatewayRunning===false?'正在保存…':client.enabled?'开启中…':this.pendingForceDesktop?'正在后台强制结束…':this.pendingQuitDesktop?'正在退出应用…':'正在停用接入…';
    if(this.gatewayRunning===false)return client.enabled?'已选择，下次启动生效':'未选择，下次启动生效';
    return client.enabled?'已启用':'未启用';
  }
  runtimeState(client){
    return this.connection(client).label;
  }
  renderManager(){
    const manager=this.manager;if(!manager?.dialog.open)return;
    this.renderNetworkAdaptation();
    const {list,note}=manager;list.replaceChildren();
    const groups=new Map();
    for(const client of this.managementClients()){
      let parent=list;if(client.parentId){if(!groups.has(client.parentId)){const group=document.createElement('section'),title=document.createElement('h3'),name=client.parentId==='codex'?'Codex':'Claude';group.className='client-management-group';group.setAttribute('aria-label',name);title.textContent=name;group.append(title);list.append(group);groups.set(client.parentId,group);}parent=groups.get(client.parentId);}
      const row=document.createElement('div');row.className='client-management-row';row.dataset.clientId=client.id;
      const text=document.createElement('span'),name=document.createElement('strong'),status=document.createElement('small'),reason=document.createElement('small'),control=document.createElement('span'),label=document.createElement('span'),toggle=document.createElement('button');
      const state=this.connection(client),failure=this.operationFailures?.get(client.id),message=failure?ClientLifecycle.failureMessage(failure):state.reason;name.textContent=client.parentId==='codex'&&client.id==='codex'?'APP / CLI':client.name;status.textContent=BridgeI18n.t(state.label)+(state.modeLabel?' · '+BridgeI18n.t(state.modeLabel):'');status.setAttribute('role','status');status.setAttribute('aria-busy',String(state.waiting));status.classList.toggle('client-connection-waiting',state.waiting);reason.textContent=BridgeI18n.t(message);reason.hidden=!message||reason.textContent===status.textContent;text.append(name);if(client.description){const description=document.createElement('small');description.textContent=BridgeI18n.t(client.description);text.append(description);}text.append(status,reason);
      control.className='client-management-control';label.className='client-toggle-status';label.setAttribute('role','status');label.textContent=BridgeI18n.t(this.managementState(client));
      toggle.type='button';toggle.className='client-management-switch';toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(client.enabled));toggle.setAttribute('aria-label',BridgeI18n.t('启用')+' '+client.name);toggle.setAttribute('aria-busy',String(this.pending.has(client.id)));toggle.disabled=(!client.enabled&&!(client.selectable??client.configured))||this.pending.size>0||!!this.choosing||this.hasUncertain();
      toggle.append(document.createElement('span'));control.append(toggle,label);row.append(this.icon(client.id),text,control);parent.append(row);
      toggle.onclick=()=>this.toggle(client.id,!client.enabled).catch(error=>this.notify(error.message));
      if(ClientLifecycle.connectionModes(client).length>1){const choose=document.createElement('button');choose.type='button';choose.textContent=BridgeI18n.t('选择连接方式');choose.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();choose.onclick=()=>this.selectConnectionMode(client.id).catch(error=>this.notify(error.message));control.append(choose);}
      if(!client.enabled&&client.running&&client.canQuitDesktop!==false&&this.gatewayRunning!==false){const quit=document.createElement('button');quit.type='button';quit.textContent=BridgeI18n.t('关闭电脑 App…');quit.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();quit.onclick=()=>this.toggle(client.id,false).catch(error=>this.notify(error.message));control.append(quit);}
      if(client.enabled){const open=document.createElement('button');open.type='button';open.textContent=BridgeI18n.t('查看聊天');open.onclick=()=>{manager.dialog.close();this.choose(client.id).catch(error=>this.notify(error.message));};control.append(open);}
      if(failure){const retry=document.createElement('button');retry.type='button';retry.textContent=BridgeI18n.t(failure.uncertain?'刷新状态':ClientLifecycle.retryLabel(failure.action));retry.disabled=this.pending.size>0||!!this.choosing||!!this.loading||(!failure.uncertain&&this.hasUncertain());retry.onclick=()=>Promise.resolve(failure.uncertain?this.refresh():this.retryOperation(client.id)).catch(error=>this.notify(error.message));control.append(retry);}
      if(!failure&&state.retryable&&this.gatewayRunning!==false){const retry=document.createElement('button');retry.type='button';retry.textContent=BridgeI18n.t('重试连接');retry.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();retry.onclick=()=>this.toggle(client.id,true).catch(error=>this.notify(error.message));control.append(retry);}
      if(!failure&&state.canReconnect&&this.gatewayRunning!==false){const reconnect=document.createElement('button');reconnect.type='button';reconnect.textContent=BridgeI18n.t('重新连接 Desktop');reconnect.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();reconnect.onclick=()=>this.reconnect(client.id).catch(error=>this.notify(error.message));control.append(reconnect);}
      if(!failure&&state.canInitialize&&this.gatewayRunning!==false){const initialize=document.createElement('button');initialize.type='button';initialize.textContent=BridgeI18n.t(state.initializeLabel);initialize.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();initialize.onclick=()=>this.initialize(client.id).catch(error=>this.notify(error.message));control.append(initialize);}
      if(!failure&&state.canStartDesktop&&this.gatewayRunning!==false){const start=document.createElement('button');start.type='button';start.textContent=BridgeI18n.t('启动 Desktop');start.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();start.onclick=()=>this.initialize(client.id).catch(error=>this.notify(error.message));control.append(start);}
    }
    note.textContent=BridgeI18n.t(this.gatewayRunning===false?'网关未启动，开关仅保存下次启动时的选择，不会打开或退出应用。':'安装和登录请在电脑端完成。关闭接入可保留或退出电脑 App。macOS Claude 默认后台启动并尝试恢复已有连接；“初始化连接”经确认后使用电脑前台。Windows 优先尝试后台初始化。')+' '+BridgeI18n.t(ClientLifecycle.sessionNotice(this.windowsSession));
  }
  openManager(){
    if(this.manager?.dialog.open)return;
    const dialog=this.view.dialog('应用管理'),list=document.createElement('div'),note=document.createElement('p');note.className='muted';
    if(this.wslSupported){const scan=document.createElement('button');scan.type='button';scan.textContent=BridgeI18n.t('扫描 WSL 应用');scan.onclick=()=>this.scanWsl(scan);dialog.append(scan);}
    const title=dialog.querySelector('.picker-head h2');title.classList.add('dialog-content-start');title.tabIndex=-1;title.autofocus=true;dialog.append(list,note);this.manager={dialog,list,note};
    this.createNetworkAdaptation();
    dialog.addEventListener('close',()=>{if(this.manager?.dialog===dialog)this.manager=null;});dialog.showModal();this.renderManager();this.refresh();this.readNetworkAdaptation();
  }

  createNetworkAdaptation(){
    if(window.BridgeSharedRelay)return;
    const section=document.createElement('section'),heading=document.createElement('div'),title=document.createElement('h3'),refresh=document.createElement('button'),label=document.createElement('label'),caption=document.createElement('span'),box=document.createElement('input'),hint=document.createElement('p'),status=document.createElement('p');
    section.className='gateway-network-settings';heading.className='gateway-network-heading';title.dataset.i18n='网关连接';title.textContent=BridgeI18n.t('网关连接');
    refresh.type='button';refresh.className='plain';refresh.dataset.i18n='刷新状态';refresh.textContent=BridgeI18n.t('刷新状态');refresh.onclick=()=>this.readNetworkAdaptation();heading.append(title,refresh);
    label.className='display-option';caption.dataset.i18n='网络变化自动恢复';caption.textContent=BridgeI18n.t('网络变化自动恢复');box.type='checkbox';box.disabled=true;box.setAttribute('aria-describedby','network-adaptation-help');label.append(caption,box);
    hint.id='network-adaptation-help';hint.className='muted';hint.dataset.i18n='默认关闭。开启后检测网络变化，更新所选网卡的 IP 入口，并恢复失效的临时 HTTPS 连接。开关即时保存，无需重启网关。';hint.textContent=BridgeI18n.t(hint.dataset.i18n);status.className='muted';status.setAttribute('role','status');
    box.onchange=()=>this.setNetworkAdaptation(box.checked);
    section.append(heading,label,hint,status);this.manager.dialog.append(section);this.manager.networkAdaptation={section,box,status,refresh};
  }
  renderNetworkAdaptation(){
    const panel=this.manager?.networkAdaptation;if(!panel)return;
    const state=this.networkAdaptationState,known=typeof state?.enabled==='boolean',busy=!!this.networkAdaptationBusy;
    panel.section.hidden=!!window.BridgeSharedRelay;
    panel.box.checked=state?.enabled===true;panel.box.disabled=busy||!known||state?.canEdit===false;
    panel.box.setAttribute('aria-busy',String(busy));panel.refresh.disabled=busy;
    panel.status.textContent=BridgeI18n.t(busy?(this.networkAdaptationBusy==='write'?'正在保存…':'正在读取网络恢复设置…'):this.networkAdaptationError||(state?.canEdit===false?'免密访问下不能远程修改此设置，请在网关 App 中调整。':known?(state.enabled?'已启用':'未启用'):'正在读取网络恢复设置…'));
  }
  async readNetworkAdaptation(){
    if(window.BridgeSharedRelay||this.networkAdaptationBusy)return;
    this.networkAdaptationBusy='read';this.networkAdaptationError='';this.renderNetworkAdaptation();
    try{this.networkAdaptationState=await this.networkAdaptationRequest();}
    catch(error){this.networkAdaptationState=null;this.networkAdaptationError=error.status===404?'请更新网关以使用网络变化自动恢复。':'网络恢复设置读取失败，请刷新状态。';}
    finally{this.networkAdaptationBusy=false;this.renderNetworkAdaptation();}
  }
  networkAdaptationRequest(body){
    return ClientLifecycle.request(signal=>this.request('/api/network-adaptation',body,signal),{timeout:10000,uncertain:body!==undefined,message:'网络恢复设置尚未确认，请刷新状态后重试。'}).then(state=>{if(typeof state?.enabled!=='boolean')throw Error('网络恢复设置读取失败，请刷新状态。');return state;});
  }
  async setNetworkAdaptation(enabled){
    if(window.BridgeSharedRelay||this.networkAdaptationBusy||typeof this.networkAdaptationState?.enabled!=='boolean'||this.networkAdaptationState.canEdit===false)return;
    this.networkAdaptationBusy='write';this.networkAdaptationError='';this.renderNetworkAdaptation();
    try{this.networkAdaptationState=await this.networkAdaptationRequest({enabled});}
    catch(error){
      this.networkAdaptationError=error.status>=400&&error.status<500?error.message:'网络恢复设置尚未确认，请刷新状态后重试。';
      // A lost response is not permission to replay a state-changing request.
      try{this.networkAdaptationState=await this.networkAdaptationRequest();if(this.networkAdaptationState.enabled===enabled)this.networkAdaptationError='';}
      catch{this.networkAdaptationState=null;}
    }finally{this.networkAdaptationBusy=false;this.renderNetworkAdaptation();}
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
