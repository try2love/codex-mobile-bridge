// Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
// Harness plugin: authenticated loopback adapter over the installed session services.
import {createServer} from 'node:http';
import {readFile,writeFile,rename,unlink} from 'node:fs/promises';
import {timingSafeEqual,randomUUID} from 'node:crypto';
export const inject=['sessionController','sessionSkillCatalog','permissionPresets','agents','workspaceRegistry'];
export async function apply(ctx,config){
  const {token,endpoint}=JSON.parse(await readFile(config.configPath,'utf8'));
  // The gateway accepts at most 100 MiB of decoded images plus small JSON fields.
  const maxRequestBytes=Math.ceil(100*1024*1024*4/3)+1024*1024;
  const questions=new Map(),history=new Map();
  let quitting=false,activeMutations=0;
  const signal=()=>new AbortController().signal;
  const list=async()=> (await ctx.sessionController.list({},signal())).items;
  const find=async id=>{const row=(await list()).find(r=>r.sessionId===id);if(!row)throw Error('DeepSeek 会话不存在');return row;};
  const values=row=>row.projections?.values||{};
  const capabilities=()=>({models:typeof ctx.sessionController.selectModel==='function',effort:typeof ctx.sessionController.selectModel==='function',permissions:typeof ctx.permissionPresets?.set==='function',attachments:typeof ctx.sessionController.prompt==='function',skills:typeof ctx.sessionSkillCatalog?.list==='function',queue:typeof ctx.sessionController.prompt==='function',steer:typeof ctx.sessionController.prompt==='function'});
  const normalize=row=>{const v=values(row),m=v.modelSelection?.next||v.modelSelection?.lastUsed||ctx.get?.('agentDefaultModel')?.currentSelection()||{};return {id:row.sessionId,title:v.title||'新会话',cwd:row.cwd||'',backend:'deepseek',mode:'desktop',status:typeof row.running==='boolean'?(row.running?'active':'idle'):'unknown',runtimeKnown:typeof row.running==='boolean',updatedAt:row.updatedAt,model:m.provider&&m.model?m.provider+'/'+m.model:'',effort:m.reasoningEffort||'',requests:[...questions.values()].filter(q=>q.sid===row.sessionId).map(q=>q.public)};};
  const records=async row=>{
    const projection=await ctx.sessionController.projections({sessionId:row.sessionId},signal());
    const seq=projection.asOfSeq??row.projections?.asOfSeq??0;
    const old=history.get(row.sessionId);if(old?.seq===seq)return old.records;
    let before,all=[],last=old?.records.at(-1)?.event.seq??-1;
    for(let page=0;page<100;page++){
      const got=await ctx.sessionController.page({address:{kind:'session',sessionId:row.sessionId},throughSeq:seq,...before===undefined?{}:{beforeSeq:before},maxMessages:150},signal());
      const batch=got.records||[];all.unshift(...batch.filter(r=>r.event.seq>last));
      if(!got.hasMore||!batch.length||batch[0].event.seq<=last)break;
      before=batch[0].event.seq;
    }
    all=[...(old?.records||[]),...all];history.set(row.sessionId,{seq,records:all});if(history.size>8)history.delete(history.keys().next().value);return all;
  };
  const bounded=async promise=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('插件目录读取超时，请重试')),6000);})]);}finally{clearTimeout(timer);}};
  const localized=value=>typeof value==='string'?value:value?.['zh-CN']||value?.zh||value?.en||'';
  const catalog=async(sid,section='all')=>{
    const row=normalize(await find(sid)),result={currentModel:row.model,currentEffort:row.effort,capabilities:capabilities()};
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
    // Only presence and public provider names leave the desktop; never resolve a key.
    const service=ctx.get?.('deepseekAccount'),settings=ctx.get?.('settings'),credentials=ctx.get?.('credentials'),llm=ctx.get?.('llm'),configured=new Set();
    if(service&&(await bounded(service.getState())).status==='credential-stored')configured.add('deepseek-account');
    if(settings&&credentials&&llm){
      const namespaces=settings.describe({redactSecrets:true});
      for(const provider of llm.listConfigurableProviders()){
        let profile=namespaces.find(n=>n.ns===provider.settingsNs)?.value;
        for(const key of provider.settingsPath)profile=profile&&typeof profile==='object'?profile[key]:undefined;
        const ref=profile?.apiKeyEnv;
        if(typeof ref==='string'&&/^[A-Za-z_][A-Za-z0-9_]*$/.test(ref)&&(await bounded(credentials.describe(ref))).configured)configured.add(provider.provider);
      }
    }
    const raw=await bounded(ctx.sessionController.modelCatalog()),sources=raw.groups.filter(g=>configured.has(g.id)&&g.models.length>0).map(g=>({id:g.id,kind:g.id==='deepseek-account'?'account':'api',label:g.name||g.id}));
    const groups=raw.groups.filter(g=>configured.has(g.id)).map(g=>({id:g.id,name:g.name||g.id,models:g.models.map(m=>({id:g.id+'/'+m.id,name:m.name,efforts:(m.reasoning?.efforts||[]).map(e=>e.id),defaultEffort:m.reasoning?.defaultEffort}))}));
    const session=sid?normalize(await find(sid)):null,provider=session?.model.split('/')[0]||raw.default?.provider;
    const current=sources.find(s=>s.id===provider)||null;
    return {provider:'deepseek',readOnly:true,canManage:false,status:sources.length?'configured':'unconfigured',current,sources,groups,models:groups.flatMap(g=>g.models)};
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
      if(typeof ctx.agents.list!=='function')return {connected:true,bridgeRevision:3,complete:false,sessions:[]};
      const agents=ctx.agents.list();
      if(!Array.isArray(agents))return {connected:true,bridgeRevision:3,complete:false,sessions:[]};
      const sessions=agents.map(agent=>({id:agent.id,status:agent.status==='running'?'active':agent.status==='idle'?'idle':'unknown',runtimeKnown:['idle','running'].includes(agent.status),requests:[...questions.values()].filter(q=>q.sid===agent.id).map(q=>q.public)}));
      for(const question of questions.values())if(!sessions.some(s=>s.id===question.sid))sessions.push({id:question.sid,status:'waiting',runtimeKnown:true,requests:[question.public]});
      return {connected:true,bridgeRevision:3,complete:sessions.every(s=>typeof s.id==='string'&&s.id.length>0),sessions};
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
    if(action==='status')return {connected:true,bridgeRevision:3,nativeQuit:typeof ctx.get?.('appExit')==='function',configured:(await account()).status==='configured'};
    if(action==='account')return account(sid);
    if(action==='list')return {connected:true,sessions:(await list()).filter(r=>!r.parentSessionId).map(r=>({...normalize(r),archived:(ctx.workspaceRegistry.archivedSessionIds||[]).includes(r.sessionId)})).sort((a,b)=>Number(a.archived)-Number(b.archived))};
    if(action==='projects'){const seen=new Map();for(const row of await list())if(row.cwd)seen.set(row.cwd,{key:row.cwd,cwd:row.cwd,name:row.cwd.replaceAll('\\','/').split('/').pop()});for(const w of ctx.workspaceRegistry.list())seen.set(w.path,{key:w.path,cwd:w.path,name:w.name||w.path});return {projects:[...seen.values()]};}
    if(action==='create'){const project=(await dispatch('projects')).projects.find(p=>p.key===body.projectKey);if(!project)throw Error('请选择现有项目');const result=await ctx.sessionController.create({sessionId:'session-'+body.id,cwd:project.cwd});if(body.title)await ctx.sessionController.rename({sessionId:result.sessionId,title:body.title});return {status:'ready',result:{id:result.sessionId,title:body.title||'新会话'}};}
    const row=await find(sid);
    if(action==='rename'){const title=String(body.title||'').trim();if(!title||title.length>120)throw Error('标题为 1–120 字');await ctx.sessionController.rename({sessionId:sid,title});return {status:'accepted'};}
    if(action==='archive'){if(body.confirmed!==true)throw Error('请确认归档');await ctx.workspaceRegistry.archiveSession(sid,{stopActivity:true});return {status:'accepted'};}
    if(action==='open')return {ok:true};
    if(action==='detail')return {session:normalize(row),capabilities:capabilities(),records:(await records(row)).filter(r=>['user/message','assistant/message','tool/call','tool/result','todo/write','turn/start','turn/end'].includes(r.event.type)),steps:values(row).todos||[],connected:true};
    if(action==='catalog')return catalog(sid,body.section||'all');
    if(action==='access'){const presets=ctx.permissionPresets.catalog(),options=presets.options;if(body.mode){if(!options.some(o=>o.value===body.mode))throw Error('权限选项无效');await ctx.sessionController.create({sessionId:sid,cwd:row.cwd});const agent=ctx.agents.get(sid);if(!agent)throw Error('当前会话没有活动智能体');ctx.permissionPresets.set(agent.session,body.mode);}return {status:'accepted',mode:body.mode||values(row).permissions?.currentValue||presets.defaultPreset,modes:options.map(o=>o.value),options};}
    if(action==='settings'){const c=await catalog(sid,'models'),selected=body.model||c.currentModel,m=c.models.find(m=>m.id===selected);if(!m)throw Error('请选择可用模型');const effort=body.effort??(selected===c.currentModel?c.currentEffort:m.defaultEffort);if(effort&&!m.efforts.includes(effort))throw Error('该模型不支持所选思考强度');const slash=selected.indexOf('/'),result=await ctx.sessionController.selectModel({sessionId:sid,provider:selected.slice(0,slash),model:selected.slice(slash+1),reasoningEffort:effort||undefined}),actual=result.selected;return {status:'accepted',model:actual.provider+'/'+actual.model,effort:actual.reasoningEffort||''};}
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
    const mutating=['create','rename','archive','stop','send','respond','settings'].includes(action)||(action==='access'&&Boolean(body?.mode));
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
