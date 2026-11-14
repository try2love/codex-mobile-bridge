// Run in the isolated production page. Exercises native DOM events, not a fake DOM.
window.runComposerCommandChecks=async()=>{
 const form=document.createElement('form');form.className='composer';form.style.cssText='position:fixed;left:8px;right:8px;bottom:8px;z-index:100;background:var(--surface,#fff)';
 const input=document.createElement('textarea'),submit=document.createElement('button');submit.type='submit';submit.textContent='发送';form.append(input,submit);document.body.append(form);
 const checks=[],calls=[],notices=[];let scope='first',enabled=true,sends=0;
 form.onsubmit=event=>{event.preventDefault();sends++;};
 const actions=()=>[{id:'model',label:'模型',enabled,run:()=>calls.push('model')},{id:'plan',label:'计划模式',enabled:true,args:true,run:()=>calls.push('plan')}];
 const picker=new ComposerCommands({input,scope:()=>scope,actions,onError:text=>notices.push(text)});
 const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
 const type=value=>{input.focus();input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));};
 const key=(value,options={})=>input.dispatchEvent(new KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true,...options}));
 try{
  type('/');check(!picker.menu.hidden&&picker.menu.children.length===2,'slash opens shared actions with Simplified Chinese labels');
  type('/计');check(picker.menu.children.length===1&&picker.menu.textContent.includes('计划模式'),'Chinese command search works');
  key('Escape');check(picker.menu.hidden&&input.value==='/计','Escape preserves the draft');
  type('/model');key('Enter',{isComposing:true});check(calls.length===0&&input.value==='/model','IME confirmation never invokes a command');
  key('Enter');check(calls.join()==='model'&&input.value===''&&sends===0,'keyboard selection runs once and never submits a model message');
  type('/plan 保留草稿');form.requestSubmit();check(calls.at(-1)==='plan'&&input.value==='保留草稿'&&sends===0,'send consumes plan prefix while keeping prompt for explicit submission');
  type('/model');const old=picker.menu.firstElementChild;scope='next';old.click();check(calls.length===2&&input.value==='/model','stale visible option cannot operate on another chat');
  type('/model');enabled=false;form.requestSubmit();check(input.value==='/model'&&sends===0&&notices.length===1,'disabled command remains a draft on submit');
  type('/Users/me/file');form.requestSubmit();check(sends===1,'absolute path keeps normal message submission');
  type('/custom-command arg');form.requestSubmit();check(sends===2,'unknown custom command keeps existing behavior');
  enabled=true;type('/');key('ArrowDown');key('Tab');check(calls.at(-1)==='plan'&&sends===2,'arrow and Tab select an action without form submission');
  type('/');const rect=picker.menu.getBoundingClientRect();check(rect.width<=innerWidth&&picker.menu.scrollHeight>=picker.menu.clientHeight,'menu remains within the viewport');
  return checks;
 }finally{form.remove();}
};
