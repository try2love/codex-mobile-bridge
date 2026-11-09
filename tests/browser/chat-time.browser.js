// Production renderers, synthetic messages only; no native client calls.
window.runChatTimeChecks=async function(){
  const check=(value,label)=>{if(!value)throw Error(label);};
  const stamp=new Date(2026,0,2,8,7).getTime(),clock=new Date(stamp).toISOString();
  const messages=[{id:'user',role:'user',text:'Question',timestamp:stamp},
    {id:'reply',role:'assistant',text:'Reply',timestamp:stamp+60000},
    {id:'old',role:'assistant',text:'No native timestamp'}];
  check(!document.getElementById('notify-button'),'composer notification button removed');
  for(const provider of ['claude','deepseek','zcode']){
    const root=document.createElement('div');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
    const select=document.createElement('select');for(const name of ['codex','claude','deepseek','zcode'])select.add(new Option(name,name));
    const detail={session:{id:'fixture',title:'Chat',cwd:'/fixture',status:'idle'},connected:true,draftAccountIdentity:'fixture',capabilities:{send:true,notifications:true},messages};
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
      for(const time of times){const rect=time.getBoundingClientRect();check(rect.width>0&&rect.left>=0&&rect.right<=innerWidth,provider+' visible clock fits viewport');}
      check(root.scrollWidth<=innerWidth,provider+' no horizontal overflow');
    }finally{view.clear();root.remove();}
  }
  const viewport=document.createElement('div'),container=document.createElement('div'),older=document.createElement('button'),newer=document.createElement('button');
  viewport.append(container);document.body.append(viewport);
  const timeline=new ChatTimeline({url:()=>'/synthetic',request:()=>{throw Error('Unexpected read');},renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){},elements:{viewport,container,older,newer}});
  try{
    for(const message of messages)container.append(timeline.renderRow({...message,key:message.id,version:'1'}));
    check(container.querySelectorAll('time.message-time').length===2,'Codex shared timestamp rendering');
    check(container.querySelector('time.message-time').dateTime===clock,'Codex exact native instant');
  }finally{timeline.dispose();viewport.remove();}
  return {providers:4};
};
