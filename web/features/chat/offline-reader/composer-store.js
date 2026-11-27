'use strict';
(() => {
  const reader = () => window.OfflineChatReader;
  const cold = () => reader()?.cold === true;
  const records = new Map(), writers = new Map(), guards = new Map(), guardVersions = new Map();
  let auth = null, request = null, refreshSession = null, generation = 0, database, running, timer, wakeAgain = false;
  const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const key = route => {
    if (!route || !route.provider || !route.session) throw Error('请选择会话');
    return JSON.stringify([route.provider, route.host || 'local', route.session]);
  };
  const parse = id => {
    const [provider, host, session] = JSON.parse(id);
    return {provider, host, session};
  };
  const emptyDraft = () => ({text:'', selectionStart:0, selectionEnd:0, quotes:[], skills:[], attachmentDrafts:[], workMode:'default'});
  const empty = () => ({account:'', gateway:'', canSend:false, title:'', draft:null, outbox:[], context:{}});
  const ownerKey = sync => JSON.stringify([sync.gatewayId, sync.identity]);
  function currentOwner() {
    const sync = auth?.sessionSync;
    if (!sync) throw Error('无法确认本地保存位置');
    return ownerKey(sync);
  }
  async function openDisk() {
    if (!database) database = new Promise((resolve, reject) => {
      const open = indexedDB.open('bridge-offline-composer', 1);
      open.onupgradeneeded = () => open.result.createObjectStore('records').createIndex('pendingOwner', 'pendingOwner');
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    }).catch(error => { database = null; throw error; });
    return database;
  }
  async function disk(action, fields, owner) {
    const db = await openDisk(), write = action === 'composerWrite';
    return new Promise((resolve, reject) => {
      const tx = db.transaction('records', write ? 'readwrite' : 'readonly'), table = tx.objectStore('records');
      const id = fields.sessionId, lookup = action === 'composerPending'
        ? table.index('pendingOwner').getAllKeys(owner) : table.get([owner, id]);
      let result;
      lookup.onsuccess = () => {
        if (action === 'composerPending') { result = {sessions:lookup.result.map(pair => pair[1])}; return; }
        const {revision = 0, value = null} = lookup.result || {};
        result = {revision, value};
        if (!write) return;
        if (fields.revision !== revision) { result.applied = false; return; }
        result = {revision:revision + 1, value:fields.value, applied:true};
        const row = {revision:result.revision, value:result.value};
        if (row.value.outbox?.length) row.pendingOwner = owner;
        table.put(row, [owner, id]);
      };
      tx.oncomplete = () => resolve(result);
      tx.onabort = tx.onerror = () => reject(tx.error || Error('本地保存失败'));
    });
  }
  async function storage(action, fields, epoch) {
    if (epoch !== generation) throw Error('登录身份已变化');
    let result;
    if (cold()) {
      result = await reader().composer(action, fields);
    } else if (window.CodexBridgeNativeOffline) {
      const sync = auth?.sessionSync, cache = window.BridgeChatSyncCache;
      if (!sync || cache?.owner && ownerKey(sync) !== ownerKey(cache.owner)) throw Error('无法确认本地保存位置');
      await cache?.nativeReady;
      if (epoch !== generation) throw Error('登录身份已变化');
      result = await window.CodexBridgeNativeOffline.request({type:action, gatewayId:sync.gatewayId, identity:sync.identity, ...fields});
    } else {
      result = await disk(action, fields, currentOwner());
    }
    if (epoch !== generation) throw Error('登录身份已变化');
    if (!result || (action === 'composerPending' ? !Array.isArray(result.sessions) :
        !Number.isSafeInteger(result.revision) || action === 'composerWrite' && typeof result.applied !== 'boolean')) {
      throw Error('本地存储暂不可用');
    }
    return result;
  }
  function namespace(id) {
    if (!id.startsWith('[')) return '';
    const {provider, host} = parse(id);
    return 'binding:' + JSON.stringify([provider, host]);
  }
  function binding(id, epoch) {
    const scope = namespace(id);
    if (!scope) return '';
    if (!guards.has(scope)) {
      const task = storage('composerRead', {sessionId:scope}, epoch).then(row => row.value?.token || '');
      guards.set(scope, task);
      task.catch(() => { if (guards.get(scope) === task) guards.delete(scope); });
    }
    return guards.get(scope);
  }
  function archive(record) {
    const {previous = {}, ...last} = record;
    if (last.account) previous[JSON.stringify([last.gateway, last.account])] = last;
    return previous;
  }
  function inBinding(value, token) {
    if (!value || (value.binding || '') === token) return value;
    return {...empty(), previous:archive(copy(value)), binding:token};
  }
  function visible(value) {
    if (!value) return null;
    const {previous, ...record} = value;
    return copy(record);
  }
  function notify(id) {
    if (id.startsWith('[')) window.dispatchEvent(new CustomEvent('bridge-offline-composer-change', {detail:{route:parse(id)}}));
  }
  // Serialize one editor's intents. The storage CAS also arbitrates different tabs and native WebViews.
  function ordered(id, task) {
    const epoch = generation, scope = namespace(id), version = guardVersions.get(scope);
    const assertCurrent = () => {
      if (epoch !== generation || version !== guardVersions.get(scope)) throw Error('会话账号已变化');
    };
    const before = writers.get(id) || Promise.resolve();
    const next = before.catch(() => {}).then(() => { assertCurrent(); return task(epoch, assertCurrent); });
    writers.set(id, next);
    const done = () => { if (writers.get(id) === next) writers.delete(id); };
    next.then(done, done);
    return next;
  }
  function read(id) {
    return ordered(id, async (epoch, assertCurrent) => {
      const row = await storage('composerRead', {sessionId:id}, epoch);
      const value = inBinding(row.value, await binding(id, epoch));
      assertCurrent();
      const previous = records.get(id);
      records.set(id, value);
      if (previous || value) {
        if (JSON.stringify(previous) !== JSON.stringify(value)) notify(id);
      }
      return visible(value);
    });
  }
  function change(id, transform) {
    return ordered(id, async (epoch, assertCurrent) => {
      let row = await storage('composerRead', {sessionId:id}, epoch);
      for (;;) {
        const token = await binding(id, epoch);
        assertCurrent();
        const current = inBinding(row.value, token);
        const next = transform(copy(current));
        if (next && token) next.binding = token;
        if (!next || JSON.stringify(next) === JSON.stringify(row.value)) {
          records.set(id, current);
          return visible(current);
        }
        row = await storage('composerWrite', {sessionId:id, revision:row.revision, value:next}, epoch);
        assertCurrent();
        if (row.applied) {
          records.set(id, row.value); notify(id);
          if (!cold() && auth) channel?.postMessage({owner:currentOwner(), id});
          return visible(row.value);
        }
      }
    });
  }
  let channel;
  try { channel = new BroadcastChannel('bridge-offline-composer'); } catch { /* Optional across tabs and origins. */ }
  if (channel) channel.onmessage = ({data}) => {
    if (!auth || !data || data.owner !== currentOwner() || typeof data.id !== 'string') return;
    if (data.id.startsWith('binding:')) {
      guardVersions.set(data.id, (guardVersions.get(data.id) || 0) + 1);
      guards.delete(data.id);
      for (const id of records.keys()) if (namespace(id) === data.id) {
        records.delete(id); notify(id);
        void read(id).catch(() => {});
      }
    } else if (records.has(data.id)) void read(data.id).catch(() => {});
    if (data.id) schedule(250);
  };
  function invalidate(provider, host = 'local') {
    const scope = 'binding:' + JSON.stringify([provider, host]), token = identifier();
    guardVersions.set(scope, (guardVersions.get(scope) || 0) + 1);
    const write = change(scope, () => ({token})).then(() => token);
    guards.set(scope, write);
    for (const id of records.keys()) if (namespace(id) === scope) { records.delete(id); notify(id); }
    return write;
  }
  function observe(route, meta) {
    const id = key(route), account = meta?.account;
    if (cold() || !auth || !account) return Promise.resolve(null);
    const gateway = meta.gateway || auth.draftIdentity || auth.sessionSync.gatewayId;
    if (gateway !== (auth.draftIdentity || auth.sessionSync.gatewayId)) return Promise.resolve(null);
    const current = records.get(id);
    if (current?.account === account && current.gateway === gateway && current.canSend === (meta.canSend === true)
        && (!meta.title || current.title === meta.title)) return Promise.resolve(visible(current));
    return change(id, stored => {
      let record = stored || empty();
      const target = JSON.stringify([gateway, account]);
      if (record.account !== account || record.gateway !== gateway) {
        const previous = archive(record);
        const restored = previous[target] || empty();
        if (!record.account && record.draft) restored.draft = record.draft;
        record = {...restored, previous};
        delete previous[target];
      }
      return {...record, gateway, account, canSend:meta.canSend === true, title:meta.title || record.title};
    });
  }
  function mutateRoute(route, fn, expected) {
    const id = key(route), scope = expected || records.get(id)?.account;
    return change(id, stored => {
      if (scope && stored?.account !== scope) throw Error('会话账号已变化');
      return fn(stored || empty());
    });
  }
  function identifier() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    return uuid(); // Shared UUID fallback for HTTP and native custom origins.
  }
  function enqueue(route, body, replacement) {
    const id = identifier();
    return mutateRoute(route, record => {
      if (!record.canSend || !record.account || !record.gateway) throw Error('连接后确认会话即可发送');
      if ((!body?.text?.trim() && !body?.attachments?.length && !body?.references?.length) || (body.text || '').length > 100000) throw Error('请输入 1–100000 字的消息');
      const {id:oldId, sessionId, ...message} = copy(body);
      record.outbox.push({id, body:message, state:'pending', createdAt:Date.now()});
      record.draft = copy(replacement || emptyDraft());
      return record;
    }).then(record => { schedule(100); return record; });
  }
  function edit(route, id, text) {
    const replacement = identifier();
    return mutateRoute(route, record => {
      const item = record.outbox.find(row => row.id === id);
      if (!item || !['pending','failed'].includes(item.state)) throw Error('消息已开始投递');
      if (typeof text !== 'string' || (!text.trim() && !item.body.attachments?.length && !item.body.references?.length) || text.length > 100000) throw Error('请输入 1–100000 字的消息');
      item.body.text = text;
      if (item.state === 'failed') {
        item.id = replacement; item.state = 'pending'; delete item.error;
      }
      return record;
    }).then(record => { schedule(0); return record; });
  }
  function remove(route, id) {
    return mutateRoute(route, record => {
      const item = record.outbox.find(row => row.id === id);
      if (item?.state === 'checking') throw Error('正在核对投递结果');
      record.outbox = record.outbox.filter(row => row.id !== id);
      return record;
    }).then(record => { schedule(0); return record; });
  }
  function live() {
    return !!auth && !!request && !cold() && !reader()?.enabled && globalThis.navigator?.onLine !== false;
  }
  function suspend() {
    generation++; records.clear(); writers.clear(); guards.clear(); guardVersions.clear(); clearTimeout(timer); timer = null;
    auth = null; request = null; refreshSession = null; running = null; wakeAgain = false;
  }
  function bind(verified, api, onVerified) {
    if (cold()) return;
    const sync = verified?.sessionSync;
    if (!verified?.authenticated || sync?.version !== 1 || !/^[a-f0-9]{32}$/.test(sync.gatewayId) || !/^[a-f0-9]{64}$/.test(sync.identity)) { suspend(); return; }
    if (!auth || ownerKey(auth.sessionSync) !== ownerKey(sync)) suspend();
    auth = verified; request = api; refreshSession = onVerified; schedule(0);
  }
  function schedule(delay) {
    if (!live()) return;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; void flush(); }, delay);
  }
  function deliveryPath(route, row, account, gatewayId) {
    const params = {id:row.id, account, gatewayId};
    if (route.provider !== 'codex') return ClientIdentity.path(route.provider, 'delivery', route.session, params);
    return '/api/sessions/' + encodeURIComponent(route.session) + '/delivery?' + new URLSearchParams({...params, host:route.host});
  }
  async function drain(route, epoch, call, gatewayId) {
    const id = key(route);
    for (;;) {
      if (!live() || epoch !== generation) return false;
      const record = await read(id), item = record?.outbox?.[0];
      if (!item || item.state === 'failed' || record.gateway !== (auth.draftIdentity || gatewayId)) return false;
      const account = record.account, path = deliveryPath(route, item, account, gatewayId);
      const proof = await call(path);
      if (!live() || epoch !== generation || proof?.deliveryVersion !== 1 || proof.account !== account) return true;
      let status = proof.status, failure;
      if (item.state === 'pending' && status === 'notFound') {
        if (!proof.ready) return true;
        let claimed = false, body;
        await change(id, value => {
          claimed = false;
          const row = value?.outbox?.[0];
          if (value?.account !== account || row?.id !== item.id || row.state !== 'pending') return;
          row.state = 'checking'; claimed = true; body = row.body; return value;
        });
        if (!claimed || epoch !== generation) return true;
        if (!live()) {
          await change(id, value => {
            const row = value?.outbox?.[0];
            if (value?.account !== account || row?.id !== item.id || row.state !== 'checking') return;
            row.state = 'pending'; return value;
          });
          return true;
        }
        try {
          const sent = await call(path, {...body, id:item.id, sessionId:route.session});
          status = sent?.id === item.id ? sent.status : 'unknown';
        } catch (error) {
          // Only the gateway's explicit pre-dispatch rejection proves that nothing was sent.
          if (error.code !== 'offline_delivery_wait' && error.code !== 'offline_delivery_rejected') return true;
          status = error.code === 'offline_delivery_wait' ? 'retry' : 'failed';
          failure = error.message;
        }
      }
      if (!live() || epoch !== generation) return true;
      const accepted = ['accepted','queued','ready','sent','delivered'].includes(status);
      if (!accepted && !['retry','failed','rejected','withdrawn','cancelled'].includes(status)) {
        if (item.state === 'pending' && status !== 'notFound') {
          await change(id, value => {
            const row = value?.outbox?.[0];
            if (value?.account !== account || row?.id !== item.id || row.state !== 'pending') return;
            row.state = 'checking'; return value;
          });
        }
        return true;
      }
      await change(id, value => {
        const row = value?.outbox?.[0];
        if (value?.account !== account || row?.id !== item.id) return;
        if (accepted) value.outbox.shift();
        else {
          row.state = status === 'retry' ? 'pending' : 'failed';
          if (row.state === 'failed' && failure) row.error = failure;
        }
        return value;
      });
      if (!accepted) return status === 'retry';
    }
  }
  function flush() {
    if (!live()) return Promise.resolve();
    if (running) { wakeAgain = true; return running; }
    const epoch = generation, api = request, sync = auth.sessionSync;
    const current = () => live() && generation === epoch;
    const call = (path, body) => api(path, body, undefined, current, true);
    const task = (async () => {
      let retry = false, hasPending = false;
      try {
        const {sessions} = await storage('composerPending', {}, epoch);
        if (!sessions.length || !current()) return;
        hasPending = true;
        const check = await call('/api/auth');
        if (!check?.authenticated || check.sessionSync?.gatewayId !== sync.gatewayId || check.sessionSync?.identity !== sync.identity || !current()) return;
        if ((check.draftIdentity || sync.gatewayId) !== (auth.draftIdentity || sync.gatewayId)) return;
        refreshSession?.(check);
        if (check.online === false) { retry = true; return; }
        await Promise.all(sessions.map(async id => {
          try { if (await drain(parse(id), epoch, call, sync.gatewayId)) retry = true; }
          catch { retry = true; }
        }));
      } catch { retry = hasPending; }
      finally {
        if (epoch === generation) {
          if (wakeAgain) { wakeAgain = false; schedule(0); }
          else if (retry) schedule(10000);
        }
      }
    })();
    running = task;
    task.finally(() => { if (running === task) running = null; });
    return task;
  }
  window.addEventListener('online', () => schedule(0));
  window.addEventListener('pageshow', () => schedule(0));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(0); });
  document.addEventListener('bridge-hot-offline', event => { if (!event.detail?.active) schedule(0); });
  window.BridgeOfflineComposer = {
    bind, suspend, observe, invalidate, flush, get:route => read(key(route)), peek:route => route ? visible(records.get(key(route))) : null,
    saveDraft:(route, draft) => mutateRoute(route, record => ({...record, draft:copy(draft || emptyDraft())})),
    saveContext:(route, patch, account) => mutateRoute(route, record => record.account && (!account || record.account === account)
      ? {...record, context:{...record.context, ...copy(patch)}} : null, account),
    preferences:() => read(''), savePreferences:value => change('', () => copy(value)), enqueue, edit, remove
  };
})();
