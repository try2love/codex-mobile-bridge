'use strict';
(() => {
  const marker = '\ue200visualize\ue202', endMarker = '\ue201', maxBytes = 1024 * 1024;
  const t = value => typeof BridgeI18n === 'undefined' ? value : BridgeI18n.t(value);
  const active = new Map();
  const theme = () => document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  function read(source, offset = 0) {
    if (!source.startsWith(marker, offset)) return null;
    const relativeEnd = source.slice(offset, offset + 8192).indexOf(endMarker, marker.length);
    if (relativeEnd < 0) return null;
    const end = offset + relativeEnd;
    try {
      const value = JSON.parse(source.slice(offset + marker.length, end));
      if (!value || Array.isArray(value) || typeof value !== 'object' || Object.keys(value).some(key => !['path','mode','title'].includes(key)) ||
          typeof value.path !== 'string' || value.path.length > 4096 || /[\x00-\x1f]/.test(value.path) ||
          !/^(?:\/|[a-z]:[\\/]|\\\\)/i.test(value.path) || !/\.html?$/i.test(value.path) ||
          value.mode !== undefined && !['regular','wide'].includes(value.mode) || value.title !== undefined && (typeof value.title !== 'string' || value.title.length > 200)) return null;
      return {...value, end:end + endMarker.length};
    } catch { return null; }
  }
  function saved(key) {
    try { const value = sessionStorage.getItem(key); if (value && value.length <= 16384) return JSON.parse(value); } catch {}
    return null;
  }
  function persist(key, state) {
    const serialized = JSON.stringify(state);
    if (!state || typeof state !== 'object' || Array.isArray(state) || new TextEncoder().encode(serialized).length > 16384) return false;
    try {
      const keys = Object.keys(sessionStorage).filter(item => item.startsWith('bridge-visualize:') && item !== key);
      while (keys.length >= 99) sessionStorage.removeItem(keys.shift());
      sessionStorage.setItem(key, serialized);
    }
    catch { return false; }
    return true;
  }
  function button(label, action) {
    const node = document.createElement('button'); node.type = 'button'; node.className = 'plain'; node.dataset.i18n = label; node.textContent = t(label); node.onclick = action; return node;
  }
  function create(value, {owner, resolve}) {
    const context = owner.__visualizeContext;
    const card = document.createElement('section'); card.className = 'visualize-card'; card.dataset.mode = value.mode || 'regular';
    card.setAttribute('aria-label', value.title || t('交互图表'));
    const toolbar = document.createElement('div'); toolbar.className = 'visualize-toolbar';
    const label = document.createElement('span'); label.textContent = value.title || t('交互图表'); if (!value.title) label.dataset.i18n = '交互图表';
    const stage = document.createElement('div'); stage.className = 'visualize-stage';
    const status = document.createElement('p'); status.className = 'visualize-status'; status.setAttribute('role', 'status');
    const promptArea = document.createElement('div'); promptArea.className = 'visualize-followup'; promptArea.hidden = true;
    const promptText = document.createElement('p');
    const promptButton = button('放入输入框', () => {
      if (!context?.canCompose() || !card.isConnected) return;
      owner.dispatchEvent(new CustomEvent('bridge-compose-prompt', {bubbles:true, detail:{text:promptText.textContent}}));
      promptArea.hidden = true; closeFullscreen();
    });
    promptArea.append(promptText, promptButton);
    const retry = button('重试', () => load()); retry.hidden = true;
    const full = button('全屏查看', () => openFullscreen()); full.disabled = true;
    const surface = document.createElement('dialog'); surface.className = 'visualize-surface'; surface.setAttribute('role','region');
    surface.setAttribute('aria-label', value.title || t('交互图表')); surface.dataset.mode = value.mode || 'regular';
    const closeBar = document.createElement('div'); closeBar.className = 'visualize-closebar';
    const closeButton = button('关闭', () => closeFullscreen()); closeBar.append(closeButton);
    surface.append(closeBar, stage, promptArea);
    toolbar.append(label, retry, full); card.append(toolbar, surface, status);
    let frame, instance, revision, source, timer, controller, disposed = false;
    let started = false, stateKey, height = 300;
    const post = data => { if (frame?.contentWindow && instance) frame.contentWindow.postMessage({instance, ...data}, '*'); };
    const resetFrame = () => { clearTimeout(timer); frame?.remove(); frame = null; instance = null; full.disabled = true; };
    function fail(message = '交互图表加载失败，请重试') {
      closeFullscreen(); resetFrame(); status.textContent = t(message); retry.hidden = false;
    }
    function mount() {
      resetFrame();
      frame = document.createElement('iframe'); frame.title = value.title || t('交互图表');
      frame.setAttribute('sandbox', 'allow-scripts'); frame.setAttribute('referrerpolicy', 'no-referrer');
      frame.style.height = height + 'px';
      frame.src = (window.BridgeRelayBase || '') + '/visualize-shell'; stage.replaceChildren(frame);
      timer = setTimeout(() => fail(), 15000);
    }
    async function load() {
      if (disposed || !context || context.signal?.aborted) return;
      started = true; controller?.abort(); controller = new AbortController(); resetFrame();
      status.textContent = t('正在加载交互图表…'); retry.hidden = true; promptArea.hidden = true;
      const current = controller;
      try {
        const file = resolve(value.path);
        if (!file?.visualization) throw Error('可视化文件未被此聊天引用');
        const url = new URL(context.url(file.id), location.href);
        if (url.origin !== location.origin) throw Error('可视化地址无效');
        const response = await fetch(url, {credentials:'same-origin', redirect:'error', cache:'no-store', signal:current.signal});
        if (!response.ok) throw Error('交互图表加载失败，请重试');
        // Bound the wire response before JSON parsing, including chunked responses.
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let wire = '', bytes = 0;
        try {
          while (true) {
            const {done, value:chunk} = await reader.read(); if (done) break;
            bytes += chunk.length;
            if (bytes > maxBytes * 6 + 2048) { await reader.cancel(); throw Error('可视化内容超过 1 MB'); }
            wire += decoder.decode(chunk, {stream:true});
          }
        } finally { reader.releaseLock(); }
        const result = JSON.parse(wire + decoder.decode());
        if (disposed || current !== controller || current.signal.aborted) return;
        if (typeof result.text !== 'string' || new TextEncoder().encode(result.text).length > maxBytes || !/^[a-f0-9]{64}$/.test(result.revision)) throw Error('可视化内容无效');
        source = result.text; revision = result.revision;
        stateKey = 'bridge-visualize:' + JSON.stringify([location.pathname, context.key, file.id, revision]);
        mount();
      } catch (error) { if (!disposed && !current.signal.aborted && current === controller) fail(error.message); }
    }
    function receive(event) {
      const data = event.data;
      if (disposed || !frame || event.source !== frame.contentWindow || event.origin !== 'null') return;
      if (data?.type === 'bridge-visualize-ready' && typeof data.instance === 'string' && /^[a-f0-9]{32}$/.test(data.instance) && data.instance !== instance) {
        instance = data.instance;
        post({type:'bridge-visualize-content', text:source, theme:theme(), state:saved(stateKey)}); return;
      }
      if (!instance || data?.instance !== instance || !card.isConnected || context.signal?.aborted) return;
      if (data.type === 'bridge-visualize-mounted') { clearTimeout(timer); status.textContent = ''; full.disabled = false; }
      else if (data.type === 'bridge-visualize-resize' && Number.isFinite(data.height)) {
        height = Math.min(12000, Math.max(80, Math.ceil(data.height)));
        frame.style.height = height + 'px';
      } else if (data.type === 'bridge-visualize-state' && Number.isSafeInteger(data.requestId)) {
        let ok = false; try { ok = persist(stateKey, data.state); } catch {}
        post({type:'bridge-visualize-state-result', requestId:data.requestId, ok});
        if (!ok) status.textContent = t('交互状态保存失败');
      } else if (data.type === 'bridge-visualize-followup' && context.canCompose() && typeof data.prompt === 'string' && data.prompt.trim() && data.prompt.length <= 10000) {
        promptText.textContent = data.prompt; promptArea.hidden = false;
      } else if (data.type === 'bridge-visualize-escape') closeFullscreen();
    }
    function closeFullscreen() { if (surface.classList.contains('visualize-dialog')) surface.close(); }
    function openFullscreen() {
      if (disposed || !frame || surface.classList.contains('visualize-dialog')) return;
      // Keep the iframe in its original parent so arbitrary controls and script
      // memory survive fullscreen, even without the widget-state API.
      surface.classList.add('visualize-dialog'); surface.setAttribute('role','dialog');
      surface.showModal(); closeButton.focus({preventScroll:true});
    }
    surface.addEventListener('close', () => {
      surface.classList.remove('visualize-dialog'); surface.setAttribute('role','region');
      if (!disposed && full.isConnected) full.focus({preventScroll:true});
    });
    function dispose() {
      if (disposed) return; disposed = true;
      closeFullscreen(); controller?.abort(); resetFrame(); observer.disconnect();
      window.removeEventListener('message', receive); context?.signal?.removeEventListener('abort', dispose); active.delete(card);
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && !started) { observer.disconnect(); load(); }
    }, {rootMargin:'300px'});
    if (context) observer.observe(card);
    else { status.textContent = t('此聊天暂时无法读取交互图表'); retry.hidden = true; }
    window.addEventListener('message', receive); context?.signal?.addEventListener('abort', dispose, {once:true});
    active.set(card, {dispose, updateTheme:() => post({type:'bridge-visualize-theme', theme:theme()})});
    return card;
  }
  new MutationObserver(() => {
    for (const [card, controller] of active) if (!card.isConnected) controller.dispose();
  }).observe(document.documentElement, {childList:true, subtree:true});
  new MutationObserver(() => { for (const controller of active.values()) controller.updateTheme(); })
    .observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
  window.BridgeVisualize = {read, create, install(markdown) {
    markdown.block.ruler.before('paragraph', 'visualize', (state, startLine, endLine, silent) => {
      const offset = state.bMarks[startLine] + state.tShift[startLine], value = read(state.src, offset);
      if (!value || state.src.slice(value.end).split('\n', 1)[0].trim()) return false;
      if (silent) return true;
      let nextLine = startLine; while (nextLine < endLine && state.bMarks[nextLine] < value.end) nextLine++;
      const token = state.push('visualize', '', 0); token.meta = value; token.content = state.src.slice(offset, value.end); token.map = [startLine,nextLine]; state.line = nextLine; return true;
    }, {alt:['paragraph']});
  }};
})();
