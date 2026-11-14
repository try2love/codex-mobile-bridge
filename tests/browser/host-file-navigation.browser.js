// Use the production GatewayServer with an isolated project and sibling folder.
window.runHostFileNavigationChecks=async({project,outside})=>{
 await BridgeFeatures.load('chat');
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const previousChat=document.getElementById('chat');if(previousChat)previousChat.id='host-files-previous-chat';
 const chat=document.createElement('section');chat.id='chat';chat.className='chat';chat.style.cssText='position:fixed;inset:0;z-index:100;background:var(--surface);display:flex;flex-direction:column';
 chat.innerHTML='<header class="chat-head"></header><div class="timeline" tabindex="0"></div><textarea></textarea>';
 document.body.append(chat);
 const request=async url=>{const response=await fetch(url);const result=await response.json();if(!response.ok)throw Error(result.error);return result;};
 const wb=new Workbench({chat,request,csrf:()=>'',notify:()=>{},composer:chat.querySelector('textarea'),openChat:()=>{}});
 const wait=async predicate=>{const end=Date.now()+4000;while(!predicate()){if(Date.now()>end)throw Error('File view timed out');await new Promise(r=>setTimeout(r,20));}};
 try{
  wb.open('11111111-1111-4111-8111-111111111111','local');wb.files();
  const session=wb.current,tab=session.files.find(t=>t.id==='files');
  await wait(()=>tab.loaded&&!tab.loading);
  check(session.directory===project&&tab.parentPath===project.slice(0,project.lastIndexOf('/')),'starts in native project with a parent');
  const toolbar=tab.body.querySelector('.wb-toolbar');
  await new Promise(requestAnimationFrame);
  const addressWidth=toolbar.querySelector('input').getBoundingClientRect().width;
  check(toolbar.scrollWidth<=toolbar.clientWidth&&addressWidth>=48,`directory address remains usable without horizontal overflow (${toolbar.scrollWidth}/${toolbar.clientWidth}, input ${addressWidth})`);
  check(!tab.upload.disabled,'project upload remains available');
  const list=tab.list;wb.select('chat');wb.files();check(tab.list===list,'returning to files reuses the existing list');
  let address=tab.body.querySelector('.wb-address input');address.value=outside;address.form.requestSubmit();
  await wait(()=>session.directory===outside&&tab.loaded&&!tab.loading);
  check(tab.upload.disabled&&!tab.up.disabled,'outside navigation permits parent but blocks upload');
  const target=outside+'/报告.txt',row=[...tab.list.children].find(n=>n.dataset.path===target);
  check(!!row,'host list keeps absolute file identity');
  row.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));
  let dialog=document.querySelector('dialog[open]');
  check(!!dialog.querySelector('a[download]')&&![...dialog.querySelectorAll('button')].some(n=>n.textContent==='引用到消息'),'outside menu preserves download and excludes project reference');
  const download=new URL(dialog.querySelector('a').href);check(download.searchParams.get('scope')==='host','download keeps explicit host scope');dialog.close();
  row.click();await wait(()=>session.files.some(t=>t.id==='file:'+target&&t.body.textContent.includes('outside preview')));
  check(true,'actual production HTTP preview reads the sibling file');
  await wb.locate(session,target);await wait(()=>row.classList.contains('wb-locate-highlight'));
  check(tab.list===row.parentNode,'locate reuses the cached directory list');
  address=tab.body.querySelector('.wb-address input');address.value='/';address.form.requestSubmit();
  await wait(()=>session.directory==='/'&&tab.loaded&&!tab.loading);
  check(tab.up.disabled&&tab.upload.disabled,'filesystem root cannot navigate above itself or upload');
  address=tab.body.querySelector('.wb-address input');address.value=project;address.form.requestSubmit();
  await wait(()=>session.directory===project&&tab.loaded&&!tab.loading);
  check(!tab.upload.disabled&&wb.back()&&session.active==='chat','return to initial directory retains project actions and chat back');
  return checks;
 }finally{wb.cancelLocate();wb.stopThumbnails();wb.resizeObserver.disconnect();wb.mobileObserver.disconnect();chat.remove();if(previousChat)previousChat.id='chat';document.querySelectorAll('dialog[open]').forEach(d=>d.close());}
};
