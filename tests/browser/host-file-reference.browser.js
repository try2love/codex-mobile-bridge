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
  button.click();check(!!find('从本机上传'),'paperclip preserves local upload option');check(document.querySelector('.attachment-menu[open]').getBoundingClientRect().height<180,'paperclip uses a compact menu');find('引用宿主机项目文件').click();await tick();
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
  const caption=chat.querySelector('.timeline-attachments small'),beforeCalls=calls.length,beforeNotices=notices.length;
  check([...caption.childNodes].some(node=>node.nodeType===Node.TEXT_NODE&&node.textContent==='outside.txt')&&
    ![...caption.querySelectorAll('a,button')].some(node=>node.textContent==='outside.txt'),'unresolved outside attachment retains a plain filename');
  caption.click();await tick();check(calls.length===beforeCalls&&notices.length===beforeNotices&&!document.querySelector('dialog[open]'),'plain unavailable filename cannot fetch bytes or open file actions');
  workspace.request=async url=>{calls.push(url);return {name:'inside.txt',path:'inside.txt',size:4,clickDownloadMiB:1,locatable:true,kind:'file'};};
  const anchor=chat.querySelector('.timeline-attachments a');anchor.click();await tick();dialog=document.querySelector('dialog[open]');check(!!dialog?.querySelector('[data-direct-download]'),'in-project native attachment offers threshold-limited download');find('定位并打开所在文件夹').click();await tick();check(located[0]==='inside.txt','native attachment locates exact project file');
  workspace.request=async()=>({name:'inside.txt',path:'inside.txt',size:2*1024*1024,clickDownloadMiB:1,locatable:true,kind:'file'});anchor.click();await tick();check(!document.querySelector('dialog[open] [data-direct-download]')&&!!find('定位并打开所在文件夹'),'over-threshold file retains locate without direct download');
  const rich=document.createElement('article');chat.append(rich);
  const body=document.createElement('div');body.className='message-body';rich.append(body);
  renderMarkdown(body,'附件：[one.png](https://example.invalid/one)、[two.png](https://example.invalid/two)');
  const uploads=[{type:'image',name:'one.png'},{type:'image',name:'two.png'},{type:'file',name:'unavailable.txt'}];
  renderChatAttachments(rich,uploads,{url:item=>({preview:'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==',original:'/fixture/'+item.name})});
  check(rich.querySelectorAll('.timeline-attachments img').length===2,'both image previews are retained');
  check(['one.png','two.png','unavailable.txt'].every(name=>rich.querySelector('.timeline-attachments small').textContent.includes(name)),
    'same-name external links preserve unresolved attachment filenames');
  const imageFiles=uploads.slice(0,2).map(item=>({name:item.name,reference:'/uploads/'+item.name,path:'/uploads/'+item.name,image:true}));
  const imageText='附件：[one.png](/uploads/one.png)、[two.png](/uploads/two.png)',imageURL=file=>'/fixture/'+file.name;
  body.replaceChildren();renderMarkdown(body,imageText,imageFiles,imageURL);rich.__refreshAttachmentLinks();
  const labels=rich.querySelector('.timeline-attachments small');
  check([...body.querySelectorAll('a')].map(link=>link.textContent).join(',')==='one.png,two.png'&&
    !labels.textContent.includes('one.png')&&!labels.textContent.includes('two.png')&&labels.textContent.includes('unavailable.txt'),
    'linked attachment filenames appear once and the unavailable filename keeps its fallback');
  const linkedOnly=document.createElement('article'),linkedBody=document.createElement('div');
  linkedBody.className='message-body';linkedOnly.append(linkedBody);chat.append(linkedOnly);
  renderMarkdown(linkedBody,imageText,imageFiles,imageURL);
  renderChatAttachments(linkedOnly,uploads.slice(0,2),{url:item=>({preview:'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==',original:'/fixture/'+item.name})});
  check(linkedOnly.querySelectorAll('.timeline-attachments img').length===2&&!linkedOnly.querySelector('.attachment-caption-prefix'),
    'two linked images retain previews without an empty attachment caption');

  const viewport=document.createElement('div'),container=document.createElement('div'),older=document.createElement('button'),newer=document.createElement('button');
  viewport.append(container);chat.append(viewport,older,newer);
  const timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/fixture/'+action+'?',
    request:async()=>{throw Error('Unexpected timeline request');},renderMeta(){},status(){},renderText:(node,text)=>{node.textContent=text;}});
  const initialLanguage=BridgeI18n.language(),nextLanguage=initialLanguage==='en'?'zh':'en';
  try{
    timeline.apply({epoch:'attachment',sequence:1,meta:{requests:[]},hasMore:false,
      files:[{id:'a'.repeat(64),path:'/project/inside.txt',reference:'/project/inside.txt'}],
      rows:[{key:'file-message',role:'user',order:0,version:'1',text:'See attachments.',attachments:[
        {type:'file',name:'inside.txt',path:'/project/inside.txt'},{type:'file',name:'unavailable.txt'}]}]},true);
    const label=container.querySelector('.timeline-attachments small'),file=label.querySelector('a');
    const before=label.querySelector('.attachment-caption-prefix').textContent,href=file.getAttribute('href');
    let clicked=0;file.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();clicked++;});
    BridgeI18n.setLanguage(nextLanguage);timeline.relabel();
    const prefix=label.querySelector('.attachment-caption-prefix').textContent;
    check(prefix===BridgeI18n.t('附件：')&&prefix!==before,'timeline relabel translates only the caption prefix');
    check(label.querySelector('a')===file&&file.getAttribute('href')===href&&label.textContent.includes('unavailable.txt'),
      'timeline relabel preserves the same authorized link and ordinary file fallback');
    file.click();
    check(clicked===1,'same attachment link remains actionable after timeline relabel');
  }finally{BridgeI18n.setLanguage(initialLanguage);timeline.dispose();}
 }finally{document.querySelectorAll('dialog[open]').forEach(d=>d.close());chat.remove();}
 return checks;
};
