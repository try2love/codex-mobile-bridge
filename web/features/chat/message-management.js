'use strict';

// Presentation only. Callbacks retain host routing, durable receipts and draft storage.
class MessageManagement {
  constructor(options){
    Object.assign(this,{t:text=>text,uuid:()=>crypto.randomUUID(),localRecords:()=>[],loadEditor:()=>null,saveEditor:()=>{},beforeOpen:()=>{},onChanged:()=>{},confirm:message=>window.confirm(message),...options});
    this.doc=options.document||document;this.generation=0;this.busy=false;this.drag=null;this.editor=null;
    this.dialog=this.node('dialog','message-management picker');this.dialog.setAttribute('aria-labelledby','message-management-title');
    const head=this.node('div','picker-head'),title=this.node('h2');title.id='message-management-title';title.textContent=this.t('消息管理');
    this.closeButton=this.button('关闭',()=>this.dialog.close());head.append(title,this.closeButton);
    this.status=this.node('p','message-management-status');this.status.setAttribute('role','status');
    this.content=this.node('div','message-management-content');this.content.tabIndex=-1;this.content.addEventListener('scroll',()=>{this.scrollEpoch=(this.scrollEpoch||0)+1;this.scrollingUntil=Date.now()+500;},{passive:true});
    this.refreshButton=this.button('刷新',()=>this.refresh());
    const headerActions=this.node('div','message-management-header-actions');headerActions.append(this.refreshButton,this.closeButton);head.append(headerActions);
    this.dialog.append(head,this.status,this.content);this.doc.body.append(this.dialog);
    this.dialog.addEventListener('cancel',event=>{if(this.busy||this.drag)event.preventDefault();});
    this.dialog.addEventListener('close',()=>{this.generation++;this.stopNativeRefresh();this.cancelDrag();this.opener?.focus({preventScroll:true});});
  }
  node(tag,className=''){const node=this.doc.createElement(tag);node.className=className;return node;}
  button(label,action){const button=this.node('button');button.type='button';button.textContent=this.t(label);button.addEventListener('click',action);return button;}
  cloneValue(value){if(typeof structuredClone==='function')return structuredClone(value);return value===undefined?undefined:JSON.parse(JSON.stringify(value));}
  current(token){return this.dialog.open&&this.generation===token;}
  async open(){
    if(this.busy)return;this.target=this.cloneValue(typeof this.scope==='function'?this.scope():this.scope);if(!this.target)return;
    const token=++this.generation;
    this.data=null;this.local=[];this.content.replaceChildren();
    this.opener=this.doc.activeElement;this.beforeOpen();await Promise.resolve(this.editorSave).catch(()=>{});this.editor=await this.loadEditor(this.target);this.persistedEditor=this.cloneValue(this.editor);if(token!==this.generation)return;
    this.dialog.showModal();this.generation++;await this.refresh();
  }
  locked(){return this.busy||!!this.drag||!!this.editor||Date.now()<(this.scrollingUntil||0);}
  stopNativeRefresh(){clearInterval(this.nativeTimer);this.nativeTimer=null;}
  async refresh(quiet=false){
    if(this.busy||this.drag)return;
    if(quiet&&(this.refreshing||this.locked()||this.doc.hidden||!this.dialog.open))return;
    const token=this.generation,request=this.refreshGeneration=(this.refreshGeneration||0)+1,editor=this.editor,scroll=this.scrollEpoch||0;const valid=()=>this.current(token)&&request===this.refreshGeneration&&!this.busy&&!this.drag&&this.editor===editor&&(this.scrollEpoch||0)===scroll;
    this.refreshing=request;if(!quiet)this.status.textContent=this.t('正在读取消息…');
    try{
      const records=await this.localRecords(this.target),ids=records.map(r=>r.id);if(this.editor?.id)ids.push(this.editor.id);
      const data=await this.load(this.target,{ids:[...new Set(ids)]});if(!valid())return;
      const remaining=await this.localRecords(this.target);if(!valid())return;
      this.status.textContent='';
      if(data.nativeQueue===undefined)this.stopNativeRefresh();
      else if(!this.nativeTimer)this.nativeTimer=setInterval(()=>this.refresh(true),3000);
      if(quiet&&JSON.stringify([data,remaining])===JSON.stringify([this.data,this.local]))return;
      this.data=data;this.local=remaining;
      if(this.editor?.status==='unknown'&&this.editApplied(data,this.editor)){await this.persistEditor(null);this.editor=null;}
      if(this.editor?.operation==='edit'&&this.editor.status==='editing'){
        const row=(data.queue||[]).find(row=>row.id===this.editor.sourceId);
        const blockedReason=!row?'notQueued':row.text!==this.editor.originalText?'conflict':null;
        const baseVersion=blockedReason?this.editor.baseVersion:data.version;
        if(blockedReason!==this.editor.blockedReason||baseVersion!==this.editor.baseVersion){const updated={...this.editor,blockedReason,baseVersion};await this.persistEditor(updated);this.editor=updated;}
      }
      this.render();
    }catch(error){if(valid())this.status.textContent=error.message||String(error);}
    finally{if(this.refreshing===request)this.refreshing=null;}
  }
  // Background state must not replace an actively edited list or its scroll position.
  update(){if(this.dialog.open&&!this.locked())return this.refresh();}
  persistEditor(value){const target=this.cloneValue(this.target),snapshot=this.cloneValue(value);this.editorSave=Promise.resolve(this.editorSave).catch(()=>{}).then(async()=>{await this.saveEditor(target,snapshot,this.persistedEditor??null);this.persistedEditor=this.cloneValue(snapshot);});return this.editorSave;}
  async action(callback){
    if(this.busy||this.drag)return;this.busy=true;this.closeButton.disabled=true;this.refreshButton.disabled=true;this.content.inert=true;this.status.textContent='';
    try{await callback();return true;}catch(error){this.status.textContent=error.message||String(error);return false;}
    finally{this.busy=false;this.closeButton.disabled=false;this.refreshButton.disabled=false;this.content.inert=false;}
  }
  render(){
    const scroll=this.content.scrollTop;this.content.replaceChildren();
    const native=this.data.nativeQueue,showNative=native===null||!!native?.length;
    this.queue=this.data.queue||[];const queueSection=this.section(showNative?'网关待发送队列':'待发送队列');this.list=this.node('ol','message-management-queue');queueSection.append(this.list);
    if(!this.queue.length)this.empty(queueSection,'当前没有待发送消息');
    const canReorder=this.data.capabilities?.reorder!==false;
    for(const [index,row] of this.queue.entries()){
      const item=this.node('li','message-management-row');item.dataset.id=row.id;
      const handle=this.button('拖动排序',()=>{});handle.className='message-management-handle';handle.textContent='⠿';handle.setAttribute('aria-label',this.t('拖动排序'));handle.disabled=!canReorder||!!this.editor;
      handle.addEventListener('pointerdown',event=>this.startDrag(event,row.id));
      const copy=this.node('div','message-management-copy'),text=this.node('div','message-management-text');text.textContent=row.text||'';copy.append(text);
      if(row.attachments?.length){const files=this.node('small');files.textContent=row.attachments.map(f=>typeof f==='string'?f:f.name).join(' · ');copy.append(files);}
      const controls=this.node('div','message-management-actions');
      if(canReorder){const up=this.button('上移',()=>this.move(row.id,-1)),down=this.button('下移',()=>this.move(row.id,1));up.disabled=!!this.editor||index===0;down.disabled=!!this.editor||index===this.queue.length-1;controls.append(up,down);}
      if(this.data.capabilities?.edit!==false)controls.append(this.button('编辑',()=>this.editRow(row)));
      if(this.data.capabilities?.withdraw!==false)controls.append(this.button('撤回',()=>this.withdrawRow(row)));
      item.append(handle,copy,controls);this.list.append(item);
    }
    if((this.data.reason||this.data.capabilities?.reason)){const reason=this.node('p','muted');reason.textContent=this.t(this.data.reason||this.data.capabilities.reason);queueSection.append(reason);}
    if(showNative){
      const section=this.section('Codex 中待发送');
      if(native===null)this.empty(section,'暂时无法读取 Codex 的待发送消息。');
      else{
        this.empty(section,'这些消息由 Codex 发送。请在电脑上的 Codex 中编辑、排序或撤回。');
        const list=this.node('ol','message-management-queue');section.append(list);
        for(const row of native){
          const item=this.node('li','message-management-row'),copy=this.node('div','message-management-copy'),text=this.node('div','message-management-text');
          text.textContent=row.text;copy.append(text);
          if(row.attachments?.length){const names=this.node('small');names.textContent=row.attachments.join(' · ');copy.append(names);}
          item.append(copy);list.append(item);
        }
      }
    }
    const unknown=this.section('发送结果未确认');
    const server=this.data.unknown||[],serverIds=new Set(server.map(row=>row.id));
    for(const row of server)this.pendingRow(unknown,row,(this.local||[]).some(local=>local.id===row.id)?'服务端及此设备记录':'服务端记录',()=>this.ignoreServer(row));
    const groups=new Map();for(const row of this.local||[]){if(serverIds.has(row.id))continue;const key=JSON.stringify([row.id,row.fingerprint||row.body]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
    for(const records of groups.values())this.pendingRow(unknown,records[0],'此设备记录',()=>this.ignoreBrowser(records));
    if(!server.length&&!(this.local||[]).length)this.empty(unknown,'当前没有未确认消息');
    if(this.editor)this.renderEditor();this.content.scrollTop=scroll;
  }
  section(label){const section=this.node('section'),heading=this.node('h3');heading.textContent=this.t(label);section.append(heading);this.content.append(section);return section;}
  empty(section,label){const text=this.node('p','muted');text.textContent=this.t(label);section.append(text);}
  pendingRow(section,row,source,ignore){
    const item=this.node('div','message-management-pending'),label=this.node('small'),text=this.node('div','message-management-text');
    label.textContent=this.t(source);text.textContent=row.text??row.body?.text??'';
    const help=this.node('p','muted');help.textContent=this.t('消息可能已经送达。忽略仅移除此记录，不会撤销或重新发送消息。');
    item.append(label,text,help,this.button('忽略此记录',ignore));section.append(item);
  }
  async ignoreServer(row){if(!await this.confirm(this.t('消息可能已经送达。忽略仅移除此记录，不会撤销或重新发送消息。')))return;const ok=await this.action(async()=>{await this.ignore(this.target,{version:this.data.version,expectedAccount:this.data.draftAccountIdentity,id:row.id});this.onChanged(this.target);});if(ok)await this.refresh();}
  async ignoreBrowser(records){if(!await this.confirm(this.t('消息可能已经送达。忽略仅移除此记录，不会撤销或重新发送消息。')))return;const ok=await this.action(async()=>{for(const row of records)await this.ignoreLocal(this.target,row);});if(ok)await this.refresh();}
  async withdrawRow(row){
    const ok=await this.action(async()=>{await this.withdraw(this.target,{version:this.data.version,expectedAccount:this.data.draftAccountIdentity,id:row.id});this.onChanged(this.target);});if(ok)await this.refresh();
  }
  async editRow(row){
    if(this.editor){this.status.textContent=this.t('请先完成或放弃当前编辑');return;}
    const ok=await this.action(async()=>{
      const editor={id:row.id,sourceId:row.id,baseVersion:this.data.version,originalText:row.text||'',body:this.cloneValue(row),status:'editing',operation:'edit'};
      await this.persistEditor(editor);this.editor=editor;
    });if(ok){this.render();this.editInput?.focus({preventScroll:true});}
  }
  editApplied(data,editor){
    if(editor?.operation!=='edit'||!editor.attempt)return false;
    const matches=row=>row.id===editor.sourceId&&row.editId===editor.attempt&&row.text===editor.body.text;
    return (data.queue||[]).some(matches)||(data.receipts||[]).some(row=>row.id===editor.sourceId&&row.edit?.status==='applied'&&row.edit.id===editor.attempt&&row.edit.text===editor.body.text);
  }
  renderEditor(){
    const section=this.section('编辑待发送消息'),help=this.node('p','muted');help.textContent=this.t('保存后原位更新消息，原附件、Skill 和发送设置保持不变。');section.append(help);
    if(this.editor.status==='unknown'||this.editor.operation!=='edit'){
      this.empty(section,'这次保存的结果尚未确认，请刷新检查；不会自动再次保存。');
      const text=this.node('div','message-management-text');text.textContent=this.editor.body.text;section.append(text,this.button('结束编辑',()=>this.endUnknownEditor()));return;
    }
    const unavailable=!!this.editor.blockedReason;
    if(this.editor.blockedReason==='notQueued')this.empty(section,'这条消息已开始发送或不在队列中，编辑内容已保留。');
    else if(this.editor.blockedReason==='conflict')this.empty(section,'原消息已在其他页面修改，编辑内容已保留，请结束编辑后重新选择消息。');
    else if(unavailable)this.empty(section,'请刷新消息队列后再保存，编辑内容已保留。');
    const names=this.editor.body.attachmentNames;if(this.editor.body.attachments?.length){const files=this.node('small');files.textContent=names?.length?names.join(' · '):this.t('已保留原附件');section.append(files);}
    this.editInput=this.node('textarea');this.editInput.value=this.editor.body.text;this.editInput.setAttribute('aria-label',this.t('编辑待发送消息'));this.editInput.maxLength=100000;
    this.editInput.addEventListener('input',()=>{this.editor.body.text=this.editInput.value;this.persistEditor(this.editor).catch(error=>{this.status.textContent=error.message||String(error);});});
    const save=this.button('保存',()=>this.submitEditor());save.disabled=unavailable;
    section.append(this.editInput,save,this.button('放弃编辑',()=>this.discardEditor()));
  }
  async endUnknownEditor(){
    if(!this.editor||(this.editor.status!=='unknown'&&this.editor.operation==='edit')||!await this.confirm(this.t('消息可能已经送达。忽略仅移除此记录，不会撤销或重新发送消息。')))return;
    await this.discardEditor();
  }
  async discardEditor(){const ok=await this.action(async()=>{await this.persistEditor(null);this.editor=null;});if(ok)this.render();}
  async submitEditor(){
    if(!this.editor||this.editor.status!=='editing'||this.editor.operation!=='edit'||!this.editor.sourceId||!this.editor.baseVersion||this.editor.blockedReason)return;
    const ok=await this.action(async()=>{
      const claimed={...this.editor,status:'unknown',attempt:this.uuid()};await this.persistEditor(claimed);this.editor=claimed;
      try{
        const result=await this.edit(this.target,{id:claimed.sourceId,version:claimed.baseVersion,text:claimed.body.text,editId:claimed.attempt});
        if(!this.editApplied(result,claimed))throw Error(this.t('保存结果尚未确认，编辑内容已保留'));
        await this.persistEditor(null);this.editor=null;this.onChanged(this.target);
      }catch(error){
        if(error.status===400){const restored={...claimed,status:'editing',blockedReason:'refreshRequired'};delete restored.attempt;await this.persistEditor(restored);this.editor=restored;}
        throw error;
      }
    });if(ok)await this.refresh();else this.render();
  }
  async move(id,delta){if(this.busy||this.drag||this.editor)return;const ids=this.queue.map(row=>row.id),from=ids.indexOf(id),to=from+delta;if(from<0||to<0||to>=ids.length)return;ids.splice(to,0,ids.splice(from,1)[0]);await this.persistOrder(ids,id);}
  async persistOrder(ids,focusId){
    const prior=this.queue.slice(),rows=new Map(prior.map(row=>[row.id,row]));
    this.queue=ids.map(id=>rows.get(id));this.data={...this.data,queue:this.queue};this.render();
    const success=await this.action(async()=>{await this.reorder(this.target,{version:this.data.version,expectedAccount:this.data.draftAccountIdentity,ids});this.onChanged(this.target);return true;});
    if(!success){this.data={...this.data,queue:prior};this.render();}else await this.refresh();
    this.list?.querySelector(`[data-id="${CSS.escape(focusId)}"] .message-management-handle`)?.focus({preventScroll:true});
  }
  startDrag(event,id){
    if(event.button!==0||this.busy||this.editor||this.queue.length<2)return;
    event.preventDefault();const handle=event.currentTarget;this.drag={id,handle,ids:this.queue.map(row=>row.id),pointer:event.pointerId};handle.setPointerCapture(event.pointerId);handle.closest('li').classList.add('dragging');
    const move=event=>{if(!this.drag||event.pointerId!==this.drag.pointer)return;const rows=[...this.list.children],other=rows.find(row=>{const box=row.getBoundingClientRect();return event.clientY>=box.top&&event.clientY<box.bottom;});const item=rows.find(row=>row.dataset.id===id);if(other&&other!==item){const box=other.getBoundingClientRect();this.list.insertBefore(item,event.clientY<box.top+box.height/2?other:other.nextSibling);}this.content.scrollTop+=event.clientY<this.content.getBoundingClientRect().top+40?-12:event.clientY>this.content.getBoundingClientRect().bottom-40?12:0;};
    const end=event=>{if(!this.drag||event.pointerId!==this.drag.pointer)return;const ids=[...this.list.children].map(row=>row.dataset.id),before=this.drag.ids;this.cancelDrag();if(event.type==='pointerup'&&ids.join('|')!==before.join('|'))this.persistOrder(ids,id);else this.render();};
    this.drag.cleanup=()=>{this.doc.removeEventListener('pointermove',move);this.doc.removeEventListener('pointerup',end);this.doc.removeEventListener('pointercancel',end);handle.closest('li')?.classList.remove('dragging');if(handle.hasPointerCapture(event.pointerId))handle.releasePointerCapture(event.pointerId);};
    // Moving a row can release its pointer capture. Keep the gesture on the document.
    this.doc.addEventListener('pointermove',move);this.doc.addEventListener('pointerup',end);this.doc.addEventListener('pointercancel',end);
  }
  cancelDrag(){const drag=this.drag;this.drag=null;drag?.cleanup?.();}
  dispose(){this.generation++;this.stopNativeRefresh();this.cancelDrag();this.dialog.remove();}
}
if(typeof module!=='undefined')module.exports={MessageManagement};
