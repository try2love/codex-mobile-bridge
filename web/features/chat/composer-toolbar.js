'use strict';

class ComposerToolbar {
  constructor({form,tools,bottom,actions,input,controls,workMode,body,hint,onPreferenceChange,sendState}){
    Object.assign(this,{form,tools,bottom,actions,input,controls,workMode,body});
    this.labels=Object.fromEntries(composerTools.map(tool=>[tool.id,tool.label]));
    this.shortLabels=Object.fromEntries(composerTools.map(tool=>[tool.id,tool.shortLabel||tool.label]));
    this.preferences=ComposerPreferences.shared();this.layout=this.preferences.value.layout;this.display=this.preferences.value.display;
    this.wrappers={};this.groups={};this.disposed=false;this.followUpMode=this.preferences.value.followUpMode;
    form.classList.add('custom-composer');tools.classList.add('toolbar-top');bottom.classList.add('toolbar-bottom');
    this.frame=this.node('div','composer-frame');body.before(this.frame);this.frame.append(tools,body);
    this.moreButton=this.button('工具面板','more');this.moreButton.classList.add('toolbar-more');this.moreButton.setAttribute('aria-expanded','false');this.moreButton.onclick=()=>this.openMenu();
    controls.more=this.moreButton;for(const key of ['topLeft','topRight','bottomLeft','bottomRight']){
      const group=this.node('div','toolbar-group '+(key.endsWith('Right')?'toolbar-right':'toolbar-left'));group.dataset.toolbarZone=key;this.groups[key]=group;
      if(key.startsWith('top'))tools.append(group);else bottom.insertBefore(group,actions);
    }
    this.collapsed=false;
    this.restoreButton=this.button('展开输入区','collapse');this.restoreButton.classList.add('toolbar-restore');this.restoreButton.hidden=true;this.restoreButton.onclick=()=>this.setCollapsed(false);tools.append(this.restoreButton);
    controls.collapse=this.button(this.labels.collapse,'collapse');controls.collapse.setAttribute('aria-expanded','true');controls.collapse.onclick=()=>this.setCollapsed(!this.collapsed);
    this.sources=this.node('div','toolbar-sources');this.sources.hidden=true;form.append(this.sources);this.sources.append(workMode);
    if(hint)input.after(hint);
    this.modeTag=this.node('button','toolbar-mode-tag');this.modeTag.type='button';this.modeTag.hidden=true;input.before(this.modeTag);this.modeTag.onclick=()=>this.setMode('default');
    for(const id of ['plan','goal']){const button=this.button(this.labels[id],id);button.onclick=()=>this.setMode(workMode.value===id?'default':id);controls[id]=button;}
    for(const id of composerToolIds){
      const control=controls[id];control.dataset.composerTool=id;
      if(['skills','permissions','attachments'].includes(id)){
        control.classList.add('toolbar-icon');control.querySelector('svg')?.remove();this.decorate(id);
      }
      const wrapper=this.node('div','toolbar-tool');wrapper.dataset.tool=id;wrapper.append(control);this.wrappers[id]=wrapper;
      if(id==='model'&&!control.querySelector('svg'))control.prepend(ComposerToolbar.icon(id));
      if(id==='sendMode'){const icon=ComposerToolbar.icon(id);icon.classList.add('toolbar-select-icon');wrapper.prepend(icon);}
      if(['more','attachments','context','plan','goal','collapse'].includes(id))control.append(this.node('span','toolbar-copy',this.t(this.shortLabels[id])));
    }
    this.menu=this.node('dialog','picker toolbar-menu');this.menu.setAttribute('aria-label',this.t('工具面板'));
    const head=this.node('div','picker-head');this.menuTitle=this.node('h2','',this.t('工具面板'));const close=this.node('button','icon-button');close.append(ComposerIcons.create('close'));close.type='button';close.setAttribute('aria-label',this.t('关闭'));close.onclick=()=>this.menu.close();
    this.moreContent=this.node('div','toolbar-more-tools');this.editButton=this.button('定制工具栏','edit');this.editButton.className='icon-button toolbar-edit-button';this.editLabel=this.node('span','',this.t('定制'));this.editButton.append(this.editLabel);this.editButton.onclick=()=>this.edit();head.append(this.menuTitle,this.editButton,close);this.menu.append(head,this.moreContent);document.body.append(this.menu);
    this.menu.addEventListener('click',event=>{const button=event.target.closest('[data-menu-tool]');if(button)this.activate(button.dataset.menuTool);});
    this.menu.addEventListener('close',()=>this.moreButton.setAttribute('aria-expanded','false'));
    this.alerts=Array.from(form.querySelectorAll('[role=alert]'));
    this.submitControl=new ComposerSubmitControl(this,sendState);
    this.alertObserver=new MutationObserver(()=>{
      if(this.collapsed&&this.alerts.some(alert=>alert.textContent.trim()))this.setCollapsed(false);
    });
    for(const alert of this.alerts)this.alertObserver.observe(alert,{subtree:true,childList:true,characterData:true});
    this.modeListener=()=>this.requestSync();workMode.addEventListener('change',this.modeListener);
    this.unsubscribe=this.preferences.subscribe(value=>{
      const modeChanged=this.followUpMode!==value.followUpMode;this.followUpMode=value.followUpMode;
      this.layout=value.layout;this.display=value.display;this.apply();
      if(modeChanged)onPreferenceChange();
      this.editor?.settingsChanged();
    });
    this.onLanguage=()=>this.relabel();document.addEventListener('bridge-language',this.onLanguage);
    this.status=this.node('p','toolbar-status');this.status.hidden=true;this.status.setAttribute('role','status');input.after(this.status);
    this.overflowChanged=()=>this.scheduleOverflow();this.resizeObserver=new ResizeObserver(this.overflowChanged);
    for(const group of Object.values(this.groups)){this.resizeObserver.observe(group);group.addEventListener('scroll',this.overflowChanged,{passive:true});}
    this.apply();
  }
  static icon(id){return ComposerIcons.create(id);}
  icon(id){if(id!=='permissions'){const icon=ComposerToolbar.icon(id);if(id==='collapse'&&this.collapsed)icon.style.transform='rotate(180deg)';return icon;}const mode=this.controls.permissions.dataset.permissionMode||'',icon=permissionIcon(mode);icon.dataset.permissionMode=mode;return icon;}
  t(text){return BridgeI18n.t(text);}
  node(tag,cls='',text){const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;}
  button(label,id){const button=this.node('button','toolbar-icon');button.type='button';button.setAttribute('aria-label',this.t(label));button.title=this.t(label);button.append(this.icon(id));return button;}
  decorate(id){const control=this.controls[id],label=id==='attachments'?this.t(this.labels[id]):control.textContent.trim()||this.t(this.labels[id]);const icon=control.querySelector('svg');if(!icon)control.append(this.icon(id));else if(id==='permissions'&&icon.dataset.permissionMode!==(control.dataset.permissionMode||''))icon.replaceWith(this.icon(id));this.attribute(control,'aria-label',label);if(!control.disabled||!control.title)this.attribute(control,'title',label);}
  attribute(node,name,value){if(node.getAttribute(name)!==String(value))node.setAttribute(name,String(value));}
  hidden(node,value){if(node.hidden!==!!value)node.hidden=!!value;}
  showError(error){this.status.textContent=this.t(error.message);this.status.hidden=false;this.setCollapsed(false);}
  save(layout,display=this.display){this.preferences.update({layout,display});}
  actionMode(){return this.submitControl.action().mode;}
  requestSync(){
    if(this.scheduled||this.disposed)return;
    this.scheduled=true;queueMicrotask(()=>{if(this.scheduled&&!this.disposed)this.sync();});
  }
  apply(){
    for(const key of composerZones){
      const parent=key==='more'?this.sources:this.groups[key];
      this.layout[key].forEach((id,index)=>{
        const wrapper=this.wrappers[id],before=key==='more'?null:parent.children[index];
        if(wrapper.parentElement!==parent||key!=='more'&&before!==wrapper)parent.insertBefore(wrapper,before||null);
      });
    }
    this.controls.sendMode.value=this.preferences.value.followUpMode;
    this.requestSync();
  }

