'use strict';
const panels=[...document.querySelectorAll('[data-setup-panel]')];
const methodLinks=[...document.querySelectorAll('[data-method]')];
const translations=[...document.querySelectorAll('[data-en]')].map(node=>({node,zh:node.textContent,en:node.dataset.en}));
let language='zh',copyTimer;
function setLanguage(value,persist=false){
  language=value==='en'?'en':'zh';document.documentElement.lang=language==='en'?'en':'zh-CN';
  translations.forEach(item=>item.node.textContent=item[language]);
  document.querySelectorAll('[data-language]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.language===language)));
  document.title=language==='en'?'Connection guide · Codex Mobile Bridge':'连接配置指南 · Codex Mobile Bridge';
  if(persist){try{localStorage.setItem('cmb-site-language',language);}catch{}const url=new URL(location.href);url.searchParams.set('lang',language);history.replaceState(null,'',url);}
}
function showMethod(){
  const target=document.getElementById(location.hash.slice(1));
  const key=target?.closest('[data-setup-panel]')?.dataset.setupPanel||'server';
  panels.forEach(panel=>panel.hidden=panel.dataset.setupPanel!==key);
  methodLinks.forEach(link=>{if(link.dataset.method===key)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  if(target)requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));
}
document.querySelectorAll('[data-language]').forEach(button=>button.onclick=()=>setLanguage(button.dataset.language,true));
document.querySelectorAll('[data-copy]').forEach(button=>button.onclick=async()=>{
  const code=button.parentElement.querySelector('code'),status=document.getElementById('copy-status');
  let copied=false;
  try{await navigator.clipboard.writeText(code.textContent);copied=true;}catch{
    const range=document.createRange();range.selectNodeContents(code);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
  }
  status.textContent=copied?(language==='en'?'Copied. Run only in the location stated above.':'已复制。请在步骤标明的位置操作。'):(language==='en'?'Text selected. Copy it with your browser.':'已选中文本，请使用浏览器复制。');
  status.hidden=false;clearTimeout(copyTimer);copyTimer=setTimeout(()=>status.hidden=true,5000);
});
let initial=new URL(location.href).searchParams.get('lang');
if(!['zh','en'].includes(initial)){try{initial=localStorage.getItem('cmb-site-language');}catch{}if(!['zh','en'].includes(initial))initial=navigator.language.toLowerCase().startsWith('zh')?'zh':'en';}
setLanguage(initial);window.addEventListener('hashchange',showMethod);showMethod();
