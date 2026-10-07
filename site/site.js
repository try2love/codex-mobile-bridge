'use strict';
const repo='https://github.com/try2love/codex-mobile-bridge';
const video=document.getElementById('tour');
const languageButtons=[...document.querySelectorAll('[data-language]')];
let language='zh',chapters=[];
const translations=[...document.querySelectorAll('[data-en]')].map(node=>({node,zh:node.textContent,en:node.dataset.en}));
const attributes=['alt','aria-label','title'].flatMap(name=>[...document.querySelectorAll('[data-en-'+name+']')].map(node=>({node,name,zh:node.getAttribute(name),en:node.getAttribute('data-en-'+name)})));
function setTracks(){for(const track of video.textTracks)track.mode=track.language===language?'showing':'disabled';}
function renderChapters(){
  document.getElementById('chapters').replaceChildren(...chapters.map(chapter=>{
    const button=document.createElement('button'),time=document.createElement('time'),label=document.createElement('span');
    button.type='button';time.textContent=Math.floor(chapter.start/60)+':'+String(Math.floor(chapter.start%60)).padStart(2,'0');label.textContent=chapter[language];
    button.append(time,label);button.onclick=()=>{
      const seek=()=>{video.currentTime=chapter.start;video.play().catch(()=>{});};
      if(video.readyState>=1)seek();else{video.addEventListener('loadedmetadata',seek,{once:true});video.load();}
      video.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});
    };return button;
  }));
}
function setLanguage(value,persist=false){
  language=value==='en'?'en':'zh';document.documentElement.lang=language==='en'?'en':'zh-CN';
  for(const item of translations)item.node.textContent=item[language];
  for(const item of attributes)item.node.setAttribute(item.name,item[language]);
  languageButtons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.language===language)));
  document.querySelectorAll('[data-doc-link]').forEach(link=>link.href=language==='en'?repo+'/blob/main/README_EN.md':repo);
  document.querySelectorAll('[data-setup-link]').forEach(link=>{const url=new URL(link.href);url.searchParams.set('lang',language);link.href=url;});
  const title=language==='en'?'Codex Mobile Bridge | Your workspace, across screens':'Codex Mobile Bridge｜随身接力你的 Codex 工作台';
  const description=language==='en'?'Explore the v2.0 remote workbench for Web, Android and iOS. Try interactive demos of chat, files, terminal, Git and side chats.':'从手机 Web、Android、iOS 和电脑浏览器继续 Codex 工作。体验 v2.0 文件、终端、Git、侧边聊天与分屏工作台的交互演示。';
  document.title=title;document.querySelector('meta[name=description]').content=description;document.querySelector('meta[property="og:title"]').content=title;document.querySelector('meta[property="og:description"]').content=description;
  const canonical='https://try2love.github.io/codex-mobile-bridge/'+(language==='en'?'?lang=en':'');
  document.querySelector('link[rel=canonical]').href=canonical;document.querySelector('meta[property="og:url"]').content=canonical;
  setTracks();renderChapters();if(window.demoExperienceReady)refreshExperience();if(lightbox.open&&zoomSource)showImage(zoomSource);
  if(persist){try{localStorage.setItem('cmb-site-language',language);}catch{}const url=new URL(location.href);url.searchParams.set('lang',language);history.replaceState(null,'',url);}
}
languageButtons.forEach(button=>button.onclick=()=>setLanguage(button.dataset.language,true));
const lightbox=document.getElementById('lightbox');let zoomSource=null;
function showImage(source){const image=document.getElementById('large-image');image.src=source.src;image.alt=source.alt;document.getElementById('image-caption').textContent=source.alt;}
document.querySelectorAll('[data-zoom]').forEach(button=>button.onclick=()=>{zoomSource=button.querySelector('img');showImage(zoomSource);lightbox.showModal();});
document.getElementById('close-lightbox').onclick=()=>lightbox.close();
lightbox.addEventListener('click',event=>{if(event.target===lightbox){const rect=lightbox.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)lightbox.close();}});
const routeButtons=[...document.querySelectorAll('[data-route]')];
function selectRoute(key,focus=false){routeButtons.forEach(button=>{const active=button.dataset.route===key;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;document.getElementById(button.getAttribute('aria-controls')).hidden=!active;if(active&&focus)button.focus();});}
routeButtons.forEach((button,i)=>{
  button.onclick=()=>selectRoute(button.dataset.route);
  button.onkeydown=event=>{let next;if(event.key==='ArrowRight')next=(i+1)%routeButtons.length;if(event.key==='ArrowLeft')next=(i+routeButtons.length-1)%routeButtons.length;if(event.key==='Home')next=0;if(event.key==='End')next=routeButtons.length-1;if(next!==undefined){event.preventDefault();selectRoute(routeButtons[next].dataset.route,true);}};
});
let initial=new URL(location.href).searchParams.get('lang');
if(!['zh','en'].includes(initial)){try{initial=localStorage.getItem('cmb-site-language');}catch{}if(!['zh','en'].includes(initial))initial=navigator.language.toLowerCase().startsWith('zh')?'zh':'en';}
setLanguage(initial);
video.addEventListener('loadedmetadata',setTracks);
fetch('./assets/tour-chapters.json?v=1.3.0').then(response=>{if(!response.ok)throw Error('Chapters unavailable');return response.json();}).then(value=>{chapters=value;renderChapters();}).catch(()=>{});