  available(id){return !this.controls[id].hidden;}
  shown(id){return this.available(id)&&this.display[id]!=='hidden';}
  enabled(id){return this.available(id)&&!this.controls[id].disabled;}
  sync(){
    if(this.disposed)return;this.scheduled=false;
    if(this.controls.sendMode.value!==this.preferences.value.followUpMode)this.controls.sendMode.value=this.preferences.value.followUpMode;
    for(const id of ['skills','permissions','attachments'])this.decorate(id);
    this.hidden(this.restoreButton,!this.collapsed||this.tools.contains(this.controls.collapse));
    this.syncWorkMode();
    this.syncTools();
    this.syncLayout();
    this.syncModeTag();
    if(this.menu.open)this.renderMenu();
    this.submitControl.sync();
    this.renderChoice();if(this.editor?.dialog.open)this.editor.updateToolState();this.scheduleOverflow();
  }
  syncWorkMode(){
    for(const id of ['plan','goal']){const option=this.workMode.querySelector('[value='+id+']'),control=this.controls[id];
      const hidden=this.workMode.hidden||!option,disabled=this.workMode.disabled||!option||option.disabled;
      if(control.hidden!==hidden)control.hidden=hidden;if(control.disabled!==disabled)control.disabled=disabled;
      this.attribute(control,'aria-pressed',this.workMode.value===id);
      this.attribute(control,'title',disabled?(option?.title||this.workMode.title||this.t('当前接入不支持此功能')):this.t(this.labels[id]));
    }
  }
  syncTools(){
    for(const id of composerToolIds){
      const wrapper=this.wrappers[id],control=this.controls[id];this.hidden(wrapper,!this.shown(id));this.attribute(wrapper,'data-display',this.display[id]);wrapper.classList.toggle('toolbar-tool-disabled',control.disabled);
      const copy=control.querySelector('.toolbar-copy');if(copy){const label=id==='context'?(control.title||this.t(this.shortLabels[id])):this.t(this.shortLabels[id]);if(copy.textContent!==label)copy.textContent=label;}
      if(id==='model')this.attribute(control,'aria-label',control.title||control.textContent.trim()||this.t(this.labels[id]));
      if(id==='sendMode')this.attribute(wrapper,'title',control.selectedOptions[0]?.textContent||this.t(this.labels[id]));
    }
  }
  syncLayout(){
    // Empty left groups keep right-aligned tools and the fixed send area anchored.
    for(const key of Object.keys(this.groups))this.hidden(this.groups[key],key.endsWith('Right')&&!this.layout[key].some(id=>this.shown(id)));
    const panelZone=composerZones.find(key=>this.layout[key].includes('more'))||'topLeft',panel=this.wrappers.more;
    const panelParent=this.collapsed&&panelZone.startsWith('bottom')?this.tools:this.groups[panelZone];
    if(panel.parentElement!==panelParent){const index=this.layout[panelZone].indexOf('more');panelParent.insertBefore(panel,panelParent===this.tools?panelParent.firstChild:panelParent.children[index]||null);}
    this.hidden(this.tools,!this.collapsed&&!['topLeft','topRight'].some(key=>this.layout[key].some(id=>this.shown(id))));
  }
  syncModeTag(){
    const mode=this.workMode.value;this.hidden(this.modeTag,this.workMode.hidden||!['plan','goal'].includes(mode));const tag=this.t(mode==='goal'?'目标模式':'计划模式')+' ×';if(this.modeTag.textContent!==tag)this.modeTag.textContent=tag;if(this.modeTag.disabled!==this.workMode.disabled)this.modeTag.disabled=this.workMode.disabled;
  }
  scheduleOverflow(){
    if(this.overflowFrame||this.disposed)return;
    this.overflowFrame=requestAnimationFrame(()=>{
      this.overflowFrame=null;
      for(const group of Object.values(this.groups)){
        const left=group.scrollLeft>1,right=group.scrollWidth-group.clientWidth-group.scrollLeft>1;
        group.classList.toggle('can-scroll-left',left);group.classList.toggle('can-scroll-right',right);
        this.attribute(group,'tabindex',left||right?0:-1);
        this.attribute(group,'title',left||right?this.t('左右滑动查看更多工具'):'');
      }
    });
  }
  setCollapsed(value){
    this.collapsed=!!value;this.body.hidden=this.collapsed;this.form.classList.toggle('is-collapsed',this.collapsed);
    const label=this.collapsed?'展开输入区':'收起输入区';this.labels.collapse=label;this.shortLabels.collapse=label;
    this.controls.collapse.setAttribute('aria-expanded',String(!this.collapsed));this.controls.collapse.setAttribute('aria-label',this.t(label));this.controls.collapse.title=this.t(label);
    if(this.collapsed)this.input.blur();else{this.input.style.height='auto';this.input.style.height=Math.min(160,Math.max(48,this.input.scrollHeight||48))+'px';}
    this.sync();
  }
  setMode(value){if(this.workMode.disabled||this.workMode.hidden||this.workMode.querySelector('[value='+value+']')?.disabled)return;this.workMode.value=value;this.workMode.dispatchEvent(new Event('change',{bubbles:true}));this.sync();}
  addChoices(){
    return composerTools.filter(tool=>tool.addMenu&&this.available(tool.id)).map(tool=>({label:this.controls[tool.id].getAttribute('aria-label')||this.t(tool.label),disabled:!this.enabled(tool.id),title:this.controls[tool.id].title,action:()=>{if(this.enabled(tool.id))this.controls[tool.id].click();}}));
  }
  renderMenu(){
    const buttons=new Map(Array.from(this.moreContent.children,button=>[button.dataset.menuTool,button])),focused=document.activeElement;
    let index=0;
    for(const id of this.layout.more){
      if(this.display[id]==='hidden')continue;
      let button=buttons.get(id);buttons.delete(id);
      if(!button){button=this.node('button','toolbar-menu-tool');button.type='button';button.dataset.menuTool=id;button.append(this.icon(id),this.node('span'),this.node('small'));}
      else if(id==='permissions'||id==='collapse'){const icon=this.icon(id),current=button.querySelector('svg');if(current.outerHTML!==icon.outerHTML)current.replaceWith(icon);}
      if(button.disabled===this.enabled(id))button.disabled=!this.enabled(id);const reason=button.querySelector('small'),explanation=button.disabled?(this.available(id)&&this.controls[id].title)||this.t('当前接入不支持此功能'):'';if(reason.textContent!==explanation)reason.textContent=explanation;this.hidden(reason,!explanation);
      const label=id==='permissions'?this.controls[id].getAttribute('aria-label'):this.t(this.labels[id]),copy=button.querySelector('span');if(copy.textContent!==label)copy.textContent=label;
      if(this.controls[id].hasAttribute('aria-pressed'))button.setAttribute('aria-pressed',this.controls[id].getAttribute('aria-pressed'));
      const at=this.moreContent.children[index++];if(at!==button)this.moreContent.insertBefore(button,at||null);
    }
    for(const button of buttons.values())button.remove();
    if(focused?.isConnected&&this.moreContent.contains(focused)&&!focused.disabled&&document.activeElement!==focused)focused.focus({preventScroll:true});
  }
  openMenu(){this.submitControl.close();this.renderMenu();this.menu.showModal();this.moreButton.setAttribute('aria-expanded','true');}
  anchor(id){return this.sources.contains(this.controls[id])?this.moreButton:this.controls[id];}
  activate(id){
    if(!this.enabled(id))return;
    this.menu.close();const control=this.controls[id];
    if(control.tagName!=='SELECT'){control.click();return;}
    this.choice?.dialog.close();
    const dialog=this.node('dialog','picker toolbar-choice'),head=this.node('div','picker-head');
    const title=this.node('h2','',this.t(this.labels[id])),close=this.node('button','icon-button');close.append(ComposerIcons.create('close'));close.type='button';close.setAttribute('aria-label',this.t('关闭'));close.onclick=()=>dialog.close();head.append(title,close);dialog.append(head);
    const buttons=new Map();
    for(const option of control.options){
      const button=this.node('button','toolbar-choice-option');button.type='button';
      button.onclick=()=>{if(!this.enabled(id)||option.disabled||option.hidden)return;control.value=option.value;control.dispatchEvent(new Event('change',{bubbles:true}));dialog.close();};
      buttons.set(option.value,button);dialog.append(button);
    }
    this.choice={dialog,title,close,id,buttons};
    dialog.addEventListener('close',()=>{dialog.remove();if(this.choice?.dialog===dialog)this.choice=null;},{once:true});
    document.body.append(dialog);this.renderChoice();dialog.showModal();
  }
  renderChoice(){
    if(!this.choice)return;
    const {dialog,title,close,id,buttons}=this.choice,control=this.controls[id];
    if(title.textContent!==this.t(this.labels[id]))title.textContent=this.t(this.labels[id]);close.setAttribute('aria-label',this.t('关闭'));
    for(const option of control.options){const button=buttons.get(option.value);if(!button)continue;
      if(button.textContent!==option.textContent)button.textContent=option.textContent;
      button.disabled=option.disabled||control.disabled;button.hidden=option.hidden;
      this.attribute(button,'aria-pressed',option.value===control.value);
    }
    if(control.hidden&&dialog.open)dialog.close();
  }

