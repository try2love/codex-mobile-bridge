// Experimental read-only terminal. Not imported by any production entry point.
// Protocol: zai-org/ZCode @ a9fe401eede6e6e5e01be9048679c206fe6f02d9.
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export const ORIGIN = 'https://zcode.z.ai';
const MAX_BYTES = 1024 * 1024;
const identifier = value => typeof value === 'string' && value.trim() === value
  && value.length > 0 && Buffer.byteLength(value) <= 128;

export class ProbeFailure extends Error {
  constructor(reason, stage) {
    super(reason);
    this.reason = reason;
    this.stage = stage;
  }
}

export function parsePairingLink(value) {
  let url;
  try { url = new URL(value); } catch { throw new ProbeFailure('invalid-link', 'input'); }
  if (url.origin !== ORIGIN || url.pathname !== '/remote/v4' || url.username || url.password
    || url.hash || [...url.searchParams.keys()].some(key => !['sid', 'mid', 'name', 'app_version'].includes(key))
    || [...url.searchParams.keys()].some(key => url.searchParams.getAll(key).length !== 1)) {
    throw new ProbeFailure('invalid-link', 'input');
  }
  const sid = url.searchParams.get('sid'), mid = url.searchParams.get('mid');
  if (!identifier(sid) || !identifier(mid)) throw new ProbeFailure('invalid-link', 'input');
  return { sid, mid };
}

