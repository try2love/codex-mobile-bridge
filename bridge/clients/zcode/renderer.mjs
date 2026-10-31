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
        if (value?.layer?.acquire && value?.layer?.entries?.has('conversation/' + (request.anchorSessionId || request.sessionId))) contexts.add(value);
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
    const bound = window[key]?.sessions?.get(request.sessionId) || window[key];
    const loaded = bound?.lease.store.getState();
    if (loaded?.status !== 'live' || loaded.snapshot.sessionId !== request.sessionId)
      return { complete: false, sessions: [] };
    const current = loaded.snapshot;
    if (current.queue.items.length || current.pendingInteractions.length || current.control.activeWorks.length ||
        current.pendingCommands?.length || current.backgroundWorks?.length)
      return { complete: true, sessions: [{ id: request.sessionId, status: 'active', runtimeKnown: true }] };
    const sessions = [];
    for (const state of window[key]?.sessions?.values() || [bound]) {
      const live = state?.lease.store.getState();
      if (live?.status !== 'live' || JSON.stringify(state.binding.runtime) !== JSON.stringify(expected))
        return { complete: false, sessions };
      const snapshot = live.snapshot;
      const busy = snapshot.queue.items.length || snapshot.pendingInteractions.length || snapshot.control.activeWorks.length ||
        snapshot.pendingCommands?.length || snapshot.backgroundWorks?.length;
      sessions.push({ id: snapshot.sessionId, status: busy ? 'active' : 'idle', runtimeKnown: true });
    }
    for (const workspacePath of new Set([request.workspace, ...tasks.map(task => task.workspacePath)])) {
      checkDeadline();
      const native = await agent.listSessions({ workspacePath, runtimePolicy: 'existing-only', limit: 201 });
      if (native.length > 200) return { complete: false, sessions };
      for (const row of native) {
        const value = await agent.readSession({ workspacePath, sessionId: row.sessionId, runtimePolicy: 'existing-only', messageLimit: 1 });
        const busy = value.runtime.pendingRequestIds.length || value.projection.status !== 'idle' ||
          value.projection.pendingPermissions.length || value.projection.activeToolCalls.length || value.projection.backgroundJobs.length;
        const known = sessions.find(item => item.id === row.sessionId);
        if (known) { if (busy) known.status = 'active'; }
        else sessions.push({ id: row.sessionId, status: busy ? 'active' : 'idle', runtimeKnown: true });
      }
    }
    const again = await services.projectService.readTree({ includeDeleted: true, includeDeletedTasks: true });
    const stable = JSON.stringify(tree) === JSON.stringify(again);
    const still = await agent.collectLocalRuntimeChildProcesses();
    return { complete: stable && still.length === 1 && still[0].pid === expected.processId &&
      tasks.every(task => sessions.some(row => row.id === task.taskId)) &&
      sessions.every(row => {
        const state = window[key]?.sessions?.get(row.id) || (row.id === request.sessionId ? bound : null);
        const live = state?.lease.store.getState();
        if (live?.status !== 'live' || JSON.stringify(state.binding.runtime) !== JSON.stringify(expected)) return false;
        const snapshot = live.snapshot;
        if (snapshot.queue.items.length || snapshot.pendingInteractions.length || snapshot.control.activeWorks.length ||
            snapshot.pendingCommands?.length || snapshot.backgroundWorks?.length) row.status = 'active';
        return true;
      }), sessions };
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
  // Upgrade the original single-session lease without dropping the desktop's store.
  if (window[key] && !window[key].sessions) window[key].sessions = new Map([[window[key].sessionId, window[key]]]);
  const states = window[key]?.sessions || new Map();
  if (request.action === 'list') {
    const tree = await services.projectService.readTree({});
    return { sessions: tree.tasks.filter(row => row.workspacePath === request.workspace && (!row.hostId || row.hostId === 'local'))
      .map(row => {
        const state = states.get(row.taskId), value = state?.lease.store.getState();
        return value?.status === 'live' && same(state.binding, current) ? { snapshot: value.snapshot } :
          { id: row.taskId, title: row.title, cwd: row.workspacePath, model: row.model,
            effort: row.thoughtLevel, updatedAt: row.updatedAt, archived: row.archived };
      }) };
  }
  const read = () => agent.readSession({ ...target, runtimePolicy: 'existing-only', messageLimit: 100 });
  // Verify the original session/cwd before any subscription or command.
  const history = ['attach', 'history'].includes(request.action) || request.action === 'detail' &&
    (!states.has(request.sessionId) || !same(states.get(request.sessionId).binding, current)) ? await read() : null;
  if (history && (history.session?.sessionId !== request.sessionId || history.session?.workspace?.workspacePath !== request.workspace))
    throw Error('Native session workspace changed');
  let state = states.get(request.sessionId);
  if (request.action === 'attach') {
    if (state && (!same(state.binding, current) || state.lease.store.getState().status === 'closed')) {
      // Only this explicit action can adopt a new host generation. Old command IDs
      // remain claimed by DesktopSessions; replacing a subscription never resends them.
      state.lease.release(); states.delete(request.sessionId); state = null;
    }
    if (!state) {
      checkDeadline();
      // Reuse the App's ref-counted store, including its delta reducer. A second
      // subscribe on the renderer connection would evict the desktop subscription.
      const layer = contexts.length === 1 ? contexts[0].layer : window[key]?.layer;
      if (!layer || layer.disposed) throw Error('请先在 ZCode 打开此项目的一条聊天，再重新连接');
      const lease = layer.acquire(request.sessionId);
      state = { binding: current, sessionId: request.sessionId, lease, layer, sessions: states };
      states.set(request.sessionId, state); window[key] = state;
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
  if (request.action === 'detail' && (!state || !same(state.binding, current))) return { detached: true, history };
  if (!state || state.sessionId !== request.sessionId || !same(state.binding, current))
    throw Error('Prototype subscription is detached; explicitly attach again');
  if (request.action === 'detach') {
    state.lease.release();
    states.delete(request.sessionId);
    if (window[key] === state) { delete window[key]; const remaining = states.values().next().value; if (remaining) window[key] = remaining; }
    return { detached: true };
  }
  const projection = state.lease.store.getState();
  if (!projection.snapshot || projection.status !== 'live')
    throw Error('ZCode 原生会话连接尚未就绪，请在原 App 核对后重新连接');
  const before = projection.snapshot;
  if (before.sessionId !== request.sessionId) throw Error('Wrong native snapshot');
  if (request.action === 'create') {
    const row = (await models()).find(model => model.provider === before.config.provider && model.model === before.config.model);
    if (!row || !row.efforts.includes(before.config.thought)) throw Error('请先为当前原生会话选择有效模型和思考深度');
    if (!same(await identity(), current)) throw Error('ZCode owner changed before create');
    checkDeadline();
    // Match the native composer: firstInput persists the session and its input in one admission.
    // A bare draft is not a durable chat and cannot receive native goal/rename commands yet.
    const body = request.body;
    if (typeof body.firstMessage !== 'string' || !body.firstMessage.trim() || body.firstMessage.length > 100000)
      throw Error('请输入首条消息');
    const sid = 'sess_' + body.id;
    const ack = await agent.sendConversationCommandV4({ workspacePath: request.workspace, projectId: null,
      envelope: { commandId: body.id, clientId: current.clientId, sessionId: null, type: 'createSession',
        payload: { sessionId: sid, workspaceId: request.workspace,
          config: { provider: row.provider, model: row.model, thought: before.config.thought, mode: 'build' },
          firstInput: { text: body.firstMessage, modelSelection: { providerId: row.provider, modelId: row.model,
            options: { reasoningLevel: before.config.thought } }, mode: 'build', planEnabled: false } }, issuedAt: Date.now() } });
    if (ack.status !== 'accepted' || ack.result?.sessionId !== sid)
      return { status: ack.status === 'accepted' ? 'unknown' : ack.status, message: ack.message || '原生创建结果待核对，请查看 ZCode 会话列表；不会自动重试', nativeAck: ack };
    const layer = state.layer || (contexts.length === 1 ? contexts[0].layer : null);
    let notice;
    try {
      if (!layer || layer.disposed) throw Error('原生会话已创建，请点击继续会话完成接入');
      const lease = layer.acquire(sid);
      states.set(sid, { binding: current, sessionId: sid, lease, layer, sessions: states });
    } catch (error) { notice = String(error.message || error); }
    return { status: 'accepted', result: { id: sid }, ...(notice ? { message: notice } : {}) };
  }
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
  } else if (request.action === 'work-mode') {
    if (!['default', 'plan'].includes(body.mode)) throw Error('工作模式无效');
    const planning = before.config.planEnabled || before.config.mode === 'plan';
    if (planning === (body.mode === 'plan')) return { status: 'accepted' };
    if (before.control.activeWorks.length || before.pendingInteractions.length || before.queue.items.length)
      throw Error('请等待当前任务结束后再切换模式');
    type = 'switchCollaborationMode'; payload = { mode: body.mode === 'plan' ? 'plan' : 'build' };
  } else if (request.action === 'goal') {
    if (!['set', 'pause', 'resume', 'clear'].includes(body.operation)) throw Error('目标操作无效');
    if (body.operation !== 'set' && (!before.goal || body.expected?.goalId !== before.goal.targetId ||
        body.expected?.objective !== before.goal.objective)) throw Error('目标已变化，请刷新后操作');
    if (['set', 'resume'].includes(body.operation) && (before.control.activeWorks.length || before.pendingInteractions.length ||
        before.queue.items.length || before.config.planEnabled || before.config.mode === 'plan'))
      throw Error('请结束当前任务并退出计划模式后再开启目标');
    if (body.operation === 'set') {
      if (before.goal && before.goal.status !== 'verified') throw Error('请先关闭当前目标');
      type = 'sendGoalCommand'; payload = { text: body.objective };
    } else if (body.operation === 'clear') {
      // Use the App's structured native goal operation; never send a slash prompt.
      if (!same(await identity(), current)) throw Error('ZCode owner changed before dispatch');
      const latest = await read();
      if (latest.session.target?.targetId !== before.goal.targetId) throw Error('目标已变化，请刷新后操作');
      checkDeadline();
      await services.zcodeTaskService.goalSession({ taskId: request.sessionId, workspacePath: request.workspace,
        inputId: body.id, action: 'clear', expectedRevision: latest.runtime.stateRevision });
      return { status: 'accepted' };
    } else { type = body.operation === 'pause' ? 'pauseGoal' : 'resumeGoal'; payload = {}; }
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
      ...(['switchModelConfig', 'switchCollaborationMode', 'pauseGoal', 'resumeGoal'].includes(type) ? { baseRevision: before.revision, baseLogEpoch: before.logEpoch } : {}),
      type, payload, issuedAt: Date.now() } });
}
