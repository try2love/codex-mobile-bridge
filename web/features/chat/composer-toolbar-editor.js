'use strict';

class ComposerToolbarEditor {
  constructor(toolbar){
    this.toolbar=toolbar;this.dialog=toolbar.node('dialog','picker toolbar-editor');this.dialog.setAttribute('aria-label',toolbar.t('定制工具栏'));
    this.buildHeader();
    this.body=toolbar.node('div','toolbar-editor-body');
    this.buildPreview();
    this.buildSettings();
    this.dialog.append(this.head,this.body,this.status);
    document.body.append(this.dialog);
    this.bindInteraction();
  }
  buildHeader(){
    const toolbar=this.toolbar;
    this.head=toolbar.node('div','picker-head');
    this.title=toolbar.node('h2','',toolbar.t('定制工具栏'));
    const titleGroup=toolbar.node('div','toolbar-editor-title');
    titleGroup.append(this.title);
    this.single=toolbar.node('button','plain',toolbar.t('单栏默认'));
    this.single.type='button';
    this.single.dataset.composerPreset='single';
    this.single.onclick=()=>this.applyPreset('single');
    this.reset=toolbar.node('button','plain',toolbar.t('双栏默认'));
    this.reset.type='button';
    this.reset.dataset.composerPreset='default';
    this.reset.onclick=()=>this.applyPreset('default');
    this.closeButton=toolbar.node('button','primary toolbar-editor-close');
    this.closeButton.append(ComposerIcons.create('close'));
    this.closeButton.type='button';
    this.closeButton.autofocus=true;
    this.closeButton.onclick=()=>this.dialog.close();
    this.head.append(titleGroup,this.single,this.reset,this.closeButton);
  }
  buildPreview(){
    const toolbar=this.toolbar;
    this.canvas=toolbar.node('section','toolbar-editor-canvas');this.groups={};this.groupNames={topLeft:'上栏左侧',topRight:'上栏右侧',bottomLeft:'下栏左侧',bottomRight:'下栏右侧',more:'工具面板'};
    const row=position=>{
      const section=toolbar.node('section','toolbar-editor-row toolbar-editor-row-'+position);
      const bar=toolbar.node('div','toolbar-editor-bar');
      const pin=position==='bottom'?toolbar.node('button','toolbar-editor-fixed toolbar-editor-submit'):null;
      if(pin){pin.type='button';pin.dataset.editorTool='submit';pin.onclick=()=>this.selectTool('submit');pin.setAttribute('aria-label',toolbar.t('发送 · 固定'));pin.append(ComposerToolbar.icon('send'),toolbar.node('small','toolbar-preview-caption',toolbar.t('发送')));}
      for(const side of ['Left','Right']){const key=position+side,group=toolbar.node('div','toolbar-drop-zone '+(side==='Right'?'toolbar-align-right':''));group.dataset.dropZone=key;group.setAttribute('aria-label',toolbar.t(this.groupNames[key]));this.groups[key]=group;bar.append(group);}
      if(position==='bottom')bar.append(pin);section.append(bar);return section;
    };
    this.canvas.append(row('top'));
    const input=toolbar.node('div','toolbar-editor-input',toolbar.t('消息内容'));this.canvas.append(input,row('bottom'));this.previewArea=toolbar.node('section','toolbar-editor-preview-area');this.previewArea.append(this.canvas);this.body.append(this.previewArea);
    this.library=toolbar.node('section','toolbar-editor-toolbox');const more=toolbar.node('div','toolbar-drop-zone toolbar-editor-library');more.dataset.dropZone='more';more.setAttribute('aria-label',toolbar.t('工具面板'));this.groups.more=more;this.library.append(more);this.body.append(this.library);
  }
  buildSettings(){
    const toolbar=this.toolbar;
    this.stylePanel=toolbar.node('section','toolbar-display-settings');this.styleTitle=toolbar.node('p','toolbar-display-title');this.styleChoices=toolbar.node('div','toolbar-display-choices');
    for(const [mode,label] of [['hidden','隐藏'],['icon','仅图标'],['text','仅文字'],['both','图标＋文字']]){const button=toolbar.node('button','',toolbar.t(label));button.type='button';button.dataset.displayChoice=mode;button.onclick=()=>this.changeDisplay(mode);this.styleChoices.append(button);}
    this.stylePreview=toolbar.node('div','toolbar-display-preview');this.stylePreview.setAttribute('aria-hidden','true');this.toolStatus=toolbar.node('p','muted toolbar-tool-status');const styleHead=toolbar.node('div','toolbar-display-heading');styleHead.append(this.styleTitle,this.stylePreview);this.styleLabel=toolbar.node('p','toolbar-setting-label');this.stylePanel.append(styleHead,this.styleLabel,this.styleChoices);this.inspector=toolbar.node('section','toolbar-editor-inspector');this.inspector.append(this.stylePanel);this.body.append(this.inspector);
    this.movePanel=toolbar.node('section','toolbar-move-settings');this.moveTitle=toolbar.node('p','toolbar-display-title');this.moveChoices=toolbar.node('div','toolbar-move-choices');
    for(const zone of composerZones){const button=toolbar.node('button');button.type='button';button.dataset.moveZone=zone;button.onclick=()=>this.relocate(this.selectedId,zone,this.draft[zone].length);this.moveChoices.append(button);}
    this.movePanel.append(this.moveTitle,this.moveChoices);this.inspector.append(this.movePanel,this.toolStatus);
    this.status=toolbar.node('p','muted toolbar-editor-status');this.status.setAttribute('aria-live','polite');
  }
  bindInteraction(){
    const toolbar=this.toolbar;
    this.dialog.addEventListener('pointerdown',event=>{if(event.pointerType!=='touch')this.press(event);});
    this.dialog.addEventListener('pointermove',event=>{if(event.pointerType!=='touch')this.move(event);});
    this.dialog.addEventListener('pointerup',event=>{if(event.pointerType!=='touch')this.release(event);});
    this.dialog.addEventListener('pointercancel',event=>{if(event.pointerType!=='touch')this.cancelDrag();});this.dialog.addEventListener('lostpointercapture',event=>{if(event.pointerType!=='touch')this.cancelDrag();});
    const point=(event,touch)=>({target:event.target,button:0,pointerId:touch.identifier,clientX:touch.clientX,clientY:touch.clientY,isTouch:true,preventDefault:()=>event.preventDefault()});
    this.dialog.addEventListener('touchstart',event=>{if(event.touches.length===1)this.press(point(event,event.touches[0]));else this.cancelDrag();},{passive:true});
    this.dialog.addEventListener('touchmove',event=>{if(event.touches.length!==1){this.cancelDrag();return;}this.move(point(event,event.touches[0]));},{passive:false});
    this.dialog.addEventListener('touchend',event=>{if(this.drag)event.preventDefault();if(event.changedTouches[0])this.release(point(event,event.changedTouches[0]));},{passive:false});
    this.dialog.addEventListener('touchcancel',()=>this.cancelDrag());
    this.dialog.addEventListener('contextmenu',event=>{if(event.target.closest('[data-drag-tool]'))event.preventDefault();});
    this.dialog.addEventListener('click',event=>{const button=event.target.closest('[data-drag-tool]');if(button)this.selectTool(button.dataset.dragTool);});
    this.dialog.addEventListener('keydown',event=>this.keydown(event));
    this.dialog.addEventListener('close',()=>{this.cancelDrag();toolbar.moreButton.focus({preventScroll:true});});
    this.dialog.addEventListener('cancel',event=>{if(this.drag||this.pending){event.preventDefault();this.cancelDrag();}});
    this.onHide=()=>{if(document.hidden)this.cancelDrag();};document.addEventListener('visibilitychange',this.onHide);
  }

