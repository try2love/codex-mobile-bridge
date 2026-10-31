// Isolated production-resource fixture; no model calls or native writes.
window.runDesktopParityChecks=async function({language='zh',keepOpen=false}={}){
  const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const settle=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  BridgeI18n.setLanguage(language);BridgeI18n.apply();
  document.getElementById('login').hidden=true;
  for(const provider of ['claude','deepseek','zcode']){
    const root=document.createElement('div');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
    const select=document.createElement('select');for(const name of ['codex','claude','deepseek','zcode'])select.add(new Option(name,name));
    const detail={session:{id:'fixture',title:'共享聊天布局',cwd:'/fixture',status:'idle',modelDisplayName:'GLM-5.3-Flash',effort:'low',requests:[],contextUsage:{usedTokens:28522,contextWindow:1000000}},connected:true,draftAccountIdentity:'fixture',
      capabilities:{send:true,attachments:true,uploadFiles:true,plan:true,goal:true,skills:true},messages:[{id:'answer',role:'assistant',text:'Shared reply'}]};
    const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
      if(body)throw Error('Unexpected native write');
      return url.includes('/detail')?detail:url.includes('/workspace')?{entries:[],project:'/fixture'}:{sessions:[{id:'fixture',title:'Shared chat'}]};
    }});
    await view.choose(provider);await view.open('fixture');clearTimeout(view.timer);await settle();
    const actions=view.form.querySelector('.compose-bottom'),rect=actions.getBoundingClientRect(),items=[view.attachButton,view.mode,view.workMode,view.send];
    check(items.every(node=>{const b=node.getBoundingClientRect();return b.width>20&&b.left>=rect.left-1&&b.right<=rect.right+1;}),provider+' controls fit the composer');
    check(Math.max(...items.map(node=>node.getBoundingClientRect().top))-Math.min(...items.map(node=>node.getBoundingClientRect().top))<15,provider+' send and modes stay in one row');
    check(view.contextButton.title.includes('3%'),provider+' uses the shared native context ring');
    view.contextDetails();check(view.contextUsage.dialog.textContent.includes('2.9%'),provider+' shows the native percentage');view.contextUsage.dialog.close();await settle();
    check([...view.messages.querySelectorAll('.message-actions button')].some(node=>node.textContent===BridgeI18n.t('引用')),provider+' offers shared quote actions');
    view.clear();root.remove();
  }
  const canvas=document.createElement('canvas');canvas.width=120;canvas.height=80;
  const url=canvas.toDataURL('image/png'),source=document.createElement('img');source.src=url;document.body.append(source);
  const name='Screenshot_2026-11-01-very-long-image-name-中文.png';
  BridgeImageViewer.configureComposer({scope:()=> 'parity',canReuse:()=>true,add:async()=>true});
  BridgeImageViewer.registerWorkspaceImage(source,{kind:'image',mime:'image/png',data:url.split(',')[1],name,size:120},{id:'fixture'},'parity');
  source.click();await settle();
  const reuse=document.getElementById('image-preview-reuse'),close=document.getElementById('image-preview-close'),title=document.getElementById('image-preview-title'),toolbar=title.parentElement;
  check(!reuse.hidden&&getComputedStyle(reuse).whiteSpace==='nowrap','image reuse stays horizontal');
  check(reuse.getBoundingClientRect().height<60&&toolbar.getBoundingClientRect().height<85,'long filename cannot grow the toolbar vertically');
  check(title.getBoundingClientRect().right<=reuse.getBoundingClientRect().left&&reuse.getBoundingClientRect().right<=innerWidth&&close.getBoundingClientRect().left>=0,'filename yields space to visible controls');
  if(!keepOpen){close.click();await settle();source.remove();}
  return {width:innerWidth,language,checks};
};
