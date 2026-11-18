// Real shared renderers in the loopback fixture; every request is synthetic.
async function runActivityGroupBrowserChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],frame=()=>new Promise(requestAnimationFrame),check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const longText=Array.from({length:140},(_,i)=>'Synthetic activity line '+i+' — preserve independent reading.').join('\n');
  const original={theme:document.documentElement.dataset.theme,reasoning:document.documentElement.dataset.showReasoning,process:document.documentElement.dataset.showProcess,font:document.documentElement.style.getPropertyValue('--message-size')};
  const restore=()=>{for(const [name,value] of [['theme',original.theme],['showReasoning',original.reasoning],['showProcess',original.process]])if(value===undefined)delete document.documentElement.dataset[name];else document.documentElement.dataset[name]=value;if(original.font)document.documentElement.style.setProperty('--message-size',original.font);else document.documentElement.style.removeProperty('--message-size');};
  try{for(const provider of ['codex','claude','deepseek','antigravity','zcode']){
    document.documentElement.dataset.showReasoning='true';document.documentElement.dataset.showProcess='true';document.documentElement.style.setProperty('--message-size','16px');
    const root=document.createElement('div');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:20';document.body.append(root);
    let view,timeline,sequence=0,initialized=false,epoch='groups',rows,apply,getNode,container,viewport;
    const startedAt='2026-11-16T01:00:00Z';
    const row=(id,order,role='assistant',kind='',text=longText)=>({id,key:id,order,role,kind,text,version:'v1',turnId:'native-task',turnStartedAt:startedAt});
    const commentary={...row('commentary',1.5,'assistant','','Synthetic progress commentary'),phase:'commentary'};
    const interaction={...row('answered',3.5,'user','steeringUserMessage','Synthetic processed question response'),interaction:'response',status:'accepted'};
    const supplement={...row('supplement',3.75,'user','steeringUserMessage','A temporary user task must stay visible.'),status:'accepted'};
    const editedFile={...row('file1',3.85,'activity','fileChange','/project/2026-11-17-长文件名.md\n@@ -1 +1,3 @@\n-old\n+new\n+second\n+third'),status:'completed',activityFiles:[{path:'/project/2026-11-17-长文件名.md',added:3,removed:1}]};
    const initial=[{...row('user',0,'user','userMessage','Synthetic user prompt'),timestamp:startedAt},row('reason',1,'reasoning','reasoning'),commentary,row('cmd1',2,'activity','commandExecution'),row('reason-mid',2.5,'reasoning','reasoning'),row('cmd2',3,'activity','commandExecution'),interaction,supplement,editedFile,{...row('cmd3',3.9,'activity','commandExecution'),status:'inProgress'}];
    const final={...row('final',4,'assistant','','Synthetic final reply'),phase:'final_answer',timestamp:'2026-11-16T01:02:05Z'};
    const fileSummary={...row('files',5,'fileChanges','fileChanges',''),fileSummary:{count:1,added:2,removed:1,basis:'recorded',files:[{path:'synthetic.txt',added:2,removed:1}]}};
    const pending={id:'still-pending',tool:'Synthetic pending approval',input:{command:'synthetic-only'}};
    const detail=(messages,active)=>({connected:true,session:{id:view?.sid||'groups',title:'Synthetic activity groups',status:active?'active':'idle',requests:[pending],capabilities:{}},messages:messages.map(message=>({...message,turnStatus:message.turnStatus||(active?'inProgress':'completed')})),files:[],history:{before:'native:groups',hasMore:true}});
    if(provider==='codex'){
      viewport=document.createElement('div');viewport.style.cssText='height:600px;max-height:75vh;overflow:auto';container=document.createElement('div');viewport.append(container);root.append(viewport);
      const older=document.createElement('button'),newer=document.createElement('button');root.append(older,newer);
      timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/synthetic/'+action+'?',request:async()=>{throw Error('Unexpected native request');},renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){}});
      apply=(messages,active=false,history=false,reset=false)=>{const first=!initialized;initialized=true;timeline.apply({rows:messages.map(message=>({...message,role:message.role==='reasoning'?'activity':message.role,turnStatus:message.turnStatus||(active?'inProgress':'completed')})),sequence:++sequence,epoch,before:epoch+'.'+messages[0].key,hasMore:true,meta:{requests:[pending]},files:[]},first||reset,history);};
      getNode=id=>timeline.nodes.get(id).node;
    }else{
      const select=document.createElement('select');for(const name of ['codex',provider])select.add(new Option(name,name));
      view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{if(body)throw Error('Unexpected write');if(url.includes('/detail'))return detail(initial,true);if(url.includes('/workspace'))return {entries:[],total:0,project:'/synthetic'};return {sessions:[detail(initial,true).session]};}});
      await view.choose(provider);await view.open('groups');clearTimeout(view.timer);container=view.messageList;viewport=view.messages;
      apply=(messages,active=false)=>view.applyDetail(detail(messages,active));getNode=id=>view.messageNodes.get(id).node;
    }
    try{
      rows=initial;apply(rows,true);await frame();await frame();
      let tools=getNode('cmd1').closest('.chat-activity-tools');
      const task=tools.closest('.chat-activity-turn'),afterSupplement=getNode('cmd3').closest('.chat-activity-turn');
      const outside=id=>getNode(id).parentElement===container&&!getNode(id).closest('.chat-activity-group');
      check(task&&task.open&&task.parentElement===container&&task.dataset.state==='active'&&getComputedStyle(task.firstChild).display==='none',provider+' active task shows process rows without an outer disclosure heading');
      const reasoningOnly=getNode('reason').closest('.chat-activity-tools');
      check(reasoningOnly&&reasoningOnly.parentElement===task.lastChild&&!reasoningOnly.open&&getNode('reason').tagName==='DETAILS'&&!getNode('reason').open,provider+' reasoning keeps independent details inside a shared process batch');
      check(reasoningOnly.firstChild.textContent===BridgeI18n.t('思考摘要'),provider+' a reasoning-only batch uses the reasoning summary label');
      check(getNode('commentary').parentElement===task.lastChild&&getNode('commentary').getBoundingClientRect().height>0,provider+' explicit commentary appears inside the active task');
      check(reasoningOnly!==tools,provider+' commentary separates adjacent reasoning and tool batches');
      check(tools&&!tools.open&&tools.parentElement===task.lastChild,provider+' consecutive tools default to a collapsed tools group');
      check(getNode('cmd2').closest('.chat-activity-tools')===tools&&getNode('answered').closest('.chat-activity-tools')===tools,provider+' explicit processed interaction joins the adjacent tool batch');
      check(getNode('reason-mid').closest('.chat-activity-tools')===tools&&getNode('reason-mid').tagName==='DETAILS',provider+' reasoning between tools joins their batch without splitting it or losing its detail');
      check(getNode('cmd1').dataset.icon==='command'&&tools.dataset.icon==='command'&&reasoningOnly.dataset.icon==='reasoning',provider+' shared batches and native details use their semantic activity icons');
      check(getNode('cmd1').tagName==='DETAILS'&&getNode('cmd2').tagName==='DETAILS',provider+' original per-tool details remain inside the batch');
      check(outside('user')&&outside('supplement')&&getNode('supplement').getBoundingClientRect().height>0,provider+' initial and temporary steering user tasks remain outside the batch');
      check(afterSupplement&&afterSupplement!==task&&afterSupplement.open&&outside('supplement'),provider+' same native turn continues in a separate task fragment around the visible supplement');
      check(container.querySelectorAll('.chat-activity-tools').length===3,provider+' task fragments contain consecutive reasoning and tool batches separated by commentary or user input');
      const runningTools=getNode('cmd3').closest('.chat-activity-tools'),reducedMotion=matchMedia('(prefers-reduced-motion: reduce)').matches;
      check(!runningTools.open&&runningTools.dataset.running==='true'&&tools.dataset.running==='false',provider+' only the currently running last tool batch is marked while remaining folded');
      check(getComputedStyle(runningTools.firstChild).animationName===(reducedMotion?'none':'chat-activity-breathe'),provider+' running summary gently breathes according to motion preference');
      tools.firstChild.click();reasoningOnly.firstChild.click();getNode('reason').open=true;getNode('cmd1').open=true;getNode('cmd2').open=true;await frame();await frame();
      let leaf=getNode('cmd1'),body=leaf.querySelector('.activity-body'),reasonBody=getNode('reason').querySelector('.activity-body'),groupBody=tools.lastChild;body.scrollTop=120;reasonBody.scrollTop=90;groupBody.scrollTop=35;
      check(body.scrollTop===120&&reasonBody.scrollTop===90&&groupBody.scrollTop===35,provider+' reasoning, tool detail and batch body scroll independently');
      rows=[...rows,final,fileSummary];apply(rows);await frame();await frame();
      const elapsedLabel=BridgeI18n.t('用时 {minutes} 分钟 {seconds} 秒').replace('{minutes}','2').replace('{seconds}','5');
      check(!task.open&&!afterSupplement.open&&task.dataset.state==='complete'&&getComputedStyle(task.firstChild).display!=='none'&&task.firstChild.textContent===elapsedLabel&&afterSupplement.firstChild.hidden,provider+' completion collapses both task fragments once and shows one native 125-second duration');
      check(runningTools.dataset.running==='false'&&getComputedStyle(runningTools.firstChild).animationName==='none',provider+' terminal task stops the running summary animation');
      task.firstChild.click();await frame();await frame();
      check(getNode('cmd1').closest('.chat-activity-tools')===tools&&tools.open,provider+' opening the completed task preserves its manually opened tool batch');
      runningTools.firstChild.click();await frame();await frame();
      const fileHeading=getNode('file1').querySelector('summary'),filePath=fileHeading.querySelector('.chat-activity-file'),fileStats=fileHeading.querySelector('.chat-activity-stats');
      check(filePath?.textContent.includes('2026-11-17-长文件名.md')&&filePath.getBoundingClientRect().height>0,provider+' expanded tools show the native file path in the compact edited-file row');
      check(fileStats?.textContent.includes('+3')&&/[−-]1/.test(fileStats.textContent)&&fileStats.getBoundingClientRect().height>0,provider+' compact edited-file row shows native added and removed line counts');
      check(outside('final')&&getNode('final').getBoundingClientRect().height>0,provider+' final reply stays visible outside the batch');
      check(outside('files')&&getNode('files').getBoundingClientRect().height>0,provider+' fileSummary/fileChanges result remains an outer row');
      check(getNode('files').classList.contains('edited-files-card')&&getNode('files').querySelector('.edited-files-label')?.textContent.includes('1'),provider+' file result uses the shared file summary card');
      check(getNode('commentary').closest('.chat-activity-turn')===task&&outside('supplement'),provider+' completion keeps commentary folded and user steering outside');
      check(timeline?timeline.pendingRequests&&timeline.meta.requests[0].id===pending.id:view.state.requests[0].id===pending.id&&view.requests.children.length>0&&!view.requests.closest('.chat-activity-group'),provider+' pending approval stays separate from processed interactions');
      leaf=getNode('cmd1');body=leaf.querySelector('.activity-body');reasonBody=getNode('reason').querySelector('.activity-body');groupBody=tools.lastChild;
      check(leaf.open&&getNode('reason').open&&body.scrollTop===120&&reasonBody.scrollTop===90&&groupBody.scrollTop===35,provider+' completion preserves detail expansion and reading positions');
      const tracked=[task,afterSupplement,tools,leaf,body,getNode('reason'),reasonBody],mutations=[],observer=new MutationObserver(records=>mutations.push(...records));observer.observe(container,{subtree:true,childList:true});
      for(let i=0;i<3;i++)apply(rows);await frame();observer.disconnect();
      check(tracked.every(node=>node.isConnected)&&!mutations.some(record=>[...record.removedNodes].some(removed=>tracked.some(node=>removed===node||removed.contains?.(node)))),provider+' unchanged refresh never detaches reading nodes');
      check(task.open&&afterSupplement.open&&getNode('cmd1')===leaf&&tools.open&&body.scrollTop===120&&reasonBody.scrollTop===90&&groupBody.scrollTop===35,provider+' unchanged refresh keeps expansion, identity and inner scrolling');
      viewport.scrollTop=100;await frame();await frame();if(timeline)timeline.following=false;else view.reading={following:false,top:viewport.scrollTop,anchor:captureChatScrollAnchor(viewport,container)};
      const groupTop=tools.getBoundingClientRect().top;rows=[row('older',-1,'user','userMessage','Earlier synthetic prompt'),...rows];apply(rows,false,true);await frame();await frame();
      check(task.open&&afterSupplement.open&&getNode('cmd1')===leaf&&getNode('cmd1').closest('.chat-activity-tools')===tools&&body.scrollTop===120&&reasonBody.scrollTop===90,provider+' history prepend preserves group and leaf reading nodes');
      check(Math.abs(tools.getBoundingClientRect().top-groupTop)<3,provider+' history prepend preserves the batch pixel anchor');
      tools.firstChild.click();await frame();apply(rows);await frame();check(!tools.open&&task.open,provider+' manual tool collapse survives a completed refresh');
      tools.firstChild.click();task.firstChild.click();await frame();apply(rows);await frame();check(tools.open&&!task.open,provider+' manual task collapse survives a completed refresh');
      task.firstChild.click();await frame();apply(rows);await frame();check(task.open&&tools.open,provider+' manually reopened completed task remains open on later refresh');
      const colors=[];
      for(const theme of ['light','dark'])for(const size of [10,16,32]){
        document.documentElement.dataset.theme=theme;document.documentElement.style.setProperty('--message-size',size+'px');await frame();
        const summary=tools.firstChild,style=getComputedStyle(summary),rect=tools.getBoundingClientRect(),parent=container.getBoundingClientRect();
        check(parseFloat(style.fontSize)===size&&rect.width<=parent.width+1&&summary.scrollWidth<=summary.clientWidth+1,provider+' '+theme+' '+size+'px tools group scales within its container');colors.push({theme,size,color:style.color});
        const groupIcon=getComputedStyle(summary,'::before'),leafIcon=getComputedStyle(getNode('cmd1').firstElementChild,'::before');
        check(parseFloat(groupIcon.width)===size&&parseFloat(leafIcon.width)===size&&groupIcon.maskImage===leafIcon.maskImage&&groupIcon.maskImage.includes('data:image/svg+xml'),provider+' '+theme+' '+size+'px activity icons reuse static masks at one em');
        if(provider==='claude'&&globalThis.__captureActivityGroup&&(innerWidth<500&&theme==='dark'&&size===32||innerWidth>=500&&theme==='light'&&size===16)){
          const details=[getNode('reason'),getNode('cmd1'),getNode('cmd2')],states=details.map(node=>node.open),scroll=groupBody.scrollTop;details.forEach(node=>node.open=false);groupBody.scrollTop=0;await frame();
          viewport.scrollTop+=tools.getBoundingClientRect().top-viewport.getBoundingClientRect().top-12;await frame();await globalThis.__captureActivityGroup({theme,size,width:innerWidth});
          details.forEach((node,index)=>node.open=states[index]);groupBody.scrollTop=scroll;await frame();
        }
      }
      check(colors[0].color!==colors[3].color,provider+' tools summary follows the actual light/dark palette');document.documentElement.style.setProperty('--message-size','16px');
      document.documentElement.dataset.showReasoning='false';if(timeline)timeline.setVisibility({showReasoning:false});await frame();await frame();
      check(getNode('reason').hidden&&getNode('reason').closest('.chat-activity-turn')===task&&outside('supplement')&&outside('final')&&outside('files'),provider+' hiding reasoning never groups ordinary user/final/file rows');
      document.documentElement.dataset.showReasoning='true';if(timeline)timeline.setVisibility({showReasoning:true});await frame();await frame();check(!getNode('reason').hidden,provider+' reasoning can be shown again independently');
      rows=[...rows,row('tool-before-user',5.5,'activity','commandExecution'),row('plain-reply',6,'user','userMessage','Yes — an ordinary user task without interaction metadata.'),row('after-user-a',7,'activity','commandExecution'),row('after-user-b',8,'activity','commandExecution')];apply(rows);await frame();
      const after=getNode('after-user-a').closest('.chat-activity-tools');
      check(outside('plain-reply')&&after&&getNode('after-user-b').closest('.chat-activity-tools')===after,provider+' ordinary answer-shaped user text separates tool batches');
      check(getNode('tool-before-user').closest('.chat-activity-tools')!==after&&after!==tools,provider+' tools never merge across an ordinary user or file result');
      const range=document.createRange();range.selectNodeContents(getNode('supplement').querySelector('.message-body'));document.getSelection().removeAllRanges();document.getSelection().addRange(range);apply(rows);await frame();
      check(!document.getSelection().isCollapsed&&outside('supplement'),provider+' unchanged updates preserve selected ordinary user text');document.getSelection().removeAllRanges();
      if(view)view.prepareChat('different-chat');else epoch='other-groups';apply(initial,false,false,true);await frame();await frame();
      const freshTools=getNode('cmd1').closest('.chat-activity-tools');
      check(!freshTools.closest('.chat-activity-turn').open,provider+' initially loaded completed history starts folded');
      check(freshTools!==tools&&!freshTools.open&&!getNode('reason').open&&!getNode('cmd1').open&&getNode('cmd1').querySelector('.activity-body').scrollTop===0,provider+' another conversation inherits no group expansion or scroll');
      const legacy=(id,order,role,kind,text)=>{const {turnId,turnStartedAt,...message}=row(id,order,role,kind,text);return message;};
      const fallback=[legacy('legacy-user',0,'user','userMessage','Legacy prompt'),legacy('legacy-tool',1,'activity','commandExecution'),legacy('legacy-final',2,'assistant','','Legacy final answer'),legacy('legacy-unknown-user',3,'user','','Yes, approved — ordinary text without interaction metadata.'),legacy('legacy-next-tool',4,'activity','commandExecution'),legacy('legacy-next-final',5,'assistant','','Next legacy final answer')];
      apply(fallback,false,false,true);await frame();await frame();
      const legacyTask=getNode('legacy-tool').closest('.chat-activity-turn'),nextLegacyTask=getNode('legacy-next-tool').closest('.chat-activity-turn');
      check(legacyTask&&nextLegacyTask&&legacyTask!==nextLegacyTask&&!legacyTask.open&&!nextLegacyTask.open,provider+' legacy adapters without turn IDs use ordinary user boundaries');
      check(outside('legacy-final')&&outside('legacy-next-final')&&outside('legacy-unknown-user'),provider+' legacy final fallback never infers interactions from unknown user text');
      check(!legacyTask.firstChild.textContent.match(/\d/),provider+' historical tasks without native clocks show no invented elapsed duration');
      if(view){
        view.prepareChat('cached-live-task');
        const cachedRows=[legacy('cached-user',0,'user','','Cached prompt'),{...legacy('cached-progress',1,'assistant','','Cached progress'),phase:'commentary'},legacy('cached-tool',2,'activity','commandExecution')];
        const cachedDetail=detail(cachedRows,false);delete cachedDetail.session.status;cachedDetail.messages=cachedRows;
        view.applyDetail(cachedDetail,true);await frame();
        const cachedTask=getNode('cached-tool').closest('.chat-activity-turn');
        check(!cachedTask.open,provider+' cached history without runtime evidence starts folded');
        view.applyDetail({...detail(cachedRows,true),messages:cachedRows});await frame();await frame();
        check(getNode('cached-tool').closest('.chat-activity-turn')===cachedTask&&cachedTask.open&&cachedTask.dataset.state==='active'&&getComputedStyle(cachedTask.firstChild).display==='none'&&getNode('cached-progress').getBoundingClientRect().height>0,provider+' fresh active status reopens the cached task and shows its progress');
      }
      for(const terminalStatus of ['interrupted','failed']){
        if(view)view.prepareChat('terminal-'+terminalStatus);
        const completedAt=terminalStatus==='interrupted'?'2026-11-16T01:02:05Z':Date.parse('2026-11-16T01:02:05Z');
        const nativeRows=[row('terminal-user',0,'user','userMessage','Synthetic terminal task'),{...row('terminal-commentary',1,'assistant','','Synthetic terminal progress'),phase:'commentary'},row('terminal-tool',2,'activity','commandExecution')].map(message=>({...message,turnId:'terminal-'+terminalStatus,turnStatus:'inProgress'}));
        apply(nativeRows,true,false,true);await frame();await frame();
        const terminalTask=getNode('terminal-tool').closest('.chat-activity-turn');
        check(terminalTask.open&&terminalTask.dataset.state==='active',provider+' '+terminalStatus+' task initially shows its running process');
        let terminalRows=nativeRows.map(message=>({...message,turnStatus:terminalStatus,turnCompletedAt:completedAt}));
        apply(terminalRows);await frame();await frame();
        check(!terminalTask.open&&terminalTask.dataset.state==='complete'&&terminalTask.firstChild.textContent===elapsedLabel,provider+' '+terminalStatus+' without a final reply collapses once and displays the native 125-second duration');
        check(!container.querySelector('[data-key="terminal-final"]')&&getNode('terminal-commentary').closest('.chat-activity-turn')===terminalTask,provider+' '+terminalStatus+' keeps commentary in the completed process without inventing a final reply');
        terminalTask.firstChild.click();await frame();
        const lateFinal={...row('terminal-final',3,'assistant','','Synthetic delayed final reply'),phase:'final_answer',turnId:'terminal-'+terminalStatus,turnStatus:terminalStatus,turnCompletedAt:completedAt,timestamp:'2026-11-16T01:09:00Z'};
        terminalRows=[...terminalRows,lateFinal];apply(terminalRows);await frame();await frame();
        check(getNode('terminal-tool').closest('.chat-activity-turn')===terminalTask&&terminalTask.open&&terminalTask.firstChild.textContent===elapsedLabel&&outside('terminal-final'),provider+' '+terminalStatus+' late final preserves a manually reopened process and its native completion duration');
        terminalTask.firstChild.click();await frame();
        const resumedRows=terminalRows.map(({turnCompletedAt,...message})=>({...message,turnStatus:'inProgress'}));
        apply(resumedRows,true);await frame();await frame();
        check(terminalTask.open&&terminalTask.dataset.state==='active'&&getComputedStyle(terminalTask.firstChild).display==='none',provider+' '+terminalStatus+' task reopens its process when native execution resumes');
        const finishedRows=resumedRows.map(message=>({...message,turnStatus:'completed',turnCompletedAt:completedAt}));
        apply(finishedRows);await frame();await frame();
        check(!terminalTask.open&&terminalTask.dataset.state==='complete'&&terminalTask.firstChild.textContent===elapsedLabel,provider+' '+terminalStatus+' resumed task automatically collapses on its next completion');
        terminalTask.firstChild.click();await frame();apply(finishedRows);await frame();
        check(terminalTask.open,provider+' '+terminalStatus+' repeated completed refresh preserves the reader reopening the process');
      }
      if(timeline){timeline.following=true;for(let i=10;i<280;i++)timeline.apply({rows:[{...row('live-'+i,i),turnId:'live-'+i,phase:'final_answer',turnStatus:'completed'}],sequence:++sequence,epoch,meta:{requests:[pending]},files:[]});const groups=container.__chatTaskGroups?.size||0;check(timeline.rows.size<=240&&!freshTools.isConnected&&!groups,provider+' sliding window releases omitted group nodes and cache');}
    }finally{document.getSelection().removeAllRanges();view?.clear();timeline?.dispose();root.remove();restore();}
  }}finally{restore();}
  return {passed:checks.length,width:innerWidth,checks};
}