  open(){
    this.selectedId='model';this.status.textContent='';this.relabel();this.settingsChanged();this.dialog.showModal();
  }
  settingsChanged(){
    this.cancelDrag();this.draft=normalizeComposerLayout(this.toolbar.layout);this.displayDraft=normalizeComposerDisplay(this.toolbar.display);
    const focused=this.dialog.contains(document.activeElement)?document.activeElement.closest('[data-drag-tool]')?.dataset.dragTool:null;
    this.render(focused);
  }
  relabel(){
    const t=text=>this.toolbar.t(text);
    this.dialog.setAttribute('aria-label',t('定制工具栏'));this.title.textContent=t('定制工具栏');this.reset.textContent=t('双栏默认');this.closeButton.setAttribute('aria-label',t('关闭'));
    this.single.textContent=t('单栏默认');
    this.styleLabel.textContent=t('显示方式');
    this.dialog.querySelectorAll('.toolbar-editor-fixed').forEach(node=>{const label=t('发送 · 固定');node.querySelector('small').textContent=t('发送');node.setAttribute('aria-label',label);});
    this.dialog.querySelector('.toolbar-editor-input').textContent=this.toolbar.input.placeholder||t('消息内容');
    for(const zone of composerZones)this.groups[zone].setAttribute('aria-label',t(this.groupNames[zone]));
    const styleLabels={icon:'仅图标',text:'仅文字',both:'图标＋文字',hidden:'隐藏'};
    this.styleChoices.querySelectorAll('button').forEach(button=>button.textContent=t(styleLabels[button.dataset.displayChoice]));
    this.moveTitle.textContent=t('移动到');const shortZones={topLeft:'上左',topRight:'上右',bottomLeft:'下左',bottomRight:'下右',more:'工具面板'};this.moveChoices.querySelectorAll('button').forEach(button=>{const zone=button.dataset.moveZone;button.textContent=t(shortZones[zone]);button.setAttribute('aria-label',t(this.groupNames[zone]));});
    if(this.draft)this.render();
  }
  toolLabel(id){
    const toolbar=this.toolbar;
    if(id==='model')return Array.from(toolbar.controls.model.querySelectorAll('span')).filter(node=>!node.hidden).map(node=>node.textContent.trim()).join(' ')||toolbar.t(toolbar.shortLabels[id]);
    if(id==='permissions')return toolbar.controls[id].textContent.trim()||toolbar.t(toolbar.shortLabels[id]);
    if(id==='sendMode')return toolbar.controls.sendMode.selectedOptions[0]?.textContent||toolbar.t(toolbar.shortLabels[id]);
    if(id==='submit')return toolbar.t('发送');
    return toolbar.t(toolbar.shortLabels[id]);
  }
  render(focusId){
    const toolbar=this.toolbar;
    for(const key of Object.keys(this.groups)){
      const group=this.groups[key];group.replaceChildren();
      for(const id of this.draft[key]){
        const hidden=this.displayDraft[id]==='hidden',button=toolbar.node('button','toolbar-drag-tool');button.type='button';button.dataset.dragTool=id;button.classList.toggle('toolbar-drag-hidden',hidden);button.setAttribute('aria-label',toolbar.t(toolbar.labels[id])+(hidden?' · '+toolbar.t('隐藏'):''));
        const names={model:'模型',collapse:'收起',plan:'计划',goal:'目标'},label=toolbar.t(names[id]||toolbar.shortLabels[id]);button.append(toolbar.icon(id),key==='more'?toolbar.node('span','',label+(hidden?' · '+toolbar.t('隐藏'):'')):toolbar.node('small','toolbar-preview-caption',label));
        button.title=toolbar.controls[id].disabled?toolbar.controls[id].title||toolbar.t('当前接入不支持此功能'):toolbar.t(toolbar.labels[id]);group.append(button);
      }
    }
    const single=composerPreset('single');this.single.setAttribute('aria-pressed',String(JSON.stringify(single.layout)===JSON.stringify(this.draft)&&JSON.stringify(single.display)===JSON.stringify(this.displayDraft)));
    this.renderStyle();
    if(focusId)this.dialog.querySelector('[data-drag-tool='+focusId+']')?.focus();
  }
  selectTool(id){
    if(this.selectedId!==id){this.selectedId=id;this.renderStyle();}
  }
  renderStyle(){
    this.dialog.querySelectorAll('[data-drag-tool]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.dragTool===this.selectedId)));
    this.dialog.querySelector('[data-editor-tool=submit]').setAttribute('aria-pressed',String(this.selectedId==='submit'));
    this.updateToolState();
    const toolbar=this.toolbar,id=this.selectedId,mode=this.displayDraft[id];this.styleTitle.textContent=toolbar.t(id==='submit'?composerSubmitTool.label:toolbar.shortLabels[id]);this.movePanel.inert=id==='submit';this.movePanel.style.visibility=id==='submit'?'hidden':'';this.styleChoices.setAttribute('role','group');this.styleChoices.setAttribute('aria-label',toolbar.t('显示方式'));
    this.styleChoices.querySelectorAll('button').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.displayChoice===mode));button.hidden=button.dataset.displayChoice==='hidden'&&['more','submit'].includes(id);});
    this.moveChoices.querySelectorAll('button').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.moveZone===this.zoneOf(id)));button.disabled=id==='more'&&button.dataset.moveZone==='more';});
  }
  updateToolState(){
    if(!this.selectedId)return;
    const control=this.toolbar.controls[this.selectedId];
    const reason=this.displayDraft[this.selectedId]==='hidden'?this.toolbar.t('已隐藏，可在此恢复显示。'):control?.disabled?this.toolbar.t(control.title||'当前接入不支持此功能'):'';
    if(this.toolStatus.textContent!==reason)this.toolStatus.textContent=reason;
    this.toolbar.hidden(this.toolStatus,!reason);
    const id=this.selectedId,mode=this.displayDraft[id],label=this.toolLabel(id),key=JSON.stringify([id,mode,label]);
    if(this.previewKey!==key){
      this.previewKey=key;this.stylePreview.replaceChildren();
      if(!['text','hidden'].includes(mode))this.stylePreview.append(id==='submit'?ComposerIcons.create('send'):this.toolbar.icon(id));
      if(mode!=='icon'){const text=this.toolbar.node('span','',mode==='hidden'?this.toolbar.t('隐藏'):label);if(id==='submit')this.stylePreview.prepend(text);else this.stylePreview.append(text);}
    }
  }
  changeDisplay(mode){
    if(mode==='hidden'&&['more','submit'].includes(this.selectedId))return;
    const display={...this.displayDraft,[this.selectedId]:mode};
    try{this.toolbar.save(this.draft,display);this.status.textContent='';}
    catch(error){this.status.textContent=this.toolbar.t(error.message);}
  }
  applyPreset(id){
    const single=id==='single';
    const question=single?'恢复单栏默认布局和显示方式？':'恢复默认工具栏布局和显示方式？';
    if(!window.confirm(this.toolbar.t(question)))return;
    this.cancelDrag();
    try{
      this.toolbar.preferences.applyPreset(id);
      this.status.textContent=this.toolbar.t(single?'已应用单栏布局':'已恢复默认工具栏');
    }catch(error){this.status.textContent=this.toolbar.t(error.message);}
  }
  zoneOf(id){return composerZones.find(key=>this.draft[key].includes(id));}
  relocate(id,key,index){
    if(id==='more'&&key==='more')return;
    this.selectedId=id;
    try{
      this.toolbar.preferences.moveTool(id,key,index);
      this.dialog.querySelector('[data-drag-tool='+id+']')?.focus({preventScroll:true});
      this.status.textContent=this.toolbar.t('已移动到')+' '+this.toolbar.t(this.groupNames[key]);
    }catch(error){this.status.textContent=this.toolbar.t(error.message);}
  }
  press(event){
    const button=event.target.closest('[data-drag-tool]');if(!button||event.button!==0)return;
    this.cancelDrag();const press={id:button.dataset.dragTool,button,pointer:event.pointerId,x:event.clientX,y:event.clientY,isTouch:!!event.isTouch};this.pending=press;
    if(!event.isTouch)this.dialog.setPointerCapture(event.pointerId);
    press.timer=setTimeout(()=>this.startDrag(press),320);
  }
  startDrag(press){
    if(this.pending!==press)return;
    clearTimeout(press.timer);this.pending=null;this.dialog.style.height=this.dialog.getBoundingClientRect().height+'px';this.drag={id:press.id,pointer:press.pointer,x:press.x,y:press.y,target:null};press.button.classList.add('toolbar-lifted');this.dialog.classList.add('toolbar-editing-drag');
    this.ghost=press.button.cloneNode(true);this.ghost.removeAttribute('data-drag-tool');this.ghost.removeAttribute('aria-label');this.ghost.setAttribute('aria-hidden','true');this.ghost.className='toolbar-drag-ghost';this.ghost.disabled=true;this.dialog.append(this.ghost);
    this.marker=this.toolbar.node('span','toolbar-drop-marker');this.status.textContent=this.toolbar.t('拖到目标位置后松开；Esc 取消移动。');this.paint();
  }
  move(event){
    if(this.pending&&event.pointerId===this.pending.pointer&&Math.hypot(event.clientX-this.pending.x,event.clientY-this.pending.y)>9){if(this.pending.isTouch){this.cancelDrag();return;}this.startDrag(this.pending);}
    if(!this.drag||event.pointerId!==this.drag.pointer)return;
    event.preventDefault();this.drag.x=event.clientX;this.drag.y=event.clientY;
    if(!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=null;this.paint();});
  }
  paint(){
    const drag=this.drag;if(!drag)return;
    const bounds=this.dialog.getBoundingClientRect(),scroll=this.dialog.scrollTop;
    if(drag.y<bounds.top+70)this.dialog.scrollTop-=10;else if(drag.y>bounds.bottom-35)this.dialog.scrollTop+=10;
    if(this.dialog.scrollTop!==scroll&&!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=null;this.paint();});
    this.ghost.style.left=(drag.x-bounds.left+this.dialog.scrollLeft-30)+'px';this.ghost.style.top=(drag.y-bounds.top+this.dialog.scrollTop-60)+'px';
    const hit=document.elementFromPoint(drag.x,drag.y),group=hit?.closest('[data-drop-zone]');
    const target=group&&this.dialog.contains(group)&&!(drag.id==='more'&&group.dataset.dropZone==='more')?group:null;
    if(!target){this.clearTarget();return;}
    const siblings=Array.from(target.querySelectorAll('[data-drag-tool]')).filter(node=>node.dataset.dragTool!==drag.id);
    const before=siblings.find(node=>{const rect=node.getBoundingClientRect();return drag.y<rect.top-3||(drag.y<=rect.bottom+3&&drag.x<rect.left+rect.width/2);});
    const index=before?siblings.indexOf(before):siblings.length,key=target.dataset.dropZone;
    if(drag.target?.key===key&&drag.target.index===index)return;
    this.clearTarget();target.classList.add('toolbar-drop-over');target.insertBefore(this.marker,before||null);drag.target={key,index};
  }
  clearTarget(){this.marker?.remove();for(const group of Object.values(this.groups))group.classList.remove('toolbar-drop-over');if(this.drag)this.drag.target=null;}
  release(event){
    if(this.drag?.pointer===event.pointerId){
      this.drag.x=event.clientX;this.drag.y=event.clientY;this.paint();const {id,target}=this.drag;this.cancelDrag();if(target)this.relocate(id,target.key,target.index);
    }else if(this.pending?.pointer===event.pointerId){const id=this.pending.id;this.cancelDrag();this.selectTool(id);}
    if(!event.isTouch&&this.dialog.hasPointerCapture(event.pointerId))this.dialog.releasePointerCapture(event.pointerId);
  }
  cancelDrag(){
    this.dialog.classList.remove('toolbar-editing-drag');this.dialog.style.height='';
    if(this.pending)clearTimeout(this.pending.timer);this.pending=null;
    if(this.frame)cancelAnimationFrame(this.frame);this.frame=null;this.clearTarget();this.ghost?.remove();this.ghost=null;this.marker=null;this.drag=null;
    this.dialog.querySelector('.toolbar-lifted')?.classList.remove('toolbar-lifted');
  }
  keydown(event){
    const button=event.target.closest('[data-drag-tool]');if(!button||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Delete','Backspace'].includes(event.key))return;
    event.preventDefault();const id=button.dataset.dragTool,key=this.zoneOf(id),at=this.draft[key].indexOf(id);
    if(['Delete','Backspace'].includes(event.key))this.relocate(id,'more',this.draft.more.length);
    else if(event.key==='ArrowUp'||event.key==='ArrowDown')this.relocate(id,(event.key==='ArrowUp'?'top':'bottom')+(key.endsWith('Right')?'Right':'Left'),99);
    else if(event.shiftKey)this.relocate(id,(key.startsWith('bottom')?'bottom':'top')+(event.key==='ArrowLeft'?'Left':'Right'),99);
    else this.relocate(id,key,Math.max(0,Math.min(at+(event.key==='ArrowLeft'?-1:1),this.draft[key].length-1)));
  }
  dispose(){this.cancelDrag();document.removeEventListener('visibilitychange',this.onHide);this.dialog.remove();}
}
