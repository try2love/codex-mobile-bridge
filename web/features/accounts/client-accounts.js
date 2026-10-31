'use strict';
// Shared account snapshots for native clients; only the desktop host can enroll them.
class DesktopClientAccountsPanel {
  constructor({root,provider,read,request,readModels=null,desktop=false,toolbar=null,visible=null,onChanged=()=>{},onUpdate=()=>{}}){
    Object.assign(this,{root,provider,read,request,readModels,desktop,toolbar,onChanged,onUpdate});
    this.visible=visible||(()=>!document.hidden&&!root.closest('[hidden]')&&(!root.closest('dialog')||root.closest('dialog').open));
    this.value=null;this.generation=0;this.checkedAt=0;this.busy=false;this.loading=false;this.queryingId=null;this.error='';this.models=null;this.modelsOpen=false;this.modelsRevision=0;this.syncLocalCliIds=new Set();
    this.languageListener=()=>{BridgeI18n.apply(this.root);this.render();};this.visibilityListener=()=>this.resume();
    document.addEventListener('bridge-language',this.languageListener);document.addEventListener('visibilitychange',this.visibilityListener);
    this.build();this.render();
  }
  text(value){return typeof BridgeI18n==='undefined'?value:BridgeI18n.t(value);}
  node(tag,text,cls){const node=document.createElement(tag);if(text!==undefined){node.textContent=this.text(text);if(typeof BridgeI18n!=='undefined'&&Object.hasOwn(BridgeI18n.dictionary,text))node.dataset.i18n=text;}if(cls)node.className=cls;return node;}
  button(text,fn){const button=this.node('button',text);button.type='button';button.onclick=fn;return button;}
  build(){
    this.root.classList.add('accounts-panel');this.root.replaceChildren();
    const toolbar=this.toolbar||this.node('div',undefined,'accounts-toolbar');if(!this.toolbar)toolbar.append(this.node('h3','已保存的接入'));
    this.refreshButton=this.button(this.toolbar?'刷新':'重新加载',()=>this.refresh());if(this.toolbar)this.refreshButton.className='accounts-refresh';toolbar.append(this.refreshButton);if(!this.toolbar)this.root.append(toolbar);
    this.status=this.node('p',undefined,'account-muted');this.status.setAttribute('role','status');this.errorNode=this.node('p',undefined,'error');this.errorNode.setAttribute('role','alert');
    this.current=this.node('p',undefined,'accounts-current');this.modelsButton=this.button('查看可用模型',()=>this.toggleModels());this.modelsBox=this.node('div',undefined,'accounts-models');this.modelsBox.ontouchstart=()=>{this.modelsTouching=true;};this.modelsBox.ontouchend=this.modelsBox.ontouchcancel=()=>{this.modelsTouching=false;};this.modelsControls=this.node('div',undefined,'accounts-model-controls');this.modelsRefreshButton=this.button('刷新模型',()=>this.loadModels(true));this.modelsUpdated=this.node('small',undefined,'account-muted');this.modelsContent=this.node('div');this.modelsControls.append(this.modelsRefreshButton,this.modelsUpdated);this.modelsBox.append(this.modelsControls,this.modelsContent);this.rows=this.node('div',undefined,'accounts-list');this.modelFallback=this.node('div',undefined,'accounts-model-fallback');this.modelFallback.append(this.modelsButton,this.modelsBox);this.root.append(this.status,this.errorNode,this.current,this.rows,this.modelFallback);
    this.hint=this.node('p',undefined,'account-muted');this.root.append(this.hint);
    if(this.desktop){const name=this.node('label',undefined,'accounts-import-name');this.importName=name;this.nameInput=this.node('input');this.nameInput.type='text';this.nameInput.maxLength=80;name.append(this.node('span','接入名称（可选）'),this.nameInput);this.root.append(name);this.importButton=this.button(this.provider==='deepseek'?'保存当前账号':'保存当前接入',()=>this.importCurrent());this.root.append(this.importButton);if(this.provider==='deepseek'){this.importApiButton=this.button('保存当前 API',()=>this.importCurrent('api'));this.root.append(this.importApiButton);}}
    if(this.desktop)this.buildEditor();
  }
  buildEditor(){
    this.addApiButton=this.button(this.provider==='claude'?'添加 API 接入':'添加 DeepSeek 官方 API',()=>this.openEditor({kind:'api',api:{}}));this.root.append(this.addApiButton);
    this.editor=this.node('form',undefined,'accounts-client-editor');this.editor.hidden=true;
    const field=(label,type='text')=>{const wrap=this.node('label'),input=this.node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;wrap.append(this.node('span',label),input);return {wrap,input};};
    this.editName=field('名称');this.editName.input.maxLength=this.provider==='claude'?120:100;this.editName.input.required=true;
    this.editUrl=field('API 地址','url');this.editUrl.input.maxLength=2048;
    this.editKey=field('API Key','password');this.editKey.input.maxLength=8192;this.editKey.input.autocomplete='new-password';
    this.editModels=field('模型 ID（每行一个）','textarea');this.editModels.input.rows=3;
    this.authWrap=this.node('label');this.authWrap.append(this.node('span','认证方式'));this.editAuth=this.node('select');for(const value of ['bearer','x-api-key']){const option=this.node('option',value==='bearer'?'Bearer':'x-api-key');option.value=value;this.editAuth.append(option);}this.authWrap.append(this.editAuth);
    this.editHint=this.node('p',undefined,'account-muted');this.editSubmit=this.node('button','保存配置');this.editSubmit.type='submit';this.editCancel=this.button('取消编辑',()=>this.closeEditor());
    this.editor.append(this.editName.wrap,this.editUrl.wrap,this.authWrap,this.editKey.wrap,this.editModels.wrap,this.editHint,this.editSubmit,this.editCancel);this.editor.onsubmit=event=>{event.preventDefault();return this.saveEditor();};this.root.append(this.editor);
  }
  openEditor(value){
    if(!this.desktop||this.busy||this.loading)return;
    this.editing=value;this.editor.hidden=false;this.editName.input.value=value.name||'';this.editKey.input.value='';this.editUrl.input.value=value.api?.baseUrl||'';this.editAuth.value=value.api?.authScheme||'bearer';this.editModels.input.value=(value.api?.models||[]).join('\n');
    const api=!!value.api,claude=api&&this.provider==='claude';this.editKey.wrap.hidden=!api;this.editKey.input.required=api&&!value.api.hasKey;this.editKey.input.placeholder=this.text(value.api?.hasKey?'留空保留已有密钥':'');
    for(const part of [this.editUrl.wrap,this.authWrap,this.editModels.wrap])part.hidden=!claude;this.editUrl.input.required=claude;this.editModels.input.required=claude;
    this.render();
  }
  closeEditor(){if(!this.editor)return;this.editor.hidden=true;this.editing=null;this.editKey.input.value='';}
  async editAccount(row){
    if(!this.desktop||this.busy||this.loading||row.saved===false)return;
    const generation=this.generation;this.busy=true;this.error='';this.render();let value;
    try{value=await this.request({action:'edit',id:row.id});}catch(error){if(generation===this.generation)this.error=error.message;}
    finally{if(generation===this.generation){this.busy=false;if(value)this.openEditor(value);this.render();}}
  }
  async saveEditor(){
    if(!this.desktop||!this.editing||this.busy||this.loading)return;
    const value={action:this.editing.api?'save-api':'rename',name:this.editName.input.value.trim(),...(this.editing.id?{id:this.editing.id}:{})};
    if(this.editing.api){value.apiKey=this.editKey.input.value;if(this.provider==='claude')Object.assign(value,{baseUrl:this.editUrl.input.value.trim(),authScheme:this.editAuth.value,models:this.editModels.input.value.split(/\r?\n/).map(row=>row.trim()).filter(Boolean)});}
    if(await this.perform(value))this.closeEditor();
  }
  removeAccount(row){
    if(!this.desktop||this.busy||this.loading||row.saved===false)return;
    if(window.confirm(this.text('删除此已保存接入？仅删除网关保存的副本，不会退出电脑上的账号。')))return this.perform({action:'remove',id:row.id});
  }
  dispose(){this.generation++;clearTimeout(this.timer);this.closeEditor();this.disposed=true;document.removeEventListener('bridge-language',this.languageListener);document.removeEventListener('visibilitychange',this.visibilityListener);}
  resume(){clearTimeout(this.timer);if(this.disposed||!this.visible())return;if(!this.checkedAt||Date.now()-this.checkedAt>=300000)return this.refresh(false);this.schedule();}
  schedule(){clearTimeout(this.timer);if(!this.disposed&&this.visible())this.timer=setTimeout(()=>this.resume(),Math.max(0,300000-(Date.now()-this.checkedAt)));}
  activeIds(value=this.value){return new Set(value?.activeIds||[value?.activeId].filter(Boolean));}
  accept(value,notifyChange=true){
    if(!value||!Array.isArray(value.accounts))throw Error(this.text('账号信息暂不可用'));
    if(value.readOnly===true)this.closeEditor();
    const previous=this.value,changed=this.value&&(JSON.stringify([...this.activeIds()].sort())!==JSON.stringify([...this.activeIds(value)].sort())||(this.value.modelCatalogIdentity||'')!==(value.modelCatalogIdentity||''));this.value=value;
    if(changed){this.modelsRevision++;this.modelsLoading=false;this.models=null;this.modelsOpen=false;if(notifyChange)this.onChanged(value,previous);}this.onUpdate(value);this.render();
  }
  async refresh(force=true){
    if(this.disposed||this.loading||this.busy)return;clearTimeout(this.timer);this.loading=true;this.error='';this.render();const generation=this.generation;
    try{
      const value=await this.read();if(generation!==this.generation)return;this.accept(value);
      if(this.provider!=='claude'&&this.value.capabilities?.quota!==false&&this.visible())for(const row of this.value.accounts){
        if(!this.visible()||generation!==this.generation)break;
        const details=await this.request({action:'details',id:row.id,refresh:force});if(generation!==this.generation)return;this.accept(details);
      }
    }catch(error){if(generation===this.generation)this.error=error.message;}
    finally{if(generation===this.generation){this.checkedAt=Date.now();this.loading=false;this.render();if(this.value)this.onUpdate(this.value);this.schedule();}}
  }
  async perform(value){
    if(this.disposed||this.busy||this.loading)return false;clearTimeout(this.timer);this.busy=true;this.queryingId=this.provider==='claude'&&value.action==='details'?value.id:null;this.error='';this.render();const generation=this.generation;
    try{const result=await this.request(value);if(generation!==this.generation)return false;if(['switch','import-current','save-api','remove'].includes(value.action)){this.modelsRevision++;this.models=null;this.modelsOpen=false;}this.accept(result,!['import-current','save-api','rename','remove'].includes(value.action));return true;}
    catch(error){if(generation===this.generation)this.error=error.message;return false;}
    finally{if(generation===this.generation){this.busy=false;this.queryingId=null;this.checkedAt=Date.now();this.render();if(this.value)this.onUpdate(this.value);this.schedule();}}
  }
  async importCurrent(kind){
    if(!this.desktop||this.busy||this.loading)return;
    if(this.provider==='claude'&&!window.confirm(this.text('保存当前账号会短暂重启应用；运行中的任务会阻止操作。')+'\n\n'+this.text('重新启动 Claude Desktop 后，初始化连接需要电脑处于解锁状态。是否确认退出？')))return;
    const name=(this.nameInput?.value||'').trim();
    if(await this.perform({action:'import-current',...(kind?{kind}:{}),...(name?{name}:{})})){this.nameInput.value='';await this.refresh(false);}
  }
  choose(row){
    if(this.busy||this.loading||this.value?.canSwitch!==true||this.activeIds().has(row.id))return;
    const prompt=this.text('切换会重启对应桌面应用。未发送草稿按账号保留，待发附件需重新添加。请先结束任务并保存工作，确认切换？')+(this.provider==='claude'?'\n\n'+this.text('重新启动 Claude Desktop 后，初始化连接需要电脑处于解锁状态。是否确认退出？'):'');
    if(window.confirm(prompt))return this.perform({action:'switch',id:row.id,
      ...(this.provider==='claude'&&this.syncLocalCliIds.has(row.id)?{syncLocalCli:true}:{})});
  }
  duration(minutes){return !minutes?'额度窗口':minutes%1440===0?minutes/1440+this.text(' 天'):minutes%60===0?minutes/60+this.text(' 小时'):minutes+this.text(' 分钟');}
  queryTime(seconds){const date=new Date(seconds*1000),pad=value=>String(value).padStart(2,'0');return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;}
  usageControls(row){
    const usage=row.usage||row.quota,controls=this.node('div',undefined,'accounts-usage-controls');
    const label=this.text(this.queryingId===row.id?'查询中…':this.provider==='claude'?'查看剩余额度':'刷新余额');
    const refresh=this.button('',()=>this.perform({action:'details',id:row.id,refresh:true}));
    refresh.title=label;refresh.setAttribute('aria-label',label);refresh.className='accounts-usage-refresh';
    refresh.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 12a8 8 0 1 1-2.34-5.66L20 8M20 3v5h-5"/></svg>';
    refresh.disabled=this.busy||this.loading;
    if(usage?.checkedAt)controls.append(this.node('small',this.text(this.provider==='claude'?'查询时间：':'更新于：')+(this.provider==='claude'?this.queryTime(usage.checkedAt):new Date(usage.checkedAt*1000).toLocaleString(BridgeI18n.locale())),'account-updated'));
    controls.append(refresh);return controls;
  }
  usage(box,row){
    const usage=row.usage||row.quota;
    if(row.kind==='api'&&!usage?.balance)return;
    const heading=this.node('div',undefined,'accounts-client-usage-heading');
    heading.append(this.node('strong',this.provider==='claude'?'剩余额度':'账户余额'));
    box.append(heading);
    const results=this.node('div',undefined,'accounts-client-usage-results');
    let shown=false;
    for(const bucket of usage?.limits||[])for(const window of bucket.windows||[]){
      const line=this.node('div',undefined,'accounts-window'),percent=window.remainingPercent;
      const remaining=Number.isFinite(percent)?this.text('剩余 ')+Math.round(percent*10)/10+'%':window.remaining!=null?[window.remaining,window.total].filter(value=>value!=null).join(' / '):this.text('暂未提供');
      line.append(this.node('span',[bucket.name,this.text(this.duration(window.windowDurationMins))].filter(Boolean).join(' · ')),this.node('strong',remaining));
      if(Number.isFinite(percent)){const progress=this.node('progress');progress.max=100;progress.value=Math.min(100,Math.max(0,percent));progress.setAttribute('aria-label',this.text('剩余额度'));line.append(progress);}
      if(window.resetsAt)line.append(this.node('small',this.text('恢复时间：')+new Date(window.resetsAt*1000).toLocaleString(BridgeI18n.locale()),'account-reset-time'));
      results.append(line);shown=true;
    }
    for(const wallet of usage?.balance?.wallets||[]){
      const line=this.node('div',undefined,'accounts-window');line.append(this.node('span','账户余额'),this.node('strong',[wallet.currency,wallet.remaining].filter(value=>value!=null).join(' ')));
      if(wallet.toppedUp!=null||wallet.granted!=null)line.append(this.node('small',[wallet.toppedUp!=null?this.text('充值余额：')+wallet.toppedUp:'',wallet.granted!=null?this.text('赠送余额：')+wallet.granted:''].filter(Boolean).join(' · '),'account-reset-time'));
      results.append(line);shown=true;
    }
    const unavailable=this.provider==='claude'?'暂未提供额度信息':'余额信息暂不可用',failure=this.provider==='claude'?'额度查询失败':'余额查询失败';
    if(!shown)results.append(this.node('span',usage?.status==='error'?(usage.error||failure):usage?.status==='loading'?'查询中…':usage?.status==='unsupported'?unavailable:!usage&&this.provider==='claude'?'尚未查询':!usage?'正在读取余额…':unavailable,'account-muted'));
    if(shown&&usage?.status==='error')results.append(this.node('small',this.text(usage.error||failure)+' · '+this.text('保留上次结果，数据尚未更新'),'account-muted'));
    box.append(results);
  }
  async toggleModels(){
    this.modelsOpen=!this.modelsOpen;if(!this.modelsOpen){this.render();return;}
    if(!this.value?.modelCatalogIdentity)this.models=null;
    return this.loadModels();
  }
  async loadModels(refresh=false){
    const focused=refresh&&document.activeElement===this.modelsRefreshButton,generation=this.generation,revision=++this.modelsRevision,identity=this.value?.modelCatalogIdentity||'';this.modelsError='';this.modelsLoading=true;this.render();
    try{const value=await this.readModels(refresh);if(generation!==this.generation||revision!==this.modelsRevision)return;if(identity&&value.cache?.identity!==identity){this.models=null;throw Error(this.text('账号已切换，请重新打开模型设置'));}this.models=value;}
    catch(error){if(generation===this.generation&&revision===this.modelsRevision)this.modelsError=error.message;}
    finally{if(generation===this.generation&&revision===this.modelsRevision){this.modelsLoading=false;this.render();if(focused&&this.modelsRefreshButton.isConnected&&(document.activeElement===document.body||document.activeElement===this.modelsRefreshButton)&&!this.modelsTouching)this.modelsRefreshButton.focus({preventScroll:true});}}
  }
  renderModels(box){
    box.replaceChildren();this.modelsRefreshButton.disabled=this.modelsLoading;
    const updated=this.models?.cache?.updatedAt;this.modelsUpdated.hidden=!(Number.isFinite(updated)&&updated>0);this.modelsUpdated.textContent=this.modelsUpdated.hidden?'':this.text('更新于：')+new Date(updated*1000).toLocaleString(BridgeI18n.locale());
    if(this.modelsLoading&&!this.models)box.append(this.node('p','正在读取模型…','account-muted'));
    if(this.modelsError||this.models?.modelError)box.append(this.node('p',this.modelsError||this.models.modelError,'error'));
    if(!this.models&&(this.modelsLoading||this.modelsError))return;
    if(this.models?.notice)box.append(this.node('p',this.models.notice,'account-muted'));
    let count=0;const groups=this.models?.groups?.length?this.models.groups:[{models:this.models?.models||this.models?.catalog?.models||[]}];
    for(const group of groups){
      if(!group.models?.length)continue;count+=group.models.length;
      box.append(this.node('strong',[group.surface==='code'?'Code':group.surface==='cowork'?'Cowork':group.label||group.name||group.id,this.text(group.source==='session-history'?'历史模型':group.source==='desktop-config'?'配置模型':'可用模型')].filter(Boolean).join(' · ')));
      if(group.notice)box.append(this.node('p',group.notice,'account-muted'));
      const list=this.node('ul');for(const model of group.models){const row=this.node('li',typeof model==='string'?model:model.name||model.id);if(model.efforts?.length)row.append(this.node('small',' · '+model.efforts.map(effort=>this.text(typeof effort==='string'?effort:effort.name||effort.id)).join(' / '),'account-muted'));list.append(row);}box.append(list);
    }
    if(!count&&!this.models?.notice)box.append(this.node('p','当前客户端未返回可用模型，请检查客户端登录与模型配置。','account-muted'));
  }
  reconcileRows(cards,modelTargetId){
    const previous=new Map([...this.rows.children].filter(card=>card.accountRowId).map(card=>[card.accountRowId,card]));
    const next=cards.map(card=>{
      const old=previous.get(card.accountRowId);if(!old)return card;
      const keep=card.accountRowId===modelTargetId&&this.modelsBox.parentNode===old?this.modelsBox:null;
      for(const child of [...old.children])if(child!==keep)child.remove();
      for(const child of [...card.children])old.insertBefore(child,keep);
      old.className=card.className;return old;
    });
    for(const child of [...this.rows.children])if(!next.includes(child))child.remove();
    next.forEach((card,index)=>{if(this.rows.children[index]!==card)this.rows.insertBefore(card,this.rows.children[index]||null);});
    const target=next.find(card=>card.accountRowId===modelTargetId);
    if(target){if(this.modelsBox.parentNode!==target)target.append(this.modelsBox);}
    else this.modelFallback.append(this.modelsButton,this.modelsBox);
  }
  render(){
    const value=this.value;this.refreshButton.disabled=this.busy||this.loading;
    for(const control of [this.addApiButton,this.importButton,this.importApiButton,this.importName])if(control)control.hidden=value?.readOnly===true;
    if(this.addApiButton)this.addApiButton.hidden=value?.readOnly===true||value?.capabilities?.addApi===false;
    this.status.textContent=this.text(this.busy?'正在处理…':this.loading?'正在读取…':value?.message||(value?.gatewayRunning===false?'网关未启动':''));this.errorNode.textContent=this.text(this.error||'');
    this.current.textContent=this.text('当前接入：')+(value?.current?.name||value?.current?.label||value?.accounts.find(row=>row.id===value.activeId)?.name||this.text('暂未识别'))+(value?.current?.routes?.length?' · '+this.text('已配置接入：')+value.current.routes.map(route=>this.text(({'deepseek-account':'官方账号','deepseek-official':'API 接入'})[route]||route)).join(' / '):'');
    if(value?.cliSync?.status==='verified')this.current.textContent+=' · '+this.text('本机 CLI 已核验');
    else if(value?.cliSync?.status==='failed')this.current.textContent+=' · '+this.text('本机 CLI 同步失败：')+this.text(value.cliSync.error||'请检查接入');
    this.hint.textContent=this.text(value?.enrollmentHint|| (value?.readOnly?'账号和 API 请在已绑定的第三方客户端中管理':this.desktop?'官方账号请先在对应桌面应用登录，再保存当前接入。API 可在此添加；保存后选择切换才会应用。':'添加账号或 API，请在电脑端完成。'))+(this.provider==='deepseek'?' '+this.text('现有会话继续使用各自选择的模型接入。'):'');
    this.modelsButton.hidden=!this.readModels;this.modelsButton.textContent=this.text(this.modelsOpen?'收起模型':'查看可用模型');this.modelsButton.disabled=this.busy||this.modelsLoading;this.modelsBox.hidden=!this.modelsOpen;
    if(this.modelsOpen){
      const key=JSON.stringify([this.models,this.modelsError,this.modelsLoading&&!this.models,BridgeI18n.locale()]);
      if(this.modelsRenderKey!==key){this.modelsRenderKey=key;this.renderModels(this.modelsContent);}
      if(this.modelsRefreshButton)this.modelsRefreshButton.disabled=this.modelsLoading;
    }else if(this.modelsRenderKey){this.modelsRenderKey=null;this.modelsContent.replaceChildren();this.modelsUpdated.textContent='';}
    if(this.nameInput)this.nameInput.disabled=this.busy||this.loading;
    for(const button of [this.importButton,this.importApiButton])if(button)button.disabled=this.busy||this.loading||!value||value.canImport===false;
    if(this.editor){
      for(const control of [this.addApiButton,this.editSubmit,this.editCancel,this.editName.input,this.editUrl.input,this.editKey.input,this.editAuth,this.editModels.input])control.disabled=this.busy||this.loading;
      this.editHint.textContent=this.text(this.editing?.api?(this.provider==='claude'?'上游需兼容 Anthropic Messages API，填写其支持的 Claude 模型 ID。凭据仅保存于此电脑；保存不会立即切换。':'仅配置 DeepSeek 官方 API Key；自定义上游请在 Harness 中配置。保存不会立即切换。'):'此处修改已保存接入的名称；登录凭据请在原桌面应用中更新后重新保存。');
    }
    let modelsPlaced=false,modelTargetId=null;const cards=[];
    const activeIds=this.activeIds(),accounts=[...(value?.accounts||[])].sort((a,b)=>Number(activeIds.has(b.id))-Number(activeIds.has(a.id)));
    if(value&&!accounts.length)cards.push(this.node('p','尚未保存此客户端的接入。','account-muted'));
    for(const row of accounts){
      const active=activeIds.has(row.id),card=this.node('section',undefined,'account-bucket accounts-client-card'+(active?' is-current':'')),identity=this.node('div',undefined,'accounts-identity'),name=this.node('div',undefined,'accounts-name'),heading=this.node('div',undefined,'accounts-name-heading');card.accountRowId=row.id;
      heading.append(this.node('h3',row.name||row.email||row.id));if(active)heading.append(this.node('span','当前接入','accounts-plan'));if(row.saved===false)heading.append(this.node('span','尚未保存','account-muted'));name.append(heading);
      name.append(this.node('p',row.kind==='api'?'API 接入':'官方账号','account-muted'));if(row.email)name.append(this.node('p',row.email,'account-muted'));identity.append(name);
      card.append(identity);
      if(value?.capabilities?.quota!==false&&(row.kind!=='api'||(row.usage||row.quota)?.balance)){const usage=this.node('div',undefined,'accounts-usage accounts-client-usage');this.usage(usage,row);if(usage.children.length)card.append(usage);}
      const actions=this.node('div',undefined,'accounts-actions'),switcher=this.button(active?'当前接入':'切换到此接入',()=>this.choose(row));switcher.disabled=active||this.busy||this.loading||value.canSwitch!==true;actions.append(switcher);
      if(this.provider==='claude'&&!active){const label=this.node('label',undefined,'account-reminder'),checkbox=this.node('input');checkbox.type='checkbox';checkbox.checked=row.kind==='api'&&this.syncLocalCliIds.has(row.id);checkbox.disabled=row.kind!=='api'||this.busy||this.loading;checkbox.onchange=()=>{if(checkbox.checked)this.syncLocalCliIds.add(row.id);else this.syncLocalCliIds.delete(row.id);};label.append(checkbox,this.node('span','同时应用到本机 CLI'));actions.append(label);if(row.kind!=='api')actions.append(this.node('small','官方 Claude Desktop 登录不能直接同步到 Claude Code CLI','account-muted'));}
      if(this.desktop&&row.saved!==false){for(const [label,fn] of [['编辑',()=>this.editAccount(row)],['删除',()=>this.removeAccount(row)]]){const button=this.button(label,fn);button.disabled=this.busy||this.loading;actions.append(button);}}
      card.append(actions);const footer=this.node('div',undefined,'accounts-card-footer');if(active&&!modelsPlaced&&this.readModels){footer.append(this.modelsButton);modelsPlaced=true;modelTargetId=row.id;}if(value?.capabilities?.quota!==false&&(row.kind!=='api'||(row.usage||row.quota)?.balance))footer.append(this.usageControls(row));if(footer.children.length)card.append(footer);cards.push(card);
    }
    this.reconcileRows(cards,modelTargetId);
  }
}
if(typeof module!=='undefined')module.exports={DesktopClientAccountsPanel};
