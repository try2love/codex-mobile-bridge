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
  const panel = node('section', '', 'local-transfer-progress');
  panel.hidden = true; panel.setAttribute('aria-label', label('本地数据传输'));
  const head = node('div', '', 'local-transfer-head'), title = node('strong', '本地数据传输');
  const details = node('div', '', 'local-transfer-body');
  const toggle = button('收起', () => {
    details.hidden = !details.hidden;
    toggle.textContent = label(details.hidden ? '展开' : '收起');
    toggle.setAttribute('aria-expanded', String(!details.hidden));
  });
  toggle.setAttribute('aria-expanded', 'true');
  head.append(title, toggle);
  const current = node('p', '', 'local-transfer-current'), status = node('p');
  status.setAttribute('role', 'status');
  const progress = node('progress'); progress.max = 1;
  const remaining = node('p', '', 'local-transfer-remaining'), actions = node('div', '', 'local-transfer-actions');
  const pause = button('暂停', () => controller.pause());
  const resume = button('继续', async () => {
    const state = await controller.state();
    await controller.resume({allowMetered: !state.allowTransfer});
  });
  const cancel = button('取消传输', () => controller.cancel());
  const dismiss = button('关闭', () => { panel.hidden = true; });
  actions.append(pause, resume, cancel, dismiss);
  details.append(current, status, progress, remaining, actions); panel.append(head, details);
  document.body.append(panel);
  function paint(task) {
    panel.hidden = !task || task.state === 'cancelled';
    if (!task) return;
    const reasons = {manual: '已暂停', network: '等待 Wi-Fi', offline: '等待连接网关', space: '空间不足，已暂停', error: '传输中断，点击继续'};
    const finished = task.state === 'completed';
    status.textContent = label(finished ? '传输完成' : task.state === 'running' ? '正在传输' : reasons[task.reason] || '已暂停');
    current.textContent = task.title || '';
    current.title = task.title || '';
    const left = Math.max(0, task.expectedBytes - task.downloadedBytes);
    remaining.textContent = finished ? label('已保存到手机') : label('预计剩余') + ' ' + size(left);
    if (task.clearedItems) remaining.textContent += ' · ' + label('部分内容已手动清理');
    if (task.failedSessions) remaining.textContent += ' · ' + label('部分会话读取失败');
    if (finished) progress.value = 1;
    else if (task.expectedBytes) progress.value = Math.min(.99, task.downloadedBytes / task.expectedBytes);
    else progress.removeAttribute('value');
    pause.hidden = task.state !== 'running'; resume.hidden = task.state !== 'paused';
    resume.textContent = label(task.reason === 'network' ? '使用当前网络继续' : '继续');
    cancel.hidden = finished; dismiss.hidden = !finished;
  }
  function source(session) {
    const providers = {codex: 'Codex', claude: 'Claude', deepseek: 'DeepSeek', zcode: 'ZCode', antigravity: 'Antigravity'};
    return [providers[session.client] || session.client, session.host === 'local' ? label('此电脑') : session.host].filter(Boolean).join(' · ');
  }
  async function choose(previousSelection = [], allSelected = false) {
    if (!controller) { requested = true; return; }
    const dialog = node('dialog', '', 'picker local-transfer-dialog');
    const heading = node('div', '', 'picker-head');
    const close = button('×', () => dialog.close()); close.setAttribute('aria-label', label('关闭'));
    heading.append(node('h2', '本地数据传输'), close);
    const content = node('div'), feedback = node('p'); feedback.setAttribute('role', 'status');
    const footer = node('div', '', 'local-transfer-actions');
    dialog.append(heading, content, feedback, footer); document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove(), {once: true}); dialog.showModal();
    const selected = new Set(previousSelection), sessions = new Map(); let cursor, busy = false;
    const list = node('div', '', 'local-transfer-sessions');
    const selectAll = button('全选', async () => {
      if (busy) return;
      busy = true; selectAll.disabled = true; next.disabled = true;
      try {
        while (cursor) await load();
        for (const id of sessions.keys()) selected.add(id);
        allSelected = true;
        draw();
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; selectAll.disabled = false; next.disabled = !selected.size; }
    });
    const more = button('加载更多会话', () => load().catch(error => { feedback.textContent = error.message; }));
    const next = button('查看下载量', async () => {
      if (busy || !selected.size) return;
      busy = true; next.disabled = true; more.disabled = true; selectAll.disabled = true;
      feedback.textContent = label('正在计算下载量并核对已有缓存…');
      try {
        const selectedChat = typeof browserNavigation === 'undefined' ? null : browserNavigation.read();
        const prepared = await controller.prepare(allSelected ? {all: true} : [...selected], selectedChat?.id);
        if (dialog.open) await confirm(prepared);
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; next.disabled = !selected.size; more.disabled = false; selectAll.disabled = false; }
    });
    next.className = 'primary'; next.disabled = true;
    content.append(selectAll, list, more); footer.append(next);
    function draw() {
      list.replaceChildren();
      for (const session of sessions.values()) {
        const line = node('label', '', 'local-transfer-choice'), input = node('input');
        input.type = 'checkbox'; input.checked = selected.has(session.id);
        input.onchange = () => {
          if (busy) { input.checked = selected.has(session.id); return; }
          allSelected = false;
          if (input.checked) selected.add(session.id); else selected.delete(session.id);
          next.disabled = !selected.size;
        };
        const text = node('span'), name = node('strong'); name.textContent = session.title;
        const detail = node('small'); detail.textContent = source(session);
        text.append(name, detail); line.append(input, text); list.append(line);
      }
      more.hidden = !cursor;
      next.disabled = !selected.size;
    }
    async function load() {
      more.disabled = true;
      const page = await request('/api/history-transfer/catalog' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''));
      if (!dialog.open) return;
      for (const session of page.sessions || []) sessions.set(session.id, session);
      cursor = page.nextCursor; draw(); more.disabled = false;
      feedback.textContent = sessions.size ? '' : label('暂无可传输的会话');
    }
    async function confirm(prepared) {
      const state = await controller.state();
      content.replaceChildren(); footer.replaceChildren(); feedback.textContent = '';
      const values = node('dl', '', 'local-transfer-sizes');
      for (const [name, bytes] of [['会话全量', prepared.fullBytes], ['本次预计下载量', prepared.downloadBytes], ['复用已有缓存', prepared.reusedBytes]])
        values.append(node('dt', name), node('dd', size(bytes)));
      content.append(values);
      if (prepared.failedSessions) feedback.textContent = label('部分会话读取失败') + ' · ' + prepared.failedSessions;
      const start = button(state.allowTransfer ? '开始传输' : '使用当前网络开始传输', async () => {
        start.disabled = true;
        try {
          await controller.start(prepared, {allowMetered: !state.allowTransfer}); dialog.close();
        } catch (error) { feedback.textContent = error.message; start.disabled = false; }
      });
      start.className = 'primary';
      const back = button('调整选择', () => { dialog.close(); void choose([...selected], allSelected); });
      footer.append(back, start);
      if (Number.isFinite(state.freeBytes) && state.freeBytes < prepared.downloadBytes) {
        content.append(node('p', label('空间还差') + ' ' + size(prepared.downloadBytes - state.freeBytes)));
        start.textContent = label(state.allowTransfer ? '先传一部分' : '使用当前网络先传一部分');
      }
    }
    try { await load(); } catch (error) { feedback.textContent = error.message; }
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
