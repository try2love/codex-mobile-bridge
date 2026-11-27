'use strict';
// WebKit can carry a text field's focus ring to a button focused by a tap.
// Mark that focus only for the lifetime of the focused element.
(function () {
  let touchPointer = false;
  let touchClick = null;
  let touchFocus = null;
  function clearFocus() {
    touchFocus?.removeAttribute('data-touch-focus');
    touchFocus = null;
  }
  function markFocus(element) {
    if (element === document.body || element === document.documentElement || element.closest('.xterm')) return;
    touchFocus = element;
    touchFocus.setAttribute('data-touch-focus', '');
  }
  document.addEventListener('pointerdown', (event) => {
    if (!event.isTrusted) return;
    touchPointer = event.pointerType === 'touch';
    touchClick = null;
    if (!touchPointer) clearFocus();
  }, true);
  document.addEventListener('pointercancel', () => { touchPointer = false; }, true);
  document.addEventListener('contextmenu', () => { touchPointer = false; }, true);
  document.addEventListener('keydown', (event) => {
    if (!event.isTrusted) return;
    touchPointer = false;
    touchClick = null;
    clearFocus();
  }, true);
  document.addEventListener('click', (event) => {
    if (!event.isTrusted) return;
    // WebKit can report a compatibility click as a mouse click after a touch.
    const fromTouch = touchPointer && event.detail > 0;
    touchPointer = false;
    touchClick = fromTouch ? event : null;
    if (!fromTouch) {
      clearFocus();
      return;
    }
    const active = document.activeElement;
    if (active?.contains(event.target)) markFocus(active);
    setTimeout(() => { if (touchClick === event) touchClick = null; }, 0);
  }, true);
  document.addEventListener('focusin', (event) => {
    clearFocus();
    if (touchClick) markFocus(event.target);
  }, true);
  document.addEventListener('focusout', clearFocus, true);
})();
// Apply the saved/system palette before any visible startup content is painted.
(function () {
  var saved;
  try { saved = JSON.parse(localStorage.getItem('bridge-appearance')); } catch (_) {}
  var theme = saved && saved.theme;
  var dark = theme === 'dark' || theme !== 'light' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = dark ? '#17181c' : '#ffffff';
})();
// Optional renderers are shared across tabs, but never gate the chat list.
(function () {
  var resources = new Map(), groups = new Map(), resourceNodes = new Map(), backgroundGroups = new Map(), prefetchScheduled = false;
  function unavailable(slow) {
    var zh = slow ? '组件加载时间较长，请重试' : '组件暂不可用，请重试';
    return new Error(typeof BridgeI18n !== 'undefined' ? BridgeI18n.t(zh) : slow ? 'Component is taking too long to load. Please retry.' : 'Component unavailable. Please retry.');
  }
  function resource(placeholder, background) {
    if (resources.has(placeholder)) return resources.get(placeholder);
    var script = placeholder.tagName === 'SCRIPT';
    var task = new Promise(function (resolve, reject) {
      var node = document.createElement(script ? 'script' : 'link');
      node.bridgeOptionalResource = true;
      node.fetchPriority = background ? 'low' : 'auto';
      resourceNodes.set(placeholder, node);
      node.onload = function () { node.onload = node.onerror = null; resolve(); };
      node.onerror = function () {
        node.onload = node.onerror = null; node.remove(); resources.delete(placeholder); resourceNodes.delete(placeholder);
        reject(unavailable(false));
      };
      if (script) { node.async = false; node.src = placeholder.getAttribute('data-bridge-src'); }
      else { node.rel = 'stylesheet'; node.href = placeholder.getAttribute('data-bridge-href'); }
      document.head.appendChild(node);
    });
    resources.set(placeholder, task);
    return task;
  }
  window.BridgeFeatures = {
    prefetchChat: function () {
      var connection = navigator.connection;
      if (prefetchScheduled || document.hidden || connection && (connection.saveData || /(^|-)2g$/.test(connection.effectiveType) || connection.downlink < 1.5)) return;
      prefetchScheduled = true;
      // Give the visible list a paint and let its foreground reads settle first.
      var schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : function (run) { setTimeout(run, 0); };
      schedule(function () { setTimeout(function () {
        if (document.hidden) { prefetchScheduled = false; return; }
        window.BridgeFeatures.load('chat', {background: true}).catch(function () { prefetchScheduled = false; });
      }, 1500); });
    },
    load: function (name, options) {
      var background = !!(options && options.background);
      if (!groups.has(name) || !background) backgroundGroups.set(name, background);
      if (!background) resourceNodes.forEach(function (node, placeholder) { if (placeholder.dataset.bridgeFeature === name) node.fetchPriority = 'auto'; });
      if (!groups.has(name)) {
        var nodes = Array.from(document.querySelectorAll('[data-bridge-feature]')).filter(function (node) { return node.dataset.bridgeFeature === name; });
        var scripts = nodes.filter(function (node) { return node.tagName === 'SCRIPT'; });
        var styles = nodes.filter(function (node) { return node.tagName === 'LINK'; });
        // Fetch dependencies concurrently, but retain ordered execution and
        // stop at a failed dependency. Slow links otherwise pay an RTT per file.
        var downloads = scripts.map(function (node) {
          var link = document.createElement('link');link.rel = 'preload';link.as = 'script';
          link.href = node.getAttribute('data-bridge-src');link.fetchPriority = backgroundGroups.get(name) ? 'low' : 'auto';
          document.head.appendChild(link);return link;
        });
        var task = Promise.all(styles.map(function (node) { return resource(node, backgroundGroups.get(name)); })).then(function () {
          return scripts.reduce(function (previous, node) { return previous.then(function () { return resource(node, backgroundGroups.get(name)); }); }, Promise.resolve());
        });
        task.then(function () { downloads.forEach(function (node) { node.remove(); }); }, function () { downloads.forEach(function (node) { node.remove(); }); });
        groups.set(name, task);
        task.catch(function () { groups.delete(name); });
      }
      // A slow classic script may still execute. Keep its shared promise instead
      // of inserting a second copy on timeout (which redeclares global classes).
      return new Promise(function (resolve, reject) {
        var timer = setTimeout(function () { reject(unavailable(true)); }, 45000);
        groups.get(name).then(function () { clearTimeout(timer); resolve(); }, function (error) { clearTimeout(timer); reject(error); });
      });
    }
  };
})();
// Keep this small and independent: even i18n or the main app may fail to load.
(function startWhenParsed() {
  if (startWhenParsed.started) return;
  if (document.currentScript) startWhenParsed.source = document.currentScript.src;
  if (document.readyState === 'loading') {
    // Interactive precedes deferred clipboard execution, whose failures we own.
    document.addEventListener('readystatechange', startWhenParsed, {once: true});
    return;
  }
  startWhenParsed.started = true;
  var root = document.getElementById('bootstrap-status');
  var title = document.getElementById('bootstrap-title');
  var message = document.getElementById('bootstrap-message');
  var retry = document.getElementById('bootstrap-retry');
  var details = document.getElementById('bootstrap-details');
  var diagnosticText = document.getElementById('bootstrap-diagnostics');
  var originalUrl = location.href;
  var originalPairing = location.hash.indexOf('#pair=') === 0;
  var complete = false, stopped = false, readyRequested = false, resourcesDone = false;
  var timer = null, deadline = null, phase = 'loading', failureKind = null, cancelRead = null;
  var retryTimers = [], preloads = [];
  var eagerPending = false, startScripts = null;
  var started = Date.now(), lastFailure = null, lastUnattributed = null, pendingResources = [];
  var requiredSources = new Set();
  var english = false;
  try { english = localStorage.getItem('bridge-language') === 'en'; } catch (_) {}
  if (/(?:^|[?&])lang=en(?:&|$)/.test(location.search)) english = true;
  function text(zh, en) { return english ? en : zh; }
  function assetPath(value) {
    // Never display a query, pairing fragment, origin or arbitrary error text.
    var path = String(value || '').split(/[?#]/)[0].replace(/^https?:\/\/[^/]+/i, '');
    return /^\/(?:[\w.-]+\/)*[\w.-]+\.(?:m?js|css)$/.test(path) ? path : '';
  }
  function errorKind(error) {
    var name = error && error.name;
    return /^(?:Syntax|Reference|Type|Range|URI|Eval|Aggregate)?Error$/.test(name) ? name : 'Error';
  }
  function localScriptPath(value) {
    if (typeof value !== 'string' || !value) return '';
    try {
      var source = new URL(value, location.href);
      return source.origin === new URL(location.href).origin ? assetPath(source.pathname) : '';
    } catch (_) { return ''; }
  }
  function requiredPath(value) {
    var path = localScriptPath(value);
    return requiredSources.has(path) ? path : '';
  }
  function runtimePath(error, filename) {
    var source = filename || error && (error.sourceURL || error.fileName);
    if (source) return requiredPath(source);
    var stack = error && error.stack;
    if (typeof stack !== 'string') return '';
    var frames = stack.slice(0, 8192).split('\n').slice(0, 20);
    for (var i = 0; i < frames.length; i++) {
      // WebKit uses name@URL, V8 uses "at name (URL)". Trust the first
      // source frame, not an app caller below an injected/optional script.
      var frame = /^\s*(?:at (?:.*?\()?|[^@]*@)([a-z][a-z\d+.-]*:\/\/[^\s()]+)\)?\s*$/i.exec(frames[i]);
      if (frame) return requiredPath(frame[1].replace(/:\d+(?::\d+)?$/, ''));
    }
    return '';
  }
  function addRequiredSource(value) {
    var path = localScriptPath(value);
    if (path) requiredSources.add(path);
  }
  function retryResourceUrl(value, attempt) {
    if (attempt < 2) return value;
    try {
      var url = new URL(value, location.href);
      if (url.origin !== new URL(location.href).origin || !assetPath(url.pathname)) return value;
      // A failed preload can remain cached. Only an explicit resource error
      // permits a fresh request; a slow script might still execute later.
      url.searchParams.set('bridge_retry', started.toString(36) + '-' + attempt);
      return url.href;
    } catch (_) { return value; }
  }
  addRequiredSource(startWhenParsed.source);
  Array.from(document.querySelectorAll('script[data-bridge-src], #bridge-clipboard-script')).forEach(function (node) {
    if (!node.dataset.bridgeFeature) addRequiredSource(node.getAttribute('data-bridge-src') || node.src);
  });
  function diagnostics() {
    var missing = [];
    if (typeof fetch !== 'function') missing.push('fetch');
    if (typeof Promise !== 'function') missing.push('Promise');
    if (typeof Object.fromEntries !== 'function') missing.push('Object.fromEntries');
    if (typeof Object.hasOwn !== 'function') missing.push('Object.hasOwn');
    if (typeof Element !== 'undefined' && typeof Element.prototype.replaceChildren !== 'function') missing.push('replaceChildren');
    var lines = [text('启动诊断', 'Startup diagnostics'),
      text('阶段：', 'Phase: ') + phase + ' / ' + Math.round((Date.now() - started) / 1000) + 's'];
    if (lastFailure) lines.push(text('错误：', 'Error: ') + lastFailure.kind +
      (lastFailure.path ? ' @ ' + lastFailure.path : '') + (lastFailure.line ? ':' + lastFailure.line : ''));
    if (lastFailure && lastFailure.attempts) lines.push(text('资源尝试：', 'Resource attempts: ') + lastFailure.attempts + ' / 3');
    if (lastUnattributed) lines.push(text('未归属错误（未中断启动）：', 'Unattributed error (startup continued): ') + lastUnattributed.kind + ' / ' + lastUnattributed.event);
    if (pendingResources.length) lines.push(text('等待资源：', 'Waiting for: ') + pendingResources.map(assetPath).filter(Boolean).join(', '));
    if (missing.length) lines.push(text('浏览器缺少能力：', 'Missing browser features: ') + missing.join(', '));
    if (typeof navigator !== 'undefined') lines.push(String(navigator.userAgent || '').slice(0, 256));
    return lines.join('\n');
  }
  function fail(kind) {
    if (complete) return false;
    if (stopped) return true;
    stopped = true;
    failureKind = kind;
    cancelPending();
    root.hidden = false;
    root.setAttribute('data-state', 'failed');
    root.setAttribute('aria-busy', 'false');
    title.textContent = text('工作台尚未加载完成', 'The workspace has not finished loading');
    message.textContent = phase === 'pairing'
      ? text('配对请求结果尚未确认。可重新加载查看连接状态；若仍未登录，请重新扫描新的配对码。', 'The pairing result is not confirmed. Reload to check the connection; if you are still signed out, scan a new pairing code.')
      : kind === 'script'
      ? text('部分页面组件未能启动。请重新加载；若仍失败，请检查网络或更新浏览器。', 'Some page components could not start. Reload the page; if it still fails, check your connection or update your browser.')
      : phase === 'auth'
        ? text('等待网关响应时间较长。请检查网络和电脑网关，再重新加载。', 'The gateway is taking longer to respond. Check your connection and computer gateway, then reload.')
        : text('页面资源加载时间较长。请检查网络后重新加载。', 'Page resources are taking longer to load. Check your connection, then reload.');
    diagnosticText.textContent = diagnostics();
    details.hidden = false;
    return true;
  }
  function recovering() {
    if (complete || stopped) return;
    root.setAttribute('data-state', 'recovering');
    root.setAttribute('aria-busy', 'true');
    title.textContent = text('正在恢复连接…', 'Restoring your connection…');
    message.textContent = text('连接暂时较慢，正在等待恢复，请稍候。', 'The connection is taking a moment. Waiting for it to recover.');
  }
  function progress() {
    if (complete || stopped) return;
    clearTimeout(timer);
    timer = setTimeout(recovering, 12000);
    // Only a stalled startup expires; successful downloads renew the budget.
    clearTimeout(deadline);
    deadline = setTimeout(function () { fail('timeout'); }, 30000);
  }
  function beginPhase() {
    progress();
  }
  function cancelled() {
    var error = new Error('Startup cancelled');
    error.startupCancelled = true;
    return error;
  }
  function cancelPending() {
    clearTimeout(timer);
    clearTimeout(deadline);
    retryTimers.forEach(clearTimeout);
    retryTimers = [];
    preloads.forEach(function (node) { node.remove(); });
    preloads = [];
    if (cancelRead) cancelRead();
  }
  function waitingForAuth() {
    if (complete || stopped) return;
    phase = 'auth';
    beginPhase();
  }
  // Only the initial read uses this policy. Pairing and all other writes stay outside it.
  function readAuth(read) {
    if (complete) return read();
    if (stopped) return Promise.reject(cancelled());
    if (cancelRead) cancelRead();
    return new Promise(function (resolve, reject) {
      var attempt = 0, settled = false, controller = null, attemptTimer = null, retryTimer = null;
      function finish(error, value) {
        if (settled) return;
        settled = true;
        clearTimeout(attemptTimer);
        clearTimeout(retryTimer);
        if (cancelRead === cancel) cancelRead = null;
        if (error) reject(error); else resolve(value);
      }
      function cancel() {
        if (controller) controller.abort();
        finish(cancelled());
      }
      function run() {
        if (settled) return;
        attempt++;
        var current = new AbortController(), finished = false;
        controller = current;
        function end(error, value) {
          if (finished || settled) return;
          finished = true;
          clearTimeout(attemptTimer);
          if (!error) { progress(); finish(null, value); return; }
          var retryable = error.status >= 400 ? error.status === 408 || error.status === 429 || error.status >= 500
            : error.name === 'TypeError' || error.name === 'AbortError';
          if (!retryable || attempt >= 3) { finish(error); return; }
          recovering();
          retryTimer = setTimeout(run, attempt === 1 ? 500 : 1500);
        }
        attemptTimer = setTimeout(function () {
          current.abort();
          var error = new Error('Startup read timed out'); error.name = 'AbortError'; end(error);
        }, 8000);
        Promise.resolve().then(function () { if (!settled) return read(current.signal); }).then(function (value) { end(null, value); }, function (error) { end(error); });
      }
      cancelRead = cancel;
      run();
    });
  }
  function onError(event) {
    if (event.target && event.target.bridgeOptionalResource) return;
    // Resource errors reach the capture listener before the element's retry handler.
    if (event.target && event.target.bridgeStartupResource) return;
    if (event.target && event.target.id === 'bridge-clipboard-script') {
      eagerPending = true;
      recovering();
      // Keep an executable request in flight until this early native clipboard
      // definition is ready. A timer gap could let WebView's load hook run first.
      loadResource(event.target, event.target.src, true, function () {
        eagerPending = false;
        if (startScripts) startScripts();
      }, 1);
      return;
    }
    // Owned script resource errors are handled above. A proxy may inject an
    // optional analytics script that our CSP correctly blocks; it is not a
    // workspace dependency and must not stop the startup queue.
    if (event.target === window) runtimeFailure(event.error, event.filename, event.lineno, 'error');
  }
  function runtimeFailure(error, filename, line, event) {
    if (complete || stopped) return;
    var path = runtimePath(error, filename);
    // Opaque browser/extension errors do not establish a failed dependency.
    // Keep loading; resource failures and the unchanged deadline still apply.
    if (!path) { lastUnattributed = {kind: errorKind(error), event: event}; return; }
    lastFailure = {kind: errorKind(error), path: path,
      line: typeof line === 'number' && line > 0 ? line : 0};
    fail('script');
  }
  function onRejection(event) {
    var error = event && event.reason;
    runtimeFailure(error, '', error && error.line, 'unhandledrejection');
  }
  function loadResource(placeholder, url, scripts, done, eagerAttempts) {
    var attempt = eagerAttempts || 0;
    pendingResources.push(url);
    function load() {
      if (stopped) return;
      var node = document.createElement(scripts ? 'script' : 'link'), settled = false;
      attempt++;
      node.bridgeStartupResource = true;
      function finish(failed) {
        if (settled || stopped) return;
        settled = true;
        node.onload = node.onerror = null;
        if (failed) {
          lastFailure = {kind: 'resource', path: assetPath(url), attempts: attempt};
          if (attempt >= 3) { fail('script'); return; }
          recovering();
          if (eagerAttempts && scripts) { load(); return; }
          retryTimers.push(setTimeout(load, attempt === 1 ? 500 : 1500));
          return;
        }
        pendingResources.splice(pendingResources.indexOf(url), 1);
        if (lastFailure && lastFailure.kind === 'resource' && lastFailure.path === assetPath(url)) lastFailure = null;
        progress();
        done();
      }
      node.onload = function () { finish(false); };
      node.onerror = function () { finish(true); };
      if (scripts) {
        node.async = false;
        node.src = retryResourceUrl(url, attempt);
      } else {
        node.rel = 'stylesheet';
        node.href = retryResourceUrl(url, attempt);
      }
      placeholder.parentNode.replaceChild(node, placeholder);
      placeholder = node;
    }
    load();
  }
  function loadResources() {
    var styles = Array.from(document.querySelectorAll('link[data-bridge-href]')).filter(function (node) { return !node.dataset.bridgeFeature; }), next = 0, active = 0;
    var scripts = Array.from(document.querySelectorAll('script[data-bridge-src]')).filter(function (node) { return !node.dataset.bridgeFeature; });
    function pumpStyles() {
      if (stopped) return;
      while (active < 4 && next < styles.length) {
        var node = styles[next++]; active++;
        loadResource(node, node.getAttribute('data-bridge-href'), false, function () { active--; pumpStyles(); });
      }
      if (!active && next === styles.length) {
        startScripts = loadScripts;
        if (!eagerPending) loadScripts();
      }
    }
    function loadScripts() {
      var index = 0;
      function advance() {
        if (stopped) return;
        if (index === scripts.length) { resourcesDone = true; finishReady(); return; }
        // Downloads are independent; execution still waits for each dependency.
        var script = scripts[index];
        loadResource(script, script.getAttribute('data-bridge-src'), true, function () { index++; advance(); });
      }
      advance();
    }
    // This stylesheet precedes the parser-blocking bootstrap. Its initial load
    // has settled before we run. Some WebViews retain an empty sheet on failure.
    var shell = document.getElementById('bridge-shell-style'), shellReady = false;
    // A failed response can leave a sheet whose cssRules getter throws SecurityError.
    // Treat it like a missing sheet so the bounded loader can recover the resource.
    try { shellReady = !!(shell && shell.sheet && shell.sheet.cssRules.length); } catch (_) {}
    if (shell && !shellReady) loadResource(shell, shell.href, false, pumpStyles, 1);
    else pumpStyles();
    // Discover the required scripts while styles load. Let the browser schedule
    // downloads instead of paying another network round trip per execution group.
    // Keep the same destination and versioned URL so execution/reopens reuse cache.
    var probe = document.createElement('link');
    if (probe.relList && probe.relList.supports && probe.relList.supports('preload')) {
      for (var i = 0; i < scripts.length; i++) {
        var link = document.createElement('link');
        link.rel = 'preload'; link.as = 'script'; link.href = scripts[i].getAttribute('data-bridge-src');
        document.head.appendChild(link); preloads.push(link);
      }
    }
  }
  function pairingSubmitted() {
    phase = 'pairing';
    // Once the write may have reached the server, a reload must not replay it.
    originalUrl = null;
    originalPairing = false;
  }
  function ready() {
    readyRequested = true;
    // A late pairing response can resolve its unknown-result notice.
    if (stopped && phase === 'pairing' && failureKind === 'timeout') stopped = false;
    finishReady();
  }
  function finishReady() {
    if (!readyRequested || !resourcesDone || stopped) return;
    if (complete) return;
    complete = true;
    originalUrl = null;
    cancelPending();
    root.hidden = true;
    window.removeEventListener('error', onError, true);
    window.removeEventListener('unhandledrejection', onRejection);
  }
  title.textContent = text('聊天', 'Chats');
  message.textContent = text('正在连接…', 'Connecting…');
  document.getElementById('bootstrap-details-label').textContent = text('查看详情', 'View details');
  details.hidden = true;
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
  window.addEventListener('pagehide', function (event) { if (!event.persisted) { stopped = true; cancelPending(); } });
  beginPhase();
  window.BridgeBootstrap = {waitingForAuth: waitingForAuth, pairingSubmitted: pairingSubmitted,
    failed: function () { return fail('connection'); }, ready: ready, readAuth: readAuth};
  if (document.readyState !== 'complete') document.addEventListener('DOMContentLoaded', loadResources, {once: true});
  else loadResources();
})();
