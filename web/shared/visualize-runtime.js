'use strict';
(() => {
  const config = window.__bridgeVisualization;
  delete window.__bridgeVisualization;
  const host = parent, send = value => host.postMessage({instance:config.instance, ...value}, config.sourceOrigin);
  let state = config.state, sequence = 0;
  const pending = new Map();
  const applyTheme = theme => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.dispatchEvent(new CustomEvent('openai:set_globals', {detail:{globals:{theme}}}));
  };
  const api = {
    get widgetState() { return state; },
    get theme() { return document.documentElement.dataset.theme; },
    setWidgetState(next) {
      try {
        const value = typeof next === 'function' ? next(state) : next;
        const serialized = JSON.stringify(value, (_, item) => {
          if (typeof item === 'function' || typeof item === 'symbol' || typeof item === 'undefined' || typeof item === 'number' && !Number.isFinite(item)) throw Error('Invalid widget state');
          return item;
        });
        if (!value || typeof value !== 'object' || Array.isArray(value) || new TextEncoder().encode(serialized).length > 16384) throw Error('Widget state exceeds 16 KiB');
        const saved = JSON.parse(serialized), requestId = ++sequence;
        return new Promise((resolve, reject) => {
          const timer = setTimeout(() => { pending.delete(requestId); reject(Error('State persistence timed out')); }, 5000);
          pending.set(requestId, {resolve, reject, timer, saved});
          send({type:'bridge-visualize-state', requestId, state:saved});
        });
      } catch (error) { return Promise.reject(error); }
    },
    sendFollowUpMessage({prompt, title} = {}) {
      if (!navigator.userActivation?.isActive || typeof prompt !== 'string' || !prompt.trim() || prompt.length > 10000) return Promise.reject(Error('A user action and a prompt are required'));
      send({type:'bridge-visualize-followup', prompt, title:typeof title === 'string' ? title.slice(0, 200) : ''});
      return Promise.resolve();
    },
    notifyIntrinsicHeight() { measure(); },
  };
  Object.defineProperty(window, 'openai', {value:Object.freeze(api), configurable:false, writable:false});
  window.addEventListener('message', event => {
    const value = event.data;
    if (event.source !== host || event.origin !== config.sourceOrigin || value?.instance !== config.instance) return;
    if (value.type === 'bridge-visualize-theme') applyTheme(value.theme === 'dark' ? 'dark' : 'light');
    if (value.type === 'bridge-visualize-state-result') {
      const request = pending.get(value.requestId);
      if (!request) return;
      pending.delete(value.requestId); clearTimeout(request.timer);
      if (value.ok) { state = request.saved; window.dispatchEvent(new CustomEvent('openai:set_globals', {detail:{globals:{widgetState:state}}})); request.resolve(); }
      else request.reject(Error('Unable to persist widget state'));
    }
  });
  applyTheme(config.theme);
  let lastHeight = 0, scheduled = false;
  function measure() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      const root = document.getElementById('bridge-visualize-root');
      if (!root) return;
      const height = Math.ceil(Math.max(root.getBoundingClientRect().height, root.scrollHeight));
      if (height !== lastHeight) { lastHeight = height; send({type:'bridge-visualize-resize', height}); }
    });
  }
  function ready() {
    const root = document.getElementById('bridge-visualize-root');
    new ResizeObserver(measure).observe(root);
    new MutationObserver(measure).observe(root, {childList:true, subtree:true, attributes:true, characterData:true});
    measure(); send({type:'bridge-visualize-mounted'});
    const icons = document.createElement('script');
    icons.src = 'https://unpkg.com/lucide@1.17.0/dist/umd/lucide.js';
    icons.onload = () => window.lucide?.createIcons({attrs:{width:16, height:16}});
    icons.onerror = () => { icons.remove(); };
    if (root.querySelector('[data-lucide]')) document.head.append(icons);
  }
  document.addEventListener('click', event => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab || tab.disabled || tab.getAttribute('aria-disabled') === 'true') return;
    const list = tab.closest('[role="tablist"]');
    if (!list) return;
    for (const item of list.querySelectorAll('[role="tab"]')) {
      const active = item === tab;
      item.classList.toggle('active', active); item.setAttribute('aria-selected', String(active));
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      if (panel) panel.hidden = !active && item.getAttribute('aria-controls') !== tab.getAttribute('aria-controls');
    }
  });
  let tooltip, trigger;
  function hideTooltip() {
    if (trigger && tooltip) {
      const ids = (trigger.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== tooltip.id);
      if (ids.length) trigger.setAttribute('aria-describedby', ids.join(' ')); else trigger.removeAttribute('aria-describedby');
    }
    tooltip?.remove(); tooltip = trigger = null;
  }
  function showTooltip(target) {
    const next = target.closest?.('[data-tooltip]');
    if (next === trigger) return;
    hideTooltip();
    if (!next || !next.dataset.tooltip) return;
    trigger = next; tooltip = document.createElement('div');
    tooltip.id = 'bridge-viz-tooltip'; tooltip.className = 'tooltip'; tooltip.setAttribute('role', 'tooltip');
    tooltip.textContent = next.dataset.tooltip; document.body.append(tooltip);
    trigger.setAttribute('aria-describedby', [trigger.getAttribute('aria-describedby'), tooltip.id].filter(Boolean).join(' '));
    const bounds = trigger.getBoundingClientRect(), tip = tooltip.getBoundingClientRect();
    tooltip.style.left = Math.max(4, Math.min(innerWidth - tip.width - 4, bounds.left + (bounds.width - tip.width) / 2)) + 'px';
    tooltip.style.top = Math.max(4, bounds.top - tip.height - 5) + 'px';
  }
  document.addEventListener('pointerover', event => showTooltip(event.target));
  document.addEventListener('pointerout', hideTooltip);
  document.addEventListener('focusin', event => showTooltip(event.target));
  document.addEventListener('focusout', hideTooltip);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { hideTooltip(); send({type:'bridge-visualize-escape'}); }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, {once:true}); else ready();
})();
