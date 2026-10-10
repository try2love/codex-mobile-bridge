(() => {
  if (window !== window.top || window.BridgeNativeSystemBars) return;
  window.BridgeNativeSystemBars = true;
  let pending = false, previous = '';
  function opaqueColor(value) {
    const match = /^rgba?\(\s*(\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/.exec(value);
    if (!match || match[4] !== undefined && Number(match[4]) !== 1) return null;
    return '#' + match.slice(1, 4).map(v => Number(v).toString(16).padStart(2, '0')).join('');
  }
  function colorAt(y) {
    let element = document.elementFromPoint(innerWidth / 2, y);
    while (element) {
      const color = opaqueColor(getComputedStyle(element).backgroundColor);
      if (color) return color;
      element = element.parentElement;
    }
    return opaqueColor(getComputedStyle(document.documentElement).backgroundColor) || '#ffffff';
  }
  function refresh() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const colors = JSON.stringify({ top: colorAt(1), bottom: colorAt(Math.max(1, innerHeight - 1)) });
      if (colors === previous) return;
      previous = colors;
      window.prompt('codexbridge-system-bars:__BRIDGE_CLIPBOARD_TOKEN__', colors);
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
  refresh();
})();