function openCloudflareGuide(){if(location.hash==='#cloudflare-guide'){selectRoute('cloudflare');document.getElementById('cloudflare-guide').scrollIntoView();}}
window.addEventListener('hashchange',openCloudflareGuide);openCloudflareGuide();

const demoFrame=document.getElementById('product-demo');
const deviceStage=document.getElementById('device-panel');
const deviceButtons=[...document.querySelectorAll('[data-device]')];
let demoPlatform='ios';
const experienceCopy={
 ios:{name:'iOS APP · PREVIEW',zh:['从电脑列表，走进工作台。','保存你的几台电脑，随时进入同一条聊天。打开更多菜单，还能体验通知、设置和灵动岛示意。',['点选 MacBook Pro，进入聊天','点击 ＋，打开文件或侧边聊天','打开更多，查看灵动岛示意'],'iOS 预览需自行签名；灵动岛后台状态可能滞后。'],en:['From your computers to your workspace.','Save several computers and pick up the same chat. Open More to explore notifications, settings and a Live Activity illustration.',['Choose MacBook Pro and open a chat','Use + to open files or a side chat','Open More to preview a Live Activity'],'The iOS preview requires your own signing. Background Live Activity state may be stale.']},
 android:{name:'ANDROID APP · PREVIEW',zh:['随身接力，操作更直接。','扫码保存电脑，手机上继续聊天、管理文件和审阅变更。紧凑的界面，把空间留给当前任务。',['选择一台已保存的电脑','发送一句要求，查看模拟回复','打开 Git，试着暂存与提交'],'Android 提供 APK 安装包；当前以前台通知为主。'],en:['Pick up your work, wherever you are.','Scan and save computers, continue chats, manage files and review changes. A compact interface leaves room for the task.',['Choose a saved computer','Send an instruction and see a simulated reply','Open Git, then try staging and committing'],'An Android APK is available. Notifications currently focus on foreground use.']},
 web:{name:'MOBILE WEB · PREVIEW',zh:['打开浏览器，就到工作现场。','不用安装 App。连接预览网关后，手机浏览器也能使用文件、终端、Git 与侧边聊天。',['直接在输入框补充要求','切换模型、Skill 与权限','点击 ＋，打开新的工作台标签'],'演示为手机浏览器布局；实际访问需连通电脑网关。'],en:['Open a browser. Get back to work.','No App installation needed. Connect to a preview gateway to use files, a terminal, Git and side chats from a mobile browser.',['Add an instruction in the composer','Change model, Skills and permissions','Use + to open a workbench tab'],'This illustrates a mobile browser. Real use requires a reachable computer gateway.']},
 desktop:{name:'DESKTOP WEB · PREVIEW',zh:['一边对话，一边完成工作。','宽屏左右分屏：主聊天与工具各占一栏。试着打开侧边聊天或终端，再拖动中间分隔条。',['左侧保留主聊天','右侧打开文件、Git 或终端','拖动分隔条调整两栏比例'],'窄屏会自动保持单栏。'],en:['Keep the conversation beside the work.','Wide browsers split the main chat and tools into two panes. Open a side chat or terminal, then drag the divider.',['Keep the main chat on the left','Open files, Git or a terminal on the right','Drag the divider to resize the panes'],'Narrow screens stay in a single pane.']},
 gateway:{name:'DESKTOP GATEWAY APP · SIMULATION',zh:['电脑端配置，手机端接力。','按照真实电脑端界面添加连接、保存配置并启动演示网关。二维码、网络检测和服务器部署需要在实际 App 中使用。',['打开「网络与登录」','添加并配置连接方式','保存后回到「连接与状态」启动'],'进阶服务器配置需按教程自行完成；App 不代执行 sudo。'],en:['Set up on desktop. Pick up on mobile.','Use the actual desktop layout to add a connection, save settings and start a demo gateway. Pairing codes, network checks and deployment require the real App.',['Open Network and sign-in','Add and configure a connection','Save, then start from Connection and status'],'Advanced server preparation follows the guide; the App does not run sudo for you.']}
};
function refreshExperience(){
 const copy=experienceCopy[demoPlatform][language];
 document.getElementById('stage-title').textContent=copy[0];document.getElementById('stage-description').textContent=copy[1];document.getElementById('stage-steps').replaceChildren(...copy[2].map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));document.getElementById('stage-boundary').textContent=copy[3];document.getElementById('device-name').textContent=experienceCopy[demoPlatform].name;
 demoFrame.title=language==='en'?'Bridge '+experienceCopy[demoPlatform].name+' interactive demo':'Bridge '+experienceCopy[demoPlatform].name+' 交互演示';
 demoFrame.contentWindow?.postMessage({type:'bridge-demo-language',lang:language},'*');
}
window.demoExperienceReady=true;
function selectDevice(key,scene,focus=false){
 if(!experienceCopy[key])return;
 demoPlatform=key;deviceStage.dataset.platform=key;deviceStage.setAttribute('aria-labelledby','device-'+key);
 deviceButtons.forEach(b=>{const active=b.dataset.device===key;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;if(active&&focus)b.focus();});
 const url=new URL('./demo.html',location.href);url.searchParams.set('platform',key);url.searchParams.set('lang',language);if(scene)url.searchParams.set('scene',scene);else if(key==='desktop')url.searchParams.set('scene','files');
 demoFrame.src=url.href;refreshExperience();
}
deviceButtons.forEach((b,i)=>{b.onclick=()=>selectDevice(b.dataset.device);b.onkeydown=e=>{let next;if(e.key==='ArrowRight')next=(i+1)%deviceButtons.length;if(e.key==='ArrowLeft')next=(i+deviceButtons.length-1)%deviceButtons.length;if(e.key==='Home')next=0;if(e.key==='End')next=deviceButtons.length-1;if(next!==undefined){e.preventDefault();selectDevice(deviceButtons[next].dataset.device,undefined,true);}};});
function showDemoScene(scene,scroll=false){if(scene==='split')selectDevice('desktop','side');else if(demoPlatform==='gateway')selectDevice('desktop',scene);else demoFrame.contentWindow?.postMessage({type:'bridge-demo-scene',scene},'*');if(scroll)document.getElementById('experience').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}
document.querySelectorAll('[data-scene]').forEach(b=>b.onclick=()=>showDemoScene(b.dataset.scene));
document.querySelectorAll('[data-demo-jump]').forEach(b=>b.onclick=()=>showDemoScene(b.dataset.demoJump,true));
demoFrame.onload=refreshExperience;
window.addEventListener('message',e=>{if(e.source!==demoFrame.contentWindow||e.origin!=='null'||e.data?.type!=='bridge-demo-handoff')return;selectDevice('ios','chat');});
selectDevice('ios');
