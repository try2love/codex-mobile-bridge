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
  const account = async () => {
    const provider = await services.oauthService.getActiveProvider();
    const raw = provider ? await services.credentialService.load('oauth:' + provider + ':user_info') : null;
    const profile = raw ? JSON.parse(raw) : {};
    const subject = profile.id || profile.user_id || profile.sub || profile.customerNumber || profile.email;
    const digest = subject ? await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify([provider, subject]))) : null;
    return { identity: digest ? [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('') : '',
      provider, name: profile.name || profile.displayName || profile.email || 'ZCode', email: profile.email || '' };
  };
  const models = async () => {
    const view = await services.modelSelectionService.getView();
    return view.providers.flatMap(provider => provider.models.filter(model => model.config.enabled !== false).map(model => ({
      id: provider.providerId + '/' + model.modelId, name: model.modelId + ' · ' + provider.providerName,
      provider: provider.providerId, model: model.modelId,
      efforts: model.config.optionSpecs?.reasoningLevel?.values || [],
    })));
  };
  if (request.action === 'account') return { account: await account(), models: await models(), capabilities: { models: true } };
  if (request.action === 'lifecycle') {
    const tree = await services.projectService.readTree({ includeDeleted: true, includeDeletedTasks: true });
    const tasks = [...tree.tasks, ...tree.deletedTasks];
    // Refuse remote ownership and incomplete/large inventories. This check runs only
    // immediately before an explicit account transaction, never on a polling timer.
    if (tasks.length > 100 || tasks.some(task => task.hostId && task.hostId !== 'local'))
      return { complete: false, sessions: [] };
    const runtimes = await agent.collectLocalRuntimeChildProcesses();
    const expected = request.expectedRuntime;
    if (!expected || runtimes.length !== 1 || runtimes[0].pid !== expected.processId)
      return { complete: false, sessions: [] };
    const loaded = window[key]?.lease.store.getState();
    if (loaded?.status !== 'live' || loaded.snapshot.sessionId !== request.sessionId)
      return { complete: false, sessions: [] };
    const current = loaded.snapshot;
    if (current.queue.items.length || current.pendingInteractions.length || current.control.activeWorks.length ||
        current.pendingCommands?.length || current.backgroundWorks?.length)
      return { complete: true, sessions: [{ id: request.sessionId, status: 'active', runtimeKnown: true }] };
    const sessions = [];
    for (const workspacePath of new Set([request.workspace, ...tasks.map(task => task.workspacePath)])) {
      checkDeadline();
      const native = await agent.listSessions({ workspacePath, runtimePolicy: 'existing-only', limit: 201 });
      if (native.length > 200) return { complete: false, sessions };
      for (const row of native) {
        const value = await agent.readSession({ workspacePath, sessionId: row.sessionId, runtimePolicy: 'existing-only', messageLimit: 1 });
        const busy = value.runtime.pendingRequestIds.length || value.projection.status !== 'idle' ||
          value.projection.pendingPermissions.length || value.projection.activeToolCalls.length || value.projection.backgroundJobs.length;
        sessions.push({ id: row.sessionId, status: busy ? 'active' : 'idle', runtimeKnown: true });
      }
    }
    const again = await services.projectService.readTree({ includeDeleted: true, includeDeletedTasks: true });
    const stable = JSON.stringify(tree) === JSON.stringify(again);
    const still = await agent.collectLocalRuntimeChildProcesses();
    return { complete: stable && still.length === 1 && still[0].pid === expected.processId &&
      tasks.every(task => sessions.some(row => row.id === task.taskId)) &&
      sessions.every(row => row.id === request.sessionId), sessions };
  }
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
  if (request.action === 'detail') return { binding: current, snapshot: before, account: await account() };
  const body = request.body || {};
  const skills = async () => {
    const catalog = await agent.getSkillReferenceCatalog({ ...target, runtimePolicy: 'existing-only' });
    if (catalog.authority !== 'session') throw Error('ZCode 会话 Skill 目录尚未就绪');
    return catalog.skills.filter(skill => skill.enabled).map(skill => ({
      id: skill.id, name: skill.name, description: skill.description, path: skill.path, selectable: true,
    }));
  };
  if (request.action === 'catalog') {
    const value = { capabilities: { models: true, effort: true, skills: true },
      currentModel: before.config.provider + '/' + before.config.model, currentEffort: before.config.thought };
    if (body.section !== 'skills') value.models = await models();
    if (body.section !== 'models') value.skills = await skills();
    return value;
  }
  let type, payload;
  if (request.action === 'send') {
    // This first slice deliberately does not implement queue/steer or auto-start a cold owner.
    if (before.inputRouting?.mode !== 'startNow' || before.control.activeWorks.length ||
        before.pendingInteractions.length || before.queue.items.length)
      throw Error('Native session is busy or awaiting input');
    type = 'sendText'; payload = { text: body.text, requestedDelivery: 'startNow' };
    if (body.plugins?.length) {
      const catalog = await skills();
      const selected = body.plugins.map(id => catalog.find(skill => skill.id === id));
      if (selected.some(skill => !skill)) throw Error('所选 Skill 已变化，请重新选择');
      // Same native mention representation as ZCode's composer; no copied skill body.
      const label = text => text.replaceAll('\\', '\\\\').replaceAll('[', '\\[').replaceAll(']', '\\]');
      const destination = text => text.replaceAll('\\', '\\\\').replaceAll('>', '\\>');
      payload.text = selected.map(skill => '[' + label('$' + skill.name) + '](' + destination(skill.path) + ')').join('\n') + '\n\n' + body.text;
    }
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
  } else if (request.action === 'settings') {
    if (before.control.activeWorks.length || before.pendingInteractions.length || before.queue.items.length)
      throw Error('请等待当前任务结束后再切换模型');
    const row = (await models()).find(model => model.id === body.model);
    if (!row || (row.efforts.length && !row.efforts.includes(body.effort)))
      throw Error('请选择原 App 当前提供的模型和思考深度');
    type = 'switchModelConfig'; payload = { provider: row.provider, model: row.model, thought: body.effort || '' };
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
      ...(type === 'switchModelConfig' ? { baseRevision: before.revision, baseLogEpoch: before.logEpoch } : {}),
      type, payload, issuedAt: Date.now() } });
}
