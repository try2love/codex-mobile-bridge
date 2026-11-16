// Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
/* Injected into the Claude renderer through the verified DevTools Console. Uses the existing
 * workspace file APIs and session APIs; never changes CSP. Workspace trust is written
 * only by the explicitly confirmed mobileProjectTrust operation.
 * Runtime files must remain local. Reloading the renderer removes this bridge.
 */
(async () => {
  const config = __BRIDGE_CONFIG__;
  const api = window['claude.web'];
  if (!api?.LocalSessions?.getAll || (config.transport !== 'cdp' && (!api.LocalSessions.readFileAtCwd || !api.LocalSessions.writeSessionFile))) {
    throw new Error('当前 Claude Desktop 不提供所需的本地文件接口');
  }
  const normalizedWorkspace = cwd => {
    const value = cwd.replaceAll('\\', '/').replace(/\/$/, '');
    return /^[A-Za-z]:\//.test(value) ? value.toLowerCase() : value;
  };
  const sameWorkspace = cwd => typeof cwd === 'string' &&
    normalizedWorkspace(cwd) === normalizedWorkspace(config.cwd);
  function ownerError(code, message) {
    window.__codingRemoteInjectionError = message;
    console.error('Error: BRIDGE_OWNER_' + code + ' ' + message + ' [' +
      (config.inspectionMarker || config.generation) + ']');
    throw new Error(message);
  }
  const existing = window.__claudeMobileBridge;
  let sameOwner = false, canHandoff = false, safeLegacyStatus = false;
  if (existing) {
    let state;
    try {
      state = existing.status();
      // Early file connectors cleared busy at their reply deadline while the
      // Desktop operation could still be running. Their status cannot fence it.
      safeLegacyStatus = state?.transport === 'file' ?
        typeof state.suspended === 'boolean' && typeof state.writing === 'boolean' :
        state?.transport === 'cdp' && typeof state.busy === 'boolean';
      canHandoff = state?.transport === 'file' &&
        (state.connectorRevision === config.connectorRevision || state.connectorRevision === 14 && config.connectorRevision === 15 ||
          [14,15].includes(state.connectorRevision) && config.connectorRevision === 16) && typeof existing.handoff === 'function';
      if (typeof existing.owns === 'function') sameOwner = existing.owns(config.cwd) === true;
      else if (state?.transport === 'file' && state.sessionId) {
        // Legacy connectors expose only a session ID. Check Desktop's live
        // session list before deciding whether that renderer is ours.
        const rows = await api.LocalSessions.getAll();
        const sessions = Array.isArray(rows) ? rows : rows?.sessions;
        const session = sessions?.find(row => row.sessionId === state.sessionId);
        sameOwner = sameWorkspace(session?.cwd);
      }
    } catch { /* Unknown ownership is occupied, never permission to replace. */ }
    if (!state || state.busy && !state.handoffPending || state.writing ||
        sameOwner && !safeLegacyStatus ||
        !sameOwner && state.suspended !== true && !canHandoff) {
      const message = state?.busy && !state.handoffPending || state?.writing ?
        'Claude 桥接仍有请求进行中，请稍后重试连接' :
        '旧版 Claude 桥接无法核验在途请求；请确认当前任务结束后重新加载或退出重开 Claude Desktop，再初始化新网关（只需迁移一次）';
      ownerError(state?.busy && !state.handoffPending || state?.writing ? 'BUSY' : 'LEGACY', message);
    }
  }
  async function replaceExisting() {
    if (!existing) return;
    let state;
    try { state = existing.status(); } catch {
      ownerError('UNCONFIRMED', 'Claude 桥接状态已变化，请刷新后重试连接');
    }
    if (!sameOwner && state?.suspended !== true) {
      let released = false;
      try { released = canHandoff && await existing.handoff(config.cwd); } catch {}
      if (!released)
        ownerError('UNCONFIRMED', '旧网关仍有未确认请求或未完成接替，请在旧网关检查结果后重试');
    }
    try { state = existing.status(); } catch {
      ownerError('UNCONFIRMED', 'Claude 桥接状态已变化，请刷新后重试连接');
    }
    if (!state || window.__claudeMobileBridge !== existing || state.busy || state.writing ||
        !sameOwner && state.suspended !== true)
      ownerError('UNCONFIRMED', 'Claude 桥接状态已变化，请刷新后重试连接');
    try { existing.stop(); } catch {
      ownerError('UNCONFIRMED', 'Claude 桥接状态已变化，请刷新后重试连接');
    }
  }
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(config.token),
    {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
  const hex = buffer => [...new Uint8Array(buffer)].map(v => v.toString(16).padStart(2, '0')).join('');
  async function pack(value) {
    const payload = JSON.stringify(value);
    return JSON.stringify({payload, signature: hex(await crypto.subtle.sign('HMAC', key, encoder.encode(payload)))});
  }
  async function read(name = config.request) {
    const file = await api.LocalSessions.readFileAtCwd(config.cwd, name);
    if (!file) throw new Error('无法读取桥接请求文件');
    const envelope = JSON.parse(file.contents);
    if (typeof envelope.signature !== 'string' || !/^[a-f0-9]{64}$/.test(envelope.signature)) return null;
    const signature = new Uint8Array(envelope.signature.match(/../g).map(v => parseInt(v, 16)));
    if (!await crypto.subtle.verify('HMAC', key, signature, encoder.encode(envelope.payload))) return null;
    const value = JSON.parse(envelope.payload);
    return value.generation === config.generation || config.reconnect ? value : null;
  }
  const surfaces = {code: 'LocalSessions', cowork: 'LocalAgentModeSessions'};
  const allowed = new Set(['getAll', 'getSession', 'getTranscript', 'sendMessage',
    'interrupt', 'stop', 'respondToToolPermission', 'start', 'getPermissionMode', 'archive', 'unarchive', 'loadArchivedSessions', 'delete', 'updateSession',
    'setPermissionMode', 'setModel', 'setEffort', 'getDefaultEffort', 'getSupportedCommands', 'setThinkingSummariesWanted', 'getContextUsageSummary', 'getContextUsage',
    'listSessionDirectory', 'readSessionFile', 'readSessionImageAsDataUrl', 'readSessionPanelDocumentAsDataUrl']);
  const capabilities = Object.fromEntries(Object.entries(surfaces).map(([kind, name]) =>
    [kind, [...allowed].filter(method => typeof api[name]?.[method] === 'function')]));
  // Read model choices already offered by Desktop. Do not call its model-list setter.
  const offeredModels = new Map();
  const knownSessions = new Map();
  const bounded = (promise, ms, message) => new Promise((resolve, reject) => {
    let settled=false;
    const timer=setTimeout(()=>{if(!settled){settled=true;reject(message instanceof Error ? message : new Error(message));}},ms);
    Promise.resolve(promise).then(value=>{if(!settled){settled=true;clearTimeout(timer);resolve(value);}},error=>{if(!settled){settled=true;clearTimeout(timer);reject(error);}});
  });
  const restore = [];
  for (const [kind, surface] of Object.entries(surfaces)) {
    capabilities[kind].push('mobileCatalog', 'mobileProgress', 'mobileAccount');
    if(['getSession','getTranscript'].every(method=>typeof api[surface]?.[method]==='function'))capabilities[kind].push('mobileImage');
    if(kind==='code')capabilities[kind].push('mobileList');
    if(kind==='code'&&['checkTrust','saveTrust'].every(method=>typeof api[surface]?.[method]==='function'))capabilities[kind].push('mobileProjectTrust');
    if(kind==='code'&&['getAll','getSession','archive','unarchive'].every(method=>typeof api[surface]?.[method]==='function'))capabilities[kind].push('mobileArchiveState','mobileArchive');
    if(typeof api[surface]?.getSession==='function'&&typeof api[surface]?.getTranscript==='function')capabilities[kind].push('mobileDetail');
    if(kind==='code'&&typeof api[surface]?.getTranscriptTail==='function'&&typeof api[surface]?.getTranscriptPage==='function')capabilities[kind].push('mobileHistory');
    if(kind==='code'&&typeof api[surface]?.forkSession==='function'&&typeof api[surface]?.getTranscript==='function')capabilities[kind].push('mobileFork');
    if(['getSession','updateSession'].every(method=>typeof api[surface]?.[method]==='function'))capabilities[kind].push('mobileRename');
  }
  function installObservers() {
    for (const surface of Object.values(surfaces)) {
      const target = api[surface], original = target?.setAvailableCodeModels;
      if (typeof original === 'function') {
        const observer = function (models, ...rest) {
          if (Array.isArray(models)) offeredModels.set(target, models);
          return original.call(target, models, ...rest);
        };
        try { target.setAvailableCodeModels = observer; restore.push(() => { if (target.setAvailableCodeModels === observer) target.setAvailableCodeModels = original; }); } catch {}
      }
    }
  }
  async function activate(bridge) {
    await replaceExisting();
    // A second injector may have completed the same asynchronous handoff.
    if (window.__claudeMobileBridge !== existing)
      ownerError('UNCONFIRMED', 'Claude 桥接状态已变化，请刷新后重试连接');
    try {
      installObservers();
      window.__claudeMobileBridge = bridge;
      delete window.__codingRemoteInjectionError;
    } catch (error) {
      for (const undo of restore) undo();
      throw error;
    }
  }
  async function mobileCatalog(target, kind, sessionId, includeSkills=true) {
    let choices, source='session-history';
    const store=window['claude.settings']?.Custom3pSetup?.bootstrapStateStore;
    if(typeof store?.getState==='function'){
      const state=await bounded(store.getState(),2500,'模型读取超时').catch(()=>null);
      const catalog=state?.modelSelectorCatalog;
      const selected=Array.isArray(catalog)?catalog.find(row=>row.id===kind):null;
      if(Array.isArray(selected?.models)){choices=selected.models;source='desktop-catalog';}
    }
    if(!choices&&offeredModels.has(target)){choices=offeredModels.get(target);source='desktop-observed';}
    if(!choices){
      const result=await bounded(target.getAll(),2500,'模型读取超时').catch(()=>knownSessions.get(target)||[]);
      const rows=Array.isArray(result)?result:Array.isArray(result?.sessions)?result.sessions:knownSessions.get(target)||[];
      knownSessions.set(target,rows);choices=rows.map(row=>row.model).filter(Boolean);
    }
    const models = new Map();
    for (const raw of choices) {
      if(!raw||raw.disabled_reason||raw.disabled===true||raw.restricted===true||raw.updateRequired===true)continue;
      const id = typeof raw === 'string' ? raw : raw.id || raw.value || raw.model;
      if (typeof id !== 'string' || !id) continue;
      const name = typeof raw === 'string' ? id : raw.displayName || raw.label || raw.name || id;
      const options=raw.thinking?.effort_options||raw.efforts||[];
      const efforts=Array.isArray(options)?options.map(e=>typeof e==='string'?e:e?.id).filter(e=>typeof e==='string'):[];
      const recommended=Array.isArray(options)?options.find(e=>e?.recommended)?.id:undefined;
      models.set(id, {id, name, efforts,...recommended?{defaultEffort:recommended}:{}});
    }
    const canModel=typeof target.setModel==='function';
    const result={models:[...models.values()],source,maxRequestBytes:10*1024*1024,capabilities:{models:canModel,manualModels:canModel,manualEffort:canModel&&typeof target.setEffort==='function',
      effort:canModel&&typeof target.setEffort==='function'&&[...models.values()].some(m=>m.efforts.length),
      attachments:typeof target.sendMessage==='function',permissions:typeof target.setPermissionMode==='function',
      skills:typeof target.getSupportedCommands==='function',queue:kind==='code',steer:kind==='code'}};
    if(includeSkills&&typeof target.getSupportedCommands==='function'){
      const commands=await bounded(target.getSupportedCommands(sessionId?{sessionId}:{}),2500,'技能读取超时').catch(()=>[]);
      result.skills=(Array.isArray(commands)?commands:[]).filter(row=>typeof row.name==='string').map(row=>({
        id:row.name,name:row.name,description:typeof row.description==='string'?row.description:'',kind:'skill',selectable:row.userInvocable!==false}));
    }
    if(source==='session-history')result.notice='客户端尚未提供当前可选模型，历史模型仅供查看';
    return result;
  }
  async function mobileAccount(){
    const settings=window['claude.settings']?.Custom3pSetup;
    const result={provider:'claude',readOnly:true,canManage:false,status:'unknown',current:null};
    if(typeof settings?.getLoginDesktop3pStatus==='function'){
      const value=await bounded(settings.getLoginDesktop3pStatus(),2500,'接入信息读取超时');
      if(value?.enabled){
        result.status=value.thirdPartyConfigured&&!value.needsInteractiveAuth?'ready':'needs-login';
        result.current={kind:'api',label:typeof value.hybridOrganizationName==='string'&&value.hybridOrganizationName||
          (typeof value.provider==='string'?value.provider:'Claude Desktop'),
          ...(typeof value.provider==='string'?{provider:value.provider}:{})};
      }else result.current={kind:'official',label:'Claude Desktop'};
    }
    result.groups=await Promise.all(Object.entries(surfaces).filter(([,name])=>typeof api[name]?.getAll==='function').map(async([surface,name])=>{
      const catalog=await mobileCatalog(api[name],surface,undefined,false);
      return {surface,models:catalog.models,source:catalog.source,...catalog.notice?{notice:catalog.notice}:{}};
    }));
    return result;
  }
  // Persist only fields used by the phone, not provider configuration or tokens.
  function publicSession(row) {
    if (!row || typeof row !== 'object') return row;
    return Object.fromEntries(['sessionId', 'title', 'cwd', 'originCwd', 'model',
      'lastActivityAt', 'createdAt', 'isArchived', 'isRunning', 'turnRunning',
      'lifecycleState', 'error', 'pendingToolPermissions', 'permissionMode', 'effort', 'effortLevel', 'userSelectedFolders'].filter(k => k in row).map(k => [k, row[k]]));
  }
  function sanitize(method, result) {
    if (method === 'getContextUsageSummary' || method === 'getContextUsage') {
      if(!result||typeof result!=='object')return null;
      // Desktop's summary uses totalTokens/rawMaxTokens, not billing totals.
      const used=result.totalTokens,limit=result.rawMaxTokens;
      return Number.isFinite(used)&&used>=0&&Number.isFinite(limit)&&limit>0
        ? {usedTokens:used,contextWindow:limit} : null;
    }
    if (method === 'getAll' && Array.isArray(result)) return result.map(publicSession);
    if (method === 'getSession') return publicSession(result);
    if (method === 'getTranscript' && Array.isArray(result)) return result
      .filter(row => ['user', 'assistant', 'result'].includes(row.type || row.message?.role) ||
        row.type==='system'&&row.subtype==='compact_boundary')
      .map(row => {
        if(row.type==='system')return {type:'system',subtype:'compact_boundary'};
        if(row.type==='result'){
          const modelUsage=row.modelUsage&&typeof row.modelUsage==='object'&&!Array.isArray(row.modelUsage)
            ? Object.fromEntries(Object.entries(row.modelUsage).filter(([model,usage])=>
              model&&usage&&typeof usage==='object'&&Number.isSafeInteger(usage.contextWindow)&&usage.contextWindow>0)
              .map(([model,usage])=>[model,{contextWindow:usage.contextWindow}])) : {};
          return Object.fromEntries(
          ['type','uuid','subtype'].filter(k=>typeof row[k]==='string').map(k=>[k,row[k]])
          .concat(['is_error','isSidechain','isSyntheticResult','isSynthetic','isMeta','isCompactSummary']
            .filter(k=>typeof row[k]==='boolean').map(k=>[k,row[k]]),
            typeof row.num_turns==='number'&&Number.isFinite(row.num_turns)?[['num_turns',row.num_turns]]:[],
            'parent_tool_use_id' in row?[['parent_tool_use_id',row.parent_tool_use_id==null?null:
              typeof row.parent_tool_use_id==='string'?row.parent_tool_use_id:'nested']]:[],
            Object.keys(modelUsage).length?[['modelUsage',modelUsage]]:[]));
        }
        const source = row.message || row;
        let content = typeof source.content === 'string' ? source.content :
          (Array.isArray(source.content) ? source.content : []).filter(b =>
            ['text','input_text','output_text','tool_use','tool_result','image','input_image','thinking_summary'].includes(b.type))
          .map(b => b.type === 'tool_use' ? {type:b.type,name:b.name,input:b.input,id:b.id} :
            b.type === 'tool_result' ? {type:b.type,content:b.content,tool_use_id:b.tool_use_id} :
            ['image','input_image'].includes(b.type) ? {type:b.type,imageRef:{event:row.uuid||source.id,part:source.content.indexOf(b)}} : b.type === 'thinking_summary' ? {type:b.type,text:b.text || b.summary} : {type:b.type,text:b.text});
        const result = row.toolUseResult;
        if (result && typeof result === 'object') {
          const detail = ['stdout','stderr','output','content'].filter(k => typeof result[k] === 'string' && result[k]).map(k => result[k]).join('\n');
          if (detail) {
            if (typeof content === 'string') content = [{type:'text',text:content}];
            const tool = content.find(b => b.type === 'tool_result');
            if (tool && (!tool.content || Array.isArray(tool.content) && !tool.content.length)) tool.content = detail;
            else if (!tool) content.push({type:'tool_result',content:detail});
          }
        }
        const usage=source.usage&&typeof source.usage==='object'?Object.fromEntries(
          ['input_tokens','cache_creation_input_tokens','cache_read_input_tokens','context_window']
            .filter(key=>Number.isSafeInteger(source.usage[key])&&source.usage[key]>=0)
            .map(key=>[key,source.usage[key]])):null;
        return {type:row.type||source.role,uuid:row.uuid,isSynthetic:row.isSynthetic === true,isMeta:row.isMeta === true,
          isSidechain:row.isSidechain === true,
          ...Object.fromEntries(['entrypoint','promptId','parentUuid','sessionId']
            .filter(key=>typeof row[key]==='string').map(key=>[key,row[key]])),
          ...(typeof row.timestamp==='string'||typeof row.timestamp==='number'?{timestamp:row.timestamp}:{}),
          ...(typeof row.parent_tool_use_id==='string'?{parent_tool_use_id:row.parent_tool_use_id}:{}),
          message:{id:source.id,role:source.role || row.type,content,
            ...(typeof source.model==='string'?{model:source.model}:{}),
            ...(usage&&Object.keys(usage).length?{usage}:{}),
            ...(typeof source.effort==='string'?{effort:source.effort}:{})}};
      });
    return result ?? null;
  }
  function callArgs(method, args) {
    // JSON cannot encode holes/undefined. Claude's optional IPC positional
    // parameters accept undefined, while null fails the image/tool schema.
    if(method==='sendMessage')return args.map((value,index)=>index>=2&&value===null?undefined:value);
    if (method !== 'getSupportedCommands') return args;
    const options = args[0];
    // Older assistants sent a bare session ID (or no argument).
    return [typeof options === 'string' ? {sessionId:options} :
      options && typeof options === 'object' && !Array.isArray(options) ? options : {}];
  }
  async function mobileRename(target,id,options={}){
    const title=typeof options.title==='string'?options.title.trim():'';
    if(!title||[...title].length>120||/[\u0000-\u001f\u007f]/u.test(options.title))throw Error('标题为 1–120 字，不能包含控制字符');
    const before=await target.getSession(id);
    if(before?.sessionId!==id)throw Error('改名目标无法确认，尚未发送修改');
    const cwd=before.cwd,folders=JSON.stringify(before.userSelectedFolders);
    // Desktop 3.6556.2 owns title persistence for both Code and Cowork.
    // updateSession changes metadata without starting or resuming a session.
    await target.updateSession(id,{title,titleSource:'user'});
    const after=await target.getSession(id);
    if(after?.sessionId!==id||after.title!==title||after.cwd!==cwd||
      JSON.stringify(after.userSelectedFolders)!==folders)throw Error('改名结果待核对，请检查原客户端；不会重复提交');
    return {status:'confirmed',scope:'native',result:{id,title}};
  }
  async function mobileFork(target,id,options={}){
    if(target!==api.LocalSessions||!capabilities.code.includes('mobileFork'))throw Error('当前 Claude 接入未提供原生分支接口');
    if(typeof options.id!=='string'||!/^[0-9a-f-]{36}$/i.test(options.id)||typeof options.messageId!=='string'||!options.messageId)throw Error('分支标识无效');
    const transcript=await target.getTranscript(id);
    const anchor=Array.isArray(transcript)&&transcript.find(row=>row.uuid===options.messageId&&row.type==='assistant'&&
      !['isSynthetic','isMeta','isSidechain','parent_tool_use_id','isCompactSummary'].some(key=>row[key]));
    if(!anchor)throw Error('原消息已不可用，请刷新聊天后重试');
    // Use Desktop's own inclusive UUID cut and preserve the parent workspace.
    // The gateway receipt fences retries; a lost response must never fork again.
    return target.forkSession(id,'local_'+options.id,options.messageId);
  }
  async function mobileList(){
    if(typeof api.LocalSessions?.loadArchivedSessions==='function')await api.LocalSessions.loadArchivedSessions();
    const rows=await Promise.all(Object.entries(surfaces).filter(([,name])=>typeof api[name]?.getAll==='function').map(async([kind,name])=>{
      const result=await api[name].getAll();
      if(Array.isArray(result))knownSessions.set(api[name],result);
      return [kind,sanitize('getAll',result)];
    }));
    return Object.fromEntries(rows);
  }
  async function mobileArchiveState(target,id){
    if(target!==api.LocalSessions||!capabilities.code.includes('mobileArchive'))throw Error('当前接入尚未确认原生归档和恢复能力');
    const [root,all]=await Promise.all([target.getSession(id),target.getAll()]);
    if(root?.sessionId!==id||!Array.isArray(all))throw Error('归档目标或子会话状态无法确认');
    const scope=new Set([id]);let expanded=true;
    while(expanded){expanded=false;for(const row of all){if(scope.has(row.spawnedFrom?.sessionId)&&!scope.has(row.sessionId)){scope.add(row.sessionId);expanded=true;}}}
    const rows=[root,...all.filter(row=>row.sessionId!==id&&scope.has(row.sessionId))];
    const nonempty=value=>Array.isArray(value)?value.length>0:value&&typeof value==='object'?true:!!value;
    const idle=row=>row&&row.isRunning===false&&typeof row.isArchived==='boolean'&&
      !row.turnRunning&&!row.starting&&!row.cliBootPending&&!row.hasBackgroundActivity&&!row.hasBackgroundWork&&!row.hasLiveWorkflows&&
      ['pendingToolPermissions','pendingRefusalFallbackPrompt','pendingAutoModeServerFallbackPrompt','pendingLanyardConsent','pendingViolinBowPrompt','pendingCwdTrustPrompt','heldInput','queuedSideSessionNotes','spawningTaskIds'].every(key=>!nonempty(row[key]));
    return {session:publicSession(root),archiveSafe:rows.every(idle),archiveFlags:Object.fromEntries(rows.map(row=>[row.sessionId,row.isArchived])),
      runtimeKnown:rows.every(row=>typeof row.isRunning==='boolean'&&typeof row.isArchived==='boolean'),
      runningIds:rows.filter(row=>row.isRunning||row.turnRunning).map(row=>row.sessionId)};
  }
  async function mobileArchive(target,id,options={}){
    if(typeof options.archived!=='boolean'||(options.disconnect!=null&&typeof options.disconnect!=='boolean')||options.disconnect&&!options.archived)throw Error('归档设置无效');
    let state=await mobileArchiveState(target,id);
    if(options.disconnect){
      if(!state.runtimeKnown||state.runningIds.length&&typeof target.interrupt!=='function')return {archiveNotSent:true,message:'无法确认可停止的原生任务，未停止或归档'};
      const scope=Object.keys(state.archiveFlags).sort().join(',');
      for(const child of [...state.runningIds].reverse())await target.interrupt(child);
      const deadline=Date.now()+15000;
      do{
        state=await mobileArchiveState(target,id);
        if(Object.keys(state.archiveFlags).sort().join(',')!==scope)throw Error('子会话范围已变化，尚未发送归档请求');
        if(state.archiveSafe)break;
        if(Date.now()>=deadline)throw Error('停止结果待核对，尚未发送归档请求');
        await new Promise(resolve=>setTimeout(resolve,100));
      }while(true);
    }
    if(!state.archiveSafe)return {archiveNotSent:true,message:'聊天或子会话有运行、排队、审批或未知状态，请先在原客户端处理'};
    if(options.archived)await target.archive(id,{cleanupWorktree:false});else await target.unarchive(id);
    return {status:'accepted'};
  }
  async function mobileProjectTrust(target,path,confirmed){
    if(typeof path!=='string'||!path||path.length>4096||/[\0\r\n]/.test(path)||typeof confirmed!=='boolean')throw Error('工作区确认格式无效');
    const before=await target.checkTrust(path);
    if(typeof before?.trusted!=='boolean')throw Error('无法核验原生工作区信任状态');
    if(!before.trusted&&confirmed)await target.saveTrust(path);
    const state=confirmed?await target.checkTrust(path):before;
    if(typeof state?.trusted!=='boolean'||confirmed&&!state.trusted)throw Error('工作区信任结果待核对，请在原客户端检查');
    return {trusted:state.trusted,path};
  }
  async function mobileImage(target,id,source){
    if(!source||typeof source.event!=='string'||!Number.isInteger(source.part)||source.part<0||source.part>=4096)throw Error('图片引用无效');
    const session=await target.getSession(id);if(session?.sessionId!==id)throw Error('会话标识已变化');
    const raw=await target.getTranscript(id),rows=Array.isArray(raw)?raw:raw?.messages||raw?.transcript||[];
    const event=rows.find(row=>(row.uuid||row.message?.id)===source.event),block=event?.message?.content?.[source.part]||event?.content?.[source.part];
    if(!block||!['image','input_image'].includes(block.type))throw Error('原生历史中未找到可预览图片');
    if(block.source?.type==='base64')return {mediaType:block.source.media_type,data:block.source.data};
    const url=typeof block.image_url==='object'?block.image_url?.url:block.image_url||block.url;
    const match=typeof url==='string'&&/^data:(image\/(?:png|jpeg|gif|webp));base64,(.*)$/.exec(url);
    if(!match)throw Error('原生图片格式不支持预览');
    return {mediaType:match[1],data:match[2]};
  }
  async function mobileDetail(target,id,options={}){
    const paged=target===api.LocalSessions&&capabilities.code.includes('mobileHistory');
    const limit=Number.isInteger(options.historyLimit)&&options.historyLimit>=1&&options.historyLimit<=100?options.historyLimit:40;
    const before=options.historyBefore;
    if(before!=null&&(typeof before!=='string'||!before||!paged))throw Error('历史游标无效');
    const transcriptPromise=paged?(before?target.getTranscriptPage(id,before,limit):target.getTranscriptTail(id,limit)):target.getTranscript(id);
    const [session,page,context]=await Promise.all([target.getSession(id),transcriptPromise,mobileContextUsage(target,id)]);
    if(paged&&(!Array.isArray(page?.messages)||typeof page.hasMore!=='boolean'||before&&page.found!==true))throw Error('历史分页响应无效');
    const rows=paged?page.messages:page;
    const boundary=rows?.[0]?.uuid;
    if(paged&&page.hasMore&&(typeof boundary!=='string'||!boundary||boundary===before))throw Error('历史游标未前进');
    return {session:sanitize('getSession',session),transcript:sanitize('getTranscript',rows),
      history:{before:paged&&page.hasMore?boundary:null,hasMore:paged?page.hasMore:false},...context};
  }
  async function mobileContextUsage(target,id){
    const methods=['getContextUsageSummary','getContextUsage'].filter(method=>typeof target[method]==='function');
    if(!methods.length)return {contextUsage:null,contextUsageStatus:'unsupported'};
    // Desktop's summary is gated by the running CLI version. The older getter
    // is also read-only and returns null instead of starting a dormant process.
    // Share one deadline so this optional data never doubles detail latency.
    const deadline=Date.now()+1500, timeout=new Error('Context unavailable');
    for(const method of methods){
      const remaining=deadline-Date.now();if(remaining<=0)break;
      try{
        const usage=sanitize(method,await bounded(target[method](id),remaining,timeout));
        if(usage)return {contextUsage:usage,contextUsageStatus:'available'};
      }catch(error){if(error===timeout)break;}
    }
    return {contextUsage:null,contextUsageStatus:'unavailable'};
  }
  if (config.transport === 'cdp') {
    let stopped = false, inFlight = 0, highWater = 0;
    await activate({
      stop() { stopped = true; for (const undo of restore) undo(); },
      owns(cwd) { return sameWorkspace(cwd); },
      status() { return {transport:'cdp', stopped, busy:inFlight > 0, connectorRevision:config.connectorRevision, surfaces:capabilities}; },
      async invoke(msg) {
        if (stopped || msg?.token !== config.token || msg.generation !== config.generation)
          return {error:'连接已过期'};
        if (!Number.isSafeInteger(msg.seq) || msg.seq <= highWater) return {error:'请求已处理，不会重复执行'};
        highWater = msg.seq;
        if (typeof msg.expires !== 'number' || msg.expires < Date.now()/1000) return {error:'请求已过期'};
        inFlight++;
        try {
          const target = api[surfaces[msg.surface]];
          if (!target || !Array.isArray(msg.args)) throw new Error('不支持的桌面操作');
          if (msg.method === 'mobileCatalog') return {result:await mobileCatalog(target,msg.surface,msg.args[0])};
          if (msg.method === 'mobileAccount') return {result:await mobileAccount()};
          if (msg.method === 'mobileProjectTrust' && capabilities[msg.surface].includes('mobileProjectTrust')) return {result:await mobileProjectTrust(target,msg.args[0],msg.args[1])};
          if (msg.method === 'mobileImage' && capabilities[msg.surface].includes('mobileImage')) return {result:await mobileImage(target,msg.args[0],msg.args[1])};
          if (msg.method === 'mobileArchiveState' && capabilities[msg.surface].includes('mobileArchiveState')) return {result:await mobileArchiveState(target,msg.args[0])};
          if (msg.method === 'mobileArchive' && capabilities[msg.surface].includes('mobileArchive')) return {result:await mobileArchive(target,msg.args[0],msg.args[1])};
          if (msg.method === 'mobileList') return {result:await bounded(mobileList(),30000,'读取会话超时')};
          if (msg.method === 'mobileDetail' && capabilities[msg.surface].includes('mobileDetail')) return {result:await bounded(mobileDetail(target,msg.args[0],msg.args[1]),30000,'读取会话超时')};
          if (msg.method === 'mobileFork' && capabilities[msg.surface].includes('mobileFork')) return {result:await mobileFork(target,msg.args[0],msg.args[1])};
          if (msg.method === 'mobileRename' && capabilities[msg.surface].includes('mobileRename')) return {result:await mobileRename(target,msg.args[0],msg.args[1])};
          if (msg.method === 'mobileProgress') { const page=msg.surface==='code'&&capabilities.code.includes('mobileHistory')?await target.getTranscriptTail(msg.args[0],24):await target.getTranscript(msg.args[0]); const rows=Array.isArray(page)?page:page?.messages; return {result:sanitize('getTranscript',Array.isArray(rows)?rows.slice(-24):[])}; }
          if (!allowed.has(msg.method) || typeof target[msg.method] !== 'function') throw new Error('不支持的桌面操作');
          return {result:sanitize(msg.method, await target[msg.method](...callArgs(msg.method,msg.args)))};
        } catch (error) { return {error:String(error.message || error)}; }
        finally { inFlight--; }
      }
    });
    return;
  }
  const initial = await read();
  if (!initial || (initial.generation !== config.generation && (initial.type !== 'idle' || initial.seq !== 0)))
    throw new Error('连接脚本已过期，请通过手机 Claude 标题选择启动以重新连接');
  const sessions = await api.LocalSessions.getAll();
  knownSessions.set(api.LocalSessions,Array.isArray(sessions)?sessions:[]);
  const matching = sessions.filter(row => !row.isArchived && sameWorkspace(row.cwd));
  const sessionId = (matching.find(row => row.title === 'Codex Bridge Connector') || matching[0])?.sessionId;
  let staging = null;
  if (!sessionId) {
    const trust = await api.LocalSessions.checkTrust?.(config.cwd);
    if (trust?.trusted !== true)
      throw new Error('Claude 尚未信任目录：' + config.cwd + '。请在 Claude Code 页面选择此目录并确认信任，再初始化连接。');
    if (typeof api.FileSystem?.savePastedFile !== 'function')
      throw new Error('当前 Claude Desktop 不支持无消息初始化，请更新 Claude Desktop 后重试');
    const digest = prefix => crypto.subtle.digest('SHA-256',encoder.encode(prefix + config.token));
    staging = {name:'cmb-' + hex(await digest('cmb-reply-name:')).slice(0,32) + '.bin',
      key:await crypto.subtle.importKey('raw',await digest('cmb-reply-key:'),{name:'AES-GCM'},false,['encrypt']),
      pending:null};
  }
  let generation = initial.generation;
  const generations = new Set([generation]);
  let stopped = false, suspended = initial.type === 'stop', busy = false, inFlight = 0, writing = 0, highWater = initial.seq, lastWrite = 0, dirty = true;
  let handoff = null, completedHandoff = null;
  let wake, wakeRequested=false;
  const wakePoll=()=>{wakeRequested=true;wake?.();};
  const pause=async()=>{
    if(!wakeRequested)await new Promise(resolve=>{
      let timer;
      const finish=()=>{clearTimeout(timer);wake=undefined;resolve();};
      wake=finish;timer=setTimeout(finish,500);
    });
    wakeRequested=false;
  };
  let state = {seq: 0, done: false};
  await activate({stop() { stopped = true;wakePoll(); for (const undo of restore) undo(); },
    owns(cwd) { return sameWorkspace(cwd); },
    status() { return {transport: 'file', stopped, suspended, busy:busy || inFlight > 0 || !!handoff,
      handoffPending:!!handoff,
      writing:writing > 0, sessionId, lastWrite, connectorRevision:config.connectorRevision}; },
    async handoff(cwd) {
      if (sameWorkspace(cwd) || stopped || suspended) return false;
      if (handoff) return bounded(handoff.promise,6000,false).catch(() => false);
      if (busy || inFlight || writing) return false;
      let resolve;
      const promise = new Promise(done => { resolve = done; });
      const pending = {id:null,promise,resolve};
      handoff = pending;
      let current;
      try { current = await read(); }
      catch { if (handoff === pending) handoff = null; resolve(false); return false; }
      if (handoff !== pending) return false;
      if (busy || inFlight || writing || stopped || suspended ||
          !current || current.generation !== generation || current.type !== 'idle' ||
          current.seq !== highWater || highWater > 0 && state.done !== true) {
        handoff = null; resolve(false); return false;
      }
      pending.id = generation + ':' + highWater + ':' + Date.now();
      dirty = true; wakePoll();
      // A timeout does not revoke the offer: the old writer may already have
      // fenced itself and its acknowledgement may still be reaching Desktop.
      return bounded(promise,6000,false).catch(() => false);
    }});
  async function publish() {
    writing++;
    try {
      if (staging?.pending?.generation === generation) {
        const ack = await read('staged-ack.json').catch(() => null);
        if (ack?.generation !== generation || ack.receipt !== staging.pending.receipt) {dirty=true;return;}
        staging.pending=null;
      }
      let value = {...state, generation, connected: !stopped && !suspended && !handoff?.id,
        ...(handoff?.id ? {handoffId:handoff.id} : {}),
        timestamp: Date.now() / 1000, connectorRevision:config.connectorRevision, surfaces: capabilities};
      if (staging) value.receipt=hex(crypto.getRandomValues(new Uint8Array(16)));
      let text = await pack(value);
      if (stopped) return;
      if (encoder.encode(text).length > 32 * 1024 * 1024) {
        state = {seq: state.seq, done: true, error: '会话内容超过 32 MiB，请在桌面查看'};
        text = await pack({...value, ...state, result: undefined});
      }
      if (stopped) return;
      if (staging) {
        const nonce=crypto.getRandomValues(new Uint8Array(12));
        const ciphertext=new Uint8Array(await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce},staging.key,encoder.encode(text)));
        const packet=new Uint8Array(nonce.length+ciphertext.length);packet.set(nonce);packet.set(ciphertext,nonce.length);
        const chunks=[];
        for(let offset=0;offset<packet.length;offset+=24576)
          chunks.push(btoa(String.fromCharCode(...packet.subarray(offset,offset+24576))));
        if (staging.pending?.generation===generation || generation!==value.generation || stopped) {dirty=true;return;}
        const pending=staging.pending={generation,receipt:value.receipt};
        try {
          const saved=await api.FileSystem.savePastedFile(chunks.join(''),'application/octet-stream',staging.name);
          if (!saved?.path) throw new Error('Claude 未能暂存连接回执，请重新初始化连接');
        } catch (error) {
          // The outer publish deadline does not settle this native write. Only
          // its actual failure permits retry; a late failure cannot clear a new receipt.
          if (staging.pending===pending) staging.pending=null;
          throw error;
        }
      } else {
        const result = await api.LocalSessions.writeSessionFile(sessionId, config.response, text);
        if (!result?.hash) throw new Error('Claude 拒绝写入桥接文件，请检查目录信任和文件权限');
      }
      lastWrite = Date.now();
    } finally { writing--; }
  }
  console.info('Claude 文件桥接启动中；状态：window.__claudeMobileBridge.status()');
  try {
    while (!stopped) {
      let msg;
      try { msg = await bounded(read(),4000,'读取桥接文件超时'); }
      catch (error) {
        if (!config.reconnect) throw error;
        await new Promise(resolve => setTimeout(resolve, 1000));
        continue;
      }
      if (stopped) break;
      if (handoff && msg?.type === 'handoff-ack' && msg.generation === generation &&
          msg.handoffId === handoff.id && msg.seq === highWater) {
        completedHandoff = handoff; handoff = null; suspended = true; stopped = true;
        break;
      }
      if (handoff && msg?.type === 'request' && msg.generation === generation &&
          Number.isSafeInteger(msg.seq) && msg.seq > highWater) {
        const pending = handoff; handoff = null; pending.resolve(false); dirty = true;
      }
      if (msg && msg.generation !== generation) {
        // A signed idle packet is the only restart handshake. Never replay a request.
        if (!config.reconnect || generations.has(msg.generation) || msg.type !== 'idle' || msg.seq !== 0) {
          await new Promise(resolve => setTimeout(resolve, 500)); continue;
        }
        generation = msg.generation; generations.add(generation);
        highWater = 0; state = {seq: 0, done: false}; busy = false; suspended = false; dirty = true;
      }
      if (msg?.type === 'stop') {
        if (handoff) { const pending = handoff; handoff = null; pending.resolve(false); }
        if (!config.reconnect) break;
        if (!suspended) { suspended = true; dirty = true; }
      }
      if (!suspended && !busy && msg?.type === 'request' && Number.isSafeInteger(msg.seq) && msg.seq > highWater) {
        highWater = msg.seq;
        state = {seq: msg.seq, done: false};
        dirty = true;
        if (typeof msg.expires !== 'number' || msg.expires < Date.now() / 1000) {
          state = {seq: msg.seq, done: true, error: '请求已过期，未执行'};
        } else {
          busy = true;
          const dispatchedGeneration = generation;
          const operation = Promise.resolve().then(async () => {
            const target = api[surfaces[msg.surface]];
            if (target && msg.method === 'mobileCatalog') return mobileCatalog(target,msg.surface,msg.args[0]);
            if (target && msg.method === 'mobileAccount') return mobileAccount();
            if (target && msg.method === 'mobileProjectTrust' && capabilities[msg.surface].includes('mobileProjectTrust')) return mobileProjectTrust(target,msg.args[0],msg.args[1]);
            if (target && msg.method === 'mobileArchiveState' && capabilities[msg.surface].includes('mobileArchiveState')) return mobileArchiveState(target,msg.args[0]);
            if (target && msg.method === 'mobileArchive' && capabilities[msg.surface].includes('mobileArchive')) return mobileArchive(target,msg.args[0],msg.args[1]);
            if (target && msg.method === 'mobileList') return mobileList();
            if (target && msg.method === 'mobileDetail' && capabilities[msg.surface].includes('mobileDetail')) return mobileDetail(target,msg.args[0],msg.args[1]);
            if (target && msg.method === 'mobileFork' && capabilities[msg.surface].includes('mobileFork')) return mobileFork(target,msg.args[0],msg.args[1]);
            if (target && msg.method === 'mobileRename' && capabilities[msg.surface].includes('mobileRename')) return mobileRename(target,msg.args[0],msg.args[1]);
            if (target && msg.method === 'mobileImage' && capabilities[msg.surface].includes('mobileImage')) return mobileImage(target,msg.args[0],msg.args[1]);
            if (target && msg.method === 'mobileProgress') { const page=msg.surface==='code'&&capabilities.code.includes('mobileHistory')?await target.getTranscriptTail(msg.args[0],24):await target.getTranscript(msg.args[0]); const rows=Array.isArray(page)?page:page?.messages; return sanitize('getTranscript',Array.isArray(rows)?rows.slice(-24):[]); }
            if (!target || !allowed.has(msg.method) || typeof target[msg.method] !== 'function' || !Array.isArray(msg.args)) {
              throw new Error('不支持的桌面操作');
            }
            return sanitize(msg.method, await target[msg.method](...callArgs(msg.method,msg.args)));
          });
          // The deadline settles the gateway reply, not the Desktop call.
          // Retain ownership until the underlying call itself settles.
          inFlight++;
          operation.then(() => { inFlight--; }, () => { inFlight--; });
          bounded(operation,Math.max(100,Math.min(90000,(msg.expires-Date.now()/1000)*1000)),'桌面操作超时，结果待核对；不会重复执行').then(result => { if (generation === dispatchedGeneration) state = {seq: msg.seq, done: true, result}; },
            error => { if (generation === dispatchedGeneration) state = {seq: msg.seq, done: true, error: String(error.message || error)}; })
            .finally(() => { if (generation === dispatchedGeneration) { busy = false; dirty = true;wakePoll(); } });
        }
      }
      if (dirty || Date.now() - lastWrite > 5000) {
        dirty=false;
        try { await bounded(publish(),4000,'写入桥接文件超时'); }
        catch(error) { dirty = true;window.__codingRemoteInjectionError=String(error.message||error); }
      }
      await pause();
    }
  } catch (error) { window.__codingRemoteInjectionError = String(error.message || error); console.error('Claude 文件桥接停止：', error); }
  finally { stopped = true; for (const undo of restore) undo(); await publish().catch(() => {});
    if (handoff) { handoff.resolve(false); handoff = null; }
    completedHandoff?.resolve(true); }
})();
