// Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
// Harness plugin: authenticated loopback adapter over the installed session services.
import {createServer} from 'node:http';
import {readFile,writeFile,rename,unlink,realpath,stat} from 'node:fs/promises';
import {timingSafeEqual,randomUUID} from 'node:crypto';
export const inject=['sessionController','sessionSkillCatalog','permissionPresets','agents','workspaceRegistry'];
export async function readHistoryPage(controller,row,body={},signal=new AbortController().signal){
  const limit=body.historyLimit??40;
  if(!Number.isInteger(limit)||limit<1||limit>100)throw Error('历史页大小无效');
  let cursor;
  if(body.historyBefore!=null){
    try{cursor=JSON.parse(Buffer.from(body.historyBefore,'base64url').toString('utf8'));}catch{throw Error('历史游标无效');}
    if(cursor?.sid!==row.sessionId||!Number.isSafeInteger(cursor.seq)||cursor.seq<0||!Number.isSafeInteger(cursor.before)||cursor.before<0)throw Error('历史游标无效');
  }
  const projection=cursor?null:await controller.projections({sessionId:row.sessionId},signal);
  const seq=cursor?.seq??projection?.asOfSeq??row.projections?.asOfSeq;
  if(!Number.isSafeInteger(seq)||seq<0)throw Error('历史版本无效');
  const page=await controller.page({address:{kind:'session',sessionId:row.sessionId},throughSeq:seq,...cursor?{beforeSeq:cursor.before}:{},maxMessages:limit},signal);
  const rows=page.records||[],boundary=rows[0]?.event?.seq;
  if(!Array.isArray(rows)||typeof page.hasMore!=='boolean'||page.hasMore&&(!Number.isSafeInteger(boundary)||cursor&&boundary>=cursor.before))throw Error('历史分页响应无效');
  return {rows,history:{before:page.hasMore?Buffer.from(JSON.stringify({sid:row.sessionId,seq,before:boundary})).toString('base64url'):null,hasMore:page.hasMore}};
}
// Retain only display metadata from request headers; never forward the header itself.
export async function historyRecords(ctx,rows,signal=new AbortController().signal){
  let selection;
  const first=rows[0]?.event?.seq,query=ctx.get?.('sessionQuery');
  const firstRelevant=rows.find(r=>['request/header','assistant/message'].includes(r.event.type));
  if(first>0&&firstRelevant?.event.type==='assistant/message'&&typeof query?.observeSession==='function'){
    // The native pager returns a contiguous suffix. Its preceding request may be
    // outside that page; a cold observation reads it without activating an agent.
    const observation=await query.observeSession(ctx.sessionId,{signal,projectionMode:'none'});
    try{for(const event of observation.events){if(event.seq>=first)break;if(event.type==='request/header')selection=event.data?.header?.config;}}
    finally{observation[Symbol.dispose]();}
  }
  const result=[];
  for(const row of rows){
    const event=row.event;
    if(event.type==='request/header'){selection=event.data?.header?.config;continue;}
    if(!['user/message','assistant/message','tool/call','tool/result','todo/write','turn/start','turn/end'].includes(event.type))continue;
    if(event.type==='assistant/message'&&selection){
      const modelSelection={};for(const key of ['provider','model','reasoningEffort'])if(typeof selection[key]==='string')modelSelection[key]=selection[key];
      result.push({...row,event:{...event,data:{...event.data,modelSelection}}});
    }else result.push(row);
  }
  return result;
}
export async function readHistoryImage(ctx,sid,source,signal=new AbortController().signal){
  if(!Number.isSafeInteger(source?.seq)||source.seq<0||!Number.isSafeInteger(source?.part)||source.part<0||source.part>=4096)throw Error('图片引用无效');
  const query=ctx.get?.('sessionQuery'),attachments=ctx.get?.('attachments');
  if(typeof query?.observeSession!=='function'||typeof attachments?.readImage!=='function')throw Error('当前 Harness 未提供历史图片读取接口');
  const observation=await query.observeSession(sid,{signal,projectionMode:'none'});
  try{
    const event=observation.events.find(event=>event.seq===source.seq);
    if(!event||!['user/message','assistant/message'].includes(event.type))throw Error('原生历史中未找到可预览图片');
    const message=event.data?.message||event.data,block=message?.content?.[source.part],ref=block?.attachment;
    if(block?.type!=='image'||!ref||!Number.isSafeInteger(ref.bytes)||ref.bytes<=0||ref.bytes>20*1024*1024)throw Error('原生图片格式或大小不支持预览');
    const stored=await attachments.readImage(ref,signal);
    if(!stored?.data||stored.data.byteLength!==ref.bytes||stored.ref?.attachmentId!==ref.attachmentId)throw Error('原生图片标识已变化');
    return {mediaType:stored.ref.mediaType,data:Buffer.from(stored.data).toString('base64')};
  }finally{observation[Symbol.dispose]();}
}
export async function apply(ctx,config){
  const {token,endpoint}=JSON.parse(await readFile(config.configPath,'utf8'));
  // The gateway accepts at most 100 MiB of decoded images plus small JSON fields.
  const maxRequestBytes=Math.ceil(100*1024*1024*4/3)+1024*1024;
  const questions=new Map();
  let quitting=false,activeMutations=0;
  const signal=()=>new AbortController().signal;
  const list=async()=> (await ctx.sessionController.list({},signal())).items;
  const find=async id=>{const row=(await list()).find(r=>r.sessionId===id);if(!row)throw Error('DeepSeek 会话不存在');return row;};
  const values=row=>row.projections?.values||{};
  const modeServices=row=>{
    const agent=row&&ctx.agents?.get?.(row.sessionId),scope=agent?.ctx||ctx,commands=scope.get?.('commands');
    const plan=!!agent&&!!commands?.list(agent).some(command=>command.definitionId==='@deepseek-ai/dsh-plan-mode');
    return {plan,commands,compact:!!agent&&!!commands?.list(agent).some(command=>command.definitionId==='@deepseek-ai/dsh-command-compact'),goals:scope.get?.('goals')};
  };
  const workState=row=>{
    const services=modeServices(row),agent=ctx.agents?.get?.(row.sessionId),plan=values(row).plan;
    const goal=agent&&services.goals?services.goals.get(agent):values(row).goal;
    return {collaborationMode:plan?.active?'plan':'default',
      goal:goal?{goalId:goal.id,revision:goal.revision,objective:goal.objective,
        status:goal.phase==='active'&&goal.activation!=='armed'?'paused':goal.phase,
        roundsStarted:goal.roundsStarted,maxGoalRounds:goal.maxGoalRounds}:null};
  };
  const workspaceCapable=()=>typeof ctx.workspaceRegistry.get==='function'&&typeof ctx.workspaceRegistry.list==='function'&&ctx.workspaceRegistry.list().every(w=>Array.isArray(w.sessionIds)&&typeof w.attachSession==='function'&&typeof w.detachSession==='function');
  const nativeSession=id=>ctx.get?.('sessions')?.get?.(id)||ctx.agents?.get?.(id)?.session;
  const capabilities=row=>({tasks:typeof ctx.get?.('jobs')?.list==='function'||!!ctx.get?.('subagents'),compact:!!row&&row.running===false&&modeServices(row).compact,resume:!!row&&typeof ctx.agents?.get==='function'&&!ctx.agents.get(row.sessionId)&&typeof ctx.sessionController.resolveAgent==='function',plan:!!modeServices(row).plan,goal:!!modeServices(row).goals,goalEdit:true,goalPause:!row||workState(row).goal?.status==='active',goalResume:!!row&&['paused','blocked'].includes(workState(row).goal?.status),goalPauseStops:true,models:typeof ctx.sessionController.selectModel==='function',manualModels:typeof ctx.sessionController.selectModel==='function',manualEffort:typeof ctx.sessionController.selectModel==='function',effort:typeof ctx.sessionController.selectModel==='function',permissions:typeof ctx.permissionPresets?.set==='function',attachments:typeof ctx.sessionController.prompt==='function',skills:typeof ctx.sessionSkillCatalog?.list==='function',queue:typeof ctx.sessionController.prompt==='function',steer:typeof ctx.sessionController.prompt==='function',fork:typeof ctx.sessionController.fork==='function',rename:!!row&&!!nativeSession(row.sessionId)&&typeof ctx.get?.('sessionTitle')?.rename==='function',organization:workspaceCapable(),archive:typeof ctx.workspaceRegistry.archiveSession==='function'&&typeof ctx.workspaceRegistry.unarchiveSession==='function',unarchive:typeof ctx.workspaceRegistry.archiveSession==='function'&&typeof ctx.workspaceRegistry.unarchiveSession==='function'});
  const membership=row=>{
    if(!workspaceCapable())return {};
    const members=ctx.workspaceRegistry.list().filter(w=>w.sessionIds.includes(row.sessionId));
    return members.length<=1?{projectId:members[0]?.id||null,projectName:members[0]?.title||''}:{};
  };
  const contextUsage=row=>{
    const pressure=values(row).contextPressure||{},used=pressure.projectedTokens??pressure.pressureTokens,limit=pressure.contextWindow;
    return Number.isFinite(used)&&used>=0&&Number.isFinite(limit)&&limit>0
      ? {usedTokens:used,contextWindow:limit,pressureTokens:pressure.pressureTokens,estimated:Number.isFinite(pressure.projectedTokens)&&pressure.projectedTokens!==pressure.pressureTokens} : null;
  };
  const tasks=row=>{
    const jobs=ctx.get?.('jobs'),subagents=ctx.get?.('subagents');
    const result=(jobs?.list(row.sessionId)||[]).filter(job=>job.owner===row.sessionId).map(job=>({
      id:'job:'+job.id,title:job.label||job.id,kind:job.kind,status:job.status,
      cancellable:job.status==='running'&&typeof jobs.kill==='function'}));
    for(const child of values(row).subagentCatalog||[]){
      const agent=ctx.agents.get(child.id),running=agent?.status==='running';
      result.push({id:'child:'+child.id,title:child.label||child.id,kind:'subagent',
        status:running?'running':agent?.status==='idle'?'idle':'unknown',
        cancellable:running&&typeof subagents?.interrupt==='function'});
    }
    return result;
  };
  const permissionState=row=>{const catalog=ctx.permissionPresets.catalog(),mode=values(row).permissions?.currentValue||catalog.defaultPreset,option=catalog.options.find(o=>o.value===mode);return {permissionMode:mode,permissionLabel:localized(option?.label||option?.name)||mode};};
  const normalize=row=>{const v=values(row),m=v.modelSelection?.next||v.modelSelection?.lastUsed||ctx.get?.('agentDefaultModel')?.currentSelection()||{};return {id:row.sessionId,capabilities:capabilities(row),title:v.title||'新会话',cwd:row.cwd||'',backend:'deepseek',mode:'desktop',connectionState:typeof ctx.agents?.get!=='function'?'unknown':ctx.agents.get(row.sessionId)?'ready':'detached',status:typeof row.running==='boolean'?(row.running?'active':'idle'):'unknown',runtimeKnown:typeof row.running==='boolean',updatedAt:row.updatedAt,model:m.provider&&m.model?m.provider+'/'+m.model:'',effort:m.reasoningEffort||'',...membership(row),...permissionState(row),...workState(row),contextUsage:contextUsage(row),requests:[...questions.values()].filter(q=>q.sid===row.sessionId).map(q=>q.public)};};
  const organization=async row=>{
    if(!workspaceCapable())throw Error('当前 Harness 未提供已确认的原生项目接口');
    const cwd=await realpath(row.cwd),projects=[],members=[];
    for(const workspace of ctx.workspaceRegistry.list()){
      if(workspace.sessionIds.includes(row.sessionId))members.push(workspace.id);
      let path;try{path=await realpath(workspace.path);}catch{continue;}
      if(path===cwd)projects.push({id:workspace.id,title:workspace.title||workspace.path});
    }
    if(members.length>1)throw Error('原生项目成员关系不唯一，请先在原客户端核对');
    return {projectId:members[0]||null,projects,scope:'native',identity:{id:row.sessionId,cwd:row.cwd}};
  };
  const bounded=async promise=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('插件目录读取超时，请重试')),6000);})]);}finally{clearTimeout(timer);}};
  const localized=value=>typeof value==='string'?value:value?.['zh-CN']||value?.zh||value?.en||'';
  const catalog=async(sid,section='all')=>{
    const row=normalize(await find(sid)),result={currentModel:row.model,currentEffort:row.effort,capabilities:row.capabilities};
    if(section!=='plugins'){
      const raw=await bounded(ctx.sessionController.modelCatalog());
      result.models=raw.groups.flatMap(g=>g.models.map(m=>({id:g.id+'/'+m.id,name:m.name,efforts:(m.reasoning?.efforts||[]).map(e=>e.id),defaultEffort:m.reasoning?.defaultEffort})));
      if(!result.currentModel&&raw.default?.provider&&raw.default?.model){result.currentModel=raw.default.provider+'/'+raw.default.model;result.currentEffort=raw.default.reasoningEffort||'';}
      result.capabilities.effort=result.capabilities.effort&&result.models.some(m=>m.efforts.length>0);
    }
    if(section!=='models'){
      const skills=await bounded(ctx.sessionSkillCatalog.list({sessionId:sid},signal()));
      result.skills=skills.skills.map(s=>({id:s.name,name:s.name,kind:'skill',description:s.description,provider:s.provider,selectable:s.invocation?.userInvocable!==false}));
      const inventory=ctx.get?.('pluginInventory')||ctx.pluginInventory;
      if(inventory){
        try{
          const snapshot=await bounded(inventory.list()),packages=new Map();
          for(const entry of [...(snapshot.entries||[]),...(snapshot.agentPresets||[]).flatMap(p=>p.rows.map(r=>({...r,preset:p.name||p.id})))]){
            const key=entry.moduleName;if(!key)continue;
            const status=entry.fiberPhase==='active'?'运行中':entry.fiberPhase==='failed'?'加载失败':entry.enabled===false?'已停用':'按会话启用';
            const old=packages.get(key);if(old?.status==='运行中')continue;
            packages.set(key,{id:'installed:'+key,name:localized(entry.meta?.title)||key.replace('@deepseek-ai/dsh-',''),description:localized(entry.meta?.description),kind:'plugin',selectable:false,status});
          }
          result.skills.push(...[...packages.values()].sort((a,b)=>a.name.localeCompare(b.name)));
        }catch(error){result.pluginError=String(error.message||error);}
      }else result.pluginError='当前 Harness 未提供插件清单，请更新或重新打开 Harness';
    }
    return result;
  };
  const account=async sid=>{
    // Report provenance and public reference names, never resolve a key.
    const service=ctx.get?.('deepseekAccount'),settings=ctx.get?.('settings'),credentials=ctx.get?.('credentials'),llm=ctx.get?.('llm'),configured=new Map();
    if(service&&(await bounded(service.getState())).status==='credential-stored')configured.set('deepseek-account',{credentialSource:'file'});
    if(settings&&credentials&&llm){
      const namespaces=settings.describe({redactSecrets:true});
      for(const provider of llm.listConfigurableProviders()){
        let profile=namespaces.find(n=>n.ns===provider.settingsNs)?.value;
        for(const key of provider.settingsPath)profile=profile&&typeof profile==='object'?profile[key]:undefined;
        const ref=profile?.apiKeyEnv;
        if(typeof ref==='string'&&/^[A-Za-z_][A-Za-z0-9_]*$/.test(ref)){
          const credential=await bounded(credentials.describe(ref));
          if(credential.configured)configured.set(provider.provider,{credentialRef:ref,credentialSource:['file','env','project-env','user-env'].includes(credential.source)?credential.source:'unknown'});
        }
      }
    }
    const raw=await bounded(ctx.sessionController.modelCatalog()),sources=raw.groups.filter(g=>configured.has(g.id)&&g.models.length>0).map(g=>({id:g.id,kind:g.id==='deepseek-account'?'account':'api',label:g.name||g.id,...configured.get(g.id)}));
    const groups=raw.groups.filter(g=>configured.has(g.id)).map(g=>({id:g.id,name:g.name||g.id,models:g.models.map(m=>({id:g.id+'/'+m.id,name:m.name,efforts:(m.reasoning?.efforts||[]).map(e=>e.id),defaultEffort:m.reasoning?.defaultEffort}))}));
    const session=sid?normalize(await find(sid)):null,provider=session?.model.split('/')[0]||raw.default?.provider;
    const current=sources.find(s=>s.id===provider)||null;
    return {provider:'deepseek',readOnly:true,canManage:false,credentialSourcesVersion:1,status:sources.length?'configured':'unconfigured',current,sources,groups,models:groups.flatMap(g=>g.models),
      ...(raw.default?.provider&&raw.default?.model?{currentModel:raw.default.provider+'/'+raw.default.model,currentEffort:raw.default.reasoningEffort||''}:{})};
  };
  // Desktop and mobile share one pending request. First explicit answer wins.
  for (const event of ['user-questions/request','approval/request']) {
    ctx.on(event,async(req,next)=>{
      if(!req.agent||Object.isFrozen(req))return next();
      if(req.signal?.aborted) return event==='approval/request'?'cancelled':next();
      const id=randomUUID(),controller=new AbortController(),original=req.signal;
      req.signal=original?AbortSignal.any([original,controller.signal]):controller.signal;
      let resolveMobile,rejectMobile;
      const mobile=new Promise((resolve,reject)=>{resolveMobile=resolve;rejectMobile=reject;});
      const approval=event==='approval/request';
      questions.set(id,{sid:req.agent.id,approval,public:{id,tool:approval?req.toolName:'提问',needsInput:!approval,input:approval?{reason:req.reason||''}:{questions:req.questions}},resolve:resolveMobile});
      const abort=()=>rejectMobile(Error('请求已取消'));original?.addEventListener('abort',abort,{once:true});
      // A missing desktop answerer is not a user's denial.
      const desktop=Promise.resolve().then(next).then(value=>value==='unavailable'?mobile:value);
      try{return await Promise.race([desktop,mobile]);}
      finally{questions.delete(id);controller.abort();original?.removeEventListener('abort',abort);req.signal=original;}
    },{prepend:true,global:true});
  }
  function lifecycle(){
      // The runtime registry includes child agents omitted from the chat list.
      // This snapshot is requested only before a native quit/restart.
      if(typeof ctx.agents.list!=='function')return {connected:true,bridgeRevision:8,complete:false,sessions:[]};
      const agents=ctx.agents.list();
      if(!Array.isArray(agents))return {connected:true,bridgeRevision:8,complete:false,sessions:[]};
      const sessions=agents.map(agent=>{
        let goal;try{goal=modeServices({sessionId:agent.id}).goals?.get(agent);}catch{return {id:agent.id,status:'unknown',runtimeKnown:false,requests:[]};}
        const armed=goal?.phase==='active'&&goal.activation==='armed';
        return {id:agent.id,status:armed||agent.status==='running'?'active':agent.status==='idle'?'idle':'unknown',runtimeKnown:['idle','running'].includes(agent.status),requests:[...questions.values()].filter(q=>q.sid===agent.id).map(q=>q.public)};
      });
      for(const question of questions.values())if(!sessions.some(s=>s.id===question.sid))sessions.push({id:question.sid,status:'waiting',runtimeKnown:true,requests:[question.public]});
      return {connected:true,bridgeRevision:8,complete:sessions.every(s=>typeof s.id==='string'&&s.id.length>0),sessions};
  }
  const idle=runtime=>runtime.complete&&runtime.sessions.every(s=>s.runtimeKnown&&s.status==='idle'&&!s.requests.length);
  const quitFailed=error=>{quitting=false;ctx.logger?.error?.('Harness native quit failed: '+String(error.message||error));};
  function finishQuit(){
    try{
      const exit=ctx.get?.('appExit');
      // No await between this snapshot and appExit: native work can arrive
      // while the HTTP acceptance is flushing, even after mobile writes stop.
      if(!quitting||activeMutations||!idle(lifecycle())||typeof exit!=='function'){quitting=false;return;}
      Promise.resolve(exit(0)).catch(quitFailed);
    }catch(error){quitFailed(error);}
  }
  async function dispatch(action,sid,body){
    if(action==='lifecycle')return lifecycle();
    if(action==='quit'){
      if(quitting)throw Error('Harness 正在退出，请稍后重试');
      if(body.expectedPid!==process.pid||body.expectedGeneration!==generation)throw Error('Harness 进程已变化，请重新检查后再退出');
      if(typeof ctx.get?.('appExit')!=='function')throw Error('当前 Harness 未提供正常退出接口，请在电脑端退出');
      if(activeMutations||!idle(lifecycle()))throw Error('有任务运行、等待确认或状态未知，请先在电脑端检查后再退出');
      quitting=true;
      return {status:'accepted',pid:process.pid,generation};
    }
    if(action==='status')return {connected:true,bridgeRevision:11,...config.plugin?{plugin:config.plugin}:{},projectDirectories:true,createModels:true,nativeQuit:typeof ctx.get?.('appExit')==='function',configured:(await account()).status==='configured'};
    if(action==='account')return account(sid);
    if(action==='list')return {connected:true,sessions:(await list()).filter(r=>r.origin!=='subagent').map(r=>({...normalize(r),archived:(ctx.workspaceRegistry.archivedSessionIds||[]).includes(r.sessionId)})).sort((a,b)=>Number(a.archived)-Number(b.archived))};
    if(action==='projects'){const seen=new Map();for(const row of await list())if(row.cwd)seen.set(row.cwd,{key:row.cwd,cwd:row.cwd,name:row.cwd.replaceAll('\\','/').split('/').pop()});for(const w of ctx.workspaceRegistry.list())seen.set(w.path,{key:w.path,cwd:w.path,name:w.title||w.name||w.path,workspaceId:w.id});return {projects:[...seen.values()]};}
    if(action==='create'){
      let project;
      if(body.projectDirectory){
        const expected=body.projectDirectory,cwd=await realpath(expected.cwd),identity=await stat(cwd);
        if(!identity.isDirectory()||cwd!==expected.cwd||identity.dev!==expected.device||identity.ino!==expected.inode)throw Error('项目目录已变化，请重新选择目录');
        project={cwd};
      }else project=(await dispatch('projects')).projects.find(p=>p.key===body.projectKey);
      if(!project)throw Error('请选择项目目录');
      // cwd alone deliberately creates an ungrouped native session. Use the
      // registry's durable workspace identity so Desktop owns membership too.
      if(typeof ctx.workspaceRegistry.resolveByPath!=='function'||typeof ctx.workspaceRegistry.create!=='function')throw Error('当前 Harness 不支持原生项目关联，请更新 Harness 后重试');
      const canonical=await realpath(project.cwd);
      const workspace=await ctx.workspaceRegistry.resolveByPath(canonical)||await ctx.workspaceRegistry.create(canonical);
      if(!workspace?.id||workspace.path!==canonical)throw Error('原生项目目录未确认，请在 Harness 检查工作区');
      const result=await ctx.sessionController.create({sessionId:'session-'+body.id,workspaceId:workspace.id});
      if(body.model!==undefined)await dispatch('settings',result.sessionId,{model:body.model,
        ...(body.effort!==undefined?{effort:body.effort}:{}),...(body.manualModel?{manualModel:true}:{}),...(body.manualEffort?{manualEffort:true}:{})});
      if(body.title)await ctx.sessionController.rename({sessionId:result.sessionId,title:body.title});
      return {status:'ready',result:{id:result.sessionId,title:body.title||'新会话'}};
    }
    const row=await find(sid);
    if(action==='fork'){
      if(!capabilities().fork)throw Error('当前 Harness 未提供原生分支接口');
      const atSeq=Number(body.messageId);
      if(typeof body.messageId!=='string'||!/^\d+$/.test(body.messageId)||!Number.isSafeInteger(atSeq)||atSeq<0)throw Error('分支消息标识无效');
      const page=await ctx.sessionController.page({address:{kind:'session',sessionId:sid},throughSeq:atSeq,maxMessages:1},signal());
      const anchor=page.records?.find(record=>record.event?.seq===atSeq)?.event;
      if(anchor?.type!=='turn/end'||anchor.data?.reason?.kind!=='completed')throw Error('原回复尚未完成或已不可用，请刷新聊天后重试');
      const child=await ctx.sessionController.fork({sessionId:sid,atSeq,allowMigration:false});
      if(typeof child?.sessionId!=='string'||!child.sessionId||child.sessionId===sid)throw Error('Harness 尚未确认分支结果，请在桌面核对');
      return {status:'ready',result:{id:child.sessionId}};
    }
    if(action==='rename'){
      const title=typeof body.title==='string'?body.title.trim():'';
      if(!title||[...title].length>120||/[\u0000-\u001f\u007f]/u.test(body.title))throw Error('标题为 1–120 字，不能包含控制字符');
      // The native Session store can retain a loaded chat without an Agent.
      // Never use sessionController.rename here: it resumes a cold Agent.
      const session=nativeSession(sid),titles=ctx.get?.('sessionTitle');
      if(!session||session.id!==sid||typeof titles?.rename!=='function')return {notSent:true,message:'请先连接原会话，再执行原生改名'};
      const limit=titles.config?.maxTitleBytes;
      if(!Number.isInteger(limit)||limit<=0)return {notSent:true,message:'无法核验原客户端的标题长度限制，尚未执行改名'};
      if(title.replace(/\s+/gu,' ')!==title||/[\u007f-\u009f\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]/u.test(title))return {notSent:true,message:'名称包含原客户端会改写的空白或不可见字符，请调整后重试'};
      if(Buffer.byteLength(title,'utf8')>limit)return {notSent:true,message:'名称超出原客户端的长度限制，请缩短后重试'};
      const accepted=await titles.rename(session,title),fresh=await find(sid);
      if(accepted?.title!==title||values(fresh).title!==title||fresh.cwd!==row.cwd||nativeSession(sid)!==session)throw Error('改名结果待核对，请检查原客户端；不会重复提交');
      return {status:'confirmed',scope:'native',result:{id:sid,title},...(Number.isSafeInteger(accepted.eventSeq)?{nativeEventSeq:accepted.eventSeq}:{})};
    }
    if(action==='organization'){
      const before=await organization(row);
      if(!body.operation||body.operation==='read')return before;
      if(body.operation!=='set-project'||body.projectId!==null&&typeof body.projectId!=='string')throw Error('项目设置无效');
      if(body.projectId!==null&&!before.projects.some(w=>w.id===body.projectId))return {notSent:true,message:'项目必须与会话原工作目录一致'};
      if(before.projectId!==body.projectId){
        // Tauri variants can relax attachSession's cwd check. Enforce the
        // original directory here even when the native registry accepts more.
        const target=body.projectId===null?null:ctx.workspaceRegistry.get(body.projectId);
        if(target&&await realpath(target.path)!==await realpath(row.cwd))return {notSent:true,message:'项目目录已变化，未执行修改'};
        if(body.projectId!==null&&!target)return {notSent:true,message:'项目已不存在，未执行修改'};
        if(target)await target.attachSession(sid);
        if(before.projectId)await ctx.workspaceRegistry.get(before.projectId).detachSession(sid);
      }
      const fresh=await organization(await find(sid));
      if(fresh.projectId!==body.projectId||fresh.identity.cwd!==row.cwd)throw Error('项目修改结果待核对，请检查原客户端；不会重复提交');
      return {...fresh,status:'confirmed'};
    }
    if(action==='archive-state')return {...normalize(row),archived:(ctx.workspaceRegistry.archivedSessionIds||[]).includes(sid),_archiveFlags:{[sid]:(ctx.workspaceRegistry.archivedSessionIds||[]).includes(sid)},archiveSafe:row.running===false&&![...questions.values()].some(q=>q.sid===sid)};
    if(action==='archive'){
      if(typeof body.archived!=='boolean'||!capabilities().archive)throw Error('当前接入尚未确认原生归档和恢复能力');
      if(body.disconnect!=null&&typeof body.disconnect!=='boolean'||body.disconnect&&!body.archived)throw Error('归档设置无效');
      if(body.disconnect){
        if(typeof ctx.parallel!=='function'||typeof ctx.waterfall!=='function'||typeof row.running!=='boolean')return {archiveNotSent:true,message:'无法确认可停止的原生任务，未停止或归档'};
        // Native stop providers cover this session's turn, jobs, schedules and descendants.
        // Do not use stopActivity: that native option writes the archive flag BEFORE stopping.
        await ctx.parallel('workspace/session-stop',{sessionId:sid});
        const deadline=Date.now()+15000;
        while(true){
          const activity=await ctx.waterfall('workspace/session-activity',{sessionId:sid},()=>Promise.resolve([]));
          const fresh=await find(sid);
          if(Array.isArray(activity)&&!activity.length&&fresh.running===false&&![...questions.values()].some(q=>q.sid===sid))break;
          if(Date.now()>=deadline)throw Error('停止结果待核对，尚未发送归档请求');
          await new Promise(resolve=>setTimeout(resolve,100));
        }
      }else if(row.running!==false||[...questions.values()].some(q=>q.sid===sid))return {archiveNotSent:true,message:'聊天有运行、审批或未知状态，请先在原客户端处理'};
      try{if(body.archived)await ctx.workspaceRegistry.archiveSession(sid,{});else await ctx.workspaceRegistry.unarchiveSession(sid);}
      catch(error){if(error?.name==='WorkspaceActiveSessionError'&&error.sessionId===sid&&Array.isArray(error.activity))return {archiveNotSent:true,message:'聊天或子智能体有运行中的工作，请先在原客户端处理'};throw error;}
      return {status:'accepted'};
    }
    if(action==='open')return {ok:true};
    if(action==='resume'){const found=await ctx.sessionController.resolveAgent(sid);if(found.error)throw found.error;if(found.agent?.id!==sid)throw Error('Native session identity changed');return {status:'ready'};}
    if(action==='image')return readHistoryImage(ctx,sid,body.source,signal());
    if(action==='detail'){const page=await readHistoryPage(ctx.sessionController,row,body,signal());return {session:normalize(row),capabilities:capabilities(row),records:await historyRecords({sessionId:sid,get:name=>ctx.get?.(name)},page.rows,signal()),history:page.history,steps:values(row).todos||[],connected:true};}
    if(action==='catalog')return catalog(sid,body.section||'all');
    if(action==='tasks')return {tasks:tasks(row)};
    if(action==='stop-task'){
      const task=tasks(row).find(task=>task.id===body.taskId);
      if(!task?.cancellable)throw Error('后台任务已结束或不支持独立停止');
      if(task.id.startsWith('job:'))await ctx.get('jobs').kill(task.id.slice(4),sid,'user');
      else await ctx.get('subagents').interrupt(task.id.slice(6),{kind:'user',parentSessionId:sid});
      return {status:'accepted'};
    }
    if(action==='compact'){
      const agent=ctx.agents.get(sid),services=modeServices(row);
      if(!agent||row.running!==false||!services.compact||[...questions.values()].some(q=>q.sid===sid))throw Error('请等待当前任务结束并连接原会话后再压缩上下文');
      const result=await services.commands.execute(agent,'/compact',[],signal());
      if(result?.result?.kind!=='success')throw Error(result?.result?.text||'原生上下文压缩未确认');
      return {status:'accepted',message:result.result.text};
    }
    if(action==='work-mode'||action==='goal'){
      const services=modeServices(row);
      if(action==='work-mode'&&(!services.plan||!['default','plan'].includes(body.mode))||action==='goal'&&!services.goals)throw Error('当前 Harness 未提供此原生工作模式');
      if(action==='work-mode'&&row.running!==false)throw Error('请等待当前任务结束后再切换模式');
      if(action==='goal'&&!['set','pause','resume','clear','edit'].includes(body.operation))throw Error('目标操作无效');
      // Explicit controls may restore the native owner; passive reads above never do.
      const found=await ctx.sessionController.resolveAgent(sid);if(found.error)throw found.error;
      const agent=found.agent;if(agent?.id!==sid)throw Error('Native session identity changed');
      if(action==='work-mode'){
        const result=await services.commands.execute(agent,body.mode==='plan'?'/plan':'/plan off',[],signal());
        if(result?.result?.kind!=='success')throw Error(result?.result?.text||'原生计划模式未确认');
        return {status:'accepted'};
      }
      const goal=services.goals.get(agent),expected=body.expected;
      if(body.operation!=='set'&&(!goal||expected?.goalId!==goal.id||expected?.revision!==goal.revision))throw Error('目标已变化，请刷新后操作');
      if(['set','resume'].includes(body.operation)&&(row.running!==false||[...questions.values()].some(q=>q.sid===sid)))throw Error('请等待当前任务结束后再开启目标');
      if(['set','edit'].includes(body.operation)&&(typeof body.objective!=='string'||!body.objective.trim()||body.objective.length>4000))throw Error('请输入 1–4000 字的目标');
      if(['set','resume'].includes(body.operation)&&workState(row).collaborationMode==='plan')throw Error('请先退出计划模式');
      const ref=goal?{id:goal.id,revision:goal.revision}:null;
      if(body.operation==='set')services.goals.create(agent,{objective:body.objective});
      else if(body.operation==='edit')services.goals.edit(agent,ref,{objective:body.objective});
      else services.goals[body.operation](agent,ref);
      return {status:'accepted'};
    }
    if(action==='access'){const presets=ctx.permissionPresets.catalog(),options=presets.options;if(body.mode){if(!options.some(o=>o.value===body.mode))throw Error('权限选项无效');await ctx.sessionController.create({sessionId:sid,cwd:row.cwd});const agent=ctx.agents.get(sid);if(!agent)throw Error('当前会话没有活动智能体');ctx.permissionPresets.set(agent.session,body.mode);}return {status:'accepted',mode:body.mode||values(row).permissions?.currentValue||presets.defaultPreset,modes:options.map(o=>o.value),options};}
    if(action==='settings'){const c=body.manualModel===true?{models:[],currentModel:'',currentEffort:''}:await catalog(sid,'models'),selected=body.model||c.currentModel,m=c.models.find(m=>m.id===selected);if(typeof selected!=='string'||!/^([^\s/\x00-\x1f\x7f]{1,128})\/([^\s\x00-\x1f\x7f]{1,256})$/.test(selected))throw Error('请输入 provider/model 格式的模型名称');const effort=body.effort??(selected===c.currentModel?c.currentEffort:m?.defaultEffort);if(effort&&(typeof effort!=='string'||!/^[A-Za-z0-9_-]{1,64}$/.test(effort)))throw Error('请输入有效的思考强度');if(effort&&body.manualEffort!==true&&m&&!m.efforts.includes(effort))throw Error('该模型不支持所选思考强度');const slash=selected.indexOf('/'),result=await ctx.sessionController.selectModel({sessionId:sid,provider:selected.slice(0,slash),model:selected.slice(slash+1),reasoningEffort:effort||undefined}),actual=result.selected;if(!actual||actual.provider+'/'+actual.model!==selected||(body.effort&&actual.reasoningEffort!==body.effort))throw Error('Harness 未确认模型或思考强度设置，请刷新检查');return {status:'accepted',model:actual.provider+'/'+actual.model,effort:actual.reasoningEffort||''};}
    if(action==='stop'){await ctx.sessionController.cancel({sessionId:sid});return {status:'accepted'};}
    if(action==='send'){
      if(body.attachments?.length||body.resolvedFiles?.length)throw Error('此应用的消息附件仅支持图片；其他文件请使用文件传输。');
      let text=body.text||'';if(body.plugins?.length){const c=await catalog(sid,'plugins');if(body.plugins.some(id=>!c.skills.some(s=>s.id===id&&s.selectable!==false)))throw Error('插件已不可用');text='请使用以下已安装技能：'+body.plugins.join(', ')+'\n\n'+text;}
      const content=text?[{type:'text',text}]:[];
      for(const image of body.resolvedImages||[]){const match=/^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$/.exec(image.url);if(!match)throw Error('图片格式无效');content.push({type:'image',mediaType:match[1],data:match[2],...(image.name?{name:image.name}:{})});}
      if(!content.length)throw Error('请输入内容');
      const mode=body.mode==='steer'&&row.running?'steer':'queue',result=await ctx.sessionController.prompt({sessionId:sid,requestId:body.id,mode,content},signal());
      if(result?.accepted!==true)throw Error('Harness 未确认接收消息，请在桌面核对');
      return {status:row.running&&mode==='queue'?'queued':'accepted'};
    }
    if(action==='respond'){const pending=questions.get(body.requestId);if(!pending||pending.sid!==sid)throw Error('提问已过期');if(pending.approval){const outcome={accept:'allowed-once',decline:'rejected'}[body.decision];if(!outcome)throw Error('请选择本次允许或拒绝');questions.delete(body.requestId);pending.resolve(outcome);return {status:'accepted'};}const answers=pending.public.input.questions.map(q=>{const selected=body.answers?.[q.id]?.answers;if(!Array.isArray(selected)||!selected.length||selected.some(s=>typeof s!=='string'||s.length>20000))throw Error('请回答每一项问题');const labels=(q.options||[]).map(o=>o.label);return {id:q.id,selected:selected.filter(s=>labels.includes(s)),custom:selected.filter(s=>!labels.includes(s)).join('\n')};});questions.delete(body.requestId);pending.resolve({answers});return {status:'accepted'};}
    throw Error('不支持的 DeepSeek 操作');
  }
  const server=createServer(async(req,res)=>{try{
    const credential=String(req.headers.authorization||'').replace(/^Bearer /,'');const a=Buffer.from(credential),b=Buffer.from(token);
    if(req.method!=='POST'||req.url!=='/mobile'||req.headers.origin||a.length!==b.length||!timingSafeEqual(a,b)){res.writeHead(403);res.end();return;}
    let size=0,chunks=[];for await(const chunk of req){size+=chunk.length;if(size>maxRequestBytes)throw Error('请求过大');chunks.push(chunk);}const {action,sid,body={}}=JSON.parse(Buffer.concat(chunks));
    const mutating=['create','fork','rename','archive','stop','send','respond','settings','resume','work-mode','goal','compact','stop-task'].includes(action)||(action==='access'&&Boolean(body?.mode))||(action==='organization'&&body?.operation==='set-project');
    if(mutating&&quitting)throw Error('Harness 正在退出，请稍后重试');
    let result;
    if(mutating)activeMutations++;
    try{result=await dispatch(action,sid,body);}finally{if(mutating)activeMutations--;}
    res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});
    // Flush acceptance before native teardown disposes this connector's server.
    res.end(JSON.stringify(result),action==='quit'?finishQuit:undefined);
  }catch(error){res.writeHead(400,{'content-type':'application/json'});res.end(JSON.stringify({error:String(error.message||error)}));}});
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const port=server.address().port,generation=randomUUID();await writeFile(endpoint+'.tmp',JSON.stringify({port,pid:process.pid,generation}),{mode:0o600});await rename(endpoint+'.tmp',endpoint);
  ctx.effect(()=>async()=>{
    server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    // Another host may already have published its endpoint in this shared file.
    // Retiring an older fiber must only remove the endpoint it created.
    try{const current=JSON.parse(await readFile(endpoint,'utf8'));if(current.generation===generation)await unlink(endpoint);}catch{}
  });
}
