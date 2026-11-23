'use strict';
(() => {
  let controller, ownerKey = '', request, requested = false;
  const label = text => typeof BridgeI18n === 'undefined' ? text : BridgeI18n.t(text);
  const size = bytes => {
    if (!Number.isFinite(bytes)) return label('正在计算');
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let unit = 0;
    while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit++; }
    return bytes.toLocaleString(undefined, {maximumFractionDigits: unit ? 1 : 0}) + ' ' + units[unit];
  };
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text) element.textContent = label(text);
    if (className) element.className = className;
    return element;
  };
  const button = (text, action) => {
    const element = node('button', text); element.type = 'button';
    element.onclick = async event => {
      try { await action(event); }
      catch (error) { status.textContent = error.message; panel.hidden = false; }
    };
    return element;
  };
  const panel = node('section', '', 'local-transfer-progress download-panel');
  panel.hidden = true; panel.setAttribute('aria-label', label('本地数据传输'));
  const head = node('div', '', 'download-head'), title = node('button', '本地数据传输', 'download-summary');
  const details = node('div', '', 'download-details');
  let collapsed = false, dragged;
  const place = () => {
    if (typeof BridgeDownload !== 'undefined') BridgeDownload.placePanel(panel,{details,summary:title,toggle,collapsed,dragged,label});
  };
  const toggle = button('收起', () => {
    collapsed = !collapsed;details.hidden = collapsed;
    toggle.textContent = label(details.hidden ? '展开' : '收起');
    toggle.setAttribute('aria-expanded', String(!details.hidden));place();
  });
  toggle.className='download-toggle';title.type='button';
  BridgeDownload.bindPanelDrag(panel,title,{place,moved:value=>{dragged=value;},toggle:()=>toggle.click()});
  title.title=label('拖动以移动下载浮窗');
  toggle.setAttribute('aria-expanded', 'true');
  head.append(title, toggle);
  const current = node('p', '', 'download-name'), status = node('span', '', 'download-state');
  status.setAttribute('role', 'status');
  const progress = node('progress', '', 'download-progress'); progress.max = 1;
  const remaining = node('p', '', 'download-note'), actions = node('div', '', 'download-actions');
  const pause = button('暂停', () => controller.pause());
  const resume = button('继续', async () => {
    const state = await controller.state();
    await controller.resume({allowMetered: !state.allowTransfer});
  });
  const cancel = button('取消传输', () => controller.cancel());
  const dismiss = button('关闭', () => controller.dismiss());
  const reopen=button('查看任务',()=>choose());
  actions.append(reopen,pause, resume, cancel, dismiss);
  const metrics=node('div','','download-metrics'),amount=node('span'),rate=node('span');metrics.append(amount,rate);
  details.append(current, status, progress, metrics, remaining, actions); panel.append(head, details);
  document.body.append(panel);
  let sample;
  function paint(task) {
    panel.hidden = !task || task.dismissed === true || task.state === 'cancelled' || ownerKey !== JSON.stringify(window.BridgeChatSyncCache?.owner);
    if (!task) return;
    const reasons = {manual:'已暂停',network:'等待 Wi-Fi',offline:'等待连接网关',space:'空间不足，已暂停',error:'传输中断，点击继续'};
    const finished=task.state==='completed',preparing=task.state==='preparing',ready=task.state==='ready',failed=task.state==='prepare-error';
    status.textContent=label(finished?'传输完成':preparing?'正在准备会话':ready?'准备完成，等待开始':failed?'准备失败':task.state==='running'?'正在传输':reasons[task.reason]||'已暂停');
    current.textContent=task.title||label('本地数据传输');current.title=current.textContent;
    const done=(task.processedBytes??task.downloadedBytes??0)+(task.currentBytes||0),total=task.fullBytes||task.expectedBytes||0;
    amount.textContent=preparing?(task.preparingSessions||0)+' / '+(task.sessionCount||'—')+' '+label('个会话'):size(Math.min(done,total))+' / '+size(total);
    const now=Date.now();
    if(sample?.task!==task||task.state!=='running')sample={task,time:now,bytes:task.downloadedBytes||0,speed:0};
    else if(now-sample.time>=500){sample.speed=Math.max(0,(task.downloadedBytes-sample.bytes)*1000/(now-sample.time));sample.time=now;sample.bytes=task.downloadedBytes;}
    rate.textContent=task.state==='running'?size(sample.speed)+'/s':'';
    remaining.textContent=failed?task.error||'':preparing?label('准备期间可以返回聊天，完成后在这里继续'):ready?label('已准备就绪，查看下载量后开始传输'):finished?label('已保存到手机'):label('待处理')+' '+size(Math.max(0,total-done));
    if(task.clearedItems)remaining.textContent+=' · '+label('部分内容已手动清理');
    if(task.failedSessions)remaining.textContent+=' · '+label('部分会话读取失败');
    if(task.incompleteSessions?.length)remaining.textContent+=' · '+label('当前回合有未同步内容，稍后可补传');
    if(preparing)progress.removeAttribute('value');else progress.value=finished?1:total?Math.min(.99,done/total):0;
    reopen.hidden=!(preparing||ready||failed);pause.hidden=task.state!=='running';resume.hidden=task.state!=='paused';
    resume.textContent=label(task.reason==='network'?'使用当前网络继续':'继续');
    cancel.hidden=finished||preparing||ready||failed;dismiss.hidden=!finished&&!failed;
    title.style.setProperty('--transfer-progress',(finished?100:total?Math.min(100,done/total*100):0)+'%');
    title.textContent='↓ '+status.textContent+(total&&!preparing?' · '+Math.min(100,Math.floor(done/total*100))+'%':preparing?' · '+(task.preparingSessions||0)+'/'+(task.sessionCount||'—'):'');place();
  }
  window.addEventListener('resize',place);window.visualViewport?.addEventListener('resize',place);window.visualViewport?.addEventListener('scroll',place);
  if(typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(place);observer.observe(panel);for(const node of document.querySelectorAll('.composer'))observer.observe(node);}
  setInterval(()=>{if(!document.hidden&&controller?.task?.state==='running')paint(controller.task);},1000);

  function source(session) {
    const providers = {codex: 'Codex', claude: 'Claude', deepseek: 'DeepSeek', zcode: 'ZCode', antigravity: 'Antigravity'};
    return [providers[session.client] || session.client, session.host === 'local' ? label('此电脑') : session.host].filter(Boolean).join(' · ');
  }
  async function choose(previousSelection = [], allSelected = false) {
    if (!controller) { requested = true; return; }
    if(!previousSelection.length&&['preparing','ready','prepare-error','selecting'].includes(controller.task?.state))previousSelection=controller.task.selection?.ids||[];
    if(['running','paused'].includes(controller.task?.state)){panel.hidden=false;collapsed=false;place();return;}
    const dialog = node('dialog', '', 'picker local-transfer-dialog');
    const heading = node('div', '', 'picker-head');
    const close = button('×', () => dialog.close()); close.setAttribute('aria-label', label('关闭')); close.className='icon-button';
    const minimize=button('最小化',()=>{dialog.close();panel.hidden=false;place();});
    minimize.className='plain';minimize.disabled=!['preparing','ready'].includes(controller.task?.state);
    heading.append(node('h2', '本地数据传输'), minimize, close);
    const content = node('div'), feedback = node('p'); feedback.setAttribute('role', 'status');
    const footer = node('div', '', 'local-transfer-actions');
    dialog.append(heading, content, feedback, footer); document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove(), {once: true}); dialog.showModal();
    const selected = new Set(previousSelection), sessions = new Map(); let cursor, busy = false, loading = true, agent = 'codex';
    const providers = {codex:'Codex',claude:'Claude',deepseek:'DeepSeek',zcode:'ZCode',antigravity:'Antigravity'};
    const tabs = node('div', '', 'local-transfer-tabs workbench-tabs'), count = node('p', '', 'local-transfer-count');
    tabs.setAttribute('role', 'tablist');tabs.setAttribute('aria-label', label('Agent'));
    let unavailable = [];
    const list = node('div', '', 'local-transfer-sessions');
    const selectAll = button('全选当前 Agent', () => {
      if (busy) return;
      for (const session of sessions.values()) if (session.client === agent) selected.add(session.id);
      allSelected = false; draw();
    });
    selectAll.className='plain';
    const next = button('查看下载量', async () => {
      if (busy || !selected.size) return;
      busy = true; minimize.disabled=false; next.disabled = true; selectAll.disabled = true;
      feedback.textContent = label('正在准备会话…');
      try {
        const selectedChat = typeof browserNavigation === 'undefined' ? null : browserNavigation.read();
        const prepared = await controller.prepare([...selected], selectedChat?.id, page => {
          if (dialog.open) feedback.textContent = label('正在准备会话') + ' ' + (page.summary?.processed || 0) + '/' + page.sessionCount +
            (page.summary?.currentTitle ? ' · ' + page.summary.currentTitle : '');
        });
        if (dialog.open) await confirm(prepared);
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; next.disabled = !selected.size; selectAll.disabled = loading; }
    });
    next.className = 'primary'; next.disabled = true;
    const retry = button('重试读取', () => load().catch(error => { feedback.textContent = error.message; retry.hidden = false; }));
    retry.hidden = true;
    content.append(tabs, count, selectAll, list, retry); footer.append(next);
    function draw() {
      tabs.replaceChildren();
      for (const [id, name] of Object.entries(providers)) {
        const total = [...sessions.values()].filter(session => session.client === id).length;
        const tab = button(name + ' (' + total + ')', () => { if (!busy) {agent = id; draw();} });
        tab.setAttribute('role', 'tab');tab.setAttribute('aria-selected', String(agent === id));tabs.append(tab);
      }
      count.textContent = label('已选择') + ' ' + selected.size + ' ' + label('个');
      list.replaceChildren();
      for (const session of sessions.values()) {
        if (session.client !== agent) continue;
        const line = node('label', '', 'local-transfer-choice'), input = node('input');
        input.type = 'checkbox'; input.checked = selected.has(session.id);
        input.onchange = () => {
          if (busy) { input.checked = selected.has(session.id); return; }
          allSelected = false;
          if (input.checked) selected.add(session.id); else selected.delete(session.id);
          count.textContent = label('已选择') + ' ' + selected.size + ' ' + label('个');
          next.disabled = loading || !selected.size;
        };
        const text = node('span'), name = node('strong'); name.textContent = session.title;
        const detail = node('small'); detail.textContent = source(session);
        text.append(name, detail); line.append(input, text); list.append(line);
      }
      selectAll.disabled = loading || busy;
      const failed = unavailable.filter(item => item.client === agent);
      feedback.textContent = loading ? label('正在读取全部会话…') : failed.length ? label('部分接入读取失败，请稍后重试') :
        [...sessions.values()].some(session => session.client === agent) ? '' : label('暂无可传输的会话');
      next.disabled = loading || !selected.size;
    }
    async function load() {
      retry.hidden = true;
      do {
        const page = await request('/api/history-transfer/catalog' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''));
        if (!dialog.open) return;
        for (const session of page.sessions || []) sessions.set(session.id, session);
        if(!cursor)controller.recordBatch=page.recordBatch;
        unavailable = page.unavailable || [];cursor = page.nextCursor;
        loading = !!cursor; draw();
      } while (cursor);
    }
    async function confirm(prepared) {
      const state = await controller.state();
      content.replaceChildren(); footer.replaceChildren(); feedback.textContent = '';
      const values = node('dl', '', 'local-transfer-sizes');
      for (const [name, bytes] of [['预计最多下载', prepared.downloadBytes]])
        values.append(node('dt', name), node('dd', size(bytes)));
      content.append(values,node('p','已完整保存的内容会自动跳过，实际下载量可能更少。'));
      if (prepared.failedSessions) feedback.textContent = label('部分会话读取失败') + ' · ' + prepared.failedSessions;
      if (prepared.warnings?.length) feedback.textContent += (feedback.textContent ? ' · ' : '') + label('当前回合有未同步内容，稍后可补传') + ' (' + prepared.warnings.length + ')';
      const start = button(state.allowTransfer ? '开始传输' : '使用当前网络开始传输', async () => {
        start.disabled = true;
        try {
          await controller.start(prepared, {allowMetered: !state.allowTransfer}); dialog.close();
        } catch (error) { feedback.textContent = error.message; start.disabled = false; }
      });
      start.className = 'primary';
      const back = button('调整选择', () => { dialog.close(); if(controller.task?.state==='ready')controller.task.state='selecting'; void choose([...selected], allSelected); });
      footer.append(back, start);
      if (Number.isFinite(state.freeBytes) && state.freeBytes < prepared.downloadBytes) {
        content.append(node('p', label('空间还差') + ' ' + size(prepared.downloadBytes - state.freeBytes)));
        start.textContent = label(state.allowTransfer ? '先传一部分' : '使用当前网络先传一部分');
      }
    }
    try {
      if(controller.task?.state==='preparing'){
        content.replaceChildren(node('p','正在准备会话，可以最小化后继续聊天'));footer.replaceChildren();
        const prepared=await controller.preparing;
        if(dialog.open&&prepared)await confirm(prepared);
      }else if(controller.task?.state==='ready'&&controller.task.prepared){
        await confirm(controller.task.prepared);
      }else await load();
    }catch(error){feedback.textContent=error.message;retry.hidden=false;}
  }
  async function connect(api, {open = false} = {}) {
    requested ||= open;
    const cache = window.BridgeChatSyncCache, native = window.CodexBridgeNativeOffline;
    if (!cache?.owner || !native) return;
    request = api;
    const key = JSON.stringify(cache.owner);
    if (ownerKey !== key) {
      if (controller) await controller.pause('auth').catch(() => {});
      panel.hidden = true;
      ownerKey = key;
      const owner = {...cache.owner};
      controller = new HistoryTransferTask({
        request: (...args) => request(...args), changed: paint,
        native: (type, fields) => {
          if (JSON.stringify(cache.owner) !== key) return Promise.reject(Error(label('请重新连接网关')));
          return native.request({type, ...owner, ...fields});
        },
      });
      try { await controller.restore(); } catch { /* Older phone builds keep ordinary chat available. */ }
    }
    if (requested) { requested = false; await choose(); }
  }
  window.addEventListener('codexbridge-native-offline-state', event => {
    if (controller && typeof event.detail?.networkAvailable === 'boolean')
      void controller.network(event.detail).catch(() => {});
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && controller) controller.state().then(state => controller.network(state)).catch(() => {});
  });
  const pauseForLogin = () => {
    panel.hidden = true;
    if (controller) void controller.pause('auth').catch(() => {});
  };
  document.addEventListener('bridge-auth-lost', pauseForLogin);
  document.addEventListener('bridge-logged-out', pauseForLogin);
  window.BridgeHistoryTransfer = {connect, open: choose, size};
})();
