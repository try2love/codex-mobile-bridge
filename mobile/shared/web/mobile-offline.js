(() => {
  if (window !== window.top || window.CodexBridgeNativeOffline) return;
  const prompt = window.prompt.bind(window);
  const token = '__BRIDGE_CLIPBOARD_TOKEN__';
  const pending = new Map();
  const android = /(?:^|\s)BridgeMobile\/[\w.-]+-Android(?:\s|$)/.test(navigator.userAgent);
  let port;
  let sequence = 0;
  let appearanceScope;

  function publish(state) {
    window.BridgeChatForeground = state.foreground;
    window.dispatchEvent(new CustomEvent('bridge-chat-foreground', {detail: {foreground: state.foreground}}));
    window.dispatchEvent(new CustomEvent('codexbridge-native-offline-state', {detail: state}));
  }

  function appearance() {
    try { return JSON.parse(localStorage.getItem('bridge-appearance')); } catch { return undefined; }
  }

  Object.defineProperty(window, 'CodexBridgeNativeOffline', {value: Object.freeze({
    request(event) {
      return new Promise((resolve, reject) => {
        const id = String(++sequence);
        let initialAppearance;
        if (event.type === 'auth') {
          appearanceScope = event.status === 'valid' ? {gatewayId: event.gatewayId, identity: event.identity} : undefined;
          if (appearanceScope) initialAppearance = appearance();
        }
        const value = JSON.stringify({appearance: initialAppearance, ...event, requestId: id});
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error('Native archive request timed out'));
        }, event.type === 'transferChunkFetch' || event.type?.endsWith('Commit') ? 45000 : 15000);
        pending.set(id, {resolve, reject, timer});
        try {
          if (android) {
            if (port) { port.postMessage(value); return; }
          } else if (prompt('codexbridge-offline:' + token, value) === 'queued') return;
        } catch (error) {
          clearTimeout(timer);
          pending.delete(id);
          reject(error);
          return;
        }
        clearTimeout(timer);
        pending.delete(id);
        reject(new Error('Native archive unavailable'));
      });
    },
    receive(receivedToken, reply) {
      if (receivedToken !== token) return;
      if (reply.state) return publish(reply.state);
      const request = pending.get(reply.id);
      if (!request) return;
      pending.delete(reply.id);
      clearTimeout(request.timer);
      if (reply.ok) request.resolve(reply.value);
      else request.reject(new Error('Native archive unavailable'));
    }
  })});
  for (const name of ['bridge-appearance-change', 'bridge-appearance-restored']) {
    document.addEventListener(name, event => {
      if (appearanceScope) window.CodexBridgeNativeOffline.request({type: 'appearance', ...appearanceScope, appearance: event.detail}).catch(() => {});
    });
  }
  if (android) {
    const connect = event => {
      if (event.data !== token || event.ports.length !== 1) return;
      window.removeEventListener('message', connect);
      port = event.ports[0];
      port.onmessage = message => {
        try { window.CodexBridgeNativeOffline.receive(token, JSON.parse(message.data)); } catch {}
      };
      port.postMessage('ready');
    };
    window.addEventListener('message', connect);
  }
  publish({foreground: false, allowPrefetch: false, allowTransfer: false});
})();
