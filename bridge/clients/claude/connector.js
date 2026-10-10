// Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
/* Injected into the Claude renderer through the verified DevTools Console. Uses the existing
 * workspace file APIs and session APIs; never changes CSP or workspace trust.
 * Runtime files must remain local. Reloading the renderer removes this bridge.
 */
(async () => {
  const config = __BRIDGE_CONFIG__;
  const api = window['claude.web'];
  if (!api?.LocalSessions?.getAll || (config.transport !== 'cdp' && (!api.LocalSessions.readFileAtCwd || !api.LocalSessions.writeSessionFile))) {
    throw new Error('当前 Claude Desktop 不提供所需的本地文件接口');
  }
  window.__claudeMobileBridge?.stop();
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(config.token),
    {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
  const hex = buffer => [...new Uint8Array(buffer)].map(v => v.toString(16).padStart(2, '0')).join('');
  async function pack(value) {
    const payload = JSON.stringify(value);
    return JSON.stringify({payload, signature: hex(await crypto.subtle.sign('HMAC', key, encoder.encode(payload)))});
  }
  async function read() {
    const file = await api.LocalSessions.readFileAtCwd(config.cwd, config.request);
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
    'interrupt', 'stop', 'respondToToolPermission', 'start', 'getPermissionMode', 'archive', 'delete', 'updateSession',
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
    if(kind==='code')capabilities[kind].push('mobileList');
    if(typeof api[surface]?.getSession==='function'&&typeof api[surface]?.getTranscript==='function')capabilities[kind].push('mobileDetail');
    const target = api[surface], original = target?.setAvailableCodeModels;
    if (typeof original === 'function') {
      const observer = function (models, ...rest) {
        if (Array.isArray(models)) offeredModels.set(target, models);
        return original.call(target, models, ...rest);
      };
      try { target.setAvailableCodeModels = observer; restore.push(() => { if (target.setAvailableCodeModels === observer) target.setAvailableCodeModels = original; }); } catch {}
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
    const canModel=source!=='session-history'&&typeof target.setModel==='function'&&models.size>0;
    const result={models:[...models.values()],source,maxRequestBytes:10*1024*1024,capabilities:{models:canModel,
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
      .filter(row => ['user', 'assistant', 'result'].includes(row.type || row.message?.role))
      .map(row => {
        if(row.type==='result')return Object.fromEntries(
          ['type','uuid','subtype'].filter(k=>typeof row[k]==='string').map(k=>[k,row[k]])
          .concat(['is_error','isSidechain','isSyntheticResult','isSynthetic','isMeta','isCompactSummary']
            .filter(k=>typeof row[k]==='boolean').map(k=>[k,row[k]]),
            typeof row.num_turns==='number'&&Number.isFinite(row.num_turns)?[['num_turns',row.num_turns]]:[],
            'parent_tool_use_id' in row?[['parent_tool_use_id',row.parent_tool_use_id==null?null:
              typeof row.parent_tool_use_id==='string'?row.parent_tool_use_id:'nested']]:[]));
        const source = row.message || row;
        let content = typeof source.content === 'string' ? source.content :
          (Array.isArray(source.content) ? source.content : []).filter(b =>
            ['text','input_text','output_text','tool_use','tool_result','image','input_image','thinking_summary'].includes(b.type))
          .map(b => b.type === 'tool_use' ? {type:b.type,name:b.name,input:b.input,id:b.id} :
            b.type === 'tool_result' ? {type:b.type,content:b.content,tool_use_id:b.tool_use_id} :
            ['image','input_image'].includes(b.type) ? {type:b.type} : b.type === 'thinking_summary' ? {type:b.type,text:b.text || b.summary} : {type:b.type,text:b.text});
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
        return {type:row.type,uuid:row.uuid,isSynthetic:row.isSynthetic === true,isMeta:row.isMeta === true,
          message:{id:source.id,role:source.role || row.type,content,
            ...(typeof source.model==='string'?{model:source.model}:{}),
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
  async function mobileList(){
    const rows=await Promise.all(Object.entries(surfaces).filter(([,name])=>typeof api[name]?.getAll==='function').map(async([kind,name])=>{
      const result=await api[name].getAll();
      if(Array.isArray(result))knownSessions.set(api[name],result);
      return [kind,sanitize('getAll',result)];
    }));
    return Object.fromEntries(rows);
  }
  async function mobileDetail(target,id){
    const [session,transcript,context]=await Promise.all([target.getSession(id),target.getTranscript(id),mobileContextUsage(target,id)]);
    return {session:sanitize('getSession',session),transcript:sanitize('getTranscript',transcript),...context};
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
    let stopped = false, highWater = 0;
    window.__claudeMobileBridge = {
      stop() { stopped = true; for (const undo of restore) undo(); },
      status() { return {transport:'cdp', stopped, connectorRevision:config.connectorRevision, surfaces:capabilities}; },
      async invoke(msg) {
        if (stopped || msg?.token !== config.token || msg.generation !== config.generation)
          return {error:'连接已过期'};
        if (!Number.isSafeInteger(msg.seq) || msg.seq <= highWater) return {error:'请求已处理，不会重复执行'};
        highWater = msg.seq;
        if (typeof msg.expires !== 'number' || msg.expires < Date.now()/1000) return {error:'请求已过期'};
        try {
          const target = api[surfaces[msg.surface]];
          if (!target || !Array.isArray(msg.args)) throw new Error('不支持的桌面操作');
          if (msg.method === 'mobileCatalog') return {result:await mobileCatalog(target,msg.surface,msg.args[0])};
          if (msg.method === 'mobileAccount') return {result:await mobileAccount()};
          if (msg.method === 'mobileList') return {result:await bounded(mobileList(),30000,'读取会话超时')};
          if (msg.method === 'mobileDetail' && capabilities[msg.surface].includes('mobileDetail')) return {result:await bounded(mobileDetail(target,msg.args[0]),30000,'读取会话超时')};
          if (msg.method === 'mobileProgress') { const rows=await target.getTranscript(msg.args[0]); return {result:sanitize('getTranscript',Array.isArray(rows)?rows.slice(-24):[])}; }
          if (!allowed.has(msg.method) || typeof target[msg.method] !== 'function') throw new Error('不支持的桌面操作');
          return {result:sanitize(msg.method, await target[msg.method](...callArgs(msg.method,msg.args)))};
        } catch (error) { return {error:String(error.message || error)}; }
      }
    };
    return;
  }
  const initial = await read();
  if (!initial || (initial.generation !== config.generation && (initial.type !== 'idle' || initial.seq !== 0)))
    throw new Error('连接脚本已过期，请通过手机 Claude 标题选择启动以重新连接');
  // Reusing this dedicated session never changes another project's workspace.
  const normalize = path => String(path || '').replaceAll('\\', '/').replace(/\/$/, '').toLowerCase();
  const sessions = await api.LocalSessions.getAll();
  knownSessions.set(api.LocalSessions,Array.isArray(sessions)?sessions:[]);
  let session = sessions.find(row => row.title === 'Codex Bridge Connector'
    && normalize(row.cwd) === normalize(config.cwd) && !row.isArchived);
  // Reuse a session already belonging to this workspace; file calls do not send a turn.
  session ||= sessions.find(row => normalize(row.cwd) === normalize(config.cwd) && !row.isArchived);
  if (!session) {
    try { session = await api.LocalSessions.start({cwd: config.cwd, message: '',
      title: 'Codex Bridge Connector', useWorktree: false}); }
    catch (error) {
      if (/trust_required|WorkspaceTrustError/.test(String(error)))
        throw new Error('Claude 尚未信任目录：' + config.cwd + '。请在 Claude Code 页面选择此目录并确认信任，再在网关的“Claude 接入准备”中点击“初始化连接”。');
      throw error;
    }
  }
  const sessionId = session.sessionId;
  if (!sessionId) throw new Error('无法创建文件桥接会话；请在 Code 页面确认目录信任');
  let generation = initial.generation;
  const generations = new Set([generation]);
  let stopped = false, suspended = initial.type === 'stop', busy = false, highWater = initial.seq, lastWrite = 0, dirty = true;
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
  window.__claudeMobileBridge = {stop() { stopped = true;wakePoll(); for (const undo of restore) undo(); },
    status() { return {transport: 'file', stopped, busy, sessionId, lastWrite, connectorRevision:config.connectorRevision}; }};
  async function publish() {
    let value = {...state, generation, connected: !stopped && !suspended,
      timestamp: Date.now() / 1000, connectorRevision:config.connectorRevision, surfaces: capabilities};
    let text = await pack(value);
    if (encoder.encode(text).length > 32 * 1024 * 1024) {
      state = {seq: state.seq, done: true, error: '会话内容超过 32 MiB，请在桌面查看'};
      text = await pack({...value, ...state, result: undefined});
    }
    const result = await api.LocalSessions.writeSessionFile(sessionId, config.response, text);
    if (!result?.hash) throw new Error('Claude 拒绝写入桥接文件，请检查目录信任和文件权限');
    lastWrite = Date.now();
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
      if (msg && msg.generation !== generation) {
        // A signed idle packet is the only restart handshake. Never replay a request.
        if (!config.reconnect || generations.has(msg.generation) || msg.type !== 'idle' || msg.seq !== 0) {
          await new Promise(resolve => setTimeout(resolve, 500)); continue;
        }
        generation = msg.generation; generations.add(generation);
        highWater = 0; state = {seq: 0, done: false}; busy = false; suspended = false; dirty = true;
      }
      if (msg?.type === 'stop') {
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
            if (target && msg.method === 'mobileList') return mobileList();
            if (target && msg.method === 'mobileDetail' && capabilities[msg.surface].includes('mobileDetail')) return mobileDetail(target,msg.args[0]);
            if (target && msg.method === 'mobileProgress') { const rows=await target.getTranscript(msg.args[0]); return sanitize('getTranscript',Array.isArray(rows)?rows.slice(-24):[]); }
            if (!target || !allowed.has(msg.method) || typeof target[msg.method] !== 'function' || !Array.isArray(msg.args)) {
              throw new Error('不支持的桌面操作');
            }
            return sanitize(msg.method, await target[msg.method](...callArgs(msg.method,msg.args)));
          });
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
  finally { stopped = true; for (const undo of restore) undo(); await publish().catch(() => {}); }
})();
