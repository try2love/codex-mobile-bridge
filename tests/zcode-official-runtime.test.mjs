import test from 'node:test';
import assert from 'node:assert/strict';
import { createRelay } from '../desktop/features/zcode-official/runtime.mjs';
import { ChannelServer, ProxyChannel, ServiceChannels, Emitter,
  createAcknowledgedWebRemoteControlRelayProtocol } from '../desktop/features/zcode-official/vendor/zcode-official.mjs';

const link = 'https://zcode.z.ai/remote/v4?sid=fixture-device&mid=fixture-machine';
const hello = { kind: 'hello', protocolVersion: 3, connectionId: 'fixture-connection',
  clientMode: 'web-remote-replayable', deliveryProfile: 'replayable', serverTime: Date.now(),
  capabilities: { nativeDialogs: false, localTerminal: false, binaryFrames: false, compression: 'none' }, auth: {} };

function fixture({ owned = true, wrongBridge = false, large = false, incompatible = false } = {}) {
  let socket, server, relay, socketCount = 0, clientHello;
  const requests = [], commands = [];
  const events = new Emitter();
  const emit = data => socket.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(data) }));
  const data = payload => emit({ type: 'data', payload });
  const dependencies = {
    random: () => 0,
    fetcher: async (url, options) => {
      assert.equal(options.redirect, 'error');
      assert.equal(new URL(url).origin, 'https://zcode.z.ai');
      const body = url.endsWith('/device') ? [{ device_sid: owned ? 'fixture-device' : 'someone-else', device_mid: 'fixture-machine', online: true }]
        : { token: 'AAAAAAAAAAAAAAAAAAAAAA', expires_in: 60 };
      return new Response(JSON.stringify({ code: 0, msg: 'ok', data: body }));
    },
    createSocket: () => {
      socketCount++;
      socket = new EventTarget(); socket.readyState = 0;
      socket.close = () => { socket.readyState = 3; queueMicrotask(() => socket.dispatchEvent(new Event('close'))); };
      socket.send = text => {
        const message = JSON.parse(text);
        if (message.type === 'auth_init') {
          queueMicrotask(() => emit({ type: 'auth_ack', device_sid: 'fixture-device', terminal_sid: 'fixture-terminal', pair_status: 'matched' }));
          return;
        }
        if (message.type === 'pair_status_query') {
          queueMicrotask(() => emit({ type: 'pair_status_ack', terminal_sid: 'fixture-terminal', pair_status: 'matched' })); return;
        }
        const p = message.payload;
        if (p.zcode_type.startsWith('rpc-frame')) { relay.acceptPayload(p); return; }
        requests.push(p);
        if (p.zcode_type === 'bootstrap-request') queueMicrotask(() => data({ zcode_type: 'bootstrap-response', requestId: p.requestId,
          success: true, result: { windowControlSessionId: 'fixture-window', desktopAppVersion: '3.17.2', workspaces: [] } }));
        else if (p.zcode_type === 'workspace-list-request') queueMicrotask(() => data({ zcode_type: 'workspace-list-response', requestId: p.requestId,
          success: true, result: { workspaces: [{ workspacePath: '/fixture', kind: 'local', label: 'Fixture' }] } }));
        else if (p.zcode_type === 'workspace-bridge-open') {
          queueMicrotask(() => {
            relay = createAcknowledgedWebRemoteControlRelayProtocol({ bridgeSessionId: p.bridgeSessionId,
              sendFrame: payload => { queueMicrotask(() => data(payload)); return true; } });
            // Initialize is intentionally sent before bridge-ready: real transport
            // ordering must not lose it while the control promise is settling.
            server = new ChannelServer(relay.protocol, undefined);
            server.registerChannel(ServiceChannels.ZCodeAgent, ProxyChannel.fromService({
              helloConversationV4: async () => ({ ...hello, ...(incompatible ? { clientMode: 'desktop-continuous' } : {}) }),
              initializeConversationV4: async input => { clientHello = input; return { ok: true }; },
              onAgentRuntimeRestarted: events.event, onAgentRuntimeLifecycle: events.event,
              onDynamicConversationFrame: () => events.event,
              onDynamicLocalTtftFacts: () => events.event,
              sendConversationCommandV4: async value => { commands.push(value); return new Promise(() => {}); },
            }));
            server.registerChannel(ServiceChannels.Project, ProxyChannel.fromService({ readTree: async () => ({ tasks: [], fixture: large ? 'x'.repeat(1400000) : 'ok' }) }));
            queueMicrotask(() => data({ zcode_type: 'workspace-bridge-ready', requestId: p.requestId, bridgeSessionId: p.bridgeSessionId,
              bridge: { bridgeSessionId: p.bridgeSessionId, kind: 'local', workspaceKey: wrongBridge ? '/other' : '/fixture', workspacePath: '/fixture' } }));
          });
        }
      };
      queueMicrotask(() => { socket.readyState = 1; socket.dispatchEvent(new Event('open')); });
      return socket;
    },
  };
  return { dependencies, requests, commands, emit, get clientHello() { return clientHello; }, get socketCount() { return socketCount; },
    close() { server?.dispose(); relay?.dispose(); events.dispose(); socket?.close(); } };
}

