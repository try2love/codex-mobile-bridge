(() => {
  if (window !== window.top) return;
  const persist = () => window.prompt('codexbridge-session:__BRIDGE_CLIPBOARD_TOKEN__', '');
  document.addEventListener('bridge-authenticated', persist);
  const app = document.getElementById('app');
  if (app && !app.hidden) persist();
})();
