'use strict';
// Native adapters supply identifiers; each chat surface supplies its authorized
// preview route. Keep attachment markup and the existing image viewer shared.
function renderChatAttachments(node,items,{url,fileUrl,showToolImages=true,offlineTurnId,deferOriginals=false}={}){
  if(!items?.length)return;
  node.__refreshAttachmentLinks=()=>renderChatAttachments(node,items,{url,fileUrl,showToolImages,offlineTurnId,deferOriginals});
  node.querySelector?.('.timeline-attachments')?.remove();
  const t=value=>BridgeI18n.t(value),attachments=document.createElement('div');attachments.className='timeline-attachments';
  const offline=()=>globalThis.window?.OfflineChatReader?.enabled;
  const originalLinks=new Map();
  const bodyLinks=[...(node.querySelectorAll?.('.message-body a[href]')||[])];
  const hasBodyLink=(item,name)=>bodyLinks.some(link=>{
    const href=fileUrl?.(item),mapped=link.dataset?.chatFilePath,mappedName=link.dataset?.chatFileName;
    return !!link.textContent.trim()&&((href&&link.getAttribute('href')===href)||
      (item.path&&mapped===item.path)||(!item.path&&mapped&&mappedName===name));
  });
  const openOffline=(target,item)=>target.addEventListener('click',event=>{
    if(!offline())return;
    event.preventDefault();
    const label=target.textContent,button=target.localName==='button';
    if(button){if(target.disabled)return;target.disabled=true;target.textContent=t('正在读取本机附件…');}
    window.OfflineChatReader.openAttachment(item,offlineTurnId,target).catch(error=>{
      if(button)target.textContent=error.message;
    }).then(()=>{
      if(button){target.disabled=false;if(target.textContent===t('正在读取本机附件…'))target.textContent=label;}
    });
  });
  for(const item of items){
    const kind=item.type||item.kind;
    if(kind==='toolImage'&&!showToolImages)continue;
    if(!['localImage','image','toolImage'].includes(kind)&&!(kind==='file'&&item.desktopId))continue;
    if(offline())continue;
    const source=url(item);if(!source)continue;
    const link=document.createElement('a');link.href=source.original;link.target='_blank';link.rel='noopener';
    link.dataset.imagePreview=source.original;link.dataset.imageName=item.name||t('图片附件');
    if(offlineTurnId)openOffline(link,item);
    if(deferOriginals&&(!item.thumb||source.preview===source.original)){
      link.textContent=link.dataset.imageName;originalLinks.set(item,link);continue;
    }
    const image=document.createElement('img');image.className='attachment-image';image.alt=item.name||t('图片附件');
    image.loading='lazy';image.decoding='async';image.src=source.preview;
    image.onerror=()=>link.remove();link.append(image);attachments.append(link);
  }
  const label=document.createElement('small');label.className='muted';
  const prefix=document.createElement('span');prefix.className='attachment-caption-prefix';prefix.textContent=t('附件：');label.append(prefix);
  let count=0;
  items.forEach(item=>{
    const kind=item.type||item.kind,name=item.name||item.path?.split(/[\\/]/).pop()||kind||t('文件');
    if(!offline()&&hasBodyLink(item,name))return;
    if(count++)label.append(document.createTextNode('、'));
    if(originalLinks.has(item)){label.append(originalLinks.get(item));return;}
    if(offline()&&offlineTurnId){
      const button=document.createElement('button');
      button.type='button';button.className='plain';button.textContent=name;
      openOffline(button,item);label.append(button);return;
    }
    if(kind!=='file'||!item.path){label.append(document.createTextNode(name));return;}
    const href=fileUrl?.(item);
    if(!href){label.append(document.createTextNode(name));return;}
    const link=document.createElement('a');link.textContent=name;link.href=href;
    if(offlineTurnId)openOffline(link,item);
    label.append(link);
    const player=href && window.BridgeChatMedia?.create(name,href);
    if(player)attachments.append(player);
  });
  if(count)attachments.append(label);
  if(attachments.children.length)node.append(attachments);
}
class ChatAttachments {
  constructor({root,button,input,upload,onChange,onDraftChange,preview,paste,drop,thumbnail,chooseReference,canReference=()=>true,menuChoices=()=>[],canUpload=()=>true,anchor=()=>button}){
    Object.assign(this,{root,button,input,upload,onChange,onDraftChange,preview,thumbnail,canUpload});this.key=null;this.rows=[];this.drafts=new Map();this.locked=false;
    button.onclick=()=>{
      if(!this.key||this.locked)return;
      const key=this.key,valid=action=>(...args)=>{if(this.key===key&&!this.locked)action(...args);};
      const extra=window.BridgeFileActions?menuChoices():[];
      if(window.BridgeFileActions&&(chooseReference||extra.length)){
        const upload=this.canUpload()?valid(source=>{if(source&&window.BridgeUploadSource&&!window.BridgeUploadSource(source,input.accept))return;input.click();}):null;
        const reference=chooseReference&&canReference()?valid(chooseReference):null;
        const choices=extra.map(choice=>({...choice,action:valid(choice.action)}));
        window.BridgeFileActions.attach(anchor(),upload,reference,choices);
      }else if(this.canUpload())input.click();
    };input.onchange=()=>{this.add([...input.files]);input.value='';};
    paste?.addEventListener('paste',event=>{
      const files=[...event.clipboardData?.files||[]];
      if(files.length){event.preventDefault();this.add(files);}
    });
    drop?.addEventListener('dragover',event=>event.preventDefault());
    drop?.addEventListener('drop',event=>{
      event.preventDefault();
      const files=[...event.dataTransfer?.files||[]];
      if(files.length)this.add(files);
    });
  }
  open(key){this.key=key;this.error='';if(!this.drafts.has(key)){let saved=[];try{saved=JSON.parse(sessionStorage.getItem('attachments:'+key)||'[]');}catch{}this.drafts.set(key,saved.map(row=>({...row,status:'ready'})));}this.rows=this.drafts.get(key);this.render();}
  save(key,rows){try{sessionStorage.setItem('attachments:'+key,JSON.stringify(rows.filter(r=>r.status==='ready').map(({id,name,size,image,thumb,thumbWidth,thumbHeight,reference,kind})=>({id,name,size,image,thumb,thumbWidth,thumbHeight,reference,kind}))));}catch{}}
  draftRows(){
    return this.rows.map(({id,name,size,image,thumb,thumbWidth,thumbHeight,reference,kind,status})=>
      ({id,name,size,image,thumb,thumbWidth,thumbHeight,reference,kind,status:status==='ready'?'ready':'reselect'}));
  }
  restoreDraftRows(snapshot){
    if(!this.key||!Array.isArray(snapshot))return;
    const existing=new Map(this.rows.map(row=>[row.id,row]));
    const restored=snapshot.filter(row=>typeof row?.id==='string'&&typeof row.name==='string').slice(0,10).map(row=>{
      const {id,name,size,image,thumb,thumbWidth,thumbHeight,reference,kind}=row;
      const draft={id,name,size,image,thumb,thumbWidth,thumbHeight,reference,kind};
      return existing.get(id)||(row.status==='ready'?{...draft,status:'ready'}:{
        ...draft,status:'failed',needsReselect:true,error:'文件尚未上传，请重新选择后上传。',
      });
    });
    this.rows.splice(0,this.rows.length,...restored);
    this.save(this.key,this.rows);this.render();
  }
  addReference(info){
    if(!this.key||this.locked||!['file','directory'].includes(info.kind)||!info.path||!info.absolutePath)return false;
    if(this.rows.some(row=>row.reference?.absolutePath===info.absolutePath))return true;
    if(this.rows.length>=10){this.error='每条消息最多添加 10 个不同附件';this.render();return false;}
    this.rows.push({id:uuid(),name:info.name,size:info.size,kind:info.kind,status:'ready',reference:{path:info.path,absolutePath:info.absolutePath}});
    this.error='';this.save(this.key,this.rows);this.render();this.onDraftChange?.();return true;
  }
  add(files){
    if(!this.key||this.locked||!this.canUpload())return;
    if(this.rows.length+files.length>10){this.error='每条消息最多添加 10 个不同附件';this.render();return;}
    if(files.some(f=>!f.size||f.size>20*1024*1024)){this.error='每个附件需为 1 字节至 20 MB';this.render();return;}
    if([...this.rows.filter(row=>!row.reference),...files].reduce((n,f)=>n+f.size,0)>100*1024*1024){this.error='每条消息的附件总计不能超过 100 MB';this.render();return;}
    this.error='';const key=this.key,rows=this.rows;
    for(const file of files){
      const row={id:uuid(),name:file.name,size:file.size,file,status:'pending'};
      if(typeof File!=='undefined'&&file instanceof File&&typeof URL?.createObjectURL==='function')row.objectUrl=URL.createObjectURL(file);
      rows.push(row);
    }
    this.render();this.onDraftChange?.();this.uploadPending(key,rows);
  }
  async uploadPending(key,rows){
    for(const row of rows.filter(r=>r.status==='pending')){
      if(row.status!=='pending'||!rows.includes(row))continue;
      row.status='uploading';if(this.key===key)this.render();
      try{
        const result=await this.upload(key,row.id,row.file);Object.assign(row,result,{status:'ready'});
        if(row.image&&this.thumbnail){
          try{
            const thumb=await this.thumbnail(key,row.id,row.file);
            Object.assign(row,thumb,{thumb:thumb.mime||thumb.image||'image/webp'});
            if(row.objectUrl&&typeof URL?.revokeObjectURL==='function')URL.revokeObjectURL(row.objectUrl);
            delete row.objectUrl;
          }catch{}
        }
      }
      catch(error){row.status='failed';row.error=error.message;}
      if(this.drafts.get(key)===rows){this.save(key,rows);if(this.key===key){this.render();this.onDraftChange?.();}}
    }
  }
  ready(){return this.rows.every(r=>r.status==='ready');}
  ids(){return this.rows.filter(r=>!r.reference).map(r=>r.id);}
  references(){return this.rows.filter(r=>r.reference).map(r=>({...r.reference}));}
  setLocked(value){this.locked=value;this.render();}
  clear(key,ids){
    const rows=this.drafts.get(key)||[];
    for(let i=rows.length-1;i>=0;i--)if(ids.includes(rows[i].id)){
      if(rows[i].objectUrl&&typeof URL?.revokeObjectURL==='function')URL.revokeObjectURL(rows[i].objectUrl);
      rows.splice(i,1);
    }
    if(this.drafts.get(key)===rows){this.save(key,rows);if(this.key===key)this.render();}
  }
  forget(matches){
    for(const [key,rows] of this.drafts)if(matches(key)){for(const row of rows)if(row.objectUrl&&typeof URL?.revokeObjectURL==='function')URL.revokeObjectURL(row.objectUrl);rows.splice(0);this.drafts.delete(key);try{sessionStorage.removeItem('attachments:'+key);}catch{}}
    try{for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('attachments:')&&matches(key.slice(12)))sessionStorage.removeItem(key);}}catch{}
    if(this.key&&matches(this.key)){this.key=null;this.rows=[];this.error='';this.render();}
  }
  reset(){
    for(const rows of this.drafts.values())for(const row of rows)if(row.objectUrl&&typeof URL?.revokeObjectURL==='function')URL.revokeObjectURL(row.objectUrl);
    this.key=null;this.rows=[];this.drafts.clear();this.error='';this.render();
  }
  async thumbnailBlob(file){
    let width,height,source;
    if(typeof createImageBitmap==='function'){
      source=await createImageBitmap(file);width=source.width;height=source.height;
    }else if(typeof Image==='function'&&typeof URL?.createObjectURL==='function'){
      source=await new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('无法解码图片'));image.src=URL.createObjectURL(file);});
      width=source.naturalWidth;height=source.naturalHeight;
    }else throw Error('无法生成缩略图');
    const scale=Math.min(1,640/Math.max(width,height)),canvas=document.createElement('canvas');
    canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
    const context=canvas.getContext('2d');context.drawImage(source,0,0,canvas.width,canvas.height);
    const close=()=>{if('close' in source&&typeof source.close==='function')source.close();};
    const encode=type=>canvas.convertToBlob
      ?canvas.convertToBlob({type,quality:.8})
      :new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('无法生成缩略图')),type,.8));
    let blob=await encode('image/webp');
    if(blob.type!=='image/webp')blob=await encode('image/jpeg');
    close();return {data:blob,width:canvas.width,height:canvas.height,mime:blob.type||'image/jpeg'};
  }
  render(){
    const t=BridgeI18n.t,node=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
    this.root.replaceChildren();this.button.disabled=!this.key||this.locked;
    if(this.error)this.root.append(node('p',t(this.error),'error'));
    for(const row of this.rows){
      const card=node('div',null,'attachment-chip'),info=node('div',null,'attachment-info');
      if(row.image){
        const image=node('img',null,'attachment-thumb');image.alt='';image.decoding='async';
        image.src=(row.status==='ready'&&row.thumb&&this.preview&&this.key)?this.preview(this.key,row.id):(row.objectUrl||'');
        if(row.status==='ready'&&this.preview&&this.key){image.src=this.preview(this.key,row.id);image.dataset.imagePreview=image.src+(image.src.includes('?')?'&':'?')+'variant=original';image.dataset.imageName=row.name;image.tabIndex=0;image.setAttribute('role','button');image.setAttribute('aria-label',t('图片附件')+' '+row.name);}
        image.onerror=()=>image.remove();card.append(image);
      }
      info.append(node('strong',row.name));
      const status=row.reference?t('电脑项目引用'):row.status==='ready'?t('已上传'):row.status==='failed'?t(row.error):t('正在上传…');
      info.append(node('small',(row.kind==='directory'?t('文件夹'):row.size/1024/1024>=1?(row.size/1024/1024).toFixed(1)+' MB':Math.ceil(row.size/1024)+' KB')+' · '+status,row.status==='failed'?'error':''));card.append(info);
      if(row.status==='failed'&&!row.needsReselect){
        const retry=node('button',t('重试'),'plain');retry.type='button';retry.disabled=this.locked||!this.canUpload();
        retry.onclick=()=>{if(this.locked||!this.canUpload())return;row.status='pending';this.uploadPending(this.key,this.rows);};card.append(retry);
      }
      const remove=node('button','×','attachment-remove');remove.type='button';remove.disabled=this.locked&&!globalThis.window?.OfflineChatReader?.enabled;remove.setAttribute('aria-label',t('移除附件')+' '+row.name);
      remove.onclick=()=>{this.rows.splice(this.rows.indexOf(row),1);this.error='';this.save(this.key,this.rows);this.render();this.onDraftChange?.();};card.append(remove);this.root.append(card);
    }
    this.root.hidden=!this.rows.length&&!this.error;this.onChange?.();
  }
}
if(typeof module!=='undefined')module.exports={ChatAttachments};
