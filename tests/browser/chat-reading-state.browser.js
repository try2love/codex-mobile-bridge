// Real renderers and layout, synthetic messages only; no native client is used.
async function runChatReadingStateChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],frame=()=>new Promise(requestAnimationFrame);
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const longText=Array.from({length:140},(_,i)=>'Reading line '+i+' — keep this position.').join('\n');
  for(const provider of ['deepseek','claude','codex']){
    const root=document.createElement('div');root.className='app';
    root.style.cssText='position:fixed;inset:0;background:var(--bg);z-index:20';document.body.append(root);
    let view,timeline,sequence=0,current=[];
    const row=(id,order,role='assistant',text=longText)=>({id,order,role,text,version:'v1'});
    let rows=[row('before',0),row('thought',1,'activity',longText),row('tool',2,'activity'),row('after',3)];
    const detail=messages=>({connected:true,session:{id:'reading',title:'Reading fixture',status:'idle',capabilities:{}},messages,files:[]});
    let apply,getNode,viewport,container;
    if(provider==='codex'){
      viewport=document.createElement('div');viewport.style.cssText='height:100%;overflow:auto';container=document.createElement('div');viewport.append(container);root.append(viewport);
      const older=document.createElement('button'),newer=document.createElement('button');root.append(older,newer);
      timeline=new ChatTimeline({url:action=>'/synthetic/'+action+'?',elements:{viewport,container,older,newer},
        renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){},request:async url=>{
          const key=new URL(url,location.origin).searchParams.get('key').split('.').at(-1),message=current.find(row=>row.id===key);
          return {key,version:message.version,text:message.text,next:null,offset:0};
        }});
      apply=(messages,reset=false)=>{current=messages;timeline.apply({epoch:'reading',sequence:++sequence,meta:{requests:[]},files:[],hasMore:false,
        rows:messages.map(message=>({...message,key:message.id,role:message.role==='reasoning'?'activity':message.role,kind:message.role}))},reset);};
      getNode=id=>timeline.nodes.get(id).node;apply(rows,true);
    }else{
      const select=document.createElement('select');for(const id of ['codex','claude','deepseek'])select.add(new Option(id,id));
      view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
        if(body)throw Error('Unexpected write');
        if(url.includes('/detail'))return detail(rows);
        if(url.includes('/workspace'))return {entries:[],total:0,project:'/synthetic'};
        return {sessions:[detail(rows).session]};
      }});
      await view.choose(provider);await view.open('reading');clearTimeout(view.timer);
      apply=messages=>view.applyDetail(detail(messages));getNode=id=>view.messageNodes.get(id).node;
      viewport=container=view.messages;
    }
    try{
      const positions={thought:210,tool:330};
      for(const id of Object.keys(positions)){
        const node=getNode(id);
        node.open=true;
      }
      await frame();await frame();
      for(const [id,top] of Object.entries(positions)){
        const body=getNode(id).querySelector('.activity-body');body.scrollTop=top;
        check(body.scrollTop===top,provider+' '+id+' is independently scrollable');
      }
      viewport.scrollTop=140;await frame();
      const outerTop=viewport.scrollTop,original=getNode('thought');
      const mutations=[];const observer=new MutationObserver(records=>mutations.push(...records));observer.observe(container,{childList:true});
      for(let i=0;i<3;i++)apply(rows);
      await frame();observer.disconnect();
      check(!mutations.length,provider+' unchanged refresh does not detach any message');
      check(getNode('thought')===original,provider+' unchanged message identity is stable');
      check(Math.abs(viewport.scrollTop-outerTop)<2,provider+' unchanged refresh retains outer reading position');
      const retained=label=>{for(const [id,top] of Object.entries(positions)){
        const node=getNode(id);check(node.open&&node.querySelector('.activity-body').scrollTop===top,provider+' '+label+' retains '+id+' expansion and inner position');
      }};
      retained('unchanged refresh');
      rows=[...rows,row('newer',4)];apply(rows);await frame();retained('appending a reply');
      rows=[row('older',-1),...rows];apply(rows);await frame();retained('prepending history');
      rows=rows.map(message=>message.id==='tool'?{...message,order:.5}:message).sort((a,b)=>a.order-b.order);
      apply(rows);await frame();await frame();retained('reordering rows');
      if(view){rows=rows.filter(message=>message.id!=='before');apply(rows);await frame();retained('removing a different row');}
      rows=rows.map(message=>message.id==='thought'?{...message,text:message.text+'\nNew thought line',version:'v2'}:message);
      apply(rows);await frame();await frame();retained('growing an expanded summary');
      check(getNode('thought').textContent.includes('New thought line'),provider+' updated summary is rendered');
      if(view){
        view.applyDetail({...detail(rows),files:[{id:'new-file',path:'example.md',reference:'example.md'}]});
      }else{
        rows=rows.map(message=>message.id==='thought'?{...message,status:'completed'}:message);apply(rows);
      }
      await frame();await frame();retained('metadata refresh');
      rows=rows.map(message=>message.id==='thought'?{...message,text:'Short summary',version:'v3'}:message);
      apply(rows);await frame();await frame();
      check(getNode('thought').open&&getNode('thought').querySelector('.activity-body').scrollTop===0,provider+' shrinking summary clamps to its actual range');
      if(view){view.prepareChat('different-chat');apply(rows);}else apply(rows,true);
      await frame();check(!getNode('tool').open&&getNode('tool').querySelector('.activity-body').scrollTop===0,provider+' new conversation does not inherit expansion or scroll');
      const start=1700000000000;
      rows=[row('turn-user',0,'user','First task'),row('turn-tool',1,'activity','Tool result'),row('turn-answer',2,'assistant','Final answer')]
        .map(message=>({...message,turnId:'native-first',turnStatus:'inProgress',turnStartedAt:start}));
      apply(rows,true);await frame();
      check(root.querySelector('.chat-turn-completion')?.disabled===true&&!getNode('turn-tool').classList.contains('chat-process-collapsed'),provider+' running task keeps its process visible');
      if(timeline)timeline.jumpBottom();
      else{viewport.scrollTop=viewport.scrollHeight;view.reading={top:viewport.scrollTop,following:true};}
      rows=rows.map(message=>({...message,turnStatus:'completed',turnCompletedAt:start+125000,...(message.id==='turn-answer'?{phase:'final_answer'}:{})}));
      apply(rows);await frame();
      const footer=root.querySelector('.chat-turn-completion');
      check(footer?.textContent===BridgeI18n.t('已完成，用时 {minutes} 分钟 {seconds} 秒').replace('{minutes}',2).replace('{seconds}',5)+' ▾',provider+' completion uses the native 125 second interval');
      check(footer.nextElementSibling===getNode('turn-tool'),provider+' completion is a flat control before the process');
      check(getNode('turn-tool').classList.contains('chat-process-collapsed')&&!getNode('turn-answer').classList.contains('chat-process-collapsed'),provider+' only process collapses after native success');
      footer.click();
      check(!getNode('turn-tool').classList.contains('chat-process-collapsed')&&footer.getAttribute('aria-expanded')==='true',provider+' manual expansion reveals the same process node');
      const language=BridgeI18n.language();BridgeI18n.setLanguage('en');timeline?.relabel();view?.relabel();
      check(root.querySelector('.chat-turn-completion').textContent==='Completed in 2 min 5 sec ▴',provider+' completion translates without changing native clocks');
      BridgeI18n.setLanguage(language);timeline?.relabel();view?.relabel();
      apply(rows);await frame();
      check(root.querySelectorAll('.chat-turn-completion').length===1&&root.querySelector('.chat-turn-completion')===footer,provider+' repeated refresh reuses exactly one footer');
      check(!getNode('turn-tool').classList.contains('chat-process-collapsed'),provider+' refresh preserves manual expansion');
      rows=[...rows,{...row('partial-history',3,'assistant','Older task without start'),turnId:'native-second',turnStatus:'completed',turnCompletedAt:start+500000}];
      apply(rows);await frame();
      check(root.querySelectorAll('.chat-turn-completion').length===1&&root.querySelector('.chat-turn-completion')===footer,provider+' a completed turn without steps does not add a footer');
      if(timeline){timeline.setVisibility({showProcess:false});timeline.setVisibility({showProcess:true});check(root.querySelectorAll('.chat-turn-completion').length===1,provider+' process visibility preserves completion metadata');}
    }finally{view?.clear();timeline?.dispose();root.remove();}
  }
  return {width:innerWidth,passed:checks.length,checks};
}
