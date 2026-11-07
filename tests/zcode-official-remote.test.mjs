import test from 'node:test';
import assert from 'node:assert/strict';
import { probe, parsePairingLink, ORIGIN } from '../scripts/zcode-official-remote/probe.mjs';

const link = `${ORIGIN}/remote/v4?sid=device-one&mid=machine-one&app_version=3.17.2`;
const jwt = 'private-login-token';
const workspace = { workspacePath: '/isolated-project', label: 'test', kind: 'local' };

function fixture({ device, httpStatus = 200, failHttp, server, neverReply = false } = {}) {
  const requests = [], frames = [], sockets = [], urls = [];
  class Socket extends EventTarget {
    readyState = 0;
    constructor(url) {
      super(); urls.push(url); sockets.push(this);
      queueMicrotask(() => { this.readyState = 1; this.dispatchEvent(new Event('open')); });
    }
    receive(value) {
      queueMicrotask(() => this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(value) })));
    }
    send(text) {
      const m = JSON.parse(text); frames.push(m);
      if (neverReply) return;
      if (server?.(m, this)) return;
      if (m.type === 'auth_init') this.receive({ type: 'auth_ack', device_sid: m.device_sid, terminal_sid: 'terminal-one', pair_status: 'matched' });
      if (m.type === 'pair_status_query') this.receive({ type: 'pair_status_ack', terminal_sid: 'terminal-one', pair_status: 'matched' });
      const p = m.payload;
      if (p?.zcode_type === 'bootstrap-request') this.receive({ type: 'data', payload: {
        zcode_type: 'bootstrap-response', requestId: p.requestId, success: true,
        result: { windowControlSessionId: 'window-one', desktopAppVersion: '3.17.2', workspaces: [workspace] },
      } });
      if (p?.zcode_type === 'workspace-list-request') this.receive({ type: 'data', payload: {
        zcode_type: 'workspace-list-response', requestId: p.requestId, success: true, result: { workspaces: [workspace] },
      } });
      if (p?.zcode_type === 'workspace-bridge-open') this.receive({ type: 'data', payload: {
        zcode_type: 'workspace-bridge-ready', requestId: p.requestId, bridgeSessionId: p.bridgeSessionId,
        bridge: { kind: 'local', bridgeSessionId: p.bridgeSessionId, workspaceKey: p.workspaceKey, workspacePath: p.workspaceKey },
      } });
    }
    close() { this.readyState = 3; queueMicrotask(() => this.dispatchEvent(new Event('close'))); }
  }
  return { requests, frames, sockets, urls, io: {
    createSocket: url => new Socket(url),
    fetcher: async (url, options) => {
      requests.push({ url, options });
      if (failHttp) throw Error(`secret ${jwt} ticket=in-error`);
      return new Response(JSON.stringify({ code: 0, msg: 'ok', data: url.endsWith('/device')
        ? [device || { device_sid: 'device-one', device_mid: 'machine-one', online: true }]
        : { token: (requests.length > 2 ? 'b' : 'a').repeat(21) + 'A', expires_in: 60 },
      }), { status: httpStatus });
    },
  } };
}

test('official origin and exact QR parameters only', () => {
  assert.deepEqual(parsePairingLink(link), { sid: 'device-one', mid: 'machine-one' });
  for (const bad of [link.replace('https:', 'http:'), link.replace('zcode.z.ai', 'evil.test'), link + '&token=secret',
    link + '&sid=other', link + '#fragment', link.replace('device-one', ''), link.replace('/remote/v4', '/else')]) {
    assert.throws(() => parsePairingLink(bad), { reason: 'invalid-link' });
  }
});

test('owned device -> fresh terminal token -> bootstrap -> list; closes without model or generic RPC', async () => {
  const f = fixture(); const result = await probe({ jwt, link }, f.io);
  assert.equal(result.ok, true); assert.equal(result.workspaceCount, 1); assert.equal(result.bridgeReady, false);
  assert.equal(result.modelRequests, 0); assert.equal(f.sockets[0].readyState, 3);
  assert.deepEqual(f.requests.map(x => new URL(x.url).pathname), ['/api/v1/remote/device', '/api/v1/remote/token']);
  assert.equal(f.requests[1].options.body, '{"role":"terminal"}');
  assert.ok(f.requests.every(x => x.options.redirect === 'error'));
  assert.deepEqual(f.frames.map(x => x.payload?.zcode_type || x.type), ['auth_init', 'bootstrap-request', 'workspace-list-request']);
  assert.ok(!JSON.stringify(result).includes(jwt));
});

