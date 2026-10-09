'use strict';
// One foreground transfer, independent of the selected chat and workbench tabs.
const BridgeDownload = (() => {
  const CHUNK = 1024 * 1024, LIMIT = 50 * 1024 * 1024;
  const changed = '源文件已变化，请重新下载。';
  const invalid = '下载响应不完整或格式不正确，请重试。';
  function eligible(value, base) {
    try {
      const url = new URL(value, base);
      return url.origin === new URL(base).origin && !url.username && !url.password &&
        (/^\/api\/sessions\/[0-9a-f-]{36}\/(?:files\/[a-f0-9]{64}|workspace\/download)$/.test(url.pathname) ||
         /^\/api\/desktop-sessions\/(?:claude|deepseek)\/workspace\/download$/.test(url.pathname));
    } catch { return false; }
  }
  const host = typeof module === 'object' && module.exports ? require('../../hosts/environment.js') : BridgeHost;
  const native = host.isNativeApp;
  function filename(disposition, fallback) {
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition || '');
    const quoted = /filename="([^"]+)"/i.exec(disposition || '');
    let name = quoted?.[1] || fallback || 'download';
    try { if (encoded) name = decodeURIComponent(encoded[1]); } catch {}
    return name.split(/[\\/]/).pop().replace(/[\x00-\x1f\x7f]/g, '').slice(0, 240) || 'download';
  }
  function strongTag(value) { return /^"[^"\r\n]+"$/.test(value || '') ? value : null; }
  class Task {
    constructor({fetcher = (...args) => fetch(...args), onChange = () => {}, clock = () => performance.now()} = {}) {
      this.fetcher = fetcher; this.onChange = onChange; this.clock = clock;
      this.version = 0; this.status = 'idle'; this.parts = []; this.bytes = 0; this.total = null;
    }
    emit() { this.onChange(this); }
    start(url, name) {
      this.cancel(false);
      Object.assign(this, {url, name:filename('', name), parts:[], bytes:0, total:null, etag:null,
        ranged:false, restartRequired:false, error:'', speed:0});
      return this.resume();
    }
    cancel(notify = true) {
      this.version++; this.controller?.abort(); this.controller = null;
      this.parts = []; this.bytes = 0; this.total = null; this.speed = 0; this.status = 'idle';
      if (notify) this.emit();
    }
    pause() {
      if (this.status !== 'downloading') return;
      this.version++; this.controller?.abort(); this.controller = null;
      this.status = 'paused'; this.speed = 0;
      this.restartRequired = Boolean(this.bytes && (!this.ranged || !this.etag));
      if (this.restartRequired) this.error = '此连接不支持断点续传，请重新下载。';
      this.emit();
    }
    restart() { return this.start(this.url, this.name); }
    async resume() {
      if (this.status === 'downloading' || this.status === 'complete' || this.status === 'exported') return;
      if (this.bytes && (!this.ranged || !this.etag || this.restartRequired)) {
        this.status = 'error'; this.restartRequired = true;
        this.error = '此连接不支持断点续传，请重新下载。'; this.emit(); return;
      }
      const version = ++this.version;
      const controller = this.controller = new AbortController();
      this.status = 'downloading'; this.error = ''; this.speed = 0;
      let sampleTime = this.clock(), sampleBytes = this.bytes;
      this.emit();
      try {
        while (version === this.version) {
          const offset = this.bytes, end = Math.max(offset, Math.min(offset + CHUNK - 1, LIMIT - 1));
          const headers = {Range:`bytes=${offset}-${end}`};
          if (offset && this.etag) headers['If-Range'] = this.etag;
          const response = await this.fetcher(this.url, {
            credentials:'same-origin', cache:'no-store', redirect:'error', headers, signal:controller.signal
          });
          if (version !== this.version) { await response.body?.cancel(); return; }
          const etag = strongTag(response.headers.get('ETag'));
          if (response.status === 416) {
            const match = /^bytes \*\/(\d+)$/.exec(response.headers.get('Content-Range') || '');
            if (match && Number(match[1]) === offset && (!offset || (etag === this.etag && (this.total === null || this.total === offset)))) {
              this.total = offset; await response.body?.cancel(); break;
            }
            throw Error(changed);
          }
          if (response.status === 401 || response.status === 403) throw Error('登录已失效，请重新登录后继续下载。');
          if (response.status === 409 && (await response.json()).code === 'download_changed') throw Error(changed);
          if (response.status !== 200 && response.status !== 206) throw Error('下载失败，请检查连接后重试。');
          if (offset && (response.status !== 206 || !etag || etag !== this.etag)) throw Error(changed);
          let expected = null;
          const length = response.headers.get('Content-Length');
          if (length !== null && !/^\d+$/.test(length)) throw Error(invalid);
          if (response.status === 206) {
            const range = /^bytes (\d+)-(\d+)\/(\d+|\*)$/.exec(response.headers.get('Content-Range') || '');
            if (!range) throw Error(invalid);
            const first = Number(range[1]), last = Number(range[2]);
            const total = range[3] === '*' ? null : Number(range[3]);
            if (first !== offset || last < first || last > end || (total !== null && last >= total)) throw Error(invalid);
            if (this.total !== null && total !== this.total) throw Error(changed);
            expected = last - first + 1;
            if (length !== null && Number(length) !== expected) throw Error(invalid);
            this.total = total; this.ranged = true;
          } else {
            this.total = length === null ? null : Number(length);
            expected = this.total; this.ranged = false;
          }
          if (this.total > LIMIT) throw Error('文件超过 50 MB 下载限制。');
          this.etag = etag;
          this.name = filename(response.headers.get('Content-Disposition'), this.name);
          // Partial data can only be retained across requests with a strong validator.
          if (this.ranged && !etag && (this.total === null || this.total > expected)) throw Error('此连接不支持断点续传，请重新下载。');
          this.emit();
          if (!response.body) throw Error(invalid);
          const reader = response.body.getReader();
          let received = 0;
          while (true) {
            const {done, value} = await reader.read();
            if (version !== this.version) { await reader.cancel(); return; }
            if (done) break;
            received += value.byteLength;
            if (this.bytes + value.byteLength > LIMIT) throw Error('文件超过 50 MB 下载限制。');
            if (expected !== null && received > expected) throw Error(invalid);
            this.parts.push(value); this.bytes += value.byteLength;
            const now = this.clock(), elapsed = now - sampleTime;
            if (elapsed >= 200) {
              const current = (this.bytes - sampleBytes) * 1000 / elapsed;
              this.speed = this.speed ? this.speed * .65 + current * .35 : current;
              sampleTime = now; sampleBytes = this.bytes; this.emit();
            }
          }
          if (expected !== null && received !== expected) throw Error('下载中断，请继续下载。');
          if (!this.ranged || this.bytes === this.total) { this.total = this.bytes; break; }
        }
        if (version !== this.version) return;
        this.status = 'complete'; this.speed = 0; this.controller = null; this.emit();
      } catch (error) {
        if (version !== this.version) return;
        controller.abort(); this.controller = null; this.status = 'error'; this.speed = 0;
        this.error = error.message === 'Failed to fetch' ? '下载中断，请继续下载。' : error.message;
        if (![changed, invalid, '登录已失效，请重新登录后继续下载。', '下载中断，请继续下载。',
          '下载失败，请检查连接后重试。', '文件超过 50 MB 下载限制。', '此连接不支持断点续传，请重新下载。'].includes(this.error)) this.error = '下载中断，请继续下载。';
        this.restartRequired = [changed, invalid, '文件超过 50 MB 下载限制。'].includes(this.error) || Boolean(this.bytes && (!this.ranged || !this.etag));
        this.emit();
      }
    }
    blob() {
      if (!['complete', 'exported'].includes(this.status)) throw Error('下载尚未完成。');
      return new Blob(this.parts, {type:'application/octet-stream'});
    }
  }
  return {Task, eligible, native, filename};
})();
if (typeof module !== 'undefined') module.exports = BridgeDownload;

