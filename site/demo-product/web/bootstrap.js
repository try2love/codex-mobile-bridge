'use strict';
// Keep this small and independent: even i18n or the main app may fail to load.
(function () {
  var root = document.getElementById('bootstrap-status');
  var title = document.getElementById('bootstrap-title');
  var message = document.getElementById('bootstrap-message');
  var retry = document.getElementById('bootstrap-retry');
  var originalUrl = location.href;
  var originalPairing = location.hash.indexOf('#pair=') === 0;
  var complete = false, timer = null, phase = 'loading';
  var english = false;
  try { english = localStorage.getItem('bridge-language') === 'en'; } catch (_) {}
  if (/(?:^|[?&])lang=en(?:&|$)/.test(location.search)) english = true;
  function text(zh, en) { return english ? en : zh; }
  function fail(kind) {
    if (complete) return false;
    clearTimeout(timer);
    root.hidden = false;
    root.setAttribute('aria-busy', 'false');
    title.textContent = text('工作台尚未加载完成', 'The workspace has not finished loading');
    message.textContent = phase === 'pairing'
      ? text('配对请求结果尚未确认。可重新加载查看连接状态；若仍未登录，请重新扫描新的配对码。', 'The pairing result is not confirmed. Reload to check the connection; if you are still signed out, scan a new pairing code.')
      : kind === 'script'
      ? text('部分页面组件未能启动。请重新加载；若仍失败，请检查网络或更新浏览器。', 'Some page components could not start. Reload the page; if it still fails, check your connection or update your browser.')
      : phase === 'auth'
        ? text('等待网关响应时间较长。请检查网络和电脑网关，再重新加载。', 'The gateway is taking longer to respond. Check your connection and computer gateway, then reload.')
        : text('页面资源加载时间较长。请检查网络后重新加载。', 'Page resources are taking longer to load. Check your connection, then reload.');
    return true;
  }
  function waitingForAuth() {
    if (complete) return;
    phase = 'auth';
    clearTimeout(timer);
    timer = setTimeout(function () { fail('timeout'); }, 12000);
  }
  function onError(event) {
    if ((event.target && event.target.tagName === 'SCRIPT') || event.target === window) fail('script');
  }
  function onRejection() { fail('script'); }
  function pairingSubmitted() {
    phase = 'pairing';
    // Once the write may have reached the server, a reload must not replay it.
    originalUrl = null;
    originalPairing = false;
  }
  function ready() {
    if (complete) return;
    complete = true;
    originalUrl = null;
    clearTimeout(timer);
    root.hidden = true;
    window.removeEventListener('error', onError, true);
    window.removeEventListener('unhandledrejection', onRejection);
  }
  title.textContent = text('正在连接工作台…', 'Connecting to your workspace…');
  message.textContent = text('首次加载可能需要片刻。若长时间没有响应，可重新加载页面。', 'The first load may take a moment. Reload the page if it stops responding.');
  retry.textContent = text('重新加载', 'Reload');
  retry.disabled = false;
  retry.onclick = function () {
    // app.js removes a pairing fragment before network access. Restore it only
    // for an explicit retry during startup, without changing a newer chat URL.
    if (!complete && originalPairing && !location.hash) history.replaceState(null, '', originalUrl);
    location.reload();
  };
  window.addEventListener('error', onError, true);
  window.addEventListener('unhandledrejection', onRejection);
  timer = setTimeout(function () { fail('timeout'); }, 12000);
  window.BridgeBootstrap = {waitingForAuth: waitingForAuth, pairingSubmitted: pairingSubmitted,
    failed: function () { return fail('connection'); }, ready: ready};
})();
