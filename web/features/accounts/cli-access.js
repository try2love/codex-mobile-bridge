'use strict';
class CliAccessPanel {
  constructor({root,provider,read,request,formOnly=false,onChanged=()=>{}}){
    if(!['codex','claude'].includes(provider))throw Error('Invalid CLI provider');
    Object.assign(this,{root,provider,read,request,formOnly,onChanged});this.value=null;this.busy=false;this.generation=0;this.draftRevision=0;this.apiResults=new Map();this.switchRequests=new Map();
    this.build();
  }
  t(key){return BridgeI18n.t(key);}
  node(tag,key,className=''){
    const node=document.createElement(tag);node.className=className;
    if(key){node.dataset.i18n=key;node.textContent=this.t(key);}
    return node;
  }
  button(key,action){const button=this.node('button',key);button.type='button';button.onclick=action;return button;}
  field(key,type='text'){
    const label=this.node('label'),input=document.createElement('input');input.type=type;
    label.append(this.node('span',key),input);return {label,input};
  }
  build(){
    this.root.classList.add('cli-access-panel');this.root.replaceChildren();
    const heading=this.node('h2',this.provider==='codex'?'Codex CLI 接入':'Claude Code CLI 接入'),refresh=this.button('刷新',()=>this.refresh());
    this.status=this.node('p',null,'account-muted');this.status.setAttribute('role','status');
    this.error=this.node('p',null,'error');this.error.setAttribute('role','alert');
    this.rows=this.node('div',null,'cli-access-rows');
    this.name=this.field('名称');this.baseUrl=this.field('API 地址','url');this.model=this.field('模型 ID');this.key=this.field('API Key','password');this.key.input.autocomplete='new-password';
    this.scheme=this.node('select');for(const [id,name] of [['x-api-key','x-api-key'],['bearer','Bearer']]){const option=this.node('option');option.value=id;option.textContent=name;this.scheme.append(option);}
    this.schemeRow=this.node('label');this.schemeRow.append(this.node('span','认证方式'),this.scheme);
    this.form=this.node('form',null,'cli-access-form');this.form.onsubmit=event=>{event.preventDefault();const provider=this.provider;this.perform(this.editKind==='rename'?{provider,action:'rename',id:this.editId,name:this.name.input.value}:{provider,action:'saveApi',id:this.editId||undefined,name:this.name.input.value,baseUrl:this.baseUrl.input.value,model:this.model.input.value,apiKey:this.key.input.value,authScheme:provider==='claude'?this.scheme.value:undefined});};
    this.save=this.node('button','保存 API 接入');this.save.type='submit';
    this.cancel=this.button('取消编辑',()=>this.resetForm());this.cancel.hidden=true;
    const modelHint=this.node('p','模型接口不可用时仍可手动填写模型 ID。','account-muted');modelHint.classList.add('cli-access-model-hint');
    for(const field of [this.baseUrl.label,this.key.label,this.schemeRow])field.classList.add('cli-access-full-row');
    this.save.classList.add('primary');
    const actions=this.node('div',null,'cli-access-form-actions');actions.append(this.cancel,this.save);
    if(this.formOnly&&this.provider==='codex'){
      this.root.classList.add('codex-api-editor');
      this.name.input.placeholder=this.t('例如：我的 API');
      this.key.input.placeholder=this.t('请输入 API Key');
      this.model.input.placeholder=this.t('获取后自动填入，或手动输入');
      ApiAccessEditor.mount(this,{connectionFields:[this.name.label,this.baseUrl.label,this.key.label],actions});
      this.cancel.hidden=false;this.cancel.textContent=this.t('取消');this.cancel.onclick=()=>{this.resetForm();if(this.root.parentNode?.tagName==='DETAILS')this.root.parentNode.open=false;};
      this.baseUrl.input.placeholder='https://api.example.com/v1';
      this.baseUrl.input.oninput=this.key.input.oninput=()=>this.clearDraftModels();
    }else this.form.append(this.name.label,this.model.label,modelHint,this.baseUrl.label,this.key.label,this.schemeRow,actions);
    this.native=this.button('保存当前原生接入',()=>this.perform({provider:this.provider,action:'importCurrent',name:this.t('本机原生接入')}));
    this.root.append(...(this.formOnly?[this.status,this.error,this.form]:[heading,refresh,this.status,this.error,this.rows,this.form,this.native]));
    this.onLanguage=()=>this.render();document.addEventListener('bridge-language',this.onLanguage);
  }
  setBusy(busy){
    this.busy=busy;
    this.root.querySelectorAll('button,input,select').forEach(control=>{control.disabled=busy;});
    if(this.fetchModels){this.fetchModels.disabled=busy||!!this.draftLoading;this.save.disabled=busy||!!this.draftLoading;}
  }
  modelSelection(selected){return ApiAccessEditor.selection(this,selected);}
  clearDraftModels(){return ApiAccessEditor.clear(this);}
  discoverModels(){return ApiAccessEditor.discover(this);}
  clear(){
    this.generation++;
    this.clearDraftModels();
    this.value=null;
    this.apiResults.clear();
    this.setBusy(false);
    this.form.reset();
    this.key.input.value='';
    this.editId=null;this.editKind=null;this.cancel.hidden=!this.modelSelect;
    this.status.textContent='';this.error.textContent='';
    this.render();
  }
  dispose(){this.clear();document.removeEventListener('bridge-language',this.onLanguage);}
  resetForm(){this.clearDraftModels();this.editId=null;this.editKind=null;this.form.reset();this.key.input.value='';this.cancel.hidden=!this.modelSelect;this.render();}
  async refresh(){
    if(this.busy)return;
    const generation=++this.generation;
    this.setBusy(true);this.status.textContent=this.t('正在读取…');this.error.textContent='';
    try{const value=await this.read();if(generation===this.generation){this.value=value;const receipt=value?.codex?.switch;if(['complete','failed','restored'].includes(receipt?.phase))this.switchRequests.delete(receipt.targetId);for(const row of value?.[this.provider]?.accounts||[])if(!row.modelCatalogIdentity)this.apiResults.delete(row.id);this.render();}}
    catch(error){if(generation===this.generation){this.error.textContent=this.t(error.message);this.status.textContent='';}}
    finally{if(generation===this.generation)this.setBusy(false);}
  }
  async perform(value){
    if(this.busy)return;
    const generation=++this.generation;
    this.setBusy(true);this.error.textContent='';this.status.textContent=this.t('正在保存…');
    try{const result=await this.request(value);if(generation===this.generation){this.value=result;const receipt=result?.codex?.switch;if(['complete','failed','restored'].includes(receipt?.phase))this.switchRequests.delete(receipt.targetId);this.apiResults.clear();this.resetForm();this.onChanged();}}
    catch(error){if(generation===this.generation){this.error.textContent=this.t(error.message);this.status.textContent='';}}
    finally{if(value.apiKey)value.apiKey='';if(generation===this.generation)this.setBusy(false);}
  }
  async probe(row,action,refresh=false){
    if(this.busy)return;
    const generation=++this.generation;
    const scope=JSON.stringify([row.id,row.baseUrl,row.model,row.modelCatalogIdentity||'']),saved=this.apiResults.get(row.id),previous=row.modelCatalogIdentity&&saved?.scope===scope?saved:null;this.setBusy(true);this.error.textContent='';this.apiResults.set(row.id,action==='models'&&previous?.models?{...previous,loading:true}:{scope,message:this.t('正在检查模型接口…')});this.render();
    try{
      const result=await this.request({provider:this.provider,action,id:row.id,...(refresh?{refresh:true}:{})});
      if(generation!==this.generation)return;
      if(result.probe?.id!==row.id||result.probe?.provider!==this.provider||result.probe?.status!=='reachable'||result.probe?.scope!=='models')throw Error('模型接口返回了其他接入的结果');
      const current=result[this.provider]?.accounts?.find(account=>account.id===row.id);
      if(current&&JSON.stringify([current.id,current.baseUrl,current.model,current.modelCatalogIdentity||''])!==scope)throw Error('模型接口返回了其他接入的结果');
      if(action==='models'&&!Array.isArray(result.probe.models))throw Error('上游未返回兼容的模型列表，请手动填写模型 ID');
      if(action==='models'&&row.modelCatalogIdentity&&result.probe.cache?.identity!==row.modelCatalogIdentity)throw Error('模型接口返回了其他接入的结果');
      this.value=result;
      this.apiResults.set(row.id,action==='models'?{scope,models:result.probe.models,cache:result.probe.cache,...(result.probe.modelError?{error:this.t(result.probe.modelError)}:{})}:{scope,message:this.t('模型接口连接成功；未发送对话请求。')});
      this.render();
    }catch(error){if(generation===this.generation){this.apiResults.set(row.id,action==='models'&&previous?.models?{...previous,error:this.t(error.message)}:{scope,error:this.t(error.message)});this.render();}}
    finally{if(generation===this.generation)this.setBusy(false);}
  }
  render(){
    const provider=this.provider,data=this.value?.[provider];
    if(this.modelSelect){this.name.input.placeholder=this.t('例如：我的 API');this.key.input.placeholder=this.t(this.editId?'留空以保留现有 Key':'请输入 API Key');this.model.input.placeholder=this.t('获取后自动填入，或手动输入');}
    this.schemeRow.hidden=provider!=='claude'||this.editKind==='rename';for(const field of [this.baseUrl,this.model,this.key])field.label.hidden=this.editKind==='rename';this.save.textContent=this.t(this.editKind==='rename'?'保存名称':'保存 API 接入');this.rows.replaceChildren();
    if(!data){this.status.textContent='';return;}
    this.status.textContent=provider==='codex'?(this.formOnly?'':this.t('Codex CLI 与 Desktop 共用本机接入配置。')):this.t('Claude Code CLI 使用所选配置目录。');
    if(provider==='claude'&&data.verification?.status==='configured')this.status.textContent+=' '+this.t((data.accounts||[]).find(row=>row.id===data.activeId)?.kind==='api'?'API 配置已核对；未验证 Key 的远端有效性。':'原生配置已核对。');
    if(data.cliSwitch?.phase==='pending')this.rows.append(this.node('p','正在切换接入…','account-muted'));
    if(data.cliSwitch?.phase==='failed'){const error=this.node('p',null,'error');error.textContent=this.t(data.cliSwitch.error);this.rows.append(error);}
    if(this.formOnly){
      const results=[...this.apiResults.entries()].map(([id,result])=>{
        const name=data.accounts?.find(row=>row.id===id)?.name||id;
        return name+': '+(result.error||result.message||(result.models||[]).join(' · '));
      });
      if(results.length)this.status.textContent=results.join('\n');
      return;
    }
    for(const row of data.accounts||[]){
      const card=this.node('section',null,'cli-access-row'),title=this.node('strong');title.textContent=row.name;
      const detail=this.node('small');detail.textContent=[row.kind==='api'?this.t('API 接入'):this.t('原生登录'),row.baseUrl,row.model].filter(Boolean).join(' · ');
      const actions=this.node('div',null,'cli-access-actions');
      if(row.id===data.activeId){actions.append(this.node('span','当前接入'),this.button('重命名',()=>this.edit(provider,row,true)));}
      else{
        const switchButton=this.button('切换到此接入',()=>this.switchTo(provider,row.id,data));actions.append(switchButton);
        const edit=this.button(row.kind==='api'?'编辑':'重命名',()=>this.edit(provider,row,row.kind!=='api'));actions.append(edit);
        const remove=this.button('删除',()=>this.perform({provider,action:'delete',id:row.id}));actions.append(remove);
      }
      if(row.kind==='api'){
        actions.append(this.button('查看上游模型',()=>this.probe(row,'models')),this.button('测试模型接口',()=>this.probe(row,'test')));
        const scope=JSON.stringify([row.id,row.baseUrl,row.model,row.modelCatalogIdentity||'']),saved=this.apiResults.get(row.id),result=saved?.scope===scope&&(!saved.models||!row.modelCatalogIdentity||saved.cache?.identity===row.modelCatalogIdentity)?saved:null,feedback=this.node('div',null,'cli-access-probe');
        if(result?.models){actions.append(this.button('刷新模型',()=>this.probe(row,'models',true)));feedback.textContent=this.t('模型接口返回：')+result.models.join(' · ')+' — '+this.t('仅核对模型列表，未发送对话请求。');const updated=result.cache?.updatedAt;if(Number.isFinite(updated)&&updated>0){const stamp=this.node('small',null,'account-muted');stamp.textContent=this.t('更新于：')+new Date(updated*1000).toLocaleString(BridgeI18n.locale());feedback.append(stamp);}if(result.error){const error=this.node('small',null,'error');error.textContent=result.error;feedback.append(error);}}
        else if(result?.error){feedback.classList.add('error');feedback.textContent=result.error;}
        else if(result?.message)feedback.textContent=result.message;
        card.append(title,detail,actions,feedback);
      }else card.append(title,detail,actions);
      this.rows.append(card);
    }
    if(this.busy)this.setBusy(true);
  }
  edit(provider,row,rename=false){
    this.clearDraftModels();
    this.editId=row.id;this.editKind=rename?'rename':'api';this.name.input.value=row.name;
    this.baseUrl.input.value=row.baseUrl||'';this.model.input.value=row.model||'';
    this.scheme.value=row.authScheme||'x-api-key';this.key.input.value='';this.cancel.hidden=false;this.render();
    if(this.root.parentNode?.tagName==='DETAILS')this.root.parentNode.open=true;
    this.name.input.focus();
  }
  requestId(){
    if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
    const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
    const h=[...bytes].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);
  }
  switchTo(provider,id,data){
    if(provider==='codex'&&data.sharedWithDesktop){
      const confirm=this.node('div',null,'cli-access-confirm');
      confirm.append(this.node('p','此接入同时用于本机 Codex CLI。切换会同步共享认证；仅在需要时重启 Codex Desktop。'));
      const tasks=this.field('已结束所有任务，并在切换完成前暂停其他客户端发起任务','checkbox'),restart=this.field('同意在需要时重启 Codex Desktop','checkbox');
      const submit=this.button('确认切换',()=>{
        if(!tasks.input.checked||!restart.input.checked)return;
        if(!this.switchRequests.has(id))this.switchRequests.set(id,this.requestId());
        confirm.remove();this.perform({provider,action:'switch',id,requestId:this.switchRequests.get(id),confirmed:true,tasksConfirmed:true,desktopRestartConfirmed:true});
      });
      const cancel=this.button('取消',()=>confirm.remove());
      confirm.append(tasks.label,restart.label,submit,cancel);this.rows.prepend(confirm);
      return;
    }
    this.perform({provider,action:'switch',id,confirmed:true});
  }
}
CliAccessPanel.editorId=0;
