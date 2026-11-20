(() => {
  if (window !== window.top || window.BridgeNativeSession) return;
  window.BridgeNativeSession = true;
  const persist = () => window.prompt('codexbridge-session:__BRIDGE_CLIPBOARD_TOKEN__', '');
  document.addEventListener('bridge-authenticated', persist);
  document.addEventListener('bridge-logged-out', () => window.prompt('codexbridge-logout:__BRIDGE_CLIPBOARD_TOKEN__', ''));
  document.addEventListener('bridge-auth-lost', () => window.prompt('codexbridge-auth-lost:__BRIDGE_CLIPBOARD_TOKEN__', ''));
  document.addEventListener('bridge-chat-visible', event => {
    const {threadId, host} = event.detail || {};
    if (document.visibilityState !== 'visible' || typeof threadId !== 'string' || !threadId || threadId.length > 512 || typeof host !== 'string' || host.length > 256) return;
    window.prompt('codexbridge-chat-visible:__BRIDGE_CLIPBOARD_TOKEN__', JSON.stringify({threadId, host}));
  });
  const app = document.getElementById('app');
  if (app && !app.hidden) persist();
})();
