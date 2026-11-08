import { connectProtocol } from './protocol.mjs';
import { SessionDataLayer, createAgentConversationTransport, getV4ClientId } from './vendor/zcode-official.mjs';

export async function createRelay(options, dependencies) {
  const layers = new Map();
  const state = {};
  let disposed = false;
  const protocol = await connectProtocol({ ...options, onState(value) {
    if (value.connected === false) clearLayers();
    options.onState?.(value);
  } }, dependencies);
  function clearLayers() {
    disposed = true;
    for (const row of layers.values()) { clearTimeout(row.timer); row.layer.dispose(); }
    layers.clear();
  }
  const values = new Map([['zcode-v4-client-id:v1', getV4ClientId()]]);
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)) };
  function acquireWorkspace(scope, agent) {
    if (disposed || !protocol.connected) throw Error('ZCode official relay is disconnected');
    if (agent !== protocol.services.zcodeAgentService || !scope?.workspacePath || scope.workspaceIdentity || scope.remoteSessionId)
      throw Error('ZCode official relay workspace identity mismatch');
    const key = scope.workspacePath;
    let row = layers.get(key);
    if (!row) {
      const transport = createAgentConversationTransport(agent, { workspacePath: key });
      row = { transport, layer: new SessionDataLayer({ transport }), count: 0, timer: null };
      layers.set(key, row);
    }
    clearTimeout(row.timer); row.timer = null; row.count++;
    let released = false;
    return { layer: row.layer, release() {
      if (released || disposed) return;
      released = true; row.count--;
      if (row.count === 0) row.timer = setTimeout(() => {
        if (row.count || layers.get(key) !== row) return;
        layers.delete(key); row.layer.dispose();
      }, 30000);
    } };
  }
  return { ...protocol, state, storage, contexts: [], clientMode: 'web-remote-replayable', acquireWorkspace,
    get connected() { return protocol.connected; },
    dispose() { clearLayers(); values.clear(); protocol.dispose(); },
  };
}
