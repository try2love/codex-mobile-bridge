// Isolated production page only; never saves files or calls a real gateway.
async function runImageSaveChecks(){
  let passed=0;
  const check=(ok,label)=>{if(!ok)throw Error(label);passed++;},id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  const anchor=document.createElement('button');document.body.append(anchor);
  const dialog=()=>document.getElementById('image-preview-dialog');
  const name='Screenshot_2026-11-01_很长的原始图片文件名.png';
  const open=url=>{anchor.dataset.imagePreview=url;anchor.dataset.imageName=name;anchor.click();};
  BridgeImageViewer.configureComposer({scope:()=> 'fixture',canReuse:()=>true,add:()=>true});
  try{
    for(const path of [`/api/sessions/${id}/uploads/${id}/preview`,...['claude','deepseek','zcode'].map(provider=>`/api/desktop-sessions/${provider}/uploads/${id}/preview?sessionId=fixture`)]){
      open(path);check(dialog()?.open,'approved image opens');
      const save=document.getElementById('image-preview-save'),url=new URL(save.href);
      check(!save.hidden&&url.searchParams.get('download')==='1'&&url.searchParams.get('variant')==='original','download requests original bytes');
      check(save.download===name,'filename survives preview');
      const toolbar=dialog().querySelector('.image-preview-toolbar'),controls=save.closest('.image-preview-controls'),rect=save.getBoundingClientRect();
      check(toolbar.scrollWidth<=toolbar.clientWidth+1,'toolbar fits viewport');
      check(controls&&controls.lastElementChild===save&&save.previousElementSibling.id==='image-preview-fit','save is last, after fit');
      check(controls.scrollWidth<=controls.clientWidth+1&&rect.right<=innerWidth,'footer fits viewport');
      check(save.querySelector('svg')&&save.getAttribute('aria-label')===BridgeI18n.t('保存到本地')&&!save.textContent,'save uses accessible icon only');
      const reuse=document.getElementById('image-preview-reuse'),reuseRect=reuse.getBoundingClientRect();
      const close=document.getElementById('image-preview-close').getBoundingClientRect(),title=document.getElementById('image-preview-title').getBoundingClientRect();
      check(!reuse.hidden&&reuse.textContent===BridgeI18n.t('引用'),'short reference label');
      check(close.right<=title.left&&title.right<=reuseRect.left&&reuseRect.right<=innerWidth,'header order fits without overlap');
      check(Math.abs(close.top+close.height/2-reuseRect.top-reuseRect.height/2)<1&&toolbar.children.length===3,'header stays one row');
      check(getComputedStyle(reuse).whiteSpace==='nowrap','reuse label stays horizontal');
      dialog().close();
    }
    const image=document.createElement('img'),data='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2p8AAAAASUVORK5CYII=';
    image.src='data:image/png;base64,'+data;document.body.append(image);
    try{
      const path=`/api/sessions/${id}/workspace/download?path=image.png`;
      check(BridgeImageViewer.registerWorkspaceImage(image,{kind:'image',mime:'image/png',data,name:'image.png'},{id,provider:'codex'},null,path),'workspace image is renderer registered');
      image.click();check(document.getElementById('image-preview-save').href===new URL(path,location.href).href,'workspace download reuses its authorized URL');dialog().close();
    }finally{image.remove();}
    open('https://other.invalid/api/sessions/'+id+'/uploads/'+id+'/preview');
    check(!dialog().open,'arbitrary remote images do not gain a save entry');
    return {passed,width:innerWidth};
  }finally{if(dialog()?.open)dialog().close();anchor.remove();BridgeImageViewer.configureComposer(null);}
}
