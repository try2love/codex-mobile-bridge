// Synthetic metadata only: choosing a host reference must never fetch file bytes.
window.runHostFileReferenceChecks=async()=>{
 await BridgeFeatures.load('chat');
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);},tick=()=>new Promise(r=>setTimeout(r,0));
 const chat=document.createElement('section');chat.className='chat';
 const root=document.createElement('div'),button=document.createElement('button'),input=document.createElement('input'),composer=document.createElement('textarea');
 input.type='file';composer.value='已有草稿';chat.append(root,button,input,composer);document.body.append(chat);
 const session={id:'fixture',host:'local'},calls=[],notices=[],located=[];
 const workspace={current:session,composer,size:n=>String(n),fileIcon:()=>document.createElement('span'),notify:text=>notices.push(text),select:()=>{},
  url:(s,action,path)=>'/fixture/workspace/'+action+'?path='+encodeURIComponent(path),
  async request(url){calls.push(url);const path=new URL(url,location.href).searchParams.get('path');
   if(url.includes('/info'))return {kind:path==='folder'?'directory':'file',name:path.split('/').at(-1),path,absolutePath:'/project/'+path};
   if(url.includes('/files/'))return {name:'inside.txt',path:'inside.txt',size:4,clickDownloadMiB:1,locatable:true,kind:'file'};
   return {project:'fixture',path,entries:path?[{kind:'file',path:'folder/inside.txt',name:'inside.txt',size:4}]:[{kind:'directory',path:'folder',name:'folder'},...Array.from({length:60},(_,i)=>({kind:'file',name:'file-'+i+'.txt',path:'file-'+i+'.txt',size:4}))],nextOffset:null};
  },referenceFile:Workbench.prototype.referenceFile,locate:async(s,path)=>located.push(path)};
 BridgeFileActions.register(chat,workspace);
 const panel=new ChatAttachments({root,button,input,upload:()=>{throw Error('Unexpected upload');},onChange:()=>{},chooseReference:()=>BridgeFileActions.pickReference(workspace)});panel.open('local|fixture');workspace.attachReference=info=>panel.addReference(info);
 const find=text=>[...document.querySelectorAll('dialog[open] button')].find(n=>n.textContent===text);
 try{
  button.click();check(['拍照','选择照片','选择文件'].every(label=>find(label)),'plus separates camera, photos and files');check(document.querySelector('.attachment-menu[open]').getBoundingClientRect().height<240,'plus uses a compact menu');find('引用宿主机项目文件').click();await tick();
  let dialog=document.querySelector('dialog.host-file-picker[open]');check(!!dialog.querySelector('[data-path="folder"]'),'paperclip opens host directory directly');
  await new Promise(requestAnimationFrame);const list=dialog.querySelector('.wb-file-list'),head=dialog.querySelector('.wb-search'),top=head.getBoundingClientRect().top;check(list.scrollHeight>list.clientHeight,'long directory is constrained to its own viewport');list.scrollTop=200;await new Promise(requestAnimationFrame);check(list.scrollTop>0&&head.getBoundingClientRect().top===top&&dialog.scrollTop===0,'only file rows scroll while search stays fixed');list.scrollTop=0;
  dialog.querySelector('[data-path="folder"]').click();await tick();find('引用此文件夹').click();await tick();
  check(composer.value==='已有草稿'&&panel.references()[0].path==='folder','folder is an attachment and leaves message text unchanged');
  button.click();find('引用宿主机项目文件').click();await tick();document.querySelector('dialog[open] [data-path="folder"]').click();await tick();document.querySelector('dialog[open] [data-path="folder/inside.txt"]').click();await tick();
  check(composer.value==='已有草稿'&&panel.references()[1].absolutePath==='/project/folder/inside.txt'&&panel.ids().length===0,'file reference retains native identity without upload');
  check(calls.every(url=>url.startsWith('/fixture/workspace/')),'reference selection fetches metadata only');
  button.click();const before=composer.value;panel.open('local|another');find('引用宿主机项目文件').click();await tick();check(!document.querySelector('dialog[open]')&&composer.value===before,'late attachment choice cannot target another chat');panel.open('local|fixture');
  button.click();find('引用宿主机项目文件').click();await tick();document.querySelector('dialog[open] [data-path="folder"]').click();await tick();dialog=document.querySelector('dialog[open]');let finish;workspace.request=()=>new Promise(resolve=>finish=resolve);dialog.querySelector('[data-path="folder/inside.txt"]').click();workspace.current={};finish({path:'unexpected',kind:'file'});await tick();check(composer.value===before,'late metadata from a previous session cannot append a reference');dialog.close();await tick();workspace.current=session;
  renderChatAttachments(chat,[{type:'file',name:'inside.txt',path:'/project/inside.txt'},{type:'file',name:'outside.txt',path:'/outside/outside.txt'}],{fileUrl:item=>item.path==='/project/inside.txt'?'/api/sessions/11111111-1111-4111-8111-111111111111/files/'+'a'.repeat(64):null});
  const outside=chat.querySelector('[data-unavailable-file]');outside.click();check(notices.at(-1)==='文件不在当前项目中或已不可用'&&!document.querySelector('dialog[open]'),'outside attachment stays clickable but only shows toast');
  workspace.request=async url=>{calls.push(url);return {name:'inside.txt',path:'inside.txt',size:4,clickDownloadMiB:1,locatable:true,kind:'file'};};
  const anchor=chat.querySelector('.timeline-attachments a');anchor.click();await tick();dialog=document.querySelector('dialog[open]');check(!!dialog?.querySelector('[data-direct-download]'),'in-project native attachment offers threshold-limited download');find('定位并打开所在文件夹').click();await tick();check(located[0]==='inside.txt','native attachment locates exact project file');
  workspace.request=async()=>({name:'inside.txt',path:'inside.txt',size:2*1024*1024,clickDownloadMiB:1,locatable:true,kind:'file'});anchor.click();await tick();check(!document.querySelector('dialog[open] [data-direct-download]')&&!!find('定位并打开所在文件夹'),'over-threshold file retains locate without direct download');
 }finally{document.querySelectorAll('dialog[open]').forEach(d=>d.close());chat.remove();}
 return checks;
};