if (typeof document !== 'undefined') (() => {
  if (BridgeDownload.native(navigator.userAgent)) return;
  const t = text => typeof BridgeI18n === 'undefined' ? text : BridgeI18n.t(text);
  const units = bytes => {
    if (bytes < 1024) return Math.round(bytes) + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };
  let panel, summary, details, name, state, progress, amount, speed, toggle, pause, save, cancel, note;
  let collapsed = false, dragged = null, drag = null, frame = null;
  const objectURLs = new Set();
  const task = new BridgeDownload.Task({onChange:render});
  function element(tag, className, parent) {
    const node = document.createElement(tag); node.className = className;
    if (parent) parent.append(node); return node;
  }
  function button(parent, action) {
    const node = element('button', '', parent); node.type = 'button'; node.onclick = action; return node;
  }
  function clear() {
    task.cancel(); for (const url of objectURLs) URL.revokeObjectURL(url); objectURLs.clear();
  }
  function create() {
    panel = element('section', 'download-panel', document.body); panel.id = 'download-panel';
    panel.setAttribute('aria-label', t('文件下载'));
    const header = element('div', 'download-head', panel);
    summary = element('button', 'download-summary', header); summary.type = 'button';
    summary.onclick = () => { if (!drag?.moved) { collapsed = !collapsed; render(); } };
    summary.onpointerdown = event => {
      if (event.button || matchMedia('(max-width:720px)').matches) return;
      const rect = panel.getBoundingClientRect();
      drag = {x:event.clientX, y:event.clientY, left:rect.left, top:rect.top, moved:false};
      summary.setPointerCapture(event.pointerId);
    };
    summary.onpointermove = event => {
      if (!drag || !summary.hasPointerCapture(event.pointerId)) return;
      if (Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 5) drag.moved = true;
      if (drag.moved) { dragged = {x:drag.left + event.clientX - drag.x, y:drag.top + event.clientY - drag.y}; place(); }
    };
    summary.onpointerup = () => { setTimeout(() => { drag = null; }, 0); };
    summary.onpointercancel = () => { drag = null; };
    summary.onkeydown = event => {
      const direction = {ArrowLeft:[-16,0], ArrowRight:[16,0], ArrowUp:[0,-16], ArrowDown:[0,16]}[event.key];
      if (!direction || matchMedia('(max-width:720px)').matches) return;
      event.preventDefault(); const rect = panel.getBoundingClientRect();
      dragged = {x:rect.left + direction[0], y:rect.top + direction[1]}; place();
    };
    toggle = button(header, () => { collapsed = !collapsed; render(); }); toggle.className = 'download-toggle';
    details = element('div', 'download-details', panel);
    name = element('strong', 'download-name', details);
    state = element('span', 'download-state', details); state.setAttribute('role', 'status');
    progress = element('progress', 'download-progress', details); progress.max = 100;
    const metrics = element('div', 'download-metrics', details);
    amount = element('span', '', metrics); speed = element('span', '', metrics);
    note = element('p', 'download-note', details); note.setAttribute('role', 'status');
    const actions = element('div', 'download-actions', details);
    pause = button(actions, () => task.status === 'downloading' ? task.pause() : task.restartRequired ? task.restart() : task.resume());
    save = button(actions, () => {
      const url = URL.createObjectURL(task.blob()); objectURLs.add(url);
      const link = document.createElement('a'); link.href = url; link.download = task.name;
      document.body.append(link); link.click(); link.remove();
      task.status = 'exported'; task.emit();
      setTimeout(() => { URL.revokeObjectURL(url); objectURLs.delete(url); }, 60000);
    });
    cancel = button(actions, clear);
    const resize = new ResizeObserver(schedulePlace);
    resize.observe(panel);
    for (const node of document.querySelectorAll('.composer')) resize.observe(node);
  }
  function schedulePlace() {
    if (!frame) frame = requestAnimationFrame(() => { frame = null; place(); });
  }
  function place() {
    if (!panel || panel.hidden) return;
    const viewport = window.visualViewport, narrow = matchMedia('(max-width:720px)').matches;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
    panel.style.width = Math.min(width - 24, collapsed ? 230 : 340) + 'px';
    const panelLeft = left + width - panel.offsetWidth - 12;
    let bottom = top + height - 12;
    for (const node of document.querySelectorAll(narrow ? '.composer, .client-navigation' : '.composer')) {
      const rect = node.getBoundingClientRect();
      if (rect.width && rect.height && rect.right > panelLeft && rect.bottom > top && rect.top > top) bottom = Math.min(bottom, rect.top - 10);
    }
    // In very short keyboard viewports the compact bar keeps controls out of the composer.
    const cramped = narrow && bottom - top < 215;
    details.hidden = collapsed || cramped; panel.classList.toggle('download-compact', details.hidden);
    summary.setAttribute('aria-expanded', String(!details.hidden));
    toggle.textContent = details.hidden ? '+' : '−'; toggle.setAttribute('aria-label', t(details.hidden ? '展开下载浮窗' : '收起下载浮窗'));
    const h = panel.offsetHeight, w = panel.offsetWidth;
    const x = !narrow && dragged ? dragged.x : left + width - w - 12;
    const y = !narrow && dragged ? dragged.y : bottom - h;
    panel.style.left = Math.max(left + 12, Math.min(x, left + width - w - 12)) + 'px';
    panel.style.top = Math.max(top + 8, Math.min(y, top + height - h - 8)) + 'px';
  }
  function render() {
    if (!panel && task.status === 'idle') return;
    if (!panel) create();
    panel.hidden = task.status === 'idle'; if (panel.hidden) return;
    const labels = {downloading:'正在下载', paused:'已暂停', error:'下载未完成', complete:'下载完成，可以保存', exported:'已交给浏览器保存'};
    const percent = task.total ? Math.min(100, Math.floor(task.bytes / task.total * 100)) : null;
    panel.setAttribute('aria-label', t('文件下载'));
    summary.textContent = '↓  ' + t(labels[task.status]) + (percent === null ? '' : ' · ' + percent + '%');
    summary.title = t('拖动以移动下载浮窗');
    toggle.textContent = collapsed ? '+' : '−'; toggle.setAttribute('aria-label', t(collapsed ? '展开下载浮窗' : '收起下载浮窗'));
    name.textContent = task.name; name.title = task.name;
    state.textContent = t(labels[task.status]);
    if (percent === null && task.status === 'downloading') progress.removeAttribute('value');
    else progress.value = percent ?? (['complete','exported'].includes(task.status) ? 100 : 0);
    progress.setAttribute('aria-label', t('下载进度'));
    amount.textContent = units(task.bytes) + (task.total === null ? '' : ' / ' + units(task.total));
    speed.textContent = task.status === 'downloading' ? units(task.speed || 0) + '/s' : '';
    note.textContent = t(task.error || ''); note.hidden = !task.error;
    const finished = ['complete', 'exported'].includes(task.status);
    pause.hidden = finished; pause.textContent = t(task.status === 'downloading' ? '暂停下载' : task.restartRequired ? '重新下载' : '继续下载');
    save.hidden = !finished; save.textContent = t('保存文件');
    cancel.textContent = t(finished ? '关闭' : '取消下载');
    place();
  }
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href]');
    if (!anchor || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || anchor.hasAttribute('data-image-preview')) return;
    if (!BridgeDownload.eligible(anchor.href, location.href)) return;
    event.preventDefault();
    collapsed = false;
    if (task.status !== 'idle') {
      render();
      if (task.url !== anchor.href) { note.hidden = false; note.textContent = t('请先保存或取消当前下载，再下载其他文件。'); }
      summary.focus({preventScroll:true}); return;
    }
    task.start(anchor.href, anchor.download || anchor.textContent.trim());
  });
  document.addEventListener('bridge-language', render);
  window.addEventListener('resize', schedulePlace);
  window.visualViewport?.addEventListener('resize', schedulePlace);
  window.visualViewport?.addEventListener('scroll', schedulePlace);
  new MutationObserver(records => {
    if (records.some(record => !panel?.contains(record.target))) schedulePlace();
  }).observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['hidden', 'class']});
  // Logout invalidates the source session; chat/list navigation deliberately does not.
  window.BridgeDownloads = {clear};
})();
