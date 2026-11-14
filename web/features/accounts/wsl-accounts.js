'use strict';
// WSL credentials and account state are scoped to a registered environment.
class WslAccountsPanel {
  constructor({root,provider,read,request,desktop=false,onSwitched}){
    Object.assign(this,{root,provider,read,request,desktop,onSwitched});this.entry='';this.generation=0;this.value=null;this.models=new Map();this.busy=false;
    root.classList.add('wsl-accounts-panel');this.onLanguage=()=>this.render();document.addEventListener('bridge-language',this.onLanguage);this.render();
  }
  t(key){return BridgeI18n.t(key);}
  node(tag,text='',className=''){const node=document.createElement(tag);node.className=className;node.textContent=text;return node;}
  button(key,action){const node=this.node('button',this.t(key));node.type='button';node.onclick=action;node.disabled=this.busy;return node;}
  clear(){this.generation++;this.value=null;this.models.clear();this.busy=false;this.entry='';this.error='';this.editRow=null;this.sources=null;if(this.key)this.key.value='';this.render();}
  dispose(){this.clear();document.removeEventListener('bridge-language',this.onLanguage);}
  select(entry){this.clear();this.entry=entry||'';this.root.hidden=!this.entry;if(this.entry)return this.refresh();}
  async refresh(){if(!this.entry||this.busy)return;const generation=++this.generation,entry=this.entry;this.busy=true;this.error='';this.render();try{const value=await this.read(entry);if(generation!==this.generation)return;if(value.entry!==entry||value.provider!==this.provider)throw Error('WSL 账号结果与所选环境不一致');if(value.restartPending)this.error=value.restartError||'账号配置已应用，共享服务尚未恢复；请检查并恢复，不要重复切换账号';else if(value.continuationPending)this.error=value.continuationError||'账号配置已切换，原会话续接尚未核验；请刷新检查，不要重复提交切换';else if(Array.isArray(value.draftContinuations))this.onSwitched?.(value);this.value=value;}catch(error){if(generation===this.generation)this.error=error.message;}finally{if(generation===this.generation){this.busy=false;this.render();}}}
  async perform(action,fields={}){
    if(this.busy||!this.entry||(!this.value?.available&&!(action==='connect'&&this.value?.capabilities?.connect)))return;
    const generation=++this.generation,entry=this.entry,payload={provider:this.provider,entry,action,...(['switch','recover-restart'].includes(action)?{expectedIdentity:this.value.identity}:{}),...fields};this.busy=true;this.error='';if(this.key)this.key.value='';this.render();
    try{const result=await this.request(payload);if(generation!==this.generation)return;
      if(result.entry&&result.entry!==entry||result.provider&&result.provider!==this.provider)throw Error('WSL 账号结果与所选环境不一致');
      if(action==='switch'||action==='recover-restart'){if(result.restartPending)this.error=result.restartError||'账号配置已应用，共享服务尚未恢复；请检查并恢复，不要重复切换账号';else if(result.continuationPending)this.error=result.continuationError||'账号配置已切换，原会话续接尚未核验；请刷新检查，不要重复提交切换';else this.onSwitched?.(result);}
      if(action==='models'){this.models.set(fields.id,result);}
      else if(action==='import-sources'){this.sources=result.sources||[];}
      else if(Array.isArray(result.accounts)){this.value=result;this.models.clear();this.editRow=null;}
      else {this.busy=false;await this.refresh();}
    }catch(error){if(generation===this.generation)this.error=error.message;}
    finally{payload.apiKey='';if(generation===this.generation){this.busy=false;this.render();}}
  }
  field(form,key,type='text',value=''){const label=this.node('label'),input=this.node('input');input.type=type;input.value=value;input.disabled=this.busy;label.append(this.node('span',this.t(key)),input);form.append(label);return input;}
  render(){
    const root=this.root,data=this.value;root.replaceChildren();if(!this.entry)return;
    const identity=data?.target||{},heading='WSL · '+(this.provider==='claude'?'Claude Code':'Codex CLI');
    const head=this.node('div','','wsl-account-heading');head.append(this.node('h3',heading||this.t('WSL 账号与接入')),this.button('刷新',()=>this.refresh()));root.append(head);
    if(this.error){const error=this.node('p',this.t(this.error),'error');error.setAttribute('role','alert');root.append(error);}
    if(!data){root.append(this.node('p',this.t(this.busy?'正在读取…':'请选择 WSL 环境'),'account-muted'));return;}
    root.append(this.node('small',[identity.distribution,identity.user,identity.configDir].filter(Boolean).join(' · ')||this.entry,'wsl-account-target account-muted'));
    const statusKey=!data.available?'WSL 环境不可用；请启动发行版后刷新。':data.activeVerified===false?'尚未验证登录有效性':'WSL 配置已读取；登录有效性以核验结果为准。';
    const status=this.node('p',this.t(statusKey),'account-muted wsl-account-status');status.setAttribute('role','status');root.append(status);
    if(!data.available&&data.capabilities?.connect)root.append(this.button('启动并连接 WSL',()=>this.perform('connect')));
    if(this.provider==='codex'&&data.restartPending){const recovery=this.node('div','','wsl-account-confirm');recovery.append(this.node('p',this.t('账号配置已应用。此操作仅恢复共享服务和原会话，不会再次切换账号。')));if(data.capabilities?.recoverRestart){const button=this.button('检查并恢复共享服务',()=>this.confirmRestart());button.disabled=this.busy||!data.available;recovery.append(button);}root.append(recovery);}
    const notes=[];
    if(data.verification)notes.push(this.t('核验状态')+': '+this.t(data.verification.status||data.verification));
    if(data.sharedWithDesktop===false)notes.push(this.t('此 WSL 配置独立于本机接入。'));
    if(data.sharedWithDesktop||data.shared)notes.push(this.t('此配置目录与其他接入共用。'));
    if(notes.length)root.append(this.node('p',notes.join(' '),'account-muted wsl-account-notes'));
    if(data.captureNotice)root.append(this.node('p',this.t(data.captureNotice),'account-muted'));
    if((data.blockers||[]).length){const notice=this.node('details','','wsl-account-limits');notice.append(this.node('summary',this.t('暂不能切换接入')));for(const reason of data.blockers)notice.append(this.node('p',this.t(typeof reason==='string'?reason:reason.message||reason.code),'account-muted'));root.append(notice);}
    if(!(data.accounts||[]).length){root.append(this.node('p',this.t('尚未保存 WSL 接入'),'accounts-current'));root.append(this.node('p',this.t('添加或导入只保存接入，不会切换当前账号。'),'account-muted'));}
    const blocked=!data.available||(data.blockers||[]).length>0;
    if((data.accounts||[]).length)root.append(this.node('h4',this.t('已保存的 WSL 接入'),'wsl-account-list-title'));
    for(const row of data.accounts||[]){
      const card=this.node('section','','wsl-account-row'),actions=this.node('div','','wsl-account-actions');card.append(this.node('strong',row.id==='native'?this.t(row.name):row.name||row.id),this.node('small',[this.t(row.kind==='api'?'API 接入':'原生登录'),row.baseUrl,row.model].filter(Boolean).join(' · ')));
      if(row.id===data.activeId||row.active)actions.append(this.node('span',this.t('当前接入')));
      else {const native=row.id==='native',attempt=!!(native?data.capabilities?.attemptRestore:data.capabilities?.attemptSwitch),canSwitch=data.capabilities?.switch||(native&&data.capabilities?.restore)||attempt;if(canSwitch&&!data.restartPending){const button=this.button(attempt?'检查并切换':'切换到此接入',()=>this.confirmSwitch(row,attempt));button.disabled=this.busy||!data.available||(blocked&&!attempt&&!(native&&data.capabilities?.restore));actions.append(button);}}
      if(row.kind==='api'&&data.capabilities?.models){const button=this.button('查看上游模型',()=>this.perform('models',{id:row.id}));button.disabled=this.busy||!data.available;actions.append(button);}
      if(data.capabilities?.save&&data.available&&row.id!=='native'){const active=row.id===data.activeId||row.active;if(!active)actions.append(this.button('编辑',()=>{this.editRow=row;this.render();}));actions.append(this.button('重命名',()=>this.editName(row)));if(!active)actions.append(this.button('删除',()=>this.confirmDelete(row)));}
      card.append(actions);if(row.kind!=='api')card.append(this.node('p',this.t('原生登录的模型请在原 CLI 查看；已有聊天继续使用原模型。'),'account-muted'));
      const result=this.models.get(row.id);if(result){const output=this.node('div','','wsl-account-models');const list=this.node('ul');for(const model of result.models||[]){const id=typeof model==='string'?model:model.id,name=typeof model==='string'?model:model.name||model.id,item=this.node('li',name+(name!==id?' · '+id:'')),efforts=typeof model==='object'?model.efforts:null;item.append(this.node('small',this.t('推理强度')+': '+(Array.isArray(efforts)&&efforts.length?efforts.map(effort=>this.t(typeof effort==='string'?effort:effort.name||effort.id)).join(' / '):this.t('上游未提供'))));list.append(item);}output.append(list);if(!(result.models||[]).length)output.append(this.node('p',this.t(result.modelError||'未返回模型列表')));if(result.cache?.updatedAt)output.append(this.node('small',this.t('更新于：')+new Date(result.cache.updatedAt*1000).toLocaleString(BridgeI18n.locale())));if(result.cache?.state)output.append(this.node('small',this.t('缓存状态')+': '+result.cache.state));output.append(this.button('刷新模型',()=>this.perform('models',{id:row.id,refresh:true})));card.append(output);}
      root.append(card);
    }
    if(data.capabilities?.save&&data.available){this.renderEditor(root);if(data.capabilities?.capture!==false&&!(data.accounts||[]).some(row=>row.id==='native')){const actions=this.node('div','','wsl-account-actions');actions.append(this.button('保存当前原生接入',()=>this.perform('import-current',{})));root.append(actions);}}
    if(data.loginGuidance){const help=this.node('details','','wsl-account-help');help.append(this.node('summary',this.t('官方登录')),this.node('p',this.t(data.loginGuidance),'account-muted'));root.append(help);}
  }
  confirmSwitch(row,attempt=false){const box=this.node('div','','wsl-account-confirm'),restart=this.provider==='codex'&&(attempt||this.value?.restartRequired);box.append(this.node('p',this.t(restart?'将核验所选 WSL 配置目录的任务、审批和未确认发送；全部空闲后正常重启共享服务。原终端连接可能需要重新连接，不会强制结束进程。':attempt?'将先释放此网关的空闲执行器；外部 CLI 或未确认任务仍会阻止切换。':'仅切换所选 WSL 环境的后续请求。请先结束该环境正在运行的任务。')),this.button(attempt||restart?'检查并切换':'确认切换',()=>this.perform('switch',{id:row.id})),this.button('取消',()=>box.remove()));this.root.prepend(box);}
  confirmRestart(){const box=this.node('div','','wsl-account-confirm');box.append(this.node('p',this.t('将核验已应用的账号配置，并仅尝试正常恢复所选 WSL 共享服务和原会话。不会重复切换账号或强制结束进程。')),this.button('确认恢复共享服务',()=>this.perform('recover-restart')),this.button('取消',()=>box.remove()));this.root.prepend(box);}
  confirmDelete(row){const box=this.node('div','','wsl-account-confirm');box.append(this.node('p',this.t('删除此保存的接入？')+' '+row.name),this.button('删除',()=>this.perform('delete',{id:row.id})),this.button('取消',()=>box.remove()));this.root.prepend(box);}
  editName(row){const form=this.node('form','','wsl-account-editor'),name=this.field(form,'名称','text',row.name);const save=this.node('button',this.t('保存名称'));save.type='submit';save.disabled=this.busy;form.append(save,this.button('取消',()=>form.remove()));form.onsubmit=event=>{event.preventDefault();this.perform('rename',{id:row.id,name:name.value});};this.root.prepend(form);name.focus();}
  renderEditor(root){
    const editing=this.editRow,details=this.node('details'),summary=this.node('summary',this.t(editing?'编辑 WSL API 接入':'添加 WSL API 接入')),form=this.node('form','','wsl-account-editor');details.open=!!editing||!(this.value.accounts||[]).length;details.append(summary,form);
    const name=this.field(form,'名称','text',editing?.name||''),baseUrl=this.field(form,'API 地址','url',editing?.baseUrl||''),model=this.field(form,'模型 ID','text',editing?.model||'');this.key=this.field(form,'API Key','password');this.key.autocomplete='new-password';this.key.required=!editing;this.key.placeholder=this.t(editing?'留空保留已有密钥':'');for(const input of [name,baseUrl,model])input.required=true;const key=this.key;
    const scheme=this.node('select');for(const value of ['x-api-key','bearer']){const option=this.node('option',value);option.value=value;scheme.append(option);}scheme.value=editing?.authScheme||'x-api-key';if(this.provider==='claude'){const label=this.node('label');label.append(this.node('span',this.t('认证方式')),scheme);form.append(label);}
    const save=this.node('button',this.t('保存 API 接入'));save.type='submit';save.disabled=this.busy;form.append(save);if(editing)form.append(this.button('取消编辑',()=>{this.editRow=null;this.render();}));form.onsubmit=event=>{event.preventDefault();this.perform('save-api',{...(editing?{id:editing.id}:{}),name:name.value,baseUrl:baseUrl.value,model:model.value,apiKey:key.value,authScheme:scheme.value});};root.append(details);
    if(this.value.capabilities?.import){
      const imports=this.node('div','','wsl-account-import');imports.append(this.button('从本机导入 API 接入',()=>this.perform('import-sources')),this.node('p',this.t('导入的 localhost、127.0.0.1 和 ::1 地址指向所选 WSL 环境，地址保持原样。'),'account-muted'));root.append(imports);
      if(this.sources){const importForm=this.node('form','','wsl-account-editor'),label=this.node('label'),source=this.node('select');label.append(this.node('span',this.t('选择本机保存的 API 接入')),source);for(const row of this.sources){const option=this.node('option',[row.name,row.source].filter(Boolean).join(' · '));option.value=row.id;source.append(option);}const submit=this.node('button',this.t('导入指定 API 接入'));submit.type='submit';submit.disabled=this.busy||!this.sources.length;importForm.append(label,submit);if(!this.sources.length)importForm.append(this.node('p',this.t('本机暂无可导入的 API 接入'),'account-muted'));importForm.onsubmit=event=>{event.preventDefault();const selected=this.sources.find(row=>row.id===source.value);if(selected)this.perform('import-api',{sourceId:selected.id,sourceIdentity:selected.sourceIdentity});};root.append(importForm);}
    }
  }
}
