'use strict';
window.runChatMediaChecks = () => {
  const checks=[],host=document.createElement('div');host.style.width='300px';document.body.append(host);
  const thread='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  const file={id:'a'.repeat(64),name:'你好.wav',reference:'/project/你好.wav',image:false};
  const route='/api/sessions/'+thread+'/files/'+file.id+'?host=remote%3Assh';
  const check=(name,ok)=>{if(!ok)throw Error(name);checks.push(name);};
  try{
    renderMarkdown(host,'![audio](</project/你好.wav>)\n\n[声音](</project/你好.wav>)',[file],()=>route);
    const players=[...host.querySelectorAll('audio')];
    check('both native image syntax and ordinary links have a player',players.length===2);
    check('playback waits for user interaction',players.every(p=>p.controls&&p.preload==='none'&&!p.autoplay));
    check('preview retains host selection',players.every(p=>new URL(p.src).searchParams.get('host')==='remote:ssh'&&new URL(p.src).searchParams.get('preview')==='1'));
    check('original file actions remain on separate anchors',[...host.querySelectorAll('a')].every(a=>a.getAttribute('href')===route&&!a.querySelector('audio')));
    check('narrow layout contains controls',host.scrollWidth<=301);
    players[0].dispatchEvent(new Event('error'));
    check('play errors show a download fallback',!host.querySelector('.chat-media-error').hidden&&host.querySelectorAll('a').length===2);
    players[0].dispatchEvent(new Event('loadeddata'));
    check('successful retry clears the error',host.querySelector('.chat-media-error').hidden);
    const blocked=document.createElement('div');
    renderMarkdown(blocked,'![external](https://example.invalid/audio.wav) ![unknown](/project/unknown.wav)');
    check('unapproved and external media remain nonloading',!blocked.querySelector('audio,video,img'));
    check('shared helper rejects external routes',!BridgeChatMedia.create('sound.wav','https://example.invalid'+route));
    check('shared helper rejects active content',!BridgeChatMedia.create('page.html',route));
    const attachments=document.createElement('div');host.append(attachments);
    renderChatAttachments(attachments,[{type:'file',path:'/project/clip.mp4',name:'片段.mp4'}],{fileUrl:()=>'/api/desktop-sessions/claude/workspace/download?sessionId=fixture&path=clip.mp4'});
    const video=attachments.querySelector('video');
    check('desktop file attachments share inline video',video?.playsInline&&video.controls&&video.preload==='none');
    check('desktop file actions remain available',attachments.querySelector('a')?.textContent==='片段.mp4');
    return {passed:checks.length,checks};
  }finally{host.remove();}
};
