(() => {
  if (window !== window.top || window.BridgeNativeSystemBars) return;
  window.BridgeNativeSystemBars = true;
  let pending = false, previous = '';
  const pageEdges = new Map();
  function rgba(value) {
    const match = /^rgba?\(\s*(\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/.exec(value);
    return match ? [...match.slice(1, 4).map(Number), Number(match[4] ?? 1)] : null;
  }
  function hex(channels) {
    return '#' + channels.map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  }
  function opaqueColor(value) {
    const color = rgba(value);
    return color?.[3] === 1 ? hex(color.slice(0, 3)) : null;
  }
  function composite(base, overlay) {
    const color = rgba(overlay.color);
    if (!color) return base;
    const alpha = color[3] * overlay.opacity;
    return hex(color.slice(0, 3).map((value, index) => value * alpha +
      parseInt(base.slice(1 + index * 2, 3 + index * 2), 16) * (1 - alpha)));
  }
  function colorAt(y, edge) {
    const x = innerWidth / 2;
    let element = document.elementFromPoint(x, y);
    const dialogs = Array.from(document.querySelectorAll('dialog[open]'));
    const overlays = [];
    if (dialogs.length) {
      const covering = dialogs.reverse().find(dialog => {
        const rect = dialog.getBoundingClientRect();
        if (x >= rect.left && x < rect.right && y >= rect.top && y < rect.bottom) return true;
        const style = getComputedStyle(dialog, '::backdrop');
        overlays.unshift({color: style.backgroundColor, opacity: Number(style.opacity || 1)});
        return false;
      });
      // Backdrop hit tests vary across WebViews; use visible dialog bounds.
      if (!covering) {
        const underlying = pageEdges.get(edge);
        element = underlying?.isConnected ? underlying : document.body;
      } else if (!element || !covering.contains(element)) element = covering;
    } else if (element) {
      pageEdges.set(edge, element);
    }
    let background;
    while (element) {
      const color = opaqueColor(getComputedStyle(element).backgroundColor);
      if (color) { background = color; break; }
      element = element.parentElement;
    }
    background ||= opaqueColor(getComputedStyle(document.documentElement).backgroundColor) || '#ffffff';
    return overlays.reduce(composite, background);
  }
  function refresh() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const colors = JSON.stringify({ top: colorAt(1, 'top'), bottom: colorAt(Math.max(1, innerHeight - 1), 'bottom') });
      if (colors === previous) return;
      if (window.prompt('codexbridge-system-bars:__BRIDGE_CLIPBOARD_TOKEN__', colors) === 'saved') previous = colors;
    });
  }
  const watchDialog = dialog => observer.observe(dialog, { attributes: true, attributeFilter: ['open'] });
  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList') {
        for (const node of record.addedNodes) if (node.nodeName === 'DIALOG') watchDialog(node);
      }
    }
    refresh();
  });
  // Watch only page containers and dialog state, keeping streamed message mutations out of this observer.
  for (const element of [document.documentElement, document.body, document.getElementById('app')]) {
    if (element) observer.observe(element, { attributes: true, childList: element === document.body, attributeFilter: ['class', 'style', 'data-theme', 'hidden'] });
  }
  document.querySelectorAll('dialog').forEach(watchDialog);
  window.addEventListener('resize', refresh);
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', refresh);
  window.visualViewport?.addEventListener('resize', refresh);
  let animationUntil = 0, animationFrame = false, lastAnimationSample = -Infinity;
  const followAnimation = () => {
    const now = performance.now();
    if (now - lastAnimationSample >= 32) { lastAnimationSample = now; refresh(); }
    if (now < animationUntil && document.querySelector('dialog.session-menu[open]')) requestAnimationFrame(followAnimation);
    else { animationFrame = false; refresh(); }
  };
  document.addEventListener('animationstart', event => {
    if (!event.target.matches?.('dialog.session-menu')) return;
    const style = getComputedStyle(event.target, event.pseudoElement || null);
    const duration = Math.max(...style.animationDuration.split(',').map(value => parseFloat(value) * (value.trim().endsWith('ms') ? 1 : 1000)));
    animationUntil = performance.now() + duration + 32;
    if (!animationFrame) { animationFrame = true; requestAnimationFrame(followAnimation); }
  });
  refresh();
})();
