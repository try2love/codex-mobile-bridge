'use strict';
class ClientNavigation {
  constructor({view,request,notify}) {
    Object.assign(this,{view,request,notify});this.clients=[];this.revision=0;this.pending=new Set();this.provider=localStorage.getItem('bridge-client')||'codex';const requested=new URLSearchParams(location.search).get('client');if(['codex','claude','deepseek'].includes(requested))this.provider=requested;
    this.heading=document.querySelector('#sidebar .list-heading');this.sidebar=document.getElementById('sidebar');
    this.footer=document.querySelector('.sidebar-foot');this.footer.classList.add('client-functions');
    this.navigation=document.createElement('div');this.navigation.className='client-navigation';this.sidebar.append(this.navigation);this.navigation.append(this.footer);
    this.switcher=document.createElement('nav');this.switcher.className='client-switch';this.switcher.dataset.i18nAriaLabel='切换应用';this.switcher.setAttribute('aria-label',BridgeI18n.t('切换应用'));this.switchDock=document.createElement('div');this.switchDock.className='client-switch-dock';this.switchToggle=document.createElement('button');this.switchToggle.type='button';this.switchToggle.className='plain';this.switchToggle.id='client-switch-toggle';this.switcher.id='client-switch';this.switchToggle.setAttribute('aria-controls',this.switcher.id);this.switchToggle.onclick=()=>{if(this.switchToggle.hidden)return;this.preference('switch-collapsed',!this.preference('switch-collapsed',undefined,false));this.paint();document.getElementById('appearance-dialog').close();};this.switchDock.append(this.switcher);this.navigation.append(this.switchDock);
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
    const settings=document.getElementById('appearance-dialog');settings.querySelector('.picker-head').after(this.switchToggle);
    const switchPreferences=document.createElement('fieldset');switchPreferences.className='appearance-section';const switchTitle=document.createElement('legend');switchTitle.dataset.i18n='底部应用切换';switchTitle.textContent=BridgeI18n.t('底部应用切换');switchPreferences.append(switchTitle);
    for(const [id,name] of [['codex','Codex'],['claude','Claude'],['deepseek','DeepSeek Harness']]){const row=document.createElement('label'),box=document.createElement('input'),caption=document.createElement('span');row.className='display-option';box.type='checkbox';box.id='appearance-switch-'+id;box.checked=this.preference('switch:'+id);box.onchange=()=>{this.preference('switch:'+id,box.checked);this.paint();};caption.textContent=name;row.append(caption,box);switchPreferences.append(row);}const switchHint=document.createElement('p');switchHint.className='muted';switchHint.dataset.i18n='隐藏图标不关闭应用，仍可在应用管理中打开聊天。';switchHint.textContent=BridgeI18n.t('隐藏图标不关闭应用，仍可在应用管理中打开聊天。');switchPreferences.append(switchHint);preferences.after(switchPreferences);
    const swipeRow=document.createElement('label'),swipeBox=document.createElement('input'),swipeCaption=document.createElement('span');swipeRow.className='display-option';swipeBox.type='checkbox';swipeBox.id='appearance-swipe-clients';swipeBox.checked=this.preference('swipe-clients',undefined,true);swipeBox.onchange=()=>{this.preference('swipe-clients',swipeBox.checked);this.cancelListSwipe();};swipeCaption.dataset.i18n='左右滑动切换应用';swipeCaption.textContent=BridgeI18n.t('左右滑动切换应用');swipeRow.append(swipeCaption,swipeBox);switchPreferences.append(swipeRow);this.bindListGestures();
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
  syncHome(){this.home.hidden=!BridgeHost.hasNativeLayout();}
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
      this.cancelListSwipe();const touch=event.touches[0],control=event.target.closest('input,select,textarea,button,a,[contenteditable="true"],summary');
      if(event.touches.length!==1||!touch||touch.clientX<28||touch.clientX>innerWidth-28||event.target.closest('.list-heading,.chat-list-toolbar,.client-navigation')||(control&&!control.closest('.session'))||window.getSelection()?.toString())return;
      this.listGesture={list,provider:this.provider,id:touch.identifier,x:touch.clientX,y:touch.clientY,time:Date.now(),horizontal:false};return;
    }
    const gesture=this.listGesture;if(!gesture||gesture.list!==list)return;if(gesture.provider!==this.provider){this.cancelListSwipe();return;}
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
    const enabled=this.clients.filter(c=>c.enabled),index=enabled.findIndex(c=>c.id===this.provider),next=enabled[index+(dx<0?1:-1)];
    this.finishListSwipe(gesture,dx,Math.abs(dx)>=60?next?.id:null);
  }
  swipeRows(){return this.provider==='codex'?document.getElementById('sessions'):this.view.rowsRoot;}
  copySwipeRows(source){
    const clone=source.cloneNode(true);clone.classList.add('client-swipe-rows');
    // A preview is visual only: no duplicate IDs, focus targets or native actions.
    for(const node of [clone,...clone.querySelectorAll('*')]){
      for(const attr of [...node.attributes])if(['id','name','href','autofocus'].includes(attr.name)||attr.name.startsWith('on'))node.removeAttribute(attr.name);
    }
    return {clone,scroll:source.scrollTop};
  }
  rememberSwipeList(){
    const source=this.swipeRows();if(!source.getBoundingClientRect().width)return;
    this.swipeSnapshots.set(this.provider,{...this.copySwipeRows(source),epoch:this.view.providerReadEpochs.get(this.provider),language:BridgeI18n.language()});
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
      const source=this.swipeRows(),rect=source.getBoundingClientRect(),listRect=gesture.list.getBoundingClientRect();
      const root=document.createElement('div');root.className='client-swipe-preview';root.setAttribute('aria-hidden','true');root.inert=true;
      Object.assign(root.style,{left:listRect.left+'px',top:rect.top+'px',width:listRect.width+'px',height:Math.max(0,this.navigation.getBoundingClientRect().top-rect.top)+'px'});
      const current=document.createElement('div'),incoming=document.createElement('div');current.className=incoming.className='client-swipe-page';
      const currentRows=this.copySwipeRows(source);current.append(currentRows.clone);root.append(current,incoming);
      const title=document.createElement('div');title.className='client-swipe-caption';
      if(next){
        title.append(this.icon(next.id),document.createTextNode(next.name));incoming.append(title);
        const snapshot=this.swipeSnapshots.get(next.id),saved=next.id!=='codex'&&this.view.providerViews.has(next.id)&&snapshot?.epoch===this.view.providerReadEpochs.get(next.id)&&snapshot?.language===BridgeI18n.language()?snapshot:null;
        if(saved){const rows=saved.clone.cloneNode(true);incoming.append(rows);incoming._rows=rows;incoming._scroll=saved.scroll;}
        else{
          // Use only already-prefetched titles; previewing never starts a request.
          const rows=document.createElement('div');rows.className='sessions client-swipe-rows';
          const cached=next.id==='codex'?null:this.view.providerViews.get(next.id);
          if(next.id==='codex'){const savedCodex=this.copySwipeRows(document.getElementById('sessions'));incoming._rows=savedCodex.clone;incoming._scroll=savedCodex.scroll;incoming.append(savedCodex.clone);}
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
    const end=commit?-preview.direction*preview.width:0;
    try{
      if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
        preview.animations=[preview.current,preview.incoming].map((node,i)=>node.animate([{transform:node.style.transform},{transform:`translate3d(${end+i*preview.direction*preview.width}px,0,0)`}],{duration:commit?210:180,easing:'cubic-bezier(.22,.7,.22,1)',fill:'forwards'}));
        await Promise.all(preview.animations.map(animation=>animation.finished));
      }
      if(this.swipePreview!==preview)return;
      if(commit&&this.provider===gesture.provider&&this.clients.some(client=>client.id===next&&client.enabled))this.choose(next).catch(error=>this.notify(error.message));
    }catch(error){if(error.name!=='AbortError')this.notify(error.message);}
    finally{if(this.swipePreview===preview)this.cancelListSwipe();}
  }
  visibleClients(){return this.clients.filter(client=>client.enabled&&this.preference('switch:'+client.id));}
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
    this.cancelListSwipe();
    if(id===this.provider&&this.view.provider===(id||'codex'))return;
    this.rememberSwipeList();
    this.listReadDone=false;this.listVisit=(this.listVisit||0)+1;this.provider=id;if(id)localStorage.setItem('bridge-client',id);
    const ready=this.view.choose(id||'codex');this.paint();await ready;
  }
  acknowledgeList(provider,value){
    if(this.listReadDone||provider!==this.provider||document.hidden||document.getElementById('app').hidden||value.connected===false||value.unavailableHosts?.length)return;
    const list=provider==='codex'?this.sidebar:this.view.list;if(list?.getBoundingClientRect().width===0)return;
    const visit=this.listVisit;const read=value.notificationRead;if(!read||!Number.isSafeInteger(read.cursor))return;
    this.listReadDone=true;
    this.request('/api/mobile/events/read',{provider,streamId:read.streamId,through:read.cursor}).catch(()=>{if(this.provider===provider&&this.listVisit===visit)this.listReadDone=false;});
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
    const forceDesktop=quitDesktop==='force';quitDesktop=quitDesktop===true||forceDesktop;
    const before=this.clients.map(c=>({...c})),previous=this.provider;
    this.operationFailures??=new Map();this.operationFailures.delete(id);let failed=false;
    this.pending.add(id);this.pendingQuitDesktop=quitDesktop;this.pendingForceDesktop=forceDesktop;++this.revision;
    this.applyClients({clients:this.clients.map(c=>c.id===id?{...c,enabled}:c)});
    try{const data=await ClientLifecycle.request(signal=>this.request('/api/clients',{provider:id,enabled,...(!enabled?{quitDesktop}:{}),...(forceDesktop?{forceDesktop:true,forceConfirmed:true}:{}),...(initializeDesktop?{initializeDesktop:true}:{})},signal),{timeout:this.lifecycleTimeout??45000});this.applyClients(data);}
    catch(error){failed=true;this.operationFailures.set(id,ClientLifecycle.failure(initializeDesktop?'initialize':enabled?'enable':forceDesktop?'force':quitDesktop?'quit':'disable',error));this.provider=previous;this.applyClients({clients:before});throw error;}
    finally{this.pending.delete(id);this.pendingQuitDesktop=false;this.pendingForceDesktop=false;++this.revision;this.paint();if(failed)await this.refresh();}
  }
  async reconnect(id){
    const client=this.clients.find(row=>row.id===id);if(this.pending.size||id!=='claude'||!client?.reconnectSupported||!client.canReconnect||client.setupStatus==='connecting')return;
    this.pending.add(id);this.reconnecting=id;++this.revision;this.paint();
    try{const data=await this.request('/api/clients/claude/reconnect',{});this.applyClients(data);const connected=data.clients.find(row=>row.id===id)?.connected;this.notify(connected?'Claude 已连接':'已发起重新连接');if(connected&&this.provider===id)await this.view.refresh();}
    finally{this.pending.delete(id);this.reconnecting=null;++this.revision;this.paint();}
  }
  paint(){
    this.cancelListSwipe();this.syncViewClients();
    const provider=this.provider,row=this.clients.find(c=>c.id===provider);this.badge.replaceChildren();if(provider)this.badge.append(this.icon(provider),document.createTextNode(row?.name||'Codex'));
    const target=provider&&provider!=='codex'?this.view.list:this.sidebar;target.prepend(this.heading);target.append(this.navigation);
    this.account.classList.toggle('navigation-hidden',!provider||(provider==='codex'&&!!window.BridgeSharedRelay));this.notifications.classList.toggle('navigation-hidden',!provider);this.manage.hidden=!this.preference('clients');
    this.account.textContent=BridgeI18n.t('账号管理');this.account.removeAttribute('data-i18n');this.notifications.textContent=BridgeI18n.t('通知管理');this.notifications.removeAttribute('data-i18n');
    this.switcher.replaceChildren();const enabled=this.clients.filter(c=>c.enabled),visible=this.visibleClients();const collapsed=this.preference('switch-collapsed',undefined,false);this.switchToggle.hidden=enabled.length<2||!visible.length;this.switchDock.hidden=this.switchToggle.hidden||collapsed;this.navigation.classList.toggle('switch-dock-hidden',this.switchDock.hidden);this.switchToggle.textContent=BridgeI18n.t(collapsed?'显示APP':'隐藏APP');this.switchToggle.setAttribute('aria-expanded',String(!collapsed));this.switchToggle.setAttribute('aria-label',BridgeI18n.t(collapsed?'展开应用图标':'收起应用图标'));this.switchToggle.title=this.switchToggle.getAttribute('aria-label');
    for(const client of visible){const button=document.createElement('button');button.type='button';button.append(this.icon(client.id),document.createTextNode(client.name));button.classList.toggle('selected',client.id===provider);button.setAttribute('aria-pressed',String(client.id===provider));button.onclick=()=>this.choose(client.id).catch(e=>this.notify(e.message));this.switcher.append(button);}
    document.getElementById('desktop-backend').hidden=true;
    document.getElementById('new-chat').disabled=!provider;
    this.sidebar.classList.toggle('no-client',!provider);document.getElementById('app').classList.toggle('no-enabled-client',!provider);
    let empty=this.sidebar.querySelector('.no-client-message');if(!empty){empty=document.createElement('p');empty.className='no-client-message muted';empty.textContent=BridgeI18n.t('尚未启用应用，请打开应用管理。');this.heading.after(empty);}empty.hidden=!!provider;this.renderManager();
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
    const {list,note}=manager;list.replaceChildren();
    for(const client of this.clients){
      const row=document.createElement('div');row.className='client-management-row';row.dataset.clientId=client.id;
      const text=document.createElement('span'),name=document.createElement('strong'),status=document.createElement('small'),reason=document.createElement('small'),control=document.createElement('span'),label=document.createElement('span'),toggle=document.createElement('button');
      const state=this.connection(client),failure=this.operationFailures?.get(client.id),message=failure?ClientLifecycle.failureMessage(failure):state.reason;name.textContent=client.name;status.textContent=BridgeI18n.t(state.label);status.setAttribute('role','status');status.setAttribute('aria-busy',String(state.waiting));status.classList.toggle('client-connection-waiting',state.waiting);reason.textContent=BridgeI18n.t(message);reason.hidden=!message||reason.textContent===status.textContent;text.append(name,status,reason);
      control.className='client-management-control';label.className='client-toggle-status';label.setAttribute('role','status');label.textContent=BridgeI18n.t(this.managementState(client));
      toggle.type='button';toggle.className='client-management-switch';toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(client.enabled));toggle.setAttribute('aria-label',BridgeI18n.t('启用')+' '+client.name);toggle.setAttribute('aria-busy',String(this.pending.has(client.id)));toggle.disabled=(!client.enabled&&!(client.selectable??client.configured))||this.pending.size>0||!!this.choosing||this.hasUncertain();
      toggle.append(document.createElement('span'));control.append(toggle,label);row.append(this.icon(client.id),text,control);list.append(row);
      toggle.onclick=()=>this.toggle(client.id,!client.enabled).catch(error=>this.notify(error.message));
      if(!client.enabled&&client.running&&this.gatewayRunning!==false){const quit=document.createElement('button');quit.type='button';quit.textContent=BridgeI18n.t('关闭电脑 App…');quit.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();quit.onclick=()=>this.toggle(client.id,false).catch(error=>this.notify(error.message));control.append(quit);}
      if(client.enabled){const open=document.createElement('button');open.type='button';open.textContent=BridgeI18n.t('查看聊天');open.onclick=()=>{manager.dialog.close();this.choose(client.id).catch(error=>this.notify(error.message));};control.append(open);}
      if(failure){const retry=document.createElement('button');retry.type='button';retry.textContent=BridgeI18n.t(failure.uncertain?'刷新状态':ClientLifecycle.retryLabel(failure.action));retry.disabled=this.pending.size>0||!!this.choosing||!!this.loading||(!failure.uncertain&&this.hasUncertain());retry.onclick=()=>Promise.resolve(failure.uncertain?this.refresh():this.retryOperation(client.id)).catch(error=>this.notify(error.message));control.append(retry);}
      if(!failure&&state.retryable&&this.gatewayRunning!==false){const retry=document.createElement('button');retry.type='button';retry.textContent=BridgeI18n.t('重试连接');retry.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();retry.onclick=()=>this.toggle(client.id,true).catch(error=>this.notify(error.message));control.append(retry);}
      if(!failure&&state.canInitialize&&this.gatewayRunning!==false){const initialize=document.createElement('button');initialize.type='button';initialize.textContent=BridgeI18n.t('初始化连接');initialize.disabled=this.pending.size>0||!!this.choosing||this.hasUncertain();initialize.onclick=()=>this.initialize(client.id).catch(error=>this.notify(error.message));control.append(initialize);}
    }
    note.textContent=BridgeI18n.t(this.gatewayRunning===false?'网关未启动，开关仅保存下次启动时的选择，不会打开或退出应用。':'安装和登录请在电脑端完成。关闭接入可保留或退出电脑 App。macOS Claude 默认后台启动并尝试恢复已有连接；“初始化连接”经确认后使用电脑前台。Windows 优先尝试后台初始化。')+' '+BridgeI18n.t(ClientLifecycle.sessionNotice(this.windowsSession));
  }
  openManager(){
    if(this.manager?.dialog.open)return;
    const dialog=this.view.dialog('应用管理'),list=document.createElement('div'),note=document.createElement('p');note.className='muted';dialog.append(list,note);this.manager={dialog,list,note};
    dialog.addEventListener('close',()=>{if(this.manager?.dialog===dialog)this.manager=null;});dialog.showModal();this.renderManager();this.refresh();
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
