'use strict';
// Desktop providers use the same conversation, composer and workspace presentation as Codex.
class DesktopSessionsView {
  constructor({root,select,request,notify,onSelect,csrf,onUnauthorized,pinPreferences=null,onSessionMenu=null,onOpenSessionMenu=null}) {
    Object.assign(this,{root,select,request,notify,onSelect,csrf,onUnauthorized,pinPreferences,onSessionMenu,onOpenSessionMenu});this.provider='codex';this.generation=0;this.detailRevision=0;this.modelSelectionRevision=0;this.drafts=new Map();this.provisionalDrafts=new Map();this.accountDrafts=new Map();this.skillSelections=new Map();this.composerDrafts=typeof ComposerDrafts==='function'?new ComposerDrafts():null;this.draftGateway='';this.draftScope=null;this.draftTouched=false;this.restoringDraft=false;this.pending=new Map();this.rows=[];this.messageNodes=new Map();this.providerViews=new Map();this.detailCache=new Map();this.activity=new SessionActivity({key:'bridge-desktop-session-activity'});try{this.collapsedGroups=new Set(JSON.parse(localStorage.getItem('bridge-desktop-session-groups')||'[]'));}catch{this.collapsedGroups=new Set();}
    this.readTasks=new Map();this.readEpoch=0;this.providerReadEpochs=new Map();this.listPrefetchAt=new Map();this.detailCacheSizes=new Map();this.detailCacheBytes=0;this.modelDialog=null;this.mermaidScope=window.BridgeMermaidCards?.createScope();
    this.listCacheIdentity='';this.listCacheHost='';this.modelCatalogs=new Map();
    this.container=this.node('section','desktop-sessions');root.append(this.container);this.container.hidden=true;
    this.list=this.node('aside','ds-list');this.main=this.node('main','ds-main');this.container.append(this.list,this.main);
    this.search=this.node('input','search');this.search.placeholder=BridgeI18n.t('搜索');this.label(this.search,'搜索聊天或项目');this.search.oninput=()=>this.renderList();
    const filters=this.node('div','list-filters');const display=this.node('label','list-mode');display.append(this.translated('span','显示方式'));this.listMode=this.node('select');this.label(this.listMode,'聊天列表显示方式');for(const [id,title] of [['recent','最近交互'],['project','按项目']])this.listMode.add(new Option(BridgeI18n.t(title),id));this.listMode.onchange=()=>this.renderList();display.append(this.listMode);
    this.surface=this.node('select','ds-surface');this.label(this.surface,'会话类型');this.surface.add(new Option(BridgeI18n.t('全部会话'),'all'));this.surface.value='all';this.surfaceSelection='all';this.surface.hidden=true;this.surface.onchange=()=>{this.surfaceSelection=this.surface.value;this.renderList();};const archive=this.node('label','archive');this.archived=this.node('input');this.archived.type='checkbox';this.archived.onchange=()=>this.renderList();archive.append(this.archived,this.translated('span','已归档'));filters.append(this.surface,display,archive);
    this.listStatus=this.node('p','muted ds-list-status');this.listStatus.setAttribute('role','status');this.connectionRetry=this.button('重试连接',async()=>{const provider=this.provider;if(this.retryingClient||!this.retryClient)return;this.retryingClient=provider;this.renderConnectionStatus();try{await this.retryClient(provider);}finally{this.retryingClient=null;this.renderConnectionStatus();}});this.connectionRetry.hidden=true;
    this.connectionInitialize=this.button('初始化连接',async()=>{const provider=this.provider;if(this.initializingClient||!this.initializeClient)return;this.initializingClient=provider;this.renderConnectionStatus();try{await this.initializeClient(provider);}finally{this.initializingClient=null;this.renderConnectionStatus();}});this.connectionInitialize.hidden=true;
    this.connectionReconnect=this.button('重新连接',async()=>{const provider=this.provider;if(this.reconnectingClient||!this.reconnectClient)return;this.reconnectingClient=provider;this.renderConnectionStatus();try{await this.reconnectClient(provider);}finally{this.reconnectingClient=null;this.renderConnectionStatus();}});this.connectionReconnect.hidden=true;
    this.connectionNotice=this.node('p','muted');this.connectionNotice.hidden=true;this.connectionRefresh=this.button('刷新状态',async()=>{if(this.refreshingClient||!this.refreshClients)return;this.refreshingClient=true;this.renderConnectionStatus();try{await this.refreshClients();}finally{this.refreshingClient=false;this.renderConnectionStatus();}});this.connectionRefresh.hidden=true;this.rowsRoot=this.node('div','sessions');const toolbar=this.node('div','chat-list-toolbar');toolbar.append(this.search,filters);this.list.append(toolbar,this.listStatus,this.connectionNotice,this.connectionRetry,this.connectionReconnect,this.connectionInitialize,this.connectionRefresh,this.rowsRoot);if(typeof bindChatListToolbar==='function')bindChatListToolbar(toolbar);
    this.welcome=this.node('div','welcome ds-welcome');const logo=this.node('img','mark large');logo.src='/icon-ui.png';logo.alt='';logo.width=72;logo.height=72;this.welcome.append(logo,this.translated('h1','从一条聊天继续'),this.translated('p','选择电脑上的聊天，查看进度或发送消息。','muted'));
    this.conversation=this.node('section','ds-conversation chat');this.conversation.hidden=true;this.main.append(this.welcome,this.conversation);
    this.back=this.iconButton('返回聊天列表','m15 5-7 7 7 7',()=>this.backToList());this.back.classList.add('ds-back');
    this.clientIcon=this.node('span','client-icon');const info=this.node('div','chat-info');this.title=this.node('span','ds-title');this.titleButton=this.button('',()=>this.chatDetails());this.titleButton.className='chat-title-button';this.label(this.titleButton,'查看聊天详情');this.titleButton.append(this.title);info.append(this.titleButton);
    this.status=this.node('span','status');this.status.setAttribute('role','status');const settings=this.iconButton('设置','M5 12h.01M12 12h.01M19 12h.01',()=>{document.getElementById('appearance-dialog')?.showModal();window.loadNotificationDefaults?.();});settings.className='ds-settings appearance-button';settings.setAttribute('data-open-appearance','');
    const head=this.node('header','ds-head chat-head');head.append(this.back,this.clientIcon,info,this.status,settings);
    this.messages=this.node('div','ds-messages timeline');this.messages.tabIndex=-1;this.progress=this.node('div','working');this.progress.hidden=true;this.requests=this.node('div','ds-requests');this.notice=this.node('p','notice');this.notice.hidden=true;this.resumeButton=this.button('继续会话',()=>this.continueChat());this.resumeButton.hidden=true;
    this.messageList=this.node('div');this.messages.append(this.messageList);
    this.messages.addEventListener('scroll',()=>this.rememberReading(),{passive:true});
    this.readingResize=new ResizeObserver(()=>{
      this.restoreReading();
      if(this.resumeAttempt&&!this.resumeAttempt.timer&&!this.workbench?.isFileVisible)this.checkResume(this.resumeAttempt);
    });
    this.readingResize.observe(this.messageList);this.readingResize.observe(this.messages);
    this.readRetry=this.button('重试读取',async()=>{
      if(this.readRetry.disabled||!this.sid)return;
      this.readRetry.disabled=true;
      try{await this.open(this.sid);}finally{this.readRetry.disabled=false;}
    });
    this.form=this.node('form','ds-composer composer');this.tools=this.node('div','composer-tools');this.modelButton=this.button('',()=>this.models());this.modelButton.className='tool-button ds-model';this.modelName=this.translated('span','模型','ds-model-name');this.modelEffort=this.node('span','ds-model-effort');this.modelButton.append(this.modelName,this.modelEffort);
    this.skillsButton=this.button('Skill',()=>this.skills());this.skillsButton.className='tool-button';this.skillsButton.hidden=true;this.permissionsButton=this.button('权限',()=>this.permissions());this.permissionsButton.className='tool-button ds-permissions';this.permissionsButton.hidden=true;
    this.composerToggle=this.iconButton('收起输入区','m6 9 6 6 6-6',()=>this.collapseComposer(!this.composerBody.hidden));this.composerToggle.className='composer-toggle';this.composerToggle.setAttribute('aria-expanded','true');this.contextUsage=new ContextUsageControl({onCompact:async()=>{const result=await this.mutate('compact',{});if(result)this.notify(BridgeI18n.t(result.message||'已提交上下文压缩'));return !!result;}});this.contextButton=this.contextUsage.button;this.contextButton.classList.add('ds-context');this.contextRing=this.contextUsage.ring;this.tools.append(this.modelButton,this.skillsButton,this.permissionsButton,this.contextButton,this.composerToggle);
    this.composerBody=this.node('div','ds-composer-body');this.skillPills=this.node('div','skill-pills');this.attachmentList=this.node('div','attachment-list');this.attachmentList.hidden=true;this.attachmentInput=this.node('input');this.attachmentInput.type='file';this.attachmentInput.multiple=true;this.attachmentInput.accept='image/png,image/jpeg,image/gif,image/webp';this.attachmentInput.hidden=true;this.attachButton=this.iconButton('添加文件或图片','m8 12 6-6a3.5 3.5 0 0 1 5 5l-8 8a5 5 0 0 1-7-7l8-8m-6 10 7-7',()=>{});this.attachButton.className='attach-button';this.error=this.node('div','error');this.error.setAttribute('role','alert');this.manageMessages=this.button('消息管理',()=>this.onManageMessages?.());this.manageMessages.className='plain';this.manageMessages.hidden=true;this.input=this.node('textarea');this.input.rows=1;this.input.maxLength=100000;this.input.placeholder=BridgeI18n.t('继续这条聊天…');this.label(this.input,'消息内容');this.input.oninput=()=>{this.draftTouched=true;this.saveDraft();this.resizeInput();this.updateComposer();};this.input.addEventListener('keyup',()=>this.saveDraft());this.input.addEventListener('click',()=>this.saveDraft());
    this.mode=this.node('select');this.label(this.mode,'发送方式');for(const [id,title] of [['send','发送新消息'],['queue','完成后发送'],['steer','补充当前任务']])this.mode.add(new Option(BridgeI18n.t(title),id));this.mode.onchange=()=>{this.saveDraft();this.updateComposer();};
    this.workMode=this.node('select','work-mode-select');this.label(this.workMode,'工作模式');for(const [id,title] of [['default','普通模式'],['plan','计划模式'],['goal','目标模式']])this.workMode.add(new Option(BridgeI18n.t(title),id));
    this.modeHint=this.node('p','muted work-mode-hint');this.goalRoot=this.node('section','goal-card');this.goalToggle=this.button('目标状态',()=>{});this.goalToggle.hidden=true;this.tools.append(this.goalToggle);
    this.workModes=new WorkModes({select:this.workMode,hint:this.modeHint,goalRoot:this.goalRoot,goalToggle:this.goalToggle,newId:()=>this.requestId(),onCancel:payload=>this.goalAction('clear',payload),onStatus:payload=>this.goalAction(payload.status==='paused'?'pause':'resume',payload),onEdit:payload=>this.goalAction('edit',payload)});
    this.send=this.button('发送 ↑',()=>{});this.send.className='primary';this.send.type='submit';this.stop=this.button('■ 停止',()=>this.mutate('stop',this.state?.stopTarget||{}));this.stop.className='stop';this.stop.hidden=true;
    const actions=this.node('div','ds-actions compose-bottom'),sendActions=this.node('div','compose-actions');sendActions.append(this.stop,this.send);actions.append(this.attachButton,this.attachmentInput,this.mode,this.workMode,sendActions);this.composerBody.append(this.skillPills,this.attachmentList,this.error,this.manageMessages,this.input,this.modeHint,actions);this.sendFeedback=this.node('div','queue-card ds-send-feedback');this.sendFeedback.hidden=true;this.form.append(this.tools,this.sendFeedback,this.composerBody);this.form.onsubmit=e=>{e.preventDefault();return this.submit();};
    this.quotes=typeof ComposerQuotes==='function'?new ComposerQuotes({messages:this.messages,input:this.input,container:this.composerBody,canQuote:()=>!!this.state,scope:()=>this.sid&&this.provider!=='codex'&&this.draftScope?.gateway===this.draftGateway&&this.draftScope?.provider===this.provider&&this.draftScope?.session===this.sid?this.draftScope:null,onChange:()=>{this.draftTouched=true;this.saveDraft();},onError:text=>{this.error.textContent=text;},onPrompt:text=>{const start=this.input.selectionStart,end=this.input.selectionEnd;this.input.value+=(this.input.value?'\n\n':'')+text;this.input.setSelectionRange(start,end);this.input.oninput();this.collapseComposer(false);},translate:text=>BridgeI18n.t(text)}):null;
    this.attachments=new ChatAttachments({root:this.attachmentList,button:this.attachButton,input:this.attachmentInput,canUpload:()=>this.state?.capabilities?.attachments===true,chooseReference:()=>this.state?.capabilities?.projectReferences===true&&window.BridgeFileActions.pickReference(this.workbench),paste:this.input,drop:this.form,onChange:()=>this.updateComposer(),upload:(key,id,file)=>this.uploadAttachment(key,id,file),preview:(key,id)=>this.attachmentUrl(key,id,'preview'),thumbnail:async(key,id,file)=>{const image=await this.attachments.thumbnailBlob(file);return this.uploadBinary(this.attachmentUrl(key,id,'thumb')+'&width='+image.width+'&height='+image.height,image.data,image.mime);}});
    const addAttachments=this.attachments.add.bind(this.attachments);this.attachments.add=files=>{if(!this.attachments.key||this.attachments.locked)return;if(this.overRequestLimit([...this.attachments.rows,...files])){this.attachments.error=this.requestLimitMessage();this.attachments.render();return;}if(this.state?.capabilities?.uploadFiles!==true&&(ClientIdentity.family(this.provider)!=='claude'||this.state?.backend!=='cli')){if(files.some(file=>!file.type?.startsWith('image/'))){this.attachments.error='此应用的消息附件仅支持图片；其他文件请使用文件传输。';this.attachments.render();return;}}addAttachments(files);};
    this.conversation.append(head,this.notice,this.messages,this.requests,this.goalRoot,this.form);this.workbench=new Workbench({chat:this.conversation,request,csrf,notify,onUnauthorized,composer:this.input,onReference:()=>this.collapseComposer(false)});this.workbench.quoteMessage=(text,source,target)=>{if(target.provider===this.provider&&target.id===this.sid&&target.host==='local')this.quotes?.add(text,source);};select.onchange=()=>this.choose(select.value);this.workbench.attachReference=info=>this.state?.capabilities?.projectReferences===true&&this.attachments.addReference(info);
    document.addEventListener('bridge-language',()=>this.relabel());
    const resume=()=>{clearTimeout(this.timer);if(document.hidden)this.listSync.reset();else{this.prefetchLists();if(this.provider!=='codex')this.refresh();}};
    const wakeResume=()=>{
      const attempt=this.resumeAttempt;if(!attempt)return;
      clearTimeout(attempt.timer);attempt.timer=null;
      if(attempt.controller){attempt.wake=true;attempt.controller.abort();}
      else this.checkResume(attempt);
    };
    document.addEventListener('visibilitychange',()=>{wakeResume();resume();});
    window.addEventListener('focus',resume);
    for(const event of ['offline','online','bridge-channel-changed','pageshow'])window.addEventListener(event,wakeResume);
    window.addEventListener('pagehide',()=>this.saveDraft());
    this.workbench.stopTask=(session,taskId)=>session.provider===this.provider&&session.id===this.sid?this.mutate('stop-task',{taskId}):null;
    this.appearanceObserver=new MutationObserver(()=>{this.renderMessages(this.lastMessages||[]);this.workbench.setThumbnails(document.documentElement.dataset.showFileThumbnails==='true');});this.appearanceObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-show-reasoning','data-show-process','data-show-file-thumbnails']});
    const listRegion=this.node('div','session-list-region');this.listHint=this.button('',()=>{});this.listHint.className='list-sync';this.listHint.hidden=true;this.listHint.setAttribute('aria-live','polite');listRegion.append(this.rowsRoot,this.listHint);this.list.append(listRegion);
    this.listSync=new ListSync({root:this.rowsRoot,status:this.listHint,refresh:()=>this.refreshList(),visible:()=>!this.root.hidden&&!this.container.hidden&&this.list.getBoundingClientRect().width>0,ready:()=>document.activeElement!==this.search,automatic:false});
    this.rowsRoot.addEventListener('scroll',()=>this.saveListSnapshot(this.provider),{passive:true});
    this.commands=typeof ComposerCommands==='function'?new ComposerCommands({input:this.input,
      scope:()=>this.isChatOpen&&this.sid?JSON.stringify([this.provider,this.sid,this.generation,this.draftGateway,this.draftScope?.account]):null,
      actions:()=>[...ComposerCommands.controls({model:this.modelButton,skills:this.skillsButton,permissions:this.permissionsButton,context:this.contextUsage,workMode:this.workMode}),...(this.commandActions?.()||[])],onError:text=>this.notify(text)}):null;
    this.updateComposer();
  }
  node(tag,cls='',text){const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;}
  resetMermaidScope(){window.BridgeMermaidCards?.clearScope(this.mermaidScope);this.mermaidScope=window.BridgeMermaidCards?.createScope();}
  translated(tag,text,cls=''){const node=this.node(tag,cls,BridgeI18n.t(text));node.dataset.i18n=text;return node;}
  label(node,text){node.dataset.i18nAriaLabel=text;node.setAttribute('aria-label',BridgeI18n.t(text));}
  button(text,fn){const n=this.translated('button',text,'plain');n.type='button';n.onclick=async()=>{try{return await fn();}catch(e){this.notify(BridgeI18n.t(e.message));}};return n;}
  iconButton(text,path,fn){const button=this.button('',fn);button.className='icon-button';this.label(button,text);button.title=BridgeI18n.t(text);const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','header-icon');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const shape=document.createElementNS(svg.namespaceURI,'path');shape.setAttribute('d',path);svg.append(shape);button.append(svg);return button;}
  path(action,sid=this.sid,provider=this.provider,query={}){const path=ClientIdentity.path(provider,action,sid,query);return window.BridgeRelayURL?.(path)||path;}
  listSnapshotKey(provider){return this.listCacheIdentity&&this.listCacheHost&&ClientIdentity.desktop(provider)?'desktop-list-v1:'+this.listCacheIdentity+':'+encodeURIComponent(this.listCacheHost)+':'+provider:'';}
  bindListCache(identity,host){
    if(this.listCacheIdentity!==identity||this.listCacheHost!==host)this.providerViews.clear();
    this.listCacheIdentity=identity;this.listCacheHost=host;
    if(identity&&host)for(const storage of [localStorage,sessionStorage])for(let i=storage.length-1;i>=0;i--){const key=storage.key(i);if(key?.startsWith('desktop-list-v1:')&&!key.startsWith('desktop-list-v1:'+identity+':')&&['claude','deepseek','zcode','antigravity'].some(provider=>key.endsWith(':'+encodeURIComponent(host)+':'+provider)))storage.removeItem(key);}
    this.restoreListSnapshots(['claude','deepseek','zcode',...(this.clientStates||[]).map(row=>row.id)]);
  }
  restoreListSnapshots(providers){
    for(const provider of providers){
      const key=this.listSnapshotKey(provider);if(!key||this.providerViews.has(provider))continue;
      let saved;try{const snapshot=localStorage.getItem(key)||sessionStorage.getItem(key);if(snapshot?.length>512*1024)continue;saved=JSON.parse(snapshot||'null');}catch{continue;}
      if(saved&&Array.isArray(saved.rows)&&saved.rows.length<=200)this.providerViews.set(provider,{...saved,cachedAt:0,snapshotReady:true});
    }
  }
  previewCachedList(provider){
    if(!this.providerViews.get(provider)?.snapshotReady)return false;
    this.choose(provider,{previewOnly:true}).catch(error=>this.notify(error.message));
    return true;
  }
  confirmCachedList(){
    if(!this.previewOnly)return;
    this.previewOnly=false;this.listStamp='';this.renderList();this.refresh().catch(error=>this.notify(error.message));
  }
  saveListSnapshot(provider){
    const key=this.listSnapshotKey(provider),view=this.providerViews.get(provider);if(!key||!view?.snapshotReady)return;
    const current=provider===this.provider?{query:this.search.value,archived:this.archived.checked,listMode:this.listMode.value,surface:this.surfaceSelection,scroll:this.rowsRoot.scrollTop}:view;
    const rows=(view.rows||[]).slice(0,200).map(row=>({id:row.id,nativeId:row.nativeId,title:row.title,cwd:row.cwd,backend:row.backend,surfaceDirKey:row.surfaceDirKey,runtime:row.runtime,host:row.host,hostLabel:row.hostLabel,projectId:row.projectId,projectName:row.projectName,sectionId:row.sectionId,sectionName:row.sectionName,updatedAt:row.updatedAt,archived:row.archived,model:row.model,modelDisplayName:row.modelDisplayName,effort:row.effort,composerCapabilities:row.composerCapabilities}));
    try{const snapshot=JSON.stringify({rows,query:current.query||'',archived:current.archived===true,listMode:current.listMode||'recent',surface:current.surface||'all',scroll:current.scroll||0});if(snapshot.length<=512*1024){localStorage.setItem(key,snapshot);sessionStorage.removeItem(key);}}catch{}
  }
  sharedRead(provider,action,sid=null){
    const epoch=this.readEpoch,version=this.providerReadEpochs.get(provider)||0,key=JSON.stringify([provider,action,sid,epoch,version]);
    if(this.readTasks.has(key))return this.readTasks.get(key);
    const task={current:()=>epoch===this.readEpoch&&version===(this.providerReadEpochs.get(provider)||0),pinRead:action==='list'?this.pinPreferences?.capture():null};
    task.promise=(async()=>{const value=await this.request(this.path(action,sid,provider));if(task.current()){
      if(action==='list'){
        const complete=value.connected!==false&&value.complete!==false&&!value.unavailableHosts?.length;
        this.providerViews.set(provider,{...this.providerViews.get(provider),rows:value.sessions||[],cachedAt:Date.now(),snapshotReady:complete});
        if(complete)this.saveListSnapshot(provider);
      }
      else if(action==='detail'&&value.session)this.cacheDetail(JSON.stringify([provider,sid]),value);
    }return value;})();this.readTasks.set(key,task);
    const done=()=>{if(this.readTasks.get(key)===task)this.readTasks.delete(key);};task.promise.then(done,done);return task;
  }
  forgetDetail(key){this.detailCache.delete(key);this.detailCacheBytes-=this.detailCacheSizes.get(key)||0;this.detailCacheSizes.delete(key);}
  cacheDetail(key,value){
    const bytes=JSON.stringify(value).length*2,limit=8*1024*1024;this.forgetDetail(key);if(bytes>limit)return;
    this.detailCache.set(key,value);this.detailCacheSizes.set(key,bytes);this.detailCacheBytes+=bytes;
    while(this.detailCache.size>12||this.detailCacheBytes>limit)this.forgetDetail(this.detailCache.keys().next().value);
  }
  invalidateReads(provider){
    this.providerReadEpochs.set(provider,(this.providerReadEpochs.get(provider)||0)+1);for(const key of this.readTasks.keys())if(JSON.parse(key)[0]===provider)this.readTasks.delete(key);
    this.listPrefetchAt.delete(provider);const saved=this.providerViews.get(provider);if(saved)saved.cachedAt=0;
    if(provider===this.provider){this.freshDetail=false;this.detailRevision++;this.loading=null;this.listSync.reset();}
  }
  prefetchLists(){
    if(document.hidden||this.root.hidden||this.resumeAttempt)return;
    const now=Date.now();for(const client of this.clientStates||[]){
      if(!ClientIdentity.desktop(client.id)||client.id===this.provider||!client.enabled||!this.sourceReadable(client)||client.operationUncertain)continue;
      if(now-Math.max(this.providerViews.get(client.id)?.cachedAt||0,this.listPrefetchAt.get(client.id)||0)<30000)continue;
      this.listPrefetchAt.set(client.id,now);this.sharedRead(client.id,'list').promise.catch(()=>{});
    }
  }
  setClientStates(clients,retry,initialize,refresh,reconnect){
    let modeChanged=false;
    for(const client of clients){const old=this.clientStates?.find(row=>row.id===client.id),changed=old?.connectionMode&&client.connectionMode&&old.connectionMode!==client.connectionMode;if(old&&(changed||old.enabled!==false&&client.enabled===false||this.sourceReadable(old)&&!this.sourceReadable(client)))this.invalidateReads(client.id);if(changed){this.forgetResumes(client.id);if(client.id===this.provider){modeChanged=true;this.staleDetail();if(this.sid)this.status.textContent=BridgeI18n.t('正在连接…');}}}
    const before=this.clientStates?.find(client=>client.id===this.provider);this.clientStates=clients;this.restoreListSnapshots(clients.map(row=>row.id));this.retryClient=retry;this.initializeClient=initialize;this.refreshClients=refresh;this.reconnectClient=reconnect;
    const selected=clients.find(client=>client.id===this.provider),cliReadable=ClientIdentity.family(this.provider)==='claude'&&this.state?.backend==='cli'&&selected?.enabled!==false&&this.sourceReadable(selected);
    if(selected?.enabled===false&&this.resumeAttempt)this.resetResume(true);
    if(this.state&&!this.canMutate()&&!cliReadable&&!(this.sourceReadable(selected)&&this.canResume()))this.staleDetail();else this.updateComposer();
    const wasWaiting=before&&ClientLifecycle.connection(before).waiting;
    const sessionReady=client=>client?.pipeReady===true||client?.setupStatus==='session-access';
    this.prefetchLists();
    if(!this.renderConnectionStatus()&&before&&(modeChanged||wasWaiting||!sessionReady(before)&&sessionReady(selected)||!this.sourceReadable(before)&&this.sourceReadable(selected))){this.listStatusKind=this.rows.length?'list':'loading';this.listStatus.textContent=this.rows.length?'':BridgeI18n.t('正在读取聊天…');return this.refresh();}
  }
  sourceReadable(client){return !!client&&(client.connected===true||client.historyAvailable===true||ClientIdentity.family(client.id)==='claude'&&client.cliAvailable===true);}
  renderConnectionStatus(){
    const client=this.clientStates?.find(client=>client.id===this.provider),state=client&&ClientLifecycle.connection(client);
    const failure=client?.operationFailure,uncertain=!!(client?.operationUncertain||failure?.uncertain),blocked=!!state&&!client.connected&&(state.waiting||state.action||state.retryable||state.canReconnect||state.canInitialize||!!failure);
    this.listStatus.setAttribute('aria-busy',String(!!state?.waiting));this.listStatus.classList.toggle('client-connection-waiting',!!state?.waiting);
    this.connectionRetry.hidden=!blocked||!state.retryable||uncertain;this.connectionRetry.disabled=!!this.retryingClient;
    this.connectionInitialize.hidden=!blocked||!state.canInitialize||uncertain;this.connectionInitialize.disabled=!!this.initializingClient;
    this.connectionReconnect.hidden=!blocked||!state.canReconnect||uncertain;this.connectionReconnect.disabled=!!this.reconnectingClient;this.connectionReconnect.textContent=BridgeI18n.t('重新连接');
    this.connectionInitialize.textContent=BridgeI18n.t(state?.initializeLabel||'初始化连接');
    this.connectionNotice.hidden=!failure;this.connectionNotice.textContent=failure?BridgeI18n.t(ClientLifecycle.failureMessage(failure)):'';this.connectionRefresh.hidden=!uncertain;this.connectionRefresh.disabled=!!this.refreshingClient;
    if(blocked){this.listStatusKind='connection';this.listStatus.textContent=BridgeI18n.t(state.label)+(state.modeLabel?' · '+BridgeI18n.t(state.modeLabel):'')+(state.reason&&state.reason!==state.label?' · '+BridgeI18n.t(state.reason):'');}
    else if(state?.ready&&state.modeLabel&&!failure&&this.listStatusKind==='connection'){this.listStatusKind='';this.listStatus.textContent='';}
    return blocked&&!(client?.enabled!==false&&this.sourceReadable(client));
  }
  get isChatOpen(){return this.provider!=='codex'&&!!this.sid&&!this.conversation.hidden;}
  bindDraftGateway(identity){if(this.draftGateway&&this.draftGateway!==identity){this.forgetResumes();this.provisionalDrafts.clear();this.drafts.clear();for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('desktop-draft:'))sessionStorage.removeItem(key);}}this.draftGateway=identity||'';}
  saveDraft(){
    if(!this.sid||this.restoringDraft)return null;
    const key=this.provider+':'+this.sid,fields={quotes:this.quotes?.quotes.map(quote=>({...quote}))||[],text:this.input.value,selectionStart:this.input.selectionStart,selectionEnd:this.input.selectionEnd,sendMode:this.mode.value,skills:[...this.selectedSkills().keys()]};
    if(!this.draftScope||!this.composerDrafts){if(this.draftGateway&&this.composerDrafts){if(this.draftTouched)this.provisionalDrafts.set(key,{gateway:this.draftGateway,account:'',...fields});}else this.drafts.set(key,fields.text);return null;}
    const saved=this.composerDrafts.save(this.draftScope,fields);
    if(saved){this.provisionalDrafts.delete(key);this.drafts.delete(key);sessionStorage.removeItem('desktop-draft:'+key);}
    else{this.provisionalDrafts.set(key,{gateway:this.draftGateway,account:this.draftScope.account,...fields});this.drafts.set(key,fields.text);}
    return saved;
  }
  continueWslAccountDrafts(result){
    if(result?.provider!=='claude'||!/^wsl-claude:[0-9a-f]{32}$/.test(result.entry||'')||
       result.continuationPending||!Array.isArray(result.draftContinuations)||!this.draftGateway||!this.composerDrafts)return 0;
    if(this.provider==='claude'&&this.sid)this.saveDraft();
    let copied=0;
    for(const row of result.draftContinuations){
      if(typeof row.id!=='string'||!row.id||!/^[a-f0-9]{64}$/.test(row.previousAccountIdentity||'')||
         !/^[a-f0-9]{64}$/.test(row.accountIdentity||''))continue;
      const known=this.rows.find(item=>item.id===row.id);
      if(known&&(known.host!==result.entry||known.runtime!=='wsl'))continue;
      if([...this.pending.keys()].some(key=>key.startsWith('claude:'+row.id+':send:')))continue;
      const previous={gateway:this.draftGateway,provider:'claude',account:row.previousAccountIdentity,host:'local',session:row.id},next={...previous,account:row.accountIdentity};
      const draft=this.composerDrafts.continueAccount(previous,next);if(!draft)continue;copied++;
      // Attachments already use the unchanged provider/session key; retain the
      // same upload references rather than copying or resubmitting uploads.
      if(this.provider==='claude'&&this.sid===row.id&&this.composerDrafts.key(this.draftScope)===this.composerDrafts.key(previous)){
        this.invalidateReads('claude');this.bindDraftScope({draftAccountIdentity:row.accountIdentity});
      }
    }
    return copied;
  }
  bindDraftScope(value){
    const account=value?.draftAccountIdentity??value?.session?.draftAccountIdentity??'';
    if(!account&&this.draftScope){this.saveDraft();this.drafts.delete(this.provider+':'+this.sid);this.draftScope=null;this.draftTouched=false;this.quotes?.reset();this.input.value='';this.selectedSkills().clear();this.updateComposer();return;}
    if(!this.draftGateway||!account||!this.sid||!this.composerDrafts)return;
    const scope={gateway:this.draftGateway,provider:this.provider,account,host:'local',session:this.sid};
    const continuation=value?.draftContinuation,session=value?.session;
    if(this.provider==='claude'&&session?.id===this.sid&&session.runtime==='wsl'&&
       /^wsl-claude:[0-9a-f]{32}$/.test(continuation?.entry||'')&&session.host===continuation.entry&&
       continuation.id===this.sid&&continuation.accountIdentity===account&&/^[a-f0-9]{64}$/.test(account)&&
       Array.isArray(continuation.previousAccountIdentities)&&continuation.previousAccountIdentities.length<=32&&
       ![...this.pending.keys()].some(key=>key.startsWith('claude:'+this.sid+':send:'))){
      if(this.draftScope&&this.draftScope.account!==account)this.saveDraft();
      for(const previousAccount of [...continuation.previousAccountIdentities].reverse()){
        if(!/^[a-f0-9]{64}$/.test(previousAccount)||previousAccount===account)continue;
        const previous={...scope,account:previousAccount},draft=this.composerDrafts.load(previous);
        if(!draft||!(draft.text||draft.quotes?.length))continue;
        if(this.composerDrafts.continueAccount(previous,scope))break;
      }
    }
    if(this.composerDrafts.key(scope)===this.composerDrafts.key(this.draftScope))return;
    const switching=!!this.draftScope;if(switching){this.saveDraft();this.drafts.delete(this.provider+':'+this.sid);for(const key of this.pending.keys())if(key.startsWith(this.provider+':'+this.sid+':send:')&&key.endsWith(':'+this.draftGateway+':'))this.pending.delete(key);}this.draftScope=scope;
    const key=this.provider+':'+this.sid,pending=this.provisionalDrafts.get(key);
    if(pending&&(pending.gateway!==this.draftGateway||pending.account&&pending.account!==account)){this.provisionalDrafts.delete(key);this.drafts.delete(key);sessionStorage.removeItem('desktop-draft:'+key);}
    const provisional=this.provisionalDrafts.get(key),promoted=provisional&&this.composerDrafts.save(scope,provisional);
    if(promoted){this.provisionalDrafts.delete(key);this.drafts.delete(key);sessionStorage.removeItem('desktop-draft:'+key);}
    const stored=this.composerDrafts.load(scope),legacy=(stored||provisional)?null:this.composerDrafts.migrate(scope,sessionStorage,{text:'desktop-draft:'+this.provider+':'+this.sid});
    const draft=promoted||provisional||stored||legacy;
    if(draft&&(switching||!this.draftTouched)){
      this.restoringDraft=true;this.input.value=draft.text;this.quotes?.restore(draft.quotes);this.input.setSelectionRange(draft.selectionStart,draft.selectionEnd);this.mode.value=draft.sendMode;
      const selected=this.selectedSkills();selected.clear();for(const id of draft.skills)selected.set(id,{id,name:id});
      this.restoringDraft=false;this.renderSkillPills();this.resizeInput();this.updateComposer();
    }else if(switching||!this.draftTouched){this.quotes?.reset();this.input.value='';this.mode.value='send';this.selectedSkills().clear();this.updateComposer();}
    if(this.draftTouched&&!switching)this.saveDraft();this.draftTouched=false;
  }
  showSelection(open){this.conversation.hidden=!open;this.welcome.hidden=open;this.container.classList.toggle('ds-open',open);this.root.classList.toggle('chat-open',open);document.body.classList.toggle('chat-detail',open);}
  rememberView(){
    if(this.provider==='codex')return;this.rememberReading();this.saveDraft();this.workbench.rememberScroll();this.providerViews.set(this.provider,{...this.providerViews.get(this.provider),rows:this.rows,sid:this.sid,query:this.search.value,archived:this.archived.checked,listMode:this.listMode.value,surface:this.surfaceSelection,scroll:this.rowsRoot.scrollTop});this.saveListSnapshot(this.provider);
  }
  async choose(provider,{previewOnly=false,loadList=true}={}){
    if(provider===this.provider)return;
    this.resetResume();this.resetMermaidScope();
    this.modelDialog?.close();this.rememberView();this.quotes?.reset();this.listSync.reset();clearTimeout(this.timer);this.generation++;this.detailRevision++;this.provider=provider;this.previewOnly=previewOnly;this.select.value=provider;this.workbench.provider=provider;this.workbench.current=null;this.workbench.panel.hidden=true;this.workbench.tabs.replaceChildren();this.conversation.classList.remove('workbench-active','wb-split');this.sid=null;this.state=null;this.freshDetail=false;const saved=this.providerViews.get(provider);this.rows=saved?.rows||[];this.listStamp='';this.search.value=saved?.query||'';this.archived.checked=saved?.archived||false;if(saved?.listMode)this.listMode.value=saved.listMode;this.surfaceSelection=saved?.surface||'all';this.showSelection(false);
    this.container.hidden=provider==='codex';this.root.classList.toggle('desktop-mode',provider!=='codex');this.clientIcon.replaceChildren();if(provider!=='codex'){const img=this.node('img');img.src='/client-icons/'+ClientIdentity.family(provider)+'.png';img.alt='';this.clientIcon.append(img);}this.clientIcon.className='client-icon client-'+provider;this.listStatusKind=this.rows.length?'list':'loading';this.listStatus.textContent=this.rows.length?'':BridgeI18n.t('正在读取聊天…');this.renderList();this.rowsRoot.scrollTop=saved?.scroll||0;this.saveListSnapshot(provider);this.updateComposer();if(saved?.sid&&!previewOnly&&loadList)this.prepareChat(saved.sid);if(!previewOnly)this.onSelect(provider);
    this.renderConnectionStatus();if(provider!=='codex'&&!previewOnly&&loadList)await this.refresh();
  }
  backToList(){if(!this.isChatOpen)return false;this.resetResume();this.rememberReading();this.modelDialog?.close();this.saveDraft();this.quotes?.reset();this.workbench.cancelLocate();this.workbench.rememberScroll();this.workbench.stopThumbnails();this.sid=null;this.state=null;this.notificationPolicy=null;this.freshDetail=false;this.detailRevision++;this.showSelection(false);this.renderList();this.updateComposer();this.rememberView();this.onNavigation?.();this.onListEnter?.();return true;}
  async accountChanged(provider,previous={},next={}){
    window.BridgeModelCatalogCache.forget(this.modelCatalogs,[this.listCacheIdentity,this.listCacheHost||'local',provider]);
    this.forgetResumes(provider);this.onHistoryInvalidated?.();
    if(provider===this.provider)this.resetMermaidScope();
    if(provider===this.provider)this.modelDialog?.close();this.invalidateReads(provider);
    const identity=value=>JSON.stringify([provider,[...(value.activeIds||[value.activeId].filter(Boolean))].sort()]);
    if(provider===this.provider)this.saveDraft();
    const drafts=new Map([...this.drafts].filter(([key])=>key.startsWith(provider+':')));this.accountDrafts.set(identity(previous),drafts);
    if(provider===this.provider){clearTimeout(this.timer);this.generation++;this.detailRevision++;this.sid=null;this.state=null;this.draftScope=null;this.draftTouched=false;this.quotes?.reset();this.freshDetail=false;this.input.value='';}
    const jsonKey=key=>{try{return JSON.parse(key)[0]===provider;}catch{return false;}};
    this.providerViews.delete(provider);for(const key of this.drafts.keys())if(key.startsWith(provider+':'))this.drafts.delete(key);for(const key of this.provisionalDrafts.keys())if(key.startsWith(provider+':'))this.provisionalDrafts.delete(key);for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('desktop-draft:'+provider+':'))sessionStorage.removeItem(key);}
    const snapshotKey=this.listSnapshotKey(provider);if(snapshotKey){localStorage.removeItem(snapshotKey);sessionStorage.removeItem(snapshotKey);}
    if(!this.draftGateway||!this.composerDrafts)for(const [key,text] of this.accountDrafts.get(identity(next))||[])this.drafts.set(key,text);
    for(const key of this.detailCache.keys())if(jsonKey(key))this.forgetDetail(key);for(const key of this.skillSelections.keys())if(jsonKey(key))this.skillSelections.delete(key);
    for(const key of this.pending.keys())if(key.startsWith(provider+':'))this.pending.delete(key);
    for(const key of Object.keys(this.activity.rows))if(key.startsWith(provider+'|'))delete this.activity.rows[key];this.activity.save();
    this.workbench.reset(provider);this.attachments.forget(jsonKey);
    if(provider!==this.provider)return;
    this.rows=[];this.listStamp='';this.lastMessages=[];this.requestStamp='';this.messageNodes.clear();this.messageList.replaceChildren();this.requests.replaceChildren();this.showSelection(false);this.renderList();this.updateComposer();await this.refresh();
  }
  clear(){this.modelCatalogs.clear();this.forgetResumes();this.sendingPreview=null;this.sendFeedback.replaceChildren();this.sendFeedback.hidden=true;this.quotes?.reset();this.resetMermaidScope();this.modelDialog?.close();this.listSync.reset();this.readEpoch++;this.readTasks.clear();this.providerReadEpochs.clear();this.listPrefetchAt.clear();this.detailCacheSizes.clear();this.detailCacheBytes=0;this.clientStates=[];this.workbench.reset();clearTimeout(this.timer);this.generation++;this.detailRevision++;this.provider='codex';this.previewOnly=false;this.select.value='codex';this.select.hidden=true;this.container.hidden=true;this.root.classList.remove('desktop-mode');this.sid=null;this.state=null;this.showSelection(false);this.drafts.clear();this.provisionalDrafts.clear();for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('desktop-draft:'))sessionStorage.removeItem(key);}this.accountDrafts.clear();this.skillSelections.clear();this.providerViews.clear();this.listCacheIdentity='';this.listCacheHost='';this.draftGateway='';this.draftScope=null;this.detailCache.clear();this.historyMessages=[];this.historyPage=null;this.historyRetry=false;this.historyBusy=false;this.activity.clear();this.freshDetail=false;this.rows=[];this.listStamp='';this.rowsRoot.replaceChildren();this.lastMessages=[];this.requestStamp='';this.pending.clear();this.attachments.reset();this.messageList.replaceChildren();this.requests.replaceChildren();this.messageNodes.clear();}
  async refresh(){
    clearTimeout(this.timer);const generation=this.generation,provider=this.provider;if(this.previewOnly||provider==='codex'||document.hidden||this.root.hidden)return;
    if(this.resumeAttempt){if(!this.resumeAttempt.timer&&!this.resumeAttempt.controller)this.checkResume(this.resumeAttempt);return;}
    if(this.renderConnectionStatus()){this.updateActivity(this.rows,false);this.renderIndicators();this.freshDetail=false;this.updateComposer();return;}
    if(this.loading?.generation===generation)return this.loading.promise;const loading={generation};this.loading=loading;
    const sid=this.sid;if(sid)this.detail(generation).catch(error=>{if(generation===this.generation&&sid===this.sid)this.showReadFailure(error);});
    loading.promise=this.refreshList().catch(()=>{}).finally(()=>{
      if(this.loading!==loading)return;
      this.loading=null;
      if(generation!==this.generation)return;
      const client=this.clientStates?.find(row=>row.id===provider);
      const interval=client?.historyAvailable&&!client.connected&&this.state?.connectionState!=='ready'?15000:(this.sid||ClientLifecycle.connection(client).waiting?2000:5000);
      this.timer=setTimeout(()=>this.refresh(),interval);
    });
    return loading.promise;
  }
  refreshList(){
    const generation=this.generation,provider=this.provider;if(this.previewOnly||provider==='codex'||document.hidden||this.root.hidden||this.renderConnectionStatus())return Promise.resolve(false);
    const task=this.sharedRead(provider,'list');if(this.listRead?.task===task&&this.listRead.generation===generation)return this.listRead.promise;
    const reading={task,generation};this.listRead=reading;
    reading.promise=(async()=>{
      try{const value=await task.promise;if(!task.current()||generation!==this.generation)return false;this.rows=value.sessions||[];for(const row of this.rows)this.pinPreferences?.observe({provider,host:row.host||'local',id:row.id},row.bridgePinned,task.pinRead);this.updateActivity(this.rows,value.connected!==false);this.listStatusKind=value.sessionAccess?'connection':'list';this.listStatus.textContent=value.sessionAccess?BridgeI18n.t('可在聊天中继续原生会话。'):value.readOnly?BridgeI18n.t('本机历史（只读）'):value.connected===false?BridgeI18n.t('尚未连接'):this.rows.length?'':BridgeI18n.t('电脑上还没有聊天');this.renderConnectionStatus();this.renderList();this.rememberView();this.onListReady?.(provider,value);if(value.connected===false&&!value.readOnly)throw Error(BridgeI18n.t('尚未连接'));this.listSync.acknowledgeSuccess();return true;}
      catch(error){if(!task.current()||generation!==this.generation)return false;this.updateActivity(this.rows,false);this.renderIndicators();if(!this.renderConnectionStatus()){this.listStatusKind='error';this.listStatus.textContent=this.rows.length?'':BridgeI18n.t(error.message);if(!this.listSync.busy)this.listSync.reportFailure(error);}if(this.sid&&!this.state)this.error.textContent=BridgeI18n.t(error.message);throw error;}
      finally{if(this.listRead===reading)this.listRead=null;}
    })();return reading.promise;
  }
  visibleActivity(){return this.isChatOpen&&!document.hidden&&!this.workbench.isFileVisible?this.provider+'|'+this.sid:null;}
  updateActivity(rows,connected=true){this.activity.update(rows.map(row=>({id:row.id,host:this.provider,connected,status:row.status,turnId:row.turnId,turnStatus:row.turnStatus})),this.visibleActivity());}
  renderIndicators(){
    const rows=new Map(this.rows.map(row=>[row.id,row]));for(const button of this.rowsRoot.querySelectorAll('.session')){button.querySelector('.session-indicator')?.remove();const row=this.freshDetail&&button.dataset.id===this.sid?this.state:rows.get(button.dataset.id),indicator=row?.requests?.length?(this.activity.rows[this.provider+'|'+button.dataset.id]?.offline?{kind:'unknown',label:'运行状态暂不可用'}:{kind:'awaiting',label:'等待回应'}):this.activity.indicator(this.provider+'|'+button.dataset.id);if(indicator){const dot=this.node('span','session-indicator '+indicator.kind);dot.title=BridgeI18n.t(indicator.label);dot.setAttribute('aria-label',BridgeI18n.t(indicator.label));button.querySelector('strong')?.prepend(dot);}}
  }
  renderList(){
    const family=ClientIdentity.family(this.provider),labels=family==='claude'?{code:'Code',cowork:'Cowork',cli:'CLI'}:family==='antigravity'?{app:'APP',cli:'CLI',ide:'IDE'}:{},types=new Set((this.rows||[]).map(row=>row.backend).filter(kind=>Object.hasOwn(labels,kind))),showTypes=types.size>1,selected=showTypes&&types.has(this.surfaceSelection)?this.surfaceSelection:'all';this.surfaceSelection=selected;const optionStamp=JSON.stringify([BridgeI18n.language(),family,...Object.keys(labels).filter(kind=>types.has(kind))]);if(optionStamp!==this.surfaceOptionsStamp){this.surface.replaceChildren(new Option(BridgeI18n.t('全部会话'),'all'),...Object.keys(labels).filter(kind=>types.has(kind)).map(kind=>new Option(labels[kind],kind)));this.surfaceOptionsStamp=optionStamp;}if(this.surface.value!==selected)this.surface.value=selected;this.surface.hidden=!showTypes;
    const displayedRows=(this.rows||[]).map(row=>{const identity={provider:this.provider,id:row.id,host:row.host||'local'};return {...row,bridgePinned:this.pinPreferences?this.pinPreferences.value(identity,row.bridgePinned):!!row.bridgePinned,pinPending:this.pinPreferences?.pending(identity)||false};});
    const query=this.search.value.trim().toLowerCase();const stamp=JSON.stringify([this.provider,this.sid,query,this.archived.checked,this.listMode.value,selected,showTypes,displayedRows,BridgeI18n.language()]);if(stamp===this.listStamp){this.renderIndicators();return;}this.listStamp=stamp;const scroll=this.rowsRoot.scrollTop,top=this.rowsRoot.getBoundingClientRect().top,anchor=scroll>0?[...this.rowsRoot.querySelectorAll('.session')].find(node=>node.getBoundingClientRect().bottom>top):null,offset=anchor?anchor.getBoundingClientRect().top-top:0;this.rowsRoot.replaceChildren();
    const timestamp=value=>{const raw=Number(value||0);return raw<1e12?raw*1000:raw;};
    const rows=displayedRows.filter(row=>(selected==='all'||row.backend===selected)&&(this.archived.checked||!row.archived)&&((row.title||'')+' '+(row.cwd||'')).toLowerCase().includes(query)).sort((a,b)=>(this.listMode.value==='recent'?Number(b.bridgePinned)-Number(a.bridgePinned):0)||timestamp(b.updatedAt)-timestamp(a.updatedAt));
    const groups=new Map();for(const row of rows){const project=Object.hasOwn(row,'projectId')?(row.projectName||BridgeI18n.t('未分配项目')):(row.cwd||BridgeI18n.t('电脑上的聊天'));const key=this.listMode.value==='project'?JSON.stringify([row.host||'local',Object.hasOwn(row,'projectId')||row.sectionId?row.backend||'desktop':'',row.projectId??project,row.sectionId||'']):'';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
    const hasPins=this.listMode.value==='recent'&&rows.some(row=>row.bridgePinned);let pinSection=null;
    for(const [name,items] of groups){let target=this.rowsRoot;if(name){const group=this.node('details','project-group ds-session-group'),key=JSON.stringify([this.provider,this.listMode.value,name]);group.open=!this.collapsedGroups.has(key);group.ontoggle=()=>{if(!group.isConnected)return;if(group.open)this.collapsedGroups.delete(key);else this.collapsedGroups.add(key);try{localStorage.setItem('bridge-desktop-session-groups',JSON.stringify([...this.collapsedGroups]));}catch{}};const first=items[0],project=Object.hasOwn(first,'projectId')?(first.projectName||BridgeI18n.t('未分配项目')):(first.cwd||BridgeI18n.t('电脑上的聊天'));group.append(this.node('summary','',[first.runtime==='wsl'?(first.hostLabel||first.host):'',project,first.sectionName].filter(Boolean).join(' · ')));this.rowsRoot.append(group);target=group;}
      for(const row of items){if(hasPins&&pinSection!==row.bridgePinned){pinSection=row.bridgePinned;target.append(this.translated('h3',pinSection?'置顶聊天':'最近聊天','muted'));}const button=this.button('',()=>this.open(row.id,{continueSelected:true}));button.disabled=this.previewOnly===true;button.className='session ds-session-row'+(row.id===this.sid?' selected':'');button.dataset.id=row.id;button.dataset.provider=this.provider;const meta=this.node('small'),location=this.node('span','',row.runtime==='wsl'?[row.hostLabel||row.host,row.cwd].filter(Boolean).join(' · '):row.cwd||''),time=this.node('time');if(row.updatedAt){const date=new Date(timestamp(row.updatedAt));if(Number.isFinite(date.getTime()))time.textContent=date.toLocaleDateString(BridgeI18n.locale(),{month:'numeric',day:'numeric'});}meta.append(location,time);button.append(this.node('strong','',row.title||BridgeI18n.t('新会话')));{const kind=this.node('span','ds-session-type',labels[row.backend]?(row.runtime==='wsl'&&row.backend==='cli'?'CLI · WSL':labels[row.backend]):Object.keys(labels).length?BridgeI18n.t('其他来源'):'APP');kind.setAttribute('aria-label',BridgeI18n.t('会话类型')+': '+kind.textContent);button.append(kind);}if(row.sectionName)button.append(this.node('span','session-source',row.sectionName));if(row.pinPending)button.append(this.translated('span','置顶设置待同步','session-source'));button.append(meta);this.onSessionMenu?.(button,row,this.provider);target.append(button);}
    }
    if(!rows.length&&this.rows.length)this.rowsRoot.append(this.translated('p','没有匹配的聊天','muted ds-list-empty'));this.rowsRoot.scrollTop=scroll;if(anchor){const next=[...this.rowsRoot.querySelectorAll('.session')].find(node=>node.dataset.id===anchor.dataset.id);if(next)this.rowsRoot.scrollTop+=next.getBoundingClientRect().top-this.rowsRoot.getBoundingClientRect().top-offset;}this.renderIndicators();this.saveListSnapshot(this.provider);
  }
  rememberReading(){
    if(!this.isChatOpen||!this.lastMessages?.length||!this.messages.clientHeight||!this.workbench.current)return;
    const top=this.messages.scrollTop;
    if(this.reading?.top!==top){
      const following=chatFollowsLatest(this.messages);
      this.reading={top,following,anchor:following?null:captureChatScrollAnchor(this.messages,this.messageList)};
    }
    this.workbench.current.reading=this.reading;
  }
  restoreReading(){
    if(!this.reading||!this.isChatOpen||!this.lastMessages?.length||!this.messages.clientHeight)return;
    const {following,anchor,top}=this.reading;
    if(following)this.messages.scrollTop=this.messages.scrollHeight;
    else if(!restoreChatScrollAnchor(this.messages,this.messageNodes.get(anchor?.key)?.node,anchor))this.messages.scrollTop=top;
    this.reading={following,top:this.messages.scrollTop,anchor:following?null:captureChatScrollAnchor(this.messages,this.messageList)};
  }
  prepareChat(sid){
    this.resetResume();this.rememberReading();this.detailFailure='';this.resetMermaidScope();
    this.modelDialog?.close();this.saveDraft();this.sid=sid;this.workModes.open(JSON.stringify([this.draftGateway,this.provider,sid]));this.historyPage=null;this.historyMessages=[];this.historyBusy=false;this.historyRetry=false;this.draftScope=null;this.draftTouched=false;this.quotes?.reset();this.mode.value='send';this.state=null;this.notificationPolicy=null;this.freshDetail=false;this.detailRevision++;this.modelSelectionRevision++;const row=this.rows.find(r=>r.id===sid);if(row)this.state={...row,readOnly:true,capabilities:{send:false,stop:false}};this.title.textContent=row?.title||BridgeI18n.t('正在读取聊天…');this.status.textContent=BridgeI18n.t('正在连接…');this.input.value=this.draftGateway&&this.composerDrafts?'':this.drafts.get(this.provider+':'+sid)||'';this.requestStamp='';this.error.textContent='';this.notice.hidden=true;this.messageList.replaceChildren(this.translated('p','正在读取聊天…','muted'));this.messageNodes.clear();this.lastMessages=[];this.requests.replaceChildren();this.showSelection(true);this.attachments.open(JSON.stringify([this.provider,sid]));this.workbench.open(sid,'local');this.reading=this.workbench.current.reading||{top:0,following:true};const cached=this.detailCache.get(JSON.stringify([this.provider,sid]));if(cached&&typeof renderMarkdown==='function'){this.applyDetail(cached,true);}this.activity.read(this.provider+'|'+sid);this.updateComposer();this.renderList();this.resizeInput();this.rememberView();this.onNavigation?.();
  }
  async open(sid,{continueSelected=false}={}){
    if(this.previewOnly)return;
    const generation=this.generation;
    if(this.sid===sid&&this.isChatOpen){if(this.workbench.current?.active!=='chat')this.workbench.select('chat');this.activity.read(this.provider+'|'+sid);this.onNavigation?.();}
    else this.prepareChat(sid);
    if(this.resumeAttempt)return;
    if(continueSelected)this.resumeSelected=sid;
    try{
      await this.detail(generation);
      if(generation!==this.generation||sid!==this.sid)return;
      this.loadNotificationPolicy();
      const client=this.clientStates?.find(row=>row.id===this.provider);
      if(continueSelected&&this.resumeSelected===sid&&this.provider==='zcode'&&client?.pipeReady===true&&this.canResume()){this.resumeSelected=null;await this.continueChat();}
    }catch(error){if(generation===this.generation&&sid===this.sid)this.showReadFailure(error);}
  }
  async detail(generation){
    const sid=this.sid,provider=this.provider,revision=this.detailRevision;
    if(window.BridgeFeatures)await window.BridgeFeatures.load('chat');
    if(generation!==this.generation||provider!==this.provider||sid!==this.sid||revision!==this.detailRevision)return;
    if(!this.mermaidScope)this.resetMermaidScope();
    const task=this.sharedRead(provider,'detail',sid),current=()=>task.current()&&generation===this.generation&&provider===this.provider&&sid===this.sid&&revision===this.detailRevision;let value;
    try{value=await task.promise;}catch(error){if(current()){this.staleDetail();throw error;}return;}if(!current())return;this.applyDetail(value);
  }
  canMutate(){const client=this.clientStates?.find(row=>row.id===this.provider);if(this.resumeAttempt||!this.state||this.state.readOnly||!this.freshDetail||client?.enabled===false||this.state.connectionState==='detached'&&this.state.capabilities?.resume===true)return false;if(ClientIdentity.family(this.provider)==='claude'&&this.state.backend==='cli')return ['ready','active','awaiting'].includes(this.state.connectionState);return !client||client.connected===true||this.state.controlScope==='session'&&this.state.connectionState==='ready'&&client.historyAvailable===true;}
  canResume(){const client=this.clientStates?.find(row=>row.id===this.provider);return this.freshDetail&&client?.enabled!==false&&this.state?.capabilities?.resume===true&&(this.state.connectionState==='detached'||ClientIdentity.family(this.provider)==='claude'&&this.state.backend==='cli');}
  resumeStorageKey(){
    return this.draftGateway&&this.sid?'desktop-resume:'+this.provider+':'+JSON.stringify([this.draftGateway,this.sid]):'';
  }
  savedResumeId(){
    const key=this.resumeStorageKey();if(!key)return null;
    const id=sessionStorage.getItem(key);
    return typeof id==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id)?id:null;
  }
  resetResume(finished=false){
    const attempt=this.resumeAttempt;this.resumeAttempt=null;this.resumeSelected=null;
    if(attempt){clearTimeout(attempt.timer);attempt.controller?.abort();}
    if(finished){const key=attempt?.key||this.resumeStorageKey();if(key)sessionStorage.removeItem(key);}
  }
  forgetResumes(provider){
    if(!provider||provider===this.provider)this.resetResume();
    const prefix='desktop-resume:'+(provider?provider+':':'');
    for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith(prefix))sessionStorage.removeItem(key);}
  }
  continueChat(){
    if(!this.canResume())return;
    if(this.state?.capabilities?.progressiveResume!==true)return this.mutate('resume',{});
    if(this.resumeAttempt)return;
    this.resumeSelected=null;
    const key=this.resumeStorageKey(),id=this.savedResumeId()||this.requestId();
    if(key)sessionStorage.setItem(key,id);
    this.invalidateReads(this.provider);this.resumeAttempt={id,sid:this.sid,key,delay:1000,timeout:12000};
    this.error.textContent='';this.renderNotice();
    return this.checkResume(this.resumeAttempt);
  }
  async checkResume(attempt){
    if(!attempt||attempt!==this.resumeAttempt||attempt.controller)return;
    clearTimeout(attempt.timer);attempt.timer=null;
    if(document.hidden||this.root.hidden||!this.isChatOpen||this.previewOnly||this.sid!==attempt.sid)return;
    if(this.workbench.isFileVisible)return;
    if(navigator.onLine===false){attempt.waitingNetwork=true;this.renderNotice();return;}
    const controller=new AbortController(),started=Date.now();attempt.controller=controller;attempt.wake=false;
    const timeout=setTimeout(()=>controller.abort(),attempt.timeout);
    try{
      const result=await this.request(this.path('resume',null),{sessionId:attempt.sid,id:attempt.id,progressive:true},controller.signal,()=>this.resumeAttempt===attempt);
      if(attempt!==this.resumeAttempt||document.hidden||this.root.hidden||this.workbench.isFileVisible)return;
      const elapsed=Date.now()-started;
      attempt.timeout=Math.min(90000,Math.max(12000,attempt.timeout*.8,elapsed*2));
      if(result.status==='ready'){
        this.resetResume(true);this.invalidateReads(this.provider);this.renderNotice();this.updateComposer();
        const generation=this.generation,sid=this.sid,current=()=>this.generation===generation&&this.sid===sid;
        try{await this.detail(generation);}catch(error){if(current())this.showReadFailure(error);}
        if(current()){clearTimeout(this.timer);this.timer=setTimeout(()=>this.refresh(),2000);}
        return;
      }
      if(result.status!=='pending')throw Error(result.message||'未能接续原会话，请重试');
      attempt.waitingNetwork=false;
      if(Number.isInteger(result.progress)&&result.progress!==attempt.progress){attempt.delay=1000;attempt.progress=result.progress;}
      attempt.delay=Math.max(attempt.delay,Math.min(12000,elapsed));
    }catch(error){
      if(attempt!==this.resumeAttempt)return;
      if(error.name==='AbortError'||error.name==='TypeError'||error.name==='SyntaxError'&&error.status===200||[408,429,500,502,503,504,520,521,522,523,524,530].includes(error.status)){
        attempt.waitingNetwork=true;
        if(controller.signal.aborted&&!attempt.wake)attempt.timeout=Math.min(90000,attempt.timeout*2);
      }else{
        this.resetResume(true);this.error.textContent=BridgeI18n.t(error.message);this.status.textContent=BridgeI18n.t('连接失败');this.renderNotice();this.updateComposer();this.refresh();return;
      }
    }finally{clearTimeout(timeout);attempt.controller=null;}
    if(attempt!==this.resumeAttempt)return;
    this.renderNotice();
    if(!document.hidden&&!this.root.hidden&&navigator.onLine!==false){
      attempt.timer=setTimeout(()=>this.checkResume(attempt),attempt.wake?0:attempt.delay);
      attempt.delay=Math.min(12000,attempt.delay*2);
    }
  }
  staleDetail(){this.freshDetail=false;this.renderRequests([]);this.requestStamp='';this.updateComposer();}
  showReadFailure(error){
    this.staleDetail();
    this.detailFailure=error.message;
    if(!this.state){
      this.title.textContent=this.rows.find(row=>row.id===this.sid)?.title||BridgeI18n.t('聊天');
      this.messageList.replaceChildren();
    }
    this.status.textContent=BridgeI18n.t('连接失败');this.renderNotice();
  }
  applyDetail(value,cached=false){
    if(!cached)this.detailFailure='';
    if(!value.session){this.showReadFailure(Error('尚未连接'));return;}
    this.freshDetail=!cached&&value.connected!==false;if(!cached)this.bindDraftScope(value);this.state={...value.session,modelCatalogIdentity:value.modelCatalogIdentity??value.session.modelCatalogIdentity??'',capabilities:value.capabilities||value.session.capabilities||{},maxRequestBytes:value.maxRequestBytes||value.session.maxRequestBytes||0};this.title.textContent=this.state.title;this.status.textContent=BridgeI18n.t(value.readOnly?'本机历史（只读）':cached?'正在连接…':!this.canMutate()?(this.state.backend==='cli'?'历史记录':'尚未连接'):this.state.requests?.length?'等待回应':this.state.status==='active'?'运行中':'已连接');this.updateComposer();this.applyHistory(value,cached);
    const requests=this.canMutate()?this.state.requests||[]:[],stamp=JSON.stringify([requests,BridgeI18n.language()]);if(stamp!==this.requestStamp){this.renderRequests(requests);this.requestStamp=stamp;}
    if(this.workbench.current)this.workbench.current.capabilities={...this.workbench.current.capabilities,tasks:this.state.capabilities.tasks===true};
    this.detailNotice=value.notice||'';this.renderNotice();if(!cached)this.updateActivity([this.state]);this.renderIndicators();
    if(!cached){
      if(this.canMutate()&&this.savedResumeId()){
        this.resetResume(true);
      }else if(this.canResume()&&this.state.capabilities.progressiveResume===true&&!this.resumeAttempt&&
               (this.resumeSelected===this.sid||this.savedResumeId())){
        this.continueChat();
      }
      this.onDetailReady?.();
    }
  }
  renderNotice(){
    if(this.resumeAttempt){const message=this.resumeAttempt.waitingNetwork?'正在等待网络恢复…':'正在接续原会话…';this.status.textContent=BridgeI18n.t(message);this.notice.textContent=BridgeI18n.t(message);this.notice.hidden=false;return;}
    if(this.detailFailure){this.notice.textContent=BridgeI18n.t(this.detailFailure);this.notice.append(this.readRetry);this.notice.hidden=false;return;}
    const resumable=this.canResume();this.notice.textContent=BridgeI18n.t(this.detailNotice||(resumable?'可先阅读聊天历史；继续时将恢复原会话。':''));this.notice.hidden=!this.detailNotice&&!resumable;if(resumable)this.notice.append(this.resumeButton);}
  sendingHere(preview=this.sendingPreview){return !!preview&&preview.provider===this.provider&&preview.sid===this.sid&&preview.gateway===this.draftGateway&&preview.account===(this.draftScope?.account||'');}
  updateComposer(){
    if(this.state){renderChatProgress(this.progress,{agentName:this.clientStates?.find(row=>row.id===this.provider)?.name||this.provider,active:this.state.status==='active',waiting:!!this.state.requests?.length,blocked:['blocked','failed','error'].includes(this.state.turnStatus||this.state.status),connected:this.canMutate(),fresh:this.freshDetail});}else this.progress.hidden=true;
    const sending=this.sendingHere();this.sendFeedback.hidden=!sending;this.sendFeedback.replaceChildren();if(sending){const status=this.translated('strong','正在发送…');status.setAttribute('role','status');this.sendFeedback.append(status,this.node('p','',this.sendingPreview.text));}
    this.send.textContent=BridgeI18n.t(sending?'正在发送…':'发送 ↑');this.send.dataset.i18n=sending?'正在发送…':'发送 ↑';
    const state=this.state,caps=state?.capabilities||{},active=state?.status==='active',ready=this.canMutate();this.form.classList.add('has-composer-presentation');this.modelName.textContent=(state?.modelDisplayName??state?.model)||BridgeI18n.t('模型');delete this.modelName.dataset.i18n;this.modelEffort.textContent=state?.effort?'· '+state.effort:'';this.modelEffort.hidden=!state?.effort;this.modelButton.title=[(state?.modelDisplayName??state?.model)||BridgeI18n.t('模型'),state?.effort].filter(Boolean).join(' · ');this.modelButton.disabled=!ready||!!this.busy||caps.models!==true;
    const permission=state?.permissionLabel||({'default':'默认权限','acceptEdits':'允许文件编辑','plan':'计划模式','bypassPermissions':'完全访问'}[state?.permissionMode])||state?.permissionMode||'权限';this.permissionsButton.textContent=BridgeI18n.t(permission);this.permissionsButton.dataset.i18n=permission;this.renderContext();
    this.resumeButton.textContent=BridgeI18n.t('继续会话');this.resumeButton.hidden=!this.canResume();this.resumeButton.disabled=!!this.busy||!this.canResume();
    const canAttach=caps.attachments===true,canReference=!!state?.cwd&&caps.projectReferences===true;this.attachmentInput.accept=(caps.uploadFiles===true||ClientIdentity.family(this.provider)==='claude'&&state?.backend==='cli')?'':'image/png,image/jpeg,image/gif,image/webp';this.attachButton.hidden=false;this.form.classList.add('ds-has-attachments');if(this.attachments){const locked=!ready||!!this.busy||!canAttach&&!canReference;if(this.attachments.locked!==locked)this.attachments.setLocked(locked);this.attachmentList.hidden=!this.attachments.rows.length&&!this.attachments.error;}
    this.attachButton.title=BridgeI18n.t('添加文件或图片')+(state?.maxRequestBytes?' · '+BridgeI18n.t('消息和图片编码后合计最多 10 MiB'):'');this.skillsButton.hidden=false;this.skillsButton.disabled=!ready||!!this.busy||caps.skills!==true;this.renderSkillPills();
    this.send.disabled=!ready||!!this.busy||caps.send!==true||this.attachments.references().length>0&&caps.projectReferences!==true||(!this.attachments.ready()||this.overRequestLimit(this.attachments.rows))||[...this.selectedSkills().values()].some(s=>s.invalid);this.stop.disabled=!ready||!active||!!this.busy;this.stop.hidden=!active||caps.stop===false;this.permissionsButton.hidden=false;this.permissionsButton.disabled=!ready||!!this.busy||caps.permissions!==true;this.mode.disabled=!ready||!!this.busy||caps.send!==true;
    this.workMode.hidden=false;this.workModes.render(state,this.mode.value,!!this.busy||!ready);this.workMode.disabled=this.workMode.disabled||this.mode.value!=='send'||caps.plan!==true&&caps.goal!==true;
    for(const [control,values] of [[this.modelButton,[caps.models]],[this.skillsButton,[caps.skills]],[this.permissionsButton,[caps.permissions]],[this.attachButton,[caps.attachments,caps.projectReferences]],[this.workMode,[caps.plan,caps.goal]]]){const capabilityState=values.some(value=>value===true)?'supported':values.every(value=>value===false)?'unsupported':'unknown';control.dataset.capabilityState=capabilityState;if(capabilityState!=='supported')control.title=BridgeI18n.t(capabilityState==='unsupported'?'当前接入不支持此功能':'正在确认可用功能…');else if(control===this.skillsButton||control===this.permissionsButton)control.title='';}
    this.mode.options[1].disabled=caps.queue!==true;if(this.mode.options[1].disabled&&this.mode.value==='queue')this.mode.value='send';const steer=this.mode.options[2];steer.disabled=!active||caps.steer!==true;if(steer.disabled&&this.mode.value==='steer')this.mode.value='send';
    this.send.disabled=this.send.disabled||caps.sendModes?.[this.mode.value]===false;
    this.commands?.refresh();
  }
  renderContext(){
    this.contextUsage.update({...this.state,id:JSON.stringify([this.draftGateway,this.provider,this.sid]),connected:this.freshDetail,busy:!!this.busy});this.contextButton.disabled=!this.state;
  }
  contextDetails(){this.contextUsage.open();}
  resizeInput(){if(this.composerBody.hidden)return;this.input.style.height='auto';this.input.style.height=Math.min(160,Math.max(48,this.input.scrollHeight||48))+'px';}
  collapseComposer(value){this.composerBody.hidden=value;this.form.classList.toggle('is-collapsed',value);this.composerToggle.setAttribute('aria-expanded',String(!value));this.label(this.composerToggle,value?'展开输入区':'收起输入区');this.composerToggle.title=BridgeI18n.t(value?'展开输入区':'收起输入区');if(!value)this.resizeInput();}
  applyHistory(value,cached=false){
    const incoming=value.messages||[],previous=this.historyMessages||[];
    const overlap=incoming.length?previous.findIndex(message=>message.id!=null&&message.id===incoming[0].id):-1;
    // Keep a previously loaded prefix only when the newest page overlaps it.
    // Rollback/replacement without an overlap explicitly replaces the display.
    const prefix=overlap>0?previous.slice(0,overlap):[];
    this.historyMessages=[...prefix,...incoming];
    this.sessionFiles=prefix.length?Array.from(new Map([...(this.sessionFiles||[]),...(value.files||[])].map(file=>[file.id,file])).values()):value.files||[];
    if(!prefix.length||!this.historyPage)this.historyPage=cached?null:value.history||null;
    this.renderMessages(this.historyMessages);
  }
  async loadOlderHistory(){
    if(this.historyBusy||!this.historyPage?.hasMore)return;
    const generation=this.generation,sid=this.sid,provider=this.provider,revision=this.detailRevision;
    if(this.historyRetry){this.historyMessages=[];this.historyPage=null;this.historyRetry=false;await this.detail(generation);return;}
    const before=this.historyPage.before;
    this.historyBusy=true;this.renderMessages(this.historyMessages||[]);
    try{
      const value=await this.request(this.path('detail',sid,provider)+'&'+new URLSearchParams({historyBefore:before,historyLimit:40}));
      if(generation!==this.generation||sid!==this.sid||provider!==this.provider||revision!==this.detailRevision)return;
      // Historical pages must never replace current requests, access or status.
      const existing=this.historyMessages||[],ids=new Set(existing.map(message=>message.id));
      const older=(value.messages||[]).filter(message=>!ids.has(message.id));
      this.reading={following:false,top:this.messages.scrollTop,anchor:captureChatScrollAnchor(this.messages,this.messageList)};
      this.historyMessages=[...older,...existing];this.historyPage=value.history||null;
      this.sessionFiles=Array.from(new Map([...(value.files||[]),...(this.sessionFiles||[])].map(file=>[file.id,file])).values());
      this.renderMessages(this.historyMessages);
    }catch(error){if(generation===this.generation&&sid===this.sid){this.historyRetry=true;this.notify(error.message);}}
    finally{if(generation===this.generation&&sid===this.sid){this.historyBusy=false;this.renderMessages(this.historyMessages||[]);}}
  }
  renderMessages(messages){
    this.rememberReading();this.lastMessages=messages;const nodes=[],next=new Map();
    for(const [index,message] of messages.entries()){
      const key=String(message.id??index),stamp=JSON.stringify([message,BridgeI18n.language(),this.sessionFiles,this.state?.capabilities?.fork,this.state?.capabilities?.editMessage,this.freshDetail]);const prior=this.messageNodes.get(key);let node=prior?.stamp===stamp?prior.node:null;
      if(!node){const activity=['activity','reasoning'].includes(message.role);node=this.node(activity?'details':'article',activity?'activity timeline-row':'message timeline-row '+message.role);if(activity){node.append(this.node('summary','',BridgeI18n.t(message.title||(message.role==='reasoning'?'思考摘要':'执行过程'))));node.append(this.node('pre','activity-body',message.text||''));}
        else{const who=message.role==='user'?BridgeI18n.t('你'):message.role==='error'?BridgeI18n.t('执行错误'):[this.clientStates?.find(client=>client.id===this.provider)?.name||this.provider,message.model&&[message.model,message.effort].filter(Boolean).join(' ')].filter(Boolean).join(' · ');node.append(this.node('span','who',who));const body=this.node('div','message-body');body.__mermaidScope=this.mermaidScope;body.__mermaidIdentity=key;renderMarkdown(body,message.text||'',this.sessionFiles||[],file=>this.workbench.url(this.workbench.current,'download',file.path));node.append(body);const actions=this.node('div','message-actions');const copy=this.button('复制',()=>window.BridgeClipboard.copy(()=>message.text||'',copy));copy.classList.add('message-copy');actions.append(copy);if(['user','assistant'].includes(message.role))actions.append(this.button('引用',()=>{this.quotes?.quoteMessage(()=>message.text||'',message.id||key,node);this.collapseComposer(false);}));if(message.role==='assistant'&&message.forkMessageId&&this.state?.capabilities?.fork===true){const branch=this.button('从这里分支',()=>this.fork(message.forkMessageId));branch.disabled=!this.freshDetail||!this.canMutate()||!!this.busy;actions.append(branch);}if(message.role==='user'&&message.editMessageId&&this.state?.capabilities?.editMessage===true){const edit=this.button('编辑并重新发送',()=>this.editMessage(message));edit.disabled=!this.freshDetail||!this.canMutate()||!!this.busy;actions.append(edit);}renderChatMessageTime(actions,message.timestamp);node.append(actions);}
      }
      if(!prior||prior.node!==node)renderChatAttachments(node,message.attachments,{fileUrl:item=>{const file=(this.sessionFiles||[]).find(file=>file.reference===item.path||file.references?.includes(item.path));return file?this.workbench.url(this.workbench.current,'download',file.path):null;},url:item=>{if(!/^[0-9a-f]{64}$/.test(item.desktopId||''))return null;const source=this.path('images/'+item.desktopId,this.sid,this.provider);return {preview:source,original:source};}});
      node.dataset.key=key;node.hidden=message.role==='reasoning'?document.documentElement.dataset.showReasoning==='false':message.role==='activity'&&document.documentElement.dataset.showProcess==='false';next.set(key,{stamp,node});nodes.push(node);
    }
    if(this.historyPage?.hasMore){const older=this.button(this.historyRetry?'历史加载失败，点击重试':this.historyBusy?'正在读取…':'加载更早内容',()=>this.loadOlderHistory());older.classList.add('plain','ds-history-older');older.disabled=!!this.historyBusy;nodes.unshift(older);}this.messageNodes=next;if(!nodes.length&&this.state)nodes.push(this.translated('p','这条聊天还没有消息','muted ds-history-empty'));nodes.push(this.progress);syncChatMessageNodes(this.messageList,nodes);this.restoreReading();
  }
  editMessage(message){
    if(!this.messageActions||!this.freshDetail||!this.canMutate()||this.state?.capabilities?.editMessage!==true||this.busy)return;
    const sid=this.sid,provider=this.provider,generation=this.generation,current=()=>sid===this.sid&&provider===this.provider&&generation===this.generation;
    const timeline={epoch:sid,abort:{signal:{get aborted(){return !current();}}},url:()=>this.path('edit-message',sid,provider),fullText:async()=>message.text||''};
    this.messageActions.open('edit',{key:message.editMessageId,version:message.editMessageId},timeline,{
      request:async(_url,attempt)=>{
        if(!current())throw Error('聊天已切换，请重新选择消息');
        const result=await this.mutate('edit-message',{messageId:message.editMessageId,text:attempt.text},sid,attempt.id);
        if(!result)throw Error(this.error.textContent||'结果待核对，请检查桌面原会话');
        return {status:result.status,id:result.result?.id||sid,source:{id:sid}};
      },refresh:()=>{if(current())this.refresh();},
    });
  }
  async fork(messageId){
    if(!this.freshDetail||!this.canMutate()||this.state?.capabilities?.fork!==true||this.busy)return;
    const sid=this.sid,provider=this.provider,generation=this.generation;this.saveDraft();
    const result=await this.mutate('fork',{messageId},sid);
    if(result?.result?.id&&provider===this.provider&&sid===this.sid&&generation===this.generation)await this.open(result.result.id);
  }
  renderRequests(requests){const saved=new Map([...this.requests.children].map(form=>[form.dataset.requestKey,new Map((form.answerInputs||[]).map(({key,area,custom,skipInput})=>[key,{values:[...area.querySelectorAll('input:checked')].map(n=>n.value),custom:custom.value,skipped:Boolean(skipInput?.checked)}]))]));this.requests.replaceChildren();for(const request of requests){const sid=this.sid,generation=this.generation,form=this.node('form','approval');form.dataset.requestKey=JSON.stringify([this.provider,sid,request.id]);const answers=saved.get(form.dataset.requestKey);form.append(this.node('strong','',BridgeI18n.t(request.tool||'等待确认')));const inputs=[];
    if(request.needsInput){for(const [i,q] of (request.input?.questions||[]).entries()){const key=String(q.id||q.question||i);const label=this.node('fieldset','ds-question');label.append(this.node('legend','',q.question||q.header||BridgeI18n.t('问题')));const area=this.node('div'),custom=this.node('input');let skipInput=null;for(const option of q.options||[]){const line=this.node('label','ds-answer-option');const check=this.node('input');check.type=q.multiSelect?'checkbox':'radio';check.name='q'+i;check.value=option.label;check.checked=answers?.get(key)?.values.includes(option.label)||false;check.onchange=()=>{if(!check.checked)return;if(skipInput)skipInput.checked=false;if(!q.multiSelect&&request.exclusiveWriteIn)custom.value='';};line.append(check,document.createTextNode(option.label+(option.description?' — '+option.description:'')));area.append(line);}custom.value=answers?.get(key)?.custom||'';custom.placeholder=BridgeI18n.t('补充回答');this.label(custom,'补充回答');custom.oninput=()=>{if(!custom.value.trim())return;if(skipInput)skipInput.checked=false;if(!q.multiSelect&&request.exclusiveWriteIn)for(const n of area.querySelectorAll('input:checked'))n.checked=false;};label.append(area,custom);if(q.canSkip){const skipLine=this.node('label','ds-answer-option ds-skip-option');skipInput=this.node('input');skipInput.type='checkbox';skipInput.checked=Boolean(answers?.get(key)?.skipped);skipInput.onchange=()=>{if(!skipInput.checked)return;for(const n of area.querySelectorAll('input:checked'))n.checked=false;custom.value='';};skipLine.append(skipInput,document.createTextNode(BridgeI18n.t('跳过此题')));label.append(skipLine);}form.append(label);inputs.push({key,area,custom,skipInput});}}
    else form.append(this.node('pre','',JSON.stringify(request.input||{},null,2)));
    const actions=this.node('div','ds-actions ds-request-actions');
    if(Array.isArray(request.decisions)){for(const choice of request.decisions){const btn=this.button(choice.label,()=>{if(!this.freshDetail||sid!==this.sid||generation!==this.generation)return;const all=actions.querySelectorAll('button');for(const b of all)b.disabled=true;return this.mutate('respond',{requestId:request.id,decision:choice.id}).finally(()=>{for(const b of all)b.disabled=false;});});if(choice.id==='accept'||choice.id==='accept_once')btn.className='primary';actions.append(btn);}form.append(actions);this.requests.append(form);continue;}
    form.answerInputs=inputs;const accept=this.button(request.needsInput?'提交回答':'允许本次',()=>{});accept.type='submit';accept.className='primary';actions.append(accept);
    // Harness question events have no denial vocabulary unless the adapter explicitly allows cancellation.
    if(!request.needsInput||ClientIdentity.family(this.provider)==='claude'||request.canCancel===true)actions.append(this.button(request.cancelLabel||'拒绝',()=>{if(!this.freshDetail||sid!==this.sid||generation!==this.generation)return;const all=actions.querySelectorAll('button');for(const b of all)b.disabled=true;return this.mutate('respond',{requestId:request.id,decision:'decline'}).finally(()=>{for(const b of all)b.disabled=false;});}));
    form.append(actions);
    form.onsubmit=event=>{event.preventDefault();if(!this.freshDetail||sid!==this.sid||generation!==this.generation)return;const answers={};for(const {key,area,custom,skipInput} of inputs){if(skipInput?.checked){answers[key]={answers:[],skipped:true};continue;}const values=[...area.querySelectorAll('input:checked')].map(n=>n.value);if(custom.value.trim())values.push(custom.value.trim());if(!values.length){this.notify(BridgeI18n.t('请回答每一项问题'));return;}answers[key]={answers:values};}const all=actions.querySelectorAll('button');for(const b of all)b.disabled=true;this.mutate('respond',{requestId:request.id,decision:'accept',answers}).finally(()=>{for(const b of all)b.disabled=false;});};this.requests.append(form);
  }}
  requestId(){if(typeof uuid==='function')return uuid();if(typeof crypto.randomUUID==='function')return crypto.randomUUID();const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);}
  pendingKey(action,body,sessionId,provider=this.provider,account=''){if(action==='create'&&window.BridgeCreationModelPicker)body=window.BridgeCreationModelPicker.intent(body);return provider+':'+sessionId+':'+action+':'+JSON.stringify(body)+(action==='send'?':'+this.draftGateway+':'+account:'');}
  async mutate(action,body,sessionId=action==='create'?null:this.sid,requestId=null,draftAccount=''){
    if(this.busy||sessionId&&(!(action==='resume'?this.canResume():this.canMutate())||sessionId!==this.sid))return null;const provider=this.provider,generation=this.generation,key=this.pendingKey(action,body,sessionId,provider,draftAccount);this.invalidateReads(provider);const epoch=this.readEpoch,providerEpoch=this.providerReadEpochs.get(provider),currentWrite=()=>epoch===this.readEpoch&&providerEpoch===this.providerReadEpochs.get(provider);this.busy=true;this.error.textContent='';this.updateComposer();
    try{let id=requestId||this.pending.get(key);if(!id)id=this.requestId();if(action==='send'||!requestId)this.pending.set(key,id);const result=await this.request(this.path(action,null,provider),{...body,id,sessionId});if(action==='resume'&&result.status==='blocked')this.pending.delete(key);if(action==='create'&&result.status==='blocked'){this.pending.delete(key);return result;}if(!['accepted','queued','ready'].includes(result.status))throw Error(result.message||'结果待核对，请检查桌面原会话');this.pending.delete(key);if(action==='access'&&result.mode&&provider===this.provider&&sessionId===this.sid&&generation===this.generation&&currentWrite()&&this.state){this.state.permissionMode=result.mode;const option=result.options?.find(row=>(row.value??row.id)===result.mode);this.state.permissionLabel=option?.label||option?.name||null;}return result;}
    catch(error){if(action!=='send'||this.sendingHere()){if(generation===this.generation&&provider===this.provider&&sessionId===this.sid)this.error.textContent=BridgeI18n.t(error.message);this.notify(BridgeI18n.t(error.message));}return null;}
    finally{const current=currentWrite();if(current)this.invalidateReads(provider);this.busy=false;this.updateComposer();if(current&&provider===this.provider)this.refresh();}
  }
  attachmentUrl(key,id,action){const [provider,sid]=JSON.parse(key);return this.path('uploads/'+encodeURIComponent(id)+'/'+action,sid,provider);}
  async uploadBinary(url,data,type){const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':type||'application/octet-stream','X-CSRF-Token':this.csrf()},body:data});const result=await response.json();if(!response.ok){if(response.status===401)this.onUnauthorized();throw Error(result.error||BridgeI18n.t('附件上传失败，请重试'));}return result;}
  uploadAttachment(key,id,file){const [provider,sid]=JSON.parse(key);return this.uploadBinary(this.path('uploads',sid,provider)+'&id='+encodeURIComponent(id)+'&name='+encodeURIComponent(file.name),file,'application/octet-stream');}
  requestLimitMessage(){return 'Claude 单次消息与图片编码后不能超过 10 MiB，请减少附件';}
  overRequestLimit(rows){const limit=this.state?.maxRequestBytes;if(!limit)return false;return rows.filter(row=>!row.reference).reduce((total,row)=>total+Math.ceil(row.size/3)*4,0)+new TextEncoder().encode(JSON.stringify(this.input.value)).length>limit;}
  selectedSkills(){const key=JSON.stringify([this.provider,this.sid]);if(!this.skillSelections.has(key))this.skillSelections.set(key,new Map());return this.skillSelections.get(key);}
  renderSkillPills(){
    const selected=this.selectedSkills();this.skillPills.replaceChildren();this.skillsButton.textContent=selected.size?'Skill ('+selected.size+')':'Skill';delete this.skillsButton.dataset.i18n;
    for(const [id,skill] of selected){const pill=this.button((skill.name||id)+' ×',()=>{selected.delete(id);this.updateComposer();this.saveDraft();});pill.className='skill-pill'+(skill.invalid?' invalid':'');pill.disabled=!!this.busy;this.skillPills.append(pill);}this.skillPills.hidden=this.state?.capabilities?.skills!==true&&this.state?.composerCapabilities?.skills!==true||!selected.size;
  }
  async skills(){
    if(!this.sid||this.state?.capabilities?.skills!==true)return;const sid=this.sid,generation=this.generation,selected=this.selectedSkills(),dialog=this.dialog('Skill'),loading=this.translated('p','正在读取已安装 Skill…','muted');dialog.append(loading);dialog.showModal();
    try{const catalog=await this.request(this.path('catalog'));if(sid!==this.sid||generation!==this.generation||!dialog.open)return dialog.close();loading.remove();const rows=catalog.skills||[],offered=new Set(rows.filter(s=>s.selectable!==false&&catalog.capabilities?.skills!==false).map(s=>s.id));for(const [id,skill] of selected)skill.invalid=!offered.has(id);this.updateComposer();
      const search=this.node('input','search'),list=this.node('div','skill-list');search.placeholder=BridgeI18n.t('搜索 Skill');this.label(search,'搜索 Skill');const render=()=>{list.replaceChildren();const query=search.value.trim().toLocaleLowerCase();for(const skill of rows.filter(s=>[s.name,s.description].join(' ').toLocaleLowerCase().includes(query))){const label=this.node('label','skill-option'),check=this.node('input'),info=this.node('span');check.type='checkbox';check.checked=selected.has(skill.id);check.disabled=!offered.has(skill.id);check.onchange=()=>{if(generation!==this.generation||sid!==this.sid)return dialog.close();if(check.checked&&this.state?.backend==='cli')selected.clear();else if(check.checked&&selected.size>=8){check.checked=false;this.notify(BridgeI18n.t('最多选择 8 个 Skill'));return;}if(check.checked)selected.set(skill.id,{id:skill.id,name:skill.name||skill.id});else selected.delete(skill.id);this.updateComposer();this.saveDraft();if(this.state?.backend==='cli')render();};info.append(this.node('strong','',skill.name||skill.id),this.node('small','',skill.description||''));if(skill.kind==='plugin')info.append(this.node('small','',BridgeI18n.t('插件')+' · '+BridgeI18n.t(skill.status||'只读')));label.append(check,info);list.append(label);}if(!list.children.length)list.append(this.translated('p','没有匹配的 Skill','muted'));};search.oninput=render;render();dialog.append(search,list);if(catalog.pluginError)dialog.append(this.node('p','error',BridgeI18n.t(catalog.pluginError)));
    }catch(error){if(dialog.open){loading.textContent=BridgeI18n.t(error.message);loading.className='error';}}
  }
  async goalAction(operation,payload){
    const {id,...body}=payload;const result=await this.mutate('goal',{...body,operation},this.sid,id);
    if(!result)throw Error(this.error.textContent||'目标操作尚未确认');
    return result;
  }
  async submit(){
    const raw=this.input.value,submittedQuotes=JSON.stringify(this.quotes?.quotes||[]),text=(this.quotes?this.quotes.serialize(raw):raw).trim(),ids=this.state?.capabilities?.attachments===true?this.attachments.ids():[],references=this.attachments.references(),submittedAttachmentIds=this.attachments.rows.map(row=>row.id),selected=this.selectedSkills();
    if((!text&&!ids.length&&!references.length)||!this.sid||!this.state||!this.freshDetail||this.busy||this.state.capabilities.send!==true||this.mode.value==='queue'&&this.state.capabilities.queue!==true||this.mode.value==='steer'&&(this.state.status!=='active'||this.state.capabilities.steer!==true)||references.length&&this.state.capabilities.projectReferences!==true||ids.length&&!this.attachments.ready())return;
    if(text.length>100000){this.error.textContent=BridgeI18n.t('消息含引用超过 100000 字符，请缩短后发送');return;}
    if(this.overRequestLimit(ids.length?this.attachments.rows:[])){this.error.textContent=BridgeI18n.t(this.requestLimitMessage());return;}
    if([...selected.values()].some(s=>s.invalid)){this.error.textContent=BridgeI18n.t('已选 Skill 已失效，请刷新列表后重新选择');return;}
    const sid=this.sid,provider=this.provider,key=JSON.stringify([provider,sid]),scope=this.draftScope;
    const body={text,mode:this.mode.value,...!this.workMode.hidden&&!this.workMode.disabled&&this.mode.value==='send'?{workMode:this.workModes.value('send')}:{}} ,skills=this.state.capabilities.skills===true?[...selected.keys()].sort():[];
    if(body.workMode==='goal'&&(ids.length||references.length||skills.length)){this.error.textContent=BridgeI18n.t('请先发送附件和 Skill，再单独设定目标');return;}
    if(ids.length)body.attachments=ids;if(references.length)body.references=references;if(skills.length)body.plugins=skills;
    const transientKey=this.pendingKey('send',body,sid,provider),scopedKey=this.pendingKey('send',body,sid,provider,scope?.account||''),fallbackId=this.pending.get(scopedKey)||this.pending.get(transientKey);
    const revision=this.saveDraft()?.revision,provisional=this.provisionalDrafts.get(provider+':'+sid),pending=scope&&this.composerDrafts?this.composerDrafts.begin(scope,body,fallbackId||this.requestId()):null;
    if(pending?.blocked){this.error.textContent=BridgeI18n.t('上次发送结果尚未确认，请检查聊天记录或先处理未确认记录。');this.manageMessages.hidden=false;return;}
    const preview={provider,sid,gateway:this.draftGateway,account:scope?.account||'',text:text||BridgeI18n.t('附件：')+submittedAttachmentIds.length};this.sendingPreview=preview;this.updateComposer();
    let result;try{result=await this.mutate('send',body,sid,pending?.id||fallbackId,scope?.account||'');if(result&&this.sendingPreview===preview&&this.sendingHere(preview))this.notify(BridgeI18n.t(result.status==='queued'?'已加入待发送队列':'已发送到会话'));}
    finally{if(this.sendingPreview===preview)this.sendingPreview=null;this.updateComposer();}
    if(scope&&(pending||this.pending.has(scopedKey)||result))this.pending.delete(transientKey);
    if(result){
      this.workModes.submitted(JSON.stringify([this.draftGateway,provider,sid]),body.workMode);
      const sameRevision=pending?this.composerDrafts.load(scope)?.revision===revision:this.input.value===raw&&JSON.stringify(this.quotes?.quotes||[])===submittedQuotes;
      const confirmed=pending?this.composerDrafts.confirm(scope,pending.id,revision):true;
      if(confirmed&&provisional&&JSON.stringify(this.provisionalDrafts.get(provider+':'+sid))===JSON.stringify(provisional)){this.provisionalDrafts.delete(provider+':'+sid);this.drafts.delete(provider+':'+sid);sessionStorage.removeItem('desktop-draft:'+provider+':'+sid);}
      if(confirmed)this.attachments.clear(key,submittedAttachmentIds);
      if(confirmed&&sameRevision){for(const id of skills)selected.delete(id);
        if(!pending)this.drafts.delete(provider+':'+sid);
        if(sid===this.sid&&provider===this.provider&&(!this.composerDrafts||this.composerDrafts.key(scope)===this.composerDrafts.key(this.draftScope))){
          this.restoringDraft=true;this.input.value='';this.quotes?.reset();this.restoringDraft=false;this.resizeInput();
        }
      }
      this.updateComposer();
    }
  }
  dialog(title){const dialog=this.node('dialog','picker ds-picker'),head=this.node('div','picker-head');head.append(this.translated('h2',title));const close=this.button('×',()=>dialog.close());close.className='icon-button';this.label(close,'关闭');head.append(close);dialog.append(head);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);return dialog;}
  async models(){
    const sid=this.sid,provider=this.provider,generation=this.generation,revision=this.modelSelectionRevision;if(!sid||!this.state||!this.canMutate()||this.state.capabilities.models===false||this.state.composerCapabilities?.models===true&&this.state.capabilities.models!==true)return;
    this.modelDialog?.close();const dialog=this.dialog('模型设置'),head=dialog.querySelector('.picker-head'),close=head.querySelector('.icon-button'),actions=this.node('div','picker-head-actions'),refresh=this.button('刷新',()=>load(true));
    this.modelDialog=dialog;dialog.addEventListener('close',()=>{if(this.modelDialog===dialog)this.modelDialog=null;});dialog.classList.add('ds-model-dialog');refresh.className='tool-button ds-model-refresh';this.label(refresh,'从上游刷新可用模型');refresh.title=BridgeI18n.t('从上游刷新可用模型');const icon=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS('http://www.w3.org/2000/svg','path');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');path.setAttribute('d','M20 12a8 8 0 1 1-2.34-5.66L20 8M20 3v5h-5');icon.append(path);refresh.prepend(icon);close.remove();actions.append(refresh,close);head.append(actions);
    const form=this.node('form','ds-model-form'),modelField=this.node('div','model-field'),modelTitle=this.translated('div','模型','model-section-title'),model=this.node('select'),modelOptions=this.node('div','model-options'),effortField=this.node('div','model-field'),effortTitle=this.translated('div','思考强度','model-section-title'),effort=this.node('select'),effortOptions=this.node('div','effort-options'),notice=this.node('p','muted'),updated=this.node('small','muted'),error=this.node('p','error'),footer=this.node('div','ds-model-actions'),cancel=this.button('取消',()=>dialog.close()),save=this.button('应用到此聊天',()=>{});
    model.hidden=true;effort.hidden=true;model.tabIndex=-1;effort.tabIndex=-1;model.setAttribute('aria-hidden','true');effort.setAttribute('aria-hidden','true');modelOptions.setAttribute('role','radiogroup');effortOptions.setAttribute('role','radiogroup');modelOptions.setAttribute('aria-label',BridgeI18n.t('模型'));effortOptions.setAttribute('aria-label',BridgeI18n.t('思考强度'));save.type='submit';save.className='primary';cancel.type='button';save.disabled=true;modelField.append(modelTitle,model,modelOptions);effortField.append(effortTitle,effort,effortOptions);footer.append(cancel,save);form.append(modelField,effortField,notice,updated,error,footer);dialog.append(form);dialog.showModal();
    const manualModel=this.node('input','ds-manual-model'),manualEffort=this.node('input','ds-manual-effort');manualModel.type='text';manualEffort.type='text';manualModel.placeholder=BridgeI18n.t(ClientIdentity.family(provider)==='deepseek'?'输入 provider/model':'输入模型名称');manualEffort.placeholder=BridgeI18n.t('输入思考强度，例如 high');this.label(manualModel,'手动输入模型名称');this.label(manualEffort,'手动输入思考强度');modelField.append(manualModel);effortField.append(manualEffort);
    const identity=this.state.modelCatalogIdentity||'',namespace=[this.listCacheIdentity,this.listCacheHost||'local',provider],cached=window.BridgeModelCatalogCache.read(this.modelCatalogs,namespace,identity);
    let rows=[],capabilities={...this.state.capabilities},current=this.state.model||'',currentEffort=this.state.effort||'',request=0,saving=false,loadingCatalog=false,needsReload=false,selectedModel='',selectedEffort='',edited=false;
    const active=()=>dialog.open&&sid===this.sid&&provider===this.provider&&generation===this.generation&&revision===this.modelSelectionRevision&&(this.state?.modelCatalogIdentity||'')===identity;
    const effortNames={auto:'自动（模型默认）',none:'无',minimal:'最低',low:'低',medium:'中',high:'高',xhigh:'极高',max:'Max',ultra:'Ultra'};
    const choices=(host,values,selected,kind,disabled=false)=>{host.replaceChildren();for(const value of values){const choice=this.node('label',kind+'-choice'),input=this.node('input'),name=this.node('span',kind==='model'?'model-choice-name':'',kind==='model'?value.name||value.id:BridgeI18n.t(value.name&&value.name!==value.id?value.name:effortNames[value.id]||value.id)),id=value.id;input.type='radio';input.name='ds-'+kind+'-choice';input.value=id;input.checked=id===selected;input.disabled=disabled&&(saving||id!=='__custom__');input.onchange=()=>{if(!input.checked||input.disabled||!active())return;edited=true;if(kind==='model'){selectedModel=id;selectedEffort='';render();}else{selectedEffort=id;renderEffort();}};choice.append(input,name);if(kind==='model'){if(value.quotaTag){const badge=this.node('span','model-choice-badge',value.quotaTag);if(value.quotaStatus)badge.classList.add('badge-'+value.quotaStatus);choice.append(badge);}choice.title=id;const check=this.node('span','model-choice-check','✓');check.setAttribute('aria-hidden','true');choice.append(check);}host.append(choice);}};
    const renderEffort=()=>{const row=rows.find(item=>item.id===selectedModel),options=capabilities.effort===false?[]:(row?.efforts||[]).map(item=>typeof item==='string'?{id:item,name:item}:item).filter(item=>item.id),custom=capabilities.manualEffort===true;
      effortField.hidden=!options.length&&!custom;effort.replaceChildren(...options.map(item=>new Option(item.name||item.id,item.id)));if(selectedEffort!=='__custom__'&&!options.some(item=>item.id===selectedEffort))selectedEffort=options.some(item=>item.id===(selectedModel===current?currentEffort:row?.defaultEffort))?(selectedModel===current?currentEffort:row.defaultEffort):options[0]?.id||'';effort.value=selectedEffort;effortOptions.style.setProperty('--effort-columns',options.length<=4?Math.max(1,options.length):options.length<=6?3:4);choices(effortOptions,[...options,...custom?[{id:'__custom__',name:BridgeI18n.t('手动输入')}]:[]],selectedEffort,'effort',saving||loadingCatalog);
      manualEffort.hidden=selectedEffort!=='__custom__';manualEffort.disabled=saving;manualModel.hidden=selectedModel!=='__custom__';manualModel.disabled=saving;save.disabled=saving||(loadingCatalog&&(selectedModel!=='__custom__'||!identity))||needsReload||!selectedModel||capabilities.models===false||(selectedModel==='__custom__'&&!manualModel.value.trim())||(selectedEffort==='__custom__'&&!manualEffort.value.trim())||!!options.length&&!selectedEffort;};
    const render=()=>{model.value=selectedModel;choices(modelOptions,[...rows,...capabilities.manualModels===true?[{id:'__custom__',name:BridgeI18n.t('手动输入模型名称')}]:[]],selectedModel,'model',saving||loadingCatalog);renderEffort();};
    manualModel.oninput=manualEffort.oninput=()=>{if(active()){edited=true;renderEffort();}};
    const showCatalog=(catalog,preserve=false,fromCache=false)=>{const offered=Array.isArray(catalog.models)?catalog.models.filter(row=>row&&typeof row.id==='string'&&row.id):[];rows=offered;capabilities={...this.state.capabilities,...catalog.capabilities};for(const key of ['manualModels','manualEffort'])if(ClientIdentity.family(provider)==='deepseek'||typeof this.state.capabilities[key]==='boolean')capabilities[key]=this.state.capabilities[key]===true;current=offered.some(row=>row.id===this.state.model)||fromCache?this.state.model||'':catalog.currentModel||this.state.model||'';currentEffort=this.state.effort||catalog.currentEffort||'';const draft=(preserve||edited)&&(selectedModel==='__custom__'||offered.some(row=>row.id===selectedModel));selectedModel=draft?selectedModel:offered.some(row=>row.id===current)?current:capabilities.manualModels===true?'__custom__':'';if(!draft)manualModel.value=current;selectedEffort=draft?selectedEffort:'';model.replaceChildren(...rows.map(row=>new Option(row.name||row.id,row.id)));needsReload=false;render();const stamp=catalog.cache?.updatedAt;updated.textContent=Number.isFinite(stamp)&&stamp>0?BridgeI18n.t('更新于：')+new Date(stamp*1000).toLocaleString(BridgeI18n.locale()):'';notice.replaceChildren();if(catalog.usageSummary&&(Array.isArray(catalog.usageSummary.items)&&catalog.usageSummary.items.length||catalog.usageSummary.credits)){notice.className='ds-usage-card';const cardHead=this.node('div','ds-usage-card-header');cardHead.append(this.node('span','ds-usage-title',catalog.usageSummary.tier||BridgeI18n.t('官方账号额度')));notice.append(cardHead);for(const item of catalog.usageSummary.items||[]){const row=this.node('div','ds-usage-row');row.append(this.node('span','ds-usage-name',item.name),this.node('span','ds-usage-val '+(item.status||''),[item.val,item.note].filter(Boolean).join(' · ')));notice.append(row);}if(catalog.usageSummary.credits)notice.append(this.node('div','ds-usage-credits',catalog.usageSummary.credits));}else{notice.className='muted';notice.textContent=(catalog.notice||(ClientIdentity.family(provider)==='deepseek'&&capabilities.manualModels!==true?'更新 DSH 接入插件后可使用手动输入模型和思考强度':!rows.length?'暂未提供可用模型':current&&!selectedModel?'当前聊天模型不在可用列表中，请选择模型后应用。':'')).split('\n').map(line=>BridgeI18n.t(line)).join('\n');}};
    if(cached?.models?.length)showCatalog(cached,false,true);
    const load=async preserve=>{if(saving||!active())return;const ticket=++request;loadingCatalog=true;refresh.disabled=true;error.textContent='';render();if(!rows.length)notice.textContent=BridgeI18n.t('正在读取模型…');try{let catalog=await this.request(this.path('catalog',sid,provider)+'&section=models'+(preserve?'&refresh=true':''));if(ticket!==request||!active()){if(!active())dialog.close();return;}if(identity&&catalog.cache?.identity!==identity){needsReload=true;throw Error(BridgeI18n.t('账号已切换，请重新打开模型设置'));}catalog={...catalog,...window.BridgeModelCatalogCache.write(this.modelCatalogs,namespace,catalog)};showCatalog(catalog,preserve);}catch(failure){if(ticket===request){if(active())error.textContent=BridgeI18n.t(failure.message);else dialog.close();}}finally{if(ticket===request&&active()){loadingCatalog=false;refresh.disabled=false;render();}}};
    form.onsubmit=async event=>{event.preventDefault();if(!active()||saving||save.disabled)return;request++;saving=true;const body={model:selectedModel==='__custom__'?manualModel.value.trim():selectedModel};if(identity)body.modelCatalogIdentity=identity;if(selectedModel==='__custom__')body.manualModel=true;if(!effortField.hidden&&selectedEffort){body.effort=selectedEffort==='__custom__'?manualEffort.value.trim():selectedEffort;if(selectedEffort==='__custom__')body.manualEffort=true;}refresh.disabled=true;cancel.disabled=true;close.disabled=true;render();error.textContent='';try{const result=await this.mutate('settings',body,sid);if(result&&active())dialog.close();else if(active()&&!result){needsReload=true;error.textContent=this.error.textContent||BridgeI18n.t('结果待核对，请检查桌面原会话');}}finally{saving=false;if(active()){refresh.disabled=false;cancel.disabled=false;close.disabled=false;render();}}};
    if(!cached?.models?.length&&capabilities.manualModels===true){selectedModel='__custom__';manualModel.value=current;}
    await load(false);
  }
  async permissions(){
    if(!this.sid||!this.canMutate()||!this.state?.capabilities?.permissions)return;
    const sid=this.sid,generation=this.generation,value=await this.request(this.path('access'));
    const current=()=>sid===this.sid&&generation===this.generation;
    if(!current())return;
    const dialog=this.dialog('权限');
    for(const option of value.options||[]){
      const mode=option.value??option.id;
      const commit=async confirmed=>{
        if(!current())return dialog.close();
        if(await this.mutate('access',{mode,...(confirmed?{confirmed:true}:{})}))dialog.close();
      };
      const button=this.button('',()=>{
        if(!current())return dialog.close();
        if(!option.requiresConfirmation||mode===value.mode)return commit(mode==='bypassPermissions'||option.requiresConfirmation===true);
        const label=document.createElement('label'),check=document.createElement('input');
        check.type='checkbox';label.append(check,BridgeI18n.t('我了解完全访问允许直接修改文件和执行命令'));
        const submit=this.button('确认启用',()=>check.checked&&commit(true));submit.disabled=true;
        check.onchange=()=>{submit.disabled=!check.checked;};
        dialog.querySelectorAll('.permission-option').forEach(row=>row.remove());dialog.append(label,submit);
      });
      permissionOption(button,mode,option.label||option.name||mode,option.description||'',mode===value.mode);
      dialog.append(button);
    }
    dialog.showModal();
  }
  chatDetails(){
    if(!this.state||!this.sid)return;
    this.onOpenSessionMenu?.({...this.rows.find(row=>row.id===this.sid),...this.state,id:this.sid},this.provider,this.titleButton);
  }

  async loadNotificationPolicy(){
    const sid=this.sid,provider=this.provider,generation=this.generation;if(!sid||this.state?.capabilities?.notifications===false)return;
    try{const value=await this.request(this.path('notifications',sid,provider));if(sid===this.sid&&provider===this.provider&&generation===this.generation){this.notificationPolicy=value;this.updateComposer();}}catch{}
  }
  async notifications(){
    const sid=this.sid,provider=this.provider,generation=this.generation;if(!sid||!this.state)return;
    const dialog=this.dialog('聊天通知设置'),loading=this.translated('p','正在读取…','muted'),current=()=>sid===this.sid&&provider===this.provider&&generation===this.generation&&dialog.open;
    dialog.append(loading);dialog.showModal();
    try{
      const value=await this.request(this.path('notifications',sid,provider));if(!current())return dialog.close();loading.remove();this.notificationPolicy=value;this.updateComposer();
      const form=this.node('form'),fields={};
      for(const [key,title] of [['requests','请求处理通知'],['completion','运行完毕通知']]){
        const label=this.node('label'),select=this.node('select');this.label(select,title);label.append(this.translated('span',title),select);
        for(const [id,text] of [['inherit','跟随全局设置'],['on','开启'],['off','未开启']]){const option=this.translated('option',text);option.value=id;select.add(option);}select.value=value[key]||'inherit';fields[key]=select;form.append(label);
      }
      const error=this.node('p','error');error.setAttribute('role','alert');const save=this.button('保存配置',()=>{});save.type='submit';save.className='primary';
      form.append(this.translated('p',value.available?'通知通道已开启':'尚未配置通知通道，请在第三方通知中配置。','muted'),error,save);
      form.onsubmit=async event=>{event.preventDefault();if(!current())return dialog.close();if(save.disabled)return;save.disabled=true;error.textContent='';try{const policy=await this.request(this.path('notifications',sid,provider),{requests:fields.requests.value,completion:fields.completion.value});if(current()){this.notificationPolicy=policy;this.updateComposer();dialog.close();this.notify(BridgeI18n.t('聊天提醒设置已保存'));}}catch(failure){if(current())error.textContent=BridgeI18n.t(failure.message);}finally{save.disabled=false;}};
      dialog.append(form);
    }catch(error){if(current()){loading.textContent=BridgeI18n.t(error.message);loading.className='error';}}
  }
  async create(){
    if(this.previewOnly)return;
    const generation=this.generation,provider=this.provider,dialog=this.dialog('新建聊天'),loading=this.translated('p','正在读取…','muted');
    dialog.classList.add('project-picker');const heading=dialog.querySelector('h2');heading.classList.add('dialog-content-start');heading.tabIndex=-1;heading.autofocus=true;
    const clientLabel=this.node('p','project-picker-client',this.clientStates?.find(row=>row.id===provider)?.name||provider);dialog.append(clientLabel,loading);dialog.showModal();
    const current=()=>generation===this.generation&&provider===this.provider&&dialog.open;
    try{
      const value=await this.request(this.path('projects',null));if(!current())return dialog.close();loading.remove();
      const projects=value.projects||[],canBrowse=Array.isArray(value.environments)&&value.browseDirectories!==false,environments=Array.isArray(value.environments)?value.environments:ClientIdentity.family(provider)==='claude'?['code','cowork','cli'].filter(kind=>projects.some(row=>row.surface===kind)).map(kind=>({key:kind,surface:kind,name:kind==='code'?'Claude · Code':kind==='cowork'?'Claude · Cowork':'Claude Code CLI'})):[{key:'desktop',surface:'desktop',name:'DeepSeek Harness'}];
      if(environments.length===1){const env=environments[0];clientLabel.textContent=env.host==='local'?(env.name||env.hostLabel||'此电脑').replace('此电脑',BridgeI18n.t('此电脑')):env.name||clientLabel.textContent;}
      const form=this.node('form'),surface=this.node('select'),project=this.node('select'),title=this.node('input'),message=this.node('textarea'),error=this.node('p','error'),workspace=this.node('section','project-picker-workspace'),location=this.node('details','project-picker-location'),summary=this.node('summary'),fullPath=this.node('p','muted'),browse=this.button('选择目录',()=>chooseDirectory());
      this.label(surface,'运行环境');this.label(project,'项目');title.maxLength=120;title.placeholder=BridgeI18n.t('留空将由Agent自行命名');this.label(title,'聊天名称');message.rows=3;message.maxLength=100000;this.label(message,'首条消息');error.setAttribute('role','alert');browse.className='plain project-picker-browse';location.append(summary,fullPath);workspace.append(location,browse);
      const field=(label,input,parent=form)=>{const node=this.node('label');node.append(this.translated('span',label),input);parent.append(node);return node;};
      for(const row of environments)surface.add(new Option(row.host==='local'?(row.name||row.hostLabel||row.key).replace('此电脑',BridgeI18n.t('此电脑')):row.name||row.hostLabel||row.key,row.key));
      const preferred=environments.find(row=>row.surface===(this.state?.surfaceDirKey||this.state?.backend)&&(!this.state?.host||row.host===this.state.host));if(preferred)surface.value=preferred.key;
      const environmentField=field('运行环境',surface);environmentField.hidden=environments.length<2;field('项目',project,workspace);workspace.append(location,browse);form.append(workspace);const titleField=field('聊天名称',title),messageField=field('首条消息',message),actions=this.node('div','project-picker-actions'),cancel=this.button('取消',()=>dialog.close()),create=this.button('创建并打开',()=>{});create.type='submit';create.className='primary';actions.append(cancel,create);form.append(error,actions);dialog.append(form);
      let directory=null,saving=false,trustTarget=null,modelPicker=null;
      const trust=this.button('确认工作区信任…',async()=>{
        const target=trustTarget;if(!target||saving||!current())return;
        saving=true;trust.disabled=true;create.disabled=true;
        try{
          const registered=await this.request(this.path('projects/register',null,provider),{environment:target.environment,path:target.path,createDirectory:false});
          const active=()=>current()&&trustTarget===target&&environment()?.key===target.environment&&path()===target.path;
          if(!active())return;
          const row=registered.project,state=await this.request(this.path('projects/trust',null,provider),{projectKey:row.key,confirmed:false});
          if(!active())return;
          if(!state.trusted){
            if(!await window.BridgeProjectPicker.confirmTrust({path:row.cwd,environment:environment()?.name,isCurrent:active})||!active())return;
            await this.request(this.path('projects/trust',null,provider),{projectKey:row.key,confirmed:true});
          }
          if(active()){trustTarget=null;trust.hidden=true;error.textContent=BridgeI18n.t('已信任工作区，请再次点击创建并发送。');}
        }catch(failure){if(current())error.textContent=BridgeI18n.t(failure.message);}
        finally{saving=false;if(current()){trust.disabled=false;update();}}
      });trust.className='plain';trust.hidden=true;error.after(trust);
      const environment=()=>environments.find(row=>row.key===surface.value),matches=row=>Array.isArray(value.environments)?row.environment===surface.value||!row.environment&&row.surface===environment()?.surface&&(!row.host||row.host===environment()?.host):ClientIdentity.family(provider)!=='claude'||row.surface===surface.value;
      const selected=()=>projects.find(row=>row.key===project.value),path=()=>project.value==='__directory__'?directory?.path:selected()?.cwd;
      const update=()=>{
        if(trustTarget&&(trustTarget.environment!==environment()?.key||trustTarget.path!==path())){trustTarget=null;trust.hidden=true;}
        const env=environment(),cli=env?.surface==='cli',requiresMessage=env?.createRequiresMessage??(ClientIdentity.family(provider)==='claude'||value.createRequiresMessage===true),cwd=path()||'';
        titleField.hidden=cli||env?.createTitle===false;message.required=requiresMessage;messageField.hidden=!requiresMessage;
        create.dataset.i18n=requiresMessage?'创建并发送':'创建并打开';create.textContent=BridgeI18n.t(create.dataset.i18n);create.disabled=saving||!env||!project.value||project.value==='__directory__'&&!directory||!!modelPicker&&!modelPicker.valid();
        location.hidden=!cwd;summary.textContent=cwd.split(/[\\/]/).filter(Boolean).slice(-2).join(' / ')||cwd;fullPath.textContent=cwd;location.open=false;browse.hidden=!canBrowse||!env;browse.dataset.i18n=cwd?'更换目录':'选择目录';browse.textContent=BridgeI18n.t(browse.dataset.i18n);
      };
      const refreshModels=()=>{
        if(value.createModels!==true)return;
        if(!modelPicker){const root=this.node('section');form.insertBefore(root,titleField);modelPicker=new window.BridgeCreationModelPicker({root,cache:this.modelCatalogs,namespace:()=>[this.listCacheIdentity,this.listCacheHost||'local',provider],isCurrent:current,onChange:update});dialog.addEventListener('close',()=>modelPicker.dispose(),{once:true});}
        const env=environment();if(!env||!project.value||project.value==='__directory__'&&!directory){modelPicker.clear();return;}
        const query=new URLSearchParams({environment:env.key,...(project.value==='__directory__'?{path:directory.path}:{projectKey:project.value})});
        const scope=JSON.stringify([this.listCacheIdentity,this.listCacheHost,provider,query.toString()]);
        modelPicker.load(scope,refresh=>this.request(this.path('create-models',null,provider,{...Object.fromEntries(query),...(refresh?{refresh:'true'}:{})})));
      };
      const sync=()=>{directory=null;error.textContent='';project.replaceChildren();for(const row of projects.filter(matches))project.add(new Option(row.name,row.key));if(canBrowse&&environment())project.add(new Option(BridgeI18n.t('选择其他目录…'),'__directory__'));update();refreshModels();};
      const chooseDirectory=()=>{
        const env=environment();if(!env||saving)return;
        return window.BridgeProjectPicker.choose({environment:env.host==='local'?(env.name||env.hostLabel||'此电脑').replace('此电脑',BridgeI18n.t('此电脑')):env.name,path:path()||null,isCurrent:()=>current()&&environment()?.key===env.key,
          request:body=>this.request(this.path('projects/browse',null,provider),{environment:env.key,...body}),
          onSelect:chosen=>{directory=chosen;project.value='__directory__';update();refreshModels();}});
      };
      surface.onchange=sync;project.onchange=()=>{update();refreshModels();};sync();heading.focus({preventScroll:true});
      if(!environments.length||!projects.length&&!canBrowse)error.textContent=BridgeI18n.t('请先在客户端创建一个项目会话');
      form.onsubmit=async event=>{
        event.preventDefault();if(!current())return dialog.close();if(saving||create.disabled)return;
        const env=environment(),cli=env?.surface==='cli';
        if(message.required&&!message.value.trim()){error.textContent=BridgeI18n.t('请输入首条消息');return;}
        saving=true;modelPicker?.setBusy(true);error.textContent='';form.querySelectorAll('input,textarea,select,button').forEach(node=>node.disabled=true);
        try{
          const selection=modelPicker?{environment:env.key,...modelPicker.selection()}:{};
          let projectKey=project.value;
          if(projectKey==='__directory__'){
            const registered=await this.request(this.path('projects/register',null,provider),{environment:env.key,path:directory.path,createDirectory:false});
            if(!current())return;const row=registered.project;projects.push(row);project.add(new Option(row.name,row.key));project.value=row.key;projectKey=row.key;
          }
          const body={projectKey,...selection};if(!cli&&env?.createTitle!==false&&title.value.trim())body.title=title.value.trim();if(message.required)body.firstMessage=message.value.trim();
          const result=await this.mutate('create',body,null);
          if(current()&&result?.code==='workspace-trust-required'&&result.canConfirmTrust&&canBrowse&&result.workspace===path()){
            trustTarget={environment:env.key,path:result.workspace};trust.hidden=false;
          }
          if(result?.result?.id&&current()){dialog.close();await this.open(result.result.id);}else if(current())error.textContent=BridgeI18n.t(result?.status==='blocked'?result.message:'结果待核对，请检查原会话列表');
        }catch(failure){if(current())error.textContent=BridgeI18n.t(failure.message);}
        finally{saving=false;if(current()){modelPicker?.setBusy(false);form.querySelectorAll('input,textarea,select,button').forEach(node=>node.disabled=false);update();}}
      };
    }catch(error){if(current()){loading.textContent=BridgeI18n.t(error.message);loading.className='error';}}
  }
  relabel(){this.renderConnectionStatus();this.listStamp='';this.requestStamp='';this.search.placeholder=BridgeI18n.t('搜索');this.input.placeholder=BridgeI18n.t('继续这条聊天…');[...this.mode.options].forEach((option,i)=>option.textContent=BridgeI18n.t(['发送新消息','完成后发送','补充当前任务'][i]));this.listMode.options[0].textContent=BridgeI18n.t('最近交互');this.listMode.options[1].textContent=BridgeI18n.t('按项目');this.renderList();this.attachments.render();this.updateComposer();this.renderMessages(this.lastMessages||[]);this.collapseComposer(this.composerBody.hidden);this.workbench.relabel();if(this.sid)this.renderNotice();if(this.sid)this.renderRequests(this.freshDetail?this.state?.requests||[]:[]);}
}
if(typeof module!=='undefined')module.exports={DesktopSessionsView};
