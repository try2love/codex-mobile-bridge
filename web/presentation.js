'use strict';
// Appearance is local to this browser; no desktop or account settings are changed.
const ChatAppearance=(()=>{
  const defaults={theme:'system',accent:'#4664ed',fontSize:16,codeSize:13,density:'standard',showReasoning:true,showProcess:true,showActivity:true,showComputer:true,showPushplus:true,showAccounts:true,showFileThumbnails:true};
  function normalize(value={}){
    if(!value||typeof value!=='object')value={};
    return {theme:['system','light','dark'].includes(value.theme)?value.theme:defaults.theme,
      accent:/^#[0-9a-f]{6}$/i.test(value.accent)?value.accent:defaults.accent,
      fontSize:Number.isInteger(value.fontSize)&&value.fontSize>=14&&value.fontSize<=20?value.fontSize:defaults.fontSize,
      codeSize:Number.isInteger(value.codeSize)&&value.codeSize>=11&&value.codeSize<=17?value.codeSize:defaults.codeSize,
      density:['compact','standard','relaxed'].includes(value.density)?value.density:defaults.density,
      showReasoning:typeof value.showReasoning==='boolean'?value.showReasoning:defaults.showReasoning,
      showProcess:typeof value.showProcess==='boolean'?value.showProcess:defaults.showProcess,
      showPushplus:typeof value.showPushplus==='boolean'?value.showPushplus:defaults.showPushplus,
      showAccounts:typeof value.showAccounts==='boolean'?value.showAccounts:defaults.showAccounts,
      showFileThumbnails:typeof value.showFileThumbnails==='boolean'?value.showFileThumbnails:defaults.showFileThumbnails,
      showComputer:typeof value.showComputer==='boolean'?value.showComputer:defaults.showComputer,
      showActivity:typeof value.showActivity==='boolean'?value.showActivity:defaults.showActivity};
  }
  function luminance(hex){
    const rgb=hex.match(/[0-9a-f]{2}/gi).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
  }
  function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
  function colors(accent,dark){
    const background=dark?'#17181c':'#ffffff';
    let text=accent;
    const rgb=accent.match(/[0-9a-f]{2}/gi).map(v=>parseInt(v,16));
    for(let step=1;contrast(text,background)<4.5&&step<=20;step++){
      text='#'+rgb.map(v=>Math.round(v+((dark?255:0)-v)*step/20).toString(16).padStart(2,'0')).join('');
    }
    return {text,ink:contrast(accent,'#ffffff')>=contrast(accent,'#000000')?'#ffffff':'#000000'};
  }
  return {defaults,normalize,colors};
})();
if(typeof module!=='undefined')module.exports=ChatAppearance;

