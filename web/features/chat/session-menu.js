'use strict';

class CliConnectionsPanel {
  constructor({request,notify,translate=text=>text,identity=()=>''}){
    Object.assign(this,{request,notify,t:translate,identity});this.timer=null;
    document.addEventListener('bridge-logged-out',()=>this.close());
  }
  close(){clearTimeout(this.timer);this.dialog?.close();this.dialog?.remove();this.dialog=null;}
  node(tag,text,cls){const node=document.createElement(tag);node.textContent=text===undefined?'':this.t(text);if(cls)node.className=cls;return node;}
  open(target=null){
    this.close();this.target=target;this.scope=this.identity();this.selected=new Set();this.pendingId=null;
    const dialog=this.dialog=this.node('dialog',undefined,'picker');dialog.setAttribute('aria-label',this.t('CLI 连接管理'));
    dialog.addEventListener('close',()=>{clearTimeout(this.timer);dialog.remove();if(this.dialog===dialog)this.dialog=null;});
    document.body.append(dialog);dialog.showModal();this.refresh();
  }
  valid(dialog){return dialog===this.dialog&&dialog.open&&this.scope===this.identity();}
  async refresh(){
    const dialog=this.dialog;if(!dialog)return;clearTimeout(this.timer);
    try{
      const data=await this.request('/api/cli-connections');if(!this.valid(dialog))return;
      this.render(data);
      if(data.operations.some(job=>job.results.some(row=>['waiting','releasing'].includes(row.status))))
        this.timer=setTimeout(()=>{if(this.valid(dialog)&&!document.hidden)this.refresh();},5000);
    }catch(error){if(this.valid(dialog)){dialog.replaceChildren(this.node('p',error.message,'error'));this.button(dialog,'关闭',()=>this.close());}}
  }
  button(parent,label,run){const button=this.node('button',label,'plain');button.type='button';button.onclick=run;parent.append(button);return button;}
  render(data){
    const dialog=this.dialog;dialog.replaceChildren();
    const head=this.node('div',undefined,'picker-head');head.append(this.node('h2',this.target?'释放 CLI 连接':'CLI 连接管理'));this.button(head,'关闭',()=>this.close());dialog.append(head);
    dialog.append(this.node('p','释放当前网关持有的连接，保留聊天历史。运行中的任务默认完成后释放。','appearance-note'));
    const match=row=>!this.target||(row.provider===this.target.provider&&row.sessionId===this.target.sessionId&&
      (row.provider!=='codex'||row.host===this.target.host));
    const rows=data.connections.filter(match),busy=new Set(data.operations.flatMap(job=>job.results.filter(row=>['waiting','releasing','unknown'].includes(row.status)).map(row=>row.key)));
    this.rows=rows;this.selected=new Set([...this.selected].filter(key=>rows.some(row=>row.key===key)&&!busy.has(key)));
    if(!rows.length)dialog.append(this.node('p','没有由网关持有的 CLI 连接'));
    for(const row of rows){
      const label=this.node('label',undefined,'display-option'),text=this.node('span');text.textContent=[row.provider==='codex'?'Codex':'Claude',row.title,row.location].filter(Boolean).join(' · ');
      const check=document.createElement('input');check.type='checkbox';check.disabled=busy.has(row.key);check.checked=this.target?!check.disabled:this.selected.has(row.key);
      if(check.checked)this.selected.add(row.key);check.onchange=()=>{if(check.checked)this.selected.add(row.key);else this.selected.delete(row.key);};label.append(text,check);dialog.append(label);
    }
    if(rows.length){
      if(!this.target)this.button(dialog,'选择全部',()=>{for(const row of rows)if(!busy.has(row.key))this.selected.add(row.key);this.render(data);});
      this.button(dialog,this.target?'释放连接':'释放所选',()=>this.submit('wait'));
      if(!this.target)this.button(dialog,'释放全部',()=>{this.selected=new Set(rows.filter(row=>!busy.has(row.key)).map(row=>row.key));this.submit('wait');});
      this.button(dialog,'停止并释放…',()=>{
        if(!rows.some(row=>this.selected.has(row.key)&&!busy.has(row.key)))return this.notify(this.t('请选择需要释放的 CLI 连接'));
        const title=this.node('h3','停止任务并释放连接？'),text=this.node('p','将停止所选会话正在运行的任务并释放连接，聊天历史会保留。');
        dialog.replaceChildren(title,text);this.button(dialog,'取消',()=>this.render(data));this.button(dialog,'确认停止并释放',()=>this.submit('stop'));
      });
    }
    const statuses={waiting:'等待任务完成',releasing:'正在释放',released:'连接已释放',detached:'网关连接已释放，原生占用待核对',unknown:'结果待核对'};
    for(const job of data.operations.slice(-10).reverse())for(const row of job.results.filter(match)){
      const line=this.node('p',undefined,'appearance-note');line.textContent=row.title+'：'+this.t(statuses[row.status]||row.status)+((row.error||row.notice)?' · '+this.t(row.error||row.notice):'');dialog.append(line);
    }
    this.button(dialog,'刷新',()=>this.refresh());
  }
  async submit(mode){
    const dialog=this.dialog,keys=this.rows.filter(row=>this.selected.has(row.key)).map(row=>row.key);
    if(!keys.length)return this.notify(this.t('请选择需要释放的 CLI 连接'));
    if(!this.valid(dialog))return;
    clearTimeout(this.timer);for(const b of dialog.querySelectorAll('button'))b.disabled=true;
    const body={id:crypto.randomUUID(),connections:keys,mode};
    try{await this.request('/api/cli-connections',body);if(this.valid(dialog)){this.selected.clear();this.notify(this.t('已提交释放请求'));await this.refresh();}}
    catch(error){if(this.valid(dialog)){this.notify(this.t(error.message));await this.refresh();}}
  }
}

