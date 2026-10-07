'use strict';

// A shared, gateway-owned ephemeral fork. Disposing a view never ends the chat.
class SideChatPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});this.creationId=uuid();
    const n=(...args)=>workbench.node(...args),b=(...args)=>workbench.button(...args);
    this.selectedSkills=new Map();
    this.head=n('div','wb-side-head');
    this.createButton=b(BridgeI18n.t('创建侧边聊天'),()=>this.create());this.createButton.className='primary';
    this.stopButton=b(BridgeI18n.t('■ 停止'),()=>this.action('stop'));this.stopButton.hidden=true;
    this.endButton=b(BridgeI18n.t('结束聊天'),async()=>{if(await this.end())this.load();});this.endButton.hidden=true;
    this.modelButton=b(BridgeI18n.t('模型与思考程度'),()=>this.modelSettings());
    this.skillsButton=b('Skill',()=>this.skills());this.skillsButton.className='tool-button';
    this.permissionsButton=b(BridgeI18n.t('权限'),()=>this.permissions());this.permissionsButton.className='tool-button';
    this.head.append(n('span','wb-side-caption',BridgeI18n.t('临时侧边聊天')),this.endButton);
    this.landing=n('div','wb-side-landing');
    this.help=n('p','wb-side-help',BridgeI18n.t('继承当前会话的上下文，单独讨论一个问题。主会话的任务不会被接续执行。'));
    this.landing.append(n('div','wb-side-symbol','⑂'),n('h2','',BridgeI18n.t('开启一段侧边聊天')),this.help,n('p','muted',BridgeI18n.t('可跨设备继续。结束聊天或重启网关后，临时内容会清除。')),this.createButton);
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    this.messages=n('div','wb-side-messages');this.messages.setAttribute('data-i18n-aria-label','侧边聊天消息');this.messages.setAttribute('aria-label',BridgeI18n.t('侧边聊天消息'));
    this.approvals=n('div','wb-side-approvals');this.queued=n('div','wb-side-queue');
    this.form=n('form','composer wb-side-composer');this.form.hidden=true;
    this.input=n('textarea');this.input.rows=1;this.input.maxLength=20000;this.input.setAttribute('data-i18n-placeholder','继续这条聊天…');this.input.placeholder=BridgeI18n.t('继续这条聊天…');this.input.setAttribute('data-i18n-aria-label','侧边聊天输入');this.input.setAttribute('aria-label',BridgeI18n.t('侧边聊天输入'));
    this.sendButton=n('button','primary',BridgeI18n.t('发送 ↑'));this.sendButton.type='submit';this.sendButton.disabled=true;
    const tools=n('div','composer-tools'),bottom=n('div','compose-bottom'),actions=n('div','compose-actions');
    this.modelButton.className='tool-button wb-side-model';
    this.toggle=b('',()=>this.collapse(!this.collapsed),BridgeI18n.t('收起输入区'));this.toggle.className='composer-toggle';this.toggle.setAttribute('aria-expanded','true');this.toggle.append(document.querySelector('#composer-toggle svg').cloneNode(true));
    this.stopButton.className='stop';tools.append(this.modelButton,this.skillsButton,this.permissionsButton,this.toggle);
    this.composerBody=n('div','wb-side-composer-body');this.skillPills=n('div','skill-pills');
    this.attachmentList=n('div','attachment-list');this.fileInput=n('input');this.fileInput.type='file';this.fileInput.multiple=true;this.fileInput.hidden=true;
    this.attachButton=b('',()=>{},BridgeI18n.t('添加文件或图片'));this.attachButton.className='attach-button';
    this.attachButton.append(document.querySelector('#attach-button svg').cloneNode(true));
    const select=(label,rows)=>{const control=n('select');control.dataset.i18nAriaLabel=label;control.setAttribute('aria-label',BridgeI18n.t(label));for(const [value,text] of rows){const option=n('option','',BridgeI18n.t(text));option.value=value;control.append(option);}return control;};
    this.sendMode=select('发送方式',[['send','发送新消息'],['queue','完成后发送'],['steer','补充当前任务']]);
    this.workMode=select('工作模式',[['default','普通模式'],['plan','计划模式']]);
    this.sendMode.onchange=()=>this.controls();this.workMode.onchange=()=>{this.modeSelected=true;this.controls();};
    actions.append(this.stopButton,this.sendButton);bottom.append(this.attachButton,this.fileInput,this.sendMode,this.workMode,actions);
    this.composerBody.append(this.skillPills,this.attachmentList,this.input,bottom);this.form.append(tools,this.composerBody);
    this.attachments=new ChatAttachments({root:this.attachmentList,button:this.attachButton,input:this.fileInput,paste:this.input,drop:this.form,
      onChange:()=>this.controls(),preview:(key,id)=>this.uploadUrl(key,id,'preview'),
      upload:(key,id,file)=>this.uploadFile(this.uploadUrl(key,id)+'&name='+encodeURIComponent(file.name),file),
      thumbnail:async(key,id,file)=>{const thumb=await this.attachments.thumbnailBlob(file);return this.uploadFile(this.uploadUrl(key,id,'thumb')+'&width='+thumb.width+'&height='+thumb.height,thumb.data);}});
    this.form.onsubmit=e=>{e.preventDefault();this.send();};
    this.input.oninput=()=>{this.resizeInput();this.controls();};
    tab.body.append(this.head,this.landing,this.status,this.messages,this.approvals,this.queued,this.form);this.controls();this.load();this.schedule();
  }
  resizeInput(){this.input.style.height='auto';this.input.style.height=Math.min(160,Math.max(48,this.input.scrollHeight))+'px';}
  collapse(value){this.collapsed=value;this.composerBody.hidden=value;this.form.classList.toggle('is-collapsed',value);this.toggle.setAttribute('aria-expanded',String(!value));const label=value?'展开输入区':'收起输入区';this.toggle.dataset.i18nTitle=label;this.toggle.dataset.i18nAriaLabel=label;this.toggle.title=BridgeI18n.t(label);this.toggle.setAttribute('aria-label',BridgeI18n.t(label));if(!value)this.resizeInput();}
  uploadUrl(child,id,operation=''){
    return '/api/sessions/'+this.session.id+'/uploads'+(operation?'/'+encodeURIComponent(id)+'/'+operation:'')+'?host='+encodeURIComponent(this.session.host)+'&side='+encodeURIComponent(child)+(operation?'':'&id='+encodeURIComponent(id));
  }
  async uploadFile(url,file){
    const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':file.type||'application/octet-stream','X-CSRF-Token':this.workbench.csrf()},body:file});
    const result=await response.json();if(!response.ok){if(response.status===401)this.workbench.onUnauthorized();const error=Error(result.error||BridgeI18n.t('上传失败'));error.status=response.status;throw error;}return result;
  }
  relabel(){
    this.input.setAttribute('data-i18n-placeholder','继续这条聊天…');this.input.placeholder=BridgeI18n.t('继续这条聊天…');
    this.input.setAttribute("data-i18n-aria-label",'侧边聊天输入');this.input.setAttribute('aria-label',BridgeI18n.t('侧边聊天输入'));
    this.messages.setAttribute("data-i18n-aria-label",'侧边聊天消息');this.messages.setAttribute('aria-label',BridgeI18n.t('侧边聊天消息'));
    if(this.state)this.apply(this.state);
  }
  url(){return '/api/sessions/'+this.session.id+'/side-chat?host='+encodeURIComponent(this.session.host);}
  request(body){return this.workbench.request(this.url(),body);}
  controls(){
    const created=!!this.state?.id,active=this.state?.status==='active',locked=!!this.busy||!!this.pending||!this.state?.connected;
    this.form.hidden=!created;this.landing.hidden=created;this.head.hidden=!created;this.messages.hidden=!created;this.queued.hidden=!created;
    if(this.attachments&&this.attachments.locked!==locked)this.attachments.setLocked(locked);
    for(const button of [this.modelButton,this.skillsButton,this.permissionsButton])button.disabled=locked;
    this.sendMode.disabled=locked;this.workMode.disabled=locked||this.sendMode.value==='steer';
    this.sendMode.querySelector('[value=steer]').disabled=!active;
    if(!active&&this.sendMode.value==='steer')this.sendMode.value='send';
    this.workMode.disabled=locked||this.sendMode.value==='steer';
    this.workMode.title=BridgeI18n.t(this.sendMode.value==='steer'?'补充内容沿用当前任务模式':this.workMode.value==='plan'?'先讨论并制定计划，确认后执行。':'');
    this.sendButton.disabled=locked||((active||!!this.state?.requests?.length)&&this.sendMode.value==='send')||(!!this.state?.requests?.length&&this.sendMode.value==='steer')||(!this.input.value.trim()&&!this.attachments?.ids().length)||!this.attachments?.ready();
    this.createButton.disabled=!!this.busy;
    this.stopButton.hidden=!created||!active;this.stopButton.disabled=!!this.busy;
    this.endButton.hidden=!created;this.endButton.disabled=!!this.busy;
    this.status.hidden=!this.status.textContent;
  }
  skillChips(){
    this.skillsButton.textContent='Skill'+(this.selectedSkills.size?' ('+this.selectedSkills.size+')':'');this.skillPills.replaceChildren();
    for(const [id,skill] of this.selectedSkills){const pill=this.workbench.raw(this.workbench.button((skill.displayName||skill.name)+' ×',()=>{this.selectedSkills.delete(id);this.skillChips();}));pill.className='skill-pill';this.skillPills.append(pill);}
  }
  dialog(title){
    const n=(...a)=>this.workbench.node(...a),dialog=n('dialog','picker'),head=n('div','picker-head');
    head.append(n('h2','',BridgeI18n.t(title)),this.workbench.button('×',()=>dialog.close(),BridgeI18n.t('关闭')));dialog.append(head);
    document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();return dialog;
  }
  async skills(){
    const child=this.state.id,n=(...a)=>this.workbench.node(...a),b=(...a)=>this.workbench.button(...a),dialog=this.dialog('选择 Skill');
    const search=n('input'),list=n('div','skill-list'),error=n('p','error'),actions=n('div','picker-actions');search.type='search';search.placeholder=BridgeI18n.t('搜索已安装 Skill');search.dataset.i18nPlaceholder='搜索已安装 Skill';search.setAttribute('aria-label',search.placeholder);search.dataset.i18nAriaLabel='搜索已安装 Skill';
    let revision=0,offset=0,total=0,loading=false,timer;
    const load=async(refresh=false,append=false)=>{
      const ticket=++revision;loading=true;more.disabled=true;error.textContent='';if(!append){offset=0;list.replaceChildren(n('p','muted',BridgeI18n.t('正在读取已安装 Skill…')));}
      try{
        const result=await this.request({action:'skills',id:child,query:search.value,offset,refresh,selected:[...this.selectedSkills.keys()]});
        if(ticket!==revision||!dialog.open||this.state?.id!==child)return;
        if(!append)list.replaceChildren();total=result.total||0;
        for(const skill of result.skills||[]){const label=n('label','skill-option'),input=n('input'),text=n('span');input.type='checkbox';input.checked=this.selectedSkills.has(skill.id);text.append(this.workbench.raw(n('strong','',skill.displayName||skill.name)),this.workbench.raw(n('small','',skill.description||'')));input.onchange=()=>{if(input.checked&&this.selectedSkills.size>=8){input.checked=false;error.textContent=BridgeI18n.t('最多选择 8 个 Skill');return;}if(input.checked)this.selectedSkills.set(skill.id,skill);else this.selectedSkills.delete(skill.id);this.skillChips();};label.append(input,text);list.append(label);}
        offset+=(result.skills||[]).length;more.hidden=offset>=total;error.textContent=(result.errors||[]).map(BridgeI18n.t).join('\n');
        if(!list.children.length)list.append(n('p','muted',BridgeI18n.t('没有匹配的 Skill')));
      }catch(e){if(ticket===revision){error.textContent=BridgeI18n.t(e.message);if(!append)list.replaceChildren();}}
      finally{if(ticket===revision){loading=false;more.disabled=false;}}
    };
    const more=b(BridgeI18n.t('加载更多'),()=>{if(!loading)load(false,true);});more.hidden=true;
    actions.append(b(BridgeI18n.t('刷新'),()=>load(true)),b(BridgeI18n.t('完成'),()=>dialog.close()));
    dialog.append(n('p','muted',BridgeI18n.t('选择后随下一条消息一起发送，最多 8 个。')),search,list,more,error,actions);
    search.oninput=()=>{revision++;clearTimeout(timer);timer=setTimeout(()=>load(),200);};dialog.addEventListener('close',()=>{revision++;clearTimeout(timer);});await load();
  }
  permissions(){
    const child=this.state.id,n=(...a)=>this.workbench.node(...a),dialog=this.dialog('会话权限'),error=n('p','error');
    dialog.append(n('p','muted',BridgeI18n.t('仅影响此侧边聊天，从下一轮生效。')));
    for(const [preset,label,help] of [['ask','请求批准','需要额外权限时询问你。'],['auto-review','帮我批准','由 Codex 审核需要额外权限的操作。'],['full-access','完全访问权限','允许访问工作区外的文件和网络，无需逐次批准。']]){
      const button=this.workbench.button('',async()=>{
        if(preset==='full-access'&&!confirm(BridgeI18n.t('允许此会话完全访问电脑文件和网络？请仅在信任任务内容时开启。')))return;
        dialog.querySelectorAll('button').forEach(b=>b.disabled=true);
        try{const state=await this.request({action:'permissions',id:child,preset,confirmed:preset==='full-access'});if(this.state?.id===child)this.apply(state);dialog.close();}
        catch(e){error.textContent=BridgeI18n.t(e.message);}finally{dialog.querySelectorAll('button').forEach(b=>b.disabled=false);}
      },BridgeI18n.t(label));permissionOption(button,preset,label,help,this.state.permissionMode===preset);dialog.append(button);
    }dialog.append(error);
  }
  async modelSettings(){
    const child=this.state.id,n=(...args)=>this.workbench.node(...args),b=(...args)=>this.workbench.button(...args);
    try{
      const catalog=await this.request({action:'catalog',id:child});
      if(this.disposed||this.state.id!==child)return;
      const dialog=n('dialog','picker'),head=n('div','picker-head'),model=n('select'),custom=n('input'),effort=n('select'),status=n('p','error');
      head.append(n('h2','',BridgeI18n.t('侧边聊天设置')),b('×',()=>dialog.close(),BridgeI18n.t('关闭')));
      const modelLabel=n('label','',BridgeI18n.t('模型')),effortLabel=n('label','',BridgeI18n.t('思考程度'));
      for(const row of catalog.models||[])model.append(new Option(row.name||row.id,row.id));
      const manual=new Option(BridgeI18n.t('自定义模型…'),'__custom__');manual.dataset.i18n='自定义模型…';model.append(manual);
      model.value=[...model.options].some(o=>o.value===catalog.currentModel)?catalog.currentModel:'__custom__';custom.value=catalog.currentModel||'';
      custom.setAttribute("data-i18n-aria-label",'模型 ID');custom.setAttribute('aria-label',BridgeI18n.t('模型 ID'));
      const fill=()=>{custom.hidden=model.value!=='__custom__';const known=catalog.models?.find(m=>m.id===model.value);const values=known?.efforts?.length?known.efforts:['none','minimal','low','medium','high','xhigh','max','ultra'];effort.replaceChildren(...values.map(value=>new Option(value,value)));effort.value=values.includes(catalog.currentEffort)?catalog.currentEffort:known?.defaultEffort||values[0];};
      model.onchange=fill;fill();modelLabel.append(model,custom);effortLabel.append(effort);
      const save=b(BridgeI18n.t('保存'),async()=>{save.disabled=true;try{const state=await this.request({action:'settings',id:child,model:model.value==='__custom__'?custom.value.trim():model.value,effort:effort.value});this.apply(state);dialog.close();}catch(e){status.textContent=BridgeI18n.t(e.message);}finally{save.disabled=false;}});
      dialog.append(head,n('p','muted',BridgeI18n.t('仅影响此侧边聊天，从下一轮生效。')),modelLabel,effortLabel,status,save);
      document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
    }catch(e){this.status.textContent=BridgeI18n.t(e.message);}
  }
  async load(){try{this.apply(await this.request());}catch(e){if(!this.disposed){this.status.textContent=BridgeI18n.t(e.message);if(this.state)this.state.connected=false;this.controls();}}}
  async create(){
    if(this.busy)return;this.busy=true;this.controls();this.status.textContent=BridgeI18n.t('正在创建临时侧边聊天…');
    try{this.apply(await this.request({action:'create',creationId:this.creationId}));}
    catch(e){if(!this.disposed)this.status.textContent=BridgeI18n.t(e.message);}
    finally{this.busy=false;this.controls();}
  }
  apply(state){
    if(this.disposed)return;
    if(this.state?.id&&this.state.id!==state.id){this.pending=null;this.creationId=uuid();this.input.value='';this.attachments.clear(this.attachments.key,this.attachments.ids());this.attachments.reset();this.selectedSkills.clear();this.skillChips();this.modeSelected=false;this.workMode.value='default';}
    this.state=state;
    if(state.id&&this.attachments.key!==state.id)this.attachments.open(state.id);
    if(state.id){this.modelButton.textContent=[state.model,state.effort].filter(Boolean).join(' · ')||BridgeI18n.t('模型与思考程度');delete this.modelButton.dataset.i18n;}
    if(!this.modeSelected)this.workMode.value=state.collaborationMode==='plan'?'plan':'default';
    const permission={'ask':'请求批准','auto-review':'帮我批准','full-access':'完全访问权限'}[state.permissionMode]||'权限';this.permissionsButton.textContent=BridgeI18n.t(permission);this.permissionsButton.dataset.i18n=permission;
    this.tab.body.classList.toggle('has-side-chat',!!state.id);
    if(this.pending&&state.submissions?.some(s=>s.id===this.pending.submissionId&&['accepted','queued'].includes(s.status))){
      if(this.input.value===this.pending.text)this.input.value='';this.attachments.clear(state.id,this.pending.attachments);this.pending=null;this.selectedSkills.clear();this.skillChips();this.resizeInput();
    }
    this.status.textContent=state.error||(!state.id?'':this.pending?BridgeI18n.t('发送结果未确认，请核对消息，避免重复发送。'):state.requests?.length?BridgeI18n.t('有请求需要处理'):state.status==='active'?BridgeI18n.t('正在回复…'):BridgeI18n.t('已连接'));
    const stamp=JSON.stringify([state.id,state.connected,state.turns]);
    if(stamp!==this.stamp){
      this.stamp=stamp;const scroll=this.messages.scrollTop,follow=this.messages.scrollHeight-this.messages.clientHeight-scroll<60;this.messages.replaceChildren();
      for(const turn of state.turns||[]){for(const message of turn.messages||[]){
        const card=this.workbench.node('article','wb-side-message '+message.role);
        if(message.role==='user'||message.role==='assistant'){
          card.append(this.workbench.node('small','',message.role==='user'?BridgeI18n.t('你'):'Codex'));
          const text=this.workbench.node('div','message-body');renderMarkdown(text,message.text||'');card.append(text);
          if(message.attachments?.length){const list=this.workbench.node('div','attachment-list');for(const file of message.attachments){
            const chip=this.workbench.node('div','attachment-chip');if(file.image){const image=this.workbench.node('img','attachment-thumb');image.src=this.uploadUrl(state.id,file.id,'preview');image.alt=file.name;image.dataset.imagePreview=image.src+'&variant=original';image.dataset.imageName=file.name;image.tabIndex=0;image.setAttribute('role','button');chip.append(image);}
            chip.append(this.workbench.raw(this.workbench.node('strong','',file.name)));list.append(chip);
          }card.append(list);}
        }else{
          const details=this.workbench.node('details');details.append(this.workbench.node('summary','',message.title||message.kind||BridgeI18n.t('工具活动')),this.workbench.node('pre','',message.output||message.text||''));card.append(details);
        }
        this.messages.append(card);
      }if(turn.error)this.messages.append(this.workbench.node('p','error',BridgeI18n.t('本次回复未完成，请检查模型接入。')));}
      if(!this.messages.children.length)this.messages.append(this.workbench.node('p','wb-empty',state.connected?BridgeI18n.t('上下文已准备好，开始单独讨论。'):BridgeI18n.t('新建后即可在这里提问。')));
      this.messages.scrollTop=follow?this.messages.scrollHeight:scroll;
    }
    this.renderApprovals(state.requests||[]);this.renderQueue(state.submissions||[]);this.controls();
  }
  renderQueue(rows){
    const stamp=JSON.stringify(rows);if(stamp===this.queueStamp)return;this.queueStamp=stamp;this.queued.replaceChildren();
    for(const row of rows.filter(r=>['queued','failed','unknown'].includes(r.status))){
      const card=this.workbench.node('div','queue-card');card.append(this.workbench.node('strong','',BridgeI18n.t(row.status==='queued'?'完成后发送':row.status==='failed'?'发送失败':'发送结果未确认')),this.workbench.raw(this.workbench.node('p','',row.text)));
      if(row.error)card.append(this.workbench.node('p','error',BridgeI18n.t(row.error)));
      if(row.status==='queued')card.append(this.workbench.button(BridgeI18n.t('取消'),()=>this.action('cancel-queued',{submissionId:row.id})));
      this.queued.append(card);
    }
  }
  renderApprovals(requests){
    const stamp=JSON.stringify(requests);if(stamp===this.approvalStamp)return;this.approvalStamp=stamp;this.approvals.replaceChildren();
    const n=(...args)=>this.workbench.node(...args);
    for(const request of requests){
      const card=n('form','wb-side-approval'),p=request.params||{},method=request.method,controls={};
      card.append(n('strong','',BridgeI18n.t('需要你的确认')));
      if(!request.supported){card.append(n('p','',p.message));this.approvals.append(card);continue;}
      for(const key of ['reason','command','cwd','message'])if(p[key])card.append(n(key==='command'?'pre':'p','',p[key]));
      if(p.changes)card.append(n('pre','',p.changes.map(c=>c.path+'\n'+(c.diff||'')).join('\n')));
      if(p.permissions)card.append(n('pre','',JSON.stringify(p.permissions,null,2)));
      const isInput=method.includes('requestUserInput'),isMcp=method==='mcpServer/elicitation/request';
      if(isInput)for(const q of p.questions||[]){
        const label=n('label','',q.question||q.header||q.id),input=n('textarea');input.rows=2;input.required=true;
        if(q.options?.length){const select=n('select');select.append(new Option(BridgeI18n.t('选择答案或自行填写'),''));for(const option of q.options)select.append(new Option(option.label,option.label));select.onchange=()=>{input.value=select.value;};label.append(select);}
        label.append(input);card.append(label);controls[q.id]=()=>input.value;
      }
      if(isMcp)for(const [key,field] of Object.entries(p.requestedSchema?.properties||{})){
        const label=n('label','',field.title||key);let input;
        if(field.enum){input=n('select');input.append(new Option(BridgeI18n.t('请选择…'),''));for(const value of field.enum)input.append(new Option(String(value),String(value)));}
        else{input=n('input');input.type=field.type==='boolean'?'checkbox':['number','integer'].includes(field.type)?'number':'text';if(input.type==='number')input.step=field.type==='integer'?'1':'any';}
        input.required=(p.requestedSchema.required||[]).includes(key)&&input.type!=='checkbox';label.append(input);card.append(label);
        controls[key]=()=>input.type==='checkbox'?input.checked:input.value===''?undefined:['number','integer'].includes(field.type)?Number(input.value):input.value;
      }
      const actions=n('div','wb-side-head'),error=n('p','error');
      const respond=async response=>{
        card.querySelectorAll('button').forEach(b=>b.disabled=true);
        try{this.apply(await this.request({action:'respond',id:this.state.id,requestId:request.id,response}));}
        catch(e){error.textContent=BridgeI18n.t(e.message);card.querySelectorAll('button').forEach(b=>b.disabled=false);}
      };
      const available=p.availableDecisions||['accept','decline'];
      const yes=n('button','',''+(isInput?BridgeI18n.t('提交回复'):BridgeI18n.t('本次允许')));yes.type='submit';if(isInput||isMcp||available.includes('accept'))actions.append(yes);
      if(!isInput&&available.includes('decline'))actions.append(this.workbench.button(BridgeI18n.t('拒绝'),()=>respond(isMcp?{action:'decline'}:{decision:'decline'})));
      card.onsubmit=e=>{e.preventDefault();if(isInput){const answers={};for(const [key,get] of Object.entries(controls)){const value=get();if(!value.trim()){error.textContent=BridgeI18n.t('请完整回答问题');return;}answers[key]=[value];}respond({answers});}
        else if(isMcp){const content={};for(const [key,get] of Object.entries(controls)){const value=get();if(value!==undefined)content[key]=value;}respond({action:'accept',content});}
        else respond({decision:'accept'});};
      card.append(actions,error);this.approvals.append(card);
    }
  }
  schedule(){
    clearTimeout(this.timer);if(this.disposed)return;
    this.timer=setTimeout(async()=>{try{if(!this.busy&&this.workbench.isVisible(this.session,this.tab)&&!document.hidden)await this.load();}finally{this.schedule();}},1200);
  }
  async send(){
    if(this.sendButton.disabled)return;
    this.busy=true;const text=this.input.value;this.pending={text,submissionId:uuid(),attachments:this.attachments.ids(),skills:[...this.selectedSkills.keys()],mode:this.sendMode.value,workMode:this.sendMode.value==='steer'?null:this.workMode.value};const attachmentIds=this.pending.attachments;this.controls();this.status.textContent=BridgeI18n.t('正在发送…');
    try{const result=await this.request({action:'send',id:this.state.id,...this.pending});if(this.disposed)return;
      if(['accepted','queued'].includes(result.status)){this.attachments.clear(this.state.id,attachmentIds);this.pending=null;if(this.input.value===text)this.input.value='';this.selectedSkills.clear();this.skillChips();this.resizeInput();await this.load();}
      else this.status.textContent=BridgeI18n.t('发送结果未确认，请核对消息，避免重复发送。');
    }catch(e){if(!this.disposed){if([400,401,403,404].includes(e.status))this.pending=null;this.status.textContent=BridgeI18n.t(e.message);}}
    finally{this.busy=false;this.controls();}
  }
  async action(action,fields={}){if(this.busy)return;this.busy=true;this.controls();try{this.apply(await this.request({action,id:this.state.id,...fields}));}catch(e){this.status.textContent=BridgeI18n.t(e.message);}finally{this.busy=false;this.controls();}}
  async end(){
    if(this.busy)return false;if(!this.state?.id)return true;
    if(!confirm(BridgeI18n.t('结束此侧边聊天？正在运行的回复会停止，临时内容将清除，所有设备都无法继续此聊天。')))return false;
    this.busy=true;this.controls();
    try{await this.request({action:'close',id:this.state.id});this.apply({id:null,connected:false,turns:[],requests:[]});return true;}
    catch(e){this.status.textContent=BridgeI18n.t(e.message);return false;}
    finally{this.busy=false;this.controls();}
  }
  dispose(){this.disposed=true;clearTimeout(this.timer);this.input.value='';this.attachments.reset();this.messages.replaceChildren();}
}
