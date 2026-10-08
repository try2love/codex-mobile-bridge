'use strict';
class ChatAttachments {
  constructor({root,button,input,upload,onChange,preview,paste,drop,thumbnail}){
    Object.assign(this,{root,button,input,upload,onChange,preview,thumbnail});this.key=null;this.rows=[];this.drafts=new Map();this.locked=false;
    button.onclick=()=>input.click();input.onchange=()=>{this.add([...input.files]);input.value='';};
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
  save(key,rows){try{sessionStorage.setItem('attachments:'+key,JSON.stringify(rows.filter(r=>r.status==='ready').map(({id,name,size,image,thumb,thumbWidth,thumbHeight})=>({id,name,size,image,thumb,thumbWidth,thumbHeight}))));}catch{}}
  add(files){
    if(!this.key||this.locked)return;
    if(this.rows.length+files.length>10){this.error='每条消息最多添加 10 个不同附件';this.render();return;}
    if(files.some(f=>!f.size||f.size>20*1024*1024)){this.error='每个附件需为 1 字节至 20 MB';this.render();return;}
    if([...this.rows,...files].reduce((n,f)=>n+f.size,0)>100*1024*1024){this.error='每条消息的附件总计不能超过 100 MB';this.render();return;}
    this.error='';const key=this.key,rows=this.rows;
    for(const file of files){
      const row={id:uuid(),name:file.name,size:file.size,file,status:'pending'};
      if(typeof File!=='undefined'&&file instanceof File&&typeof URL?.createObjectURL==='function')row.objectUrl=URL.createObjectURL(file);
      rows.push(row);
    }
    this.render();this.uploadPending(key,rows);
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
      if(this.drafts.get(key)===rows){this.save(key,rows);if(this.key===key)this.render();}
    }
  }
  ready(){return this.rows.every(r=>r.status==='ready');}
  ids(){return this.rows.map(r=>r.id);}
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
      const status=row.status==='ready'?t('已上传'):row.status==='failed'?t(row.error):t('正在上传…');
      info.append(node('small',(row.size/1024/1024>=1?(row.size/1024/1024).toFixed(1)+' MB':Math.ceil(row.size/1024)+' KB')+' · '+status,row.status==='failed'?'error':''));card.append(info);
      if(row.status==='failed'){
        const retry=node('button',t('重试'),'plain');retry.type='button';retry.disabled=this.locked;
        retry.onclick=()=>{row.status='pending';this.uploadPending(this.key,this.rows);};card.append(retry);
      }
      const remove=node('button','×','attachment-remove');remove.type='button';remove.disabled=this.locked;remove.setAttribute('aria-label',t('移除附件')+' '+row.name);
      remove.onclick=()=>{this.rows.splice(this.rows.indexOf(row),1);this.error='';this.save(this.key,this.rows);this.render();};card.append(remove);this.root.append(card);
    }
    this.root.hidden=!this.rows.length&&!this.error;this.onChange?.();
  }
}
if(typeof module!=='undefined')module.exports={ChatAttachments};