class SessionMenu {
  constructor({translate=text=>text,notify=()=>{},newId=()=>crypto.randomUUID()}={}){
    this.t=translate;this.notify=notify;this.newId=newId;this.box=null;
    document.addEventListener('bridge-logged-out',()=>this.close({immediate:true}));
    document.addEventListener('visibilitychange',()=>{if(document.hidden)this.close({immediate:true});});
    window.addEventListener('resize',()=>this.position());
    window.visualViewport?.addEventListener('resize',()=>this.position());
  }
  node(tag,cls,text){const node=document.createElement(tag);node.className=cls||'';if(text!==undefined)node.textContent=text;return node;}
  icon(name){
    const paths={pin:'m8 3 8 0-1 6 3 3H6l3-3-1-6m4 9v8',pencil:'m4 16 12-12 4 4-12 12H4v-4m9-9 4 4',link:'m9 15 6-6m-7 3-2 2a4 4 0 0 0 6 6l3-3m-6-6 3-3a4 4 0 0 1 6 6l-2 2',folder:'M3 7V4h6l2 3h10v13H3V7',section:'M4 4h16v5H4zM4 14h16v6H4z',fork:'M6 3v12a5 5 0 0 0 5 5h7M6 8h7a5 5 0 0 0 5-5',list:'M8 5h12M8 12h12M8 19h12M3 5h1M3 12h1M3 19h1',info:'M12 8h.01M12 11v6M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',archive:'M3 4h18v4H3zM5 8v12h14V8M10 12h4',copy:'M8 8h12v12H8zM16 8V4H4v12h4',back:'m14 5-7 7 7 7',chevron:'m9 5 7 7-7 7',close:'m6 6 12 12M18 6 6 18'};
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[name]||paths.info);svg.append(path);return svg;
  }
  close({immediate=false}={}){
    this.finishClosing?.();
    const box=this.box;if(!box)return;
    this.box=null;
    const anchor=this.anchor;let timer,finished=false;
    const end=event=>{if(event.target===box&&!event.pseudoElement)finish();};
    const finish=()=>{
      if(finished)return;finished=true;clearTimeout(timer);box.removeEventListener('animationend',end);
      if(this.finishClosing===finish)this.finishClosing=null;
      box.close();box.remove();if(!this.box&&anchor?.element?.isConnected)anchor.element.focus();
    };
    if(immediate||!box.open||window.matchMedia('(prefers-reduced-motion: reduce)').matches){this.clearDrag?.();finish();return;}
    const visual=getComputedStyle(box);
    box.style.setProperty('--sm-exit-transform',visual.transform);box.style.setProperty('--sm-exit-opacity',visual.opacity);
    box.style.setProperty('--sm-exit-backdrop-opacity',getComputedStyle(box,'::backdrop').opacity);
    this.clearDrag?.();
    box.style.removeProperty('animation');
    box.inert=true;box.classList.add('sm-closing');this.finishClosing=finish;
    box.addEventListener('animationend',end);timer=setTimeout(finish,260);
  }
  back(){if(!this.box)return;if(this.view!=='main'){this.render();this.box.querySelector('.sm-row:not(:disabled),button')?.focus();}else this.close();}
  bindDrag(box){
    let drag=null,timer;
    const reset=()=>{clearTimeout(timer);drag=null;box.classList.remove('sm-dragging','sm-returning');box.style.removeProperty('transform');};
    this.clearDrag=reset;
    box.addEventListener('pointerdown',event=>{
      if(event.pointerType==='mouse'||event.button!==0||!matchMedia('(max-width: 640px)').matches||!event.target.closest('.sm-handle,.sm-head,.sm-heading')||event.target.closest('button,input,a'))return;
      reset();drag={id:event.pointerId,x:event.clientX,y:event.clientY,distance:0};
      try{box.setPointerCapture(event.pointerId);}catch(_){/* Synthetic pointers do not own capture. */}
    });
    box.addEventListener('pointermove',event=>{
      if(!drag||drag.id!==event.pointerId)return;
      const y=event.clientY-drag.y,x=event.clientX-drag.x;
      if(!drag.distance&&Math.abs(x)>Math.max(8,Math.abs(y))){reset();return;}
      if(y<=8&&!drag.distance)return;
      drag.distance=Math.max(0,y);box.style.animation='none';box.classList.remove('sm-returning');box.classList.add('sm-dragging');box.style.transform=`translateY(${drag.distance}px)`;
    });
    const end=event=>{
      if(!drag||drag.id!==event.pointerId)return;
      const distance=drag.distance;drag=null;
      if(event.type==='pointerup'&&distance>=Math.min(96,Math.max(48,box.offsetHeight*.2))){this.close();return;}
      if(!distance){reset();return;}
      box.classList.add('sm-returning');box.classList.remove('sm-dragging');box.style.transform='translateY(0)';timer=setTimeout(reset,180);
    };
    box.addEventListener('pointerup',end);box.addEventListener('pointercancel',end);box.addEventListener('lostpointercapture',end);
    box.addEventListener('click',event=>{if(box.classList.contains('sm-returning')){event.preventDefault();event.stopPropagation();}},true);
  }
  show(context,anchor){
    this.close({immediate:true});this.context=context;this.anchor=anchor;this.busy=false;
    const box=this.node('dialog','session-menu');this.box=box;box.setAttribute('aria-label',this.t('会话菜单'));
    box.addEventListener('close',()=>{box.remove();if(this.box===box){this.box=null;if(anchor?.element?.isConnected)anchor.element.focus();}});
    box.addEventListener('cancel',event=>{event.preventDefault();this.back();});
    this.bindDrag(box);
    box.addEventListener('keydown',event=>{
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)||event.target.matches('input,textarea'))return;
      const items=[...box.querySelectorAll('button:not(:disabled)')],index=items.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
      items[next]?.focus();event.preventDefault();
    });
    document.body.append(box);this.render();box.showModal();this.position();
    box.querySelector('.sm-row:not(:disabled),button')?.focus();return box;
  }
  position(){
    if(!this.box?.open)return;
    const box=this.box;box.style.left='';box.style.top='';
    if(window.matchMedia('(max-width: 640px)').matches)return;
    const viewport=window.visualViewport,width=viewport?.width||window.innerWidth,height=viewport?.height||window.innerHeight;
    const rect=this.anchor?.element?.getBoundingClientRect(),x=this.anchor?.x??rect?.left??(width-box.offsetWidth)/2,y=this.anchor?.y??rect?.bottom??32;
    box.style.left=Math.max(8,Math.min(x,width-box.offsetWidth-8))+'px';box.style.top=Math.max(8,Math.min(y+4,height-box.offsetHeight-8))+'px';
  }
  row(label,icon,run,{end,disabled=false,danger=false}={}){
    const button=this.node('button','sm-row'+(danger?' sm-danger':''));button.type='button';button.disabled=disabled;
    button.append(this.icon(icon),this.node('span','',this.t(label)));if(end)button.append(this.icon('chevron'));
    button.onclick=()=>run(button);this.box.append(button);return button;
  }
  separator(){this.box.append(this.node('div','sm-divider'));}
  valid(){if(this.context.isCurrent&&!this.context.isCurrent())throw Error(this.t('连接或账号已变化，请重新打开会话菜单'));}
  async execute(run,button,{close=true}={}){
    if(this.busy)return;const box=this.box;this.busy=true;if(button)button.disabled=true;
    try{this.valid();await run(button);if(this.box===box){if(close)this.close();else this.render();}}
    catch(error){if(box.isConnected){const message=box.querySelector('.sm-error')||this.node('p','sm-error');message.setAttribute('role','alert');message.textContent=this.t(error.message);box.append(message);this.position();}else this.notify(this.t(error.message));}
    finally{if(this.box===box){this.busy=false;if(button?.isConnected)button.disabled=false;}}
  }
  render(view='main'){
    const c=this.context,box=this.box;this.clearDrag?.();this.view=view;box.replaceChildren();box.append(this.node('div','sm-handle'));
    if(view!=='main')this.row('返回会话菜单','back',()=>this.back());
    else{
      const head=this.node('div','sm-head'),line=this.node('div','sm-name'),title=this.node('strong','',c.title),close=this.node('button','sm-icon');
      close.type='button';close.setAttribute('aria-label',this.t('关闭'));close.append(this.icon('close'));close.onclick=()=>this.close();
      line.append(title);
      if(c.rename){const edit=this.node('button','sm-icon');edit.type='button';edit.setAttribute('aria-label',this.t('修改聊天名称'));edit.append(this.icon('pencil'));edit.onclick=()=>this.render('rename');line.append(edit);}
      head.append(line,this.node('small','',c.subtitle||''),close);box.append(head);
    }
    if(view==='rename'){
      if(c.renameScope==='bridge')box.append(this.node('p','sm-explanation',this.t('仅在 Bridge 中生效')));
      const form=this.node('form','sm-form'),input=this.node('input');input.value=c.title;input.maxLength=120;input.required=true;input.setAttribute('aria-label',this.t('聊天名称'));
      const save=this.node('button','primary',this.t('保存'));save.type='submit';form.append(input,save);box.append(form);
      form.onsubmit=event=>{event.preventDefault();const title=input.value.trim();if(title)this.execute(async()=>{await c.rename(title);c.title=title;},save,{close:false});};
      input.focus();input.select();
    }else if(view==='details'){
      const details=this.node('dl','sm-details');
      for(const [label,value,copy] of c.details||[]){if(value===undefined||value===null||value==='')continue;const line=this.node('div'),dd=this.node('dd','',String(value));line.append(this.node('dt','',this.t(label)),dd);
        if(copy){const button=this.node('button','sm-icon message-copy');button.type='button';button.setAttribute('aria-label',this.t('复制')+' '+this.t(label));button.append(this.icon('copy'));button.onclick=()=>this.copy(String(value),button);line.append(button);}details.append(line);}
      box.append(details);if(c.source)this.row('来源会话：'+c.source.title,'link',()=>{this.close();c.source.open();});
    }else if(view==='project'||view==='section'){
      box.append(this.node('h3','sm-heading',this.t(view==='project'?'项目':'分区')));
      this.loadOrganization(view,c,box);
    }else if(view==='delete'){
      box.append(this.node('h3','sm-heading',this.t('永久删除聊天？')),this.node('p','sm-explanation',c.subtitle||''));
      this.loadDeletion(c,box);
    }else if(view==='archive'){
      box.append(this.node('h3','sm-heading',this.t('停止任务并归档？')),this.node('p','sm-explanation',c.title),this.node('p','sm-explanation',c.subtitle||''),this.node('p','sm-explanation',this.t('将停止此聊天及其子任务，并在确认停止后归档。聊天记录会保留，未完成任务需要手动继续。')));
      const actions=this.node('div','sm-confirm-actions'),confirm=this.node('button','primary',this.t('确认归档')),cancel=this.node('button','primary',this.t('取消'));
      confirm.type=cancel.type='button';confirm.onclick=()=>this.execute(c.archive,confirm);cancel.onclick=()=>this.render();
      actions.append(confirm,cancel);box.append(actions);
      queueMicrotask(()=>cancel.focus());
    }else{
      this.row(c.pinned?'取消置顶':'置顶会话','pin',button=>this.execute(async()=>{await c.pin(!c.pinned);c.pinned=!c.pinned;},button,{close:false}));
      if(c.nativeOrganization){this.row('项目','folder',()=>this.render('project'),{end:true});this.row('分区','section',()=>this.render('section'),{end:true});}
      this.separator();
      // Native shells authorize copying in the capture phase of the first click.
      if(c.link)this.row(c.linkLabel||'复制深度链接','link',button=>this.copy(c.link,button)).classList.add('message-copy');
      else if(c.sessionId)this.row('复制 Session ID','copy',button=>this.copy(c.sessionId,button)).classList.add('message-copy');
      if(c.fork)this.row('创建聊天分支','fork',button=>this.execute(()=>{this.close();return c.fork();},button));
      this.separator();if(c.manage)this.row('发送管理','list',()=>{this.close();c.manage();});
      if(c.notifications)this.row('聊天通知设置','info',button=>this.execute(()=>{this.close();return c.notifications();},button));
      if(c.cliNetwork)this.row('检查 CLI 网络','link',c.cliNetwork);
      if(c.releaseCli)this.row('释放 CLI 连接','link',()=>{this.close();c.releaseCli();});
      this.row('会话详情','info',()=>this.render('details'),{end:true});
      if(c.archive){this.separator();this.row(c.archived?'恢复会话':'停止任务并归档…','archive',button=>c.archived?this.execute(c.archive,button):this.render('archive'),{danger:!c.archived});}
      if(c.deletion)this.row('永久删除…','close',()=>this.render('delete'),{danger:true});
    }
    this.position();
  }
  async loadDeletion(context,box){
    const pending=this.node('p','sm-explanation',this.t('正在核对删除范围…'));box.append(pending);
    const active=()=>this.box===box&&this.context===context&&this.view==='delete'&&pending.isConnected;
    try{
      this.valid();const snapshot=await context.deletion();
      if(!active())return;this.valid();pending.remove();
      box.append(this.node('p','sm-explanation',this.t('以下聊天及其子任务的历史与原生关联内容将永久删除，无法撤销。正在运行的任务须先停止。')));
      const list=this.node('ul','sm-explanation');for(const row of snapshot.sessions)list.append(this.node('li','',row.title));box.append(list);
      if(snapshot.subagentCount)box.append(this.node('p','sm-explanation',this.t('包含子任务：')+snapshot.subagentCount));
      const actions=this.node('div','sm-confirm-actions'),confirm=this.node('button','primary',this.t('确认永久删除')),cancel=this.node('button','primary',this.t('取消'));
      confirm.type=cancel.type='button';confirm.onclick=()=>this.execute(()=>context.deletion(snapshot),confirm);cancel.onclick=()=>this.render();
      actions.append(confirm,cancel);box.append(actions);this.position();cancel.focus();
    }catch(error){
      if(!active())return;pending.remove();const message=this.node('p','sm-error',this.t(error.message));message.setAttribute('role','alert');box.append(message);this.position();
    }
  }
  async loadOrganization(view,context,box){
    const pending=this.node('p','sm-explanation',this.t('正在读取原生侧边栏…'));box.append(pending);
    const active=()=>this.box===box&&this.context===context&&this.view===view&&pending.isConnected;
    try{
      this.valid();const snapshot=await context.organization();
      if(!active())return;this.valid();pending.remove();
      const scope=snapshot[view+'Scope']||snapshot.scope||context.organizationScope||'native',local=scope==='bridge';
      if(local)box.append(this.node('p','sm-explanation',this.t('仅在 Bridge 中生效')));
      const field=view==='project'?'projectId':'sectionId',selected=snapshot[field];
      const choices=[{id:null,name:this.t(view==='project'?'移出项目':'移出分区')},...snapshot[view==='project'?'projects':'sections']];
      for(const choice of choices){
        const requestId=this.newId();
        const label=choice.title||choice.name;
        const button=this.row(label,view==='project'?'folder':'section',button=>this.execute(async()=>{
          const result=await context.organization({id:requestId,kind:view,targetId:choice.id,expectedId:selected});
          if(result.status!=='confirmed')throw Error(this.t('分类结果待核对，请刷新会话列表'));
          this.notify(this.t(local?'已更新 Bridge 分类':'已同步到原生侧边栏'));
        },button),{disabled:choice.id===selected});
        button.querySelector('span').textContent=label;
        button.setAttribute('aria-pressed',String(choice.id===selected));
        if(choice.id===selected)button.append(this.node('span','sm-selected','✓'));
      }
      if(local){
        const form=this.node('form','sm-form'),input=this.node('input'),add=this.node('button','primary',this.t('添加'));
        const label=this.t(view==='project'?'新项目名称':'新分区名称');input.maxLength=120;input.required=true;input.placeholder=label;input.setAttribute('aria-label',label);add.type='submit';form.append(input,add);box.append(form);
        let request=null;
        form.onsubmit=event=>{event.preventDefault();const title=input.value.trim();if(!title)return;
          if(!request||request.title!==title)request={id:this.newId(),kind:'create-'+view,title};
          this.execute(async()=>{const result=await context.organization(request);if(result.status!=='confirmed')throw Error(this.t('分类结果待核对，请刷新会话列表'));this.notify(this.t('已更新 Bridge 分类'));},add);
        };
      }else if(choices.length===1)box.append(this.node('p','sm-explanation',this.t(view==='project'?'暂无原生项目，请先在原客户端中创建':'暂无原生分区，请先在原客户端中创建')));
      this.position();
    }catch(error){
      if(!active())return;pending.remove();const message=this.node('p','sm-error',this.t(error.message));message.setAttribute('role','alert');box.append(message);this.position();
    }
  }
  copy(text,button){
    const box=this.box;
    const copied=message=>{if(box&&this.box===box)this.close({immediate:true});this.notify(message);};
    try{this.valid();return Promise.resolve(window.BridgeClipboard.copyWithFeedback(()=>text,button,copied)).catch(error=>this.notify(this.t(error.message)));}
    catch(error){this.notify(this.t(error.message));}
  }
  bind(element,context){
    let timer=null,start=null,opened=false,releaseTimer=null;
    const clear=()=>{clearTimeout(timer);timer=null;start=null;};
    const show=anchor=>this.show(context(),{element,...anchor});
    element.addEventListener('contextmenu',event=>{
      event.preventDefault();const touch=event.pointerType==='touch'||!!start;clear();
      if(!opened){opened=touch;show(touch?{}:{x:event.clientX,y:event.clientY});}
    });
    element.addEventListener('pointerdown',event=>{clear();if(event.pointerType==='mouse'||event.button>0)return;clearTimeout(releaseTimer);opened=false;start={x:event.clientX,y:event.clientY};timer=setTimeout(()=>{if(element.isConnected){opened=true;show();}},550);});
    element.addEventListener('pointermove',event=>{if(start&&Math.hypot(event.clientX-start.x,event.clientY-start.y)>10)clear();});
    for(const name of ['pointerup','pointercancel','pointerleave','lostpointercapture'])element.addEventListener(name,()=>{clear();if(opened)releaseTimer=setTimeout(()=>{opened=false;},800);});
    element.addEventListener('click',event=>{if(opened){event.preventDefault();event.stopImmediatePropagation();opened=false;}},true);
    element.addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){event.preventDefault();show();}});
  }
}
if(typeof module!=='undefined')module.exports={SessionMenu};
