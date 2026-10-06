'use strict';
const repo='https://github.com/try2love/codex-mobile-bridge';
const video=document.getElementById('tour');
const languageButtons=[...document.querySelectorAll('[data-language]')];
let language='zh',chapters=[];
const translations=[...document.querySelectorAll('[data-en]')].map(node=>({node,zh:node.textContent,en:node.dataset.en}));
const attributes=['alt','aria-label'].flatMap(name=>[...document.querySelectorAll('[data-en-'+name+']')].map(node=>({node,name,zh:node.getAttribute(name),en:node.getAttribute('data-en-'+name)})));
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
  const title=language==='en'?'Codex Mobile Bridge | Continue your Codex work in a browser':'Codex Mobile Bridge｜用浏览器继续你的 Codex 工作';
  const description=language==='en'?'Continue Codex App chats from a phone, tablet or computer browser with your existing ChatGPT login, API or custom provider. Explore the walkthrough, features and downloads.':'从手机、平板或电脑浏览器继续 Codex App 会话，沿用官方登录、API 或自定义模型配置。查看真实界面演示、完整功能与下载。';
  document.title=title;document.querySelector('meta[name=description]').content=description;document.querySelector('meta[property="og:title"]').content=title;document.querySelector('meta[property="og:description"]').content=description;
  const canonical='https://try2love.github.io/codex-mobile-bridge/'+(language==='en'?'?lang=en':'');
  document.querySelector('link[rel=canonical]').href=canonical;document.querySelector('meta[property="og:url"]').content=canonical;
  setTracks();renderChapters();if(lightbox.open&&zoomSource)showImage(zoomSource);
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
