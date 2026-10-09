'use strict';
(() => {
  const t = text => typeof BridgeI18n === 'undefined' ? text : BridgeI18n.t(text);
  const registered = new WeakMap(), pointers = new Map();
  let dialog, title, content, status, close, zoomIn, zoomOut, fit, percentage, opener, image, observer;
  let zoom = 1, x = 0, y = 0, width = 0, height = 0, frame = 0, gesture, lastTap;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const ready = () => dialog?.open && image?.naturalWidth > 0 && !image.hidden && width > 0;
  function paint() {
    frame = 0;
    if (!ready()) return;
    const limitX = Math.max(0, (width * zoom - content.clientWidth + 24) / 2);
    const limitY = Math.max(0, (height * zoom - content.clientHeight + 24) / 2);
    x = clamp(x, -limitX, limitX); y = clamp(y, -limitY, limitY);
    image.style.width = width + 'px'; image.style.height = height + 'px';
    image.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${zoom})`;
    percentage.textContent = Math.round(zoom * 100) + '%';
    zoomOut.disabled = zoom <= 1; zoomIn.disabled = zoom >= 8; fit.disabled = zoom === 1;
    content.classList.toggle('is-zoomed', zoom > 1);
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(paint); }
  function clearPointers() {
    const captured = [...pointers.keys()]; pointers.clear(); gesture = null; lastTap = null;
    for (const id of captured) if (content.hasPointerCapture(id)) content.releasePointerCapture(id);
    content.classList.remove('is-dragging');
  }
  function cleanup() {
    if (frame) cancelAnimationFrame(frame); frame = 0;
    observer?.disconnect(); clearPointers();
    if (image) { image.onload = null; image.onerror = null; }
    image = null; zoom = 1; x = y = width = height = 0;
  }
  function measure() {
    if (!dialog.open || !image?.naturalWidth || image.hidden) return;
    const ratio = Math.min(1, Math.max(1, content.clientWidth - 24) / image.naturalWidth, Math.max(1, content.clientHeight - 24) / image.naturalHeight);
    const nextWidth = image.naturalWidth * ratio, nextHeight = image.naturalHeight * ratio;
    if (width) { x *= nextWidth / width; y *= nextHeight / height; }
    width = nextWidth; height = nextHeight; clearPointers(); schedule();
  }
  function point(event) {
    const bounds = content.getBoundingClientRect();
    return {x:event.clientX - bounds.left - bounds.width / 2, y:event.clientY - bounds.top - bounds.height / 2};
  }
  function changeZoom(value, from = {x:0,y:0}, to = from) {
    if (!ready()) return;
    const next = clamp(value, 1, 8), ratio = next / zoom;
    x = to.x - (from.x - x) * ratio; y = to.y - (from.y - y) * ratio; zoom = next;
    // Clamp immediately so the next pointer event starts from the visible position.
    if (frame) cancelAnimationFrame(frame); paint();
  }
  function reset() { clearPointers(); zoom = 1; x = y = 0; schedule(); }
  function baseline() {
    const values = [...pointers.values()];
    if (values.length === 2) {
      gesture = {mid:{x:(values[0].x + values[1].x) / 2, y:(values[0].y + values[1].y) / 2}, distance:Math.hypot(values[1].x - values[0].x, values[1].y - values[0].y)};
    } else gesture = values[0] ? {point:{x:values[0].x,y:values[0].y}} : null;
    content.classList.toggle('is-dragging', !!values.length && zoom > 1);
  }
  function pointerDown(event) {
    if (!ready() || event.button !== 0 || pointers.size >= 2) return;
    event.preventDefault();
    const p = point(event); pointers.set(event.pointerId, {...p,start:p,time:performance.now(),moved:false,type:event.pointerType});
    content.setPointerCapture(event.pointerId);
    if (pointers.size > 1) { lastTap = null; for (const p of pointers.values()) p.moved = true; }
    baseline();
  }
  function pointerMove(event) {
    const prior = pointers.get(event.pointerId); if (!prior || !ready()) return;
    event.preventDefault(); const p = point(event);
    pointers.set(event.pointerId, {...prior,...p,moved:prior.moved || Math.hypot(p.x - prior.start.x, p.y - prior.start.y) > 8});
    const before = gesture; baseline();
    if (pointers.size === 2 && before?.distance > 0) changeZoom(zoom * gesture.distance / before.distance, before.mid, gesture.mid);
    else if (pointers.size === 1 && before?.point && zoom > 1) { x += p.x - before.point.x; y += p.y - before.point.y; schedule(); }
  }
  function pointerEnd(event) {
    const prior = pointers.get(event.pointerId); if (!prior) return;
    const tap = event.type === 'pointerup' && prior.type === 'touch' && pointers.size === 1 && !prior.moved && performance.now() - prior.time < 350;
    pointers.delete(event.pointerId);
    if (content.hasPointerCapture(event.pointerId)) content.releasePointerCapture(event.pointerId);
    baseline();
    if (tap) {
      if (lastTap && performance.now() - lastTap.time < 350 && Math.hypot(prior.x - lastTap.x, prior.y - lastTap.y) < 24) { changeZoom(zoom === 1 ? 2 : 1, prior); lastTap = null; }
      else lastTap = {...prior,time:performance.now()};
    } else lastTap = null;
  }
  function create() {
    dialog = document.createElement('dialog'); dialog.id = 'image-preview-dialog'; dialog.className = 'image-preview';
    dialog.setAttribute('aria-labelledby', 'image-preview-title');
    const toolbar = document.createElement('div'); toolbar.className = 'image-preview-toolbar';
    const button = (id, text, action) => { const node = document.createElement('button'); node.id = 'image-preview-' + id; node.type = 'button'; node.textContent = text; node.onclick = action; return node; };
    close = button('close', '', () => dialog.close()); close.autofocus = true;
    title = document.createElement('span'); title.id = 'image-preview-title'; toolbar.append(close, title);
    content = document.createElement('div'); content.className = 'image-preview-content';
    const controls = document.createElement('div'); controls.className = 'image-preview-controls';
    zoomOut = button('zoom-out', '−', () => changeZoom(zoom / 1.25)); zoomIn = button('zoom-in', '+', () => changeZoom(zoom * 1.25)); fit = button('fit', '', reset);
    percentage = document.createElement('output'); percentage.id = 'image-preview-scale';
    controls.append(zoomOut, percentage, zoomIn, fit);
    status = document.createElement('p'); status.id = 'image-preview-status'; status.setAttribute('role', 'status');
    dialog.append(toolbar, content, controls, status); document.body.append(dialog);
    observer = new ResizeObserver(measure);
    content.addEventListener('pointerdown', pointerDown); content.addEventListener('pointermove', pointerMove);
    for (const type of ['pointerup','pointercancel','lostpointercapture']) content.addEventListener(type, pointerEnd);
    content.addEventListener('wheel', event => {
      if (!ready()) return;
      event.preventDefault(); clearPointers();
      const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? content.clientHeight : 1);
      changeZoom(zoom * Math.exp(-clamp(pixels, -1000, 1000) * .002), point(event));
    }, {passive:false});
    content.addEventListener('dblclick', event => {
      // Touch double-taps are handled above; ignore a compatibility mouse event.
      if (event.sourceCapabilities?.firesTouchEvents || !ready()) return;
      event.preventDefault(); clearPointers(); changeZoom(zoom === 1 ? 2 : 1, point(event));
    });
    dialog.addEventListener('close', () => {
      // A queued close event can arrive after another image has opened.
      if (dialog.open) return;
      cleanup(); content.replaceChildren(); status.textContent = '';
      if (opener?.isConnected) opener.focus({preventScroll:true}); opener = null;
    });
  }
  function source(target) {
    const saved = registered.get(target); if (saved) return saved;
    if (!target.dataset.imagePreview) return null;
    let url; try { url = new URL(target.dataset.imagePreview, location.href); } catch { return null; }
    // Only renderer-identified images from the current gateway; no arbitrary URLs.
    const codex = /^\/api\/sessions\/[0-9a-f-]{36}\/(?:uploads\/[0-9a-f-]{36}\/preview|desktop-images\/[a-f0-9]{64}|files\/[a-f0-9]{64})$/.test(url.pathname);
    const desktop = /^\/api\/desktop-sessions\/(?:claude|deepseek)\/uploads\/[0-9a-f-]{36}\/preview$/.test(url.pathname) && !!url.searchParams.get('sessionId');
    return url.origin === location.origin && (codex || desktop) ? {url:url.href,name:target.dataset.imageName} : null;
  }
  function open(target, event) {
    const value = source(target); if (!value) return;
    event.preventDefault(); if (!dialog) create(); cleanup(); opener = target;
    close.textContent = '× ' + t('关闭'); close.setAttribute('aria-label', t('关闭图片预览'));
    zoomIn.setAttribute('aria-label', t('放大图片')); zoomOut.setAttribute('aria-label', t('缩小图片')); fit.textContent = t('适应屏幕');
    percentage.setAttribute('aria-label', t('图片缩放比例')); percentage.textContent = '100%'; zoomIn.disabled = zoomOut.disabled = fit.disabled = true;
    title.textContent = value.name || target.querySelector('img')?.alt || target.alt || t('图片附件');
    status.textContent = t('正在加载图片…'); content.classList.remove('is-zoomed');
    const current = image = document.createElement('img'); current.alt = title.textContent; current.draggable = false;
    current.onload = () => { if (image === current && current.isConnected && dialog.open) { status.textContent = ''; measure(); } };
    current.onerror = () => { if (image === current && current.isConnected) { current.hidden = true; status.textContent = t('图片加载失败，请关闭后重试。'); } };
    content.replaceChildren(current); if (!dialog.open) dialog.showModal(); observer.observe(content); current.src = value.url;
  }
  window.BridgeImageViewer = {
    registerWorkspaceImage(target, result) {
      if (target.localName !== 'img' || result.kind !== 'image' || !/^image\/(?:png|jpeg|gif|webp)$/.test(result.mime) || !/^[A-Za-z0-9+/]+={0,2}$/.test(result.data)) return false;
      const url = 'data:' + result.mime + ';base64,' + result.data; if (target.src !== url) return false;
      // This explicit renderer call is the trust boundary. A copied DOM attribute
      // cannot opt arbitrary data/blob/remote URLs into the delegated viewer.
      registered.set(target, {url,name:result.name}); target.classList.add('image-preview-source'); target.tabIndex = 0; target.setAttribute('role', 'button');
      target.setAttribute('aria-label', t('查看图片') + ' ' + (result.name || target.alt)); return true;
    }
  };
  document.addEventListener('click', event => {
    const target = event.target.closest('[data-image-preview],.image-preview-source');
    if (target && !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) open(target, event);
  });
  document.addEventListener('keydown', event => {
    if (event.target.matches('img[data-image-preview],img.image-preview-source') && ['Enter', ' '].includes(event.key)) open(event.target, event);
  });
})();