if(typeof document!=='undefined')(()=>{
  const get=id=>document.getElementById(id),root=document.documentElement;
  const key='bridge-appearance',system=window.matchMedia('(prefers-color-scheme: dark)');
  let saved;try{saved=JSON.parse(localStorage.getItem(key));}catch{}
  let settings=ChatAppearance.normalize(saved),collapsed=false;
  const composer=get('composer'),body=get('composer-body'),toggle=get('composer-toggle'),message=get('message');
  function preserveReading(change){
    const timeline=typeof chatTimeline==='undefined'?null:chatTimeline;
    const anchor=timeline?.capture(),bottom=timeline?.following;
    change();
    requestAnimationFrame(()=>{if(timeline&&timeline===chatTimeline)timeline.restore(anchor,bottom);});
  }
  function save(){try{localStorage.setItem(key,JSON.stringify(settings));}catch{}}
  function apply(){
    const dark=settings.theme==='dark'||settings.theme==='system'&&system.matches;
    const colors=ChatAppearance.colors(settings.accent,dark);
    root.dataset.theme=dark?'dark':'light';root.dataset.density=settings.density;
    root.dataset.showActivity=String(settings.showActivity);
    root.dataset.showComputer=String(settings.showComputer);get('appearance-computer').checked=settings.showComputer;
    root.dataset.showPushplus=String(settings.showPushplus);root.dataset.showAccounts=String(settings.showAccounts);
    root.dataset.showFileThumbnails=String(settings.showFileThumbnails);
    get('appearance-file-thumbnails').checked=settings.showFileThumbnails;
    window.BridgeWorkbench?.setThumbnails(settings.showFileThumbnails);
    get('appearance-pushplus').checked=settings.showPushplus;get('appearance-accounts').checked=settings.showAccounts;
    root.dataset.showReasoning=String(settings.showReasoning);root.dataset.showProcess=String(settings.showProcess);
    if(typeof chatTimeline!=='undefined')chatTimeline?.setVisibility(settings);
    root.style.setProperty('--accent',settings.accent);
    root.style.setProperty('--accent-ink',colors.ink);
    root.style.setProperty('--green',colors.text);
    root.style.setProperty('--soft',settings.accent+(dark?'29':'12'));
    root.style.setProperty('--message-size',settings.fontSize+'px');
    root.style.setProperty('--code-size',settings.codeSize+'px');
    document.querySelector('meta[name="theme-color"]').content=dark?'#17181c':'#ffffff';
    for(const [id,value] of Object.entries({theme:settings.theme,accent:settings.accent,font:settings.fontSize,code:settings.codeSize,density:settings.density}))get('appearance-'+id).value=value;
    get('appearance-font-value').textContent=settings.fontSize+' px';get('appearance-code-value').textContent=settings.codeSize+' px';
    get('appearance-activity').checked=settings.showActivity;get('appearance-reasoning').checked=settings.showReasoning;get('appearance-process').checked=settings.showProcess;
    document.querySelectorAll('[data-accent]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.accent===settings.accent.toLowerCase())));
  }
  function change(values){preserveReading(()=>{settings=ChatAppearance.normalize({...settings,...values});apply();save();});}
  function relabel(){
    const text=BridgeI18n.t(collapsed?'展开输入区':'收起输入区');
    toggle.setAttribute('aria-label',text);toggle.title=text;
    toggle.setAttribute('aria-expanded',String(!collapsed));
  }
  function resizeMessage(){
    if(body.hidden)return;
    preserveReading(()=>{message.style.height='auto';message.style.height=Math.min(160,Math.max(48,message.scrollHeight))+'px';});
  }
  function setCollapsed(value){
    preserveReading(()=>{
      if(value&&body.contains(document.activeElement))toggle.focus();
      collapsed=value;body.hidden=value;composer.classList.toggle('is-collapsed',value);relabel();
    });
    if(!value)resizeMessage();
  }
  toggle.onclick=()=>setCollapsed(!collapsed);
  message.addEventListener('input',resizeMessage);
  const composerSize=new ResizeObserver(()=>get('chat').style.setProperty('--composer-height',Math.ceil(composer.getBoundingClientRect().height)+'px'));
  composerSize.observe(composer);
  const pageState=new MutationObserver(()=>document.body.classList.toggle('chat-detail',!get('app').hidden&&get('app').classList.contains('chat-open')));
  pageState.observe(get('app'),{attributes:true,attributeFilter:['hidden','class']});
  document.querySelectorAll('[data-open-appearance]').forEach(button=>button.onclick=()=>{get('appearance-dialog').showModal();window.loadNotificationDefaults?.();});
  get('chat-details-button').onclick=()=>{get('details-title').textContent=get('chat-title').textContent;get('chat-details-dialog').showModal();};
  // This script is loaded after app.js, which binds the existing close controls.
  get('appearance-language').onchange=()=>{get('phone-language').value=get('appearance-language').value;get('phone-language').onchange();get('appearance-language').value=BridgeI18n.language();};
  get('appearance-theme').onchange=()=>change({theme:get('appearance-theme').value});
  get('appearance-accent').oninput=()=>change({accent:get('appearance-accent').value});
  get('appearance-font').oninput=()=>change({fontSize:Number(get('appearance-font').value)});
  get('appearance-code').oninput=()=>change({codeSize:Number(get('appearance-code').value)});
  get('appearance-density').onchange=()=>change({density:get('appearance-density').value});
  get('appearance-reasoning').onchange=()=>change({showReasoning:get('appearance-reasoning').checked});
  get('appearance-computer').onchange=()=>change({showComputer:get('appearance-computer').checked});
  get('appearance-activity').onchange=()=>change({showActivity:get('appearance-activity').checked});
  get('appearance-file-thumbnails').onchange=()=>change({showFileThumbnails:get('appearance-file-thumbnails').checked});
  get('appearance-process').onchange=()=>change({showProcess:get('appearance-process').checked});
  document.querySelectorAll('[data-accent]').forEach(button=>button.onclick=()=>change({accent:button.dataset.accent}));
  get('appearance-pushplus').onchange=()=>change({showPushplus:get('appearance-pushplus').checked});
  get('appearance-accounts').onchange=()=>change({showAccounts:get('appearance-accounts').checked});
  get('appearance-reset').onclick=()=>change(ChatAppearance.defaults);
  system.addEventListener('change',()=>{if(settings.theme==='system')preserveReading(apply);});
  window.BridgePresentation={resizeMessage,relabel,openChat:()=>{setCollapsed(false);requestAnimationFrame(resizeMessage);},showError:()=>setCollapsed(false)};
  apply();relabel();resizeMessage();
  document.body.classList.toggle('chat-detail',!get('app').hidden&&get('app').classList.contains('chat-open'));
})();
