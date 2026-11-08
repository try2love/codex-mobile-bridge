import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { parseLink } from './link.cjs';
import { createAcknowledgedWebRemoteControlRelayProtocol, ChannelClient, ProxyChannel, ServiceChannels, ensureAgentV4ConnectionHandshake } from './vendor/zcode-official.mjs';

const ORIGIN = 'https://zcode.z.ai';
const MAX_BYTES = 1024 * 1024;
const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 128 && value.trim() === value;
export class RelayFailure extends Error {
  constructor(reason) { super(reason); this.code = reason; this.reason = reason; }
}

// This is a terminal connection, never a second agent or a model client. A dead
// socket terminates its RPC owner. In particular KICKED must not steal it back.
export async function connectProtocol({ jwt, link, timeoutMs = 120_000, waitForDevice = false, signal, onState = () => {} }, {
  fetcher = globalThis.fetch, createSocket = url => new WebSocket(url), random = Math.random,
} = {}) {
  const { sid, mid } = parseLink(link);
  if (typeof jwt !== 'string' || !jwt || jwt.length > 16384 || /\s/.test(jwt)) throw new RelayFailure('auth-required');
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 180000) throw new RelayFailure('invalid-timeout');
  const abort = new AbortController();
  let stopped = false, stopReason, socket, heartbeat, watchdog, adapter, client, bootstrap, device;
  let terminalSid, authenticated = false, requested = false, connected = false;
  let openingBridgeId, earlyBytes = 0;
  const earlyFrames = [];
  const terminalClientSessionId = randomUUID();
  const pending = new Map();
  const state = (value) => { try { onState(value); } catch { /* Observer cannot own the transport. */ } };
  let resolveConnection, rejectConnection;
  const connection = new Promise((resolve, reject) => { resolveConnection = resolve; rejectConnection = reject; });
  // HTTP can fail before the caller awaits this promise.
  connection.catch(() => {});
  const deadline = setTimeout(() => stop('connection-timeout'), timeoutMs);
  function stop(reason = 'disconnected') {
    if (stopped) return;
    stopped = true; stopReason = reason; connected = false;
    clearTimeout(deadline); clearTimeout(heartbeat); clearTimeout(watchdog);
    abort.abort();
    client?.dispose(); adapter?.dispose();
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new RelayFailure(reason)); }
    pending.clear();
    earlyFrames.length = 0;
    try { socket?.close(1000); } catch { /* Already closed. */ }
    rejectConnection(new RelayFailure(reason));
    state({ connected: false, reason });
  }
  const cancelled = () => stop('connection-cancelled');
  signal?.addEventListener('abort', cancelled, { once: true });
  const http = async (path, body) => {
    const response = await fetcher(ORIGIN + path, { method: body ? 'POST' : 'GET', redirect: 'error', cache: 'no-store',
      headers: { Authorization: 'Bearer ' + jwt, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}), signal: abort.signal });
    if (response.status === 401) throw new RelayFailure('auth-required');
    if (!response.ok) throw new RelayFailure('http-failed');
    const reader = response.body.getReader(); const chunks = []; let length = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        length += value.byteLength;
        if (length > MAX_BYTES) throw new RelayFailure('response-too-large');
        chunks.push(Buffer.from(value));
      }
    } finally { await reader.cancel().catch(() => {}); }
    const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (result?.code !== 0) throw new RelayFailure('api-rejected');
    return result.data;
  };
  const send = message => {
    if (stopped || socket?.readyState !== 1) return false;
    const text = JSON.stringify({ ...message, client_ts: Math.floor(Date.now() / 1000) });
    if (Buffer.byteLength(text) > MAX_BYTES) { stop('frame-too-large'); return false; }
    try { socket.send(text); return true; } catch { stop('socket-failed'); return false; }
  };
  const app = payload => send({ type: 'data', payload });
  const request = (payload, responseType) => new Promise((resolve, reject) => {
    if (stopped) { reject(new RelayFailure('disconnected')); return; }
    const requestId = randomUUID();
    const timer = setTimeout(() => { pending.delete(requestId); reject(new RelayFailure('request-timeout')); }, timeoutMs);
    pending.set(requestId, { responseType, resolve, reject, timer });
    if (!app({ ...payload, requestId })) {
      clearTimeout(timer); pending.delete(requestId); reject(new RelayFailure('socket-failed'));
    }
  });
  const ack = () => {
    clearTimeout(watchdog);
    // The official heartbeat allowance is 15 s; keep the existing weak-network
    // tolerance, while making every reconnect an explicit fresh ownership bind.
    watchdog = setTimeout(() => stop('heartbeat-timeout'), 15000);
  };
  const beat = () => {
    heartbeat = setTimeout(() => { if (!stopped) { send({ type: 'pair_status_query' }); beat(); } }, 4000 + random() * 2000);
  };
  async function paired(status) {
    if (!['waiting', 'matched'].includes(status)) { stop('invalid-pair-status'); return; }
    if (status !== 'matched') { if (connected) stop('device-offline'); return; }
    if (requested) return;
    requested = true;
    try {
      const first = await request({ zcode_type: 'bootstrap-request', terminalClientSessionId }, 'bootstrap-response');
      if (first.success !== true || !identifier(first.result?.windowControlSessionId) || !Array.isArray(first.result?.workspaces))
        throw new RelayFailure('invalid-bootstrap');
      bootstrap = first.result;
      const listed = await request({ zcode_type: 'workspace-list-request' }, 'workspace-list-response');
      if (listed.success !== true || !Array.isArray(listed.result?.workspaces)) throw new RelayFailure('invalid-workspaces');
      bootstrap = { ...bootstrap, workspaces: listed.result.workspaces };
      const workspace = bootstrap.workspaces.find(row => row?.kind === 'local' && typeof row.workspacePath === 'string' && row.workspacePath && !row.workspaceIdentity);
      if (!workspace) throw new RelayFailure('local-workspace-unavailable');
      const bridgeSessionId = randomUUID();
      openingBridgeId = bridgeSessionId;
      const ready = await request({ zcode_type: 'workspace-bridge-open', terminalClientSessionId,
        bridgeSessionId, workspaceKey: workspace.workspacePath }, 'workspace-bridge-ready');
      const bridge = ready.bridge;
      if (ready.bridgeSessionId !== bridgeSessionId || bridge?.bridgeSessionId !== bridgeSessionId
        || bridge.kind !== 'local' || bridge.workspaceKey !== workspace.workspacePath || bridge.workspacePath !== workspace.workspacePath)
        throw new RelayFailure('workspace-mismatch');
      adapter = createAcknowledgedWebRemoteControlRelayProtocol({ bridgeSessionId,
        bridgeGeneration: bridge.bridgeGeneration, recoveryId: bridge.recoveryId,
        measureFrameBytes: payload => Buffer.byteLength(JSON.stringify({ type: 'data', payload, client_ts: Math.floor(Date.now() / 1000) })),
        sendFrame: app });
      client = new ChannelClient(adapter.protocol);
      adapter.signal.addEventListener('abort', () => client.dispose(), { once: true });
      adapter.onDegraded(() => stop('rpc-degraded'));
      for (const payload of earlyFrames.splice(0)) adapter.acceptPayload(payload);
      earlyBytes = 0;
      const services = Object.fromEntries(Object.entries({
        zcodeAgentService: ServiceChannels.ZCodeAgent, projectService: ServiceChannels.Project,
        zcodeTaskService: ServiceChannels.ZCodeTask, modelSelectionService: ServiceChannels.ModelSelection,
        oauthService: ServiceChannels.OAuth, credentialService: ServiceChannels.Credential,
        providerSettingsService: ServiceChannels.ProviderSettings, usageStatsService: ServiceChannels.UsageStats,
      }).map(([name, channel]) => [name, ProxyChannel.toService(client.getChannel(channel))]));
      // Handshake confirms the RPC transport rather than treating bridge-ready as
      // a writable session. No create, resume or model command is sent here.
      const hello = await ensureAgentV4ConnectionHandshake(services.zcodeAgentService);
      if (hello?.clientMode !== 'web-remote-replayable' || hello.protocolVersion !== 3 || !identifier(hello.connectionId))
        throw new RelayFailure('unsupported-conversation-protocol');
      if (stopped) return;
      clearTimeout(deadline); connected = true;
      const ownerEpoch = bootstrap.windowControlSessionId + ':' + hello.connectionId;
      state({ connected: true });
      resolveConnection({ services, bootstrap, device, ownerEpoch, bridge, get connected() { return connected; }, dispose: stop });
    } catch (error) { stop(error instanceof RelayFailure ? error.reason : 'desktop-rejected'); }
  }
  try {
    if (signal?.aborted) throw new RelayFailure('connection-cancelled');
    let attempt = 0;
    for (;;) {
      const devices = await http('/api/v1/remote/device');
      const matches = Array.isArray(devices) ? devices.filter(row => row?.device_sid === sid && row.device_mid === mid) : [];
      if (matches.length !== 1) throw new RelayFailure('device-not-owned');
      if (matches[0].online === true) {
        device = { sid, mid, name: typeof matches[0].name === 'string' ? matches[0].name : '' };
        break;
      }
      if (!waitForDevice) throw new RelayFailure('device-offline');
      // Only a just-launched native App gets this startup grace. No terminal
      // ticket or session command is issued until that exact device is online.
      await delay(Math.min(1000 * 2 ** attempt++, 8000), undefined, { signal: abort.signal });
    }
    const ticket = await http('/api/v1/remote/token', { role: 'terminal' });
    jwt = '';
    if (!/^[A-Za-z0-9_-]{21}[AQgw]$/.test(ticket?.token) || !Number.isInteger(ticket.expires_in) || ticket.expires_in <= 0)
      throw new RelayFailure('invalid-ticket');
    const url = new URL('/api/v1/remote/ws2', ORIGIN.replace('https:', 'wss:'));
    url.searchParams.set('token', ticket.token); url.searchParams.set('mid', mid);
    socket = createSocket(url.toString());
    socket.addEventListener('open', () => {
      if (stopped) return;
      ack(); beat();
      send({ type: 'auth_init', device_sid: sid, meta: { platform: 'web', version: 'cmb-test', name: 'Codex Mobile Bridge Test' } });
    });
    socket.addEventListener('error', () => stop('socket-failed'));
    socket.addEventListener('close', () => stop('socket-closed'));
    socket.addEventListener('message', event => {
      if (stopped) return;
      try {
        if (typeof event.data !== 'string' || Buffer.byteLength(event.data) > MAX_BYTES) throw new RelayFailure('invalid-frame');
        const message = JSON.parse(event.data);
        if (message.type === 'error') {
          stop(({ AUTH_FAILED: 'auth-required', KICKED: 'terminal-replaced', DEVICE_OFFLINE: 'device-offline' })[message.code] || 'relay-failed');
        } else if (message.type === 'auth_ack') {
          if (authenticated || message.device_sid !== sid || !identifier(message.terminal_sid)) throw new RelayFailure('device-mismatch');
          authenticated = true; terminalSid = message.terminal_sid; ack(); void paired(message.pair_status);
        } else if (message.type === 'pair_status_ack' && authenticated) {
          if (message.terminal_sid && message.terminal_sid !== terminalSid) throw new RelayFailure('terminal-mismatch');
          ack(); void paired(message.pair_status);
        } else if (message.type === 'data' && authenticated) {
          const payload = message.payload;
          if (payload?.zcode_type === 'rpc-frame' || payload?.zcode_type === 'rpc-frame-ack') {
            if (adapter) adapter.acceptPayload(payload);
            else if (openingBridgeId && payload.bridgeSessionId === openingBridgeId) {
              earlyBytes += Buffer.byteLength(event.data);
              if (earlyBytes > MAX_BYTES * 2) throw new RelayFailure('invalid-frame');
              earlyFrames.push(payload);
            }
            return;
          }
          const item = pending.get(payload?.requestId);
          if (!item) return;
          if (['app-error', 'workspace-bridge-error'].includes(payload.zcode_type)) throw new RelayFailure('desktop-rejected');
          if (payload.zcode_type !== item.responseType) return;
          pending.delete(payload.requestId); clearTimeout(item.timer); item.resolve(payload);
        }
      } catch (error) { stop(error instanceof RelayFailure ? error.reason : 'invalid-frame'); }
    });
    return await connection;
  } catch (error) {
    const safe = error instanceof RelayFailure ? error : new RelayFailure(stopped ? stopReason : 'http-failed');
    stop(safe.reason); throw safe;
  } finally { jwt = ''; signal?.removeEventListener('abort', cancelled); }
}
