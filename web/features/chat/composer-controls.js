'use strict';
// A shared presentation layer; existing pickers and WorkModes own their state.
class CompactComposer {
  constructor({form,input,attachments,skills,modes}) {
    Object.assign(this,{form,input,attachments,skills,modes});
    form.classList.add('compact-composer');
    const permission=form.querySelector('#permissions-button');if(permission)form.querySelector('.composer-tools').prepend(permission);
    attachments.button.replaceChildren(document.createTextNode('+'));
    attachments.button.setAttribute('aria-label',BridgeI18n.t('添加内容'));
    attachments.button.title=BridgeI18n.t('添加内容');
    attachments.actions=()=>this.actions();
    this.badge=document.createElement('button');this.badge.type='button';this.badge.className='composer-mode-badge';
    this.badge.onclick=()=>this.chooseMode('default');input.before(this.badge);
    this.suggestions=document.createElement('div');this.suggestions.className='composer-suggestions';this.suggestions.hidden=true;
    this.suggestions.setAttribute('role','listbox');input.before(this.suggestions);
    input.addEventListener('input',event=>{if(!event.isComposing)this.suggest();});
    input.addEventListener('keydown',event=>{
      if(event.isComposing||this.suggestions.hidden)return;
      const rows=[...this.suggestions.querySelectorAll('button:not(:disabled)')];
      if(event.key==='Escape'){event.preventDefault();this.dismissed=input.value;this.suggestions.hidden=true;}
      if(rows.length&&['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();this.index=(this.index+(event.key==='ArrowDown'?1:-1)+rows.length)%rows.length;this.highlight(rows);}
      if(event.key==='Enter'&&rows.length){event.preventDefault();event.stopImmediatePropagation();rows[this.index]?.click();}else if(event.key==='Enter')this.suggestions.hidden=true;
    },true);
    input.addEventListener('blur',()=>{this.suggestions.hidden=true;});
    modes.onRender=()=>this.render();
    document.addEventListener('bridge-language',()=>this.render());
    this.render();
  }
  actions(){
    const entries=[];
    if(!this.skills.hidden)entries.push({label:'Skill',shortcut:'$',disabled:this.skills.disabled,run:()=>this.skills.click()});
    for(const [value,label] of [['plan','计划模式'],['goal','目标模式']]){
      const option=this.modes.select.querySelector('[value='+value+']');
      if(this.modes.select.hidden||this.modes.view?.capabilities?.[value]===false)continue;
      entries.push({label,shortcut:'/'+value,disabled:this.modes.select.disabled||option.disabled,run:()=>this.chooseMode(value)});
    }
    return entries;
  }
  chooseMode(value){
    const select=this.modes.select,option=select.querySelector('[value='+value+']');
    if(select.disabled||option?.disabled)return;
    select.value=value;select.onchange();this.render();this.input.focus();
    this.input.dispatchEvent(new Event('input',{bubbles:true}));
  }
  render(){
    this.attachments.button.setAttribute('aria-label',BridgeI18n.t('添加内容'));
    const mode=this.modes.select.value;
    const hidden=!mode||mode==='default';if(this.badge.hidden!==hidden)this.badge.hidden=hidden;
    const label=BridgeI18n.t(mode==='plan'?'计划模式':'目标模式')+' ×';if(this.badge.textContent!==label)this.badge.textContent=label;
    if(this.badge.disabled!==this.modes.select.disabled)this.badge.disabled=this.modes.select.disabled;
    if(this.input.disabled&&!this.suggestions.hidden)this.suggestions.hidden=true;
  }
  highlight(rows){rows.forEach((row,i)=>{row.setAttribute('aria-selected',String(i===this.index));row.classList.toggle('selected',i===this.index);});}
  suggest(){
    const value=this.input.value;if(value!==this.dismissed)this.dismissed=null;
    this.suggestions.replaceChildren();this.suggestions.hidden=true;
    // Only a leading command token; ordinary dollar amounts/code are text.
    const match=value.match(/^([$/])([\w-]*)$/);if(!match||value===this.dismissed||this.input.disabled)return;
    if(match[1]==='$'&&!match[2]&&!this.skills.hidden&&!this.skills.disabled){
      this.input.value='';this.input.dispatchEvent(new Event('input',{bubbles:true}));this.skills.click();return;
    }
    const rows=this.actions().filter(row=>match[1]==='$'?row.shortcut==='$':row.shortcut.startsWith('/'+match[2]));
    for(const action of rows){
      const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.disabled=action.disabled;
      button.textContent=action.shortcut+'  '+BridgeI18n.t(action.label);button.onmousedown=e=>e.preventDefault();
      button.onclick=()=>{if(button.disabled)return;this.input.value='';this.input.dispatchEvent(new Event('input',{bubbles:true}));this.suggestions.hidden=true;action.run();};
      this.suggestions.append(button);
    }
    this.index=0;this.highlight([...this.suggestions.querySelectorAll('button:not(:disabled)')]);this.suggestions.hidden=!rows.length;
  }
}
if(typeof module!=='undefined')module.exports={CompactComposer};
