(function mountNativeLayout() {
  if (document.documentElement.classList.contains('bridge-mobile')) return;
  const app = document.getElementById('app');
  if (!app) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountNativeLayout, {once:true});
    return;
  }
  // WKWebView's document-end hook can precede stylesheet readiness on a cold load.
  // Mark the native shell first so navigation can give it ownership of the menu.
  document.documentElement.classList.add('bridge-mobile');
  // Reuse a same-origin sheet: the gateway intentionally disallows inline styles.
  const layoutRules = [
    '.bridge-mobile .phone-header { min-height: 56px; padding: 4px 64px 4px 16px; }',
    '.bridge-mobile.bridge-authenticated .phone-header { display: none !important; }',
    '.bridge-mobile aside { padding-top: 0; }',
    '.bridge-mobile .list-heading { min-height: 56px; padding-right: 48px; }',
    '.bridge-mobile .list-heading button { height: 48px; display: inline-flex; align-items: center; justify-content: center; margin: 0; }',
    '.bridge-mobile .chat-head { min-height: 56px; padding: 4px 64px 4px 8px; gap: 6px; }',
    '.bridge-mobile .chat-head .appearance-button, .bridge-mobile .list-actions .appearance-button, .bridge-mobile .list-more { display: none !important; }',
    '.bridge-mobile #back { width: 40px; height: 48px; padding: 8px; margin-left: 0; flex: none; }',
    '.bridge-mobile .sidebar-foot:not(.client-functions) { order: 99; flex: none; min-height: 44px; padding: 2px 16px max(4px, env(safe-area-inset-bottom)); background: var(--page); }',
    '.bridge-mobile:not(.bridge-authenticated) .sidebar-foot { display: none; }',
    '.bridge-mobile body.chat-detail > .sidebar-foot { display: none; }',
    '.bridge-mobile body.chat-detail .composer { padding-bottom: max(8px, env(safe-area-inset-bottom)); }',
    '.bridge-mobile .sidebar-foot #logout { display: none; }',
    '.bridge-mobile .sidebar-foot:not(.client-functions) button, .bridge-mobile .sidebar-foot:not(.client-functions) a { min-height: 40px; display: inline-flex; align-items: center; text-decoration: none; }',
    '.bridge-mobile .sidebar-foot > span { display: none; }',
    '.bridge-mobile .composer { padding-bottom: 8px; }',
    '.bridge-mobile input:not([type=checkbox]):not([type=radio]), .bridge-mobile textarea { font-size: max(16px, 1em); }'
  ];
  let waitingForSheet = false;
  function installRules() {
    const sheet = Array.from(document.styleSheets).find(s => s.href && new URL(s.href).origin === location.origin);
    if (!sheet) return false;
    let index;
    try { index = sheet.cssRules.length; } catch { return false; }
    for (const rule of layoutRules) sheet.insertRule(rule, index++);
    if (waitingForSheet) {
      document.removeEventListener('load', installRules, true);
      waitingForSheet = false;
    }
    return true;
  }
  if (!installRules()) {
    waitingForSheet = true;
    document.addEventListener('load', installRules, true);
  }
  if (window === window.top) document.addEventListener('bridge-theme-change', event => {
    if (['system', 'light', 'dark'].includes(event.detail?.theme)) window.prompt('codexbridge-theme:__BRIDGE_CLIPBOARD_TOKEN__', event.detail.theme);
  });
  // Cloud authorization lives in the native view; a browser navigation cannot carry it.
  if (window === window.top) document.addEventListener('click', event => {
    if (!window.BridgeCloudWorkbench || !window.BridgeMobileCapabilities?.fileDownload
        || !event.isTrusted || event.defaultPrevented || event.button !== 0
        || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || anchor.hasAttribute('data-image-preview') || typeof BridgeDownload === 'undefined'
        || !BridgeDownload.eligible(anchor.href, location.href)) return;
    event.preventDefault();
    window.prompt('codexbridge-download:__BRIDGE_CLIPBOARD_TOKEN__', anchor.href);
  });
  let focusedAnswer = null, answerTimer = 0, answerDeadline = 0;
  const stopAnswerVisibility = () => {
    clearTimeout(answerTimer);clearTimeout(answerDeadline);answerTimer=answerDeadline=0;
    const {field, scroller} = focusedAnswer || {};
    for(const event of ['beforeinput','compositionstart','input'])field?.removeEventListener(event,stopAnswerVisibility);
    for(const event of ['touchmove','wheel'])scroller?.removeEventListener(event,stopAnswerVisibility);
    focusedAnswer=null;
  };
  const keepAnswerVisible = () => {
    answerTimer = 0;
    const {field, scroller} = focusedAnswer || {};
    if (!field || document.activeElement !== field || !field.isConnected || !scroller.isConnected || !scroller.contains(field)) {stopAnswerVisibility();return;}
    const visual = window.visualViewport;
    const viewportTop = visual?.offsetTop || 0;
    const viewportBottom = viewportTop + (visual?.height || window.innerHeight);
    const clip = scroller.getBoundingClientRect();
    const top = Math.max(clip.top, viewportTop) + 12;
    const bottom = Math.min(clip.bottom, viewportBottom) - 12;
    if (bottom <= top) return;
    const rect = field.getBoundingClientRect();
    let shift = 0;
    if (rect.height > bottom - top) {
      const visiblePart = Math.min(48, bottom - top);
      if (rect.bottom <= top) shift = rect.bottom - top - visiblePart;
      else if (rect.top >= bottom) shift = rect.top - bottom + visiblePart;
    } else if (rect.bottom > bottom) shift = rect.bottom - bottom;
    else if (rect.top < top) shift = rect.top - top;
    if (shift) {scroller.scrollTop += shift;stopAnswerVisibility();}
  };
  const scheduleAnswerVisibility = () => {
    if (!focusedAnswer) return;
    clearTimeout(answerTimer);
    answerTimer = setTimeout(keepAnswerVisible,120);
  };
  document.addEventListener('focusin', event => {
    const field = event.target;
    if (!field.matches?.('textarea, select, input:not([type=checkbox]):not([type=radio])')) return;
    const scroller = field.closest('#approvals') ? document.getElementById('timeline')
      : field.closest('.wb-side-approval')?.closest('.wb-side-approvals')
      || field.closest('.ds-question')?.closest('.ds-requests');
    if (!scroller) return;
    stopAnswerVisibility();
    focusedAnswer = {field, scroller};
    for(const event of ['beforeinput','compositionstart','input'])field.addEventListener(event,stopAnswerVisibility);
    for(const event of ['touchmove','wheel'])scroller.addEventListener(event,stopAnswerVisibility,{passive:true});
    answerDeadline=setTimeout(stopAnswerVisibility,1000);
    scheduleAnswerVisibility();
  });
  document.addEventListener('focusout', event => {
    if (event.target !== focusedAnswer?.field) return;
    stopAnswerVisibility();
  });
  window.visualViewport?.addEventListener('resize', scheduleAnswerVisibility);
  window.visualViewport?.addEventListener('scroll', scheduleAnswerVisibility);
  window.addEventListener('resize', scheduleAnswerVisibility);
  const sync = () => document.documentElement.classList.toggle('bridge-authenticated', !app.hidden);
  sync();
  new MutationObserver(sync).observe(app, { attributes: true, attributeFilter: ['hidden'] });
  let modalOpen;
  const syncModal = () => {
    const open = !!document.querySelector('dialog[open]');
    if (open === modalOpen) return;
    modalOpen = open;
    window.prompt('codexbridge-modal:__BRIDGE_CLIPBOARD_TOKEN__', open ? 'open' : 'closed');
  };
  new MutationObserver(syncModal).observe(document.documentElement,
    { subtree: true, childList: true, attributes: true, attributeFilter: ['open'] });
  syncModal();
  const resyncModal = () => { modalOpen = undefined; syncModal(); };
  window.addEventListener('focus', resyncModal);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) resyncModal(); });
  const account = document.getElementById('accounts-button');
  if (account) {
    const labelAccount = () => {
      if (account.getAttribute('data-i18n') === '账号与额度') {
        const label = document.documentElement.lang.startsWith('en') ? 'Official account' : '官方账号';
        if (account.textContent !== label) account.textContent = label;
      }
    };
    new MutationObserver(labelAccount).observe(account, { attributes: true, attributeFilter: ['data-i18n'], childList: true });
    labelAccount();
  }
  const footer = document.querySelector('.sidebar-foot');
  if (footer && !document.querySelector('.client-navigation')) {
    document.body.appendChild(footer);
    const home = document.createElement('a'); home.href = 'codexbridge://home'; home.className = 'plain bridge-home'; home.dataset.i18n = '返回电脑列表'; home.textContent = typeof BridgeI18n !== 'undefined' ? BridgeI18n.t('返回电脑列表') : '返回电脑列表';
    footer.appendChild(home);
  }
  // The native shell only accepts published addresses and verifies the chosen peer again.
  function channelState(action = 'read') {
    try {
      const raw = window.prompt('codexbridge-channels:__BRIDGE_CLIPBOARD_TOKEN__', action);
      return raw ? JSON.parse(raw) : {};
    } catch { return {}; }
  }
  let bound = '__BRIDGE_ROUTE_BOUND__' === 'yes';
  window.BridgeChannelLink?.start();
  window.BridgeMobileChannels = {
    bound() { bound = true; if (typeof Event === 'function') document.dispatchEvent(new Event('bridge-computer')); },
    mount(dialog) {
      const section=document.createElement('section');section.className='channel-picker-section';
      const en=document.documentElement.lang.startsWith('en');
      const heading=document.createElement('h3'),modes=document.createElement('div'),options=document.createElement('div');
      heading.textContent=en?'Connection channels':'连接信道';
      const refresh=document.createElement('button');refresh.type='button';refresh.className='channel-refresh';
      refresh.textContent='↻';refresh.title=en?'Refresh channel latency':'刷新信道延迟';refresh.setAttribute('aria-label',refresh.title);
      refresh.onclick=()=>{if(navigator.userActivation?.isActive)render('probe');};
      heading.append(refresh);
      modes.className='channel-modes';modes.setAttribute('role','group');modes.setAttribute('aria-label',en?'Switching mode':'切换模式');
      options.className='client-picker-options channel-options';section.append(heading,modes,options);
      const modeButtons=new Map(), entries=new Map();
      for(const [value,title] of [['auto',en?'Automatic':'自动切换'],['manual',en?'Manual':'手动切换']]){
        const button=document.createElement('button');button.type='button';button.textContent=title;
        button.onclick=()=>{if(navigator.userActivation?.isActive){render('mode:'+value);if(value==='auto')window.BridgeChannelLink?.check('network');}};
        modes.append(button);modeButtons.set(value,button);
      }
      function render(action) {
        const state=channelState(action);section.hidden=!state.rows?.length;if(section.hidden)return;
        refresh.disabled=state.probing===true;refresh.setAttribute('aria-busy',String(refresh.disabled));
        for(const [value,button] of modeButtons)button.setAttribute('aria-pressed',String((state.mode||'auto')===value));
        for(const [url,button] of entries)if(!state.rows.some(x=>x.url===url)){button.remove();entries.delete(url);}
        state.rows.forEach(({url,ms},i)=>{
          let button=entries.get(url);
          if(!button){
            button=document.createElement('button');button.type='button';button.className='channel-option';
            const glyph=document.createElement('span'),label=document.createElement('span'),latency=document.createElement('small'),check=document.createElement('span');
            glyph.className='channel-glyph';glyph.setAttribute('aria-hidden','true');glyph.textContent=url.startsWith('https:')?'↗':'⌁';
            label.textContent=url;check.setAttribute('aria-hidden','true');button.append(glyph,label,latency,check);
            button.onclick=()=>{if(!navigator.userActivation?.isActive||button.getAttribute('aria-pressed')==='true')return;channelState('pick:'+url);dialog.close();};
            entries.set(url,button);
          }
          button.children[2].textContent=ms>=0?ms+' ms':'○';
          button.children[2].dataset.latency=ms<0?'unknown':ms<100?'low':ms<300?'medium':ms<1000?'high':'very-high';
          button.children[3].textContent=url===state.current?'✓':'';
          button.setAttribute('aria-pressed',String(url===state.current));
          if(options.children[i]!==button)options.insertBefore(button,options.children[i]||null);
        });
      }
      dialog.append(section);
      render('probe');
      const update = () => render('read');
      window.addEventListener('bridge-channel-sample',update);
      dialog.addEventListener('close',()=>window.removeEventListener('bridge-channel-sample',update),{once:true});
    }
  };
  if (typeof Event === 'function') document.dispatchEvent?.(new Event('bridge-channels-ready'));
  const computer = document.getElementById('connected-computer');
  const name = document.getElementById('computer-name');
  if (computer && name) {
    const connectionName = '__BRIDGE_CONNECTION_NAME__';
    if (bound) {
      const renderAddress = () => {const address=window.BridgeChannelLink?.address||location.origin;name.textContent=address;computer.title=connectionName+' · '+address;};
      document.addEventListener('bridge-computer',renderAddress);window.addEventListener('bridge-channel-changed',renderAddress);renderAddress();
    } else if (connectionName) {
      let device = false;
      try { device = localStorage.getItem('bridge-computer-name-mode') === 'device'; } catch {}
      computer.setAttribute('role', 'button'); computer.tabIndex = 0;
      const render = () => {
        const actual = computer.dataset.deviceName || location.host;
        name.textContent = bound ? (window.BridgeChannelLink?.address||location.origin) : device ? actual : connectionName;
        const text = device ? '点击显示连接名称' : '点击显示电脑真实名称';
        const hint = typeof BridgeI18n !== 'undefined' ? BridgeI18n.t(text) : text;
        computer.title = hint + ' · ' + (device ? connectionName : actual);
        computer.setAttribute('aria-label', name.textContent + ' · ' + hint);
      };
      const toggle = () => {
        device = !device;
        try { localStorage.setItem('bridge-computer-name-mode', device ? 'device' : 'connection'); } catch {}
        render();
      };
      computer.addEventListener('click', toggle);
      computer.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
      });
      window.addEventListener('bridge-channel-changed',render);document.addEventListener('bridge-computer', render);
      document.addEventListener('bridge-language', render);
      render();
    }
  }
})();

