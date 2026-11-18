'use strict';
// The resolved page theme wins over the OS. This also works on static desktop/site pages.
(()=>{
  const root=document.documentElement,system=window.matchMedia('(prefers-color-scheme: dark)');
  const selector='[data-icon-light][data-icon-dark]';
  function dark(){return root.dataset.theme?root.dataset.theme==='dark':system.matches;}
  function apply(element){
    const attribute=element.tagName==='IMG'?'src':element.tagName==='LINK'?'href':null;
    if(!attribute)return;
    const source=element.getAttribute(dark()?'data-icon-dark':'data-icon-light');
    if(source&&element.getAttribute(attribute)!==source)element.setAttribute(attribute,source);
  }
  function refresh(){document.querySelectorAll(selector).forEach(apply);}
  window.BridgeBrandIcons={apply,refresh};
  new MutationObserver(refresh).observe(root,{attributes:true,attributeFilter:['data-theme']});
  system.addEventListener('change',refresh);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',refresh,{once:true});
  refresh();
})();
