'use strict';
(() => {
  const t = text => typeof BridgeI18n === 'undefined' ? text : BridgeI18n.t(text);
  let dialog, title, content, status, close, opener;
  function create() {
    dialog = document.createElement('dialog'); dialog.id = 'image-preview-dialog'; dialog.className = 'image-preview';
    dialog.setAttribute('aria-labelledby', 'image-preview-title');
    const toolbar = document.createElement('div'); toolbar.className = 'image-preview-toolbar';
    close = document.createElement('button'); close.id = 'image-preview-close'; close.type = 'button'; close.autofocus = true;
    close.onclick = () => dialog.close();
    title = document.createElement('span'); title.id = 'image-preview-title';
    toolbar.append(close, title);
    content = document.createElement('div'); content.className = 'image-preview-content';
    status = document.createElement('p'); status.id = 'image-preview-status'; status.setAttribute('role', 'status');
    dialog.append(toolbar, content, status); document.body.append(dialog);
    dialog.addEventListener('close', () => {
      // A queued close event can arrive after another image has opened.
      if (dialog.open) return;
      content.replaceChildren(); status.textContent = '';
      if (opener?.isConnected) opener.focus({preventScroll:true});
      opener = null;
    });
  }
  function open(target, event) {
    const url = new URL(target.dataset.imagePreview, location.href);
    // Only renderer-identified image attachments from the current gateway.
    if (url.origin !== location.origin || !/^\/api\/sessions\/[0-9a-f-]{36}\/(?:uploads\/[0-9a-f-]{36}\/preview|desktop-images\/[a-f0-9]{64}|files\/[a-f0-9]{64})$/.test(url.pathname)) return;
    event.preventDefault();
    if (!dialog) create();
    opener = target;
    close.textContent = '× ' + t('关闭'); close.setAttribute('aria-label', t('关闭图片预览'));
    title.textContent = target.dataset.imageName || target.querySelector('img')?.alt || target.alt || t('图片附件');
    status.textContent = t('正在加载图片…');
    const image = document.createElement('img'); image.alt = title.textContent;
    image.onload = () => { if (image.isConnected) status.textContent = ''; };
    image.onerror = () => { if (image.isConnected) { image.hidden = true; status.textContent = t('图片加载失败，请关闭后重试。'); } };
    content.replaceChildren(image); image.src = url.href;
    if (!dialog.open) dialog.showModal();
  }
  document.addEventListener('click', event => {
    const target = event.target.closest('[data-image-preview]');
    if (target && !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) open(target, event);
  });
  document.addEventListener('keydown', event => {
    if (event.target.matches('img[data-image-preview]') && ['Enter', ' '].includes(event.key)) open(event.target, event);
  });
})();