test('explicit workspace opens exactly returned native identity', async () => {
  const f = fixture(); const r = await probe({ jwt, link, workspaceKey: '/isolated-project' }, f.io);
  assert.equal(r.bridgeReady, true);
  const p = f.frames.at(-1).payload;
  assert.equal(p.workspaceKey, '/isolated-project'); assert.equal(p.taskId, undefined);
});

for (const device of [
  { device_sid: 'other', device_mid: 'machine-one', online: true },
  { device_sid: 'device-one', device_mid: 'other', online: true },
  { device_sid: 'device-one', device_mid: 'machine-one', online: false },
]) test(`reject unowned/offline device ${JSON.stringify(device)}`, async () => {
  const f = fixture({ device });
  await assert.rejects(probe({ jwt, link }, f.io), e => ['device-not-owned', 'device-offline'].includes(e.reason));
  assert.equal(f.requests.length, 1); assert.equal(f.sockets.length, 0);
});

test('unknown workspace cannot open a different workspace', async () => {
  const f = fixture(); await assert.rejects(probe({ jwt, link, workspaceKey: '/other' }, f.io), { reason: 'workspace-not-found' });
  assert.equal(f.frames.filter(x => x.payload?.zcode_type === 'workspace-bridge-open').length, 0);
});

test('auth expiry stops before obtaining a terminal token', async () => {
  const f = fixture({ httpStatus: 401 }); await assert.rejects(probe({ jwt, link }, f.io), { reason: 'auth-required' });
  assert.equal(f.requests.length, 1);
});

test('HTTP errors redact credentials and upstream response text', async () => {
  const f = fixture({ failHttp: true });
  await assert.rejects(probe({ jwt, link }, f.io), e => e.reason === 'http-failed' && !e.stack.includes(jwt));
});

test('KICKED closes; never acquires another token automatically', async () => {
  const f = fixture({ server(m, s) { s.receive({ type: 'error', code: 'KICKED', message: jwt }); return true; } });
  await assert.rejects(probe({ jwt, link }, f.io), { reason: 'terminal-replaced' });
  assert.equal(f.requests.length, 2); assert.equal(f.sockets[0].readyState, 3);
});

test('auth ACK for another device is not a valid connection', async () => {
  const f = fixture({ server(m, s) { s.receive({ type: 'auth_ack', device_sid: 'other', pair_status: 'matched' }); return true; } });
  await assert.rejects(probe({ jwt, link }, f.io), { reason: 'device-mismatch' });
  assert.equal(f.frames.length, 1);
});

test('unrelated response ID cannot finish bootstrap', async () => {
  const f = fixture({ server(m, s) {
    if (m.payload?.zcode_type !== 'bootstrap-request') return false;
    s.receive({ type: 'data', payload: { zcode_type: 'bootstrap-response', requestId: 'old-request', success: true,
      result: { windowControlSessionId: 'window-one', workspaces: [] } } }); return true;
  } });
  await assert.rejects(probe({ jwt, link, timeoutMs: 30 }, f.io), { reason: 'timeout', stage: 'bootstrap' });
  assert.equal(f.sockets[0].readyState, 3);
});

test('bounded wait closes silent socket and clears heartbeat', async () => {
  const f = fixture({ neverReply: true });
  await assert.rejects(probe({ jwt, link, timeoutMs: 30 }, f.io), { reason: 'timeout' });
  assert.equal(f.sockets[0].readyState, 3); assert.equal(f.frames.length, 1);
});

test('explicit reconnect always obtains a fresh one-use ticket', async () => {
  const f = fixture(); await probe({ jwt, link }, f.io); await probe({ jwt, link }, f.io);
  assert.equal(f.requests.length, 4); assert.notEqual(f.urls[0], f.urls[1]);
  assert.ok(f.sockets.every(s => s.readyState === 3));
});
