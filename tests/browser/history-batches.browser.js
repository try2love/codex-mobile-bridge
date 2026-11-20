// Production shared renderers and pagination; all native pages are synthetic.
async function runHistoryBatchBrowserChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],providers=[],frame=()=>new Promise(requestAnimationFrame),check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const waitFor=async(predicate,label)=>{for(let i=0;i<60&&!predicate();i++)await frame();check(predicate(),label);};
  const original={reasoning:document.documentElement.dataset.showReasoning,process:document.documentElement.dataset.showProcess};
  document.documentElement.dataset.showReasoning='true';document.documentElement.dataset.showProcess='true';
  try{for(const provider of ['codex','claude','deepseek','antigravity','zcode']){
    const root=document.createElement('section');root.className='app';root.style.cssText='position:fixed;inset:0;z-index:30;background:var(--bg)';document.body.append(root);
    const row=(id,order,role='activity',text='Synthetic tool '+id)=>({id,key:id,order,role,kind:role==='activity'?'commandExecution':role==='user'?'steeringUserMessage':'agentMessage',status:'completed',text,version:'v1',turnStatus:'completed'});
    const current=row('current',300,'assistant','Current reply stays fixed while older process pages are read.');
    const supplement={...row('supplement',0,'user','A temporary user task from earlier history stays a normal user message.'),status:'accepted'};
    const tools=Array.from({length:200},(_,i)=>row('tool-'+(i+1),i+1));
    const pending={id:'current-approval',tool:'Synthetic pending approval',input:{command:'synthetic-only'}};
    const session={id:'batches',title:'Synthetic history batch',status:'active',requests:[pending],capabilities:{}};
    const pages=[{rows:tools.slice(100),before:'batch.tool-101',hasMore:true},{rows:tools.slice(0,100),before:'batch.tool-1',hasMore:true},{rows:[supplement],before:'batch.supplement',hasMore:true}];
    const requests=[],deferred=[],notices=[];let view,timeline,container,viewport,getNode,done,mergeCount=0,observer;
    const latest={connected:true,session,messages:[current],files:[],history:{before:'batch.current',hasMore:true}};
    const nativePage=page=>provider==='codex'?{...page,epoch:'batch',sequence:400,meta:{requests:[{id:'expired-approval'}]},files:[]}:{connected:true,session:{...session,status:'idle',requests:[{id:'expired-approval'}]},messages:page.rows,files:[],history:{before:page.before,hasMore:page.hasMore}};
    const readHistory=(url,signal)=>{
      const parsed=new URL(url,location.href),cursor=parsed.searchParams.get(provider==='codex'?'before':'historyBefore');
      if(requests.length>=pages.length)throw Error(provider+' requested an unexpected fourth history page');
      requests.push({cursor,url});return new Promise((resolve,reject)=>{
        const entry={resolve:value=>{entry.done=true;resolve(value);},done:false};deferred.push(entry);
        signal?.addEventListener('abort',()=>reject(Error('fixture disposed')),{once:true});
      });
    };
    if(provider==='codex'){
      viewport=document.createElement('div');viewport.style.cssText='height:360px;overflow:auto';container=document.createElement('div');viewport.append(container);root.append(viewport);
      const older=document.createElement('button'),newer=document.createElement('button');root.append(older,newer);
      timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/synthetic/'+action+'?scope=batches',renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){},request:(url,body,signal)=>{
        if(body)throw Error('Unexpected write');if(new URL(url,location.href).searchParams.has('before'))return readHistory(url,signal);throw Error('Unexpected native request');
      }});
      timeline.apply({rows:[current],epoch:'batch',sequence:500,before:'batch.current',hasMore:true,meta:{requests:[pending]},files:[]},true);
      const apply=timeline.apply;timeline.apply=function(page,reset,history,...args){if(history)mergeCount++;return apply.call(this,page,reset,history,...args);};
      getNode=id=>timeline.nodes.get(id)?.node;
    }else{
      const select=document.createElement('select');for(const name of ['codex',provider])select.add(new Option(name,name));
      view=new DesktopSessionsView({root,select,notify:text=>notices.push(text),onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
        if(body)throw Error('Unexpected write');const parsed=new URL(url,location.href);if(parsed.searchParams.has('historyBefore'))return readHistory(url);
        if(url.includes('/detail'))return latest;if(url.includes('/workspace'))return {entries:[],total:0,project:'/synthetic'};return {sessions:[session]};
      }});
      await view.choose(provider);await view.open('batches');clearTimeout(view.timer);container=view.messageList;viewport=view.messages;viewport.style.flex='0 0 360px';viewport.style.height='360px';viewport.style.maxHeight='360px';
      let paintedHistory='';const render=view.renderMessages;view.renderMessages=function(messages){const signature=messages.filter(message=>message.role==='activity'||message.id==='supplement').map(message=>message.id).join('|');if(signature&&signature!==paintedHistory)mergeCount++;paintedHistory=signature;return render.call(this,messages);};
      getNode=id=>view.messageNodes.get(id)?.node;
    }
    try{
      const currentNode=getNode('current'),body=currentNode.querySelector('.message-body');currentNode.style.minHeight='1000px';
      if(timeline)timeline.following=false;await frame();await frame();viewport.scrollTop=240;await frame();await frame();
      if(timeline){timeline.following=false;timeline.anchor=timeline.capture();}else view.reading={following:false,top:viewport.scrollTop,anchor:captureChatScrollAnchor(viewport,container)};
      const top=currentNode.getBoundingClientRect().top,mutations=[];observer=new MutationObserver(records=>mutations.push(...records));observer.observe(container,{subtree:true,childList:true});
      done=timeline?timeline.loadOlder(100):view.loadOlderHistory();
      for(let page=0;page<3;page++){
        await waitFor(()=>deferred.length===page+1,provider+' native history page '+(page+1)+' is requested');
        check(getNode('current')===currentNode&&body.isConnected,provider+' page '+(page+1)+' keeps the current reply node attached');
        check(!container.querySelector('.chat-activity-tools')&&mergeCount===0,provider+' page '+(page+1)+' does not paint a partial process-only batch');
        check(Math.abs(currentNode.getBoundingClientRect().top-top)<3,provider+' page '+(page+1)+' keeps the current reply pixel anchor while reading');
        deferred[page].resolve(nativePage(pages[page]));
      }
      await done;await frame();await frame();
      check(requests.length===3&&requests.map(request=>request.cursor).join('|')==='batch.current|batch.tool-101|batch.tool-1',provider+' follows exactly the three native page cursors');
      check(mergeCount===1,provider+' three native pages commit one combined history body');
      check((timeline?timeline.rows.size:view.historyMessages.length)===202,provider+' retains all 200 raw tools plus current reply and user supplement');
      check(getNode('current')===currentNode&&body.isConnected&&!mutations.some(record=>[...record.removedNodes].some(node=>node===currentNode||node===body||node.contains?.(body))),provider+' history never detaches or replaces the current reply body');
      check(Math.abs(currentNode.getBoundingClientRect().top-top)<3,provider+' combined history preserves the current reading anchor');
      const user=getNode('supplement'),group=container.querySelector('.chat-activity-tools'),shown=()=>[...group.lastChild.children].filter(node=>node.dataset.key!==undefined);
      check(user.parentElement===container&&!user.closest('.chat-activity-group')&&user.getBoundingClientRect().height>0,provider+' the third-page temporary task stays an outer user message');
      check(group&&!group.open&&shown().length===20&&shown()[0]===getNode('tool-181')&&shown().at(-1)===getNode('tool-200'),provider+' tools initially attach only the most recent 20 cached rows');
      check(!getNode('tool-1').isConnected&&getNode('tool-181').isConnected,provider+' omitted tool nodes remain cached without entering the DOM');
      check(timeline?timeline.meta.requests[0].id===pending.id:view.state.requests[0].id===pending.id&&view.requests.children.length>0,provider+' stale historical approvals never replace the current pending request');
      for(let parent=group.parentElement;parent;parent=parent.parentElement)if(parent.matches('details.chat-activity-group'))parent.open=true;
      group.firstChild.click();await frame();await frame();
      const first=getNode('tool-181'),firstTop=first.getBoundingClientRect().top,button=group.querySelector('.chat-activity-older'),reads=requests.length;
      check(!button.hidden&&first.getBoundingClientRect().height>0,provider+' expanded tools expose a local earlier-process control');
      button.click();await frame();await frame();
      check(shown().length===40&&shown()[0]===getNode('tool-161')&&shown().at(-1)===getNode('tool-200'),provider+' local process paging appends exactly 20 earlier cached tools');
      check(requests.length===reads&&mergeCount===1,provider+' local process paging performs no native history request or full history merge');
      check(getNode('tool-181')===first&&first.isConnected&&Math.abs(first.getBoundingClientRect().top-firstTop)<3,provider+' local process paging preserves the previously visible tool node and pixel anchor');
      check(getNode('current')===currentNode&&body.isConnected&&Math.abs(currentNode.getBoundingClientRect().top-top)<3,provider+' opening and paging process details preserves outer reply reading');
      check(!notices.length,provider+' history batching completes without a retry or error notice');
      if(view){
        const draft='Unsent '+provider+' draft';view.input.value=draft;view.reading={following:false,top:viewport.scrollTop,anchor:captureChatScrollAnchor(viewport,container)};
        const newerRows=Array.from({length:200},(_,i)=>row('new-'+i,1000+i,'assistant','New synthetic reply '+i));
        const beforeDeferred=mutations.length,readingTop=currentNode.getBoundingClientRect().top;
        view.applyDetail({...latest,messages:newerRows,history:{before:'native:new-0',hasMore:true}});await frame();await frame();
        const newer=container.querySelector('.ds-history-newer');
        check(view.historyMessages.length===202&&getNode('current')===currentNode&&body.isConnected,provider+' 200 non-overlapping new replies do not replace an upward reader history window');
        check(!mutations.slice(beforeDeferred).some(record=>[...record.removedNodes].some(node=>node===currentNode||node===body||node.contains?.(body)))&&Math.abs(currentNode.getBoundingClientRect().top-readingTop)<3,provider+' deferred recent page preserves the old reply node and reading pixel anchor');
        check(newer&&newer.parentElement===container&&!newer.closest('.chat-activity-group')&&newer.getBoundingClientRect().height>0&&newer.getBoundingClientRect().bottom>viewport.getBoundingClientRect().top&&newer.getBoundingClientRect().top<viewport.getBoundingClientRect().bottom,provider+' non-overlapping recent history exposes an independent newer-content control inside the reading viewport');
        check(view.input.value===draft&&requests.length===3,provider+' deferred history preserves the unsent draft and makes no history request');
        newer.click();await frame();await frame();
        check(view.historyMessages.length===200&&view.historyMessages[0].id==='new-0'&&view.historyMessages.at(-1).id==='new-199'&&!getNode('current'),provider+' only explicit newer-content selection switches to the actual latest page');
        check(view.input.value===draft&&!container.querySelector('.ds-history-newer')&&view.reading.following&&requests.length===3,provider+' switching latest keeps the draft, removes the control and follows the new reply');
      }
      providers.push({provider,pages:requests.length,historyMerges:mergeCount,rawTools:200,initialAttached:20,afterLocalPaging:shown().length});
    }finally{observer?.disconnect();view?.clear();timeline?.dispose();for(const entry of deferred)if(!entry.done)entry.resolve(nativePage({rows:[],before:null,hasMore:false}));if(done)await done;root.remove();}
  }}finally{for(const [name,value] of [['showReasoning',original.reasoning],['showProcess',original.process]])if(value===undefined)delete document.documentElement.dataset[name];else document.documentElement.dataset[name]=value;}
  return {passed:checks.length,width:innerWidth,checks,providers};
}
