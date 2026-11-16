// Real shared renderers in the loopback fixture; every request is synthetic.
async function runActivityGroupBrowserChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],frame=()=>new Promise(requestAnimationFrame),check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const longText=Array.from({length:140},(_,i)=>'Synthetic activity line '+i+' — preserve independent reading.').join('\n');
  const originalTheme=document.documentElement.dataset.theme,originalFont=document.documentElement.style.getPropertyValue('--message-size');
  for(const provider of ['codex','claude','deepseek','antigravity','zcode']){
    const root=document.createElement('div');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:20';document.body.append(root);
    let view,timeline,sequence=0,rows,apply,getNode,container,viewport;
    const row=(id,order,role='assistant',kind='',text=longText)=>({id,key:id,order,role,kind,text,version:'v1'});
    const commentary={...row('commentary',1.5,'assistant','','Synthetic progress commentary'),phase:'commentary'};
    const initial=[row('user',0,'user','','Synthetic user prompt'),row('reason',1,'reasoning','reasoning'),commentary,row('cmd1',2,'activity','commandExecution'),row('cmd2',3,'activity','commandExecution')];
    const final=row('final',4,'assistant','','Synthetic final reply');final.phase='final_answer';
    const detail=(messages,active)=>({connected:true,session:{id:'groups',title:'Synthetic activity groups',status:active?'active':'idle',requests:[],capabilities:{}},messages,files:[],history:{before:'native:groups',hasMore:true}});
    if(provider==='codex'){
      viewport=document.createElement('div');viewport.style.cssText='height:600px;max-height:75vh;overflow:auto';container=document.createElement('div');viewport.append(container);root.append(viewport);
      const older=document.createElement('button'),newer=document.createElement('button');root.append(older,newer);
      timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/synthetic/'+action+'?',request:async()=>{throw Error('Unexpected native request');},renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){}});
      apply=(messages,active=false,history=false)=>timeline.apply({rows:messages.map(message=>({...message,role:message.role==='reasoning'?'activity':message.role,turnStatus:active?'inProgress':'completed'})),sequence:++sequence,epoch:'groups',before:'groups.'+messages[0].key,hasMore:true,meta:{requests:[]},files:[]},sequence===0,history);
      getNode=id=>timeline.nodes.get(id).node;
    }else{
      const select=document.createElement('select');for(const name of ['codex',provider])select.add(new Option(name,name));
      view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{if(body)throw Error('Unexpected write');if(url.includes('/detail'))return detail(initial,true);if(url.includes('/workspace'))return {entries:[],total:0,project:'/synthetic'};return {sessions:[detail(initial,true).session]};}});
      await view.choose(provider);await view.open('groups');clearTimeout(view.timer);container=view.messageList;viewport=view.messages;
      apply=(messages,active=false)=>view.applyDetail(detail(messages,active));getNode=id=>view.messageNodes.get(id).node;
    }
    try{
      rows=initial;apply(rows,true);await frame();
      let group=getNode('reason').closest('.chat-activity-turn'),tools=getNode('cmd1').closest('.chat-activity-tools');
      check(group?.open,provider+' running intermediate group opens by default');
      check(tools&&!tools.open&&tools.parentElement.closest('.chat-activity-turn')===group,provider+' tool batch is a collapsed second level');
      check(getNode('cmd1').tagName==='DETAILS'&&getNode('cmd1').parentElement.closest('.chat-activity-tools')===tools,provider+' original tool detail remains the third level');
      rows=[...rows,final];apply(rows);await frame();
      check(!group.open,provider+' untouched group collapses when its final reply arrives');
      check(!getNode('user').closest('.chat-activity-group')&&!getNode('final').closest('.chat-activity-group')&&getNode('final').getBoundingClientRect().height>0,provider+' user and final reply remain outside collapsed activity');
      group.firstChild.click();tools.firstChild.click();getNode('reason').open=true;getNode('cmd1').open=true;getNode('cmd2').open=true;await frame();await frame();
      const leaf=getNode('cmd1'),body=leaf.querySelector('.activity-body'),groupBody=group.lastChild;body.scrollTop=120;groupBody.scrollTop=35;
      check(body.scrollTop===120&&groupBody.scrollTop===35,provider+' leaf and group body both support independent scrolling');
      const tracked=[group,tools,leaf,body],mutations=[],observer=new MutationObserver(records=>mutations.push(...records));observer.observe(container,{subtree:true,childList:true});
      for(let i=0;i<3;i++)apply(rows);await frame();observer.disconnect();
      check(tracked.every(node=>node.isConnected)&&!mutations.some(record=>[...record.removedNodes].some(removed=>tracked.some(node=>removed===node||removed.contains?.(node)))),provider+' unchanged updates never detach stable groups or activity nodes');
      check(group.open&&tools.open&&leaf.open&&body.scrollTop===120&&groupBody.scrollTop===35,provider+' unchanged updates retain manual expansion and both scroll positions');
      viewport.scrollTop=120;await frame();await frame();
      const groupTop=group.getBoundingClientRect().top;
      rows=[row('older',-1,'user','','Earlier synthetic prompt'),...rows];apply(rows,false,true);await frame();
      check(getNode('cmd1')===leaf&&getNode('reason').closest('.chat-activity-turn')===group&&body.scrollTop===120,provider+' history prepend preserves the activity group and its leaf reading');
      check(Math.abs(group.getBoundingClientRect().top-groupTop)<3,provider+' history prepend preserves the visible group pixel anchor');
      group.firstChild.click();await frame();apply(rows,true);await frame();check(!group.open,provider+' explicit manual collapse survives a later active update');
      group.firstChild.click();await frame();
      const colors=[];
      for(const theme of ['light','dark'])for(const size of [10,16,32]){
        document.documentElement.dataset.theme=theme;document.documentElement.style.setProperty('--message-size',size+'px');await frame();
        const summary=group.firstChild,style=getComputedStyle(summary),rect=group.getBoundingClientRect(),parent=container.getBoundingClientRect();
        check(parseFloat(style.fontSize)===size&&rect.width<=parent.width+1&&summary.scrollWidth<=summary.clientWidth+1,provider+' '+theme+' '+size+'px group scales and fits its message container');
        colors.push({theme,size,color:style.color});
        if(provider==='claude'&&globalThis.__captureActivityGroup&&(innerWidth<500&&theme==='dark'&&size===32||innerWidth>=500&&theme==='light'&&size===16)){
          const details=[getNode('reason'),getNode('cmd1'),getNode('cmd2')],states=details.map(node=>node.open),scroll=groupBody.scrollTop;
          details.forEach(node=>node.open=false);groupBody.scrollTop=0;await frame();
          viewport.scrollTop+=group.getBoundingClientRect().top-viewport.getBoundingClientRect().top-12;await frame();
          await globalThis.__captureActivityGroup({theme,size,width:innerWidth});
          details.forEach((node,index)=>node.open=states[index]);groupBody.scrollTop=scroll;await frame();
        }
      }
      check(colors[0].color!==colors[3].color,provider+' activity summary follows the actual light and dark palette');
      const user2=row('user2',5,'user','','Next synthetic prompt'),reason2=row('reason2',6,'reasoning','reasoning'),final2={...row('final2',7,'assistant','','Next final reply'),phase:'final_answer'};
      rows=[...rows,user2,reason2];apply(rows,true);await frame();
      const readingGroup=getNode('reason2').closest('.chat-activity-turn');getNode('reason2').open=true;await frame();
      const readingBody=getNode('reason2').querySelector('.activity-body'),range=document.createRange();range.selectNodeContents(readingBody);document.getSelection().removeAllRanges();document.getSelection().addRange(range);
      readingBody.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
      check(!document.getSelection().isCollapsed&&readingGroup.open,provider+' reader can select actual text inside an active group');
      rows=[...rows,final2];apply(rows);await frame();
      check(readingGroup.open&&getNode('final2').getBoundingClientRect().height>0,provider+' final receipt leaves the selected activity group open and final reply visible');document.getSelection().removeAllRanges();
      const firstTurn={...row('turn-one',8,'activity','fileChange','First native turn tool'),turnId:'native-one'},secondTurn={...row('turn-two',9,'activity','fileChange','Second native turn tool'),turnId:'native-two'};
      rows=[...rows,firstTurn,secondTurn];apply(rows,true);await frame();
      check(getNode('turn-one').closest('.chat-activity-turn')!==getNode('turn-two').closest('.chat-activity-turn'),provider+' explicit native turn IDs keep adjacent intermediate turns separate');
      if(timeline){
        timeline.following=true;
        for(let i=5;i<270;i++)timeline.apply({rows:[row('live-'+i,i)],sequence:++sequence,epoch:'groups',meta:{requests:[]},files:[]});
        const groups=container.__chatActivityGroups?.size||0;
        check(timeline.rows.size<=240&&!group.isConnected&&!groups,provider+' sliding window releases omitted groups: '+JSON.stringify({rows:timeline.rows.size,connected:group.isConnected,groups}));
      }
    }finally{view?.clear();timeline?.dispose();root.remove();}
  }
  document.documentElement.dataset.theme=originalTheme;document.documentElement.style.setProperty('--message-size',originalFont);
  return {passed:checks.length,width:innerWidth,checks};
}
