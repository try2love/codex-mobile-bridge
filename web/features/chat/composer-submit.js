'use strict';

class ComposerSubmitControl {
  static nextId=0;
  constructor(toolbar,getState){
    this.toolbar=toolbar;this.getState=getState;this.mode=toolbar.controls.sendMode;
    this.send=toolbar.actions.querySelector('button[type=submit]');this.stop=toolbar.actions.querySelector('.stop');
    this.root=toolbar.node('div','composer-submit');this.root.setAttribute('aria-label',toolbar.t('发送与任务控制'));
    this.caret=toolbar.button('发送方式','collapse');this.caret.className='composer-submit-caret';
    this.caret.setAttribute('aria-haspopup','menu');this.caret.setAttribute('aria-expanded','false');
    this.stopButton=toolbar.node('button','stop composer-submit-stop');this.stopButton.type='button';this.send.classList.add('composer-submit-primary');this.stopButton.classList.add('composer-submit-primary');
    this.menu=toolbar.node('div','composer-submit-menu');this.menu.hidden=true;
    this.menu.id='composer-send-options-'+(++ComposerSubmitControl.nextId);this.menu.setAttribute('popover','manual');this.menu.setAttribute('role','menu');
    this.caret.setAttribute('aria-controls',this.menu.id);this.choices=new Map();
    for(const [value,label,detail] of [['queue','完成后发送','等待当前任务结束后执行'],['steer','补充当前任务','立即补充到正在执行的任务']]){
      const button=toolbar.node('button','composer-submit-option');button.type='button';button.dataset.sendMode=value;button.setAttribute('role','menuitemradio');
      const copy=toolbar.node('span');copy.append(toolbar.node('strong'),toolbar.node('small'));
      button.append(copy,this.checkIcon());
      button.onclick=()=>{this.sync();if(button.disabled||this.caret.hidden)return;this.mode.value=value;this.mode.dispatchEvent(new Event('change',{bubbles:true}));this.close();this.caret.focus();};
      this.choices.set(value,{button,label,detail});this.menu.append(button);
    }
    this.menuStop=toolbar.node('button','composer-submit-option composer-submit-menu-stop');this.menuStop.type='button';this.menuStop.setAttribute('role','menuitem');this.menuStop.onclick=()=>this.interrupt();this.menu.append(this.menuStop);
    toolbar.actions.append(this.root);this.root.append(this.send,this.stopButton,this.caret,this.menu);toolbar.sources.append(this.stop);
    this.caret.onclick=()=>{this.sync();if(this.caret.hidden||this.caret.disabled)return;if(this.menu.hidden)this.open();else this.close();this.caret.focus({preventScroll:true});};
    this.stopButton.onclick=()=>this.interrupt();
    this.onMode=()=>this.choose(this.mode.value);this.mode.addEventListener('change',this.onMode,true);
    this.onOutside=event=>{if(!event.composedPath().includes(this.root))this.close();};document.addEventListener('pointerdown',this.onOutside);
    this.onKey=event=>{
      if(event.key==='Escape'&&!this.menu.hidden){event.preventDefault();event.stopPropagation();this.close();this.caret.focus();}
      else if(event.key==='Tab'&&!this.menu.hidden)this.close();
      else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)&&!this.caret.hidden){
        event.preventDefault();this.sync();if(this.caret.disabled)return;this.open();
        const items=[...this.menu.querySelectorAll('button')].filter(button=>!button.disabled),index=items.indexOf(document.activeElement);
        const next=event.key==='Home'?0:event.key==='End'?items.length-1:index<0?(event.key==='ArrowDown'?0:items.length-1):(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
        items[next]?.focus();
      }
    };
    this.root.addEventListener('keydown',this.onKey);
    this.onSubmit=event=>{this.sync();if(this.action().sendDisabled){event.preventDefault();event.stopImmediatePropagation();}};toolbar.form.addEventListener('submit',this.onSubmit,true);
    this.reposition=()=>{if(!this.menu.hidden)this.position();};
    window.addEventListener('resize',this.reposition);window.visualViewport?.addEventListener('resize',this.reposition);window.visualViewport?.addEventListener('scroll',this.reposition);
  }
  availableModes(){return Array.from(this.mode.options).filter(option=>!option.disabled&&!option.hidden).map(option=>option.value);}
  action(){return deriveComposerAction({...this.getState(),availableModes:this.availableModes(),sendEnabled:!this.send.disabled,stopEnabled:!this.stop.hidden&&!this.stop.disabled,modeEnabled:!this.mode.disabled},this.toolbar.preferences.value.followUpMode);}
  choose(value){
    if(!composerSendModes.includes(value))return;
    try{this.toolbar.preferences.update({followUpMode:value});}
    catch(error){this.toolbar.showError(error);}
    this.mode.value=this.toolbar.preferences.value.followUpMode;this.toolbar.requestSync();
  }
  checkIcon(){const icon=ComposerIcons.create('check');icon.classList.add('composer-submit-check');return icon;}
  iconLabel(node,label,id){
    const text=this.toolbar.t(label);
    if(node.children.length===2&&node.firstElementChild.textContent===text&&node.lastElementChild.dataset.icon===id)return;
    node.replaceChildren(this.toolbar.node('span','',text),ComposerIcons.create(id));
  }
  text(node,value){const text=this.toolbar.t(value);if(node.textContent!==text)node.textContent=text;}
  open(){this.menu.hidden=false;if(!this.menu.matches(':popover-open'))this.menu.showPopover();this.toolbar.attribute(this.caret,'aria-expanded',true);this.position();}
  position(){
    const viewport=window.visualViewport,left=viewport?.offsetLeft||0,top=viewport?.offsetTop||0,width=viewport?.width||innerWidth,height=viewport?.height||innerHeight;
    const anchor=this.caret.getBoundingClientRect(),frame=this.toolbar.frame.getBoundingClientRect(),margin=12,gap=8;
    const above=Math.max(0,Math.min(frame.top,top+height)-top-margin-gap),below=Math.max(0,top+height-Math.max(frame.bottom,top)-margin-gap);
    this.menu.style.width=Math.min(280,width-margin*2)+'px';this.menu.style.maxHeight=Math.max(above,below)+'px';
    const rect=this.menu.getBoundingClientRect(),x=Math.max(left+margin,Math.min(anchor.right-rect.width,left+width-rect.width-margin));
    const y=above>=rect.height||above>=below?frame.top-gap-rect.height:frame.bottom+gap;
    this.menu.style.left=x+'px';this.menu.style.top=Math.max(top+margin,Math.min(y,top+height-rect.height-margin))+'px';
  }
  close(){
    if(this.menu.hidden)return;
    const focused=this.menu.contains(document.activeElement);
    if(this.menu.matches(':popover-open'))this.menu.hidePopover();this.menu.hidden=true;this.toolbar.attribute(this.caret,'aria-expanded',false);
    if(focused)(this.caret.hidden?(this.stopButton.hidden?this.send:this.stopButton):this.caret).focus({preventScroll:true});
  }
  interrupt(){this.sync();if(this.action().stopDisabled)return;this.close();this.stop.click();}
  sync(){
    const toolbar=this.toolbar,action=this.action();
    toolbar.attribute(this.root,'data-display',toolbar.display.submit);
    this.root.classList.toggle('is-stop',action.stop);this.send.classList.toggle('composer-submit-hidden',action.stop);
    toolbar.hidden(this.stopButton,!action.stop);if(this.stopButton.disabled!==action.stopDisabled)this.stopButton.disabled=action.stopDisabled;
    this.iconLabel(this.stopButton,'停止','stop');toolbar.attribute(this.stopButton,'aria-label',toolbar.t('停止当前任务'));toolbar.attribute(this.stopButton,'title',toolbar.t('停止当前任务'));toolbar.hidden(this.caret,!action.choices);if(this.caret.disabled!==action.choicesDisabled)this.caret.disabled=action.choicesDisabled;
    this.iconLabel(this.send,action.label,'send');this.send.removeAttribute('data-i18n');
    const modeLabel=this.mode.querySelector('[value='+action.mode+']')?.textContent||'发送新消息';
    toolbar.attribute(this.send,'aria-label',action.label==='正在发送…'?toolbar.t('正在发送…'):modeLabel);toolbar.attribute(this.send,'aria-disabled',action.sendDisabled);toolbar.attribute(this.send,'title',this.send.getAttribute('aria-label'));
    toolbar.attribute(this.caret,'aria-label',toolbar.t('发送方式'));toolbar.attribute(this.menu,'aria-label',toolbar.t('发送方式'));
    const available=this.availableModes();
    for(const [value,{button,label,detail}] of this.choices){
      const disabled=!available.includes(value)||action.choicesDisabled;if(button.disabled!==disabled)button.disabled=disabled;
      toolbar.attribute(button,'aria-checked',action.mode===value);this.text(button.querySelector('strong'),label);this.text(button.querySelector('small'),detail);
    }
    this.iconLabel(this.menuStop,'停止当前任务','stop');if(this.menuStop.disabled!==action.stopDisabled)this.menuStop.disabled=action.stopDisabled;
    if(!action.choices||action.choicesDisabled||toolbar.collapsed)this.close();
    else if(!this.menu.hidden)this.position();
  }
  dispose(){
    this.close();this.mode.removeEventListener('change',this.onMode,true);document.removeEventListener('pointerdown',this.onOutside);
    this.toolbar.form.removeEventListener('submit',this.onSubmit,true);this.root.removeEventListener('keydown',this.onKey);
    window.removeEventListener('resize',this.reposition);window.visualViewport?.removeEventListener('resize',this.reposition);window.visualViewport?.removeEventListener('scroll',this.reposition);
  }
}
