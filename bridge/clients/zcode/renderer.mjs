// Development-only bridge to the existing renderer service. Never starts a CLI.
// Kept as a self-contained function so CDP can execute it without altering app.asar.
export async function nativeRequest(request) {
  const checkDeadline = () => {
    if (Date.now() >= request.expiresAt) throw Error('ZCode request expired; outcome may be unknown');
  };
  checkDeadline();
  const key = '__codexMobileZCodePrototypeV1';
  const findServices = () => {
    const stack = [...document.querySelectorAll('#root,#app')].flatMap(el =>
      Object.keys(el).filter(k => k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$')).map(k => el[k]));
    const seen = new Set(), found = new Set(), contexts = new Set();
    while (stack.length && seen.size < 20000) {
      const f = stack.pop();
      if (!f || seen.has(f)) continue;
      seen.add(f); stack.push(f.child, f.sibling, f.alternate);
      for (const value of [f.memoizedProps?.value, f.memoizedProps?.services]) {
        if (value?.layer?.acquire && value?.layer?.entries?.has('conversation/' + request.sessionId)) contexts.add(value);
        const services = value?.zcodeAgentService ? value : value?.services;
        if (services?.zcodeAgentService) found.add(services);
      }
    }
    if (found.size !== 1) throw Error('Original ZCode service accessor is unavailable or ambiguous');
    return { services: [...found][0], contexts: [...contexts] };
  };
  const { services, contexts } = findServices(), agent = services.zcodeAgentService;
  const target = { workspacePath: request.workspace, sessionId: request.sessionId };
  const identity = async () => {
    const hello = await agent.helloConversationV4();
    const clientId = localStorage.getItem('zcode-v4-client-id:v1');
    if (!clientId || hello.protocolVersion !== 3 || hello.clientMode !== 'desktop-continuous')
      throw Error('Unsupported original ZCode connection');
    const runtime = await agent.getWorkspaceRuntimeIdentity({ ...target, runtimePolicy: 'existing-only' });
    if (!runtime?.identity || runtime.workspaceKey !== request.workspace)
      throw Error('Original workspace runtime is unavailable');
    return { clientId, connectionId: hello.connectionId, runtime };
  };
  const current = await identity();
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  if (request.binding && !same(current, request.binding)) throw Error('ZCode owner changed; explicit reattach required');
  const read = () => agent.readSession({ ...target, runtimePolicy: 'existing-only', messageLimit: 100 });
  // Verify the original session/cwd before any subscription or command.
  const history = ['attach', 'history'].includes(request.action) ? await read() : null;
  if (history && (history.session?.sessionId !== request.sessionId || history.session?.workspace?.workspacePath !== request.workspace))
    throw Error('Native session workspace changed');
  let state = window[key];
  if (request.action === 'attach') {
    if (state && state.sessionId !== request.sessionId)
      throw Error('Detach the previous prototype session first');
    if (state && (!same(state.binding, current) || state.lease.store.getState().status === 'closed')) {
      // Only this explicit action can adopt a new host generation. Old command IDs
      // remain claimed by DesktopSessions; replacing a subscription never resends them.
      state.lease.release(); delete window[key]; state = null;
    }
    if (!state) {
      checkDeadline();
      // Reuse the App's ref-counted store, including its delta reducer. A second
      // subscribe on the renderer connection would evict the desktop subscription.
      if (contexts.length !== 1) throw Error('请先在 ZCode 打开这条测试聊天，再重新连接');
      const lease = contexts[0].layer.acquire(request.sessionId);
      state = window[key] = { binding: current, sessionId: request.sessionId, lease };
    }
    return { binding: current, subscriptionId: state.lease.store.getState().subscriptionId };
  }
  if (request.action === 'receipt') {
    return agent.queryConversationCommandsV4({ workspacePath: request.workspace,
      commands: [{ sessionId: request.sessionId, commandId: request.commandId }] });
  }
  if (request.action === 'history') {
    return { binding: current, session: history.session, runtime: history.runtime,
      projection: history.projection, messages: history.messages.filter(m =>
        m.info?.semantics?.uiVisibility === 'visible' ||
        (!m.info?.synthetic && m.info?.visibility !== 'model-only' && m.info?.semantics?.uiVisibility !== 'hidden')) };
  }
  if (!state || state.sessionId !== request.sessionId || !same(state.binding, current))
    throw Error('Prototype subscription is detached; explicitly attach again');
  if (request.action === 'detach') {
    state.lease.release();
    delete window[key];
    return { detached: true };
  }
  const projection = state.lease.store.getState();
  if (!projection.snapshot || projection.status !== 'live')
    throw Error('ZCode 原生会话连接尚未就绪，请在原 App 核对后重新连接');
  const before = projection.snapshot;
  if (before.sessionId !== request.sessionId) throw Error('Wrong native snapshot');
  if (request.action === 'detail') return { binding: current, snapshot: before };
  const body = request.body || {};
  let type, payload;
  if (request.action === 'send') {
    // This first slice deliberately does not implement queue/steer or auto-start a cold owner.
    if (before.inputRouting?.mode !== 'startNow' || before.control.activeWorks.length ||
        before.pendingInteractions.length || before.queue.items.length)
      throw Error('Native session is busy or awaiting input');
    type = 'sendText'; payload = { text: body.text, requestedDelivery: 'startNow' };
    const attachments = [];
    for (const file of body.files || []) {
      checkDeadline();
      const uploadId = crypto.randomUUID();
      const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
      const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
        .map(b => b.toString(16).padStart(2, '0')).join('');
      const totalChunks = Math.ceil(bytes.length / (256 * 1024));
      try {
        await agent.attachmentBeginV4({ ...target, uploadId, fileName: file.name,
          mime: file.mime, totalBytes: bytes.length, totalChunks, checksum: 'sha256:' + digest });
        for (let index = 0; index < totalChunks; index++) {
          checkDeadline();
          const chunk = bytes.subarray(index * 256 * 1024, (index + 1) * 256 * 1024);
          let binary = '';
          for (let offset = 0; offset < chunk.length; offset += 8192)
            binary += String.fromCharCode(...chunk.subarray(offset, offset + 8192));
          await agent.attachmentChunkV4({ ...target, uploadId, chunkIndex: index, dataBase64: btoa(binary) });
        }
        checkDeadline();
        const committed = await agent.attachmentCommitV4({ ...target, uploadId });
        attachments.push({ ref: committed.ref, fileName: file.name, mime: file.mime, bytes: bytes.length });
      } catch (error) {
        // No message has been sent yet. Abort only this staging upload; never replay a send.
        await agent.attachmentAbortV4({ ...target, uploadId }).catch(() => {});
        throw error;
      }
    }
    if (attachments.length) payload.attachments = attachments;
  } else if (request.action === 'stop') {
    if (!body.executionId || !before.control.canStop ||
        !before.control.activeWorks.some(w => w.foregroundExecutionId === body.executionId))
      throw Error('Observed native execution has ended or changed');
    type = 'stop'; payload = { expectedForegroundExecutionId: body.executionId };
  } else if (request.action === 'respond') {
    const interaction = before.pendingInteractions.find(p => p.interactionId === body.interactionId);
    if (!interaction) throw Error('Native interaction has ended or changed');
    if (interaction.kind === 'permission' &&
        !interaction.payload.options.some(o => o.optionId === body.answer?.optionId))
      throw Error('Choose an offered native permission option');
    if (!['permission', 'userInput'].includes(interaction.kind)) throw Error('Unsupported native interaction');
    type = 'resolveInteraction'; payload = { interactionId: body.interactionId,
      answer: interaction.kind === 'permission' ? { optionId: body.answer.optionId } : body.answer };
  } else throw Error('Unsupported prototype operation');
  if (!same(await identity(), current)) throw Error('ZCode owner changed before dispatch');
  checkDeadline();
  return agent.sendConversationCommandV4({ workspacePath: request.workspace,
    envelope: { commandId: body.id, clientId: current.clientId, sessionId: request.sessionId,
      type, payload, issuedAt: Date.now() } });
}
