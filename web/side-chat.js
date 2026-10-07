'use strict';

// A shared, gateway-owned ephemeral fork. Disposing a view never ends the chat.
class SideChatPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});this.creationId=uuid();
    const n=(...args)=>workbench.node(...args),b=(...args)=>workbench.button(...args);
    const head=n('div','wb-side-head');
    this.createButton=b(BridgeI18n.t('新建侧边聊天'),()=>this.create());
    this.stopButton=b(BridgeI18n.t('停止回复'),()=>this.action('stop'));this.stopButton.hidden=true;
    this.endButton=b(BridgeI18n.t('结束聊天'),async()=>{if(await this.end())this.load();});this.endButton.hidden=true;
    this.modelButton=b(BridgeI18n.t('模型与思考程度'),()=>this.modelSettings());
    head.append(this.createButton,this.endButton);
    this.help=n('p','wb-side-help',BridgeI18n.t('继承创建时的主聊天上下文。在手机、网页或电脑 Bridge 工作台继续；离开页面会保留，关闭聊天或重启网关后不再保留。'));
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    this.messages=n('div','wb-side-messages');this.messages.setAttribute("data-i18n-aria-label",'侧边聊天消息');this.messages.setAttribute('aria-label',BridgeI18n.t('侧边聊天消息'));
    this.approvals=n('div','wb-side-approvals');
    this.form=n('form','composer wb-side-composer');this.input=n('textarea');this.input.rows=2;this.input.maxLength=20000;this.input.setAttribute("data-i18n-placeholder",'问一个相关问题…');this.input.placeholder=BridgeI18n.t('问一个相关问题…');this.input.setAttribute("data-i18n-aria-label",'侧边聊天输入');this.input.setAttribute('aria-label',BridgeI18n.t('侧边聊天输入'));
    this.sendButton=n('button','primary',BridgeI18n.t('发送 ↑'));this.sendButton.type='submit';this.sendButton.disabled=true;
    const tools=n('div','composer-tools'),bottom=n('div','compose-bottom'),actions=n('div','compose-actions');
    this.modelButton.className='tool-button';this.stopButton.className='stop';tools.append(this.modelButton);
    this.attachmentList=n('div','attachment-list');this.fileInput=n('input');this.fileInput.type='file';this.fileInput.multiple=true;this.fileInput.hidden=true;
    this.attachButton=b('',()=>{},BridgeI18n.t('添加文件或图片'));this.attachButton.className='attach-button';
    this.attachButton.append(document.querySelector('#attach-button svg').cloneNode(true));
    actions.append(this.stopButton,this.sendButton);bottom.append(this.attachButton,this.fileInput,actions);
    this.form.append(tools,this.attachmentList,this.input,bottom);
    this.attachments=new ChatAttachments({root:this.attachmentList,button:this.attachButton,input:this.fileInput,paste:this.input,drop:this.form,
      onChange:()=>this.controls(),preview:(key,id)=>this.uploadUrl(key,id,'preview'),
      upload:(key,id,file)=>this.uploadFile(this.uploadUrl(key,id)+'&name='+encodeURIComponent(file.name),file),
      thumbnail:async(key,id,file)=>{const thumb=await this.attachments.thumbnailBlob(file);return this.uploadFile(this.uploadUrl(key,id,'thumb')+'&width='+thumb.width+'&height='+thumb.height,thumb.data);}});
