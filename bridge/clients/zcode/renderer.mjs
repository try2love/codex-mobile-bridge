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
    let registryAnchor;
    while (stack.length && seen.size < 20000) {
      const f = stack.pop();
      if (!f || seen.has(f)) continue;
      seen.add(f); stack.push(f.child, f.sibling, f.alternate);
      // The service provider is mounted on the home screen too. Its module holds
      // the same native workspace registry; no open conversation is required.
      if (!registryAnchor && f.memoizedProps?.services?.zcodeAgentService && typeof f.type === 'function') registryAnchor = f.type;
      for (const value of [f.memoizedProps?.value, f.memoizedProps?.services]) {
        if (value?.layer?.acquire && value?.layer?.entries?.has('conversation/' + (request.anchorSessionId || request.sessionId))) contexts.add(value);
        const services = value?.zcodeAgentService ? value : value?.services;
        if (services?.zcodeAgentService) found.add(services);
      }
    }
    if (found.size !== 1) throw Error('Original ZCode service accessor is unavailable or ambiguous');
    return { services: [...found][0], contexts: [...contexts], registryAnchor };
  };
  const { services, contexts, registryAnchor } = findServices(), agent = services.zcodeAgentService;
  if (request.action === 'application') {
    const hello = await agent.helloConversationV4();
    const tree = await services.projectService.readTree({});
    if (hello.protocolVersion !== 3 || hello.clientMode !== 'desktop-continuous' || !hello.connectionId ||
        !Array.isArray(tree.tasks) || typeof agent.sendConversationCommandV4 !== 'function')
      throw Error('ZCode 原 App 的会话服务尚未就绪');
    return { ready: true };
  }
  if (request.action === 'registry-probe') return typeof window.__codexMobileZCodeAcquireWorkspace === 'function' ||
    contexts.find(context => !context.layer.disposed)?.layer.acquire || registryAnchor;
  const targetTask = tree => {
    const rows = tree.tasks.filter(row => row.taskId === request.sessionId && !row.deleted &&
      !row.archived && (!row.hostId || row.hostId === 'local'));
    if (rows.length !== 1) throw Error('原 App 中找不到唯一的本机会话，请刷新列表');
    return rows[0];
  };
  if (['projects', 'target'].includes(request.action)) {
    const tree = await services.projectService.readTree({});
    if (request.action === 'target') {
      return { workspace: targetTask(tree).workspacePath };
    }
    return { projects: tree.projects.filter(row => !row.deleted && row.hostId === 'local' && row.roots.some(root => root.isPrimary))
      .map(row => ({ key: row.id, name: row.name, cwd: row.roots.find(root => root.isPrimary).rootPath, surface: 'desktop', host: 'local' })) };
  }
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
    const busySnapshot = snapshot => snapshot.queue.items.length || snapshot.pendingInteractions.length ||
      snapshot.control.activeWorks.length || snapshot.pendingCommands?.length || snapshot.backgroundWorks?.length;
    // A live lease can prove work even when the full runtime inventory is
    // unavailable (for example after restarting only the gateway).
    const leased = window[key]?.sessions ? [...window[key].sessions.values()] : window[key] ? [window[key]] : [];
    for (const state of leased) {
      const live = state.lease.store.getState();
      if (live?.status === 'live' && busySnapshot(live.snapshot))
        return { complete: false, sessions: [{ id: live.snapshot.sessionId, status: 'active', runtimeKnown: true }] };
    }
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
    if (busySnapshot(current))
      return { complete: true, sessions: [{ id: request.sessionId, status: 'active', runtimeKnown: true }] };
    const sessions = [];
    for (const state of window[key]?.sessions?.values() || [bound]) {
      const live = state?.lease.store.getState();
      if (live?.status !== 'live' || JSON.stringify(state.binding.runtime) !== JSON.stringify(expected))
        return { complete: false, sessions };
      const snapshot = live.snapshot;
      sessions.push({ id: snapshot.sessionId, status: busySnapshot(snapshot) ? 'active' : 'idle', runtimeKnown: true });
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
        if (busySnapshot(snapshot)) row.status = 'active';
        return true;
      }), sessions };
  }
  const target = { workspacePath: request.workspace, sessionId: request.sessionId };
  const read = () => agent.readSession({ ...target, runtimePolicy: 'existing-only', messageLimit: 100 });
  const identity = async (workspacePath = request.workspace, sessionId = request.sessionId) => {
    const hello = await agent.helloConversationV4();
    const clientId = localStorage.getItem('zcode-v4-client-id:v1');
    if (!clientId || hello.protocolVersion !== 3 || hello.clientMode !== 'desktop-continuous')
      throw Error('Unsupported original ZCode connection');
    const runtime = await agent.getWorkspaceRuntimeIdentity({ workspacePath, sessionId, runtimePolicy: 'existing-only' });
    if (!runtime?.identity || runtime.workspaceKey !== workspacePath)
      throw Error('Original workspace runtime is unavailable');
    return { clientId, connectionId: hello.connectionId, runtime };
  };
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  // Upgrade the original single-session lease without dropping the desktop's store.
  if (window[key] && !window[key].sessions) window[key].sessions = new Map([[window[key].sessionId, window[key]]]);
  const states = window[key]?.sessions || new Map();
  if (request.action === 'attach') {
    const tree = await services.projectService.readTree({});
    if (targetTask(tree).workspacePath !== request.workspace)
      throw Error('原 App 会话或项目已变化，请刷新列表');
    const previous = states.get(request.sessionId);
    const loaded = previous?.lease.store.getState();
    if (loaded?.status === 'live' && loaded.snapshot?.sessionId === request.sessionId) {
      const current = await identity();
      if (same(previous.binding, current)) return { binding: current, subscriptionId: loaded.subscriptionId };
    }
    checkDeadline();
    // Native acquire performs cold restore. Reading history before subscribing
    // fails on the home screen because the conversation is not resident yet.
    const workspaceLease = window.__codexMobileZCodeAcquireWorkspace?.({ workspacePath: request.workspace }, agent);
    const existing = contexts.find(context => context.layer.entries.has('conversation/' + request.sessionId));
    const layer = workspaceLease?.layer || existing?.layer ||
      (window[key]?.binding?.runtime?.workspaceKey === request.workspace ? window[key].layer : null);
    let lease;
    try {
      if (!layer || layer.disposed) throw Error('ZCode 原生连接入口不可用，请在应用管理中恢复接入');
      lease = layer.acquire(request.sessionId);
      const store = lease.store;
      if (store.getState().status === 'error') void store.retry();
      if (store.getState().status !== 'live') await new Promise((resolve, reject) => {
        const finish = error => { clearTimeout(timer); off(); error ? reject(error) : resolve(); };
        const inspect = () => {
          const value = store.getState();
          if (value.status === 'live') finish();
          else if (['error','closed'].includes(value.status)) finish(Error(value.lastError || 'ZCode 原生聊天恢复失败'));
        };
        const timer = setTimeout(() => finish(Error('ZCode 原生聊天恢复超时，请重新连接')), Math.max(1, request.expiresAt - Date.now() - 250));
        const off = store.subscribe(inspect);
        inspect();
      });
      checkDeadline();
      const current = await identity();
      // V4's cold history projection need not create a legacy session/read runtime.
      // The native task index and runtime identity above bind this snapshot's cwd.
      if (store.getState().snapshot?.sessionId !== request.sessionId) throw Error('Native session workspace changed');
      // Swap only this Bridge reference, retaining the App's shared native store.
      previous?.lease.release(); previous?.workspaceLease?.release();
      const state = {binding:current,sessionId:request.sessionId,lease,layer,workspaceLease,sessions:states};
      states.set(request.sessionId,state); window[key]=state;
      return {binding:current,subscriptionId:store.getState().subscriptionId};
    } catch (error) { lease?.release(); workspaceLease?.release(); throw error; }
  }
  const current = await identity();
  if (request.binding && !same(current, request.binding)) throw Error('ZCode owner changed; explicit reattach required');
  if (request.action === 'list') {
    const tree = await services.projectService.readTree({});
    return { sessions: tree.tasks.filter(row => !row.hostId || row.hostId === 'local')
      .map(row => {
        const state = states.get(row.taskId), value = state?.lease.store.getState();
        return value?.status === 'live' && state.binding.clientId === current.clientId &&
          state.binding.connectionId === current.connectionId ? { snapshot: value.snapshot, cwd: row.workspacePath } :
          { id: row.taskId, title: row.title, cwd: row.workspacePath, model: row.model,
            effort: row.thoughtLevel, updatedAt: row.updatedAt, archived: row.archived };
      }) };
  }
  const history = request.action === 'history' || request.action === 'detail' &&
    (!states.has(request.sessionId) || !same(states.get(request.sessionId).binding, current)) ? await read() : null;
  if (history && (history.session?.sessionId !== request.sessionId || history.session?.workspace?.workspacePath !== request.workspace))
    throw Error('Native session workspace changed');
  const state = states.get(request.sessionId);
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
    state.workspaceLease?.release();
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
    let workspacePath = request.workspace, projectId = null;
    if (body.directoryProject || body.projectKey && body.projectKey !== request.workspace) {
      if (typeof window.__codexMobileZCodeAcquireWorkspace !== 'function') throw Error('ZCode 原生多项目连接尚不可用');
      const project = body.directoryProject ? await services.projectService.saveProject({ hostId: 'local',
        roots: [body.directoryProject.cwd], reuseExistingRoot: true }) :
        (await services.projectService.readProjects({ hostId: 'local' })).find(row => row.id === body.projectKey && !row.deleted);
      if (!project || project.hostId !== 'local') throw Error('原生项目已变化，请重新选择');
      const prepared = await services.projectService.prepareSession({ projectId: project.id, hostId: 'local' });
      const expected = body.directoryProject?.cwd || project.roots.find(root => root.isPrimary)?.rootPath;
      if (prepared.workspacePath !== expected || prepared.projectId !== project.id || prepared.workspaceIdentity)
        throw Error('原生项目目录已变化，请重新选择');
      workspacePath = prepared.workspacePath; projectId = prepared.projectId;
    }
    if (!same(await identity(), current)) throw Error('ZCode owner changed before create');
    checkDeadline();
    const ack = await agent.sendConversationCommandV4({ workspacePath, projectId,
      envelope: { commandId: body.id, clientId: current.clientId, sessionId: null, type: 'createSession',
        payload: { sessionId: sid, workspaceId: workspacePath,
          config: { provider: row.provider, model: row.model, thought: before.config.thought, mode: 'build' },
          firstInput: { text: body.firstMessage, modelSelection: { providerId: row.provider, modelId: row.model,
            options: { reasoningLevel: before.config.thought } }, mode: 'build', planEnabled: false } }, issuedAt: Date.now() } });
    if (ack.status !== 'accepted' || ack.result?.sessionId !== sid)
      return { status: ack.status === 'accepted' ? 'unknown' : ack.status, message: ack.message || '原生创建结果待核对，请查看 ZCode 会话列表；不会自动重试', nativeAck: ack };
    let notice, binding, workspaceLease;
    try {
      binding = await identity(workspacePath, sid);
      workspaceLease = window.__codexMobileZCodeAcquireWorkspace?.({ workspacePath }, agent);
      const layer = workspaceLease?.layer || (workspacePath === request.workspace ? state.layer : null);
      if (!layer || layer.disposed) throw Error('原生会话已创建，请点击继续会话完成接入');
      const lease = layer.acquire(sid);
      states.set(sid, { binding, sessionId: sid, lease, layer, workspaceLease, sessions: states });
    } catch (error) { workspaceLease?.release(); notice = String(error.message || error); }
    return { status: 'accepted', result: { id: sid }, workspace: workspacePath, binding,
      ...(notice ? { message: notice } : {}) };
  }
  if (request.action === 'detail') return { binding: current, snapshot: before, account: await account() };
  if (request.action === 'tasks') return { tasks: (before.backgroundWorks || []).map(work => ({
    id: work.workId, title: work.title, kind: work.kind, status: work.status,
    cancellable: work.cancellable === true && work.status === 'running',
  })) };
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
    const mode = body.mode || 'send', routing = before.inputRouting?.mode;
    const allowed = { send: routing === 'startNow' && !before.control.activeWorks.length && !before.queue.items.length,
      queue: ['startNow', 'enqueue', 'guide', 'choice'].includes(routing),
      steer: before.control.activeWorks.length > 0 && ['enqueue', 'guide'].includes(routing) };
    if (!allowed[mode] || before.pendingInteractions.length)
      throw Error('Native session is busy or awaiting input');
    type = 'sendText'; payload = { text: body.text, requestedDelivery: { send: 'startNow', queue: 'queue', steer: 'guide' }[mode] };
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
  } else if (request.action === 'rename') {
    if (typeof body.title !== 'string' || !body.title.trim() || body.title.trim().length > 120) throw Error('请输入 1–120 字的聊天名称');
    type = 'renameSession'; payload = { title: body.title.trim() };
  } else if (request.action === 'fork' || request.action === 'edit-message') {
    const edit = request.action === 'edit-message', target = body.target;
    const row = before.rows.window.find(row => row.rowId === target?.rowId && row.entityId === target?.entityId);
    if (target?.logEpoch !== before.logEpoch || !row || row.kind !== (edit ? 'userInput' : 'assistantText') ||
        row.actions?.[edit ? 'canEdit' : 'canFork'] !== true) throw Error('消息已变化或不支持此操作，请刷新后重新选择');
    if (edit && (typeof body.text !== 'string' || !body.text.trim() || body.text.length > 100000)) throw Error('请输入 1–100000 字的消息');
    type = edit ? 'editUserQuery' : 'forkAssistant';
    payload = { target: { rowId: row.rowId, entityId: row.entityId }, ...(edit ? {
      newText: body.text, workspaceMode: 'preserve', ...(row.attachments?.length ? { attachments: row.attachments } : {}),
    } : {}) };
  } else if (request.action === 'compact') {
    if (before.availability?.compact?.allowed !== true || before.control.activeWorks.length ||
        before.pendingInteractions.length || before.queue.items.length) throw Error('请等待当前任务和队列结束后再压缩上下文');
    type = 'compact'; payload = {};
  } else if (request.action === 'stop-task') {
    const work = before.backgroundWorks?.find(work => work.workId === body.taskId);
    if (!work || work.status !== 'running' || work.cancellable !== true) throw Error('后台任务已结束或不支持独立停止');
    type = 'cancelBackgroundWork'; payload = { workId: work.workId };
  } else if (request.action === 'access') {
    if (!['build','guarded','yolo'].includes(before.config.mode) || !['build','guarded','yolo'].includes(body.mode) ||
        (body.mode === 'yolo' && body.confirmed !== true)) throw Error('请确认本次权限切换');
    if (before.config.mode === body.mode) return { status: 'accepted' };
    if (before.control.activeWorks.length || before.pendingInteractions.length || before.queue.items.length || before.config.planEnabled)
      throw Error('请先结束当前任务并退出计划模式');
    type = 'switchCollaborationMode'; payload = { mode: body.mode };
  } else if (request.action === 'work-mode') {
    if (!['default', 'plan'].includes(body.mode)) throw Error('工作模式无效');
    const planning = before.config.planEnabled || before.config.mode === 'plan';
    if (planning === (body.mode === 'plan')) return { status: 'accepted' };
    if (before.control.activeWorks.length || before.pendingInteractions.length || before.queue.items.length)
      throw Error('请等待当前任务结束后再切换模式');
    type = 'switchCollaborationMode';
    payload = { mode: body.mode === 'plan' ? 'plan' : (['guarded','yolo'].includes(before.config.mode) ? before.config.mode : 'build') };
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
  const ack = await agent.sendConversationCommandV4({ workspacePath: request.workspace,
    envelope: { commandId: body.id, clientId: current.clientId, sessionId: request.sessionId,
      ...(['switchModelConfig', 'switchCollaborationMode', 'pauseGoal', 'resumeGoal', 'forkAssistant', 'editUserQuery'].includes(type) ? { baseRevision: before.revision, baseLogEpoch: before.logEpoch } : {}),
      type, payload, issuedAt: Date.now() } });
  if (type === 'forkAssistant' && ack.status === 'accepted' && ack.result?.sessionId) {
    const sid = ack.result.sessionId;
    // Explicit fork adopts only its returned child, through the existing native layer.
    const workspaceLease = window.__codexMobileZCodeAcquireWorkspace?.({ workspacePath: request.workspace }, agent);
    try {
      const layer = workspaceLease?.layer || state.layer, lease = layer.acquire(sid);
      states.set(sid, { binding: current, sessionId: sid, lease, layer, workspaceLease, sessions: states });
    } catch { workspaceLease?.release(); } // Fork remains accepted; explicit resume can restore its view.
  }
  return ack;
}