export async function probe({ jwt, link, workspaceKey, timeoutMs = 120_000 }, {
  fetcher = globalThis.fetch,
  createSocket = url => new WebSocket(url),
  random = Math.random,
} = {}) {
  const { sid, mid } = parsePairingLink(link);
  if (typeof jwt !== 'string' || !jwt.trim() || jwt.length > 16_384 || /\s/.test(jwt)) {
    throw new ProbeFailure('auth-required', 'input');
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 180_000
    || (workspaceKey !== undefined && (typeof workspaceKey !== 'string' || !workspaceKey))) {
    throw new ProbeFailure('invalid-options', 'input');
  }
  const started = Date.now();
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), timeoutMs);
  let stage = 'devices';
  const timings = {};
  const mark = name => { timings[name] = Date.now() - started; };
  const http = async (path, body) => {
    try {
      const response = await fetcher(ORIGIN + path, {
        method: body ? 'POST' : 'GET', redirect: 'error', cache: 'no-store',
        headers: { Authorization: `Bearer ${jwt}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}), signal: controller.signal,
      });
      if (response.status === 401) throw new ProbeFailure('auth-required', stage);
      if (!response.ok) throw new ProbeFailure('http-failed', stage);
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read(); if (done) break;
          size += value.byteLength;
          if (size > MAX_BYTES) throw new ProbeFailure('response-too-large', stage);
          chunks.push(Buffer.from(value));
        }
      } finally { await reader.cancel().catch(() => {}); }
      const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (value?.code !== 0 || typeof value.msg !== 'string') throw new ProbeFailure('api-rejected', stage);
      return value.data;
    } catch (error) {
      if (controller.signal.aborted) throw new ProbeFailure('timeout', stage);
      if (error instanceof ProbeFailure) throw error;
      // Never propagate vendor text, URLs, tokens or fetch exceptions.
      throw new ProbeFailure('http-failed', stage);
    }
  };
  try {
    const devices = await http('/api/v1/remote/device');
    if (!Array.isArray(devices)) throw new ProbeFailure('invalid-devices', stage);
    const matches = devices.filter(x => x?.device_sid === sid && x?.device_mid === mid);
    if (matches.length !== 1) throw new ProbeFailure('device-not-owned', stage);
    if (matches[0].online !== true) throw new ProbeFailure('device-offline', stage);
    mark('devices');
    stage = 'token';
    const ticket = await http('/api/v1/remote/token', { role: 'terminal' });
    if (!/^[A-Za-z0-9_-]{21}[AQgw]$/.test(ticket?.token) || !Number.isInteger(ticket?.expires_in)
      || ticket.expires_in <= 0) throw new ProbeFailure('invalid-ticket', stage);
    mark('token');
    const url = new URL('/api/v1/remote/ws2', ORIGIN.replace('https:', 'wss:'));
    url.searchParams.set('token', ticket.token); url.searchParams.set('mid', mid);
    stage = 'socket';
    return await new Promise((resolve, reject) => {
      let socket, heartbeat, watchdog, settled = false, authenticated = false, requested = false;
      let bootstrap, terminalSid;
      const terminalClientSessionId = randomUUID();
      const bootstrapId = randomUUID(), listId = randomUUID(), bridgeId = randomUUID();
      const bridgeRequestId = randomUUID();
      const finish = (error, result) => {
        if (settled) return;
        settled = true;
        clearTimeout(heartbeat); clearTimeout(watchdog);
        controller.signal.removeEventListener('abort', abort);
        const complete = () => error ? reject(error) : resolve(result);
        if (!socket || socket.readyState === 3) { complete(); return; }
        // Wait for our terminal to close before a caller can start another probe.
        const closeTimer = setTimeout(() => reject(new ProbeFailure('close-unconfirmed', stage)), 5000);
        socket.addEventListener('close', () => { clearTimeout(closeTimer); complete(); }, { once: true });
        try { socket.close(1000); } catch { clearTimeout(closeTimer); complete(); }
      };
      const fail = reason => finish(new ProbeFailure(reason, stage));
      const abort = () => fail('timeout');
      const send = message => {
        if (settled) return;
        try { socket.send(JSON.stringify({ ...message, client_ts: Math.floor(Date.now() / 1000) })); }
        catch { fail('socket-failed'); }
      };
      const app = payload => send({ type: 'data', payload });
      const ack = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => fail('heartbeat-timeout'), 15_000);
      };
      const beat = () => {
        heartbeat = setTimeout(() => {
          send({ type: 'pair_status_query' });
          if (!settled) beat();
        }, 4000 + random() * 2000);
      };
      const paired = status => {
        if (!['waiting', 'matched'].includes(status)) { fail('invalid-pair-status'); return; }
        if (status !== 'matched' || requested) return;
        requested = true; mark('paired'); stage = 'bootstrap';
        app({ zcode_type: 'bootstrap-request', requestId: bootstrapId, terminalClientSessionId });
      };
      const summary = bridge => ({
        ok: true, paired: true, bootstrap: true, workspaceList: true,
        workspaceCount: bootstrap.workspaces.length,
        desktopAppVersion: typeof bootstrap.desktopAppVersion === 'string'
          && /^[0-9A-Za-z.+-]{1,40}$/.test(bootstrap.desktopAppVersion) ? bootstrap.desktopAppVersion : null,
        activeTaskPresent: Boolean(bootstrap.mobileViewState?.activeTaskId || bootstrap.initialViewState?.activeTaskId),
        bridgeReady: Boolean(bridge), timingsMs: timings, modelRequests: 0,
      });
      controller.signal.addEventListener('abort', abort, { once: true });
      if (controller.signal.aborted) { abort(); return; }
      try { socket = createSocket(url.toString()); } catch { fail('socket-failed'); return; }
      socket.addEventListener('open', () => {
        if (settled) return;
        stage = 'pairing'; ack(); beat();
        send({ type: 'auth_init', device_sid: sid,
          meta: { platform: 'web', version: 'cmb-experiment', name: 'Bridge read-only probe' } });
      });
      socket.addEventListener('error', () => fail('socket-failed'));
      socket.addEventListener('close', () => fail('socket-closed'));
      socket.addEventListener('message', event => {
        if (settled) return;
        try {
          if (typeof event.data !== 'string' || Buffer.byteLength(event.data) > MAX_BYTES) { fail('invalid-frame'); return; }
          const message = JSON.parse(event.data);
          if (message.type === 'error') {
            fail(({ AUTH_FAILED: 'auth-required', KICKED: 'terminal-replaced', DEVICE_OFFLINE: 'device-offline',
              WRONG_PARAM: 'protocol-rejected', INTERNAL: 'relay-failed' })[message.code] || 'relay-failed');
          } else if (message.type === 'auth_ack') {
            if (authenticated || message.device_sid !== sid) { fail('device-mismatch'); return; }
            authenticated = true; terminalSid = message.terminal_sid; ack(); paired(message.pair_status);
          } else if (message.type === 'pair_status_ack' && authenticated) {
            if (terminalSid && message.terminal_sid && terminalSid !== message.terminal_sid) { fail('terminal-mismatch'); return; }
            ack(); paired(message.pair_status);
          } else if (message.type === 'data' && authenticated && requested) {
            const p = message.payload;
            if (p?.zcode_type === 'app-error' || p?.zcode_type === 'workspace-bridge-error') { fail('desktop-rejected'); return; }
            if (stage === 'bootstrap' && p?.zcode_type === 'bootstrap-response' && p.requestId === bootstrapId) {
              if (p.success !== true || !identifier(p.result?.windowControlSessionId) || !Array.isArray(p.result?.workspaces)) {
                fail('invalid-bootstrap'); return;
              }
              bootstrap = p.result; mark('bootstrap'); stage = 'workspace-list';
              app({ zcode_type: 'workspace-list-request', requestId: listId });
            } else if (stage === 'workspace-list' && p?.zcode_type === 'workspace-list-response' && p.requestId === listId) {
              if (p.success !== true || !Array.isArray(p.result?.workspaces)) { fail('invalid-workspaces'); return; }
              mark('workspaceList');
              if (!workspaceKey) { finish(null, summary()); return; }
              const workspaces = p.result.workspaces.filter(x => (x.workspaceIdentity || x.workspacePath) === workspaceKey);
              if (workspaces.length !== 1) { fail('workspace-not-found'); return; }
              stage = 'workspace-bridge';
              app({ zcode_type: 'workspace-bridge-open', requestId: bridgeRequestId,
                bridgeSessionId: bridgeId, terminalClientSessionId, workspaceKey });
            } else if (stage === 'workspace-bridge' && p?.zcode_type === 'workspace-bridge-ready' && p.requestId === bridgeRequestId) {
              if (p.bridgeSessionId !== bridgeId || p.bridge?.bridgeSessionId !== bridgeId || p.bridge?.workspaceKey !== workspaceKey) {
                fail('workspace-mismatch'); return;
              }
              mark('bridgeReady'); finish(null, summary(p.bridge));
            }
          }
        } catch { fail('invalid-frame'); }
      });
    });
  } finally { clearTimeout(deadline); controller.abort(); }
}

async function main() {
  // Only stdin accepts credentials. Never put a JWT in args or environment variables.
  let input = '';
  try {
    for await (const chunk of process.stdin) {
      input += chunk;
      if (Buffer.byteLength(input) > 32_768) throw new ProbeFailure('input-too-large', 'input');
    }
    const options = JSON.parse(input); input = '';
    const result = await probe(options);
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch (error) {
    const safe = error instanceof ProbeFailure ? error : new ProbeFailure('invalid-input', 'input');
    process.stdout.write(JSON.stringify({ ok: false, reason: safe.reason, stage: safe.stage, modelRequests: 0 }) + '\n');
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
