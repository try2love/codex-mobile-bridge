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
    const row=(id,order,role='assistant',kind='',text=longText)=>({id,key:id,order,role,kind,text,version:'v1'});
    const commentary={...row('commentary',1.5,'assistant','','Synthetic progress commentary'),phase:'commentary'};
    const interaction={...row('answered',3.5,'user','steeringUserMessage','Synthetic processed question response'),interaction:'response',status:'accepted'};
    const supplement={...row('supplement',3.75,'user','steeringUserMessage','A temporary user task must stay visible.'),status:'accepted'};
    const initial=[row('user',0,'user','userMessage','Synthetic user prompt'),row('reason',1,'reasoning','reasoning'),commentary,row('cmd1',2,'activity','commandExecution'),row('cmd2',3,'activity','commandExecution'),interaction,supplement];
    const final={...row('final',4,'assistant','','Synthetic final reply'),phase:'final_answer'};
    const fileSummary={...row('files',5,'fileChanges','fileChanges',''),fileSummary:{count:1,added:2,removed:1,basis:'recorded',files:[{path:'synthetic.txt',added:2,removed:1}]}};
    const pending={id:'still-pending',tool:'Synthetic pending approval',input:{command:'synthetic-only'}};
    const detail=(messages,active)=>({connected:true,session:{id:view?.sid||'groups',title:'Synthetic activity groups',status:active?'active':'idle',requests:[pending],capabilities:{}},messages,files:[],history:{before:'native:groups',hasMore:true}});
    if(provider==='codex'){
      viewport=document.createElement('div');viewport.style.cssText='height:600px;max-height:75vh;overflow:auto';container=document.createElement('div');viewport.append(container);root.append(viewport);
      const older=document.createElement('button'),newer=document.createElement('button');root.append(older,newer);
      timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/synthetic/'+action+'?',request:async()=>{throw Error('Unexpected native request');},renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){}});
      apply=(messages,active=false,history=false,reset=false)=>{const first=!initialized;initialized=true;timeline.apply({rows:messages.map(message=>({...message,role:message.role==='reasoning'?'activity':message.role,turnStatus:active?'inProgress':'completed'})),sequence:++sequence,epoch,before:epoch+'.'+messages[0].key,hasMore:true,meta:{requests:[pending]},files:[]},first||reset,history);};
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
      const outside=id=>getNode(id).parentElement===container&&!getNode(id).closest('.chat-activity-group');
      check(!container.querySelector('.chat-activity-turn'),provider+' has no outer turn activity wrapper');
      check(outside('reason')&&getNode('reason').tagName==='DETAILS'&&!getNode('reason').open,provider+' reasoning is an independent collapsed details row');
      check(outside('commentary')&&getNode('commentary').getBoundingClientRect().height>0,provider+' commentary remains an outer message');
      check(tools&&!tools.open&&tools.parentElement===container,provider+' consecutive tools default to a collapsed tools group');
      check(getNode('cmd2').closest('.chat-activity-tools')===tools&&getNode('answered').closest('.chat-activity-tools')===tools,provider+' explicit processed interaction joins the adjacent tool batch');
      check(getNode('cmd1').tagName==='DETAILS'&&getNode('cmd2').tagName==='DETAILS',provider+' original per-tool details remain inside the batch');
      check(outside('user')&&outside('supplement')&&getNode('supplement').getBoundingClientRect().height>0,provider+' initial and temporary steering user tasks remain outside the batch');
      check(container.querySelectorAll('.chat-activity-tools').length===1,provider+' reasoning and commentary never create tool groups');
      tools.firstChild.click();getNode('reason').open=true;getNode('cmd1').open=true;getNode('cmd2').open=true;await frame();await frame();
      let leaf=getNode('cmd1'),body=leaf.querySelector('.activity-body'),reasonBody=getNode('reason').querySelector('.activity-body'),groupBody=tools.lastChild;body.scrollTop=120;reasonBody.scrollTop=90;groupBody.scrollTop=35;
      check(body.scrollTop===120&&reasonBody.scrollTop===90&&groupBody.scrollTop===35,provider+' reasoning, tool detail and batch body scroll independently');
      rows=[...rows,final,fileSummary];apply(rows);await frame();await frame();
      check(getNode('cmd1').closest('.chat-activity-tools')===tools&&tools.open,provider+' completion preserves a manually opened batch');
      check(outside('final')&&getNode('final').getBoundingClientRect().height>0,provider+' final reply stays visible outside the batch');
      check(outside('files')&&getNode('files').getBoundingClientRect().height>0,provider+' fileSummary/fileChanges result remains an outer row');
      check(getNode('files').classList.contains('edited-files-card')&&getNode('files').querySelector('.edited-files-label')?.textContent.includes('1'),provider+' file result uses the shared file summary card');
      check(outside('commentary')&&outside('supplement'),provider+' completion never reclassifies commentary or user steering');
      check(timeline?timeline.pendingRequests&&timeline.meta.requests[0].id===pending.id:view.state.requests[0].id===pending.id&&view.requests.children.length>0&&!view.requests.closest('.chat-activity-group'),provider+' pending approval stays separate from processed interactions');
      leaf=getNode('cmd1');body=leaf.querySelector('.activity-body');reasonBody=getNode('reason').querySelector('.activity-body');groupBody=tools.lastChild;
      check(leaf.open&&getNode('reason').open&&body.scrollTop===120&&reasonBody.scrollTop===90&&groupBody.scrollTop===35,provider+' completion preserves detail expansion and reading positions');
      const tracked=[tools,leaf,body,getNode('reason'),reasonBody],mutations=[],observer=new MutationObserver(records=>mutations.push(...records));observer.observe(container,{subtree:true,childList:true});
      for(let i=0;i<3;i++)apply(rows);await frame();observer.disconnect();
      check(tracked.every(node=>node.isConnected)&&!mutations.some(record=>[...record.removedNodes].some(removed=>tracked.some(node=>removed===node||removed.contains?.(node)))),provider+' unchanged refresh never detaches reading nodes');
      check(getNode('cmd1')===leaf&&tools.open&&body.scrollTop===120&&reasonBody.scrollTop===90&&groupBody.scrollTop===35,provider+' unchanged refresh keeps expansion, identity and inner scrolling');
      viewport.scrollTop=100;await frame();await frame();if(timeline)timeline.following=false;else view.reading={following:false,top:viewport.scrollTop,anchor:captureChatScrollAnchor(viewport,container)};
      const groupTop=tools.getBoundingClientRect().top;rows=[row('older',-1,'user','userMessage','Earlier synthetic prompt'),...rows];apply(rows,false,true);await frame();await frame();
      check(getNode('cmd1')===leaf&&getNode('cmd1').closest('.chat-activity-tools')===tools&&body.scrollTop===120&&reasonBody.scrollTop===90,provider+' history prepend preserves group and leaf reading nodes');
      check(Math.abs(tools.getBoundingClientRect().top-groupTop)<3,provider+' history prepend preserves the batch pixel anchor');
      tools.firstChild.click();await frame();apply(rows,true);await frame();check(!tools.open,provider+' manual collapse survives another active update');
      tools.firstChild.click();await frame();apply(rows);await frame();check(tools.open,provider+' manual expansion survives another completed update');
      const colors=[];
      for(const theme of ['light','dark'])for(const size of [10,16,32]){
        document.documentElement.dataset.theme=theme;document.documentElement.style.setProperty('--message-size',size+'px');await frame();
        const summary=tools.firstChild,style=getComputedStyle(summary),rect=tools.getBoundingClientRect(),parent=container.getBoundingClientRect();
        check(parseFloat(style.fontSize)===size&&rect.width<=parent.width+1&&summary.scrollWidth<=summary.clientWidth+1,provider+' '+theme+' '+size+'px tools group scales within its container');colors.push({theme,size,color:style.color});
        if(provider==='claude'&&globalThis.__captureActivityGroup&&(innerWidth<500&&theme==='dark'&&size===32||innerWidth>=500&&theme==='light'&&size===16)){
          const details=[getNode('reason'),getNode('cmd1'),getNode('cmd2')],states=details.map(node=>node.open),scroll=groupBody.scrollTop;details.forEach(node=>node.open=false);groupBody.scrollTop=0;await frame();
          viewport.scrollTop+=tools.getBoundingClientRect().top-viewport.getBoundingClientRect().top-12;await frame();await globalThis.__captureActivityGroup({theme,size,width:innerWidth});
          details.forEach((node,index)=>node.open=states[index]);groupBody.scrollTop=scroll;await frame();
        }
      }
      check(colors[0].color!==colors[3].color,provider+' tools summary follows the actual light/dark palette');document.documentElement.style.setProperty('--message-size','16px');
      document.documentElement.dataset.showReasoning='false';if(timeline)timeline.setVisibility({showReasoning:false});await frame();await frame();
      check(getNode('reason').hidden&&outside('reason')&&outside('supplement')&&outside('final')&&outside('files'),provider+' hiding reasoning never groups ordinary user/final/file rows');
      document.documentElement.dataset.showReasoning='true';if(timeline)timeline.setVisibility({showReasoning:true});await frame();await frame();check(!getNode('reason').hidden,provider+' reasoning can be shown again independently');
      rows=[...rows,row('tool-before-user',5.5,'activity','commandExecution'),row('plain-reply',6,'user','userMessage','Yes — an ordinary user task without interaction metadata.'),row('after-user-a',7,'activity','commandExecution'),row('after-user-b',8,'activity','commandExecution')];apply(rows);await frame();
      const after=getNode('after-user-a').closest('.chat-activity-tools');
      check(outside('plain-reply')&&after&&getNode('after-user-b').closest('.chat-activity-tools')===after,provider+' ordinary answer-shaped user text separates tool batches');
      check(getNode('tool-before-user').closest('.chat-activity-tools')!==after&&after!==tools,provider+' tools never merge across an ordinary user or file result');
      const range=document.createRange();range.selectNodeContents(getNode('supplement').querySelector('.message-body'));document.getSelection().removeAllRanges();document.getSelection().addRange(range);apply(rows);await frame();
      check(!document.getSelection().isCollapsed&&outside('supplement'),provider+' unchanged updates preserve selected ordinary user text');document.getSelection().removeAllRanges();
      if(view)view.prepareChat('different-chat');else epoch='other-groups';apply(initial,false,false,true);await frame();await frame();
      const freshTools=getNode('cmd1').closest('.chat-activity-tools');
      check(freshTools!==tools&&!freshTools.open&&!getNode('reason').open&&!getNode('cmd1').open&&getNode('cmd1').querySelector('.activity-body').scrollTop===0,provider+' another conversation inherits no group expansion or scroll');
      if(timeline){timeline.following=true;for(let i=10;i<280;i++)timeline.apply({rows:[row('live-'+i,i)],sequence:++sequence,epoch,meta:{requests:[pending]},files:[]});const groups=container.__chatActivityGroups?.size||0;check(timeline.rows.size<=240&&!freshTools.isConnected&&!groups,provider+' sliding window releases omitted group nodes and cache');}
    }finally{document.getSelection().removeAllRanges();view?.clear();timeline?.dispose();root.remove();restore();}
  }}finally{restore();}
  return {passed:checks.length,width:innerWidth,checks};
}
