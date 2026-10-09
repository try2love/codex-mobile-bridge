'use strict';
// One file-choice flow for browser, Android and iOS; downloads still use the host's manager.
window.BridgeFileActions = (() => {
  const workbenches=new WeakMap(),t=text=>BridgeI18n.t(text);
  const node=(tag,text,cls='')=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  function dialog(title){const box=node('dialog',undefined,'picker'),head=node('div',undefined,'picker-head'),close=node('button','×','icon-button');close.type='button';close.setAttribute('aria-label',t('关闭'));close.onclick=()=>box.close();head.append(node('h2',title),close);box.append(head);box.addEventListener('close',()=>box.remove());document.body.append(box);return box;}
  function download(box,url,name){const link=node('a',t('下载到本地'),'wb-download');link.href=url;link.download=name;link.dataset.directDownload='true';link.onclick=()=>setTimeout(()=>box.close(),0);box.append(link);return link;}
  function menu(workbench,session,entry){
    if(session!==workbench.current)return;
    const box=dialog(entry.name),url=workbench.url(session,'download',entry.path)+'&explicit=1'+(entry.kind==='directory'?'&archive=1':'');
    download(box,url,entry.name+(entry.kind==='directory'?'.zip':''));
    if(entry.kind==='directory')box.append(node('p',t('文件夹将打包为 ZIP，准备完成后显示下载总量。'),'muted'));
    box.showModal();
  }
  function bindRow(row,workbench,session,entry){
    if(entry.kind==='blocked')return;
    let timer=null,start=null,suppress=false;
    const clear=()=>{clearTimeout(timer);timer=null;};
    row.addEventListener('contextmenu',e=>{e.preventDefault();clear();if(!suppress)menu(workbench,session,entry);});
    row.addEventListener('pointerdown',e=>{if(e.pointerType==='mouse'||e.button>0)return;start={x:e.clientX,y:e.clientY};suppress=false;clear();timer=setTimeout(()=>{timer=null;if(!row.isConnected)return;suppress=true;menu(workbench,session,entry);},550);});
    row.addEventListener('pointermove',e=>{if(start&&Math.hypot(e.clientX-start.x,e.clientY-start.y)>10)clear();});
    for(const event of ['pointerup','pointercancel','pointerleave'])row.addEventListener(event,clear);
    row.addEventListener('click',e=>{if(suppress){e.preventDefault();e.stopImmediatePropagation();suppress=false;}},true);
    row.addEventListener('keydown',e=>{if(e.key==='F10'&&e.shiftKey){e.preventDefault();menu(workbench,session,entry);}});
  }
  async function choose(anchor,workbench){
    const session=workbench.current;if(!session)return;
    const box=dialog(anchor.textContent.trim()||t('文件')),status=node('p',t('正在读取文件…'),'muted');box.append(status);box.showModal();
    try{
      const url=new URL(anchor.href,location.href);url.searchParams.set('info','1');
      const info=await workbench.request(url.pathname+url.search);
      if(!box.open||session!==workbench.current)return box.close();
      status.textContent=info.name+' · '+workbench.size(info.size);
      const locate=node('button',t('定位并打开所在文件夹'),'plain');locate.type='button';locate.disabled=!info.locatable||!info.path;
      locate.onclick=()=>{box.close();workbench.locate(session,info.path).catch(error=>workbench.notify(t(error.message)));};box.append(locate);
      if(info.size<=info.clickDownloadMiB*1024*1024)download(box,anchor.href,info.name);
      else box.append(node('p',t('文件超过点击下载阈值，请在文件标签页中定位后长按下载。'),'muted'));
      if(locate.disabled)box.append(node('p',t('此文件位于聊天项目之外，无法在当前文件标签页中定位。'),'muted'));
    }catch(error){status.textContent=t(error.message);status.className='error';}
  }
  document.addEventListener('click',event=>{
    const anchor=event.target.closest?.('a[href]');
    if(!anchor||event.defaultPrevented||event.button!==0||anchor.dataset.directDownload||anchor.hasAttribute('data-image-preview')||typeof BridgeDownload==='undefined'||!BridgeDownload.eligible(anchor.href,location.href))return;
    const chat=anchor.closest('.chat'),workbench=chat&&workbenches.get(chat);if(!workbench)return;
    event.preventDefault();event.stopImmediatePropagation();choose(anchor,workbench);
  },true);
  function mountSettings(container,request){
    if(!container)return;
    const field=node('fieldset',undefined,'appearance-section'),legend=node('legend',t('文件传输')),label=node('label'),input=node('input'),save=node('button',t('保存配置'),'plain'),message=node('p','','muted');
    legend.dataset.i18n='文件传输';input.type='number';input.min='1';input.max='1048576';input.step='1';input.required=true;save.type='button';save.dataset.i18n='保存配置';
    const caption=node('span',t('点击下载阈值（MiB）'));caption.dataset.i18n='点击下载阈值（MiB）';label.append(caption,input);
    const hint=node('p',t('超过阈值仅显示定位；文件标签页中长按或右键下载不限此大小。'),'muted');hint.dataset.i18n='超过阈值仅显示定位；文件标签页中长按或右键下载不限此大小。';field.append(legend,label,hint,save,message);container.append(field);
    let dirty=false,loading=false;input.oninput=e=>{e.stopPropagation();dirty=true;};input.onchange=e=>e.stopPropagation();
    const read=async()=>{if(dirty||loading||document.hidden)return;loading=true;try{const value=await request();if(!dirty)input.value=value.clickDownloadMiB;}catch(e){message.textContent=t(e.message);}finally{loading=false;}};
    save.onclick=async()=>{if(!input.reportValidity())return;save.disabled=true;const value=Number(input.value);try{await request({clickDownloadMiB:value});if(Number(input.value)===value)dirty=false;message.textContent=t('已保存，立即生效');}catch(e){message.textContent=t(e.message);}finally{save.disabled=false;}};
    // Fetch on reveal/focus, not on a timer. Every download reads the latest server policy.
    new MutationObserver(()=>{if(!container.hidden&&(!('open' in container)||container.open))read();}).observe(container,{attributes:true,attributeFilter:['hidden','open']});
    window.addEventListener('focus',read);read();return field;
  }
  return {register:(chat,workbench)=>workbenches.set(chat,workbench),bindRow,menu,mountSettings};
})();
