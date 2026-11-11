'use strict';
// Callers supply the same authorized file route used by the shared file menu.
window.BridgeChatMedia = (() => {
  const audio = /\.(?:wav|mp3|m4a|aac|ogg|oga|opus|flac|aif|aiff)$/i;
  const video = /\.(?:mp4|m4v|webm|ogv|mov)$/i;
  function create(name, href) {
    const kind=audio.test(name)?'audio':video.test(name)?'video':null;
    if(!kind || typeof BridgeDownload==='undefined' || !BridgeDownload.eligible(href,location.href))return null;
    const source=new URL(href,location.href);source.searchParams.set('preview','1');
    const wrapper=document.createElement('span');wrapper.className='chat-media';
    const player=document.createElement(kind);player.controls=true;player.preload='none';
    player.setAttribute('aria-label',name);if(kind==='video')player.playsInline=true;
    const error=document.createElement('span');error.className='muted chat-media-error';error.hidden=true;
    player.onerror=()=>{error.textContent=BridgeI18n.t('无法播放此文件，请通过文件菜单下载后打开。');error.hidden=false;};
    player.onloadeddata=()=>{error.hidden=true;};player.src=source.href;
    wrapper.append(player,error);return wrapper;
  }
  return {create};
})();