test('official RPC bridge validates owner, initializes shared client ID and reads fragmented replies without activating a session', async t => {
  const f = fixture({ large: true }); t.after(() => f.close());
  const runtime = await createRelay({ jwt: 'fixture-jwt', link, timeoutMs: 2000 }, f.dependencies);
  t.after(() => runtime.dispose());
  assert.equal(runtime.ownerEpoch, 'fixture-window:fixture-connection');
  assert.equal(runtime.device.mid, 'fixture-machine');
  assert.equal(runtime.storage.getItem('zcode-v4-client-id:v1'), f.clientHello.clientId);
  assert.equal(f.clientHello.clientKind, 'web');
  assert.equal((await runtime.services.projectService.readTree({})).fixture.length, 1400000);
  assert.deepEqual(f.requests.map(p => p.zcode_type), ['bootstrap-request', 'workspace-list-request', 'workspace-bridge-open']);
  assert.equal('taskId' in f.requests[2], false);
  assert.equal('project' in f.requests[2], false);
  assert.equal(f.commands.length, 0);
  const first = runtime.acquireWorkspace({ workspacePath: '/fixture' }, runtime.services.zcodeAgentService);
  const second = runtime.acquireWorkspace({ workspacePath: '/fixture' }, runtime.services.zcodeAgentService);
  assert.equal(first.layer, second.layer);
  first.release(); second.release(); runtime.dispose();
  assert.equal(first.layer.disposed, true);
  assert.equal(runtime.connected, false);
});

test('KICKED terminates pending writes and never reconnects or replays them', async t => {
  const f = fixture(); t.after(() => f.close()); const states = [];
  const runtime = await createRelay({ jwt: 'fixture-jwt', link, timeoutMs: 2000, onState: value => states.push(value) }, f.dependencies);
  t.after(() => runtime.dispose());
  const pending = runtime.services.zcodeAgentService.sendConversationCommandV4({ marker: 'only-once' });
  const rejected = assert.rejects(pending, /disposed/);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.commands.length, 1);
  f.emit({ type: 'error', code: 'KICKED', message: 'never expose fixture-jwt' });
  await rejected;
  assert.equal(runtime.connected, false);
  assert.equal(f.socketCount, 1);
  assert.equal(f.commands.length, 1);
  assert.deepEqual(states.at(-1), { connected: false, reason: 'terminal-replaced' });
});

test('foreign devices and workspace mismatches never become native services', async t => {
  for (const options of [{ owned: false }, { wrongBridge: true }, { incompatible: true }]) {
    const f = fixture(options);
    try { await assert.rejects(createRelay({ jwt: 'fixture-jwt', link, timeoutMs: 2000 }, f.dependencies), /device-not-owned|workspace-mismatch|desktop-rejected/); }
    finally { f.close(); }
    assert.equal(f.commands.length, 0);
    if (options.owned === false) assert.equal(f.socketCount, 0);
  }
});
