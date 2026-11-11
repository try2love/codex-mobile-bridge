// Production renderers, synthetic messages only; no native client calls.
window.runChatTimeChecks=async function(){
  const check=(value,label)=>{if(!value)throw Error(label);};
  const today=new Date();today.setHours(8,7,0,0);const stamp=today.getTime(),clock=new Date(stamp).toISOString();
  const actionsFollow=(node,label,action)=>{
    const bar=node.querySelector('.message-actions'),time=bar?.querySelector('time');
    check(bar?.firstElementChild?.textContent==='复制',label+' copy precedes timestamp');
    check(bar?.children[1]?.textContent==='引用',label+' quote follows copy');
    check(time&&bar.lastElementChild===time,label+' native timestamp is last');
    if(action)check(time.previousElementSibling?.textContent===action,label+' native action precedes timestamp');
    const before=time.previousElementSibling.getBoundingClientRect(),rect=time.getBoundingClientRect();
    if(Math.abs(before.top-rect.top)<before.height)check(rect.left-before.right<=10,label+' timestamp stays beside buttons');
    check(rect.width>0&&rect.left>=0&&rect.right<=innerWidth,label+' visible clock fits viewport');
  };
  const messages=[{id:'user',role:'user',text:'Question',timestamp:stamp,editMessageId:'user'},
    {id:'reply',role:'assistant',text:'Reply',timestamp:stamp+60000,forkMessageId:'reply'},
    {id:'old',role:'assistant',text:'No native timestamp'}];
  check(!document.getElementById('notify-button'),'composer notification button removed');
  for(const provider of ['claude','deepseek','zcode']){
    const root=document.createElement('div');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
    const select=document.createElement('select');for(const name of ['codex','claude','deepseek','zcode'])select.add(new Option(name,name));
    const detail={session:{id:'fixture',title:'Chat',cwd:'/fixture',status:'idle'},connected:true,draftAccountIdentity:'fixture',capabilities:{send:true,notifications:true,fork:true,editMessage:true},messages};
    const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
      if(body)throw Error('Unexpected write');
      return url.includes('/detail')?detail:url.includes('/workspace')?{entries:[],project:'/fixture'}:{sessions:[{id:'fixture',title:'Chat'}]};
    }});
    try{
      await view.choose(provider);await view.open('fixture');clearTimeout(view.timer);
      const times=[...root.querySelectorAll('time.message-time')];
      check(times.length===2,provider+' native clocks without fabricated history');
      check(times[0].dateTime===clock,provider+' exact native instant');
      check(times[0].textContent.includes('08:07'),provider+' minute precision');
      check(![...view.tools.querySelectorAll('button')].some(n=>/提醒/.test(n.textContent)),provider+' redundant reminder removed');
      const original=times[0];view.renderMessages(messages);check(root.querySelector('time.message-time')===original,provider+' refresh preserves node');
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      actionsFollow(view.messageList.querySelector('[data-key="user"]'),provider+' user','编辑并重新发送');
      actionsFollow(view.messageList.querySelector('[data-key="reply"]'),provider+' assistant','从这里分支');
      for(const time of times){const rect=time.getBoundingClientRect();check(rect.width>0&&rect.left>=0&&rect.right<=innerWidth,provider+' visible clock fits viewport');}
      check(root.scrollWidth<=innerWidth,provider+' no horizontal overflow');
    }finally{view.clear();root.remove();}
  }
  const viewport=document.createElement('div'),container=document.createElement('div'),older=document.createElement('button'),newer=document.createElement('button');
  viewport.style.cssText='position:fixed;inset:0;overflow:auto;background:var(--page);z-index:10';
  viewport.append(container);document.body.append(viewport);
  const timeline=new ChatTimeline({url:()=>'/synthetic',request:()=>{throw Error('Unexpected read');},renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){},elements:{viewport,container,older,newer},onAction(){throw Error('Unexpected mutation');}});
  try{
    const rows=messages.map((message,order)=>({...message,key:message.id,version:'1',order,turnId:'turn',turnStatus:'completed',editable:message.role==='user',forkable:message.role==='assistant'}));
    timeline.apply({rows,meta:{latestUserTurnId:'turn'},sequence:1,epoch:'fixture',before:null,hasMore:false},true);
    check(container.querySelectorAll('time.message-time').length===2,'Codex shared timestamp rendering');
    check(container.querySelector('time.message-time').dateTime===clock,'Codex exact native instant');
    actionsFollow(container.querySelector('[data-key="user"]'),'Codex live user','编辑并重新发送');
    actionsFollow(container.querySelector('[data-key="reply"]'),'Codex live assistant','从这里分支');
    for(const node of container.querySelectorAll('.message-edit,.message-fork'))node.remove();
    timeline.refreshActions();
    actionsFollow(container.querySelector('[data-key="user"]'),'Codex dynamically added edit','编辑并重新发送');
    actionsFollow(container.querySelector('[data-key="reply"]'),'Codex dynamically added fork','从这里分支');
    const old=container.querySelector('[data-key="old"]'),body=old.querySelector('.message-body'),audio=document.createElement('audio');body.append(audio);
    timeline.apply({rows:[{...rows[2],timestamp:stamp}],meta:{},sequence:2,epoch:'fixture',before:null,hasMore:false});
    check(container.querySelector('[data-key="old"]')===old&&old.querySelector('audio')===audio,'Late clock preserves existing content and media controls');
    actionsFollow(old,'Codex late native clock','从这里分支');
    // Native side chats use this same timeline with their own endpoint identity.
    timeline.url=()=>'/synthetic/side-chat';timeline.apply({rows:[{...rows[1],text:'Side reply'}],meta:{},sequence:3,epoch:'side',before:null,hasMore:false},true);
    actionsFollow(container.querySelector('[data-key="reply"]'),'Native side chat','从这里分支');
  }finally{timeline.dispose();viewport.remove();}
  const detail=document.createElement('div');detail.style.cssText='position:fixed;inset:0;overflow:auto;background:var(--page);z-index:10';document.body.append(detail);
  const panel=Object.create(AgentsPanel.prototype);Object.assign(panel,{selected:{id:'child'},detail,session:{id:'parent',host:'local'},node:(tag,cls='',text='')=>{const node=document.createElement(tag);node.className=cls;node.textContent=text;return node;},button:(label,click)=>{const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=click;return button;},workbench:{request:async()=>({turns:[{status:'completed',messages}],historyComplete:true})}});
  try{
    await panel.readDetail();
    check(detail.querySelectorAll('time.message-time').length===2,'Subagent retains native timestamps without inventing old clocks');
    actionsFollow(detail.querySelector('.wb-agent-message'),'Subagent user');
    actionsFollow(detail.querySelectorAll('.wb-agent-message')[1],'Subagent assistant');
    const quoted=[];panel.workbench.current=panel.session;panel.workbench.quoteMessage=(...args)=>quoted.push(args);
    const quote=detail.querySelector('.message-quote');quote.disabled=false;quote.click();
    check(quoted.length===1&&quoted[0][0]==='Question'&&quoted[0][2].id==='parent'&&quoted[0][2].host==='local'&&quoted[0][2].provider==='codex','Subagent quote preserves full destination identity');
    panel.workbench.current={};quote.click();
    check(quoted.length===1,'Subagent stale session cannot quote into another chat');
  }finally{panel.detailController?.abort();detail.remove();}
  return {providers:4};
};
