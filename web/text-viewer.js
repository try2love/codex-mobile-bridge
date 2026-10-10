'use strict';
(() => {
  const LIMIT = 1024 * 1024;
  const extensions = new Set('txt log json jsonl yaml yml toml xml csv tsv ini cfg conf properties py js jsx mjs cjs ts tsx java kt kts swift c h cpp hpp cs go rs rb php sh bash zsh ps1 sql css scss less vue svelte r'.split(' '));
  const links = new WeakMap();
  const t = text => BridgeI18n.t(text);
  let dialog, title, content, status, retry, download, close, downloadLabel, closeLabel, opener, current, controller;

  function actionLabel(element, path) {
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('aria-hidden', 'true'); icon.setAttribute('focusable', 'false');
    const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path'); shape.setAttribute('d', path); icon.append(shape);
    const label = document.createElement('span'); element.append(icon, label); return label;
  }

  function format(name) {
    const extension = /\.([^.]+)$/.exec(String(name))?.[1].toLowerCase();
    if (extension === 'md' || extension === 'markdown') return 'markdown';
    return extensions.has(extension) ? 'text' : null;
  }

  async function read(url, signal) {
    const response = await fetch(url, {
      signal, credentials:'same-origin', redirect:'error', cache:'no-store',
      headers:{Range:`bytes=0-${LIMIT}`}
    });
    const cancel = () => response.body?.cancel();
    if (response.status === 401 || response.status === 403) {
      await cancel(); throw Error('登录已失效，请重新登录后查看。');
    }
    if (response.status === 416 && response.headers.get('Content-Range') === 'bytes */0') {
      await cancel(); return '';
    }
    if (response.status !== 200 && response.status !== 206) {
      await cancel(); throw Error('文件读取失败，请重试或下载。');
    }
    const range = /^bytes 0-(\d+)\/(\d+)$/.exec(response.headers.get('Content-Range') || '');
    if (response.status === 206 && !range) {
      await cancel(); throw Error('文件读取失败，请重试或下载。');
    }
    const length = Number(response.headers.get('Content-Length'));
    if (length > LIMIT || (range && Number(range[2]) > LIMIT)) {
      await cancel(); throw Error('文件超过 1 MiB 预览限制，请下载查看。');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', {fatal:true});
    let bytes = 0, text = '';
    try {
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > LIMIT) throw Error('文件超过 1 MiB 预览限制，请下载查看。');
        try { text += decoder.decode(value, {stream:true}); }
        catch { throw Error('文件编码无法预览，请下载查看。'); }
      }
      try { text += decoder.decode(); }
      catch { throw Error('文件编码无法预览，请下载查看。'); }
      if (text.includes('\0')) throw Error('文件内容无法作为文本预览，请下载查看。');
      if ((range && bytes !== Number(range[2])) || (response.headers.has('Content-Length') && bytes !== length)) {
        throw Error('文件读取失败，请重试或下载。');
      }
      return text;
    } finally {
      await reader.cancel(); reader.releaseLock();
    }
  }

  function labels() {
    if (!dialog) return;
    closeLabel.textContent = t('关闭'); retry.textContent = t('重试'); downloadLabel.textContent = t('下载');
    close.setAttribute('aria-label', t('关闭文本预览'));
    if (status.textContent) status.textContent = t(status.textContent);
  }

  function create() {
    dialog = document.createElement('dialog'); dialog.id = 'text-preview-dialog'; dialog.className = 'text-preview';
    dialog.setAttribute('aria-labelledby', 'text-preview-title');
    const toolbar = document.createElement('div'); toolbar.className = 'text-preview-toolbar';
    close = document.createElement('button'); close.type = 'button'; close.autofocus = true; close.onclick = () => dialog.close();
    closeLabel = actionLabel(close, 'm6 6 12 12M18 6 6 18');
    title = document.createElement('span'); title.id = 'text-preview-title';
    download = document.createElement('a'); download.className = 'text-preview-download'; download.target = '_blank'; download.rel = 'noopener noreferrer';
    downloadLabel = actionLabel(download, 'M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4');
    // Close first so the existing native/Web download controls remain reachable.
    download.addEventListener('click', () => dialog.close());
    toolbar.append(title, download, close);
    content = document.createElement('div'); content.className = 'text-preview-content'; content.tabIndex = 0;
    status = document.createElement('p'); status.className = 'text-preview-status'; status.setAttribute('role', 'status');
    retry = document.createElement('button'); retry.type = 'button'; retry.className = 'text-preview-retry'; retry.onclick = load;
    dialog.append(toolbar, status, retry, content); document.body.append(dialog);
    dialog.addEventListener('close', () => {
      if (dialog.open) return;
      controller?.abort(); controller = null; current = null;
      content.replaceChildren(); status.textContent = ''; retry.hidden = true;
      if (opener?.isConnected) opener.focus({preventScroll:true}); opener = null;
    });
  }

  async function load() {
    controller?.abort();
    const request = controller = new AbortController(), file = current;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; request.abort(); }, 20000);
    content.replaceChildren(); content.scrollTop = 0; retry.hidden = true;
    status.hidden = false; status.textContent = t('正在加载文件…'); content.setAttribute('aria-busy', 'true');
    try {
      const text = await read(file.url, request.signal);
      if (controller !== request || !dialog.open) return;
      if (file.format === 'markdown') renderMarkdown(content, text);
      else {
        const pre = document.createElement('pre'); pre.className = 'text-preview-source'; pre.textContent = text; content.append(pre);
      }
      status.hidden = Boolean(text); status.textContent = text ? '' : t('文件为空。');
    } catch (error) {
      if ((request.signal.aborted && !timedOut) || controller !== request || !dialog.open) return;
      const messages = ['登录已失效，请重新登录后查看。', '文件读取失败，请重试或下载。',
        '文件超过 1 MiB 预览限制，请下载查看。', '文件编码无法预览，请下载查看。', '文件内容无法作为文本预览，请下载查看。'];
      status.textContent = t(messages.includes(error.message) ? error.message : '文件读取失败，请重试或下载。');
      retry.hidden = false;
    } finally {
      clearTimeout(timeout);
      if (controller === request) content.removeAttribute('aria-busy');
    }
  }

  window.BridgeTextViewer = {
    registerLink(anchor, file) {
      const kind = format(file.name);
      if (!kind || file.image || !BridgeDownload.eligible(anchor.href, location.href)) return;
      links.set(anchor, {url:anchor.href, name:file.name, format:kind});
      anchor.dataset.textPreview = '';
    }
  };
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[data-text-preview]'), file = links.get(anchor);
    if (!file || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (!dialog) create(); labels(); opener = anchor; current = file;
    title.textContent = file.name; title.title = file.name; download.href = file.url; download.download = file.name;
    if (!dialog.open) dialog.showModal();
    load();
  });
  document.addEventListener('bridge-language', labels);
})();