// Only a proven route for this gateway receives this checkpoint. Never execute a saved write.
(() => {
  const key = 'bridge-channel-reading';
  const localKey = name => name.startsWith('composer-v1:') || name === 'bridge-client' || name === 'bridge-appearance' || name.startsWith('navigation:');
  const temporaryKey = name => /^(pending:|draft:|skills:|work-mode:|message-edit:|new-chat-attempt$)/.test(name);
  const pairs = (store, keep) => Object.fromEntries(Object.entries(store).filter(([name]) => keep(name)));
  function viewport() {
    return [...document.querySelectorAll('#timeline,.ds-messages,#sessions,.ds-session-list,.ds-list')].find(n => n.clientHeight && n.scrollHeight > n.clientHeight);
  }
  let lastFailure = -Infinity, settleTimer;
  const writing = () => (typeof sending !== 'undefined' && sending) || (typeof approvalBusy !== 'undefined' && approvalBusy) ||
      (typeof desktopSessions !== 'undefined' && desktopSessions.busy);
  function waitForWrite() {
    clearTimeout(settleTimer);
    if (!writing()) { window.prompt('codexbridge-channels:__BRIDGE_CLIPBOARD_TOKEN__','settled'); return; }
    settleTimer=setTimeout(waitForWrite,750);
  }
  window.BridgeMobileHandoff = {
    unreachable() {
      if (document.hidden || performance.now() - lastFailure < 15000) return;
      lastFailure = performance.now();
      window.prompt('codexbridge-channel-failure:__BRIDGE_CLIPBOARD_TOKEN__', '');
    },
    capture() {
      if (writing()) {waitForWrite();return 'busy';}
      if (typeof saveDraft === 'function') saveDraft();
      if (typeof desktopSessions !== 'undefined' && desktopSessions.isChatOpen) desktopSessions.saveDraft();
      const node = viewport();
      const route = typeof browserNavigation !== 'undefined' && browserNavigation.read();
      const hash = route ? '#' + encodeURIComponent(route.id) + '~' + encodeURIComponent(route.provider === 'codex' ? route.host : 'desktop:' + route.provider) : '';
      const data = {version:2, local:pairs(localStorage,localKey), temporary:pairs(sessionStorage,temporaryKey),
        hash, scroll:node && {hash,view:node.id||node.className,top:node.scrollTop,
          anchor:typeof chatTimeline!=='undefined' && chatTimeline?.viewport===node ? chatTimeline.capture() : null, bottom:node.scrollHeight-node.scrollTop-node.clientHeight < 8}};
      const encoded=JSON.stringify(data);
      return new TextEncoder().encode(encoded).length<=524288 ? encoded : 'oversize';
    },
    restore(data) {
      if(data?.version!==2) return;
      for(const [name,value] of Object.entries(data.local||{})) if(localKey(name)&&typeof value==='string')localStorage.setItem(name,value);
      for(const [name,value] of Object.entries(data.temporary||{})) if(temporaryKey(name)&&typeof value==='string')sessionStorage.setItem(name,value);
      if(data.scroll)sessionStorage.setItem(key,JSON.stringify(data.scroll));
      const url=new URL(location.href);url.search='';url.hash=data.hash||'';
      history.replaceState(null,'',url);location.reload();
    }
  };
  let saved;
  try { saved=sessionStorage.getItem(key); } catch { return; }
  if(!saved)return;
  sessionStorage.removeItem(key);
  let position;try{position=JSON.parse(saved);}catch{return;}
  if(position.bottom||!Number.isFinite(position.top))return;
  let stopped=false, loading=false, pages=0, frame=0, observed=null;
  function schedule(){
    if(!stopped&&!frame)frame=requestAnimationFrame(()=>{frame=0;restore();});
  }
  // Rendering, resize and live updates all pass through the timeline's existing restore method.
  window.BridgeMobileHandoff.readingAnchor = timeline => {
    if(stopped || !position.anchor || typeof chatTimeline==='undefined' || chatTimeline!==timeline)return null;
    if(position.hash && location.hash!==position.hash)return null;
    return position.anchor;
  };
  const observer=new MutationObserver(schedule);
  const resize=typeof ResizeObserver==='function'?new ResizeObserver(schedule):null;
  function stop(){
    stopped=true;observer.disconnect();resize?.disconnect();cancelAnimationFrame(frame);clearTimeout(timer);
    window.removeEventListener('pointerdown',stop,true);window.removeEventListener('wheel',stop,true);
    window.removeEventListener('keydown',stop,true);document.removeEventListener('bridge-chat-visible',schedule);
  }
  function restore(){
    const node=viewport();
    if(stopped||!node||(node.id||node.className)!==position.view)return;
    if(observed!==node){resize?.disconnect();resize?.observe(node);if(node.firstElementChild)resize?.observe(node.firstElementChild);observed=node;}
    if(position.anchor && typeof chatTimeline!=='undefined' && chatTimeline?.viewport===node){
      if(chatTimeline.sequence<0 || chatTimeline.cached || chatTimeline.busy)return;
      if(chatTimeline.nodes.has(position.anchor.key)){
        chatTimeline.following=false;
        const current=chatTimeline.capture();
        if(current?.key!==position.anchor.key || Math.abs(current.offset-position.anchor.offset)>1){
          chatTimeline.restore(position.anchor,false);
        }
        // The live timeline can replace its initial page after this first restore.
        // Keep the checkpoint until a user gesture or the end of the handoff.
        return;
      }
      if(chatTimeline.hasMore&&!loading&&pages<6){
        loading=true;pages++;chatTimeline.loadOlder(100).finally(()=>{loading=false;schedule();});return;
      }
      if(loading)return;
    }
    if(node.scrollHeight-node.clientHeight>=position.top && Math.abs(node.scrollTop-position.top)>1)node.scrollTop=position.top;
  }
  const timer=setTimeout(stop,20000);
  window.addEventListener('pointerdown',stop,true);window.addEventListener('wheel',stop,true);window.addEventListener('keydown',stop,true);
  document.addEventListener('bridge-chat-visible',schedule);
  observer.observe(document.body,{childList:true,subtree:true});schedule();
})();