  async edit(){
    if(this.disposed||this.editingLoad)return;
    this.editingLoad=true;this.editButton.disabled=true;
    const enteredFromMenu=this.menu.open;
    try{
      await BridgeFeatures.load('composer-editor');
      if(this.disposed||enteredFromMenu&&!this.menu.open)return;
      if(this.menu.open)this.menu.close();
      this.editor ||= new ComposerToolbarEditor(this);this.editor.open();
    }catch(error){if(!this.disposed)this.showError(new Error('工具栏编辑器加载失败，请重试。'));}
    finally{this.editingLoad=false;this.editButton.disabled=false;}
  }

  relabel(){
    for(const id of composerToolIds)if(['skills','permissions','attachments'].includes(id))this.decorate(id);else if(this.controls[id].classList.contains('toolbar-icon')){this.controls[id].setAttribute('aria-label',this.t(this.labels[id]));this.controls[id].title=this.t(this.labels[id]);}
    this.moreButton.setAttribute('aria-label',this.t('工具面板'));this.moreButton.title=this.t('工具面板');this.restoreButton.setAttribute('aria-label',this.t('展开输入区'));this.restoreButton.title=this.t('展开输入区');this.menuTitle.textContent=this.t('工具面板');this.editButton.setAttribute('aria-label',this.t('定制工具栏'));this.editButton.title=this.t('定制工具栏');this.editLabel.textContent=this.t('定制');this.editor?.relabel();this.sync();
  }
  dispose(){
    this.disposed=true;this.scheduled=false;this.alertObserver.disconnect();this.submitControl.dispose();
    this.resizeObserver.disconnect();if(this.overflowFrame)cancelAnimationFrame(this.overflowFrame);
    for(const group of Object.values(this.groups))group.removeEventListener('scroll',this.overflowChanged);
    this.unsubscribe();this.choice?.dialog.remove();this.menu.remove();this.editor?.dispose();
    document.removeEventListener('bridge-language',this.onLanguage);this.workMode.removeEventListener('change',this.modeListener);
  }
}