this.form.onsubmit=e=>{e.preventDefault();this.send();};
    this.input.oninput=()=>{this.input.style.height='auto';this.input.style.height=Math.min(180,this.input.scrollHeight)+'px';this.controls();};
    tab.body.append(head,this.help,this.status,this.messages,this.approvals,this.form);this.load();this.schedule();
  }
  uploadUrl(child,id,operation=''){
    return '/api/sessions/'+this.session.id+'/uploads'+(operation?'/'+encodeURIComponent(id)+'/'+operation:'')+'?host='+encodeURIComponent(this.session.host)+'&side='+encodeURIComponent(child)+(operation?'':'&id='+encodeURIComponent(id));
  }
  async uploadFile(url,file){
    const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':file.type||'application/octet-stream','X-CSRF-Token':this.workbench.csrf()},body:file});
    const result=await response.json();if(!response.ok){if(response.status===401)this.workbench.onUnauthorized();const error=Error(result.error||BridgeI18n.t('上传失败'));error.status=response.status;throw error;}return result;
  }
  relabel(){
    this.input.setAttribute("data-i18n-placeholder",'问一个相关问题…');this.input.placeholder=BridgeI18n.t('问一个相关问题…');
    this.input.setAttribute("data-i18n-aria-label",'侧边聊天输入');this.input.setAttribute('aria-label',BridgeI18n.t('侧边聊天输入'));
    this.messages.setAttribute("data-i18n-aria-label",'侧边聊天消息');this.messages.setAttribute('aria-label',BridgeI18n.t('侧边聊天消息'));
    if(this.state)this.apply(this.state);
  }
  url(){return '/api/sessions/'+this.session.id+'/side-chat?host='+encodeURIComponent(this.session.host);}
  request(body){return this.workbench.request(this.url(),body);}
  controls(){
    if(this.attachments){const locked=!!this.busy||!!this.pending||!this.state?.connected;if(this.attachments.locked!==locked)this.attachments.setLocked(locked);}
    this.modelButton.hidden=!this.state?.id;this.modelButton.disabled=!!this.busy;
    this.sendButton.disabled=!!this.busy||!this.state?.connected||this.state.status==='active'||!!this.state.requests?.length||(!this.input.value.trim()&&!this.attachments?.ids().length)||!this.attachments?.ready()||!!this.pending;
    this.createButton.hidden=!!this.state?.id;this.createButton.disabled=!!this.busy;
    this.stopButton.hidden=!this.state?.id||this.state.status!=='active';this.stopButton.disabled=!!this.busy;
    this.endButton.hidden=!this.state?.id;this.endButton.disabled=!!this.busy;
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
    if(this.state?.id&&this.state.id!==state.id){this.pending=null;this.creationId=uuid();this.input.value='';this.attachments.clear(this.attachments.key,this.attachments.ids());this.attachments.reset();}
    this.state=state;
    if(state.id&&this.attachments.key!==state.id)this.attachments.open(state.id);
    if(state.id){this.modelButton.textContent=[state.model,state.effort].filter(Boolean).join(' · ')||BridgeI18n.t('模型与思考程度');delete this.modelButton.dataset.i18n;}
    this.help.hidden=!!state.id;
    if(this.pending&&state.submissions?.some(s=>s.id===this.pending.submissionId&&s.status==='accepted')){
      if(this.input.value===this.pending.text)this.input.value='';this.attachments.clear(state.id,this.pending.attachments);this.pending=null;
    }
    this.status.textContent=state.error||(!state.id?BridgeI18n.t('尚未创建侧边聊天'):this.pending?BridgeI18n.t('发送结果未确认，请核对消息，避免重复发送。'):state.requests?.length?BridgeI18n.t('有请求需要处理'):state.status==='active'?BridgeI18n.t('正在回复…'):BridgeI18n.t('已连接 · ')+(state.model||BridgeI18n.t('沿用主聊天设置')));
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
    this.renderApprovals(state.requests||[]);this.controls();
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
    this.busy=true;const text=this.input.value;this.pending={text,submissionId:uuid(),attachments:this.attachments.ids()};const attachmentIds=this.pending.attachments;this.controls();this.status.textContent=BridgeI18n.t('正在发送…');
    try{const result=await this.request({action:'send',id:this.state.id,...this.pending});if(this.disposed)return;
      if(result.status==='accepted'){this.attachments.clear(this.state.id,attachmentIds);this.pending=null;if(this.input.value===text)this.input.value='';await this.load();}
      else this.status.textContent=BridgeI18n.t('发送结果未确认，请核对消息，避免重复发送。');
    }catch(e){if(!this.disposed){if([400,401,403,404].includes(e.status))this.pending=null;this.status.textContent=BridgeI18n.t(e.message);}}
    finally{this.busy=false;this.controls();}
  }
  async action(action){if(this.busy)return;this.busy=true;this.controls();try{this.apply(await this.request({action,id:this.state.id}));}catch(e){this.status.textContent=BridgeI18n.t(e.message);}finally{this.busy=false;this.controls();}}
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
